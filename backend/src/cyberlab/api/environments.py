"""Approvals and environment lifecycle (Phase 2)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab import quotas
from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import (
    EnvironmentRecord,
    ManifestRevision,
    PlanApproval,
    User,
    Workspace,
    record_audit,
)
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError, ProvisionRequest
from cyberlab.manifest.models import NetworkMode, UniverseManifest
from cyberlab.manifest.policy import Plan, Severity, evaluate
from cyberlab.registry import Registry

router = APIRouter(tags=["environments"])
ACTIVE = {EnvState.provisioning, EnvState.running, EnvState.stopped}


def get_provider(request: Request) -> ProviderAdapter | None:
    return request.app.state.provider


def _not_connected(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        {"status": "not_connected", "detail": detail},
    )


def _require_provider(provider: ProviderAdapter | None) -> ProviderAdapter:
    if provider is None:
        raise _not_connected("no execution provider is configured (CYBERLAB_EXECUTION_PROVIDER)")
    st = provider.status()
    if not st.connected:
        raise _not_connected(st.detail)
    return provider


def _revision(db: Session, ws: Workspace, revision: int | None) -> ManifestRevision:
    q = select(ManifestRevision).where(ManifestRevision.workspace_id == ws.id)
    q = (
        q.where(ManifestRevision.revision == revision)
        if revision
        else q.order_by(ManifestRevision.revision.desc()).limit(1)
    )
    rev = db.scalar(q)
    if rev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no manifest revision")
    return rev


def _plan(rev: ManifestRevision, reg: Registry, settings: Settings) -> Plan:
    # Re-evaluate against the current registry and quotas; they may have changed.
    return evaluate(UniverseManifest.model_validate(rev.content), reg, settings.quotas)


def _approval_codes(plan: Plan) -> set[str]:
    return {f.code for f in plan.findings if f.severity == Severity.approval}


def _workspace_key(ws: Workspace) -> str:
    return ws.id.replace("-", "")[:12]


def _env_out(e: EnvironmentRecord) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "revision": e.revision,
        "provider": e.provider,
        "state": e.state,
        "lastError": e.last_error,
        "resources": e.resources,
        "toolPacks": e.spec.get("toolPacks", []),
        "desktop": bool((e.spec.get("access") or {}).get("desktop")),
        # True once the environment runs an image built with its tool packs installed.
        "toolPacksInstalled": bool(e.resources.get("image")) and e.state != EnvState.failed,
        "image": e.resources.get("image"),
        "lastActivityAt": e.last_activity_at.isoformat() if e.last_activity_at else None,
        "expiresAt": e.expires_at.isoformat() if e.expires_at else None,
        "createdAt": e.created_at.isoformat(),
        "updatedAt": e.updated_at.isoformat(),
    }


def _env_owned(db: Session, ws: Workspace, env_id: str) -> EnvironmentRecord:
    rec = db.get(EnvironmentRecord, env_id)
    if rec is None or rec.workspace_id != ws.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "environment not found")
    return rec


# ---------------------------------------------------------------- provider status


def _rewrite(base) -> tuple[str, str] | None:
    sr = base.source_rewrite
    return (sr.replace, sr.with_) if sr else None


@router.get("/v1/me/usage")
def my_usage(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """What you're using now (across all your workspaces) and your limits."""
    return {"usage": quotas.usage(db, user).to_dict(), "limits": quotas.limits(settings.quotas)}


