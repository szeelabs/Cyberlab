"""Creating machines in the background, with progress, for a fast and friendly web app."""

import threading
import time

from fastapi.testclient import TestClient

from cyberlab.execution import ProviderError
from cyberlab.main import create_app

from .conftest import auth
from .test_environments_api import MINIMAL, YAML
from .test_labs import LabProvider, flag_in


class SlowProvider(LabProvider):
    """Reports progress, then waits until the test lets it finish."""

    def __init__(self, fail=False):
        super().__init__()
        self.go = threading.Event()
        self.fail = fail

    def provision(self, request):
        if request.progress:
            request.progress("installing the system and its tools")
        assert self.go.wait(10)
        if self.fail:
            raise ProviderError("the image build failed")
        return super().provision(request)


def until(fn, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_lab_starts_in_the_background_with_progress(settings):
    app = create_app(settings)
    app.state.provider = p = SlowProvider()
    with TestClient(app) as c:
        started = time.monotonic()
        out = c.post("/v1/labs/linux-first-steps/start?wait=false", headers=auth()).json()
        assert time.monotonic() - started < 3  # answered while the machine is still building
        ws = out["workspaceId"]
        assert out["ready"] is False and out["progress"] == {"solved": 0, "total": 4}
        envs = lambda: c.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()  # noqa: E731
        (env,) = until(
            lambda: [e for e in envs() if e["progress"] == "installing the system and its tools"]
        )
        assert env["state"] == "provisioning"
        assert c.get(f"/v1/workspaces/{ws}/lab", headers=auth()).json()["ready"] is False
        p.go.set()
        until(lambda: c.get(f"/v1/workspaces/{ws}/lab", headers=auth()).json()["ready"])
        (env,) = envs()
        assert env["state"] == "running" and env["progress"] is None
        lab = c.get(f"/v1/workspaces/{ws}/lab", headers=auth()).json()
        assert lab["setupError"] is None
        (files,) = p.files.values()
        r = c.post(
            f"/v1/workspaces/{ws}/lab/answer",
            json={"task": "read-a-file", "answer": flag_in(files["welcome.txt"])},
            headers=auth(),
        )
        assert r.json()["correct"] is True


def test_background_failure_is_reported(settings):
    app = create_app(settings)
    app.state.provider = p = SlowProvider(fail=True)
    with TestClient(app) as c:
        ws = c.post("/v1/labs/linux-first-steps/start?wait=false", headers=auth()).json()[
            "workspaceId"
        ]
        p.go.set()
        lab = until(
            lambda: (r := c.get(f"/v1/workspaces/{ws}/lab", headers=auth()).json())["ready"] and r
        )
        assert "image build failed" in lab["setupError"]
        (env,) = c.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()
        assert env["state"] == "failed" and "image build failed" in env["lastError"]


def test_environments_can_be_created_in_the_background(settings):
    app = create_app(settings)
    app.state.provider = p = SlowProvider()
    with TestClient(app) as c:
        ws = c.post("/v1/workspaces", json={"name": "bg"}, headers=auth()).json()["id"]
        c.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
        r = c.post(f"/v1/workspaces/{ws}/environments?wait=false", json={}, headers=auth())
        assert r.status_code == 201 and r.json()["environments"][0]["state"] == "provisioning"
        p.go.set()
        until(
            lambda: (
                c.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()[0]["state"]
                == "running"
            )
        )


def test_machines_interrupted_by_a_restart_are_marked_failed(settings, tmp_path):
    db = f"sqlite:///{tmp_path / 'db.sqlite'}"
    s = settings.model_copy(update={"database_url": db})
    app = create_app(s)
    app.state.provider = p = SlowProvider()
    with TestClient(app) as c:
        ws = c.post("/v1/labs/linux-first-steps/start?wait=false", headers=auth()).json()[
            "workspaceId"
        ]
    # the server stops before the machine was created; a new server starts on the same data
    app2 = create_app(s)
    app2.state.provider = LabProvider()
    with TestClient(app2) as c:
        (env,) = c.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()
        assert env["state"] == "failed" and "restarted" in env["lastError"]
    p.go.set()
