"""Remote desktop: single-use tickets and a WebSocket carrying VNC to the environment.

Same model as the browser terminal (see ``terminal.py``):
1. ``POST /v1/workspaces/{ws}/environments/{env}/desktop`` returns a desktop ticket for a
   running environment whose manifest has ``access.desktop: true``.
2. The browser opens ``/v1/desktop`` and sends ``{"type":"auth","ticket":...}`` first.
3. After ``{"type":"ready"}``, every frame is binary VNC (RFB) in both directions. The bytes
   go through ``docker exec`` to x11vnc inside the environment: no network port is opened,
   and the VNC server has no password because the ticket already decided who may connect.

Screen contents are never logged or stored; only open/close events are audited.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketState

from cyberlab.api.deps import current_user, get_db
from cyberlab.api.environments import _env_owned, get_provider
from cyberlab.api.routes import _owned
from cyberlab.api.terminal import CLOSE_UNAVAILABLE, issue_ticket, serve_ticketed
from cyberlab.config import Settings, get_settings
from cyberlab.db import EnvironmentRecord, User, record_audit
from cyberlab.execution import ProviderAdapter, ProviderError

router = APIRouter(tags=["desktop"])

# VNC messages from a viewer are small (key/mouse events, update requests); clipboard text
# is the largest. Anything bigger is dropped.
MAX_CLIENT_FRAME = 1024 * 1024


def desktop_enabled(rec: EnvironmentRecord) -> bool:
    return bool((rec.spec.get("access") or {}).get("desktop"))


@router.post("/v1/workspaces/{ws_id}/environments/{env_id}/desktop", status_code=201)
def create_desktop_ticket(
    ws_id: str,
    env_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    rec = _env_owned(db, _owned(db, user, ws_id), env_id)
    if not desktop_enabled(rec):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this environment has no desktop; set access.desktop: true in the manifest "
            "and create the environment again",
        )
    token = issue_ticket(db, rec, user, request, settings, provider, "desktop")
    ttl = settings.terminal_ticket_ttl_seconds
    return {"ticket": token, "expiresIn": ttl, "websocketPath": "/v1/desktop"}


@router.websocket("/v1/desktop")
async def desktop_socket(websocket: WebSocket) -> None:
    await serve_ticketed(websocket, "desktop", _run_desktop)


async def _run_desktop(
    websocket: WebSocket,
    db: Session,
    provider: ProviderAdapter,
    env: EnvironmentRecord,
    subject: str,
    settings: Settings,
    release: Callable[[], None],
) -> None:
    try:
        stream = await provider.open_desktop(env.provider_ref)
    except (ProviderError, OSError) as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    started = time.monotonic()
    stats = {"in": 0, "out": 0}
    record_audit(
        db,
        actor=subject,
        action="desktop.open",
        target_type="environment",
        target_id=env.id,
        detail={"name": env.name},
    )
    db.commit()
    await websocket.send_text(json.dumps({"type": "ready", "environment": env.name}))

    async def pump_out() -> str:
        while True:
            data = await stream.read()
            if not data:
                return "exit"
            stats["out"] += len(data)
            await websocket.send_bytes(data)

    async def pump_in() -> str:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return "disconnect"
            raw = msg.get("bytes")
            if raw is None or len(raw) > MAX_CLIENT_FRAME:
                continue  # VNC is binary only; text frames and oversized frames are ignored
            stats["in"] += len(raw)
            await stream.write(raw)

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
    reason = "limit"
    try:
        done, _ = await asyncio.wait(
            tasks,
            timeout=settings.terminal_max_session_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            reason = t.result() if not t.cancelled() and t.exception() is None else "error"
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stream.close()
        record_audit(
            db,
            actor=subject,
            action="desktop.close",
            target_type="environment",
            target_id=env.id,
            detail={
                "name": env.name,
                "reason": reason,
                "seconds": round(time.monotonic() - started),
                "bytesIn": stats["in"],
                "bytesOut": stats["out"],
            },
        )
        db.commit()
        release()
        if websocket.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=1000)
