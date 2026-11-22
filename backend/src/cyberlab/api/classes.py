"""Classroom mode: a teacher runs one lab for a whole group (Phase 8, step 5).

The teacher creates a class for a lab and shares its join code. Each student who joins
gets their **own** copy of the lab in their own workspace. The teacher sees everyone's
progress and can reset a student's lab, but never sees their files or terminals.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.api.environments import ACTIVE, _workspace_key, get_provider
from cyberlab.api.labs import _lab, get_labs, get_user_labs, start_lab
from cyberlab.config import Settings, get_settings
from cyberlab.db import (
    ClassMember,
    Classroom,
    EnvironmentRecord,
    LabRun,
    ManifestRevision,
    PlanApproval,
    Snapshot,
    User,
    Workspace,
    record_audit,
)
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError
from cyberlab.labs import Lab
from cyberlab.registry import Registry

router = APIRouter(tags=["classes"])
# No look-alike characters (0/O, 1/I/L), so codes are easy to read out loud.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _normalize_code(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:]}"


class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lab_id: str = Field(alias="labId", max_length=63)


class JoinIn(BaseModel):
    code: str = Field(min_length=8, max_length=20)


class ClassPatch(BaseModel):
    open: bool


def _class_out(c: Classroom, lab: Lab | None, db: Session) -> dict:
    count = len(list(db.scalars(select(ClassMember.id).where(ClassMember.classroom_id == c.id))))
    return {
        "id": c.id,
        "name": c.name,
        "labId": c.lab_id,
        "labTitle": lab.title if lab else c.lab_id,
        "joinCode": c.join_code,
        "open": c.open,
        "members": count,
        "createdAt": c.created_at.isoformat(),
    }


def _owned_class(db: Session, user: User, class_id: str) -> Classroom:
    c = db.get(Classroom, class_id)
    if c is None or c.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "class not found")
    return c


def _progress(db: Session, lab: Lab | None, workspace_id: str | None) -> dict:
    if workspace_id is None:
        return {"started": False, "solved": 0, "total": len(lab.tasks) if lab else 0}
    run = db.scalar(select(LabRun).where(LabRun.workspace_id == workspace_id))
    envs = list(
        db.scalars(select(EnvironmentRecord).where(EnvironmentRecord.workspace_id == workspace_id))
    )
    total = len(lab.tasks) if lab else 0
    solved = len(run.solved) if run else 0
    return {
        "started": True,
        "solved": solved,
        "total": total,
        "completed": bool(total) and solved == total,
        "machines": [{"name": e.name, "state": e.state} for e in envs if e.state != "destroyed"],
        "lastSolvedAt": max(run.solved.values()) if run and run.solved else None,
    }


@router.post("/v1/classes", status_code=201)
def create_class(
    body: ClassIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_user_labs),
) -> dict:
    lab = _lab(labs, body.lab_id)
    if body.lab_id not in get_labs(request, db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "share this lab first: students can only start built-in or shared labs",
        )
    for _ in range(5):  # a clash of random codes is very unlikely; retry anyway
        c = Classroom(owner_id=user.id, name=body.name.strip(), lab_id=lab.id, join_code=new_code())
        db.add(c)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
    else:  # pragma: no cover
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "try again")
    record_audit(
        db,
        actor=user.subject,
        action="class.create",
        target_type="class",
        target_id=c.id,
        detail={"name": c.name, "lab": lab.id},
        request_id=request_id(request),
    )
    db.commit()
    return _class_out(c, lab, db)


@router.get("/v1/classes")
def my_classes(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_user_labs),
) -> dict:
    teaching = [
        _class_out(c, labs.get(c.lab_id), db)
        for c in db.scalars(
            select(Classroom).where(Classroom.owner_id == user.id).order_by(Classroom.created_at)
        )
    ]
    joined = []
    for m, c in db.execute(
        select(ClassMember, Classroom)
        .join(Classroom, Classroom.id == ClassMember.classroom_id)
        .where(ClassMember.user_id == user.id)
        .order_by(ClassMember.joined_at)
    ).all():
        lab = labs.get(c.lab_id)
        joined.append(
            {
                "id": c.id,
                "name": c.name,
                "labId": c.lab_id,
                "labTitle": lab.title if lab else c.lab_id,
                "workspaceId": m.workspace_id,
                "open": c.open,
            }
            | {"progress": _progress(db, lab, m.workspace_id)}
        )
    return {"teaching": teaching, "joined": joined}


@router.get("/v1/classes/{class_id}")
def class_roster(
    class_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_user_labs),
) -> dict:
    """The teacher's view: who joined and how far each student got (never their files)."""
    c = _owned_class(db, user, class_id)
    lab = labs.get(c.lab_id)
    roster = []
    for m, student in db.execute(
        select(ClassMember, User)
        .join(User, User.id == ClassMember.user_id)
        .where(ClassMember.classroom_id == c.id)
        .order_by(ClassMember.joined_at)
    ).all():
        roster.append(
            {
                "memberId": m.id,
                "student": student.email or student.subject,
                "joinedAt": m.joined_at.isoformat(),
                "progress": _progress(db, lab, m.workspace_id),
            }
        )
    done = sum(1 for r in roster if r["progress"].get("completed"))
    return _class_out(c, lab, db) | {
        "roster": roster,
        "completed": done,
        "tasks": [{"id": t.id, "title": t.title} for t in lab.tasks] if lab else [],
    }


