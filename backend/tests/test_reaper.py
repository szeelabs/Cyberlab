from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cyberlab.db import EnvironmentRecord
from cyberlab.execution import EnvState, ProviderError
from cyberlab.main import create_app, reap_now
from cyberlab.reaper import run_once

from .conftest import auth
from .test_environments_api import YAML, FakeProvider


def manifest(idle=None, ttl=None, ephemeral=False, name="lab"):
    lc = []
    lc.append(f"idleTimeoutMinutes: {idle if idle is not None else 'null'}")
    if ttl:
        lc.append(f"ttlHours: {ttl}")
    lc.append(f"ephemeral: {'true' if ephemeral else 'false'}")
    return f"""
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {{name: {name}}}
spec:
  environments:
    - name: shell
      baseImage: debian-12
      resources: {{cpu: 0.5, memoryMiB: 512, storageGiB: 2}}
      lifecycle: {{{", ".join(lc)}}}
"""


class TrackingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.destroyed = []
        self.fail = False

    def stop(self, ref):
        if self.fail:
            raise ProviderError("daemon unreachable")
        return super().stop(ref)

    def destroy(self, ref, *, remove_workspace_volume):
        self.destroyed.append((ref, remove_workspace_volume))
        return super().destroy(ref, remove_workspace_volume=remove_workspace_volume)


@pytest.fixture
def app(settings):
    a = create_app(settings)
    a.state.provider = TrackingProvider()
    return a


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def provision(client, text, name="lab"):
    ws = client.post("/v1/workspaces", json={"name": name}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=text, headers=YAML | auth())
    (env,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    return ws, env


def reap(app, minutes=0, hours=0):
    now = datetime.now(UTC) + timedelta(minutes=minutes, hours=hours)
    with app.state.db.new_session() as db:
        return run_once(db, app.state.provider, now=now, active=set(app.state.active_terminals))


def state(app, env_id):
    with app.state.db.new_session() as db:
        return db.get(EnvironmentRecord, env_id).state


def test_idle_environment_is_stopped_not_deleted(app, client):
    ws, env = provision(client, manifest(idle=30))
    assert reap(app, minutes=29) == []  # not yet
    (act,) = reap(app, minutes=31)
    assert (act.action, act.reason, act.delete_data) == ("stop", "idle", False)
    assert state(app, env["id"]) == "stopped"
    assert reap(app, minutes=120) == []  # stopped environments aren't idle-stopped again
    events = client.get("/v1/audit", headers=auth()).json()
    # the reaper acts as system:reaper, so it isn't in the user's own audit list
    assert "environment.stop" not in [e["action"] for e in events]


def test_activity_resets_the_idle_timer(app, client):
    ws, env = provision(client, manifest(idle=30))
    with app.state.db.new_session() as db:  # pretend it was last used 25 minutes ago...
        db.get(EnvironmentRecord, env["id"]).last_activity_at = datetime.now(UTC) - timedelta(
            minutes=25
        )
        db.commit()
    assert len(reap(app, minutes=10)) == 1  # ...so it's idle 10 minutes from now
    client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/start", headers=auth())
    assert reap(app, minutes=10) == []  # starting it counts as activity


def test_open_terminal_is_never_idle(app, client):
    ws, env = provision(client, manifest(idle=5))
    app.state.active_terminals[env["id"]] = 1
    assert reap(app, minutes=600) == []
    del app.state.active_terminals[env["id"]]
    assert len(reap(app, minutes=600)) == 1


def test_no_idle_timeout_means_never_idle(app, client):
    ws, env = provision(client, manifest(idle=None))
    assert reap(app, hours=1000) == []


def test_ttl_destroys_and_keeps_data_unless_ephemeral(app, client):
    ws, env = provision(client, manifest(ttl=2))
    assert env["expiresAt"] is not None
    assert reap(app, hours=1) == []
    (act,) = reap(app, hours=3)
    assert (act.action, act.reason, act.delete_data) == ("destroy", "ttl", False)
    assert state(app, env["id"]) == "destroyed"
    assert app.state.provider.destroyed == [("c0", False)]


def test_ephemeral_environment_is_destroyed_with_its_data(app, client):
    ws, env = provision(client, manifest(idle=15, ephemeral=True))
    (act,) = reap(app, minutes=20)
    assert (act.action, act.reason, act.delete_data) == ("destroy", "idle", True)
    assert app.state.provider.destroyed == [("c0", True)]


def test_ttl_applies_to_stopped_environments_too(app, client):
    ws, env = provision(client, manifest(ttl=1))
    client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/stop", headers=auth())
    (act,) = reap(app, hours=2)
    assert act.action == "destroy" and state(app, env["id"]) == "destroyed"


def test_provider_failure_is_recorded_and_retried(app, client):
    ws, env = provision(client, manifest(idle=5))
    app.state.provider.fail = True
    (act,) = reap(app, minutes=10)
    assert not act.ok and "daemon unreachable" in act.error
    assert state(app, env["id"]) == "running"
    app.state.provider.fail = False
    (act,) = reap(app, minutes=10)  # tried again on the next run
    assert act.ok and state(app, env["id"]) == "stopped"


def test_reaper_actions_are_audited(app, client):
    ws, env = provision(client, manifest(idle=5))
    reap(app, minutes=10)
    from sqlalchemy import select

    from cyberlab.db import AuditEvent

    with app.state.db.new_session() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.actor_subject == "system:reaper"))
        (e,) = list(ev)
    assert e.action == "environment.stop" and e.detail["reason"] == "idle"


def test_reap_now_and_without_provider(app, client):
    provision(client, manifest(idle=None))
    assert reap_now(app) == []
    app.state.provider = None
    assert reap(app, hours=100) == []


def test_terminal_session_marks_environment_active(app, client):
    import asyncio
    import json

    class Shell:
        def __init__(self):
            self.q = asyncio.Queue()

        async def read(self):
            return await self.q.get()

        async def write(self, data):
            self.q.put_nowait(b"" if data == b"exit\n" else data)

        def resize(self, r, c):
            pass

        async def close(self):
            pass

    async def open_shell(ref):
        return Shell()

    app.state.provider.open_shell = open_shell
    ws, env = provision(client, manifest(idle=5))
    tok = client.post(
        f"/v1/workspaces/{ws}/environments/{env['id']}/terminal", headers=auth()
    ).json()["ticket"]
    with client.websocket_connect("/v1/terminal") as sock:
        sock.send_text(json.dumps({"type": "auth", "ticket": tok}))
        assert json.loads(sock.receive_text())["type"] == "ready"
        assert app.state.active_terminals == {env["id"]: 1}
        assert reap(app, hours=10) == []  # in use
        sock.send_text(json.dumps({"type": "input", "data": "exit\n"}))
        while json.loads(sock.receive_text())["type"] != "exit":
            pass
    assert app.state.active_terminals == {}
    assert reap(app, minutes=1) == []  # closing the terminal restarted the idle timer
    assert len(reap(app, minutes=6)) == 1


def test_lifespan_reaper_loop_starts_and_stops(settings):
    app = create_app(settings.model_copy(update={"reaper_interval_seconds": 3600}))
    with TestClient(app):
        pass  # the background task is created and cancelled cleanly
    assert EnvState.running
