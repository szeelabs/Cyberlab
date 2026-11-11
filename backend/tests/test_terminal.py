import asyncio
import json
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cyberlab.db import TerminalTicket
from cyberlab.execution import EnvState
from cyberlab.main import create_app

from .conftest import auth
from .test_environments_api import MINIMAL, YAML, FakeProvider


class FakeShell:
    """Echoes input back as output; records resizes."""

    def __init__(self):
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.resizes = []
        self.closed = False

    async def read(self):
        return await self.queue.get()

    async def write(self, data):
        if data == b"exit\n":
            self.queue.put_nowait(b"")
        else:
            self.queue.put_nowait(b"echo:" + data)

    def resize(self, rows, cols):
        self.resizes.append((rows, cols))

    async def close(self):
        self.closed = True


class ShellProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.shells = []

    async def open_shell(self, ref):
        shell = FakeShell()
        self.shells.append(shell)
        return shell


@pytest.fixture
def provider():
    return ShellProvider()


@pytest.fixture
def app(settings, provider):
    a = create_app(settings.model_copy(update={"terminal_max_sessions_per_user": 2}))
    a.state.provider = provider
    return a


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def env(client):
    ws = client.post("/v1/workspaces", json={"name": "t"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
    (e,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    return ws, e["id"]


def ticket(client, env, user="user-a"):
    ws, env_id = env
    r = client.post(f"/v1/workspaces/{ws}/environments/{env_id}/terminal", headers=auth(user))
    assert r.status_code == 201, r.text
    return r.json()["ticket"]


@contextmanager
def open_terminal(client, tok, **kw):
    """Open a terminal; on exit, end a live session the proper way (exit + wait)."""
    with client.websocket_connect("/v1/terminal", **kw) as ws:
        ws.send_text(json.dumps({"type": "auth", "ticket": tok}))
        state = {"ready": False}
        orig = ws.receive_text

        def receive_text():
            text = orig()
            if json.loads(text).get("type") == "ready":
                state["ready"] = True
            elif json.loads(text).get("type") == "exit":
                state["ready"] = False
            return text

        ws.receive_text = receive_text
        yield ws
        if state["ready"]:
            ws.send_text(json.dumps({"type": "input", "data": "exit\n"}))
            for _ in range(50):
                msg = ws.receive()
                if msg["type"] == "websocket.close" or (
                    msg.get("text") and json.loads(msg["text"]).get("type") == "exit"
                ):
                    break


def test_full_session(client, env, provider):
    with open_terminal(client, ticket(client, env)) as ws:
        assert json.loads(ws.receive_text()) == {"type": "ready", "environment": "shell"}
        ws.send_text(json.dumps({"type": "input", "data": "cat zq-secret-marker\n"}))
        assert ws.receive_bytes() == b"echo:cat zq-secret-marker\n"
        ws.send_bytes(b"raw")
        assert ws.receive_bytes() == b"echo:raw"
        ws.send_text(json.dumps({"type": "resize", "rows": 40, "cols": 120}))
        ws.send_text(json.dumps({"type": "resize", "rows": 99999, "cols": 1}))  # ignored
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text()) == {"type": "pong"}
        ws.send_text(json.dumps({"type": "input", "data": "exit\n"}))
        assert json.loads(ws.receive_text()) == {"type": "exit", "reason": "exit"}
    shell = provider.shells[0]
    assert shell.resizes == [(40, 120)] and shell.closed
    events = client.get("/v1/audit", headers=auth()).json()
    close = next(e for e in events if e["action"] == "terminal.close")
    assert close["detail"]["reason"] == "exit" and close["detail"]["bytesIn"] > 0
    assert "zq-secret-marker" not in json.dumps(events)  # terminal content is never logged
    assert {"terminal.ticket", "terminal.open"} <= {e["action"] for e in events}


def test_ticket_is_single_use(client, env):
    tok = ticket(client, env)
    with open_terminal(client, tok) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
    with open_terminal(client, tok) as ws2:
        assert json.loads(ws2.receive_text()) == {
            "type": "error",
            "message": "invalid or expired ticket",
        }
        with pytest.raises(WebSocketDisconnect) as exc:
            ws2.receive_text()
        assert exc.value.code == 4401


@pytest.mark.parametrize("bad", ["", "not-a-real-ticket-but-long-enough-xx", "x" * 500])
def test_bad_tickets_rejected(client, env, bad):
    with open_terminal(client, bad) as ws:
        assert json.loads(ws.receive_text())["type"] == "error"


def test_expired_ticket_rejected(client, env, app):
    tok = ticket(client, env)
    with app.state.db.new_session() as db:
        db.query(TerminalTicket).update(
            {TerminalTicket.expires_at: datetime.now(UTC) - timedelta(seconds=1)}
        )
        db.commit()
    with open_terminal(client, tok) as ws:
        assert "expired" in json.loads(ws.receive_text())["message"]


def test_first_message_must_be_auth(client, env):
    for first in ["hello", "[1]", json.dumps({"type": "input", "data": "ls"})]:
        with client.websocket_connect("/v1/terminal") as ws:
            ws.send_text(first)
            assert json.loads(ws.receive_text())["message"] == "authentication required"


def test_cross_site_origin_rejected(client, env):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/terminal", headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4403


def test_same_origin_allowed(client, env):
    with open_terminal(client, ticket(client, env), headers={"origin": "http://testserver"}) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"


def test_ticket_requires_owner_and_running_env(client, env, provider):
    ws_id, env_id = env
    url = f"/v1/workspaces/{ws_id}/environments/{env_id}/terminal"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=auth("mallory")).status_code == 404
    provider.states["c0"] = EnvState.stopped
    r = client.post(url, headers=auth())
    assert r.status_code == 409 and "stopped" in r.json()["detail"]


def test_ticket_not_connected(client, env, provider):
    provider.connected = False
    ws_id, env_id = env
    r = client.post(f"/v1/workspaces/{ws_id}/environments/{env_id}/terminal", headers=auth())
    assert r.status_code == 503


def test_session_limit_per_user(client, env):
    with ExitStack() as stack:
        for _ in range(2):
            ws = stack.enter_context(open_terminal(client, ticket(client, env)))
            assert json.loads(ws.receive_text())["type"] == "ready"
        with open_terminal(client, ticket(client, env)) as third:
            assert json.loads(third.receive_text())["message"] == (
                "too many open terminal or desktop sessions"
            )
    # once closed, a new terminal is allowed again
    with open_terminal(client, ticket(client, env)) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"


def test_terminal_page_served_with_strict_csp(client):
    r = client.get("/terminal")
    assert r.status_code == 200 and "CyberLab Terminal" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert client.get("/static/vendor/xterm/xterm.js").status_code == 200
    assert client.get("/static/terminal.js").status_code == 200
