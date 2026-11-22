"""Ready-made labs: catalog, one-click start, and answer checking (Phase 8)."""

from __future__ import annotations

import hashlib
import io
import secrets
import tarfile
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.api.environments import (
    _require_provider,
    get_provider,
    start_provisioning,
)
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import (
    CustomLab,
    EnvironmentRecord,
    LabRun,
    ManifestRevision,
    User,
    Workspace,
    record_audit,
)
from cyberlab.execution import ProviderAdapter, ProviderError
from cyberlab.labs import (
    Lab,
    LabError,
    answer_hash,
    check_answer,
    manifest_for,
    new_run,
    parse_lab,
)
from cyberlab.registry import Registry

router = APIRouter(tags=["labs"])
MAX_LABS_PER_USER = 50


def _custom(request: Request, row: CustomLab) -> Lab | None:
    """A stored user lab, parsed once (re-checked whenever its content changes)."""
    cache = request.app.state.__dict__.setdefault("custom_lab_cache", {})
    key = (row.catalog_id, hashlib.sha256(row.content.encode()).hexdigest())
    if key not in cache:
        settings: Settings = request.app.state.settings
        try:
            lab = parse_lab(row.content, request.app.state.registry, settings.quotas)
            cache[key] = lab.model_copy(update={"id": row.catalog_id})
        except LabError:
            cache[key] = None  # no longer valid with this server's registry
    return cache[key]


def get_labs(request: Request, db: Session = Depends(get_db)) -> dict[str, Lab]:
    """The public catalog: built-in labs and labs people shared."""
    labs = dict(request.app.state.labs)
    for row in db.scalars(select(CustomLab).where(CustomLab.shared.is_(True))):
        lab = _custom(request, row)
        if lab is not None:
            labs[lab.id] = lab
    return labs


def get_user_labs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, Lab]:
    """Everything this user may start: the public catalog plus their own labs."""
    labs = get_labs(request, db)
    for row in db.scalars(select(CustomLab).where(CustomLab.owner_id == user.id)):
        lab = _custom(request, row)
        if lab is not None:
            labs[lab.id] = lab
    return labs


def _lab(labs: dict[str, Lab], lab_id: str) -> Lab:
    lab = labs.get(lab_id)
    if lab is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lab not found")
    return lab


def _sorted(labs) -> list[Lab]:
    order = {"beginner": 0, "intermediate": 1, "advanced": 2}
    return sorted(labs, key=lambda lab: (order[lab.level], lab.title))


@router.get("/v1/labs")
def list_labs(request: Request, labs: dict[str, Lab] = Depends(get_labs)) -> list[dict]:
    builtin = request.app.state.labs
    return [
        lab.public() | {"source": "built-in" if lab.id in builtin else "community"}
        for lab in _sorted(labs.values())
    ]


# ---------------------------------------------------------------- labs written by users


@router.get("/v1/labs/mine")
def my_labs(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    out = []
    for row in db.scalars(
        select(CustomLab).where(CustomLab.owner_id == user.id).order_by(CustomLab.created_at)
    ):
        lab = _custom(request, row)
        if lab is not None:
            out.append(lab.public() | {"source": "mine", "shared": row.shared})
    return out


@router.post("/v1/labs/import", status_code=201)
async def import_lab(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    reg: Registry = Depends(get_registry),
) -> dict:
    """Add a lab file you wrote. It is checked exactly like the built-in labs."""
    body = await request.body()
    if len(body) > settings.max_manifest_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "lab file too large")
    try:
        text = body.decode("utf-8")
        lab = parse_lab(text, reg, settings.quotas)
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lab files must be UTF-8") from exc
    except LabError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)[:2000]) from exc
    mine = list(db.scalars(select(CustomLab.id).where(CustomLab.owner_id == user.id)))
    if len(mine) >= MAX_LABS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"you have {MAX_LABS_PER_USER} labs; delete one first"
        )
    row = CustomLab(owner_id=user.id, catalog_id=f"c-{secrets.token_hex(6)}", content=text)
    db.add(row)
    record_audit(
        db,
        actor=user.subject,
        action="lab.import",
        target_type="lab",
        target_id=row.catalog_id,
        detail={"title": lab.title},
        request_id=request_id(request),
    )
    db.commit()
    return lab.model_copy(update={"id": row.catalog_id}).public() | {
        "source": "mine",
        "shared": False,
    }


