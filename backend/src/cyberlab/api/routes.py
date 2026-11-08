from __future__ import annotations

from datetime import datetime

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cyberlab import __version__
from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.config import Settings, get_settings
from cyberlab.db import (
    AuditEvent,
    EnvironmentRecord,
    ManifestRevision,
    PlanApproval,
    Snapshot,
    User,
    Workspace,
    record_audit,
)
from cyberlab.manifest.models import SLUG
from cyberlab.manifest.policy import plan_from_text
from cyberlab.registry import Registry

router = APIRouter()
MANIFEST_TYPES = {
    "application/yaml",
    "application/x-yaml",
    "text/yaml",
    "application/json",
    "text/plain",
}


# ---------------------------------------------------------------- health
@router.get("/healthz", tags=["system"])
def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/readyz", tags=["system"])
def readyz(request: Request, db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    reg: Registry = request.app.state.registry
    return {"status": "ready", "toolPacks": len(reg.tool_packs)}


# ---------------------------------------------------------------- registry (public)
@router.get("/v1/registry/categories", tags=["registry"])
def list_categories(reg: Registry = Depends(get_registry)) -> list[dict]:
    counts: dict[str, int] = {}
    for p in reg.tool_packs.values():
        counts[p.category] = counts.get(p.category, 0) + 1
    return [
        c.model_dump() | {"toolPackCount": counts.get(c.id, 0)} for c in reg.categories.values()
    ]


@router.get("/v1/registry/base-images", tags=["registry"])
def list_base_images(reg: Registry = Depends(get_registry)) -> list[dict]:
    return [b.model_dump(by_alias=True) for b in reg.base_images.values()]


@router.get("/v1/registry/tool-packs", tags=["registry"])
def list_tool_packs(
    category: str | None = Query(default=None, pattern=SLUG),
    reg: Registry = Depends(get_registry),
) -> list[dict]:
    packs = reg.tool_packs.values()
    if category:
        packs = [p for p in packs if p.category == category]
    return [p.model_dump(by_alias=True, mode="json") for p in packs]


@router.get("/v1/registry/tool-packs/{pack_id}", tags=["registry"])
def get_tool_pack(pack_id: str, reg: Registry = Depends(get_registry)) -> dict:
    pack = reg.tool_packs.get(pack_id)
    if pack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool pack not found")
    return pack.model_dump(by_alias=True, mode="json")


# ---------------------------------------------------------------- manifests
async def _manifest_text(request: Request, settings: Settings) -> str:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in MANIFEST_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send the manifest as YAML or JSON"
        )
    body = await request.body()
    if len(body) > settings.max_manifest_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "manifest too large")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "manifest must be UTF-8") from exc


