"""Full virtual machines (QEMU/KVM) and boot media from a link."""

import hashlib
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from cyberlab.api import vm as vm_api
from cyberlab.config import QuotaSettings, Settings
from cyberlab.execution import ProviderError, ProviderStatus, ProvisionRequest
from cyberlab.execution.base import CostClass
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.execution.images import DESKTOP_COMMAND, VM_COMMAND, ImageRecipeError, render
from cyberlab.main import create_app
from cyberlab.manifest.models import Environment, UniverseManifest
from cyberlab.manifest.policy import evaluate

from .conftest import auth
from .test_docker_adapter import FakeRunner, run_args
from .test_environments_api import YAML, FakeProvider


def vm_env(**over):
    return {
        "name": "pc",
        "baseImage": "vm-host",
        "resources": {"cpu": 2, "memoryMiB": 3072, "storageGiB": 24},
        "network": {"mode": "isolated"},
        "access": {"desktop": True},
        "vm": {"boot": "boot.iso", "diskGiB": 16},
    } | over


def codes(registry, **over):
    m = UniverseManifest.model_validate(
        {"apiVersion": "cyberlab.dev/v1alpha1", "kind": "Universe", "metadata": {"name": "v"},
         "spec": {"environments": [vm_env(**over)]}}
    )  # fmt: skip
    return {f.code: f for f in evaluate(m, registry, QuotaSettings()).findings}


def test_valid_vm_needs_only_approval(registry):
    f = codes(registry)
    assert set(f) == {"security.full-vm"} and f["security.full-vm"].severity == "approval"


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"vm": None}, "registry.vm"),  # vm-host without a vm block
        ({"baseImage": "ubuntu-24-04"}, "registry.vm"),  # vm block without vm-host
        ({"toolPacks": ["crypto"]}, "registry.vm"),
        ({"isolation": "microvm"}, "registry.vm"),
        ({"access": {"desktop": False}}, "access.desktop"),
        ({"resources": {"cpu": 2, "memoryMiB": 3072, "storageGiB": 10}}, "resources.storage"),
    ],
)
def test_invalid_vms_are_refused(registry, over, code):
    assert code in codes(registry, **over)


def test_boot_name_is_a_plain_file_name():
    for bad in ("../etc/passwd", "/abs.iso", "a b.iso", ".hidden"):
        with pytest.raises(ValueError):
            Environment.model_validate(vm_env(vm={"boot": bad}))


def test_vm_image_has_qemu_and_the_screen_connector():
    r = render("docker.io/library/debian:12-slim", [], vm=True, desktop=True)
    assert "qemu-system-x86" in r.dockerfile and "socat" in r.dockerfile
    assert VM_COMMAND in r.dockerfile and DESKTOP_COMMAND in r.dockerfile
    assert "xvfb" not in r.dockerfile  # no X desktop: the screen is the VM's own
    with pytest.raises(ImageRecipeError):
        render("docker.io/blackarchlinux/blackarch:latest", [], family="arch", vm=True)


def test_launcher_scripts_are_valid_shell():
    import subprocess
    from importlib import resources

    for name in ("vm.sh", "vm-desktop.sh"):
        text = resources.files("cyberlab.execution").joinpath(name).read_text()
        assert subprocess.run(["sh", "-n"], input=text, text=True).returncode == 0
    launcher = resources.files("cyberlab.execution").joinpath("vm.sh").read_text()
    assert "-enable-kvm" in launcher and "-sandbox on" in launcher and '-vnc "unix:' in launcher


# ---------------------------------------------------------------- the Docker adapter


def runner(kvm="993"):
    return FakeRunner(
        {
            "inspect --format": RunResult(0, "running", ""),
            "version --format": RunResult(0, "29.0.0 x86_64", ""),
            "run --rm": RunResult(0 if kvm else 1, kvm or "", "" if kvm else "no /dev/kvm"),
        }
    )


def req(**over):
    return ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=Environment.model_validate(vm_env(**over)),
        image="docker.io/library/debian:12-slim",
    )


def test_vm_runs_qemu_with_only_the_kvm_device_added():
    r = runner()
    DockerAdapter(r).provision(req(network={"mode": "lab"}))
    args = run_args(r)
    joined = " ".join(args)
    assert args[-1] == VM_COMMAND
    assert "--device /dev/kvm" in joined and "--group-add 993" in joined
    for flag in (
        "--cap-drop ALL",
        "--read-only",
        "--user 1000:1000",
        "--security-opt no-new-privileges",
    ):
        assert flag in joined
    assert "--privileged" not in joined
    assert "CYBERLAB_VM_MEMORY_MIB=2688" in joined and "CYBERLAB_VM_CPUS=2" in joined
    assert "CYBERLAB_VM_NETWORK=user" in joined and "CYBERLAB_VM_DISK_GIB=16" in joined


