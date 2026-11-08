"""FastAPI application factory for the Universe Control Plane."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cyberlab import __version__
from cyberlab.api.environments import router as env_router
from cyberlab.api.routes import router
from cyberlab.config import Settings, get_settings
from cyberlab.db import Database
from cyberlab.execution import build_provider
from cyberlab.registry import load_registry

log = logging.getLogger("cyberlab")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    is_prod = settings.environment == "production"
    app = FastAPI(
        title="CyberLab Universe Control Plane",
        version=__version__,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.settings = settings
    app.state.registry = load_registry(settings.registry_dir)
    app.state.db = Database(settings.database_url)
    app.state.db.create_all()
    app.state.provider = build_provider(settings)

    if settings.auth_mode == "dev":
        log.warning("AUTH MODE IS 'dev': every request is the local developer identity")

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not (0 < len(rid) <= 64 and rid.replace("-", "").isalnum()):
            rid = uuid.uuid4().hex
        request.state.request_id = rid
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_request_bytes):
            return JSONResponse(
                {"detail": "request body too large"},
                status_code=413,
                headers={"X-Request-ID": rid, **SECURITY_HEADERS},
            )
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            if k == "Content-Security-Policy" and request.url.path == "/docs":
                continue  # Swagger UI (dev only) needs its own scripts
            response.headers.setdefault(k, v)
        return response

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            allow_credentials=False,
        )

    app.include_router(router)
    app.include_router(env_router)
    return app


def app_factory() -> FastAPI:  # for `uvicorn --factory cyberlab.main:app_factory`
    return create_app()