@router.post("/v1/manifests/validate", tags=["manifests"])
async def validate_manifest(
    request: Request,
    _: User = Depends(current_user),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Validate a manifest and return findings plus a resource estimate. Provisions nothing."""
    text = await _manifest_text(request, settings)
    plan = plan_from_text(text, reg, settings.quotas, settings.max_manifest_bytes)
    return plan.to_dict()


# ---------------------------------------------------------------- workspaces
class WorkspaceIn(BaseModel):
    name: str = Field(pattern=SLUG)
    description: str = Field(default="", max_length=2000)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    manifest_revision: int | None = None


def _latest_revision(db: Session, ws_id: str) -> ManifestRevision | None:
    return db.scalar(
        select(ManifestRevision)
        .where(ManifestRevision.workspace_id == ws_id)
        .order_by(ManifestRevision.revision.desc())
        .limit(1)
    )


def _out(db: Session, ws: Workspace) -> WorkspaceOut:
    rev = _latest_revision(db, ws.id)
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        description=ws.description,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
        manifest_revision=rev.revision if rev else None,
    )


def _owned(db: Session, user: User, ws_id: str) -> Workspace:
    ws = db.get(Workspace, ws_id)
    # Same 404 for "missing" and "not yours" so IDs can't be probed.
    if ws is None or ws.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    return ws


@router.get("/v1/me", tags=["identity"])
def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "subject": user.subject, "email": user.email}


@router.get("/v1/workspaces", tags=["workspaces"], response_model=list[WorkspaceOut])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Workspace).where(Workspace.owner_id == user.id).order_by(Workspace.created_at)
    )
    return [_out(db, w) for w in rows]


@router.post("/v1/workspaces", tags=["workspaces"], status_code=201, response_model=WorkspaceOut)
def create_workspace(
    body: WorkspaceIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ws = Workspace(owner_id=user.id, name=body.name, description=body.description)
    db.add(ws)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "workspace name already exists") from exc
    record_audit(
        db,
        actor=user.subject,
        action="workspace.create",
        target_type="workspace",
        target_id=ws.id,
        detail={"name": ws.name},
        request_id=request_id(request),
    )
    db.commit()
    return _out(db, ws)


@router.get("/v1/workspaces/{ws_id}", tags=["workspaces"], response_model=WorkspaceOut)
def get_workspace(ws_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, _owned(db, user, ws_id))


@router.delete("/v1/workspaces/{ws_id}", tags=["workspaces"], status_code=204)
def delete_workspace(
    ws_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    ws = _owned(db, user, ws_id)
    live = db.scalar(
        select(EnvironmentRecord).where(
            EnvironmentRecord.workspace_id == ws.id,
            EnvironmentRecord.state != "destroyed",
        )
    )
    if live is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "destroy the workspace's environments first")
    store = request.app.state.snapshot_store
    for snap in db.scalars(select(Snapshot).where(Snapshot.workspace_id == ws.id)):
        store.delete(snap.storage_key)  # snapshot data goes with the workspace
    for model in (EnvironmentRecord, PlanApproval, ManifestRevision, Snapshot):
        db.query(model).filter(model.workspace_id == ws.id).delete()
    db.delete(ws)
    record_audit(
        db,
        actor=user.subject,
        action="workspace.delete",
        target_type="workspace",
        target_id=ws_id,
        request_id=request_id(request),
    )
    db.commit()
    return Response(status_code=204)


@router.put("/v1/workspaces/{ws_id}/manifest", tags=["workspaces"])
async def import_manifest(
    ws_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Import a manifest into a workspace as a new revision. Invalid manifests are rejected."""
    ws = _owned(db, user, ws_id)
    text = await _manifest_text(request, settings)
    plan = plan_from_text(text, reg, settings.quotas, settings.max_manifest_bytes)
    if not plan.valid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, plan.to_dict())
    current = (
        db.scalar(
            select(func.max(ManifestRevision.revision)).where(
                ManifestRevision.workspace_id == ws.id
            )
        )
        or 0
    )
    rev = ManifestRevision(
        workspace_id=ws.id, revision=current + 1, content=plan.manifest, created_by=user.subject
    )
    db.add(rev)
    record_audit(
        db,
        actor=user.subject,
        action="manifest.import",
        target_type="workspace",
        target_id=ws.id,
        detail={"revision": rev.revision, "requiresApproval": plan.requires_approval},
        request_id=request_id(request),
    )
    db.commit()
    return {"revision": rev.revision} | plan.to_dict()


@router.get("/v1/workspaces/{ws_id}/manifest", tags=["workspaces"])
def export_manifest(
    ws_id: str,
    format: str = Query(default="yaml", pattern="^(yaml|json)$"),
    revision: int | None = Query(default=None, ge=1),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ws = _owned(db, user, ws_id)
    q = select(ManifestRevision).where(ManifestRevision.workspace_id == ws.id)
    if revision is not None:
        rev = db.scalar(q.where(ManifestRevision.revision == revision))
    else:
        rev = _latest_revision(db, ws.id)
    if rev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no manifest revision")
    if format == "json":
        return rev.content
    text = yaml.safe_dump(rev.content, sort_keys=False)
    return Response(
        text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{ws.name}.universe.yaml"'},
    )


@router.get("/v1/audit", tags=["audit"])
def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.actor_subject == user.subject)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": e.id,
            "action": e.action,
            "targetType": e.target_type,
            "targetId": e.target_id,
            "detail": e.detail,
            "requestId": e.request_id,
            "createdAt": e.created_at.isoformat(),
        }
        for e in rows
    ]
