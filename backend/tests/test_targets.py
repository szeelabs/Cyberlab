"""Lab practice targets: the practice-services program, generators, policy and lockdown."""

import asyncio
import json
import random
import socket

import pytest
from fastapi.testclient import TestClient

from cyberlab.execution import EnvState, ProvisionRequest
from cyberlab.execution import target_services as ts
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.execution.images import TARGET_COMMAND, render
from cyberlab.labs.generators import GENERATORS
from cyberlab.main import create_app
from cyberlab.manifest.models import Environment, UniverseManifest
from cyberlab.manifest.policy import evaluate

from .conftest import auth
from .test_docker_adapter import FakeRunner, run_args
from .test_labs import LabProvider

# ---------------------------------------------------------------- the practice program


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def talk(port: int, send: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    if send:
        writer.write(send)
        await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    return data


@pytest.fixture
def running(monkeypatch):
    """Start the practice services on free local ports with a given config."""
    monkeypatch.setattr(ts, "HOST", "127.0.0.1")

    def run(services, script):
        async def main():
            servers = await ts.serve({"services": services})
            try:
                return await script()
            finally:
                for s in servers:
                    s.close()

        return asyncio.run(main())

    return run


def test_banner_service_greets_and_closes(running):
    port = free_port()
    out = running(
        [{"port": port, "kind": "banner", "banner": "SSH-2.0-OpenSSH_9.6p1"}],
        lambda: talk(port, b"hello\n"),
    )
    assert out == b"SSH-2.0-OpenSSH_9.6p1\r\n"


def test_http_service_serves_only_its_table(running):
    port = free_port()
    service = {
        "port": port,
        "kind": "http",
        "server": "nginx/1.24.0",
        "routes": {
            "/": {"body": "<h1>home</h1>"},
            "/api?id=7": {"type": "application/json", "body": '{"ok": true}'},
            "/old": {"status": 301, "location": "/new", "body": ""},
        },
    }

    async def script():
        get = lambda path: talk(port, f"GET {path} HTTP/1.1\r\nHost: t\r\n\r\n".encode())  # noqa: E731
        return (
            await get("/"),
            await get("/api?id=7"),
            await get("/api?id=8"),
            await get("/old"),
            await get("/../../etc/passwd"),
            await talk(port, b"HEAD / HTTP/1.1\r\n\r\n"),
        )

    home, api, missing, moved, traversal, head = running([service], script)
    assert home.startswith(b"HTTP/1.1 200 OK") and b"Server: nginx/1.24.0" in home
    assert home.endswith(b"<h1>home</h1>")
    assert b"application/json" in api and api.endswith(b'{"ok": true}')
    assert missing.startswith(b"HTTP/1.1 404") and moved.startswith(b"HTTP/1.1 301")
    assert b"Location: /new" in moved
    assert traversal.startswith(b"HTTP/1.1 404")  # a lookup table, never a file system
    assert head.startswith(b"HTTP/1.1 200") and b"<h1>" not in head


def test_service_waits_for_its_config(tmp_path):
    with pytest.raises(TimeoutError):
        ts.load(str(tmp_path / "missing.json"), wait=0.6)
    (tmp_path / "c.json").write_text('{"services": []}')
    assert ts.load(str(tmp_path / "c.json"), wait=1) == {"services": []}


def test_practice_program_uses_only_the_standard_library():
    import ast
    import sys
    from pathlib import Path

    tree = ast.parse(Path(ts.__file__).read_text())
    mods = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    mods |= {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}
    # nothing that could run commands or open arbitrary files from a request
    assert not mods & {"subprocess", "shutil", "pickle", "ctypes"}


# ---------------------------------------------------------------- generators


def test_scan_target_hides_one_service_above_the_default_scan():
    out = GENERATORS["scan-target"].run(random.Random(3), ".cyberlab-target.json", None)  # noqa: S311
    services = json.loads(out.files[".cyberlab-target.json"])["services"]
    ports = sorted(s["port"] for s in services)
    assert ports[:2] == [21, 22] and ports[3] == int(out.answers["hidden-port"]) > 10000
    web = next(s for s in services if s["kind"] == "http")
    assert web["port"] == int(out.answers["web-port"]) in (3000, 5000, 8000, 8080, 8888)
    hidden = next(s for s in services if s["port"] == ports[3])
    assert out.answers["banner-flag"] in hidden["banner"]
    ssh = next(s for s in services if s["port"] == 22)
    assert f"OpenSSH_{out.answers['ssh-version']} " in ssh["banner"]


def test_web_target_leaks_exactly_three_planted_secrets():
    out = GENERATORS["web-target"].run(random.Random(5), ".cyberlab-target.json", None)  # noqa: S311
    (svc,) = json.loads(out.files[".cyberlab-target.json"])["services"]
    routes = svc["routes"]
    assert out.answers["source-flag"] in routes["/"]["body"]
    hidden = routes["/robots.txt"]["body"].split("Disallow: ")[1].strip()
    assert out.answers["robots-flag"] in routes[hidden]["body"]
    admin = [
        p for p, r in routes.items() if p.startswith("/api/notes?id=") and "admin" in r["body"]
    ]
    assert len(admin) == 1 and out.answers["idor-flag"] in routes[admin[0]]["body"]
    assert int(admin[0].split("=")[1]) > 3  # not one of the guest's own notes
    flags = {v for k, v in out.answers.items() if k.endswith("-flag")}
    assert len(flags) == 3


# ---------------------------------------------------------------- policy


def plan(registry, *envs):
    m = UniverseManifest.model_validate(
        {
            "apiVersion": "cyberlab.dev/v1alpha1",
            "kind": "Universe",
            "metadata": {"name": "t"},
            "spec": {"environments": list(envs)},
        }
    )
    return {
        f.code: f.message
        for f in evaluate(
            m, registry, __import__("cyberlab.config").config.QuotaSettings()
        ).findings
    }


def machine(**over):
    base = {
        "name": "target",
        "role": "target",
        "baseImage": "lab-target",
        "resources": {"cpu": 0.25, "memoryMiB": 128, "storageGiB": 1},
        "network": {"mode": "lab"},
    }
    return base | over


def test_valid_target_passes(registry):
    assert plan(registry, machine()) == {}


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"role": "workstation"}, "registry.target"),  # the target base is not a workstation
        (
            {
                "baseImage": "ubuntu-24-04",
                "resources": {"cpu": 1, "memoryMiB": 1024, "storageGiB": 4},
            },
            "registry.target",
        ),
        ({"network": {"mode": "isolated"}}, "security.target-network"),
        ({"network": {"mode": "internet"}}, "security.target-network"),
        ({"toolPacks": ["crypto"]}, "registry.target"),
    ],
)
def test_invalid_targets_are_refused(registry, over, code):
    assert code in plan(registry, machine(**over))


