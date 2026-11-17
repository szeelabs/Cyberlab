import pytest

from cyberlab.execution import ProviderError
from cyberlab.execution.docker import INSTALL_DOCKERFILE, FileOpError

from .conftest import auth
from .test_environments_api import MINIMAL, YAML, FakeProvider


class InstallProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.installs = []
        self.destroyed = []
        self.fail_build = None
        self.fail_recreate = False

    def install_package(self, ref, path, max_bytes):
        self.installs.append((ref, path))
        if self.fail_build:
            raise self.fail_build
        return {"image": "cyberlab-env:abcdef0123456789", "sha256": "f" * 64, "bytes": 1234}

    def destroy(self, ref, *, remove_workspace_volume):
        self.destroyed.append((ref, remove_workspace_volume))
        return super().destroy(ref, remove_workspace_volume=remove_workspace_volume)

    def provision(self, request):
        if self.fail_recreate and request.image_override:
            raise ProviderError("docker run failed: boom")
        return super().provision(request)


def setup(client, manifest=MINIMAL):
    p = InstallProvider()
    client.app.state.provider = p
    ws = client.post("/v1/workspaces", json={"name": "i"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=manifest, headers=YAML | auth())
    (env,) = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()[
        "environments"
    ]
    return p, f"/v1/workspaces/{ws}/environments/{env['id']}", env


def test_install_rebuilds_and_recreates_keeping_files(client):
    p, base, env = setup(client)
    old_ref = next(iter(p.states))
    r = client.post(f"{base}/install", json={"path": "dl/PacketTracer.deb"}, headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert p.installs == [(old_ref, "/workspace/dl/PacketTracer.deb")]
    assert p.destroyed == [(old_ref, False)]  # the workspace volume is kept
    assert p.requests[-1].image_override == "cyberlab-env:abcdef0123456789"
    assert body["installed"] == [{"path": "/workspace/dl/PacketTracer.deb", "sha256": "f" * 64}]
    assert body["image"] == "cyberlab-env:abcdef0123456789"
    audit = client.get("/v1/audit", headers=auth()).json()
    ev = next(e for e in audit if e["action"] == "environment.install")
    assert ev["detail"]["sha256"] == "f" * 64 and ev["detail"]["bytes"] == 1234


@pytest.mark.parametrize("path", ["notes.txt", "../etc/x.deb", "/abs.deb"])
def test_install_refuses_bad_paths(client, path):
    p, base, _ = setup(client)
    r = client.post(f"{base}/install", json={"path": path}, headers=auth())
    assert r.status_code in (400, 422)
    assert p.installs == []


def test_install_refused_on_blackarch(client):
    arch = (
        MINIMAL.replace("baseImage: debian-12", "baseImage: blackarch")
        .replace(
            "cpu: 0.5, memoryMiB: 512, storageGiB: 2", "cpu: 1, memoryMiB: 1024, storageGiB: 4"
        )
        .replace("toolPacks: [crypto]", "toolPacks: []")
    )
    p, base, _ = setup(client, arch)
    r = client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth())
    assert r.status_code == 409 and "Ubuntu, Debian, Kali and Parrot" in r.json()["detail"]
    assert p.installs == []


def test_install_refused_with_allow_listed_websites(client):
    egress = MINIMAL.replace(
        "network: {mode: isolated}",
        "network: {mode: restricted-egress, allowedEgress: [example.org]}",
    )
    client.app.state.provider = InstallProvider()
    ws = client.post("/v1/workspaces", json={"name": "e"}, headers=auth()).json()["id"]
    client.put(f"/v1/workspaces/{ws}/manifest", content=egress, headers=YAML | auth())
    envs = client.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth()).json()
    if "environments" not in envs:  # no egress proxy in this fake: provisioning fails first
        pytest.skip("restricted-egress needs the egress proxy")
    env = envs["environments"][0]
    r = client.post(
        f"/v1/workspaces/{ws}/environments/{env['id']}/install",
        json={"path": "a.deb"},
        headers=auth(),
    )
    assert r.status_code == 409


def test_install_build_failure_leaves_machine_untouched(client):
    p, base, _ = setup(client)
    p.fail_build = ProviderError("installing the package failed: dependency problems")
    r = client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth())
    assert r.status_code == 502 and "dependency problems" in r.json()["detail"]
    assert p.destroyed == []  # nothing was recreated
    envs = client.get(base.rsplit("/", 1)[0], headers=auth()).json()
    assert envs[0]["state"] == "running" and "dependency" in envs[0]["lastError"]


def test_install_not_a_deb_and_too_large(client):
    p, base, _ = setup(client)
    p.fail_build = FileOpError("invalid", "this file is not a .deb package")
    assert client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth()).status_code == 422
    p.fail_build = FileOpError("too_large", "too big")
    assert client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth()).status_code == 413


def test_install_recreate_failure_is_reported_as_failed(client):
    p, base, _ = setup(client)
    p.fail_recreate = True
    r = client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth())
    assert r.status_code == 502
    envs = client.get(base.rsplit("/", 1)[0], headers=auth()).json()
    assert envs[0]["state"] == "failed" and "recreating" in envs[0]["lastError"]


def test_install_requires_owner_and_a_machine(client):
    p, base, _ = setup(client)
    assert (
        client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth("eve")).status_code
        == 404
    )
    client.delete(base, headers=auth())
    assert client.post(f"{base}/install", json={"path": "a.deb"}, headers=auth()).status_code == 409


def test_install_dockerfile_switches_back_to_the_unprivileged_user():
    df = INSTALL_DOCKERFILE.format(base="cyberlab-env:1234", user="1000:1000", sha="ab")
    lines = df.strip().splitlines()
    assert lines[0] == "FROM cyberlab-env:1234"
    assert "USER 0:0" in lines and lines[-2] == "USER 1000:1000"
    assert "COPY package.deb" in df  # the only file in the build context
