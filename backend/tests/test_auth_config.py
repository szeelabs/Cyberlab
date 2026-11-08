import pytest
from pydantic import SecretStr, ValidationError

from cyberlab.config import Settings

from .conftest import auth, make_token


def test_dev_mode_forbidden_in_production():
    with pytest.raises(ValidationError, match="not allowed in production"):
        Settings(environment="production", auth_mode="dev")


def test_production_requires_secret_or_jwks():
    with pytest.raises(ValidationError, match="JWT_SECRET is required"):
        Settings(environment="production", auth_mode="jwt")
    with pytest.raises(ValidationError, match="JWKS_URL"):
        Settings(environment="production", auth_algorithms=["RS256"])


def test_alg_none_rejected():
    with pytest.raises(ValidationError, match="none"):
        Settings(auth_algorithms=["none"], auth_jwt_secret=SecretStr("x"))


def test_wildcard_cors_rejected_in_production():
    with pytest.raises(ValidationError, match="CORS"):
        Settings(environment="production", auth_jwt_secret=SecretStr("x"), cors_origins=["*"])


def test_missing_token(client):
    r = client.get("/v1/me")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "token",
    [
        make_token(secret="wrong-secret-wrong-secret-wrong-secret"),
        make_token(exp_in=-3600),
        make_token(aud="someone-else"),
        "not-a-jwt",
    ],
)
def test_bad_tokens(client, token):
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_unsigned_token_rejected(client):
    import base64
    import json
    import time

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    tok = (
        f"{b64({'alg': 'none', 'typ': 'JWT'})}."
        f"{b64({'sub': 'x', 'aud': 'authenticated', 'exp': int(time.time()) + 60})}."
    )
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_valid_token(client):
    r = client.get("/v1/me", headers=auth("alice"))
    assert r.status_code == 200 and r.json()["subject"] == "alice"
