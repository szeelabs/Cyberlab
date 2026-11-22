"""Full virtual machines: add boot media from a link (Phase 9, step 2).

The server downloads the ISO itself, straight into the machine's /workspace, so nothing
passes through the learner's computer. Only https links to public addresses are followed
(every redirect is checked again), the size is capped, and a SHA-256 can be verified.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import tempfile
import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cyberlab.api.deps import current_user, get_db, request_id
from cyberlab.api.environments import _env_owned, _require_provider, get_provider
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import User, record_audit
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError

router = APIRouter(tags=["virtual machines"])
MAX_REDIRECTS = 5
CHUNK = 1024 * 1024


class MediaIn(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class UnsafeLink(ValueError):
    pass


def check_link(url: str, allow_private: bool = False, resolve=None) -> str:
    """Refuse anything but https to a public address (no internal services, no cloud
    metadata endpoints). Returns the host name."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise UnsafeLink("only https:// links are accepted")
    if u.username or u.password:
        raise UnsafeLink("links with a user name or password are not accepted")
    try:
        infos = (resolve or socket.getaddrinfo)(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeLink(f"can't find {u.hostname}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not allow_private and not addr.is_global:
            raise UnsafeLink(f"{u.hostname} points to a private or reserved address")
    return u.hostname


def download(
    url: str,
    dest: str,
    max_bytes: int,
    progress: Callable[[int, int | None], None],
    allow_private: bool = False,
    client: httpx.Client | None = None,
) -> str:
    """Download to `dest`, following only checked redirects. Returns the SHA-256."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=False)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_link(url, allow_private)
            with client.stream("GET", url) as res:
                if res.is_redirect:
                    url = str(res.url.join(res.headers.get("location", "")))
                    continue
                if res.status_code != 200:
                    raise ProviderError(f"the link answered {res.status_code}")
                total = int(res.headers.get("content-length") or 0) or None
                if total and total > max_bytes:
                    raise ProviderError(f"the file is larger than the limit ({max_bytes} bytes)")
                digest, done = hashlib.sha256(), 0
                with open(dest, "wb") as f:
                    for chunk in res.iter_bytes(CHUNK):
                        done += len(chunk)
                        if done > max_bytes:
                            raise ProviderError(
                                f"the file is larger than the limit ({max_bytes} bytes)"
                            )
                        digest.update(chunk)
                        f.write(chunk)
                        progress(done, total)
                return digest.hexdigest()
        raise ProviderError("too many redirects")
    except httpx.HTTPError as exc:
        raise ProviderError(f"download failed: {exc}") from exc
    finally:
        if own:
            client.close()


def _jobs(request: Request) -> dict:
    if not hasattr(request.app.state, "vm_media"):
        request.app.state.vm_media = {}
    return request.app.state.vm_media


def _vm_env(db, user, ws_id, env_id):
    ws = _owned(db, user, ws_id)
    rec = _env_owned(db, ws, env_id)
    vm = rec.spec.get("vm")
    if not vm:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{rec.name} is not a full virtual machine")
    return ws, rec, vm


@router.post("/v1/workspaces/{ws_id}/environments/{env_id}/vm/media", status_code=202)
def add_media(
    ws_id: str,
    env_id: str,
    body: MediaIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    ws, rec, vm = _vm_env(db, user, ws_id, env_id)
    adapter = _require_provider(provider)
    if rec.provider_ref is None or rec.state != EnvState.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "start the machine first")
    try:
        check_link(body.url, settings.vm_media_allow_private)
    except UnsafeLink as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    jobs = _jobs(request)
    if jobs.get(rec.id, {}).get("state") == "downloading":
        raise HTTPException(status.HTTP_409_CONFLICT, "a download is already running")
    job = {"state": "downloading", "bytes": 0, "total": None, "error": None, "sha256": None,
           "url": body.url, "startedAt": time.time()}  # fmt: skip
    jobs[rec.id] = job
    host = urlparse(body.url).hostname
    record_audit(
        db,
        actor=user.subject,
        action="vm.media.start",
        target_type="environment",
        target_id=rec.id,
        detail={"host": host},
        request_id=request_id(request),
    )
    db.commit()
    ref, boot, subject, env_id_ = rec.provider_ref, vm.get("boot", "boot.iso"), user.subject, rec.id
    database = request.app.state.db

    def run() -> None:
        fd, tmp = tempfile.mkstemp(prefix="cyberlab-vm-media-")
        os.close(fd)
        try:
            digest = download(
                body.url,
                tmp,
                settings.max_vm_media_bytes,
                lambda done, total: job.update(bytes=done, total=total),
                settings.vm_media_allow_private,
            )
            if body.sha256 and digest != body.sha256.lower():
                raise ProviderError("the file's SHA-256 doesn't match the one you gave")
            job["state"] = "copying"
            part = f"/workspace/{boot}.part"
            adapter.write_file_from_path(ref, part, tmp)
            adapter.rename_file(ref, part, f"/workspace/{boot}")
            job.update(state="ready", sha256=digest)
        except (ProviderError, OSError) as exc:
            job.update(state="failed", error=str(exc))
        finally:
            os.unlink(tmp)
            with database.new_session() as s:
                record_audit(
                    s,
                    actor=subject,
                    action="vm.media.finish",
                    target_type="environment",
                    target_id=env_id_,
                    detail={"host": host, "state": job["state"], "sha256": job["sha256"]},
                )
                s.commit()

    threading.Thread(target=run, name=f"vm-media-{rec.id}", daemon=True).start()
    return {"environment": rec.name} | _public(job)


def _public(job: dict) -> dict:
    return {k: job[k] for k in ("state", "bytes", "total", "error", "sha256")}


@router.get("/v1/workspaces/{ws_id}/environments/{env_id}/vm/media")
def media_status(
    ws_id: str,
    env_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _, rec, _ = _vm_env(db, user, ws_id, env_id)
    job = _jobs(request).get(rec.id)
    return {"environment": rec.name} | (_public(job) if job else {"state": "none"})
