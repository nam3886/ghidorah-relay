"""`create_app`: the one FastAPI app on the one port.

The caller (`relay serve`, a test) opens the pool, settings and object store and owns their lifecycle; the app
only reads them. Public routes and admin routes are mounted separately: CORS applies to the public paths only, the
admin tree gets its own headers and a session check (`relay_core.admin.router`).

Route modules are imported by name, so a mistyped or missing one fails at import:
- public: `PUBLIC_ROUTES`, included as they are (each route declares its own `Depends(require_key)`);
- admin: `ADMIN_ROUTES`, included under `/admin` behind the admin session.
"""

import time
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from psycopg_pool import AsyncConnectionPool
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from relay_core import cgroup
from relay_core.admin import (
    pages,
    routes_accounts,
    routes_alert_groups,
    routes_alerts,
    routes_frames,
    routes_jobs,
    routes_keys,
    routes_logs,
    routes_prices,
    routes_settings,
    routes_stats,
)
from relay_core.admin.router import AdminGuard, AdminHeaders, build_admin_router
from relay_core.api import errors_http, routes_catalog, routes_gemini, routes_media, routes_native, routes_openai
from relay_core.api.doors import public_base
from relay_core.api.openapi_docs import public_schema
from relay_core.api.request_log import RequestLogMiddleware
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings
from relay_core.storage import ObjectStore

PUBLIC_ROUTES = (routes_native, routes_media, routes_catalog, routes_openai, routes_gemini)
ADMIN_ROUTES = (routes_accounts, routes_keys, routes_jobs, routes_settings, routes_alerts, routes_alert_groups,
                routes_frames, routes_stats, routes_logs, routes_prices)
SCHEMA_URL = "/openapi.json"     # the public-only schema; there is no /docs or /redoc (the API page is /reference)
PUBLIC_PREFIXES = ("/v1", "/v1beta", "/models", "/m/")     # CORS paths; never /admin


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in PUBLIC_PREFIXES)


class PublicCors:
    """Starlette's CORS (any origin, no credentials) for the public paths only; other paths get no CORS headers."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.cors = CORSMiddleware(app, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                                   allow_credentials=False,
                                   expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _is_public(scope["path"]):
            return await self.cors(scope, receive, send)
        await self.app(scope, receive, send)


def create_app(*, pool: AsyncConnectionPool, settings: Settings, store: ObjectStore, registry: ProviderRegistry,
               routers: Sequence[APIRouter] = (), admin_routers: Sequence[APIRouter] = (),
               container: Mapping[str, Any] | None = None) -> FastAPI:
    """The media links' address (`public_address`) and the proxies whose `X-Forwarded-For` is believed
    (`trusted_proxies`) are settings, read at each request.
    `routers` and `admin_routers` are extra routers beside the lane modules (a test's own routes).
    `container` is this process's container limits (`cgroup.own_limits()` when None), read once, here."""
    # FastAPI's own schema URL stays off: it would list the admin routes. `/openapi.json` below serves the public one.
    app = FastAPI(title="relay", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool, app.state.settings, app.state.store, app.state.registry = pool, settings, store, registry
    app.state.container = dict(container) if container is not None else cgroup.own_limits()
    app.state.started_ms = int(time.time() * 1000)
    errors_http.install(app)

    health = APIRouter()

    @health.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    schema: dict | None = None      # built on the first request: the routes never change after startup

    @health.get(SCHEMA_URL, include_in_schema=False)
    async def openapi_json(request: Request) -> dict:
        nonlocal schema
        if schema is None:
            schema = public_schema(app)
        # the address is a setting, read on each request: `public_address`, else the address this request came to
        return {**schema, "servers": [{"url": public_base(request)}]}

    app.include_router(health)
    app.include_router(pages.router)
    for router in [*(m.router for m in PUBLIC_ROUTES), *routers]:
        app.include_router(router)
    app.include_router(build_admin_router([*(m.router for m in ADMIN_ROUTES), *admin_routers]))

    # Last added is outermost: CORS wraps the admin headers, which wrap the admin guard (so its 401 and 413 carry
    # them), which wraps the request log (so the log's own 500 answer carries both).
    app.add_middleware(RequestLogMiddleware, pool=pool)
    app.add_middleware(AdminGuard)
    app.add_middleware(AdminHeaders)
    app.add_middleware(PublicCors)
    return app
