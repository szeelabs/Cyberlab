"""Automatic snapshots from workspace.snapshotPolicy: on-stop and daily."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cyberlab import autosnap
from cyberlab.execution import ProviderError
from cyberlab.main import create_app, reap_now

from .conftest import auth
from .test_environments_api import MINIMAL, YAML
from .test_snapshots import SnapshotProvider


def manifest(policy):
    return MINIMAL.rstrip("\n") + f"\n  workspace: {{snapshotPolicy: {policy}}}\n"


@pytest.fixture
def setup(settings):
    def make(policy, limit=10):
        app = create_app(settings.model_copy(update={"max_snapshots_per_workspace": limit}))
        app.state.provider = SnapshotProvider()
        c = TestClient(app)
        c.__enter__()
        ws = c.post("/v1/workspaces", json={"name": "a"}, headers=auth()).json()["id"]
        r = c.put(f"/v1/workspaces/{ws}/manifest", content=manifest(policy), headers=YAML | auth())
        assert r.status_code in (200, 201), r.text
        (e,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
            "environments"
        ]
        return app, c, ws, e["id"]

    return make


def snaps(c, ws):
    return c.get(f"/v1/workspaces/{ws}/snapshots", headers=auth()).json()


def system_events(app):
    from cyberlab.db import AuditEvent

    with app.state.db.new_session() as db:
        return {(e.action, e.actor_subject) for e in db.query(AuditEvent).all()}


def stop_start(c, ws, env_id):
    assert (
        c.post(f"/v1/workspaces/{ws}/environments/{env_id}/stop", headers=auth()).status_code == 200
    )
    assert (
        c.post(f"/v1/workspaces/{ws}/environments/{env_id}/start", headers=auth()).status_code
        == 200
    )


def test_manual_policy_takes_nothing(setup):
    app, c, ws, env_id = setup("manual")
    stop_start(c, ws, env_id)
    assert snaps(c, ws) == []


def test_on_stop_keeps_the_newest_three_and_never_the_users_own(setup):
    app, c, ws, env_id = setup("on-stop", limit=5)
    c.post(
        f"/v1/workspaces/{ws}/environments/{env_id}/snapshots",
        json={"label": "mine"},
        headers=auth(),
    )
    for _ in range(5):
        stop_start(c, ws, env_id)
    labels = [s["label"] for s in snaps(c, ws)]
    assert labels.count("automatic: when it stopped") == autosnap.KEEP_AUTOMATIC
    assert "mine" in labels  # the user's own snapshot is never removed
    assert ("snapshot.create", autosnap.ACTOR) in system_events(app)


def test_on_stop_makes_room_at_the_workspace_limit(setup):
    app, c, ws, env_id = setup("on-stop", limit=2)
    c.post(
        f"/v1/workspaces/{ws}/environments/{env_id}/snapshots",
        json={"label": "mine"},
        headers=auth(),
    )
    for _ in range(3):
        stop_start(c, ws, env_id)
    labels = sorted(s["label"] for s in snaps(c, ws))
    assert labels == ["automatic: when it stopped", "mine"]


def test_idle_stop_by_the_reaper_also_snapshots(setup):
    app, c, ws, env_id = setup("on-stop")
    with app.state.db.new_session() as db:
        from cyberlab.db import EnvironmentRecord

        rec = db.get(EnvironmentRecord, env_id)
        rec.last_activity_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    actions = reap_now(app)
    assert [a.action for a in actions] == ["stop"]
    assert [s["label"] for s in snaps(c, ws)] == ["automatic: when it stopped"]


def test_daily_snapshots_once_a_day(setup):
    app, c, ws, env_id = setup("daily")
    store, settings, provider = app.state.snapshot_store, app.state.settings, app.state.provider
    with app.state.db.new_session() as db:
        now = datetime.now(UTC)
        assert autosnap.run_daily(db, provider, store, settings, now=now) == []  # too new
        later = now + timedelta(days=1, minutes=1)
        assert autosnap.run_daily(db, provider, store, settings, now=later) == ["shell"]
        soon = datetime.now(UTC) + timedelta(hours=1)
        assert autosnap.run_daily(db, provider, store, settings, now=soon) == []  # once a day
    assert [s["label"] for s in snaps(c, ws)] == ["automatic: daily"]


def test_a_failed_automatic_snapshot_doesnt_break_the_stop(setup):
    app, c, ws, env_id = setup("on-stop")
    app.state.provider.fail_export = ProviderError("disk full")
    r = c.post(f"/v1/workspaces/{ws}/environments/{env_id}/stop", headers=auth())
    assert r.status_code == 200 and r.json()["state"] == "stopped"
    assert snaps(c, ws) == []
    with app.state.db.new_session() as db:
        from cyberlab.db import AuditEvent

        failed = [e for e in db.query(AuditEvent).all() if e.action == "snapshot.failed"]
    assert failed and "disk full" in failed[0].detail["error"]
