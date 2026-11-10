"""Export and import bundles: move a whole universe between CyberLab servers.

A bundle is an uncompressed tar file (``.cyberlab``) with exactly these members::

    bundle.json                     format, workspace info, manifest, snapshot index
    snapshots/<snapshot-id>.tar.gz  each included snapshot (plain .tar.gz, checksummed)

Snapshots are decrypted with this server's key on export, so the bundle can be imported on
a server with a different key; on import they are re-encrypted with that server's key.

Imported bundles are untrusted input: only the expected member names are accepted, only
regular files, nothing is ever extracted using names from the archive, sizes and counts are
limited, every snapshot checksum must match, and the manifest is re-validated by the policy
engine. Importing never starts anything.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cyberlab import __version__
from cyberlab.api.deps import current_user, get_db, get_registry, request_id
from cyberlab.api.routes import _latest_revision, _owned
from cyberlab.api.snapshots import _sha256, _tmpdir, get_store
from cyberlab.config import Settings, get_settings
from cyberlab.db import ManifestRevision, Snapshot, User, Workspace, record_audit
from cyberlab.manifest.models import SLUG
from cyberlab.manifest.policy import evaluate, parse_manifest_data
from cyberlab.registry import Registry
from cyberlab.storage import SnapshotStore, StorageError

router = APIRouter(tags=["bundles"])

FORMAT = "cyberlab.dev/bundle/v1"
MAX_BUNDLE_JSON = 1024 * 1024
_SNAP_MEMBER = re.compile(r"^snapshots/([0-9a-f-]{36})\.tar\.gz$")


# ---------------------------------------------------------------- export
@router.get("/v1/workspaces/{ws_id}/export")
def export_bundle(
    ws_id: str,
    request: Request,
    background: BackgroundTasks,
    snapshots: str = Query(default="all", pattern="^(all|latest|none)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store: SnapshotStore = Depends(get_store),
) -> FileResponse:
    ws = _owned(db, user, ws_id)
    rev = _latest_revision(db, ws.id)
    if rev is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "import a manifest before exporting")
    rows = list(
        db.scalars(
            select(Snapshot)
            .where(Snapshot.workspace_id == ws.id)
            .order_by(Snapshot.created_at.desc())
        )
    )
    if snapshots == "none":
        rows = []
    elif snapshots == "latest":
        latest: dict[str, Snapshot] = {}
        for s in rows:
            latest.setdefault(s.environment_name, s)
        rows = list(latest.values())

    workdir = Path(tempfile.mkdtemp(dir=_tmpdir(settings)))
    bundle = workdir / f"{ws.name}.cyberlab"
    index = []
    try:
        with tarfile.open(bundle, "w", format=tarfile.PAX_FORMAT) as tar:
            for s in rows:
                plain = workdir / f"{s.id}.tar.gz"
                try:
                    store.get(s.storage_key, plain)
                except StorageError as exc:
                    raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
                if _sha256(plain) != s.sha256:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT, f"snapshot {s.id} failed its integrity check"
                    )
                tar.add(plain, arcname=f"snapshots/{s.id}.tar.gz", recursive=False)
                plain.unlink()
                index.append(
                    {
                        "id": s.id,
                        "environment": s.environment_name,
                        "label": s.label,
                        "sha256": s.sha256,
                        "sizeBytes": s.size_bytes,
                        "createdAt": s.created_at.isoformat(),
                    }
                )
            meta = {
                "format": FORMAT,
                "exportedAt": datetime.now(UTC).isoformat(),
                "exportedBy": f"cyberlab {__version__}",
                "workspace": {"name": ws.name, "description": ws.description},
                "manifest": rev.content,
                "snapshots": index,
            }
            data = json.dumps(meta, indent=2).encode()
            info = tarfile.TarInfo("bundle.json")
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    record_audit(
        db,
        actor=user.subject,
        action="workspace.export",
        target_type="workspace",
        target_id=ws.id,
        detail={"snapshots": len(index)},
        request_id=request_id(request),
    )
    db.commit()
    background.add_task(shutil.rmtree, workdir, True)
    return FileResponse(bundle, media_type="application/x-tar", filename=f"{ws.name}.cyberlab")


# ---------------------------------------------------------------- import
class BundleError(ValueError):
    pass


def _read_bundle(path: Path, workdir: Path, settings: Settings) -> tuple[dict, dict[str, Path]]:
    """Validate the bundle and copy snapshot data out under names *we* choose."""
    try:
        tar = tarfile.open(path, "r:")  # uncompressed only: no decompression bombs here
    except (tarfile.TarError, OSError) as exc:
        raise BundleError("not a CyberLab bundle (expected an uncompressed tar file)") from exc
    meta, files = None, {}
    with tar:
        members = tar.getmembers()
        if len(members) > settings.max_snapshots_per_workspace + 1:
            raise BundleError("bundle has too many entries")
        for m in members:
            if not m.isreg():
                raise BundleError(f"unexpected entry type for {m.name!r}")
            if m.name == "bundle.json":
                if m.size > MAX_BUNDLE_JSON:
                    raise BundleError("bundle.json is too large")
                try:
                    meta = json.loads(tar.extractfile(m).read())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise BundleError("bundle.json is not valid JSON") from exc
                continue
            match = _SNAP_MEMBER.match(m.name)
            if not match:
                raise BundleError(f"unexpected entry {m.name!r}")
            if m.size > settings.max_snapshot_bytes:
                raise BundleError("a snapshot in the bundle is too large")
            dest = workdir / f"{uuid.uuid4()}.tar.gz"  # never a name from the archive
            with tar.extractfile(m) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            files[match.group(1)] = dest
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise BundleError(f"missing bundle.json or unsupported format (need {FORMAT})")
    index = meta.get("snapshots")
    if not isinstance(index, list) or {e.get("id") for e in index if isinstance(e, dict)} != set(
        files
    ):
        raise BundleError("snapshot index does not match the snapshots in the bundle")
    for entry in index:
        if (
            not isinstance(entry.get("sha256"), str)
            or _sha256(files[entry["id"]]) != entry["sha256"]
        ):
            raise BundleError(f"snapshot {entry['id']} failed its checksum")
        if not re.match(SLUG, str(entry.get("environment", ""))):
            raise BundleError("invalid environment name in snapshot index")
    return meta, files


@router.post("/v1/workspaces/import", status_code=201)
async def import_bundle(
    request: Request,
    name: str | None = Query(default=None, pattern=SLUG),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    reg: Registry = Depends(get_registry),
    store: SnapshotStore = Depends(get_store),
) -> dict:
    """Create a new workspace from a bundle. Nothing is provisioned or restored."""
    workdir = Path(tempfile.mkdtemp(dir=_tmpdir(settings)))
    try:
        upload = workdir / "upload.cyberlab"
        size = 0
        with upload.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_bundle_bytes:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        f"bundle is larger than {settings.max_bundle_bytes} bytes",
                    )
                out.write(chunk)
        try:
            meta, files = _read_bundle(upload, workdir, settings)
        except BundleError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

        manifest, findings = parse_manifest_data(meta.get("manifest"))
        if manifest is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {
                    "detail": "bundle manifest is invalid",
                    "findings": [f.__dict__ for f in findings],
                },
            )
        plan = evaluate(manifest, reg, settings.quotas)
        if not plan.valid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"detail": "bundle manifest is not allowed on this server", "plan": plan.to_dict()},
            )

        info = meta.get("workspace") if isinstance(meta.get("workspace"), dict) else {}
        ws_name = name or info.get("name")
        if not isinstance(ws_name, str) or not re.match(SLUG, ws_name):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "choose a workspace name with ?name="
            )
        description = str(info.get("description", ""))[:2000]
        ws = Workspace(owner_id=user.id, name=ws_name, description=description)
        db.add(ws)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, "workspace name already exists; pass ?name="
            ) from exc
        db.add(
            ManifestRevision(
                workspace_id=ws.id, revision=1, content=plan.manifest, created_by=user.subject
            )
        )
        stored_keys = []
        try:
            for entry in meta["snapshots"]:
                snap = Snapshot(
                    id=str(uuid.uuid4()),
                    workspace_id=ws.id,
                    environment_name=entry["environment"],
                    label=str(entry.get("label", ""))[:200],
                    size_bytes=files[entry["id"]].stat().st_size,
                    sha256=entry["sha256"],
                    store=store.name,
                    storage_key="",
                    created_by=user.subject,
                )
                snap.storage_key = f"{ws.id}/{snap.id}.tar.gz"
                store.put(snap.storage_key, files[entry["id"]])
                stored_keys.append(snap.storage_key)
                db.add(snap)
        except StorageError as exc:
            db.rollback()
            for key in stored_keys:
                store.delete(key)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        record_audit(
            db,
            actor=user.subject,
            action="workspace.import",
            target_type="workspace",
            target_id=ws.id,
            detail={
                "snapshots": len(meta["snapshots"]),
                "requiresApproval": plan.requires_approval,
            },
            request_id=request_id(request),
        )
        db.commit()
        return {
            "workspace": {"id": ws.id, "name": ws.name},
            "revision": 1,
            "snapshots": len(meta["snapshots"]),
            "requiresApproval": plan.requires_approval,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
