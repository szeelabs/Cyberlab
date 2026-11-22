"""Automatic snapshots: the manifest's ``workspace.snapshotPolicy``.

* ``manual`` (default): only when the user asks.
* ``on-stop``: right after a machine stops (by the user or the reaper's idle timer), so the
  copy is consistent.
* ``daily``: at most once a day per machine, by the periodic job (the reaper loop).

Automatic snapshots are labelled "automatic: ...", and only the newest few per machine are
kept, so they never use up the user's snapshot limit.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cyberlab.config import Settings
from cyberlab.db import EnvironmentRecord, ManifestRevision, Snapshot, Workspace, record_audit
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError
from cyberlab.storage import SnapshotStore, StorageError

log = logging.getLogger("cyberlab")
ACTOR = "system:snapshots"
AUTO_PREFIX = "automatic: "
KEEP_AUTOMATIC = 3
DAY = timedelta(days=1)


class SnapshotRefused(Exception):
    """A snapshot can't be taken (limit reached, nothing to copy, provider can't)."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _tmpdir(settings: Settings) -> Path:
    d = Path(settings.snapshot_dir) / ".tmp"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def take_snapshot(
    db: Session,
    rec: EnvironmentRecord,
    label: str,
    actor: str,
    settings: Settings,
    adapter: ProviderAdapter,
    store: SnapshotStore,
    request_id: str | None = None,
) -> Snapshot:
    """Copy a machine's /workspace into the snapshot store. Raises SnapshotRefused, or the
    provider's or store's error."""
    volume, image = (rec.resources or {}).get("volume"), (rec.resources or {}).get("image")
    if rec.state == EnvState.destroyed or not volume or not image:
        raise SnapshotRefused("environment has no workspace to use")
    if not hasattr(adapter, "export_workspace"):
        raise SnapshotRefused("provider has no snapshot support", 501)
    count = (
        db.scalar(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.workspace_id == rec.workspace_id)
        )
        or 0
    )
    if count >= settings.max_snapshots_per_workspace:
        raise SnapshotRefused(
            f"snapshot limit reached ({settings.max_snapshots_per_workspace}); "
            "delete an old snapshot first"
        )
    snap = Snapshot(
        id=str(uuid.uuid4()),  # needed now: the storage key is built from it
        workspace_id=rec.workspace_id,
        environment_name=rec.name,
        label=label,
        size_bytes=0,
        sha256="",
        store=store.name,
        storage_key="",
        created_by=actor,
    )
    snap.storage_key = f"{rec.workspace_id}/{snap.id}.tar.gz"
    with tempfile.NamedTemporaryFile(dir=_tmpdir(settings), suffix=".tar.gz") as tmp:
        snap.size_bytes = adapter.export_workspace(
            volume, image, tmp.name, settings.max_snapshot_bytes
        )
        snap.sha256 = _sha256(Path(tmp.name))
        store.put(snap.storage_key, Path(tmp.name))
    db.add(snap)
    record_audit(
        db,
        actor=actor,
        action="snapshot.create",
        target_type="snapshot",
        target_id=snap.id,
        detail={"environment": rec.name, "bytes": snap.size_bytes, "label": label},
        request_id=request_id,
    )
    db.commit()
    return snap


def policy_for(db: Session, rec: EnvironmentRecord) -> str:
    rev = db.scalar(
        select(ManifestRevision).where(
            ManifestRevision.workspace_id == rec.workspace_id,
            ManifestRevision.revision == rec.revision,
        )
    )
    workspace = ((rev.content if rev else {}).get("spec") or {}).get("workspace") or {}
    return workspace.get("snapshotPolicy", "manual")


def _prune(db: Session, rec: EnvironmentRecord, store: SnapshotStore) -> None:
    autos = list(
        db.scalars(
            select(Snapshot)
            .where(
                Snapshot.workspace_id == rec.workspace_id,
                Snapshot.environment_name == rec.name,
                Snapshot.label.startswith(AUTO_PREFIX),
            )
            .order_by(Snapshot.created_at.desc())
        )
    )
    for old in autos[KEEP_AUTOMATIC:]:
        try:
            store.delete(old.storage_key)
        except StorageError:
            log.warning("could not delete old automatic snapshot %s", old.id)
            continue
        db.delete(old)
    db.commit()


def automatic(
    db: Session,
    rec: EnvironmentRecord,
    why: str,
    settings: Settings,
    adapter: ProviderAdapter,
    store: SnapshotStore,
) -> Snapshot | None:
    """Take an automatic snapshot, making room among older automatic ones first.
    Never raises: a failed automatic snapshot is logged and audited, nothing else."""
    try:
        _prune_to_fit(db, rec, store, settings)
        snap = take_snapshot(db, rec, f"{AUTO_PREFIX}{why}", ACTOR, settings, adapter, store)
        _prune(db, rec, store)
        return snap
    except (SnapshotRefused, ProviderError, StorageError) as exc:
        db.rollback()
        record_audit(
            db,
            actor=ACTOR,
            action="snapshot.failed",
            target_type="environment",
            target_id=rec.id,
            detail={"name": rec.name, "why": why, "error": str(exc)[:300]},
        )
        db.commit()
        log.warning("automatic snapshot of %s failed: %s", rec.name, exc)
        return None


def _prune_to_fit(
    db: Session, rec: EnvironmentRecord, store: SnapshotStore, settings: Settings
) -> None:
    """At the workspace limit, drop this machine's oldest automatic snapshot (never a
    snapshot the user took)."""
    count = (
        db.scalar(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.workspace_id == rec.workspace_id)
        )
        or 0
    )
    if count < settings.max_snapshots_per_workspace:
        return
    oldest = db.scalar(
        select(Snapshot)
        .where(
            Snapshot.workspace_id == rec.workspace_id,
            Snapshot.environment_name == rec.name,
            Snapshot.label.startswith(AUTO_PREFIX),
        )
        .order_by(Snapshot.created_at)
        .limit(1)
    )
    if oldest is not None:
        store.delete(oldest.storage_key)
        db.delete(oldest)
        db.commit()


def after_stop(db, rec, settings, adapter, store) -> Snapshot | None:
    if policy_for(db, rec) != "on-stop":
        return None
    return automatic(db, rec, "when it stopped", settings, adapter, store)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def run_daily(
    db: Session,
    adapter: ProviderAdapter | None,
    store: SnapshotStore,
    settings: Settings,
    now: datetime | None = None,
) -> list[str]:
    """Snapshot every machine whose workspace asks for daily snapshots and whose newest
    automatic snapshot (or creation) is a day old. Returns the machines' names."""
    if adapter is None:
        return []
    now = now or datetime.now(UTC)
    done = []
    rows = db.scalars(
        select(EnvironmentRecord).where(
            EnvironmentRecord.state.in_([EnvState.running.value, EnvState.stopped.value])
        )
    )
    for rec in list(rows):
        if db.get(Workspace, rec.workspace_id) is None or policy_for(db, rec) != "daily":
            continue
        last = db.scalar(
            select(func.max(Snapshot.created_at)).where(
                Snapshot.workspace_id == rec.workspace_id,
                Snapshot.environment_name == rec.name,
                Snapshot.label.startswith(AUTO_PREFIX),
            )
        )
        since = _utc(last) or _utc(rec.created_at)
        if since is not None and now - since < DAY:
            continue
        if automatic(db, rec, "daily", settings, adapter, store) is not None:
            done.append(rec.name)
    return done
