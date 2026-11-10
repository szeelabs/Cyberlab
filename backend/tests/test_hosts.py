"""Docker host settings (SSH / TLS) and provider cost classes."""

import pytest
from pydantic import SecretStr, ValidationError

from cyberlab.config import Settings
from cyberlab.execution import CostClass, build_provider
from cyberlab.execution.docker import DockerAdapter, RunResult, docker_env

PROD = {"environment": "production", "auth_jwt_secret": SecretStr("x")}


@pytest.mark.parametrize(
    "host",
    [
        "ssh://cyberlab@lab.example.org",
        "unix:///var/run/docker.sock",
        "tcp://10.0.0.5:2376",
        "ssh://me@192.0.2.10:2222",
    ],
)
def test_valid_docker_hosts(host):
    Settings(docker_host=host)


@pytest.mark.parametrize("host", ["lab.example.org", "http://x:2375", "ftp://x", "-H evil"])
def test_invalid_docker_hosts(host):
    with pytest.raises(ValidationError, match="must start with"):
        Settings(docker_host=host)


def test_plain_tcp_refused_in_production():
    with pytest.raises(ValidationError, match="unencrypted tcp://"):
        Settings(docker_host="tcp://10.0.0.5:2375", **PROD)
    Settings(docker_host="ssh://cyberlab@lab", **PROD)
    Settings(
        docker_host="tcp://10.0.0.5:2376",
        docker_tls_verify=True,
        docker_cert_path="/etc/cyberlab/docker-certs",
        **PROD,
    )


def test_tls_needs_certificates():
    with pytest.raises(ValidationError, match="CERT_PATH"):
        Settings(docker_tls_verify=True)


def test_docker_env():
    env = docker_env("tcp://h:2376", tls_verify=True, cert_path="/certs")
    assert (env["DOCKER_HOST"], env["DOCKER_TLS_VERIFY"], env["DOCKER_CERT_PATH"]) == (
        "tcp://h:2376",
        "1",
        "/certs",
    )
    assert "DOCKER_TLS_VERIFY" not in docker_env("ssh://u@h")


def runner(args, timeout, input=None, binary=False):
    return RunResult(0, "29.0.0", "")


def test_paid_provider_refused_unless_allowed():
    paid = DockerAdapter(runner, cost_class=CostClass.paid)
    st = paid.status()
    assert not st.connected and "ALLOW_PAID_PROVIDERS" in st.detail and st.cost_class == "paid"
    ok = DockerAdapter(runner, cost_class=CostClass.paid, allow_paid=True).status()
    assert ok.connected and ok.cost_class == "paid"
    assert DockerAdapter(runner, cost_class=CostClass.free).status().connected


def test_build_provider_passes_host_settings(tmp_path):
    s = Settings(
        execution_provider="docker", docker_host="ssh://cyberlab@lab", execution_cost_class="paid"
    )
    adapter = build_provider(s)
    assert adapter._env["DOCKER_HOST"] == "ssh://cyberlab@lab"
    assert adapter.status().connected is False  # paid, not allowed: refused before any call


def test_paid_provider_blocks_provisioning(settings):
    from fastapi.testclient import TestClient

    from cyberlab.main import create_app

    from .conftest import auth
    from .test_environments_api import MINIMAL, YAML

    app = create_app(
        settings.model_copy(update={"execution_provider": "docker", "execution_cost_class": "paid"})
    )
    with TestClient(app) as c:
        st = c.get("/v1/system/execution", headers=auth()).json()
        assert st["status"] == "NOT CONNECTED" and st["costClass"] == "paid"
        ws = c.post("/v1/workspaces", json={"name": "p"}, headers=auth()).json()["id"]
        c.put(f"/v1/workspaces/{ws}/manifest", content=MINIMAL, headers=YAML | auth())
        r = c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth())
        assert r.status_code == 503 and "paid" in r.json()["detail"]["detail"]
