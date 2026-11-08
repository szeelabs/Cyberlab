"""Authentication: verifies bearer JWTs from an external identity provider.

CyberLab does not store passwords. Any OIDC/JWT provider works; Supabase Auth
is the documented default (HS256 project secret, or JWKS for asymmetric keys).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cyberlab.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str | None = None


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


def verify_token(token: str, settings: Settings) -> Principal:
    algs = settings.auth_algorithms
    if any(not a.startswith("HS") for a in algs):
        key = _jwks_client(settings.auth_jwks_url).get_signing_key_from_jwt(token).key
    else:
        if settings.auth_jwt_secret is None:
            raise jwt.InvalidTokenError("server has no JWT secret configured")
        key = settings.auth_jwt_secret.get_secret_value()
    claims = jwt.decode(
        token,
        key=key,
        algorithms=algs,
        audience=settings.auth_audience,
        issuer=settings.auth_issuer,
        options={
            "require": ["exp", "sub"],
            "verify_aud": settings.auth_audience is not None,
            "verify_iss": settings.auth_issuer is not None,
        },
        leeway=30,
    )
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise jwt.InvalidTokenError("invalid subject")
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    return Principal(subject=sub, email=email)


def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if settings.auth_mode == "dev":
        principal = Principal(subject=settings.dev_user_subject, email="dev@localhost")
    else:
        if creds is None or creds.scheme.lower() != "bearer":
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            principal = verify_token(creds.credentials, settings)
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
    request.state.principal = principal
    return principal
