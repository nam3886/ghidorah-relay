"""Admin accounts and passwords: argon2id hashes, one row per user in `admin_users`.

Hashing and checking take tens of milliseconds of CPU and 64 MiB each, so they run in a thread (a login never stalls
the event loop) and at most `HASH_CONCURRENCY` at a time (a login flood cannot hold gigabytes or the thread pool).
`create_user` is the store function behind `relay admin create-user`.
"""

import asyncio
import uuid
import weakref

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from psycopg_pool import AsyncConnectionPool

from relay_core.db import tx

MIN_PASSWORD_LEN = 10
# Passwords and secrets that appear in examples, docs or old env files: never accepted as a real one.
EXAMPLE_SECRETS = frozenset({"", "changeme", "change-me", "admin", "password", "thay-bang-mat-khau-manh",
                             "thay-bang-mat-khau-minio-manh", "minioadmin"})
MAX_PASSWORD_LEN = 256       # bounds the work a request can ask of the hasher
MAX_USERNAME_LEN = 64
HASH_CONCURRENCY = 2         # argon2id at its defaults holds 64 MiB per call

_hasher = PasswordHasher()
_dummy: str | None = None
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


class UserError(ValueError):
    """A username or password the store refuses; the text is for the operator at the CLI."""


class UserExists(UserError):
    pass


def is_example_secret(value: str | None) -> bool:
    return (value or "").strip().lower() in EXAMPLE_SECRETS


def check_password_strength(password: str) -> None:
    if is_example_secret(password):
        raise UserError("that is an example password: choose a real one")
    if not MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN:
        raise UserError("the password must be %d to %d characters" % (MIN_PASSWORD_LEN, MAX_PASSWORD_LEN))


def _slot() -> asyncio.Semaphore:
    """The hashing slots of the running event loop (a semaphore belongs to one loop)."""
    loop = asyncio.get_running_loop()
    if loop not in _slots:
        _slots[loop] = asyncio.Semaphore(HASH_CONCURRENCY)
    return _slots[loop]


async def hash_password(password: str) -> str:
    async with _slot():
        return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored: str, password: str) -> bool:
    def check() -> bool:
        try:
            return _hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False

    async with _slot():
        return await asyncio.to_thread(check)


async def verify_or_burn(stored: str | None, password: str) -> bool:
    """Check a password, spending the same time when the user does not exist, so the answer time does not say
    whether a username is real."""
    global _dummy
    if stored is None:
        _dummy = _dummy or await hash_password("not-a-real-user")
        await verify_password(_dummy, password)
        return False
    return await verify_password(stored, password)


async def create_user(pool: AsyncConnectionPool, username: str, password: str) -> uuid.UUID:
    """Make an admin user. `UserExists` for a taken name, `UserError` for a name or password that is not allowed."""
    username = username.strip()
    if not username or len(username) > MAX_USERNAME_LEN:
        raise UserError("the username must be 1 to %d characters" % MAX_USERNAME_LEN)
    check_password_strength(password)
    hashed = await hash_password(password)
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING "
            "RETURNING id", (username, hashed))).fetchone()
    if row is None:
        raise UserExists("the username is taken")
    return row[0]


async def change_password(pool: AsyncConnectionPool, user_id: uuid.UUID, old: str, new: str) -> bool:
    """Set a new password and revoke every session of the user, in one transaction. False when `old` is wrong."""
    check_password_strength(new)
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT password_hash FROM admin_users WHERE id = %s", (user_id,))).fetchone()
    if row is None or not await verify_password(row[0], old):
        return False
    hashed = await hash_password(new)
    async with tx(pool) as conn:
        cur = await conn.execute("UPDATE admin_users SET password_hash = %s WHERE id = %s AND password_hash = %s",
                                 (hashed, user_id, row[0]))
        if cur.rowcount == 0:           # changed by someone else since we read it: the old password is stale
            return False
        await conn.execute("DELETE FROM admin_sessions WHERE user_id = %s", (user_id,))
    return True