def test_isolated_vm_has_no_network():
    r = runner()
    DockerAdapter(r).provision(req())
    assert "CYBERLAB_VM_NETWORK=none" in " ".join(run_args(r))


def test_no_kvm_is_refused_clearly_and_reported():
    r = runner(kvm=None)
    a = DockerAdapter(r)
    with pytest.raises(ProviderError, match="no KVM"):
        a.provision(req())
    assert a.status().full_vm is False
    b = DockerAdapter(runner())
    b.kvm_group()
    assert b.status().full_vm is True


def test_status_never_waits_for_the_kvm_probe():
    """The first probe may download an image; status pages must not wait for it."""
    import threading

    gate = threading.Event()
    slow = runner()
    real = slow.__call__

    def call(args, timeout, input=None):
        if args[1:3] == ["run", "--rm"]:
            gate.wait(5)
        return real(args, timeout, input=input)

    a = DockerAdapter(call)
    started = time.monotonic()
    assert a.status().full_vm is False  # not known yet: answered at once
    assert time.monotonic() - started < 1
    gate.set()
    for _ in range(50):
        if a.status().full_vm:
            break
        time.sleep(0.05)
    assert a.status().full_vm is True  # known once the background check finished


def test_kvm_probe_is_cached():
    r = runner()
    a = DockerAdapter(r)
    a.kvm_group()
    a.kvm_group()
    assert len([c for c in r.calls if c[1:3] == ["run", "--rm"]]) == 1


# ---------------------------------------------------------------- links


def resolver(addr):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port))]


@pytest.mark.parametrize(
    ("url", "addr", "message"),
    [
        ("http://example.org/a.iso", "93.184.215.14", "https"),
        ("ftp://example.org/a.iso", "93.184.215.14", "https"),
        ("https://user:pw@example.org/a.iso", "93.184.215.14", "user name"),
        ("https://intranet.example/a.iso", "10.0.0.5", "private"),
        ("https://metadata.example/a.iso", "169.254.169.254", "private"),
        ("https://localhost/a.iso", "127.0.0.1", "private"),
    ],
)
def test_unsafe_links_are_refused(url, addr, message):
    with pytest.raises(vm_api.UnsafeLink, match=message):
        vm_api.check_link(url, resolve=resolver(addr))


def test_public_https_link_is_accepted():
    assert (
        vm_api.check_link("https://example.org/x.iso", resolve=resolver("93.184.215.14"))
        == "example.org"
    )


def transport(routes):
    def handler(request):
        status, headers, body = routes[str(request.url)]
        return httpx.Response(status, headers=headers, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_follows_checked_redirects_and_hashes(tmp_path):
    data = b"ISO" * 1000
    c = transport(
        {
            "https://localhost/a.iso": (302, {"location": "https://localhost/mirror/a.iso"}, b""),
            "https://localhost/mirror/a.iso": (200, {"content-length": str(len(data))}, data),
        }
    )
    seen = []
    digest = vm_api.download(
        "https://localhost/a.iso",
        str(tmp_path / "f"),
        10**6,
        lambda d, t: seen.append((d, t)),
        allow_private=True,
        client=c,
    )
    assert digest == hashlib.sha256(data).hexdigest() and (tmp_path / "f").read_bytes() == data
    assert seen[-1] == (len(data), len(data))


def test_redirect_to_a_private_address_is_refused(tmp_path):
    c = transport({"https://example.org/a.iso": (302, {"location": "https://10.0.0.1/x"}, b"")})
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda h, *a, **k: (
        resolver("93.184.215.14")(h, 443) if h == "example.org" else real(h, *a, **k)
    )
    try:
        with pytest.raises(vm_api.UnsafeLink, match="private"):
            vm_api.download(
                "https://example.org/a.iso", str(tmp_path / "f"), 10**6, lambda *_: None, client=c
            )
    finally:
        socket.getaddrinfo = real


def test_download_size_cap(tmp_path):
    big = b"x" * 5000
    c = transport({"https://localhost/a": (200, {}, big)})
    with pytest.raises(ProviderError, match="larger than the limit"):
        vm_api.download(
            "https://localhost/a",
            str(tmp_path / "f"),
            1000,
            lambda *_: None,
            allow_private=True,
            client=c,
        )
    c = transport({"https://localhost/b": (200, {"content-length": "999999"}, b"")})
    with pytest.raises(ProviderError, match="larger than the limit"):
        vm_api.download(
            "https://localhost/b",
            str(tmp_path / "f"),
            1000,
            lambda *_: None,
            allow_private=True,
            client=c,
        )


# ---------------------------------------------------------------- the API


class VmProvider(FakeProvider):
    def __init__(self, full_vm=True):
        super().__init__()
        self.full_vm = full_vm
        self.written = {}
        self.renamed = []

    def status(self):
        return ProviderStatus(
            "fake", True, "ok", CostClass.free, "amd64", ("container",), self.full_vm
        )

    def write_file_from_path(self, ref, path, source):
        self.written[path] = open(source, "rb").read()

    def rename_file(self, ref, source, target):
        self.renamed.append((source, target))


VM_MANIFEST = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: vm}
spec:
  environments:
    - name: pc
      baseImage: vm-host
      resources: {cpu: 2, memoryMiB: 3072, storageGiB: 24}
      network: {mode: isolated}
      access: {desktop: true}
      vm: {boot: boot.iso, diskGiB: 16}
