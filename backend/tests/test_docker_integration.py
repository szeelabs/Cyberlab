"""End-to-end tests against a real Docker Engine.

Run with: CYBERLAB_DOCKER_TESTS=1 pytest tests/test_docker_integration.py
Optional: CYBERLAB_TEST_IMAGE_MIRROR=mirror.gcr.io (avoids Docker Hub rate limits).
"""

import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from cyberlab.main import create_app

from .conftest import REPO, auth

pytestmark = pytest.mark.skipif(
    os.environ.get("CYBERLAB_DOCKER_TESTS") != "1", reason="set CYBERLAB_DOCKER_TESTS=1"
)
YAML = {"Content-Type": "application/yaml"}
MINIMAL = (REPO / "examples" / "manifests" / "minimal.universe.yaml").read_text()
# Same environment without tool packs: still a built image (with the cyberlab user), but
# no package downloads, so it also works on hosts without internet access for builds.
BASIC = MINIMAL.replace("toolPacks: [crypto]", "toolPacks: []")
assert BASIC != MINIMAL
LAB = """
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: lab-pair}
spec:
  environments:
    - {name: alpha, baseImage: debian-12, resources: {cpu: 0.25, memoryMiB: 256, storageGiB: 1}}
    - {name: beta, baseImage: debian-12, resources: {cpu: 0.25, memoryMiB: 256, storageGiB: 1}}
"""


def sh(container, cmd):
    p = subprocess.run(
        ["docker", "exec", container, "sh", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def docker(*args):
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def client(settings):
    s = settings.model_copy(
        update={
            "execution_provider": "docker",
            "image_mirror": os.environ.get("CYBERLAB_TEST_IMAGE_MIRROR") or None,
        }
    )
    with TestClient(create_app(s)) as c:
        yield c


def provision(client, manifest, name):
    ws = client.post("/v1/workspaces", json={"name": name}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=manifest, headers=YAML | auth())
    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 201, r.text
    envs = r.json()["environments"]
    assert all(e["state"] == "running" for e in envs), envs
    return ws, envs


def destroy_all(client, ws, envs, keep=False):
    for e in envs:
        client.delete(
            f"/v1/workspaces/{ws}/environments/{e['id']}?keep_workspace={keep}", headers=auth()
        )
    client.delete(f"/v1/workspaces/{ws}", headers=auth())


def test_provider_connected(client):
    st = client.get("/v1/system/execution", headers=auth()).json()
    assert st["connected"] is True and st["status"] == "CONNECTED"


def test_minimal_environment_is_isolated_and_hardened(client):
    ws, (env,) = provision(client, BASIC, "it-minimal")
    name = env["resources"]["container"]
    try:
        assert sh(name, "id -u")[1] == "1000"
        assert sh(name, "whoami")[1] == "cyberlab"  # real user, not "I have no name!"
        assert "CapEff:\t0000000000000000" in sh(name, "cat /proc/self/status")[1]
        assert "NoNewPrivs:\t1" in sh(name, "cat /proc/self/status")[1]
        assert sh(name, "touch /etc/x")[0] != 0  # read-only root filesystem
        assert sh(name, "echo hi > /workspace/note.txt")[0] == 0
        # isolated: loopback only
        assert sh(name, "ls /sys/class/net")[1].split() == ["lo"]
        mem = docker("inspect", "--format", "{{.HostConfig.Memory}}", name).stdout.strip()
        assert mem == str(512 * 1024 * 1024)

        # stop/start keeps the workspace volume
        base = f"/v1/workspaces/{ws}/environments/{env['id']}"
        assert client.post(f"{base}/stop", headers=auth()).json()["state"] == "stopped"
        assert client.post(f"{base}/start", headers=auth()).json()["state"] == "running"
        assert sh(name, "cat /workspace/note.txt")[1] == "hi"
    finally:
        destroy_all(client, ws, [env])
    assert docker("inspect", name).returncode != 0  # container gone
    assert docker("volume", "inspect", env["resources"]["volume"]).returncode != 0


def test_lab_network_connects_peers_but_not_internet(client):
    ws, envs = provision(client, LAB, "it-lab")
    a = envs[0]["resources"]["container"]
    try:
        code, out = sh(a, "getent hosts beta")
        assert code == 0 and out, "peer should resolve on the lab network"
        assert sh(a, "getent hosts example.com")[0] != 0, "internet must not resolve"
        internal = docker(
            "network", "inspect", "--format", "{{.Internal}}", envs[0]["resources"]["network"]
        ).stdout.strip()
        assert internal == "true"
    finally:
        destroy_all(client, ws, envs)
    # destroying the last environment removes the lab network
    assert docker("network", "inspect", envs[0]["resources"]["network"]).returncode != 0


def _read_until(ws, needle: bytes, limit: int = 200) -> bytes:
    buf = b""
    for _ in range(limit):
        buf += ws.receive_bytes()
        if needle in buf:
            return buf
    raise AssertionError(f"{needle!r} not seen in {buf[-500:]!r}")


def test_browser_terminal_on_real_container(client):
    import json

    ws_id, (env,) = provision(client, BASIC, "it-terminal")
    try:
        r = client.post(f"/v1/workspaces/{ws_id}/environments/{env['id']}/terminal", headers=auth())
        assert r.status_code == 201, r.text
        with client.websocket_connect("/v1/terminal") as ws:
            ws.send_text(json.dumps({"type": "auth", "ticket": r.json()["ticket"]}))
            assert json.loads(ws.receive_text())["type"] == "ready"
            ws.send_text(json.dumps({"type": "resize", "rows": 33, "cols": 111}))
            ws.send_text(
                json.dumps(
                    {
                        "type": "input",
                        "data": "echo uid=$(id -u) size=$(stty size | tr ' ' x) pwd=$PWD; "
                        "echo persisted > /workspace/from-terminal.txt; echo DONE-$((40+2))\n",
                    }
                )
            )
            out = _read_until(ws, b"DONE-42")
            assert b"uid=1000" in out and b"size=33x111" in out and b"pwd=/workspace" in out
            ws.send_text(json.dumps({"type": "input", "data": "exit\n"}))
            for _ in range(200):  # skip remaining shell output (prompt etc.)
                msg = ws.receive()
                if msg.get("text"):
                    assert json.loads(msg["text"]) == {"type": "exit", "reason": "exit"}
                    break
            else:
                raise AssertionError("no exit message")
        name = env["resources"]["container"]
        assert sh(name, "cat /workspace/from-terminal.txt")[1] == "persisted"
    finally:
        destroy_all(client, ws_id, [env])


@pytest.mark.skipif(
    os.environ.get("CYBERLAB_DOCKER_BUILD_TESTS") != "1",
    reason="installs packages; needs internet access during image builds",
)
def test_tool_pack_is_installed(client):
    ws, (env,) = provision(client, MINIMAL, "it-crypto")  # crypto pack on debian-12
    name = env["resources"]["container"]
    try:
        assert env["toolPacksInstalled"] is True and env["image"].startswith("cyberlab-env:")
        for cmd in ["openssl version", "gpg --version", "age --version"]:
            code, out = sh(name, cmd)
            assert code == 0, (cmd, out)
        # the tools work for the unprivileged user inside the hardened container
        code, out = sh(
            name,
            "age-keygen -o /workspace/key.txt 2>/dev/null; "
            "grep -c AGE-SECRET-KEY /workspace/key.txt",
        )
        assert (code, out) == (0, "1")
    finally:
        destroy_all(client, ws, [env])