# ---------------------------------------------------------------- images and run args


def test_target_image_embeds_the_practice_program(registry):
    r = render("docker.io/library/python:3.13-slim", [], target=True)
    assert TARGET_COMMAND in r.dockerfile and "apt-get" not in r.dockerfile
    assert r.tag != render("docker.io/library/python:3.13-slim", []).tag


def target_req():
    return ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=Environment.model_validate(machine()),
        image="docker.io/library/python:3.13-slim",
    )


def test_target_runs_practice_services_hardened_with_low_ports():
    runner = FakeRunner({"inspect --format": RunResult(0, "running", "")})
    DockerAdapter(runner).provision(target_req())
    args = run_args(runner)
    joined = " ".join(args)
    assert args[-3:] == ["python3", "-B", TARGET_COMMAND]
    assert "--sysctl net.ipv4.ip_unprivileged_port_start=0" in joined
    for flag in ("--cap-drop ALL", "--read-only", "--user 1000:1000", "--network-alias target"):
        assert flag in joined


def test_workstations_get_a_name_on_the_lab_network_but_no_port_setting():
    from .test_docker_adapter import req

    runner = FakeRunner({"inspect --format": RunResult(0, "running", "")})
    DockerAdapter(runner).provision(req("lab"))
    joined = " ".join(run_args(runner))
    assert "--network-alias shell" in joined and "--sysctl" not in joined
    assert run_args(runner)[-2:] == ["sleep", "infinity"]
    runner = FakeRunner({"inspect --format": RunResult(0, "running", "")})
    DockerAdapter(runner).provision(req("isolated"))
    assert "--network-alias" not in run_args(runner)  # no network, no name


