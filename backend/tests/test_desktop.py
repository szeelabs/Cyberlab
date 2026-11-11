import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cyberlab.execution import ProviderError
from cyberlab.main import create_app

from .conftest import auth
from .test_environments_api import MINIMAL, YAML
from .test_terminal import ShellProvider

DESKTOP = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: desk}
spec:
  environments:
    - name: gui
      baseImage: ubuntu-24-04
      resources: {cpu: 1, memoryMiB: 2048, storageGiB: 4}
      network: {mode: isolated}
      access: {desktop: true}
"""


class FakeVnc:
    """Pretends to be x11vnc: sends a greeting, then echoes what it receives."""

    def __init__(self):
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.queue.put_nowait(b"RFB 003.008\n")
        self.received = []
        self.closed = False

    async def read(self):
        return await self.queue.get()

    async def write(self, data):
        self.received.append(data)
        if data == b"bye":
            self.queue.put_nowait(b"")  # the VNC server ends the stream
        else:
            self.queue.put_nowait(b"vnc:" + data)

    async def close(self):
        self.closed = True


class DesktopProvider(ShellProvider):
    def __init__(self):
        super().__init__()
        self.desktops = []
        self.fail = False

    async def open_desktop(self, ref):
        if self.fail:
            raise ProviderError("exec failed")
        d = FakeVnc()
        self.desktops.append(d)
        return d


@pytest.fixture
def provider():
    return DesktopProvider()


@pytest.fixture
def client(settings, provider):
    app = create_app(settings.model_copy(update={"terminal_max_sessions_per_user": 2}))
    app.state.provider = provider
    with TestClient(app) as c:
        yield c


def make_env(client, manifest=DESKTOP, user="user-a"):
    ws = client.post("/v1/workspaces", json={"name": "d"}, headers=auth(user)).json()["id"]
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=manifest, headers=YAML | auth(user))
    assert r.status_code in (200, 201), r.text
    (e,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth(user)).json()[
        "environments"
    ]
    return ws, e


def desk_ticket(client, ws, env_id, user="user-a"):
    r = client.post(f"/v1/workspaces/{ws}/environments/{env_id}/desktop", headers=auth(user))
    assert r.status_code == 201, r.text
    assert r.json()["websocketPath"] == "/v1/desktop"
    return r.json()["ticket"]


def test_desktop_flag_is_reported_and_provisioned(client, provider):
    ws, env = make_env(client)
    assert env["desktop"] is True
    (req,) = provider.requests
    assert req.environment.access.desktop is True


def test_full_desktop_session_is_binary_and_audited(client, provider):
    ws, env = make_env(client)
    tok = desk_ticket(client, ws, env["id"])
    with client.websocket_connect("/v1/desktop") as sock:
        sock.send_text(json.dumps({"type": "auth", "ticket": tok}))
        assert json.loads(sock.receive_text()) == {"type": "ready", "environment": "gui"}
        assert sock.receive_bytes() == b"RFB 003.008\n"
        sock.send_bytes(b"RFB 003.008\n")
        assert sock.receive_bytes() == b"vnc:RFB 003.008\n"
        sock.send_text("text frames are not VNC")  # ignored
        sock.send_bytes(b"x" * (1024 * 1024 + 1))  # oversized: dropped
        sock.send_bytes(b"bye")
        with pytest.raises(WebSocketDisconnect):
            sock.receive_bytes()
    (d,) = provider.desktops
    assert d.received == [b"RFB 003.008\n", b"bye"] and d.closed
    actions = [e["action"] for e in client.get("/v1/audit", headers=auth()).json()]
    assert {"desktop.ticket", "desktop.open", "desktop.close"} <= set(actions)
    close = next(
        e for e in client.get("/v1/audit", headers=auth()).json() if e["action"] == "desktop.close"
    )
    assert close["detail"]["reason"] == "exit" and close["detail"]["bytesIn"] == 15


def test_no_desktop_ticket_without_access_desktop(client):
    ws, env = make_env(client, MINIMAL)
    assert env["desktop"] is False
    r = client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/desktop", headers=auth())
    assert r.status_code == 409 and "access.desktop" in r.json()["detail"]


def test_tickets_cannot_be_swapped(client):
    ws, env = make_env(client)
    term = client.post(
        f"/v1/workspaces/{ws}/environments/{env['id']}/terminal", headers=auth()
    ).json()["ticket"]
    desk = desk_ticket(client, ws, env["id"])
    for path, tok in (("/v1/desktop", term), ("/v1/terminal", desk)):
        with client.websocket_connect(path) as sock:
            sock.send_text(json.dumps({"type": "auth", "ticket": tok}))
            assert "invalid or expired" in json.loads(sock.receive_text())["message"]


def test_desktop_ticket_single_use_and_owner_only(client):
    ws, env = make_env(client)
    r = client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/desktop", headers=auth("eve"))
    assert r.status_code == 404
    tok = desk_ticket(client, ws, env["id"])
    with client.websocket_connect("/v1/desktop") as sock:
        sock.send_text(json.dumps({"type": "auth", "ticket": tok}))
        assert json.loads(sock.receive_text())["type"] == "ready"
        sock.receive_bytes()
        sock.send_bytes(b"bye")
        with pytest.raises(WebSocketDisconnect):
            sock.receive_bytes()
    with client.websocket_connect("/v1/desktop") as sock:  # reuse
        sock.send_text(json.dumps({"type": "auth", "ticket": tok}))
        assert "invalid or expired" in json.loads(sock.receive_text())["message"]


def test_desktop_needs_auth_message_first(client):
    with client.websocket_connect("/v1/desktop") as sock:
        sock.send_bytes(b"RFB 003.008\n")
        assert "authentication required" in json.loads(sock.receive_text())["message"]


def test_desktop_rejects_foreign_origin(client):
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/v1/desktop", headers={"Origin": "https://evil.example"}) as s,
    ):
        s.receive_text()


def test_desktop_counts_toward_session_limit(client, provider):
    ws, env = make_env(client)
    socks = []
    try:
        for _ in range(2):
            s = client.websocket_connect("/v1/desktop").__enter__()
            socks.append(s)
            s.send_text(json.dumps({"type": "auth", "ticket": desk_ticket(client, ws, env["id"])}))
            assert json.loads(s.receive_text())["type"] == "ready"
        with client.websocket_connect("/v1/desktop") as third:
            third.send_text(
                json.dumps({"type": "auth", "ticket": desk_ticket(client, ws, env["id"])})
            )
            assert "too many open" in json.loads(third.receive_text())["message"]
    finally:
        for s in socks:
            s.receive_bytes()
            s.send_bytes(b"bye")
            with pytest.raises(WebSocketDisconnect):
                s.receive_bytes()
            s.__exit__(None, None, None)


def test_desktop_open_failure_is_reported(client, provider):
    ws, env = make_env(client)
    provider.fail = True
    tok = desk_ticket(client, ws, env["id"])
    with client.websocket_connect("/v1/desktop") as sock:
        sock.send_text(json.dumps({"type": "auth", "ticket": tok}))
        assert json.loads(sock.receive_text()) == {"type": "error", "message": "exec failed"}


def test_desktop_on_unsupported_system_is_refused_by_policy(client):
    r = client.post(
        "/v1/manifests/validate",
        content=DESKTOP.replace("ubuntu-24-04", "debian-12"),
        headers=YAML | auth(),
    )
    assert not r.json()["valid"]
    assert any(f["code"] == "access.desktop" for f in r.json()["findings"])