def _my_row(db: Session, user: User, lab_id: str) -> CustomLab:
    row = db.scalar(select(CustomLab).where(CustomLab.catalog_id == lab_id))
    if row is None or row.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lab not found")
    return row


class SharePatch(BaseModel):
    shared: bool


@router.patch("/v1/labs/{lab_id}")
def share_lab(
    lab_id: str,
    body: SharePatch,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = _my_row(db, user, lab_id)
    row.shared = body.shared
    record_audit(
        db,
        actor=user.subject,
        action="lab.share" if body.shared else "lab.unshare",
        target_type="lab",
        target_id=lab_id,
        request_id=request_id(request),
    )
    db.commit()
    return {"id": lab_id, "shared": row.shared}


@router.delete("/v1/labs/{lab_id}", status_code=204)
def delete_lab(
    lab_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = _my_row(db, user, lab_id)
    db.delete(row)
    record_audit(
        db,
        actor=user.subject,
        action="lab.delete",
        target_type="lab",
        target_id=lab_id,
        request_id=request_id(request),
    )
    db.commit()
    return Response(status_code=204)


@router.get("/v1/labs/{lab_id}/file")
def lab_file(
    lab_id: str,
    request: Request,
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_labs),
):
    """The lab file itself, to read, adapt and import elsewhere (built-in and shared labs)."""
    _lab(labs, lab_id)
    if lab_id in request.app.state.labs:
        settings: Settings = request.app.state.settings
        text = (settings.registry_dir / "labs" / f"{lab_id}.yaml").read_text("utf-8")
    else:
        text = db.scalar(select(CustomLab.content).where(CustomLab.catalog_id == lab_id))
    return PlainTextResponse(
        text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{lab_id}.yaml"'},
    )


@router.get("/v1/labs/progress")
def lab_progress(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    labs: dict[str, Lab] = Depends(get_user_labs),
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
        # False while the machines are still being prepared in the background
        "ready": run.ready,
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
    labs: dict[str, Lab] = Depends(get_user_labs),
    wait: bool = Query(default=True, description="false: return at once, prepare in background"),
) -> dict:
    """One click: a new workspace with the lab's machines running and its files in place.
    With wait=false the answer comes at once and the machines are prepared in the
    background (the Lab tab shows each step)."""
    lab = _lab(labs, lab_id)
    _require_provider(provider)  # fail before creating anything
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
    content = new_run(lab)
    run = LabRun(workspace_id=ws.id, lab_id=lab.id, answer_hashes={}, solved={})
    db.add(run)
    db.flush()  # gives the run its id, which salts the answer hashes
    run.answer_hashes = {t: answer_hash(run.id, a) for t, a in content.answers.items()}
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
    run_id, ws_id, subject, req_id = run.id, ws.id, user.subject, request_id(request)
    adapter = provider

    def place_files(session: Session) -> None:
        envs = {
            e.name: e
            for e in session.scalars(
                select(EnvironmentRecord).where(EnvironmentRecord.workspace_id == ws_id)
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
        r = session.get(LabRun, run_id)
        r.setup_error = "; ".join(errors) or None
        r.ready = True
        record_audit(
            session,
            actor=subject,
            action="lab.start",
            target_type="workspace",
            target_id=ws_id,
            detail={"lab": lab.id, "ok": not errors},
            request_id=req_id,
        )
        session.commit()

    try:
        start_provisioning(
            ws, 1, request, user, db, reg, settings, provider, wait=wait, after=place_files
        )
    except HTTPException:
        # Nothing was created (limits, processor type...): leave no empty workspace behind.
        for model in (LabRun, ManifestRevision, EnvironmentRecord):
            db.query(model).filter(model.workspace_id == ws_id).delete()
        db.delete(ws)
        db.commit()
        raise
    db.refresh(run)
    return {"workspaceId": ws_id} | run_out(lab, run)


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
    labs: dict[str, Lab] = Depends(get_user_labs),
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
    labs: dict[str, Lab] = Depends(get_user_labs),
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
