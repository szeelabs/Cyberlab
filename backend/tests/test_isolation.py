"""Stronger isolation levels: gVisor sandboxes and Kata microVMs."""

import json

import pytest
from fastapi.testclient import TestClient

from cyberlab.architect.core import draft_schema
from cyberlab.config import QuotaSettings, Settings
from cyberlab.execution import EnvState, ProviderError, ProviderStatus, ProvisionRequest
from cyberlab.execution.base import CostClass
from cyberlab.execution.docker import DockerAdapter, RunResult
from cyberlab.main import create_app
from cyberlab.manifest.models import Environment, UniverseManifest
from cyberlab.manifest.policy import evaluate

from .conftest import auth
from .test_docker_adapter import FakeRunner, run_args
from .test_environments_api import YAML, FakeProvider


def machine(**over):
    return {
        "name": "box",
        "baseImage": "ubuntu-24-04",
        "resources": {"cpu": 1, "memoryMiB": 1024, "storageGiB": 6},
        "network": {"mode": "isolated"},
    } | over


def findings(registry, **over):
    m = UniverseManifest.model_validate(
        {
            "apiVersion": "cyberlab.dev/v1alpha1",
            "kind": "Universe",
            "metadata": {"name": "m"},
            "spec": {"environments": [machine(**over)]},
        }
    )
    return {f.code: f for f in evaluate(m, registry, QuotaSettings()).findings}


def test_default_is_container():
    assert Environment.model_validate(machine()).isolation == "container"


def test_malware_pack_only_in_a_microvm_without_network(registry):
    f = findings(registry, toolPacks=["malware-analysis"])
    assert "security.isolation-level" in f and "microvm" in f["security.isolation-level"].message
    assert "security.isolation-level" in findings(
        registry, toolPacks=["malware-analysis"], isolation="sandbox"
    )
    f = findings(
        registry, toolPacks=["malware-analysis"], isolation="microvm", network={"mode": "lab"}
    )
    assert f["security.isolation"].severity == "error"
    f = findings(registry, toolPacks=["malware-analysis"], isolation="microvm")
    assert set(f) == {"security.high-risk-pack"}  # allowed, after the user's approval
    assert f["security.high-risk-pack"].severity == "approval"


def test_ai_helper_never_drafts_the_malware_pack(registry):
    schema = draft_schema(registry)
    packs = schema["properties"]["environments"]["items"]["properties"]["toolPacks"]["items"][
        "enum"
    ]
    assert "malware-analysis" not in packs


# ---------------------------------------------------------------- the Docker adapter

RUNTIMES = json.dumps({"runc": {}, "io.containerd.runc.v2": {}, "runsc": {}, "kata": {}})


def runner(runtimes=RUNTIMES):
    return FakeRunner(
        {
            "inspect --format": RunResult(0, "running", ""),
            "info --format": RunResult(0, runtimes, ""),
            "version --format": RunResult(0, "29.0.0 x86_64", ""),
        }
    )


def req(isolation):
    return ProvisionRequest(
        workspace_key="ab12cd34ef56",
        environment=Environment.model_validate(machine(isolation=isolation)),
        image="docker.io/library/ubuntu:24.04",
    )


def test_status_reports_the_installed_runtimes():
    assert DockerAdapter(runner()).status().isolation == ("container", "sandbox", "microvm")
    only_gvisor = json.dumps({"runc": {}, "runsc": {}})
    assert DockerAdapter(runner(only_gvisor)).status().isolation == ("container", "sandbox")
    assert DockerAdapter(runner("garbage")).status().isolation == ("container",)
    custom = DockerAdapter(runner(json.dumps({"kata-qemu": {}})), microvm_runtime="kata-qemu")
    assert custom.status().isolation == ("container", "microvm")


@pytest.mark.parametrize(("level", "runtime"), [("sandbox", "runsc"), ("microvm", "kata")])
def test_stronger_isolation_uses_its_runtime_and_keeps_the_hardening(level, runtime):
    r = runner()
    DockerAdapter(r).provision(req(level))
    args = run_args(r)
    joined = " ".join(args)
    assert f"--runtime {runtime}" in joined
    for flag in (
        "--cap-drop ALL",
        "--read-only",
        "--user 1000:1000",
        "--security-opt no-new-privileges",
    ):
        assert flag in joined


def test_container_isolation_uses_the_default_runtime():
    r = runner()
    DockerAdapter(r).provision(req("container"))
    assert "--runtime" not in run_args(r)


def test_missing_runtime_is_refused_with_a_clear_message():
    r = runner(json.dumps({"runc": {}}))
    with pytest.raises(ProviderError, match="Kata Containers"):
        DockerAdapter(r).provision(req("microvm"))
    assert not [c for c in r.find("run") if "--detach" in c]  # nothing was started


def test_runtime_names_are_validated():
    with pytest.raises(ProviderError):
        DockerAdapter(FakeRunner(), microvm_runtime="kata --privileged")
    with pytest.raises(ValueError):
        Settings(microvm_runtime="kata;rm -rf /")


# ---------------------------------------------------------------- the API


class LevelProvider(FakeProvider):
    def __init__(self, levels):
        super().__init__()
        self.levels = levels

    def status(self):
        return ProviderStatus("fake", True, "ok", CostClass.free, "amd64", self.levels)


MICROVM = """apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {name: vm}
spec:
  environments:
    - name: box
      baseImage: ubuntu-24-04
      resources: {cpu: 1, memoryMiB: 1024, storageGiB: 4}
      network: {mode: isolated}
      isolation: microvm
"""


@pytest.mark.parametrize(
    ("levels", "ok"), [(("container",), False), (("container", "microvm"), True)]
)
def test_api_checks_the_server_can_run_the_level(settings, levels, ok):
    app = create_app(settings)
    app.state.provider = LevelProvider(levels)
    with TestClient(app) as c:
        assert c.get("/v1/system/execution", headers=auth()).json()["isolation"] == list(levels)
        ws = c.post("/v1/workspaces", json={"name": "vm"}, headers=auth()).json()["id"]
        c.put(f"/v1/workspaces/{ws}/manifest", content=MICROVM, headers=YAML | auth())
        r = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
        if ok:
            assert r.status_code == 201
            assert app.state.provider.states and all(
                s == EnvState.running for s in app.state.provider.states.values()
            )
        else:
            assert r.status_code == 409 and "microVMs (Kata Containers)" in r.text
            assert not app.state.provider.states
