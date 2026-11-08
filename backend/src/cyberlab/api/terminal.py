"""Browser terminal: single-use tickets and the WebSocket bridge to an environment shell.

Flow
1. The user (authenticated with their normal bearer token) asks for a **ticket** for one
   running environment: ``POST /v1/workspaces/{ws}/environments/{env}/terminal``.
2. The browser opens ``/v1/terminal`` (WebSocket) and sends ``{"type":"auth","ticket":...}``
   as its first message. Browsers cannot put an Authorization header on a WebSocket, and a
   ticket in the URL would end up in logs, so it travels inside the connection instead.
3. The ticket is checked (hash match, unused, not expired, environment still running), marked
   used, and a shell is attached. Output is sent as binary frames; input, resize and ping are
   small JSON text messages.

Terminal *content* is never logged or stored; only open/close events go to the audit log.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from importlib import resources
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect, WebSocketState

from cyberlab.api.deps import current_user, get_db, request_id
from cyberlab.api.environments import _env_owned, _require_provider, get_provider
from cyberlab.api.routes import _owned
from cyberlab.config import Settings, get_settings
from cyberlab.db import EnvironmentRecord, TerminalTicket, User, record_audit
from cyberlab.execution import EnvState, ProviderAdapter, ProviderError

router = APIRouter(tags=["terminal"])

MAX_INPUT_BYTES = 16 * 1024
AUTH_TIMEOUT_SECONDS = 10
MAX_ROWS, MAX_COLS = 500, 1000
# Close codes (4000-4999 are application-defined)
CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_AUTH_FAILED = 4401
CLOSE_UNAVAILABLE = 4503
CLOSE_LIMIT = 4429
CLOSE_IDLE = 4408

PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------- tickets
@router.post("/v1/workspaces/{ws_id}/environments/{env_id}/terminal", status_code=201)
def create_ticket(
    ws_id: str,
    env_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: ProviderAdapter | None = Depends(get_provider),
) -> dict:
    ws = _owned(db, user, ws_id)
    rec = _env_owned(db, ws, env_id)
    adapter = _require_provider(provider)
    if rec.provider_ref is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "environment was never created")
    try:
        state = adapter.inspect(rec.provider_ref)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    rec.state = state.value
    if state != EnvState.running:
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"environment is {state.value}, not running")
    token = secrets.token_urlsafe(32)
    ttl = settings.terminal_ticket_ttl_seconds
    db.add(
        TerminalTicket(
            token_hash=_hash(token),
            environment_id=rec.id,
            user_subject=user.subject,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )
    )
    record_audit(
        db,
        actor=user.subject,
        action="terminal.ticket",
        target_type="environment",
        target_id=rec.id,
        detail={"name": rec.name},
        request_id=request_id(request),
    )
    db.commit()
    return {"ticket": token, "expiresIn": ttl, "websocketPath": "/v1/terminal"}


def redeem_ticket(db: Session, token: str) -> tuple[TerminalTicket, EnvironmentRecord] | None:
    """Atomically mark a ticket used. Returns None if it is unknown, used or expired."""
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    ticket = db.query(TerminalTicket).filter(TerminalTicket.token_hash == _hash(token)).first()
    now = datetime.now(UTC)
    if ticket is None or ticket.used_at is not None or _utc(ticket.expires_at) < now:
        return None
    # Conditional update: only one concurrent redeemer can win.
    won = (
        db.query(TerminalTicket)
        .filter(TerminalTicket.id == ticket.id, TerminalTicket.used_at.is_(None))
        .update({TerminalTicket.used_at: now}, synchronize_session=False)
    )
    db.commit()
    if won != 1:
        return None
    env = db.get(EnvironmentRecord, ticket.environment_id)
    return (ticket, env) if env is not None else None


# ---------------------------------------------------------------- websocket
def _origin_allowed(ws: WebSocket, settings: Settings) -> bool:
    origin = ws.headers.get("origin")
    if origin is None:  # non-browser client; the ticket still gates access
        return True
    if origin in settings.cors_origins:
        return True
    return urlparse(origin).netloc == ws.headers.get("host")


@router.websocket("/v1/terminal")
async def terminal_socket(websocket: WebSocket) -> None:
    app = websocket.app
    settings: Settings = app.state.settings
    if not _origin_allowed(websocket, settings):
        await websocket.close(code=CLOSE_FORBIDDEN_ORIGIN)
        return
    await websocket.accept()

    async def fail(code: int, message: str) -> None:
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps({"type": "error", "message": message}))
            await websocket.close(code=code)

    try:
        first = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS)
        message = json.loads(first)
    except (TimeoutError, WebSocketDisconnect, json.JSONDecodeError, KeyError):
        message = None
    token = message.get("ticket") if isinstance(message, dict) else None
    if not isinstance(message, dict) or message.get("type") != "auth":
        await fail(CLOSE_AUTH_FAILED, "authentication required")
        return

    db: Session = app.state.db.new_session()
    try:
        redeemed = redeem_ticket(db, token)
        if redeemed is None:
            await fail(CLOSE_AUTH_FAILED, "invalid or expired ticket")
            return
        ticket, env = redeemed
        subject = ticket.user_subject
        provider: ProviderAdapter | None = app.state.provider
        if provider is None or not env.provider_ref:
            await fail(CLOSE_UNAVAILABLE, "execution provider NOT CONNECTED")
            return

        counts: dict[str, int] = app.state.terminal_sessions
        if counts.get(subject, 0) >= settings.terminal_max_sessions_per_user:
            await fail(CLOSE_LIMIT, "too many open terminals")
            return
        counts[subject] = counts.get(subject, 0) + 1
        try:
            await _run_session(websocket, db, provider, env, subject, settings)
        finally:
            counts[subject] -= 1
    finally:
        db.close()


async def _run_session(
    websocket: WebSocket,
    db: Session,
    provider: ProviderAdapter,
    env: EnvironmentRecord,
    subject: str,
    settings: Settings,
) -> None:
    try:
        shell = await provider.open_shell(env.provider_ref)
    except (ProviderError, OSError) as exc:
        await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    started = time.monotonic()
    stats = {"in": 0, "out": 0}
    record_audit(
        db,
        actor=subject,
        action="terminal.open",
        target_type="environment",
        target_id=env.id,
        detail={"name": env.name},
    )
    db.commit()
    await websocket.send_text(json.dumps({"type": "ready", "environment": env.name}))

    async def pump_out() -> str:
        while True:
            data = await shell.read()
            if not data:
                return "exit"
            stats["out"] += len(data)
            await websocket.send_bytes(data)

    async def pump_in() -> str:
        while True:
            try:
                msg = await asyncio.wait_for(
                    websocket.receive(), settings.terminal_idle_timeout_seconds
                )
            except TimeoutError:
                return "idle"
            if msg["type"] == "websocket.disconnect":
                return "disconnect"
            raw = msg.get("bytes")
            if raw is None and msg.get("text") is not None:
                try:
                    m = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if not isinstance(m, dict):
                    continue
                if m.get("type") == "input" and isinstance(m.get("data"), str):
                    raw = m["data"].encode()
                elif m.get("type") == "resize":
                    rows, cols = m.get("rows"), m.get("cols")
                    if (
                        isinstance(rows, int)
                        and isinstance(cols, int)
                        and 0 < rows <= MAX_ROWS
                        and 0 < cols <= MAX_COLS
                    ):
                        shell.resize(rows, cols)
                    continue
                elif m.get("type") == "ping":
                    await websocket.send_text('{"type":"pong"}')
                    continue
                else:
                    continue
            if raw:
                if len(raw) > MAX_INPUT_BYTES:
                    continue
                stats["in"] += len(raw)
                await shell.write(raw)

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
        await shell.close()
        record_audit(
            db,
            actor=subject,
            action="terminal.close",
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
        if websocket.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps({"type": "exit", "reason": reason}))
                await websocket.close(code=CLOSE_IDLE if reason == "idle" else 1000)


# ---------------------------------------------------------------- test page
@router.get("/terminal", include_in_schema=False)
def terminal_page() -> HTMLResponse:
    html = resources.files("cyberlab").joinpath("static/terminal.html").read_text("utf-8")
    return HTMLResponse(html, headers={"Content-Security-Policy": PAGE_CSP})
