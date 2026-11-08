"""Workspace files: list, download, upload and delete files in an environment's /workspace.

The environment must be running. Operations run inside it as its own unprivileged user,
so this API can do nothing the browser terminal couldn't. Every change is audited.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, request_id
from cyberlab.api.environments import _env_owned, _require_provider, get_provider
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import EnvironmentRecord, User, record_audit
from cyberlab.execution import EnvState, ProviderAdapter
from cyberlab.execution.docker import FileOpError
from cyberlab.execution.paths import UnsafePath, workspace_path

router = APIRouter(tags=["files"])

STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_a_directory": status.HTTP_409_CONFLICT,
    "is_directory": status.HTTP_409_CONFLICT,
    "not_running": status.HTTP_409_CONFLICT,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "no_space": status.HTTP_507_INSUFFICIENT_STORAGE,
    "failed": status.HTTP_502_BAD_GATEWAY,
}
BASE = "/v1/workspaces/{ws_id}/environments/{env_id}/files"


def _target(
    db: Session, user: User, ws_id: str, env_id: str, provider: ProviderAdapter | None
) -> tuple[ProviderAdapter, EnvironmentRecord]:
    ws = _owned(db, user, ws_id)
    rec = _env_owned(db, ws, env_id)
    adapter = _require_provider(provider)
    if not hasattr(adapter, "list_files"):
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "provider has no file access")
    if rec.provider_ref is None or rec.state != EnvState.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "start the environment first")
    return adapter, rec


def _path(relative: str, *, allow_root: bool = False) -> str:
    try:
        return workspace_path(relative, allow_root=allow_root)
    except UnsafePath as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _fail(exc: FileOpError) -> HTTPException:
    return HTTPException(STATUS.get(exc.kind, 502), str(exc))


@router.get(BASE)
def list_files(
    ws_id: str,
    env_id: str,
    path: str = Query(default="", max_length=1024),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    adapter, rec = _target(db, user, ws_id, env_id, provider)
    full = _path(path, allow_root=True)
    try:
        entries = adapter.list_files(rec.provider_ref, full)
    except FileOpError as exc:
        raise _fail(exc) from exc
    return {"path": full.removeprefix("/workspace").lstrip("/"), "entries": entries}


@router.get(BASE + "/content")
def download_file(
    ws_id: str,
    env_id: str,
    request: Request,
    path: str = Query(max_length=1024),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> Response:
    adapter, rec = _target(db, user, ws_id, env_id, provider)
    full = _path(path)
    try:
        data = adapter.read_file(rec.provider_ref, full, settings.max_file_bytes)
    except FileOpError as exc:
        raise _fail(exc) from exc
    record_audit(
        db,
        actor=user.subject,
        action="file.download",
        target_type="environment",
        target_id=rec.id,
        detail={"path": full, "bytes": len(data)},
        request_id=request_id(request),
    )
    db.commit()
    name = full.rsplit("/", 1)[-1].replace('"', "")
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put(BASE + "/content", status_code=201)
async def upload_file(
    ws_id: str,
    env_id: str,
    request: Request,
    path: str = Query(max_length=1024),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    """Create or replace a file (raw request body). Parent directories are created."""
    adapter, rec = _target(db, user, ws_id, env_id, provider)
    full = _path(path)
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > settings.max_file_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"file is larger than {settings.max_file_bytes} bytes",
            )
    try:
        adapter.write_file(rec.provider_ref, full, data)
    except FileOpError as exc:
        raise _fail(exc) from exc
    record_audit(
        db,
        actor=user.subject,
        action="file.upload",
        target_type="environment",
        target_id=rec.id,
        detail={"path": full, "bytes": len(data)},
        request_id=request_id(request),
    )
    db.commit()
    return {"path": full.removeprefix("/workspace/"), "bytes": len(data)}


@router.delete(BASE, status_code=204)
def delete_file(
    ws_id: str,
    env_id: str,
    request: Request,
    path: str = Query(max_length=1024),
    recursive: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> Response:
    adapter, rec = _target(db, user, ws_id, env_id, provider)
    full = _path(path)  # the workspace root itself can never be deleted
    try:
        adapter.delete_path(rec.provider_ref, full, recursive=recursive)
    except FileOpError as exc:
        raise _fail(exc) from exc
    record_audit(
        db,
        actor=user.subject,
        action="file.delete",
        target_type="environment",
        target_id=rec.id,
        detail={"path": full, "recursive": recursive},
        request_id=request_id(request),
    )
    db.commit()
    return Response(status_code=204)
