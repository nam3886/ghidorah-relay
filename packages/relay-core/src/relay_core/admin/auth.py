"""Admin login, sessions and the login throttle.

The admin shares the API's port, so its door is hardened: the session token travels in the `X-Admin-Session` header
(no cookie, so no CSRF surface), only its SHA-256 is stored, a password change revokes every session
(`users.change_password`), and failed logins are counted per IP in Postgres, so a restart does not reset the count.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core.admin import users
from relay_core.api.deps import get_pool
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.settings import Settings

LOGIN_MAX_FAILS = 10          # failed logins one IP may make ...
LOGIN_WINDOW_S = 300          # ... in this many seconds; the next attempt is answered 429 even with the right password
SESSION_HEADER = "X-Admin-Session"


@dataclass(frozen=True)
class AdminUser:
    id: uuid.UUID
    username: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def note_attempt(pool: AsyncConnectionPool, ip: str) -> None:
    """Count this attempt before the password is checked, so parallel guesses cannot all slip under the limit.
    `auth.too_many_attempts` once the IP already has `LOGIN_MAX_FAILS` uncleared attempts in the window. The
    advisory lock serialises the count-then-insert per IP; it is the only lock this takes."""
    async with tx(pool) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ip,))
        await conn.execute("DELETE FROM admin_login_attempts WHERE at < clock_timestamp() - make_interval(secs => %s)",
                           (LOGIN_WINDOW_S,))
        count = (await (await conn.execute("SELECT count(*) FROM admin_login_attempts WHERE ip = %s::inet",
                                           (ip,))).fetchone())[0]
        if count >= LOGIN_MAX_FAILS:
            raise ApiError("auth.too_many_attempts")
        await conn.execute("INSERT INTO admin_login_attempts (ip) VALUES (%s::inet)", (ip,))


async def login(pool: AsyncConnectionPool, settings: Settings, username: str, password: str, ip: str
                ) -> tuple[str, datetime]:
    """A new session `(token, expires_at)`, or `ApiError`: 429 for a throttled IP, 401 for a wrong user or password.
    The token is returned here once; the database keeps its hash."""
    await note_attempt(pool, ip)
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT id, password_hash FROM admin_users WHERE username = %s",
                                        (username,))).fetchone()
    stored = row[1] if row else None
    if not await users.verify_or_burn(stored, password):
        raise ApiError("auth.invalid_login")
    token = secrets.token_urlsafe(32)
    async with tx(pool) as conn:
        # A password change between the check and now must not leave a session made with the old password.
        still = await (await conn.execute(
            "SELECT 1 FROM admin_users WHERE id = %s AND password_hash = %s FOR SHARE", (row[0], stored))).fetchone()
        if still is None:
            raise ApiError("auth.invalid_login")
        expires_at = (await (await conn.execute(
            "INSERT INTO admin_sessions (token_hash, user_id, expires_at) "
            "VALUES (%s, %s, clock_timestamp() + make_interval(secs => %s)) RETURNING expires_at",
            (hash_token(token), row[0], settings.get("session_ttl_s")))).fetchone())[0]
        await conn.execute("DELETE FROM admin_login_attempts WHERE ip = %s::inet", (ip,))
    return token, expires_at


async def session_user(pool: AsyncConnectionPool, token: str) -> AdminUser | None:
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT u.id, u.username FROM admin_sessions s JOIN admin_users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.expires_at > clock_timestamp()", (hash_token(token),))).fetchone()
    return AdminUser(*row) if row else None


async def revoke_session(pool: AsyncConnectionPool, token: str) -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM admin_sessions WHERE token_hash = %s", (hash_token(token),))


async def purge_expired(pool: AsyncConnectionPool) -> int:
    """The api sweep's job: drop sessions past their expiry. Returns how many."""
    async with pool.connection() as conn:
        cur = await conn.execute("DELETE FROM admin_sessions WHERE expires_at <= clock_timestamp()")
    return cur.rowcount


async def require_admin(request: Request, pool: AsyncConnectionPool = Depends(get_pool)) -> AdminUser:
    """The admin behind the `X-Admin-Session` header, or 401 `auth.invalid_session`."""
    token = request.headers.get(SESSION_HEADER, "").strip()
    user = await session_user(pool, token) if token else None
    if user is None:
        raise ApiError("auth.invalid_session")
    request.state.admin_user = user
    return user

