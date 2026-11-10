"""End-to-end tests against a real Docker Engine.

Run with: CYBERLAB_DOCKER_TESTS=1 pytest tests/test_docker_integration.py
Optional: CYBERLAB_TEST_IMAGE_MIRROR=mirror.gcr.io (avoids Docker Hub rate limits).
Optional: CYBERLAB_TEST_DOCKER_HOST=ssh://user@host runs every test through the SSH transport
(the test helpers themselves use the local docker CLI, so it must be the same engine).
"""

import os
import subprocess
import time

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


def sh(container, cmd, interpreter="sh"):
    p = subprocess.run(
        ["docker", "exec", container, interpreter, "-c", cmd],
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
            # e.g. ssh://runner@localhost: run every test through CyberLab's SSH transport
            "docker_host": os.environ.get("CYBERLAB_TEST_DOCKER_HOST") or None,
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


EGRESS = """
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: egress}
spec:
  environments:
    - name: scanner
      baseImage: debian-12
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
      network: {mode: restricted-egress, allowedEgress: [allowed.example]}
"""


# bash-only HTTP request through the proxy (the base image has no curl).
def bash(container, cmd):
    return sh(container, cmd, interpreter="bash")


def proxy_get(host: str, with_auth: bool = True) -> str:
    auth_line = "Proxy-Authorization: Basic %s\\r\\n" if with_auth else "%.0s"
    return _PROXY_GET.replace("HOST", host).replace("AUTH", auth_line)


_PROXY_GET = (
    "exec 3<>/dev/tcp/egress-proxy/3128 || exit 9; "
    'cred=$(printf %s "$(printf %s "${HTTP_PROXY#http://}" | cut -d@ -f1)" | base64 -w0); '
    "printf 'GET http://HOST/ HTTP/1.0\\r\\nHost: HOST\\r\\nAUTH\\r\\n' \"$cred\" >&3; "
    "head -1 <&3"
)


def test_restricted_egress_through_proxy(settings):
    s = settings.model_copy(
        update={
            "execution_provider": "docker",
            "image_mirror": os.environ.get("CYBERLAB_TEST_IMAGE_MIRROR") or None,
            # e.g. ssh://runner@localhost: run every test through CyberLab's SSH transport
            "docker_host": os.environ.get("CYBERLAB_TEST_DOCKER_HOST") or None,
            "egress_allow_private": True,  # the fake "internet" below uses private addresses
        }
    )
    mirror = os.environ.get("CYBERLAB_TEST_IMAGE_MIRROR")
    py = f"{mirror}/library/python:3.13-slim" if mirror else "python:3.13-slim"
    with TestClient(create_app(s)) as c:
        ws, (env,) = provision(c, EGRESS, "it-egress")
        name = env["resources"]["container"]
        key = name.removeprefix("cyberlab-").removesuffix("-scanner")
        sites = []
        try:
            for host in ("allowed.example", "blocked.example"):
                site = f"cyberlab-test-site-{host.split('.')[0]}-{key}"
                r = docker(
                    "run",
                    "-d",
                    "--name",
                    site,
                    "--network",
                    f"cyberlab-{key}-egress",
                    "--network-alias",
                    host,
                    py,
                    "python",
                    "-m",
                    "http.server",
                    "80",
                )
                assert r.returncode == 0, r.stderr
                sites.append(site)
            for _ in range(30):  # wait for the fake sites to listen
                if (
                    "200"
                    in bash(
                        name,
                        proxy_get("allowed.example"),
                    )[1]
                ):
                    break
                time.sleep(0.5)

            ok = bash(
                name,
                proxy_get("allowed.example"),
            )[1]
            assert ok.startswith("HTTP/1.0 200"), ok
            denied = bash(
                name,
                proxy_get("blocked.example"),
            )[1]
            assert denied.startswith("HTTP/1.1 403"), denied
            no_auth = bash(name, proxy_get("allowed.example", with_auth=False))[1]
            assert no_auth.startswith("HTTP/1.1 407"), no_auth
            # no way around the proxy: direct connections fail
            direct = sh(
                name,
                "timeout 5 bash -c 'exec 3<>/dev/tcp/allowed.example/80' "
                "&& echo CONNECTED || echo BLOCKED",
            )[1]
            assert "BLOCKED" in direct and "CONNECTED" not in direct, direct

            log = c.get(f"/v1/workspaces/{ws}/egress-log", headers=auth()).json()
            decisions = {(e["decision"], e.get("host"), e.get("reason")) for e in log}
            assert ("allow", "allowed.example", None) in decisions
            assert ("deny", "blocked.example", "not-allowed") in decisions
            assert ("deny", None, "auth") in decisions
        finally:
            for site in sites:
                docker("rm", "-f", site)
            destroy_all(c, ws, [env])
    assert docker("inspect", f"cyberlab-{key}-egress-proxy").returncode != 0
    assert docker("network", "inspect", f"cyberlab-{key}-egress").returncode != 0


def test_workspace_files_on_real_container(client):
    ws, (env,) = provision(client, BASIC, "it-files")
    name = env["resources"]["container"]
    base = f"/v1/workspaces/{ws}/environments/{env['id']}/files"
    blob = bytes(range(256)) * 64  # binary data, 16 KiB
    odd = "odd name; $(touch pwned) & co.txt"
    try:
        r = client.put(
            f"{base}/content",
            params={"path": "captures/day1/data.bin"},
            content=blob,
            headers=auth(),
        )
        assert r.status_code == 201, r.text
        # written by the environment's own user, not root
        assert sh(name, "stat -c %U:%G /workspace/captures/day1/data.bin")[1] == "cyberlab:cyberlab"
        r = client.put(f"{base}/content", params={"path": odd}, content=b"hi", headers=auth())
        assert r.status_code == 201
        assert sh(name, "ls /workspace")[1].splitlines() == ["captures", odd]  # no "pwned"

        root = client.get(base, headers=auth()).json()["entries"]
        assert [(e["name"], e["type"]) for e in root] == [("captures", "directory"), (odd, "file")]
        nested = client.get(base, params={"path": "captures/day1"}, headers=auth()).json()
        assert nested["entries"][0]["size"] == len(blob)

        dl = client.get(
            f"{base}/content", params={"path": "captures/day1/data.bin"}, headers=auth()
        )
        assert dl.status_code == 200 and dl.content == blob

        # a file created in the terminal is visible through the API too
        sh(name, "echo from-terminal > /workspace/t.txt")
        assert (
            client.get(f"{base}/content", params={"path": "t.txt"}, headers=auth()).content
            == b"from-terminal\n"
        )

        assert (
            client.get(f"{base}/content", params={"path": "captures"}, headers=auth()).status_code
            == 409
        )  # a directory
        assert client.get(base, params={"path": "missing"}, headers=auth()).status_code == 404
        assert (
            client.delete(base, params={"path": "captures"}, headers=auth()).status_code == 409
        )  # needs recursive
        assert (
            client.delete(
                base, params={"path": "captures", "recursive": True}, headers=auth()
            ).status_code
            == 204
        )
        assert sh(name, "test -e /workspace/captures && echo yes || echo no")[1] == "no"

        client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/stop", headers=auth())
        assert client.get(base, headers=auth()).status_code == 409
    finally:
        destroy_all(client, ws, [env])


def test_snapshot_and_restore_on_real_container(client, tmp_path):
    import io
    import tarfile

    ws, (env,) = provision(client, BASIC, "it-snap")
    name, vol = env["resources"]["container"], env["resources"]["volume"]
    envbase = f"/v1/workspaces/{ws}/environments/{env['id']}"
    try:
        sh(
            name,
            "mkdir -p /workspace/notes && echo v1 > /workspace/notes/a.txt "
            "&& printf 'bin' > /workspace/b.bin",
        )
        r = client.post(f"{envbase}/snapshots", json={"label": "v1"}, headers=auth())
        assert r.status_code == 201, r.text
        snap = r.json()
        assert snap["sizeBytes"] > 0

        sh(
            name,
            "echo v2 > /workspace/notes/a.txt && rm /workspace/b.bin && touch /workspace/new.txt",
        )
        r = client.post(f"{envbase}/restore", json={"snapshotId": snap["id"]}, headers=auth())
        assert r.status_code == 409  # still running

        client.post(f"{envbase}/stop", headers=auth())
        r = client.post(f"{envbase}/restore", json={"snapshotId": snap["id"]}, headers=auth())
        assert r.status_code == 200, r.text
        client.post(f"{envbase}/start", headers=auth())
        assert sh(name, "cat /workspace/notes/a.txt")[1] == "v1"
        assert sh(name, "cat /workspace/b.bin")[1] == "bin"
        assert sh(name, "ls -A /workspace")[1].split() == ["b.bin", "notes"]  # new.txt gone
        assert sh(name, "stat -c %U /workspace/notes/a.txt")[1] == "cyberlab"

        # Hostile and corrupt archives change nothing and write nothing outside.
        adapter = client.app.state.provider
        image = env["resources"]["image"]
        client.post(f"{envbase}/stop", headers=auth())
        corrupt = tmp_path / "corrupt.tar.gz"
        corrupt.write_bytes(b"\x1f\x8b not really gzip")
        with pytest.raises(Exception, match="restore failed"):
            adapter.import_workspace(vol, image, str(corrupt))
        crafted = tmp_path / "crafted.tar.gz"
        with tarfile.open(crafted, "w:gz") as tar:
            for member in ["../escape.txt", "/etc/evil.txt", "ok.txt"]:
                data = b"x"
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        # GNU tar refuses the '..' member and exits non-zero, so the whole restore is refused.
        with pytest.raises(Exception, match="restore failed"):
            adapter.import_workspace(vol, image, str(crafted))
        client.post(f"{envbase}/start", headers=auth())
        listing = sh(name, "ls -A /workspace")[1].split()
        assert "escape.txt" not in listing
        assert sh(name, "test -e /escape.txt && echo yes || echo no")[1] == "no"
        assert sh(name, "test -e /etc/evil.txt && echo yes || echo no")[1] == "no"
        assert "ok.txt" not in listing  # nothing from the refused archive was applied
        assert sh(name, "cat /workspace/notes/a.txt")[1] == "v1"  # workspace intact
        assert ".cyberlab-restore" not in listing

        r = client.delete(f"/v1/workspaces/{ws}/snapshots/{snap['id']}", headers=auth())
        assert r.status_code == 204
    finally:
        destroy_all(client, ws, [env])
