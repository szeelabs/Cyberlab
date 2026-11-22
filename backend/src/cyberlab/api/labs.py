"""Ready-made labs: catalog, one-click start, and answer checking (Phase 8)."""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.api.environments import (
    ProvisionIn,
    _require_provider,
    get_provider,
    provision_environments,
)
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import (
    EnvironmentRecord,
    LabRun,
    ManifestRevision,
    User,
    Workspace,
    record_audit,
)
from cyberlab.execution import ProviderAdapter, ProviderError
from cyberlab.labs import Lab, answer_hash, check_answer, manifest_for, new_run
from cyberlab.registry import Registry

router = APIRouter(tags=["labs"])


def get_labs(request: Request) -> dict[str, Lab]:
    return request.app.state.labs


def _lab(labs: dict[str, Lab], lab_id: str) -> Lab:
    lab = labs.get(lab_id)
    if lab is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lab not found")
    return lab


@router.get("/v1/labs")
def list_labs(labs: dict[str, Lab] = Depends(get_labs)) -> list[dict]:
    order = {"beginner": 0, "intermediate": 1, "advanced": 2}
    return [
        lab.public() for lab in sorted(labs.values(), key=lambda lab: (order[lab.level], lab.title))
    ]


@router.get("/v1/labs/progress")
def lab_progress(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_labs),
) -> dict:
    """The signed-in user's progress per lab: the best run (most solved, then newest)."""
    rows = db.execute(
        select(LabRun, Workspace.name)
        .join(Workspace, Workspace.id == LabRun.workspace_id)
        .where(Workspace.owner_id == user.id)
    ).all()
    best: dict[str, dict] = {}
    for run, ws_name in rows:
        lab = labs.get(run.lab_id)
        if lab is None:
            continue
        entry = {
            "workspaceId": run.workspace_id,
            "workspace": ws_name,
            "solved": len(run.solved),
            "total": len(lab.tasks),
            "completed": len(run.solved) == len(lab.tasks),
            "startedAt": run.created_at.isoformat(),
        }
        old = best.get(run.lab_id)
        if old is None or (entry["solved"], entry["startedAt"]) > (old["solved"], old["startedAt"]):
            best[run.lab_id] = entry
    done = sum(1 for e in best.values() if e["completed"])
    return {"labs": best, "completed": done, "available": len(labs)}


@router.get("/v1/labs/{lab_id}")
def get_lab(lab_id: str, labs: dict[str, Lab] = Depends(get_labs)) -> dict:
    return _lab(labs, lab_id).public()


