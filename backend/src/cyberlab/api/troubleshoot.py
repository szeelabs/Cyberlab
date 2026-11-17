"""Troubleshooter API: plain-language advice for one environment."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db
from cyberlab.api.environments import _env_owned, get_provider
from cyberlab.api.routes import _owned
from cyberlab.db import AuditEvent, User
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError
from cyberlab.reaper import ACTOR as REAPER
from cyberlab.troubleshoot import diagnose

router = APIRouter(tags=["environments"])


@router.get("/v1/workspaces/{ws_id}/environments/{env_id}/diagnose")
def diagnose_environment(
    ws_id: str,
    env_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    """Why is this machine not working? Reads state only; changes nothing but the cached state."""
    rec = _env_owned(db, _owned(db, user, ws_id), env_id)
    connected, detail = False, "no execution provider is configured"
    runtime = None
    if provider is not None:
        st = provider.status()
        connected, detail = st.connected, st.detail
    if connected and rec.provider_ref and rec.state != EnvState.destroyed:
        try:
            rec.state = provider.inspect(rec.provider_ref).value
            details = getattr(provider, "details", None)
            runtime = details(rec.provider_ref) if details else None
        except ProviderError as exc:
            rec.last_error = str(exc)
        db.commit()
    event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.target_id == rec.id, AuditEvent.actor_subject == REAPER)
        .order_by(AuditEvent.created_at.desc())
    )
    reaper_event = {"action": event.action, **(event.detail or {})} if event else None
    advice = diagnose(
        state=rec.state,
        last_error=rec.last_error,
        spec=rec.spec,
        provider_connected=connected,
        provider_detail=detail,
        runtime=runtime,
        last_reaper_event=reaper_event,
        updated_at=rec.updated_at,
        expires_at=rec.expires_at,
    )
    return {
        "environment": rec.name,
        "state": rec.state,
        "advice": [a.to_dict() for a in advice],
    }
