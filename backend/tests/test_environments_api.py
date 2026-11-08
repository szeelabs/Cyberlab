import pytest
from fastapi.testclient import TestClient

from cyberlab.execution import (
    CostClass,
    EnvState,
    ProviderError,
    ProviderStatus,
    ProvisionResult,
)
from cyberlab.main import create_app

from .conftest import REPO, auth

YAML = {"Content-Type": "application/yaml"}
MINIMAL = (REPO / "examples" / "manifests" / "minimal.universe.yaml").read_text()
INTERNET = MINIMAL.replace("mode: isolated", "mode: internet")


class FakeProvider:
    name = "fake"

    def __init__(self, connected=True, fail_provision=False):
        self.connected = connected
        self.fail_provision = fail_provision
        self.states: dict[str, EnvState] = {}
        self.requests = []

    def status(self):
        return ProviderStatus(
            "fake",
            self.connected,
            "ok" if self.connected else "not connected: daemon down",
            CostClass.free,
        )

    def provision(self, request):
        if self.fail_provision:
            raise ProviderError("docker run failed: image not found")
        self.requests.append(request)
        ref = f"c{len(self.states)}"
        self.states[ref] = EnvState.running
        return ProvisionResult(ref, EnvState.running, {"container": ref})

    def inspect(self, ref):
        return self.states.get(ref, EnvState.missing)

    def start(self, ref):
        self.states[ref] = EnvState.running
        return EnvState.running

    def stop(self, ref):
        self.states[ref] = EnvState.stopped
        return EnvState.stopped

    def destroy(self, ref, *, remove_workspace_volume):
        self.states.pop(ref, None)
        return EnvState.destroyed


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def client(settings, provider):
    app = create_app(settings)
    app.state.provider = provider
    with TestClient(app) as c:
        yield c


def workspace(client, manifest=MINIMAL, user="user-a"):
    ws = client.post("/v1/workspaces", json={"name": "lab"}, headers=auth(user)).json()["id"]
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=manifest, headers=YAML | auth(user))
    assert r.status_code == 200, r.text
    return ws


def test_not_connected_when_no_provider(settings):
    with TestClient(create_app(settings)) as c:  # default: execution_provider = none
        st = c.get("/v1/system/execution", headers=auth()).json()
        assert st["status"] == "NOT CONNECTED" and st["connected"] is False
        ws = workspace(c)
        r = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
        assert r.status_code == 503 and r.json()["detail"]["status"] == "not_connected"


def test_not_connected_when_daemon_down(client, provider):
    provider.connected = False
    assert client.get("/v1/system/execution", headers=auth()).json()["connected"] is False
    ws = workspace(client)
    assert (
        client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).status_code == 503
    )


def test_provision_lifecycle(client, provider):
    ws = workspace(client)
    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 201, r.text
    (env,) = r.json()["environments"]
    assert env["state"] == "running" and env["name"] == "shell"
    assert provider.requests[0].image == "docker.io/library/debian:12-slim"
    assert [p.id for p in provider.requests[0].tool_packs] == ["crypto"]

    # a second provision while environments exist is refused
    assert (
        client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).status_code == 409
    )
    # deleting a workspace with live environments is refused
    assert client.delete(f"/v1/workspaces/{ws}", headers=auth()).status_code == 409

    base = f"/v1/workspaces/{ws}/environments/{env['id']}"
    assert client.post(f"{base}/stop", headers=auth()).json()["state"] == "stopped"
    assert client.post(f"{base}/start", headers=auth()).json()["state"] == "running"
    provider.states["c0"] = EnvState.stopped  # changed outside CyberLab
    listed = client.get(f"/v1/workspaces/{ws}/environments?refresh=true", headers=auth()).json()
    assert listed[0]["state"] == "stopped"
    assert client.delete(base, headers=auth()).json()["state"] == "destroyed"
    assert client.post(f"{base}/start", headers=auth()).status_code == 409
    assert client.delete(f"/v1/workspaces/{ws}", headers=auth()).status_code == 204

    actions = [e["action"] for e in client.get("/v1/audit", headers=auth()).json()]
    for a in [
        "environment.provision",
        "environment.stop",
        "environment.start",
        "environment.destroy",
    ]:
        assert a in actions


def test_failure_is_recorded_not_hidden(client, provider):
    provider.fail_provision = True
    ws = workspace(client)
    (env,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    assert env["state"] == "failed" and "image not found" in env["lastError"]
    # failed environments don't block a retry
    provider.fail_provision = False
    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 201


def test_approval_required_for_internet(client, provider):
    ws = workspace(client, INTERNET)
    plan = client.get(f"/v1/workspaces/{ws}/plan", headers=auth()).json()
    assert plan["approvalCodes"] == ["security.internet-egress"] and plan["approved"] is False

    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 409 and r.json()["detail"]["required"] == ["security.internet-egress"]

    bad = client.post(
        f"/v1/workspaces/{ws}/approvals",
        json={"revision": 1, "approve": ["something-else"]},
        headers=auth(),
    )
    assert bad.status_code == 422
    ok = client.post(
        f"/v1/workspaces/{ws}/approvals",
        json={"revision": 1, "approve": ["security.internet-egress"]},
        headers=auth(),
    )
    assert ok.status_code == 201
    assert client.get(f"/v1/workspaces/{ws}/plan", headers=auth()).json()["approved"] is True

    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 201
    assert provider.requests[0].internet_approved is True
    assert "plan.approve" in [e["action"] for e in client.get("/v1/audit", headers=auth()).json()]


def test_approval_is_per_revision(client):
    ws = workspace(client, INTERNET)
    client.post(
        f"/v1/workspaces/{ws}/approvals",
        json={"revision": 1, "approve": ["security.internet-egress"]},
        headers=auth(),
    )
    client.put(f"/v1/workspaces/{ws}/manifest", content=INTERNET, headers=YAML | auth())
    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 409  # revision 2 is not approved


def test_other_users_cannot_touch_environments(client):
    ws = workspace(client, user="alice")
    (env,) = client.post(
        f"/v1/workspaces/{ws}/environments", json={}, headers=auth("alice")
    ).json()["environments"]
    base = f"/v1/workspaces/{ws}/environments"
    for method, url in [
        ("get", base),
        ("post", f"{base}/{env['id']}/stop"),
        ("delete", f"{base}/{env['id']}"),
        ("get", f"/v1/workspaces/{ws}/plan"),
    ]:
        assert getattr(client, method)(url, headers=auth("mallory")).status_code == 404
    r = client.post(
        f"/v1/workspaces/{ws}/approvals",
        json={"revision": 1, "approve": []},
        headers=auth("mallory"),
    )
    assert r.status_code == 404


def test_endpoints_require_auth(client):
    assert client.get("/v1/system/execution").status_code == 401