def _free_name(db: Session, user: User, base: str) -> str:
    taken = set(db.scalars(select(Workspace.name).where(Workspace.owner_id == user.id)))
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _archive(files: dict[str, bytes], modes: dict[str, int] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dirs: set[str] = set()
        for path in sorted(files):
            parts = path.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    info = tarfile.TarInfo(d)
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
            info = tarfile.TarInfo(path)
            # plain rwx bits only: never setuid, setgid or sticky
            info.size, info.mode = len(files[path]), (modes or {}).get(path, 0o644) & 0o777
            tar.addfile(info, io.BytesIO(files[path]))
    return buf.getvalue()


def _place(
    provider: ProviderAdapter, ref: str, files: dict[str, bytes], modes: dict[str, int]
) -> None:
    extract = getattr(provider, "extract_files", None)
    if extract is not None:
        extract(ref, _archive(files, modes))
        return
    for path, data in files.items():  # providers without archive support
        provider.write_file(ref, f"/workspace/{path}", data)


def run_out(lab: Lab, run: LabRun) -> dict:
    return {
        "lab": lab.public(),
        "runId": run.id,
        "solved": run.solved,
        "progress": {"solved": len(run.solved), "total": len(lab.tasks)},
        "setupError": run.setup_error,
        "startedAt": run.created_at.isoformat(),
    }


@router.post("/v1/labs/{lab_id}/start", status_code=201)
def start_lab(
    lab_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    reg: Registry = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
    labs: dict[str, Lab] = Depends(get_labs),
) -> dict:
    """One click: a new workspace with the lab's machines running and its files in place."""
    lab = _lab(labs, lab_id)
    adapter = _require_provider(provider)  # fail before creating anything
    ws = Workspace(owner_id=user.id, name=_free_name(db, user, lab.id), description=lab.title)
    db.add(ws)
    db.flush()
    db.add(
        ManifestRevision(
            workspace_id=ws.id,
            revision=1,
            content=manifest_for(lab).to_portable(),
            created_by=user.subject,
        )
    )
    record_audit(
        db,
        actor=user.subject,
        action="workspace.create",
        target_type="workspace",
        target_id=ws.id,
        detail={"name": ws.name, "lab": lab.id},
        request_id=request_id(request),
    )
    db.commit()
    try:
        provision_environments(
            ws.id, ProvisionIn(revision=1), request, user, db, reg, settings, adapter
        )
    except HTTPException:
        # Nothing was created (limits, processor type...): leave no empty workspace behind.
        db.query(ManifestRevision).filter(ManifestRevision.workspace_id == ws.id).delete()
        db.query(EnvironmentRecord).filter(EnvironmentRecord.workspace_id == ws.id).delete()
        db.delete(ws)
        db.commit()
        raise

    content = new_run(lab)
    run = LabRun(workspace_id=ws.id, lab_id=lab.id, answer_hashes={}, solved={})
    db.add(run)
    db.flush()  # gives the run its id, which salts the answer hashes
    run.answer_hashes = {t: answer_hash(run.id, a) for t, a in content.answers.items()}
    envs = {
        e.name: e
        for e in db.scalars(
            select(EnvironmentRecord).where(EnvironmentRecord.workspace_id == ws.id)
        )
    }
    errors = []
    for name, files in content.files.items():
        env = envs.get(name)
        if env is None or not env.provider_ref or env.state != "running":
            errors.append(
                f"{name}: the machine did not start ({(env and env.last_error) or 'unknown'})"
            )
            continue
        try:
            _place(adapter, env.provider_ref, files, content.modes.get(name, {}))
        except ProviderError as exc:
            errors.append(f"{name}: lab files could not be placed ({exc})")
    run.setup_error = "; ".join(errors) or None
    record_audit(
        db,
        actor=user.subject,
        action="lab.start",
        target_type="workspace",
        target_id=ws.id,
        detail={"lab": lab.id, "ok": not errors},
        request_id=request_id(request),
    )
    db.commit()
    return {"workspaceId": ws.id} | run_out(lab, run)


def _run(db: Session, ws: Workspace) -> LabRun:
    run = db.scalar(select(LabRun).where(LabRun.workspace_id == ws.id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this workspace has no lab")
    return run


@router.get("/v1/workspaces/{ws_id}/lab")
def workspace_lab(
    ws_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_labs),
) -> dict:
    run = _run(db, _owned(db, user, ws_id))
    return run_out(_lab(labs, run.lab_id), run)


class AnswerIn(BaseModel):
    task: str = Field(max_length=63)
    answer: str = Field(min_length=1, max_length=200)


@router.post("/v1/workspaces/{ws_id}/lab/answer")
def answer(
    ws_id: str,
    body: AnswerIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_labs),
) -> dict:
    ws = _owned(db, user, ws_id)
    run = _run(db, ws)
    lab = _lab(labs, run.lab_id)
    expected = run.answer_hashes.get(body.task)
    if expected is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such task in this lab")
    correct = check_answer(run.id, body.answer, expected)
    if correct and body.task not in run.solved:
        run.solved = {**run.solved, body.task: datetime.now(UTC).isoformat()}
    # The answer itself is never recorded, right or wrong.
    record_audit(
        db,
        actor=user.subject,
        action="lab.answer",
        target_type="workspace",
        target_id=ws.id,
        detail={"lab": lab.id, "task": body.task, "correct": correct},
        request_id=request_id(request),
    )
    db.commit()
    return {"correct": correct} | run_out(lab, run)
