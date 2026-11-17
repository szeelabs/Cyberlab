import pytest

from cyberlab.execution import CostClass, ProviderStatus
from cyberlab.execution.base import normalize_arch
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.troubleshoot import explain_error

from .conftest import auth
from .test_docker_adapter import FakeRunner
from .test_environments_api import YAML, FakeProvider

BLACKARCH = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: ba}
spec:
  environments:
    - name: box
      baseImage: blackarch
      resources: {cpu: 1, memoryMiB: 1024, storageGiB: 4}
      network: {mode: isolated}
"""


class ArmProvider(FakeProvider):
    def __init__(self, arch):
        super().__init__()
        self.arch = arch

    def status(self):
        return ProviderStatus("fake", True, "ok", CostClass.free, self.arch)


def test_registry_declares_processor_types(registry):
    assert registry.base_images["blackarch"].architectures == ["amd64"]
    for base in ["ubuntu-24-04", "debian-12", "kali-rolling", "parrot-core"]:
        assert registry.base_images[base].architectures == ["amd64", "arm64"]


@pytest.mark.parametrize(
    "raw, arch",
    [
        ("amd64", "amd64"),
        ("x86_64", "amd64"),
        ("aarch64", "arm64"),
        ("ARM64", "arm64"),
        ("riscv64", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_arch(raw, arch):
    assert normalize_arch(raw) == arch


@pytest.mark.parametrize(
    "out, arch",
    [
        ("29.0.1 arm64", "arm64"),
        ("29.0.1 amd64", "amd64"),
        ("29.0.1 sparc", "unknown"),
        ("29.0.1", "unknown"),
    ],
)
def test_docker_status_reports_architecture(out, arch):
    st = DockerAdapter(FakeRunner({"version --format": RunResult(0, out, "")})).status()
    assert st.connected and st.architecture == arch and st.detail == "Docker Engine 29.0.1"


def _provision(client, provider, manifest):
    client.app.state.provider = provider
    ws = client.post("/v1/workspaces", json={"name": "a"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=manifest, headers=YAML | auth())
    return client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())


def test_arm_server_refuses_x86_only_system_before_building(client):
    provider = ArmProvider("arm64")
    r = _provision(client, provider, BLACKARCH)
    assert r.status_code == 409
    assert "BlackArch Linux doesn't run on this server's processor type, ARM" in r.text
    assert provider.requests == []  # nothing was attempted


def test_x86_server_runs_it(client):
    r = _provision(client, ArmProvider("amd64"), BLACKARCH)
    assert r.status_code == 201, r.text


def test_unknown_processor_type_does_not_block(client):
    assert _provision(client, ArmProvider("unknown"), BLACKARCH).status_code == 201


def test_execution_status_shows_architecture(client):
    client.app.state.provider = ArmProvider("arm64")
    assert client.get("/v1/system/execution", headers=auth()).json()["architecture"] == "arm64"


@pytest.mark.parametrize(
    "error",
    [
        "no matching manifest for linux/arm64/v8 in the manifest list entries",
        "exec /bin/sh: exec format error",
    ],
)
def test_troubleshooter_explains_wrong_processor(error):
    a = explain_error(error)
    assert a.code == "build.wrong-processor" and "BlackArch needs x86" in a.fix
