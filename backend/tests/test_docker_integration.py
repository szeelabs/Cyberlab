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
    ws, (env,) = provision(client, MINIMAL, "it-minimal")
    name = env["resources"]["container"]
    try:
        assert sh(name, "id -u")[1] == "1000"
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
