"""The reaper: enforces each environment's lifecycle from its manifest.

* ``idleTimeoutMinutes``: a running environment with no activity (no open terminal, no file
  or snapshot operation, no start) for that long is **stopped**; files are kept.
* ``ttlHours``: an environment older than that is **destroyed**.
* ``ephemeral: true``: when idle or expired, the environment is destroyed **and its
  workspace data is deleted**.

``run_once`` is a pure function of the database, the provider and the current time, so it
is tested with a fake clock. The app runs it every ``CYBERLAB_REAPER_INTERVAL_SECONDS``;
``cyberlab reaper run-once`` runs it on demand. Every action is audited as
``system:reaper``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab.db import EnvironmentRecord, Workspace, record_audit
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError

ACTOR = "system:reaper"
LIVE = (EnvState.running.value, EnvState.stopped.value)


@dataclass
class ReapAction:
    environment_id: str
    name: str
    action: str  # "stop" | "destroy"
    reason: str  # "idle" | "ttl"
    delete_data: bool
    ok: bool = True
    error: str | None = None


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def decide(rec: EnvironmentRecord, now: datetime, active: set[str]) -> ReapAction | None:
    """What (if anything) the lifecycle rules require for this environment right now."""
    if rec.state not in LIVE or not rec.provider_ref:
        return None
    lifecycle = (rec.spec or {}).get("lifecycle") or {}
    ephemeral = bool(lifecycle.get("ephemeral"))
    expires = _utc(rec.expires_at)
    if expires is not None and now >= expires:
        return ReapAction(rec.id, rec.name, "destroy", "ttl", delete_data=ephemeral)
    idle = lifecycle.get("idleTimeoutMinutes")
    last = _utc(rec.last_activity_at) or _utc(rec.updated_at)
    if (
        idle
        and rec.state == EnvState.running.value
        and rec.id not in active
        and last is not None
        and now - last >= timedelta(minutes=int(idle))
    ):
        if ephemeral:
            return ReapAction(rec.id, rec.name, "destroy", "idle", delete_data=True)
        return ReapAction(rec.id, rec.name, "stop", "idle", delete_data=False)
    return None


def run_once(
    db: Session,
    provider: ProviderAdapter | None,
    *,
    now: datetime | None = None,
    active: set[str] | None = None,
) -> list[ReapAction]:
    if provider is None:
        return []
    now = now or datetime.now(UTC)
    active = active or set()
    actions: list[ReapAction] = []
    rows = db.scalars(select(EnvironmentRecord).where(EnvironmentRecord.state.in_(LIVE)))
    for rec in list(rows):
        act = decide(rec, now, active)
        if act is None:
            continue
        try:
            if act.action == "stop":
                rec.state = provider.stop(rec.provider_ref).value
            else:
                rec.state = provider.destroy(
                    rec.provider_ref, remove_workspace_volume=act.delete_data
                ).value
            rec.last_error = None
        except ProviderError as exc:
            act.ok, act.error = False, str(exc)
            rec.last_error = f"reaper {act.action} failed: {exc}"
        record_audit(
            db,
            actor=ACTOR,
            action=f"environment.{act.action}",
            target_type="environment",
            target_id=rec.id,
            detail={
                "name": rec.name,
                "reason": act.reason,
                "deleteData": act.delete_data,
                "ok": act.ok,
            },
        )
        db.commit()
        if act.ok and act.action == "destroy":
            _cleanup_workspace_networks(db, provider, rec.workspace_id)
        actions.append(act)
    return actions


def _cleanup_workspace_networks(db: Session, provider: ProviderAdapter, ws_id: str) -> None:
    still = db.scalar(
        select(EnvironmentRecord).where(
            EnvironmentRecord.workspace_id == ws_id, EnvironmentRecord.state.in_(LIVE)
        )
    )
    cleanup = getattr(provider, "remove_workspace_networks", None)
    ws = db.get(Workspace, ws_id)
    if still is None and cleanup is not None and ws is not None:
        cleanup(ws.id.replace("-", "")[:12])


def touch(db: Session, environment_id: str) -> None:
    """Record activity on an environment (resets its idle timer)."""
    rec = db.get(EnvironmentRecord, environment_id)
    if rec is not None:
        rec.last_activity_at = datetime.now(UTC)
