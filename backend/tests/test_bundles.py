import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cyberlab.execution import EnvState
from cyberlab.main import create_app
from cyberlab.storage.crypto import generate_key

from .conftest import auth
from .test_environments_api import MINIMAL, YAML
from .test_snapshots import SnapshotProvider


def make_app(settings, **extra):
    app = create_app(settings.model_copy(update=extra))
    app.state.provider = SnapshotProvider()
    return app


def setup_ws(c, name="src", user="user-a"):
    ws = c.post("/v1/workspaces", json={"name": name}, headers=auth(user)).json()["id"]
    c.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth(user))
    (env,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth(user)).json()[
        "environments"
    ]
    return ws, env["id"]


def snapshot(c, ws, env_id, label=""):
    return c.post(
        f"/v1/workspaces/{ws}/environments/{env_id}/snapshots",
        json={"label": label},
        headers=auth(),
    ).json()


def bundle_members(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def test_export_then_import_on_another_server_with_another_key(settings, tmp_path):
    # Server A (its own encryption key) exports...
    a = make_app(settings, snapshot_encryption_key=SecretStr(generate_key()))
    a.state.provider.volume = b"my lab work"
    with TestClient(a) as c:
        ws, env_id = setup_ws(c)
        first = snapshot(c, ws, env_id, "day 1")
        r = c.get(f"/v1/workspaces/{ws}/export", headers=auth())
        assert r.status_code == 200 and r.headers["content-type"] == "application/x-tar"
        bundle = r.content
    members = bundle_members(bundle)
    meta = json.loads(members["bundle.json"])
    assert meta["format"] == "cyberlab.dev/bundle/v1"
    assert meta["manifest"]["metadata"]["name"] == "my-first-universe"
    assert members[f"snapshots/{first['id']}.tar.gz"] == b"my lab work"  # decrypted for transport

    # ...server B (different key, different storage) imports and restores.
    b_settings = settings.model_copy(
        update={
            "snapshot_dir": tmp_path / "server-b",
            "snapshot_encryption_key": SecretStr(generate_key()),
        }
    )
    b = create_app(b_settings)
    b.state.provider = provider = SnapshotProvider()
    with TestClient(b) as c:
        r = c.post("/v1/workspaces/import", content=bundle, headers=auth("bob"))
        assert r.status_code == 201, r.text
        new_ws = r.json()["workspace"]["id"]
        assert r.json()["snapshots"] == 1
        snaps = c.get(f"/v1/workspaces/{new_ws}/snapshots", headers=auth("bob")).json()
        assert snaps[0]["label"] == "day 1" and snaps[0]["store"] == "local+aes256gcm"
        # nothing was started by the import
        assert c.get(f"/v1/workspaces/{new_ws}/environments", headers=auth("bob")).json() == []
        (env,) = c.post(
            f"/v1/workspaces/{new_ws}/environments", json={}, headers=auth("bob")
        ).json()["environments"]
        for ref in provider.states:
            provider.states[ref] = EnvState.stopped
        r = c.post(
            f"/v1/workspaces/{new_ws}/environments/{env['id']}/restore",
            json={"snapshotId": snaps[0]["id"]},
            headers=auth("bob"),
        )
        assert r.status_code == 200 and provider.volume == b"my lab work"
        actions = [e["action"] for e in c.get("/v1/audit", headers=auth("bob")).json()]
        assert "workspace.import" in actions


def test_export_options(settings):
    app = make_app(settings)
    with TestClient(app) as c:
        ws, env_id = setup_ws(c)
        snapshot(c, ws, env_id, "old")
        newest = snapshot(c, ws, env_id, "new")
        none = bundle_members(
            c.get(f"/v1/workspaces/{ws}/export?snapshots=none", headers=auth()).content
        )
        assert list(none) == ["bundle.json"]
        latest = bundle_members(
            c.get(f"/v1/workspaces/{ws}/export?snapshots=latest", headers=auth()).content
        )
        assert set(latest) == {"bundle.json", f"snapshots/{newest['id']}.tar.gz"}
        assert c.get(f"/v1/workspaces/{ws}/export", headers=auth("mallory")).status_code == 404


def test_import_name_conflicts_and_rename(settings):
    app = make_app(settings)
    with TestClient(app) as c:
        ws, _ = setup_ws(c, name="lab")
        bundle = c.get(f"/v1/workspaces/{ws}/export", headers=auth()).content
        assert c.post("/v1/workspaces/import", content=bundle, headers=auth()).status_code == 409
        r = c.post("/v1/workspaces/import?name=lab-copy", content=bundle, headers=auth())
        assert r.status_code == 201 and r.json()["workspace"]["name"] == "lab-copy"


# ---------------------------------------------------------------- hostile bundles
def tar_of(entries):
    """entries: list of (name, bytes | TarInfo-modifier)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type = kind
                info.linkname = "/etc/passwd"
                tar.addfile(info)
    return buf.getvalue()


def good_meta(snaps=None, manifest=None):
    import yaml

    return json.dumps(
        {
            "format": "cyberlab.dev/bundle/v1",
            "workspace": {"name": "evil"},
            "manifest": manifest or yaml.safe_load(MINIMAL),
            "snapshots": snaps or [],
        }
    ).encode()


SID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize(
    "entries,msg",
    [
        ([("bundle.json", good_meta(), "file"), ("../escape", b"x", "file")], "unexpected entry"),
        (
            [("bundle.json", good_meta(), "file"), ("/etc/cron.d/x", b"x", "file")],
            "unexpected entry",
        ),
        (
            [
                ("bundle.json", good_meta(), "file"),
                (f"snapshots/{SID}.tar.gz", b"", tarfile.SYMTYPE),
            ],
            "unexpected entry type",
        ),
        (
            [("bundle.json", good_meta(), "file"), ("snapshots/x.tar.gz", b"x", "file")],
            "unexpected entry",
        ),
        ([("bundle.json", b"{not json", "file")], "not valid JSON"),
        ([("other.json", b"{}", "file")], "unexpected entry"),
        ([("bundle.json", json.dumps({"format": "v0"}).encode(), "file")], "unsupported format"),
        # index lists a snapshot that isn't in the archive
        (
            [
                (
                    "bundle.json",
                    good_meta([{"id": SID, "environment": "shell", "sha256": "0" * 64}]),
                    "file",
                )
            ],
            "does not match",
        ),
        # checksum mismatch
        (
            [
                (
                    "bundle.json",
                    good_meta([{"id": SID, "environment": "shell", "sha256": "0" * 64}]),
                    "file",
                ),
                (f"snapshots/{SID}.tar.gz", b"tampered", "file"),
            ],
            "checksum",
        ),
    ],
)
def test_hostile_bundles_rejected(settings, entries, msg):
    app = make_app(settings)
    with TestClient(app) as c:
        r = c.post("/v1/workspaces/import", content=tar_of(entries), headers=auth())
        assert r.status_code == 422 and msg in str(r.json()["detail"]), r.json()
        assert c.get("/v1/workspaces", headers=auth()).json() == []  # nothing created


def test_not_a_tar_and_compressed_rejected(settings):
    import gzip

    app = make_app(settings)
    with TestClient(app) as c:
        for body in [
            b"plain garbage",
            gzip.compress(tar_of([("bundle.json", good_meta(), "file")])),
        ]:
            r = c.post("/v1/workspaces/import", content=body, headers=auth())
            assert r.status_code == 422 and "uncompressed tar" in r.json()["detail"]


def test_bundle_manifest_must_pass_this_servers_policy(settings):
    import yaml

    m = yaml.safe_load(MINIMAL)
    m["spec"]["environments"][0]["resources"]["cpu"] = 64  # over quota here
    app = make_app(settings)
    with TestClient(app) as c:
        r = c.post(
            "/v1/workspaces/import",
            content=tar_of([("bundle.json", good_meta(manifest=m), "file")]),
            headers=auth(),
        )
        assert r.status_code == 422 and "not allowed on this server" in str(r.json())
        m["spec"]["environments"][0]["privileged"] = True  # unknown field
        r = c.post(
            "/v1/workspaces/import",
            content=tar_of([("bundle.json", good_meta(manifest=m), "file")]),
            headers=auth(),
        )
        assert r.status_code == 422 and "invalid" in str(r.json())


def test_bundle_size_limit(settings):
    app = make_app(settings, max_bundle_bytes=1000)
    with TestClient(app) as c:
        r = c.post("/v1/workspaces/import", content=b"x" * 5000, headers=auth())
        assert r.status_code == 413
