from datetime import UTC, datetime, timedelta

import pytest

from cyberlab.db import AuditEvent, EnvironmentRecord
from cyberlab.execution import EnvState
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.reaper import ACTOR
from cyberlab.troubleshoot import diagnose, explain_error

from .conftest import auth
from .test_environments_api import MINIMAL, YAML, FakeProvider

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
SPEC = {
    "name": "shell",
    "resources": {"cpu": 0.5, "memoryMiB": 512},
    "lifecycle": {"idleTimeoutMinutes": 60},
}


def codes(**kw):
    kw.setdefault("spec", SPEC)
    kw.setdefault("provider_connected", True)
    kw.setdefault("last_error", None)
    kw.setdefault("now", NOW)
    return [a.code for a in diagnose(**kw)]


def test_running_is_ok_and_mentions_desktop():
    (a,) = diagnose(
        state="running",
        last_error=None,
        spec={**SPEC, "access": {"desktop": True}},
        provider_connected=True,
        now=NOW,
    )
    assert a.severity == "ok" and "Desktop" in a.explanation


@pytest.mark.parametrize(
    "error, code",
    [
        ("E: Unable to locate package suricata", "build.package-missing"),
        ("error: target not found: wireshark", "build.package-missing"),
        ("toomanyrequests: You have reached your pull rate limit", "build.download-failed"),
        ("Could not resolve 'deb.debian.org'", "build.download-failed"),
        ("internet access was not approved for this environment", "approval.internet"),
        (
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            "provider.unreachable",
        ),
        ("image build timed out after 900s", "build.timeout"),
        ("something nobody has seen before", "error.other"),
    ],
)
def test_failed_states_explain_known_errors(error, code):
    assert codes(state="failed", last_error=error)[0] == code


def test_unknown_error_is_shown_not_hidden():
    a = explain_error("weird thing 42")
    assert a.code == "error.other" and "weird thing 42" in a.explanation


def test_out_of_memory_is_explained_first():
    advice = diagnose(
        state="stopped",
        last_error=None,
        spec=SPEC,
        provider_connected=True,
        runtime={"OOMKilled": True, "ExitCode": 137},
        now=NOW,
    )
    assert advice[0].code == "runtime.out-of-memory" and "512 MiB" in advice[0].explanation
    assert "runtime.crashed" not in [a.code for a in advice]


def test_idle_stop_by_reaper():
    c = codes(
        state="stopped",
        last_reaper_event={"action": "environment.stop", "reason": "idle"},
    )
    assert c == ["reaper.idle-stop"]


def test_crash_vs_normal_stop():
    assert codes(state="stopped", runtime={"ExitCode": 2}) == ["runtime.crashed"]
    assert codes(state="stopped", runtime={"ExitCode": 0}) == ["state.stopped"]
    assert codes(state="stopped", runtime={"ExitCode": 137}) == ["state.stopped"]  # docker stop


@pytest.mark.parametrize("reason, words", [("ttl", "time limit"), ("idle", "throwaway")])
def test_destroyed_by_reaper(reason, words):
    (a,) = diagnose(
        state="destroyed",
        last_error=None,
        spec=SPEC,
        provider_connected=True,
        last_reaper_event={"action": "environment.destroy", "reason": reason},
        now=NOW,
    )
    assert a.code == "reaper.destroyed" and words in a.explanation


def test_provider_not_connected_comes_first():
    c = codes(state="stopped", provider_connected=False)
    assert c[0] == "provider.not-connected"


def test_missing_and_stuck_and_provisioning():
    assert codes(state="missing") == ["state.missing"]
    assert codes(state="provisioning", updated_at=NOW - timedelta(minutes=5)) == [
        "state.provisioning"
    ]
    assert codes(state="provisioning", updated_at=NOW - timedelta(minutes=45)) == ["state.stuck"]


def test_expiry_warning_only_when_close():
    assert "lifecycle.expires-soon" in codes(
        state="running", expires_at=NOW + timedelta(minutes=30)
    )
    assert "lifecycle.expires-soon" not in codes(
        state="running", expires_at=NOW + timedelta(days=1)
    )


def test_last_error_on_running_machine_is_reported():
    c = codes(state="running", last_error="E: Unable to locate package foo")
    assert "build.package-missing" in c


def test_never_empty_and_sorted():
    for state in ["running", "stopped", "failed", "missing", "provisioning", "destroyed"]:
        advice = diagnose(
            state=state, last_error=None, spec=SPEC, provider_connected=False, now=NOW
        )
        assert advice
        order = ["problem", "warning", "info", "ok"]
        assert [order.index(a.severity) for a in advice] == sorted(
            order.index(a.severity) for a in advice
        )