"""


def make(c, approve=True):
    ws = c.post("/v1/workspaces", json={"name": "vm"}, headers=auth()).json()["id"]
    c.put(f"/v1/workspaces/{ws}/manifest", content=VM_MANIFEST, headers=YAML | auth())
    if approve:
        c.post(
            f"/v1/workspaces/{ws}/approvals",
            json={"revision": 1, "approve": ["security.full-vm"]},
            headers=auth(),
        )
    return ws


def test_vm_needs_approval_and_kvm(settings):
    app = create_app(settings)
    app.state.provider = VmProvider(full_vm=False)
    with TestClient(app) as c:
        assert c.get("/v1/system/execution", headers=auth()).json()["fullVm"] is False
        ws = make(c, approve=False)
        r = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
        assert r.status_code == 409 and "security.full-vm" in r.text
        c.post(
            f"/v1/workspaces/{ws}/approvals",
            json={"revision": 1, "approve": ["security.full-vm"]},
            headers=auth(),
        )
        r = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
        assert r.status_code == 409 and "no KVM" in r.text


def test_media_from_a_link_lands_in_the_machine(settings, monkeypatch):
    data = b"\x00ISO" * 100

    def fake_download(url, dest, max_bytes, progress, allow_private=False, client=None):
        open(dest, "wb").write(data)
        progress(len(data), len(data))
        return hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(vm_api, "download", fake_download)
    monkeypatch.setattr(vm_api, "check_link", lambda url, allow_private=False: "example.org")
    app = create_app(settings)
    app.state.provider = p = VmProvider()
    with TestClient(app) as c:
        ws = make(c)
        (env,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
            "environments"
        ]
        assert env["vm"] == {"boot": "boot.iso", "diskGiB": 16}
        base = f"/v1/workspaces/{ws}/environments/{env['id']}/vm/media"
        assert c.get(base, headers=auth()).json()["state"] == "none"
        r = c.post(
            base,
            json={"url": "https://example.org/a.iso", "sha256": hashlib.sha256(data).hexdigest()},
            headers=auth(),
        )
        assert r.status_code == 202
        for _ in range(50):
            st = c.get(base, headers=auth()).json()
            if st["state"] in ("ready", "failed"):
                break
            time.sleep(0.05)
        assert st["state"] == "ready" and st["bytes"] == len(data), st
        assert p.written["/workspace/boot.iso.part"] == data
        assert p.renamed == [("/workspace/boot.iso.part", "/workspace/boot.iso")]
        actions = [e["action"] for e in c.get("/v1/audit", headers=auth()).json()]
        assert {"vm.media.start", "vm.media.finish"} <= set(actions)
        # a wrong checksum fails without publishing the file
        p.renamed.clear()
        c.post(base, json={"url": "https://example.org/a.iso", "sha256": "0" * 64}, headers=auth())
        for _ in range(50):
            st = c.get(base, headers=auth()).json()
            if st["state"] in ("ready", "failed"):
                break
            time.sleep(0.05)
        assert st["state"] == "failed" and "SHA-256" in st["error"] and p.renamed == []


def test_media_refuses_unsafe_links_and_non_vms(settings):
    app = create_app(settings)
    app.state.provider = VmProvider()
    with TestClient(app) as c:
        ws = make(c)
        (env,) = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
            "environments"
        ]
        base = f"/v1/workspaces/{ws}/environments/{env['id']}/vm/media"
        r = c.post(base, json={"url": "http://example.org/a.iso"}, headers=auth())
        assert r.status_code == 422 and "https" in r.text
        r = c.post(base, json={"url": "https://127.0.0.1/a.iso"}, headers=auth())
        assert r.status_code == 422 and "private" in r.text
        assert (
            c.post(
                base, json={"url": "https://example.org/a.iso"}, headers=auth("user-b")
            ).status_code
            == 404
        )


def test_private_downloads_are_refused_in_production():
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            auth_mode="jwt",
            auth_jwt_secret="x" * 40,
            vm_media_allow_private=True,
        )


def test_vm_memory_minimum_has_no_desktop_overhead(registry):
    """The VM's screen comes from QEMU, so the X-desktop memory isn't added."""
    small = {"cpu": 1, "memoryMiB": 1536, "storageGiB": 8}
    assert set(codes(registry, resources=small, vm={"boot": "boot.iso", "diskGiB": 2})) == {
        "security.full-vm"
    }
