from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cyberlab.execution import EnvState
from cyberlab.execution.docker import FileOpError
from cyberlab.main import create_app
from cyberlab.storage import LocalSnapshotStore, StorageError

from .conftest import auth
from .test_environments_api import MINIMAL, YAML, FakeProvider


# ---------------------------------------------------------------- local store
def test_local_store_roundtrip_and_permissions(tmp_path):
    store = LocalSnapshotStore(tmp_path / "store")
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    store.put("ws1/snap1.tar.gz", src)
    path = tmp_path / "store" / "ws1" / "snap1.tar.gz"
    assert path.read_bytes() == b"data" and oct(path.stat().st_mode)[-3:] == "600"
    assert store.exists("ws1/snap1.tar.gz")
    out = tmp_path / "out.bin"
    store.get("ws1/snap1.tar.gz", out)
    assert out.read_bytes() == b"data"
    store.delete("ws1/snap1.tar.gz")
    assert not store.exists("ws1/snap1.tar.gz")
    store.delete("ws1/snap1.tar.gz")  # idempotent
    with pytest.raises(StorageError):
        store.get("ws1/snap1.tar.gz", out)


@pytest.mark.parametrize("key", ["../x", "a/../../b", "/abs", "a//b", "", "a b", "-x"])
def test_local_store_rejects_bad_keys(tmp_path, key):
    store = LocalSnapshotStore(tmp_path)
    with pytest.raises(StorageError):
        store.exists(key)


# ---------------------------------------------------------------- API
class SnapshotProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.volume = b"original workspace"
        self.restored = []
        self.fail_export = None

    def provision(self, request):
        result = super().provision(request)
        result.resource_names.update({"volume": "vol-1", "image": "cyberlab-env:abc"})
        return result

    def export_workspace(self, volume, image, destination, max_bytes):
        if self.fail_export:
            raise self.fail_export
        Path(destination).write_bytes(self.volume)
        return len(self.volume)

    def import_workspace(self, volume, image, source):
        self.volume = Path(source).read_bytes()
        self.restored.append(volume)


@pytest.fixture
def provider():
    return SnapshotProvider()


