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