@router.get("/v1/system/execution")
def execution_status(
    _: User = Depends(current_user),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    if provider is None:
        return {
            "provider": None,
            "connected": False,
            "status": "NOT CONNECTED",
            "detail": "no execution provider is configured",
        }
    st = provider.status()
    return {
        "provider": st.name,
        "connected": st.connected,
        "status": "CONNECTED" if st.connected else "NOT CONNECTED",
        "detail": st.detail,
        "costClass": st.cost_class.value,
    }


# ---------------------------------------------------------------- approvals
class ApprovalIn(BaseModel):
    revision: int = Field(ge=1)
    approve: list[str] = Field(max_length=50)


@router.get("/v1/workspaces/{ws_id}/plan")
def get_plan(
    ws_id: str,
    revision: int | None = Query(default=None, ge=1),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
) -> dict:
    ws = _owned(db, user, ws_id)
    rev = _revision(db, ws, revision)
    plan = _plan(rev, reg, settings)
    approval = db.scalar(
        select(PlanApproval).where(
            PlanApproval.workspace_id == ws.id, PlanApproval.revision == rev.revision
        )
    )
    needed = _approval_codes(plan)
    approved = set(approval.approved_codes) if approval else set()
    out = plan.to_dict()
    out.pop("manifest", None)
    return out | {
        "revision": rev.revision,
        "approvalCodes": sorted(needed),
        "approved": needed <= approved,
    }


@router.post("/v1/workspaces/{ws_id}/approvals", status_code=201)
def approve_plan(
    ws_id: str,
    body: ApprovalIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Explicitly approve every approval-level finding of one manifest revision."""
    ws = _owned(db, user, ws_id)
    rev = _revision(db, ws, body.revision)
    plan = _plan(rev, reg, settings)
    if not plan.valid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "revision is not valid")
    needed = _approval_codes(plan)
    given = set(body.approve)
    if given != needed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "detail": "approve exactly the items this revision requires",
                "required": sorted(needed),
                "unknown": sorted(given - needed),
            },
        )
    existing = db.scalar(
        select(PlanApproval).where(
            PlanApproval.workspace_id == ws.id, PlanApproval.revision == rev.revision
        )
    )
    if existing:
        db.delete(existing)
        db.flush()
    db.add(
        PlanApproval(
            workspace_id=ws.id,
            revision=rev.revision,
            approved_codes=sorted(needed),
            approved_by=user.subject,
        )
    )
    record_audit(
        db,
        actor=user.subject,
        action="plan.approve",
        target_type="workspace",
        target_id=ws.id,
        detail={"revision": rev.revision, "codes": sorted(needed)},
        request_id=request_id(request),
    )
    db.commit()
    return {"revision": rev.revision, "approved": sorted(needed)}


# ---------------------------------------------------------------- environments
class ProvisionIn(BaseModel):
    revision: int | None = Field(default=None, ge=1)


@router.get("/v1/workspaces/{ws_id}/environments")
def list_environments(
    ws_id: str,
    refresh: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> list[dict]:
    ws = _owned(db, user, ws_id)
    rows = list(
        db.scalars(
            select(EnvironmentRecord)
            .where(EnvironmentRecord.workspace_id == ws.id)
            .order_by(EnvironmentRecord.created_at)
        )
    )
    if refresh and provider is not None:
        for r in rows:
            if r.provider_ref and r.state != EnvState.destroyed:
                try:
                    r.state = provider.inspect(r.provider_ref).value
                except ProviderError as exc:
                    r.last_error = str(exc)
        db.commit()
    return [_env_out(r) for r in rows]


@router.post("/v1/workspaces/{ws_id}/environments", status_code=201)
def provision_environments(
    ws_id: str,
    body: ProvisionIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    """Provision every environment of a validated (and, if required, approved) revision."""
    ws = _owned(db, user, ws_id)
    rev = _revision(db, ws, body.revision)
    plan = _plan(rev, reg, settings)
    if not plan.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"detail": "revision is not valid", "plan": plan.to_dict()},
        )
    needed = _approval_codes(plan)
    approval = db.scalar(
        select(PlanApproval).where(
            PlanApproval.workspace_id == ws.id, PlanApproval.revision == rev.revision
        )
    )
    if needed and (approval is None or not needed <= set(approval.approved_codes)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"detail": "this revision needs approval first", "required": sorted(needed)},
        )
    active = db.scalar(
        select(EnvironmentRecord).where(
            EnvironmentRecord.workspace_id == ws.id,
            EnvironmentRecord.state.in_([s.value for s in ACTIVE]),
        )
    )
    if active is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "workspace already has environments; destroy them first"
        )
    manifest = UniverseManifest.model_validate(rev.content)
    specs = [e.model_dump(mode="json", by_alias=True) for e in manifest.spec.environments]
    problems = quotas.check_new(quotas.usage(db, user), specs, settings.quotas)
    if problems:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"detail": "this would exceed your resource limits", "problems": problems},
        )
    adapter = _require_provider(provider)

    proxy_urls, egress_error = _configure_egress(adapter, ws, manifest, settings)
    created = []
    for env in manifest.spec.environments:
        rec = EnvironmentRecord(
            workspace_id=ws.id,
            revision=rev.revision,
            name=env.name,
            provider=adapter.name,
            state=EnvState.provisioning.value,
            spec=env.model_dump(mode="json", by_alias=True),
            last_activity_at=datetime.now(UTC),
            expires_at=(
                datetime.now(UTC) + timedelta(hours=env.lifecycle.ttl_hours)
                if env.lifecycle.ttl_hours
                else None
            ),
        )
        db.add(rec)
        db.commit()  # record intent before touching the provider
        try:
            if env.network.mode == NetworkMode.restricted_egress and egress_error:
                raise ProviderError(egress_error)
            result = adapter.provision(
                ProvisionRequest(
                    workspace_key=_workspace_key(ws),
                    environment=env,
                    image=reg.base_images[env.base_image].image,
                    family=reg.base_images[env.base_image].family,
                    source_rewrite=_rewrite(reg.base_images[env.base_image]),
                    tool_packs=tuple(reg.tool_packs[p] for p in env.tool_packs),
                    internet_approved=env.network.mode == NetworkMode.internet
                    and "security.internet-egress" in needed,
                    egress_proxy_url=proxy_urls.get(env.name),
                )
            )
            rec.provider_ref = result.provider_ref
            rec.state = result.state.value
            rec.resources = result.resource_names
        except ProviderError as exc:
            rec.state = EnvState.failed.value
            rec.last_error = str(exc)
        record_audit(
            db,
            actor=user.subject,
            action="environment.provision",
            target_type="environment",
            target_id=rec.id,
            detail={"name": env.name, "state": rec.state, "revision": rev.revision},
            request_id=request_id(request),
        )
        db.commit()
        created.append(rec)
    return {"revision": rev.revision, "environments": [_env_out(r) for r in created]}


def _configure_egress(
    adapter: ProviderAdapter, ws: Workspace, manifest: UniverseManifest, settings: Settings
) -> tuple[dict[str, str], str | None]:
    """Start the workspace egress proxy for restricted-egress environments.

    Each such environment gets a fresh random token; the proxy only learns its SHA-256.
    Returns ({env name: proxy URL with credentials}, error message or None).
    """
    restricted = [
        e for e in manifest.spec.environments if e.network.mode == NetworkMode.restricted_egress
    ]
    if not restricted:
        return {}, None
    configure = getattr(adapter, "configure_egress", None)
    if configure is None:
        return {}, f"provider {adapter.name!r} does not support restricted egress"
    tokens = {e.name: secrets.token_urlsafe(24) for e in restricted}
    clients = {
        hashlib.sha256(tokens[e.name].encode()).hexdigest(): {
            "name": e.name,
            "hosts": list(e.network.allowed_egress),
        }
        for e in restricted
    }
    try:
        address = configure(
            _workspace_key(ws), clients, allow_private=settings.egress_allow_private
        )
    except ProviderError as exc:
        return {}, f"egress proxy could not start: {exc}"
    return {name: f"http://{name}:{tok}@{address}" for name, tok in tokens.items()}, None


@router.get("/v1/workspaces/{ws_id}/egress-log")
def egress_log(
    ws_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> list[dict]:
    """Recent egress decisions (allowed / denied destinations) for this workspace."""
    ws = _owned(db, user, ws_id)
    adapter = _require_provider(provider)
    reader = getattr(adapter, "egress_log", None)
    if reader is None:
        return []
    return reader(_workspace_key(ws))


def _lifecycle(action: str):
    def handler(
        ws_id: str,
        env_id: str,
        request: Request,
        keep_workspace: bool = True,
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
        provider: ProviderAdapter | None = Depends(get_provider),
    ) -> dict:
        ws = _owned(db, user, ws_id)
        rec = _env_owned(db, ws, env_id)
        if rec.state == EnvState.destroyed:
            raise HTTPException(status.HTTP_409_CONFLICT, "environment is destroyed")
        if rec.provider_ref is None:
            # Provisioning never reached the provider; nothing to act on.
            if action != "destroy":
                raise HTTPException(status.HTTP_409_CONFLICT, "environment was never created")
            rec.state = EnvState.destroyed.value
        else:
            adapter = _require_provider(provider)
            try:
                if action == "start":
                    if rec.state != EnvState.running:
                        problems = quotas.check_new(
                            quotas.usage(db, user, exclude={rec.id}),
                            [rec.spec],
                            settings.quotas,
                            new_environments=False,
                        )
                        if problems:
                            raise HTTPException(
                                status.HTTP_409_CONFLICT,
                                {
                                    "detail": "starting this would exceed your resource limits",
                                    "problems": problems,
                                },
                            )
                    rec.state = adapter.start(rec.provider_ref).value
                    rec.last_activity_at = datetime.now(UTC)  # restart the idle timer
                elif action == "stop":
                    rec.state = adapter.stop(rec.provider_ref).value
                else:
                    rec.state = adapter.destroy(
                        rec.provider_ref, remove_workspace_volume=not keep_workspace
                    ).value
                    db.flush()
                    still_active = db.scalar(
                        select(EnvironmentRecord).where(
                            EnvironmentRecord.workspace_id == ws.id,
                            EnvironmentRecord.state.in_([s.value for s in ACTIVE]),
                        )
                    )
                    cleanup = getattr(adapter, "remove_workspace_networks", None)
                    if still_active is None and cleanup is not None:
                        cleanup(_workspace_key(ws))
                rec.last_error = None
            except ProviderError as exc:
                rec.last_error = str(exc)
                db.commit()
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        record_audit(
            db,
            actor=user.subject,
            action=f"environment.{action}",
            target_type="environment",
            target_id=rec.id,
            detail={"name": rec.name, "state": rec.state},
            request_id=request_id(request),
        )
        db.commit()
        return _env_out(rec)

    return handler


router.add_api_route(
    "/v1/workspaces/{ws_id}/environments/{env_id}/start", _lifecycle("start"), methods=["POST"]
)
router.add_api_route(
    "/v1/workspaces/{ws_id}/environments/{env_id}/stop", _lifecycle("stop"), methods=["POST"]
)
router.add_api_route(
    "/v1/workspaces/{ws_id}/environments/{env_id}", _lifecycle("destroy"), methods=["DELETE"]
)
