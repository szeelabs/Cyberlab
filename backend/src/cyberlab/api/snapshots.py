"""Snapshots: save an environment's /workspace and restore it later.

A snapshot is a gzip-compressed tar created by a locked-down helper container (no network,
no capabilities, read-only access to the workspace). The archive goes to the snapshot store
with its SHA-256 recorded, and is verified before any restore. Restores need the target
environment to be stopped, so files are never swapped under running programs.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, request_id
from cyberlab.api.environments import _env_owned, _require_provider, get_provider
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import EnvironmentRecord, Snapshot, User, Workspace, record_audit
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError
from cyberlab.execution.docker import FileOpError
from cyberlab.reaper import touch
from cyberlab.storage import SnapshotStore, StorageError

router = APIRouter(tags=["snapshots"])


def get_store(request: Request) -> SnapshotStore:
    return request.app.state.snapshot_store


class SnapshotIn(BaseModel):
    label: str = Field(default="", max_length=200)


class RestoreIn(BaseModel):
    snapshot_id: str = Field(alias="snapshotId", max_length=36)


def _out(s: Snapshot) -> dict:
    return {
        "id": s.id,
        "environment": s.environment_name,
        "label": s.label,
        "sizeBytes": s.size_bytes,
        "sha256": s.sha256,
        "store": s.store,
        "createdAt": s.created_at.isoformat(),
        "createdBy": s.created_by,
    }


def _volume_and_image(rec: EnvironmentRecord) -> tuple[str, str]:
    volume, image = rec.resources.get("volume"), rec.resources.get("image")
    if rec.state == EnvState.destroyed or not volume or not image:
        raise HTTPException(status.HTTP_409_CONFLICT, "environment has no workspace to use")
    return volume, image


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


def _adapter(provider: ProviderAdapter | None) -> ProviderAdapter:
    adapter = _require_provider(provider)
    if not hasattr(adapter, "export_workspace"):
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "provider has no snapshot support")
    return adapter


@router.get("/v1/system/storage")
def storage_status(
    _: User = Depends(current_user), store: SnapshotStore = Depends(get_store)
) -> dict:
    """Which snapshot store is configured, whether it is reachable, and if it is encrypted."""
    try:
        getattr(store, "check", lambda: "ok")()
        connected, detail = True, "ok"
    except StorageError as exc:
        connected, detail = False, str(exc)
    return {
        "store": store.name,
        "encrypted": store.name.endswith("+aes256gcm"),
        "connected": connected,
        "status": "CONNECTED" if connected else "NOT CONNECTED",
        "detail": detail,
    }


@router.post("/v1/workspaces/{ws_id}/environments/{env_id}/snapshots", status_code=201)
def create_snapshot(
    ws_id: str,
    env_id: str,
    body: SnapshotIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
    store: SnapshotStore = Depends(get_store),
) -> dict:
    ws = _owned(db, user, ws_id)
    rec = _env_owned(db, ws, env_id)
    volume, image = _volume_and_image(rec)
    adapter = _adapter(provider)
    count = (
        db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.workspace_id == ws.id))
        or 0
    )
    if count >= settings.max_snapshots_per_workspace:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"snapshot limit reached ({settings.max_snapshots_per_workspace}); "
            "delete an old snapshot first",
        )
    snap = Snapshot(
        id=str(uuid.uuid4()),  # needed now: the storage key is built from it before saving
        workspace_id=ws.id,
        environment_name=rec.name,
        label=body.label,
        size_bytes=0,
        sha256="",
        store=store.name,
        storage_key="",
        created_by=user.subject,
    )
    snap.storage_key = f"{ws.id}/{snap.id}.tar.gz"
    with tempfile.NamedTemporaryFile(dir=_tmpdir(settings), suffix=".tar.gz") as tmp:
        try:
            snap.size_bytes = adapter.export_workspace(
                volume, image, tmp.name, settings.max_snapshot_bytes
            )
            snap.sha256 = _sha256(Path(tmp.name))
            store.put(snap.storage_key, Path(tmp.name))
        except FileOpError as exc:
            code = 413 if exc.kind == "too_large" else 502
            raise HTTPException(code, str(exc)) from exc
        except (ProviderError, StorageError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    db.add(snap)
    touch(db, rec.id)
    record_audit(
        db,
        actor=user.subject,
        action="snapshot.create",
        target_type="snapshot",
        target_id=snap.id,
        detail={"environment": rec.name, "bytes": snap.size_bytes, "label": body.label},
        request_id=request_id(request),
    )
    db.commit()
    return _out(snap)


@router.get("/v1/workspaces/{ws_id}/snapshots")
def list_snapshots(
    ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    ws = _owned(db, user, ws_id)
    rows = db.scalars(
        select(Snapshot).where(Snapshot.workspace_id == ws.id).order_by(Snapshot.created_at.desc())
    )
    return [_out(s) for s in rows]


def _snapshot_owned(db: Session, ws: Workspace, snapshot_id: str) -> Snapshot:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None or snap.workspace_id != ws.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "snapshot not found")
    return snap


@router.post("/v1/workspaces/{ws_id}/environments/{env_id}/restore")
def restore_snapshot(
    ws_id: str,
    env_id: str,
    body: RestoreIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
    store: SnapshotStore = Depends(get_store),
) -> dict:
    """Replace the environment's /workspace with a snapshot. The environment must be stopped."""
    ws = _owned(db, user, ws_id)
    rec = _env_owned(db, ws, env_id)
    snap = _snapshot_owned(db, ws, body.snapshot_id)
    volume, image = _volume_and_image(rec)
    adapter = _adapter(provider)
    if rec.provider_ref:
        rec.state = adapter.inspect(rec.provider_ref).value
        db.commit()
    if snap.store != store.name:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"this snapshot is in the {snap.store!r} store, but the server now uses "
            f"{store.name!r}; switch back or re-create the snapshot",
        )
    if rec.state != EnvState.stopped:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "stop the environment before restoring a snapshot"
        )
    with tempfile.NamedTemporaryFile(dir=_tmpdir(settings), suffix=".tar.gz") as tmp:
        try:
            store.get(snap.storage_key, Path(tmp.name))
        except StorageError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        if _sha256(Path(tmp.name)) != snap.sha256:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "snapshot data failed its integrity check; not restored"
            )
        try:
            adapter.import_workspace(volume, image, tmp.name)
        except ProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    record_audit(
        db,
        actor=user.subject,
        action="snapshot.restore",
        target_type="environment",
        target_id=rec.id,
        detail={"snapshot": snap.id, "environment": rec.name},
        request_id=request_id(request),
    )
    db.commit()
    return {"restored": snap.id, "environment": rec.name}


@router.delete("/v1/workspaces/{ws_id}/snapshots/{snapshot_id}", status_code=204)
def delete_snapshot(
    ws_id: str,
    snapshot_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: SnapshotStore = Depends(get_store),
) -> Response:
    ws = _owned(db, user, ws_id)
    snap = _snapshot_owned(db, ws, snapshot_id)
    try:
        store.delete(snap.storage_key)
    except StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    db.delete(snap)
    record_audit(
        db,
        actor=user.subject,
        action="snapshot.delete",
        target_type="snapshot",
        target_id=snapshot_id,
        request_id=request_id(request),
    )
    db.commit()
    return Response(status_code=204)