# ---------------------------------------------------------------- lockdown via the API


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.provider = LabProvider()
    with TestClient(app) as c:
        yield c, app.state.provider


def test_scanning_lab_places_the_config_on_the_target_only(client):
    c, provider = client
    out = c.post("/v1/labs/network-scanning/start", headers=auth()).json()
    ws = out["workspaceId"]
    envs = {e["name"]: e for e in c.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()}
    assert envs["target"]["role"] == "target" and envs["target"]["terminal"] is False
    assert envs["attacker"]["role"] == "workstation" and envs["attacker"]["terminal"] is True
    target_ref = envs["target"]["resources"]["container"]
    assert list(provider.files) == [target_ref]
    config = json.loads(provider.files[target_ref][".cyberlab-target.json"])
    hidden = max(s["port"] for s in config["services"])
    r = c.post(
        f"/v1/workspaces/{ws}/lab/answer",
        json={"task": "hidden-port", "answer": str(hidden)},
        headers=auth(),
    )
    assert r.json()["correct"] is True


def test_practice_targets_cannot_be_opened_directly(client):
    c, _ = client
    ws = c.post("/v1/labs/web-secrets/start", headers=auth()).json()["workspaceId"]
    envs = {e["name"]: e for e in c.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()}
    tid = envs["target"]["id"]
    base = f"/v1/workspaces/{ws}/environments/{tid}"
    for method, path, body in [
        ("post", "/terminal", None),
        ("get", "/files?path=.", None),
        ("get", "/files/content?path=.cyberlab-target.json", None),
        ("post", "/snapshots", {"label": "x"}),
        ("post", "/install", {"path": "a.deb"}),
    ]:
        r = getattr(c, method)(base + path, headers=auth(), **({"json": body} if body else {}))
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert "practice target" in r.text
    # (a desktop is refused even earlier: targets can't have one)
    assert c.post(base + "/desktop", headers=auth()).status_code in (403, 409)
    # the learner's own machine is not affected
    aid = envs["attacker"]["id"]
    r = c.post(f"/v1/workspaces/{ws}/environments/{aid}/terminal", headers=auth())
    assert r.status_code != 403


def test_terminal_access_false_is_honoured(client, registry):
    c, provider = client
    ws = c.post("/v1/workspaces", json={"name": "no-term"}, headers=auth()).json()["id"]
    manifest = (
        "apiVersion: cyberlab.dev/v1alpha1\nkind: Universe\nmetadata: {name: n}\nspec:\n"
        "  environments:\n    - name: box\n      baseImage: debian-12\n"
        "      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}\n"
        "      access: {terminal: false}\n"
    )
    c.put(
        f"/v1/workspaces/{ws}/manifest",
        content=manifest,
        headers={"Content-Type": "application/yaml"} | auth(),
    )
    (e,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    assert e["terminal"] is False
    r = c.post(f"/v1/workspaces/{ws}/environments/{e['id']}/terminal", headers=auth())
    assert r.status_code == 403 and "no terminal" in r.text
    assert provider.states[e["resources"]["container"]] == EnvState.running