# ---------------------------------------------------------------- docker details
def test_docker_details_reads_oom_and_exit_code():
    from .test_docker_adapter import FakeRunner

    state = '{"Status":"exited","OOMKilled":true,"ExitCode":137,"Error":"","Pid":0}'
    runner = FakeRunner({"inspect --format": RunResult(0, state, "")})
    d = DockerAdapter(runner).details("abc123def456")
    assert d == {
        "Status": "exited",
        "OOMKilled": True,
        "ExitCode": 137,
        "Error": "",
        "StartedAt": None,
        "FinishedAt": None,
    }
    gone = FakeRunner({"inspect --format": RunResult(1, "", "No such object")})
    assert DockerAdapter(gone).details("abc123def456") is None
    junk = FakeRunner({"inspect --format": RunResult(0, "not json", "")})
    assert DockerAdapter(junk).details("abc123def456") is None


# ---------------------------------------------------------------- API
class OomProvider(FakeProvider):
    def details(self, ref):
        return {"Status": "exited", "OOMKilled": True, "ExitCode": 137}


def make_env(client):
    ws = client.post("/v1/workspaces", json={"name": "t"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
    (e,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    return ws, e["id"]


def test_diagnose_endpoint_running(client):
    client.app.state.provider = FakeProvider()
    ws, env = make_env(client)
    r = client.get(f"/v1/workspaces/{ws}/environments/{env}/diagnose", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "running" and body["advice"][0]["code"] == "state.running"


def test_diagnose_endpoint_out_of_memory(client):
    p = OomProvider()
    client.app.state.provider = p
    ws, env = make_env(client)
    p.states[next(iter(p.states))] = EnvState.stopped
    body = client.get(f"/v1/workspaces/{ws}/environments/{env}/diagnose", headers=auth()).json()
    assert body["state"] == "stopped" and body["advice"][0]["code"] == "runtime.out-of-memory"


def test_diagnose_missing_beats_an_old_reaper_event(client):
    client.app.state.provider = FakeProvider()
    ws, env = make_env(client)
    with client.app.state.db.new_session() as db:
        db.get(EnvironmentRecord, env).state = "stopped"
        db.add(
            AuditEvent(
                actor_subject=ACTOR,
                action="environment.stop",
                target_type="environment",
                target_id=env,
                detail={"name": "shell", "reason": "idle"},
            )
        )
        db.commit()
    client.app.state.provider.states = {}  # provider no longer knows it -> "missing"
    body = client.get(f"/v1/workspaces/{ws}/environments/{env}/diagnose", headers=auth()).json()
    assert [a["code"] for a in body["advice"]] == ["state.missing"]


def test_diagnose_endpoint_idle_stop(client):
    p = FakeProvider()
    client.app.state.provider = p
    ws, env = make_env(client)
    ref = next(iter(p.states))
    p.states[ref] = EnvState.stopped
    with client.app.state.db.new_session() as db:
        db.add(
            AuditEvent(
                actor_subject=ACTOR,
                action="environment.stop",
                target_type="environment",
                target_id=env,
                detail={"name": "shell", "reason": "idle"},
            )
        )
        db.commit()
    body = client.get(f"/v1/workspaces/{ws}/environments/{env}/diagnose", headers=auth()).json()
    assert body["advice"][0]["code"] == "reaper.idle-stop"


def test_diagnose_without_provider_and_ownership(client):
    client.app.state.provider = FakeProvider()
    ws, env = make_env(client)
    assert (
        client.get(
            f"/v1/workspaces/{ws}/environments/{env}/diagnose", headers=auth("eve")
        ).status_code
        == 404
    )
    client.app.state.provider = None
    body = client.get(f"/v1/workspaces/{ws}/environments/{env}/diagnose", headers=auth()).json()
    assert body["advice"][0]["code"] == "provider.not-connected"


def test_out_of_memory_while_still_running():
    # Found on real Docker: the flag is set when a program inside is killed, while the
    # machine keeps running.
    advice = diagnose(
        state="running",
        last_error=None,
        spec=SPEC,
        provider_connected=True,
        runtime={"Status": "running", "OOMKilled": True, "ExitCode": 0},
        now=NOW,
    )
    assert [a.code for a in advice] == ["runtime.out-of-memory"]
    assert "still on" in advice[0].explanation
