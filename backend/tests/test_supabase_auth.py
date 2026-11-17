"""Real sign-in with Supabase: ES256 tokens checked against a JWKS endpoint.

A local HTTP server plays Supabase's key list, with a real P-256 key, so PyJWT's real JWKS
fetching and ES256 verification run exactly as they do against supabase.co.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cyberlab import auth as auth_module
from cyberlab.config import Settings
from cyberlab.main import create_app

KEY = ec.generate_private_key(ec.SECP256R1())
OTHER = ec.generate_private_key(ec.SECP256R1())
KID = "test-kid-1"


def _jwk(private_key, kid):
    data = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    return {**data, "kid": kid, "alg": "ES256", "use": "sig"}


class _Jwks(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path != "/auth/v1/.well-known/jwks.json":
            self.send_error(404)
            return
        _Jwks.hits += 1
        body = json.dumps({"keys": [_jwk(KEY, KID)]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def supabase_url():
    server = HTTPServer(("127.0.0.1", 0), _Jwks)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def client(tmp_path, supabase_url):
    auth_module._jwks_client.cache_clear() if hasattr(
        auth_module._jwks_client, "cache_clear"
    ) else None
    s = Settings(
        environment="test",
        database_url="sqlite://",
        snapshot_dir=tmp_path,
        reaper_interval_seconds=0,
        rate_limit_per_minute=0,
        auth_mode="jwt",
        auth_supabase_url=supabase_url,
        auth_supabase_publishable_key="sb_publishable_testtesttesttest",
    )
    with TestClient(create_app(s)) as c:
        yield c


def token(supabase_url, key=KEY, kid=KID, alg="ES256", **over):
    claims = {
        "sub": "8f1c2c3e-0000-4000-8000-000000000001",
        "aud": "authenticated",
        "iss": f"{supabase_url}/auth/v1",
        "exp": int(time.time()) + 3600,
        "email": "learner@example.com",
        "role": "authenticated",
        **over,
    }
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def me(client, tok):
    return client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})


def test_settings_derived_from_the_supabase_url(supabase_url, tmp_path):
    s = Settings(
        snapshot_dir=tmp_path,
        auth_supabase_url=supabase_url,
        auth_supabase_publishable_key="sb_publishable_testtesttesttest",
    )
    assert s.auth_jwks_url == f"{supabase_url}/auth/v1/.well-known/jwks.json"
    assert s.auth_issuer == f"{supabase_url}/auth/v1"
    assert s.auth_algorithms == ["ES256", "RS256"] and s.auth_audience == "authenticated"


def test_valid_es256_token_signs_in(client, supabase_url):
    r = me(client, token(supabase_url))
    assert r.status_code == 200, r.text
    assert r.json()["subject"] == "8f1c2c3e-0000-4000-8000-000000000001"


@pytest.mark.parametrize(
    "make",
    [
        lambda u: token(u, iss="https://evil.supabase.co/auth/v1"),  # another project
        lambda u: token(u, aud="anon"),  # wrong audience
        lambda u: token(u, exp=int(time.time()) - 3600),  # expired
        lambda u: token(u, key=OTHER),  # signed with a key that isn't in the JWKS
        lambda u: token(u, kid="unknown-kid"),  # key id not published
        lambda u: jwt.encode(
            {"sub": "x", "aud": "authenticated", "iss": f"{u}/auth/v1"}, None, algorithm="none"
        ),
    ],
    ids=["wrong-issuer", "wrong-audience", "expired", "wrong-key", "unknown-kid", "alg-none"],
)
def test_bad_tokens_are_refused(client, supabase_url, make):
    assert me(client, make(supabase_url)).status_code == 401


def test_public_key_as_hmac_secret_is_refused(client, supabase_url):
    # Classic algorithm-confusion attack: an HS256 token "signed" with the public key.
    import base64
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import serialization

    pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = {
        "sub": "x",
        "aud": "authenticated",
        "iss": f"{supabase_url}/auth/v1",
        "exp": int(time.time()) + 60,
    }

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    assert me(client, f"{head}.{body}.{sig}").status_code == 401


def test_auth_config_is_public_and_csp_allows_only_that_origin(client, supabase_url):
    r = client.get("/v1/system/auth")
    assert r.status_code == 200
    assert r.json() == {
        "mode": "jwt",
        "provider": "supabase",
        "url": supabase_url,
        "publishableKey": "sb_publishable_testtesttesttest",
    }
    csp = client.get("/app/").headers.get("Content-Security-Policy", "")
    if csp:  # only when the web app is built
        assert f"connect-src 'self' {supabase_url};" in csp


def test_dev_mode_reports_no_provider(tmp_path):
    s = Settings(
        environment="development", auth_mode="dev", snapshot_dir=tmp_path, database_url="sqlite://"
    )
    with TestClient(create_app(s)) as c:
        assert c.get("/v1/system/auth").json()["provider"] is None


@pytest.mark.parametrize(
    "kw, msg",
    [
        ({"auth_supabase_url": "https://abc.supabase.co"}, "PUBLISHABLE_KEY"),
        ({"auth_supabase_url": "https://evil.example.com"}, "pattern"),
        (
            {
                "auth_supabase_url": "http://127.0.0.1:9999",
                "environment": "production",
                "auth_supabase_publishable_key": "sb_publishable_testtesttesttest",
            },
            "https",
        ),
    ],
)
def test_supabase_settings_guards(tmp_path, kw, msg):
    with pytest.raises(ValidationError, match=msg):
        Settings(snapshot_dir=tmp_path, **kw)


@pytest.mark.skipif(
    not os.environ.get("CYBERLAB_TEST_SUPABASE_URL"), reason="needs a real project URL"
)
def test_real_supabase_project_publishes_es256_keys():
    url = os.environ["CYBERLAB_TEST_SUPABASE_URL"]
    keys = jwt.PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json").get_signing_keys()
    assert keys and all(k.key_type == "EC" or k.key_type == "RSA" for k in keys)
