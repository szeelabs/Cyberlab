"""Network segments and the pivoting lab's link preview (SSRF) practice service."""

import asyncio
import json
import random

import pytest

from cyberlab.execution import ProvisionRequest
from cyberlab.execution import target_services as ts
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.labs.generators import GENERATORS
from cyberlab.manifest.models import Environment

from .test_docker_adapter import FakeRunner, run_args
from .test_targets import free_port, talk


def env(**net):
    return Environment.model_validate(
        {
            "name": "jump",
            "baseImage": "debian-12",
            "resources": {"cpu": 0.5, "memoryMiB": 512, "storageGiB": 2},
            "network": {"mode": "lab"} | net,
        }
    )


def test_segments_default_to_one_shared_network():
    assert env().network.segments == ["default"]


@pytest.mark.parametrize(
    "net",
    [
        {"segments": []},
        {"segments": ["a", "a"]},
        {"segments": ["Bad Name"]},
        {"segments": ["a", "b", "c", "d", "e"]},
        {"mode": "isolated", "segments": ["dmz"]},
    ],
)
def test_invalid_segments(net):
    with pytest.raises(ValueError):
        env(**net)


def test_machine_joins_every_segment_with_its_name():
    r = FakeRunner(
        {
            "inspect --format": RunResult(0, "running", ""),
            "network inspect": RunResult(1, "", "no such network"),
        }
    )
    DockerAdapter(r).provision(
        ProvisionRequest(
            workspace_key="ab12cd34ef56",
            environment=env(segments=["dmz", "internal"]),
            image="docker.io/library/debian:12-slim",
        )
    )
    created = [c[-1] for c in r.calls if c[1:3] == ["network", "create"]]
    assert created == ["cyberlab-ab12cd34ef56-lab-dmz", "cyberlab-ab12cd34ef56-lab-internal"]
    assert all("--internal" in c for c in r.calls if c[1:3] == ["network", "create"])
    args = run_args(r)
    assert args[args.index("--network") + 1] == "cyberlab-ab12cd34ef56-lab-dmz"
    (connect,) = [c for c in r.calls if c[1:3] == ["network", "connect"]]
    assert connect[3:] == ["--alias", "jump", "cyberlab-ab12cd34ef56-lab-internal", "abc123def456"]


def test_segment_networks_are_removed_with_the_workspace():
    r = FakeRunner(
        {
            "network ls": RunResult(
                0, "cyberlab-ab12cd34ef56-lab-dmz\ncyberlab-ab12cd34ef56-lab-internal\nother", ""
            )
        }
    )
    DockerAdapter(r).remove_workspace_networks("ab12cd34ef56")
    removed = [c[-1] for c in r.calls if c[1:3] == ["network", "rm"]]
    assert (
        "cyberlab-ab12cd34ef56-lab-dmz" in removed
        and "cyberlab-ab12cd34ef56-lab-internal" in removed
    )
    assert "other" not in removed


# ---------------------------------------------------------------- the link preview


def run_fetch(url, allow, services=()):
    async def main():
        servers = await ts.serve({"services": list(services)})
        try:
            return await ts.fetch(url, allow)
        finally:
            for s in servers:
                s.close()

    return asyncio.run(main())


def test_preview_fetches_only_allowed_lab_machines(monkeypatch):
    monkeypatch.setattr(ts, "HOST", "127.0.0.1")
    port = free_port()
    svc = {"port": port, "kind": "http", "routes": {"/": {"body": "inside!"}}}
    status, text = run_fetch(f"http://localhost:{port}/", ["localhost"], [svc])
    assert status == 200 and "inside!" in text and text.startswith("Preview of")
    assert run_fetch(f"http://127.0.0.1:{port}/", ["localhost"], [svc])[0] == 400  # not allowed
    assert run_fetch("https://localhost/", ["localhost"])[0] == 400  # http only
    assert run_fetch("file:///etc/passwd", ["localhost"])[0] == 400
    closed = free_port()
    status, text = run_fetch(f"http://localhost:{closed}/", ["localhost"])
    assert status == 502 and "could not connect" in text


def test_preview_route_on_a_practice_target(monkeypatch):
    monkeypatch.setattr(ts, "HOST", "127.0.0.1")
    inner, outer = free_port(), free_port()
    services = [
        {"port": inner, "kind": "http", "routes": {"/": {"body": "secret page"}}},
        {
            "port": outer,
            "kind": "http",
            "routes": {"/": {"body": "home"}},
            "fetchers": {"/preview": {"param": "url", "allow": ["localhost"]}},
        },
    ]

    async def main():
        servers = await ts.serve({"services": services})
        try:
            return await talk(
                outer, f"GET /preview?url=http://localhost:{inner}/ HTTP/1.1\r\n\r\n".encode()
            )
        finally:
            for s in servers:
                s.close()

    out = asyncio.run(main())
    assert out.startswith(b"HTTP/1.1 200") and b"secret page" in out


def test_pivot_generators():
    jump = GENERATORS["pivot-jump"].run(random.Random(1), "c.json", None)  # noqa: S311
    (svc,) = json.loads(jump.files["c.json"])["services"]
    assert (
        svc["fetchers"]["/preview"]["allow"][0] == "vault"
        and "http://vault/" in svc["routes"]["/"]["body"]
    )
    vault = GENERATORS["pivot-vault"].run(random.Random(2), "c.json", None)  # noqa: S311
    web, admin = json.loads(vault.files["c.json"])["services"]
    assert vault.answers["internal-flag"] in web["routes"]["/"]["body"]
    assert admin["port"] == int(vault.answers["admin-port"]) and 8000 <= admin["port"] <= 8099
    assert vault.answers["admin-flag"] in admin["routes"]["/"]["body"]
