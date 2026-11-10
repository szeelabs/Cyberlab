"""Snapshot storage. Files go in and come out by key; the backend is swappable.

Phase 3 step 2 ships ``LocalSnapshotStore`` (a folder on the control-plane host).
Step 3 adds an S3-compatible store behind the same interface.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Protocol

_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}(/[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}){0,3}$")


class StorageError(RuntimeError):
    pass


class SnapshotStore(Protocol):
    name: str

    def put(self, key: str, source: Path) -> None: ...

    def get(self, key: str, destination: Path) -> None: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


def check_key(key: str) -> str:
    if not _KEY.match(key) or ".." in key.split("/"):
        raise StorageError(f"invalid storage key {key!r}")
    return key


class LocalSnapshotStore:
    """Stores snapshots as files under ``root`` (owner-only permissions)."""

    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        path = (self.root / check_key(key)).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError("key escapes the store")
        return path

    def put(self, key: str, source: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(source, tmp)
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)  # atomic: a key is either complete or absent

    def get(self, key: str, destination: Path) -> None:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"snapshot data missing for {key}")
        shutil.copyfile(path, destination)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class EncryptedSnapshotStore:
    """Wraps any store: data is encrypted before ``put`` and verified on ``get``."""

    def __init__(self, inner: SnapshotStore, key: bytes, workdir: Path) -> None:
        self.inner = inner
        self._key = key
        self._workdir = Path(workdir)
        self._workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.name = f"{inner.name}+aes256gcm"

    def _tmp(self) -> Path:
        import tempfile

        fd, name = tempfile.mkstemp(dir=self._workdir, suffix=".enc")
        os.close(fd)
        return Path(name)

    def put(self, key: str, source: Path) -> None:
        from cyberlab.storage.crypto import encrypt_file

        tmp = self._tmp()
        try:
            encrypt_file(self._key, source, tmp)
            self.inner.put(key, tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def get(self, key: str, destination: Path) -> None:
        from cyberlab.storage.crypto import DecryptionError, decrypt_file

        tmp = self._tmp()
        try:
            self.inner.get(key, tmp)
            try:
                decrypt_file(self._key, tmp, destination)
            except DecryptionError as exc:
                raise StorageError(str(exc)) from exc
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def check(self) -> str:
        return getattr(self.inner, "check", lambda: "ok")()


class S3SnapshotStore:
    """Any S3-compatible service: AWS S3, Cloudflare R2, Backblaze B2, MinIO, ...

    Uploads and downloads stream from/to files (multipart for large snapshots).
    Credentials come from settings or the standard AWS environment; never logged.
    """

    name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            raise StorageError("S3 storage needs: pip install 'cyberlab[s3]'") from exc
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=10,
                read_timeout=120,
            ),
        )

    def _key(self, key: str) -> str:
        return self.prefix + check_key(key)

    def _wrap(self, action: str, exc: Exception) -> StorageError:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        return StorageError(f"S3 {action} failed ({code})")

    def put(self, key: str, source: Path) -> None:
        try:
            self._client.upload_file(str(source), self.bucket, self._key(key))
        except Exception as exc:  # noqa: BLE001 - botocore raises many types
            raise self._wrap("upload", exc) from exc

    def get(self, key: str, destination: Path) -> None:
        try:
            self._client.download_file(self.bucket, self._key(key), str(destination))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap("download", exc) from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:  # noqa: BLE001
            raise self._wrap("delete", exc) from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise self._wrap("lookup", exc) from exc

    def check(self) -> str:
        """Raise StorageError unless the bucket is reachable with these credentials."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception as exc:  # noqa: BLE001
            raise self._wrap("connection check", exc) from exc
        return "ok"


def build_store(settings) -> SnapshotStore:
    """Create the configured snapshot store, wrapped in encryption when a key is set."""
    if settings.snapshot_store == "s3":
        inner: SnapshotStore = S3SnapshotStore(
            settings.s3_bucket,
            prefix=settings.s3_prefix,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=(
                settings.s3_secret_access_key.get_secret_value()
                if settings.s3_secret_access_key
                else None
            ),
        )
    else:
        inner = LocalSnapshotStore(settings.snapshot_dir)
    if settings.snapshot_encryption_key is None:
        return inner
    from cyberlab.storage.crypto import parse_key

    key = parse_key(settings.snapshot_encryption_key.get_secret_value())
    return EncryptedSnapshotStore(inner, key, Path(settings.snapshot_dir) / ".tmp")
