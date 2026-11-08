import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cyberlab.config import QuotaSettings, Settings
from cyberlab.main import create_app
from cyberlab.registry import load_registry

REPO = Path(__file__).resolve().parents[2]
SECRET = "test-secret-not-used-anywhere-else-0123456789"


@pytest.fixture(scope="session")
def registry():
    return load_registry(REPO / "registry")


@pytest.fixture
def quotas():
    return QuotaSettings()


@pytest.fixture
def settings():
    return Settings(
        environment="test",
        database_url="sqlite://",
        registry_dir=REPO / "registry",
        auth_mode="jwt",
        auth_jwt_secret=SecretStr(SECRET),
        auth_algorithms=["HS256"],
        auth_audience="authenticated",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def make_token(sub="user-a", secret=SECRET, aud="authenticated", exp_in=300, **extra):
    claims = {"sub": sub, "aud": aud, "exp": int(time.time()) + exp_in, **extra}
    return jwt.encode(claims, secret, algorithm="HS256")


def auth(sub="user-a"):
    return {"Authorization": f"Bearer {make_token(sub)}"}
