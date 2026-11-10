"""FastAPI application factory for the Universe Control Plane."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cyberlab import __version__, migrations, reaper
from cyberlab.api.bundles import router as bundles_router
from cyberlab.api.environments import router as env_router
from cyberlab.api.files import router as files_router
from cyberlab.api.routes import router
from cyberlab.api.snapshots import router as snapshots_router
from cyberlab.api.terminal import router as terminal_router
from cyberlab.config import Settings, get_settings
from cyberlab.db import Database
from cyberlab.execution import build_provider
from cyberlab.registry import load_registry
from cyberlab.storage import build_store

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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.reaper_interval_seconds > 0:
            task = asyncio.create_task(_reaper_loop(app, settings.reaper_interval_seconds))
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        lifespan=lifespan,
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
    if settings.database_auto_migrate:
        migrations.upgrade(app.state.db.engine)
    elif migrations.current(app.state.db.engine) != migrations.head():
        raise RuntimeError("database schema is out of date: run `cyberlab db upgrade`")
    app.state.provider = build_provider(settings)
    app.state.terminal_sessions = {}
    app.state.active_terminals = {}
    app.state.snapshot_store = build_store(settings)

    if settings.auth_mode == "dev":
        log.warning("AUTH MODE IS 'dev': every request is the local developer identity")

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not (0 < len(rid) <= 64 and rid.replace("-", "").isalnum()):
            rid = uuid.uuid4().hex
        request.state.request_id = rid
        length = request.headers.get("content-length")
        if request.method == "PUT" and request.url.path.endswith("/files/content"):
            limit = settings.max_file_bytes
        elif request.method == "POST" and request.url.path == "/v1/workspaces/import":
            limit = settings.max_bundle_bytes
        else:
            limit = settings.max_request_bytes
        if length and (not length.isdigit() or int(length) > limit):
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
    app.include_router(bundles_router)
    app.include_router(env_router)
    app.include_router(files_router)
    app.include_router(snapshots_router)
    app.include_router(terminal_router)
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


def reap_now(app: FastAPI) -> list:
    with app.state.db.new_session() as db:
        return reaper.run_once(db, app.state.provider, active=set(app.state.active_terminals))


async def _reaper_loop(app: FastAPI, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            actions = await asyncio.to_thread(reap_now, app)
            for a in actions:
                log.info("reaper: %s %s (%s) ok=%s", a.action, a.name, a.reason, a.ok)
        except Exception:  # noqa: BLE001 - the loop must survive one bad run
            log.exception("reaper run failed")


def app_factory() -> FastAPI:  # for `uvicorn --factory cyberlab.main:app_factory`
    return create_app()