@router.patch("/v1/classes/{class_id}")
def update_class(
    class_id: str,
    body: ClassPatch,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_user_labs),
) -> dict:
    c = _owned_class(db, user, class_id)
    c.open = body.open
    record_audit(
        db,
        actor=user.subject,
        action="class.open" if body.open else "class.close",
        target_type="class",
        target_id=c.id,
        request_id=request_id(request),
    )
    db.commit()
    return _class_out(c, labs.get(c.lab_id), db)


@router.post("/v1/classes/join")
def join_class(
    body: JoinIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_user_labs),
) -> dict:
    c = db.scalar(select(Classroom).where(Classroom.join_code == _normalize_code(body.code)))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no class has this code; check it")
    member = db.scalar(
        select(ClassMember).where(ClassMember.classroom_id == c.id, ClassMember.user_id == user.id)
    )
    if member is None:
        if not c.open:
            raise HTTPException(status.HTTP_409_CONFLICT, "this class is closed to new students")
        if c.owner_id == user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "you teach this class")
        member = ClassMember(classroom_id=c.id, user_id=user.id)
        db.add(member)
        record_audit(
            db,
            actor=user.subject,
            action="class.join",
            target_type="class",
            target_id=c.id,
            request_id=request_id(request),
        )
        db.commit()
    lab = labs.get(c.lab_id)
    return {"id": c.id, "name": c.name, "labTitle": lab.title if lab else c.lab_id}


@router.post("/v1/classes/{class_id}/start", status_code=201)
def start_class_lab(
    class_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
    labs: dict[str, Lab] = Depends(get_user_labs),
    wait: bool = Query(default=True, description="false: prepare the machines in background"),
) -> dict:
    """A student starts their own copy of the class's lab (or gets the one they have)."""
    c = db.get(Classroom, class_id)
    member = (
        db.scalar(
            select(ClassMember).where(
                ClassMember.classroom_id == class_id, ClassMember.user_id == user.id
            )
        )
        if c
        else None
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "class not found")
    if member.workspace_id and db.get(Workspace, member.workspace_id):
        return {"workspaceId": member.workspace_id, "existing": True}
    out = start_lab(c.lab_id, request, user, db, reg, settings, provider, labs, wait=wait)
    member.workspace_id = out["workspaceId"]
    db.commit()
    return out | {"existing": False}


def _remove_workspace(db: Session, ws: Workspace, adapter: ProviderAdapter | None) -> None:
    """Destroy a workspace's machines and data, and delete it."""
    for rec in db.scalars(select(EnvironmentRecord).where(EnvironmentRecord.workspace_id == ws.id)):
        if rec.provider_ref and rec.state in [s.value for s in ACTIVE] and adapter is not None:
            try:
                adapter.destroy(rec.provider_ref, remove_workspace_volume=True)
            except ProviderError as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        rec.state = EnvState.destroyed.value
    cleanup = getattr(adapter, "remove_workspace_networks", None)
    if cleanup is not None:
        cleanup(_workspace_key(ws))
    for model in (EnvironmentRecord, PlanApproval, ManifestRevision, Snapshot, LabRun):
        db.query(model).filter(model.workspace_id == ws.id).delete()
    db.delete(ws)


@router.post("/v1/classes/{class_id}/members/{member_id}/reset")
def reset_member(
    class_id: str,
    member_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    """The teacher resets a student's lab: their lab machines and files are removed, and the
    student can start a fresh copy (new answers)."""
    c = _owned_class(db, user, class_id)
    m = db.get(ClassMember, member_id)
    if m is None or m.classroom_id != c.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "student not found")
    ws = db.get(Workspace, m.workspace_id) if m.workspace_id else None
    if ws is not None:
        _remove_workspace(db, ws, provider)
    m.workspace_id = None
    record_audit(
        db,
        actor=user.subject,
        action="class.reset",
        target_type="class",
        target_id=c.id,
        detail={"member": m.id},
        request_id=request_id(request),
    )
    db.commit()
    return {"reset": True}