@pytest.fixture
def app(settings, provider):
    a = create_app(settings.model_copy(update={"max_snapshots_per_workspace": 2}))
    a.state.provider = provider
    return a


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def env(client):
    ws = client.post("/v1/workspaces", json={"name": "s"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
    (e,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    return ws, e["id"]


def snap(client, ws, env_id, label="", user="user-a"):
    return client.post(
        f"/v1/workspaces/{ws}/environments/{env_id}/snapshots",
        json={"label": label},
        headers=auth(user),
    )


def test_snapshot_and_restore(client, env, provider, app):
    ws, env_id = env
    r = snap(client, ws, env_id, "before experiment")
    assert r.status_code == 201
    s = r.json()
    assert s["label"] == "before experiment" and s["sizeBytes"] == 18 and len(s["sha256"]) == 64
    assert app.state.snapshot_store.exists(f"{ws}/{s['id']}.tar.gz")

    provider.volume = b"changed after the experiment"
    restore = f"/v1/workspaces/{ws}/environments/{env_id}/restore"
    r = client.post(restore, json={"snapshotId": s["id"]}, headers=auth())
    assert r.status_code == 409 and "stop the environment" in r.json()["detail"]

    provider.states["c0"] = EnvState.stopped
    r = client.post(restore, json={"snapshotId": s["id"]}, headers=auth())
    assert r.status_code == 200 and provider.volume == b"original workspace"

    listed = client.get(f"/v1/workspaces/{ws}/snapshots", headers=auth()).json()
    assert [x["id"] for x in listed] == [s["id"]]
    actions = [e["action"] for e in client.get("/v1/audit", headers=auth()).json()]
    assert {"snapshot.create", "snapshot.restore"} <= set(actions)


def test_tampered_snapshot_is_not_restored(client, env, provider, app):
    ws, env_id = env
    s = snap(client, ws, env_id).json()
    path = Path(app.state.settings.snapshot_dir) / ws / f"{s['id']}.tar.gz"
    path.write_bytes(b"evil replacement")
    provider.states["c0"] = EnvState.stopped
    r = client.post(
        f"/v1/workspaces/{ws}/environments/{env_id}/restore",
        json={"snapshotId": s["id"]},
        headers=auth(),
    )
    assert r.status_code == 409 and "integrity" in r.json()["detail"]
    assert provider.restored == []


def test_snapshot_limit_and_delete(client, env, app):
    ws, env_id = env
    a = snap(client, ws, env_id).json()
    snap(client, ws, env_id)
    r = snap(client, ws, env_id)
    assert r.status_code == 409 and "limit" in r.json()["detail"]
    assert (
        client.delete(f"/v1/workspaces/{ws}/snapshots/{a['id']}", headers=auth()).status_code == 204
    )
    assert not app.state.snapshot_store.exists(f"{ws}/{a['id']}.tar.gz")
    assert snap(client, ws, env_id).status_code == 201


def test_too_large_snapshot(client, env, provider):
    ws, env_id = env
    provider.fail_export = FileOpError("too_large", "workspace snapshot is too large")
    assert snap(client, ws, env_id).status_code == 413
    assert client.get(f"/v1/workspaces/{ws}/snapshots", headers=auth()).json() == []


def test_owner_isolation(client, env):
    ws, env_id = env
    s = snap(client, ws, env_id).json()
    assert snap(client, ws, env_id, user="mallory").status_code == 404
    assert client.get(f"/v1/workspaces/{ws}/snapshots", headers=auth("mallory")).status_code == 404
    assert (
        client.delete(
            f"/v1/workspaces/{ws}/snapshots/{s['id']}", headers=auth("mallory")
        ).status_code
        == 404
    )
    # another user's snapshot id can't be restored into your own environment
    ws2 = client.post("/v1/workspaces", json={"name": "m"}, headers=auth("mallory")).json()["id"]
    client.put(f"/v1/workspaces/{ws2}/manifest", content=MINIMAL, headers=YAML | auth("mallory"))
    (e2,) = client.post(
        f"/v1/workspaces/{ws2}/environments", json={}, headers=auth("mallory")
    ).json()["environments"]
    r = client.post(
        f"/v1/workspaces/{ws2}/environments/{e2['id']}/restore",
        json={"snapshotId": s["id"]},
        headers=auth("mallory"),
    )
    assert r.status_code == 404


def test_workspace_delete_removes_snapshot_data(client, env, app):
    ws, env_id = env
    s = snap(client, ws, env_id).json()
    client.delete(f"/v1/workspaces/{ws}/environments/{env_id}", headers=auth())
    assert client.delete(f"/v1/workspaces/{ws}", headers=auth()).status_code == 204
    assert not app.state.snapshot_store.exists(f"{ws}/{s['id']}.tar.gz")


def test_restore_into_reprovisioned_environment(client, env, provider):
    # destroy the environment, provision again, restore the old snapshot into the new one
    ws, env_id = env
    s = snap(client, ws, env_id).json()
    client.delete(f"/v1/workspaces/{ws}/environments/{env_id}", headers=auth())
    (new,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    provider.volume = b"fresh and empty"
    provider.states[f"c{len(provider.states) - 1}"] = EnvState.stopped
    for ref in list(provider.states):
        provider.states[ref] = EnvState.stopped
    r = client.post(
        f"/v1/workspaces/{ws}/environments/{new['id']}/restore",
        json={"snapshotId": s["id"]},
        headers=auth(),
    )
    assert r.status_code == 200 and provider.volume == b"original workspace"


def test_each_snapshot_keeps_its_own_data(client, env, provider):
    # Regression: storage keys were built before the database assigned the snapshot id,
    # so every snapshot in a workspace shared (and overwrote) one file.
    ws, env_id = env
    provider.volume = b"version one"
    first = snap(client, ws, env_id).json()
    provider.volume = b"version two"
    second = snap(client, ws, env_id).json()
    assert first["sha256"] != second["sha256"]
    provider.states["c0"] = EnvState.stopped
    restore = f"/v1/workspaces/{ws}/environments/{env_id}/restore"
    client.post(restore, json={"snapshotId": first["id"]}, headers=auth())
    assert provider.volume == b"version one"
    client.post(restore, json={"snapshotId": second["id"]}, headers=auth())
    assert provider.volume == b"version two"
