"""AI Lab Architect API: draft a manifest from a plain-language description.

Drafts are returned to the user only. Nothing is saved, approved or provisioned here.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.architect import ArchitectError, ArchitectRefused, ArchitectUnavailable, draft_lab
from cyberlab.architect.core import MAX_PROMPT_CHARS
from cyberlab.config import Settings, get_settings
from cyberlab.db import User, record_audit
from cyberlab.registry import Registry

router = APIRouter(tags=["architect"])

DESCRIPTIONS = {
    "rules": "Built-in planner: matches keywords, free and offline. No AI is used.",
    "anthropic": (
        "Claude (Anthropic API). Your description is sent to Anthropic, and each draft "
        "costs money on your Anthropic account."
    ),
}


def get_architect(request: Request):
    return request.app.state.architect


class DraftIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=MAX_PROMPT_CHARS)


@router.get("/v1/system/ai")
def ai_status(
    _: User = Depends(current_user),
    model=Depends(get_architect),
    settings: Settings = Depends(get_settings),
) -> dict:
    if model is None:
        return {
            "provider": None,
            "connected": False,
            "status": "OFF",
            "detail": "the AI Lab Architect is turned off (CYBERLAB_AI_PROVIDER=off)",
            "paid": False,
        }
    return {
        "provider": model.name,
        "connected": True,
        "status": "READY",
        "model": settings.ai_model if model.name == "anthropic" else None,
        "detail": DESCRIPTIONS.get(model.name, ""),
        "paid": model.name == "anthropic",
    }


@router.post("/v1/architect/draft")
def draft(
    body: DraftIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    model=Depends(get_architect),
) -> dict:
    """Draft a lab. The result still needs the user's review, save and approval."""
    if model is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the AI Lab Architect is off")
    detail: dict = {"provider": model.name, "promptChars": len(body.prompt)}
    try:
        result = draft_lab(body.prompt, reg, settings.quotas, model)
    except ArchitectRefused as exc:
        result = None
        refusal = str(exc)
    except ArchitectUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ArchitectError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    else:
        refusal = result.reason if result.refused else ""

    if result is None or result.refused:
        detail["refused"] = True
        record_audit(
            db,
            actor=user.subject,
            action="architect.draft",
            target_type="architect",
            detail=detail,
            request_id=request_id(request),
        )
        db.commit()
        return {"provider": model.name, "refused": True, "reason": refusal}

    plan = result.plan or {}
    detail.update(refused=False, valid=plan.get("valid"))
    record_audit(
        db,
        actor=user.subject,
        action="architect.draft",
        target_type="architect",
        detail=detail,
        request_id=request_id(request),
    )
    db.commit()
    return {
        "provider": model.name,
        "refused": False,
        "explanation": result.explanation,
        "manifestYaml": yaml.safe_dump(result.manifest, sort_keys=False, allow_unicode=True),
        "plan": plan,
    }
