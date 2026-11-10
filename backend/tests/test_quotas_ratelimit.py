import pytest
from fastapi.testclient import TestClient

from cyberlab.config import QuotaSettings
from cyberlab.main import create_app
from cyberlab.ratelimit import RateLimiter, client_key

from .conftest import auth
from .test_environments_api import YAML, FakeProvider


def manifest(name, cpu=1.0, mem=1024, disk=2, count=1):
    envs = "\n".join(
        f"    - {{name: e{i}, baseImage: debian-12, resources: "
        f"{{cpu: {cpu}, memoryMiB: {mem}, storageGiB: {disk}}}}}"
        for i in range(count)
    )
    return f"""
apiVersion: cyberlab.dev/v1alpha1
kind: Universe
metadata: {{name: {name}}}
spec:
  environments:
{envs}
"""


@pytest.fixture
def client(settings):
    s = settings.model_copy(
        update={
            "quotas": QuotaSettings(
                max_environments=4,
                max_total_cpu=3,
                max_total_memory_mib=4096,
                max_total_storage_gib=12,
            )
        }
    )
    app = create_app(s)
    app.state.provider = FakeProvider()
    with TestClient(app) as c:
        yield c


def provision(c, name, user="user-a", **kw):
    ws = c.post("/v1/workspaces", json={"name": name}, headers=auth(user)).json()["id"]
    r = c.put(
        f"/v1/workspaces/{ws}/manifest", content=manifest(name, **kw), headers=YAML | auth(user)
    )
    assert r.status_code == 200, r.text
    return ws, c.post(f"/v1/workspaces/{ws}/environments", json={}, headers=auth(user))


# ---------------------------------------------------------------- live quotas
def test_quota_counts_across_workspaces(client):
    _, r = provision(client, "one", cpu=2)
    assert r.status_code == 201
    _, r = provision(client, "two", cpu=2)  # each manifest is fine alone; together, too much
    assert r.status_code == 409
    assert any(p.startswith("cpu: 2 in use + 2 requested") for p in r.json()["detail"]["problems"])
    usage = client.get("/v1/me/usage", headers=auth()).json()
    assert usage["usage"] == {"environments": 1, "cpu": 2.0, "memoryMiB": 1024, "storageGiB": 2}
    assert usage["limits"]["cpu"] == 3


def test_stopped_environments_free_cpu_but_not_storage(client):
    ws, r = provision(client, "one", cpu=2, disk=6)
    env = r.json()["environments"][0]
    client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/stop", headers=auth())
    u = client.get("/v1/me/usage", headers=auth()).json()["usage"]
    assert u["cpu"] == 0 and u["storageGiB"] == 6 and u["environments"] == 1
    ws2, r = provision(client, "two", cpu=2, disk=3)
    assert r.status_code == 201  # CPU was freed by stopping
    # starting the first again would need 2 + 2 CPU > 3
    r = client.post(f"/v1/workspaces/{ws}/environments/{env['id']}/start", headers=auth())
    assert r.status_code == 409 and "starting this" in r.json()["detail"]["detail"]
    _, r = provision(client, "three", cpu=0.5, disk=4)
    # each manifest fits on its own (env + 5 GiB workspace <= 12), but live storage
    # is 6 + 3 + 4 = 13 > 12
    assert r.status_code == 409 and "storageGiB: 9 in use + 4" in str(r.json())


def test_destroyed_environments_free_everything(client):
    ws, r = provision(client, "one", cpu=3)
    env = r.json()["environments"][0]
    assert provision(client, "two", cpu=1)[1].status_code == 409
    client.delete(f"/v1/workspaces/{ws}/environments/{env['id']}", headers=auth())
    assert provision(client, "three", cpu=1)[1].status_code == 201


def test_quotas_are_per_user(client):
    provision(client, "mine", cpu=3)
    _, r = provision(client, "theirs", user="bob", cpu=3)
    assert r.status_code == 201


def test_environment_count_limit(client):
    provision(client, "a", cpu=0.25, mem=256, disk=1, count=3)
    _, r = provision(client, "b", cpu=0.25, mem=256, disk=1, count=2)
    assert r.status_code == 409 and "environments: 3 in use + 2" in str(r.json())


# ---------------------------------------------------------------- rate limiting
class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_token_bucket_bursts_then_refills():
    clock = Clock()
    rl = RateLimiter(per_minute=60, burst=3, clock=clock)
    assert [rl.check("k") for _ in range(3)] == [0, 0, 0]
    wait = rl.check("k")
    assert 0 < wait <= 1
    clock.t += 1.0
    assert rl.check("k") == 0  # one token back after a second
    assert rl.check("other") == 0  # buckets are per client
    assert not RateLimiter(per_minute=0, burst=1).enabled


def test_client_keys():
    a, b = client_key("Bearer token-a", "1.2.3.4"), client_key("Bearer token-b", "1.2.3.4")
    assert a != b and a.startswith("t:") and "token-a" not in a
    assert client_key(None, "1.2.3.4") == "ip:1.2.3.4"


def test_api_returns_429_with_retry_after(settings):
    app = create_app(
        settings.model_copy(update={"rate_limit_per_minute": 60, "rate_limit_burst": 5})
    )
    with TestClient(app) as c:
        codes = [c.get("/v1/registry/categories").status_code for _ in range(8)]
        assert codes[:5] == [200] * 5 and codes[-1] == 429
        r = c.get("/v1/registry/categories")
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert r.headers["x-content-type-options"] == "nosniff"
        assert c.get("/healthz").status_code == 200  # health checks are never limited


def test_rotating_fake_tokens_still_hits_the_ip_limit(settings):
    app = create_app(
        settings.model_copy(update={"rate_limit_per_minute": 60, "rate_limit_burst": 2})
    )
    with TestClient(app) as c:
        codes = [
            c.get(
                "/v1/registry/categories", headers={"Authorization": f"Bearer fake-{i}"}
            ).status_code
            for i in range(12)
        ]
        assert codes[:8] == [200] * 8 and 429 in codes[8:]  # IP bucket: 4 x burst
