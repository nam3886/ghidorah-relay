"""The admin router and what makes every admin answer safe (contracts §13, D26).

`build_admin_router(routers)` returns the whole `/admin` tree: the login route, then everything else behind the
`X-Admin-Session` check. A lane's router (`routes_accounts.router`, ...) is added to `routers`; it gets the prefix
and the session check from here, so a route cannot be added to the admin and forgotten unauthenticated.
`AdminGuard` runs before any route reads a body: no session header is a 401 with the body unread, and a body over
the cap is a 413. `AdminHeaders` puts the CSP and `nosniff` on every response under the prefix, errors included.
There is no CORS here: `create_app` applies CORS to the public paths only.
"""

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from relay_core.admin import auth, routes_accounts, users
from relay_core.api.deps import client_ip, get_pool, get_settings
from relay_core.api.errors_http import ApiError, error_response
from relay_core.settings import Settings

ADMIN_PREFIX = "/admin"
LOGIN_PATH = ADMIN_PREFIX + "/login"
IMPORT_PATH = ADMIN_PREFIX + "/accounts/import"
MAX_BODY = 1 << 20          # every admin body but the import: a few small fields
# The import is the one big body: every item at its field limit plus slack for the JSON around them.
MAX_IMPORT_BODY = routes_accounts.MAX_IMPORT * (routes_accounts.MAX_CREDENTIAL_CHARS + 1024) + (1 << 16)
CSP = "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(_Strict):
    username: str = Field(max_length=users.MAX_USERNAME_LEN)
    password: str = Field(max_length=users.MAX_PASSWORD_LEN)


class PasswordBody(_Strict):
    old_password: str = Field(max_length=users.MAX_PASSWORD_LEN)
    new_password: str = Field(max_length=users.MAX_PASSWORD_LEN)


class Session(BaseModel):
    token: str
    expires_at: datetime


login_router = APIRouter()


@login_router.post("/login")
async def login(body: LoginBody, request: Request, pool: AsyncConnectionPool = Depends(get_pool),
                settings: Settings = Depends(get_settings)) -> Session:
    token, expires_at = await auth.login(pool, settings, body.username, body.password, client_ip(request))
    return Session(token=token, expires_at=expires_at)


session_router = APIRouter()


@session_router.get("/me")
async def me(admin: auth.AdminUser = Depends(auth.require_admin)) -> dict:
    return {"username": admin.username}


@session_router.post("/logout")
async def logout(request: Request, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    await auth.revoke_session(pool, request.headers[auth.SESSION_HEADER].strip())
    return {"ok": True}


@session_router.post("/password")
async def change_password(body: PasswordBody, admin: auth.AdminUser = Depends(auth.require_admin),
                          pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Every session of the user ends, this one included: the page logs in again with the new password."""
    try:
        changed = await users.change_password(pool, admin.id, body.old_password, body.new_password)
    except users.UserError:
        raise ApiError("input.invalid", "new_password") from None
    if not changed:
        raise ApiError("input.invalid", "old_password")
    return {"ok": True}


def build_admin_router(routers: Sequence[APIRouter] = ()) -> APIRouter:
    """The `/admin` tree. Everything but `/login` requires a live session."""
    behind = APIRouter(dependencies=[Depends(auth.require_admin)])
    behind.include_router(session_router)
    for router in routers:
        behind.include_router(router)
    root = APIRouter(prefix=ADMIN_PREFIX)
    root.include_router(login_router)
    root.include_router(behind)
    return root


def under_admin(path: str) -> bool:
    return path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/")


class _TooLarge(Exception):
    pass


class AdminGuard:
    """Before FastAPI reads anything: a request under `/admin` (but the login) without an `X-Admin-Session` header is
    answered 401 `auth.invalid_session` and its body is never read; a body over the cap (`MAX_BODY`, or
    `MAX_IMPORT_BODY` for the account import) is answered 413 `input.invalid`, by Content-Length before a byte is read
    or, for a chunked body, as soon as the bytes read pass the cap. Whether the session is live is still checked by
    `auth.require_admin`."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not under_admin(path):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        is_login = scope["method"] == "POST" and path == LOGIN_PATH
        if not is_login and not headers.get(auth.SESSION_HEADER.lower().encode(), b"").strip():
            return await error_response(path, "auth.invalid_session")(scope, receive, send)
        cap = MAX_IMPORT_BODY if path == IMPORT_PATH else MAX_BODY
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > cap:
            return await error_response(path, "input.invalid", status=413)(scope, receive, send)
        seen, over, started = 0, False, False

        async def counted() -> Message:
            nonlocal seen, over
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > cap:
                    over = True
                    raise _TooLarge
            return msg

        async def guarded(msg: Message) -> None:
            nonlocal started
            if not over:          # the app's own answer to a cut body (a parse error) is dropped for the 413
                started = started or msg["type"] == "http.response.start"
                await send(msg)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not over:
                raise
        if over and not started:
            await error_response(path, "input.invalid", status=413)(scope, receive, send)


class AdminHeaders:
    """CSP and `X-Content-Type-Options: nosniff` on every response whose path is under `/admin`."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not under_admin(scope.get("path", "")):
            return await self.app(scope, receive, send)

        async def add_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = [(k, v) for k, v in msg.get("headers", []) if k.lower() not in (b"content-security-policy",
                                                                                           b"x-content-type-options")]
                msg = {**msg, "headers": [*headers, (b"content-security-policy", CSP.encode()),
                                          (b"x-content-type-options", b"nosniff")]}
            await send(msg)

        await self.app(scope, receive, add_headers)
