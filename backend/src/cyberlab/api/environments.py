"""Approvals and environment lifecycle (Phase 2)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab import autosnap, quotas
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
log = logging.getLogger("cyberlab")
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
        # what is happening while the machine is being created (background creation)
        "progress": (e.resources or {}).get("progress") if e.state == "provisioning" else None,
        "resources": e.resources,
        "toolPacks": e.spec.get("toolPacks", []),
        "installed": [
            {"path": i.get("path"), "sha256": i.get("sha256")}
            for i in (e.resources or {}).get("installers", [])
        ],
        "desktop": bool((e.spec.get("access") or {}).get("desktop")),
        "terminal": bool((e.spec.get("access") or {}).get("terminal", True))
        and e.spec.get("role") != "target",
        "role": e.spec.get("role", "workstation"),
        "vm": e.spec.get("vm"),
        # True once the environment runs an image built with its tool packs installed.
        "toolPacksInstalled": bool(e.resources.get("image")) and e.state != EnvState.failed,
        "image": e.resources.get("image"),
        "lastActivityAt": e.last_activity_at.isoformat() if e.last_activity_at else None,
        "expiresAt": e.expires_at.isoformat() if e.expires_at else None,
        "createdAt": e.created_at.isoformat(),
        "updatedAt": e.updated_at.isoformat(),
    }


def _require_workstation(rec: EnvironmentRecord, what: str) -> None:
    """Practice targets are reached only over the lab network, like a real remote machine:
    no terminal, desktop, files, installs or snapshots (they would reveal the answers)."""
    if rec.spec.get("role") == "target":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{rec.name} is a practice target: no {what}. Reach it over the lab network "
            "from your own machine instead.",
        )
    if what == "terminal" and not (rec.spec.get("access") or {}).get("terminal", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{rec.name} has no terminal access")


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
        "architecture": st.architecture,
        "isolation": list(st.isolation),
        "fullVm": st.full_vm,
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
    wait: bool = Query(default=True, description="false: return at once, create in the background"),
) -> dict:
    """Provision every environment of a validated (and, if required, approved) revision.

    With wait=false the machines are created in the background: the answer comes at once
    with every machine in state "provisioning", and each one's "progress" says what is
    happening (the first build of a system can take minutes)."""
    ws = _owned(db, user, ws_id)
    rev, records = start_provisioning(
        ws, body.revision, request, user, db, reg, settings, provider, wait=wait
    )
    return {"revision": rev, "environments": [_env_out(r) for r in records]}


def start_provisioning(
    ws: Workspace,
    revision: int | None,
    request: Request,
    user: User,
    db: Session,
    reg: Registry,
    settings: Settings,
    provider: ProviderAdapter | None,
    *,
    wait: bool = True,
    after: Callable[[Session], None] | None = None,
) -> tuple[int, list[EnvironmentRecord]]:
    """Check everything, record the machines, then create them (now, or in the background).
    `after` runs once all machines were attempted (e.g. to place a lab's files)."""
    body = ProvisionIn(revision=revision)
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
    arch = adapter.status().architecture
    wrong_arch = [
        reg.base_images[e.base_image]
        for e in manifest.spec.environments
        if arch in ("amd64", "arm64") and arch not in reg.base_images[e.base_image].architectures
    ]
    if wrong_arch:
        names = ", ".join(sorted({b.name for b in wrong_arch}))
        kind = "ARM (arm64)" if arch == "arm64" else "x86 (amd64)"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{names} doesn't run on this server's processor type, {kind}. "
            "Choose another operating system for that machine in Setup.",
        )

    st_now = adapter.status()
    if any(e.vm is not None for e in manifest.spec.environments) and not st_now.full_vm:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This server has no KVM, which full virtual machines need. Run CyberLab on a "
            "server with KVM (docs/guides/virtual-machines.md).",
        )
    available = st_now.isolation
    missing = sorted({e.isolation for e in manifest.spec.environments} - set(available))
    if missing:
        names = {"sandbox": "a sandbox (gVisor)", "microvm": "microVMs (Kata Containers)"}
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This server can't run {' or '.join(names[m] for m in missing)} yet. Its owner can "
            "add it (docs/guides/isolation.md), or choose isolation 'container' in Setup.",
        )
    proxy_urls, egress_error = _configure_egress(adapter, ws, manifest, settings)
    records: list[EnvironmentRecord] = []
    for env in manifest.spec.environments:
        rec = EnvironmentRecord(
            workspace_id=ws.id,
            revision=rev.revision,
            name=env.name,
            provider=adapter.name,
            state=EnvState.provisioning.value,
            spec=env.model_dump(mode="json", by_alias=True),
            resources={"progress": "waiting to start"},
            last_activity_at=datetime.now(UTC),
            expires_at=(
                datetime.now(UTC) + timedelta(hours=env.lifecycle.ttl_hours)
                if env.lifecycle.ttl_hours
                else None
            ),
        )
        db.add(rec)
        records.append(rec)
    db.commit()  # record intent before touching the provider
    jobs = [(rec.id, env) for rec, env in zip(records, manifest.spec.environments, strict=True)]
    ws_key, subject, rev_no = _workspace_key(ws), user.subject, rev.revision
    req_id = request_id(request)
    database = request.app.state.db

    def note(rec_id: str, text: str) -> None:
        with database.new_session() as s:
            r = s.get(EnvironmentRecord, rec_id)
            if r is not None and r.state == EnvState.provisioning:
                r.resources = {**(r.resources or {}), "progress": text}
                s.commit()

    def work(session: Session) -> None:
        for rec_id, env in jobs:
            rec = session.get(EnvironmentRecord, rec_id)
            try:
                if env.network.mode == NetworkMode.restricted_egress and egress_error:
                    raise ProviderError(egress_error)
                result = adapter.provision(
                    ProvisionRequest(
                        workspace_key=ws_key,
                        environment=env,
                        image=reg.base_images[env.base_image].image,
                        family=reg.base_images[env.base_image].family,
                        source_rewrite=_rewrite(reg.base_images[env.base_image]),
                        tool_packs=tuple(reg.tool_packs[p] for p in env.tool_packs),
                        internet_approved=env.network.mode == NetworkMode.internet
                        and "security.internet-egress" in needed,
                        egress_proxy_url=proxy_urls.get(env.name),
                        progress=lambda text, rid=rec_id: note(rid, text),
                    )
                )
                session.refresh(rec)
                rec.provider_ref = result.provider_ref
                rec.state = result.state.value
                rec.resources = result.resource_names
            except ProviderError as exc:
                session.refresh(rec)
                rec.state = EnvState.failed.value
                rec.last_error = str(exc)
                rec.resources = {}
            record_audit(
                session,
                actor=subject,
                action="environment.provision",
                target_type="environment",
                target_id=rec.id,
                detail={"name": env.name, "state": rec.state, "revision": rev_no},
                request_id=req_id,
            )
            session.commit()
        if after is not None:
            after(session)

    if wait:
        work(db)
        for r in records:
            db.refresh(r)
    else:

        def background() -> None:
            with database.new_session() as s:
                try:
                    work(s)
                except Exception as exc:  # never leave machines "provisioning" forever
                    log.exception("background provisioning failed")
                    s.rollback()
                    for rec_id, _ in jobs:
                        r = s.get(EnvironmentRecord, rec_id)
                        if r is not None and r.state == EnvState.provisioning:
                            r.state, r.last_error = EnvState.failed.value, f"failed: {exc}"
                    s.commit()

        threading.Thread(target=background, name=f"provision-{ws_key}", daemon=True).start()
    return rev.revision, records


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
        stopped = False
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
                    stopped = rec.state == EnvState.stopped.value
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
        if action == "stop" and stopped:
            # workspace.snapshotPolicy: on-stop keeps a consistent copy after each stop
            store = getattr(request.app.state, "snapshot_store", None)
            if store is not None:
                autosnap.after_stop(db, rec, settings, adapter, store)
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
