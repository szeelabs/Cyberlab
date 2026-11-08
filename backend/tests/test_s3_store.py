"""S3 snapshot storage against moto's S3 server (real S3 protocol over HTTP, no cloud)."""

import socket

import boto3
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from cyberlab.config import Settings
from cyberlab.main import create_app
from cyberlab.storage import S3SnapshotStore, StorageError
from cyberlab.storage.crypto import generate_key

from .conftest import auth
from .test_environments_api import MINIMAL, YAML
from .test_snapshots import SnapshotProvider

CREDS = {"access_key_id": "test-access", "secret_access_key": "test-secret"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def endpoint():
    from moto.server import ThreadedMotoServer

    port = free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def bucket(endpoint):
    import uuid

    name = f"cyberlab-{uuid.uuid4().hex[:12]}"
    boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    ).create_bucket(Bucket=name)
    return name


def raw(endpoint, bucket, key):
    c = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )
    return c.get_object(Bucket=bucket, Key=key)["Body"].read()


def test_s3_store_roundtrip(endpoint, bucket, tmp_path):
    store = S3SnapshotStore(
        bucket, prefix="snaps", endpoint_url=endpoint, region="us-east-1", **CREDS
    )
    src = tmp_path / "s.tar.gz"
    src.write_bytes(b"\x00\x01payload" * 1000)
    store.put("ws1/abc.tar.gz", src)
    assert store.exists("ws1/abc.tar.gz") and not store.exists("ws1/nope.tar.gz")
    assert raw(endpoint, bucket, "snaps/ws1/abc.tar.gz") == src.read_bytes()
    store.get("ws1/abc.tar.gz", tmp_path / "back")
    assert (tmp_path / "back").read_bytes() == src.read_bytes()
    store.delete("ws1/abc.tar.gz")
    assert not store.exists("ws1/abc.tar.gz")
    assert store.check() == "ok"


def test_s3_errors_are_clear(endpoint, tmp_path):
    store = S3SnapshotStore("no-such-bucket", endpoint_url=endpoint, region="us-east-1", **CREDS)
    with pytest.raises(StorageError, match="S3 connection check failed"):
        store.check()
    with pytest.raises(StorageError, match="S3 download failed"):
        store.get("ws/x.tar.gz", tmp_path / "x")
    with pytest.raises(StorageError):
        store.exists("../escape")


def s3_settings(settings, endpoint, bucket, **extra):
    return settings.model_copy(
        update={
            "snapshot_store": "s3",
            "s3_bucket": bucket,
            "s3_endpoint_url": endpoint,
            "s3_region": "us-east-1",
            "s3_access_key_id": "x",
            "s3_secret_access_key": SecretStr("x"),
            "snapshot_encryption_key": SecretStr(generate_key()),
            **extra,
        }
    )


def test_snapshots_on_s3_are_encrypted_end_to_end(settings, endpoint, bucket):
    app = create_app(s3_settings(settings, endpoint, bucket))
    app.state.provider = provider = SnapshotProvider()
    provider.volume = b"top secret workspace contents"
    with TestClient(app) as c:
        st = c.get("/v1/system/storage", headers=auth()).json()
        assert st == {
            "store": "s3+aes256gcm",
            "encrypted": True,
            "connected": True,
            "status": "CONNECTED",
            "detail": "ok",
        }
        ws = c.post("/v1/workspaces", json={"name": "s3"}, headers=auth()).json()["id"]
        c.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
        (env,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
            "environments"
        ]
        snap = c.post(
            f"/v1/workspaces/{ws}/environments/{env['id']}/snapshots", json={}, headers=auth()
        ).json()
        assert snap["store"] == "s3+aes256gcm"
        stored = raw(endpoint, bucket, f"cyberlab-snapshots/{ws}/{snap['id']}.tar.gz")
        assert stored.startswith(b"CLSNAP1\0") and b"top secret" not in stored

        provider.volume = b"overwritten"
        for ref in provider.states:
            provider.states[ref] = provider.states[ref].__class__("stopped")
        r = c.post(
            f"/v1/workspaces/{ws}/environments/{env['id']}/restore",
            json={"snapshotId": snap["id"]},
            headers=auth(),
        )
        assert r.status_code == 200
        assert provider.volume == b"top secret workspace contents"


def test_storage_status_not_connected(settings, endpoint):
    app = create_app(s3_settings(settings, endpoint, "bucket-that-does-not-exist"))
    with TestClient(app) as c:
        st = c.get("/v1/system/storage", headers=auth()).json()
        assert st["status"] == "NOT CONNECTED" and st["connected"] is False
        assert "x" not in st["detail"].split()  # no credentials echoed back


def test_snapshot_from_another_store_gives_clear_error(settings, endpoint, bucket):
    app = create_app(settings)  # local store
    app.state.provider = provider = SnapshotProvider()
    with TestClient(app) as c:
        ws = c.post("/v1/workspaces", json={"name": "mv"}, headers=auth()).json()["id"]
        c.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
        (env,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
            "environments"
        ]
        snap = c.post(
            f"/v1/workspaces/{ws}/environments/{env['id']}/snapshots", json={}, headers=auth()
        ).json()
        app.state.snapshot_store = create_app(
            s3_settings(settings, endpoint, bucket)
        ).state.snapshot_store
        for ref in provider.states:
            provider.states[ref] = provider.states[ref].__class__("stopped")
        r = c.post(
            f"/v1/workspaces/{ws}/environments/{env['id']}/restore",
            json={"snapshotId": snap["id"]},
            headers=auth(),
        )
        assert r.status_code == 409 and "'local' store" in r.json()["detail"]


def test_s3_requires_bucket_and_encryption():
    with pytest.raises(ValidationError, match="S3_BUCKET is required"):
        Settings(snapshot_store="s3", snapshot_encryption_key=SecretStr(generate_key()))
    with pytest.raises(ValidationError, match="never leave this server unencrypted"):
        Settings(snapshot_store="s3", s3_bucket="b")
    with pytest.raises(ValidationError, match="32 bytes"):
        Settings(snapshot_encryption_key=SecretStr("too-short"))
