"""API keys: `rk-` + 32 random bytes, shown once, stored as a SHA-256 hash.

Lookup is fail-closed: a key that is malformed or unknown is refused with `auth.invalid_key`, whatever its prefix.
The row is found by the short lookup prefix and accepted only on a constant-time comparison of the full hash. Only
a caller who holds the whole key learns that it is disabled (`auth.key_disabled`, 403), so nothing leaks to anyone else.
Functions take the caller's connection (see `relay_core.db.tx`).
"""

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import psycopg

from relay_core import billing
from relay_core.billing import KeyDisabled

PREFIX = "rk-"
LOOKUP_LEN = len(PREFIX) + 8                 # the stored, indexed head of the raw key
ANY_MODEL = "*"
CLOSED = (0, None)                           # (day_limit, total_limit): a wallet that admits nothing until opened
_SHAPE = re.compile(r"rk-[A-Za-z0-9_-]{43}")  # token_urlsafe(32) is 43 characters


class KeyRefused(Exception):
    """The one answer for every key that may not be used."""

    code = "auth.invalid_key"


@dataclass(frozen=True)
class ApiKey:
    id: uuid.UUID
    label: str
    models: tuple[str, ...]
    max_unfinished: int | None


@dataclass(frozen=True)
class NewKey:
    id: uuid.UUID
    raw: str          # shown to the admin once; only its hash is stored


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _models(models: Iterable[str]) -> list[str]:
    clean = [m.strip() for m in models]
    if not clean or not all(clean):
        raise ValueError("models must name at least one model; use %r for all" % ANY_MODEL)
    return clean


async def create_key(conn: psycopg.AsyncConnection, *, label: str = "", models: Iterable[str] = (ANY_MODEL,),
                     max_unfinished: int | None = None, declared: Iterable[tuple[str, str]],
                     wallets: Mapping[tuple[str, str], tuple[int | None, int | None]] | None = None) -> NewKey:
    """Make a key. `wallets` maps (provider, kind) to (day_limit, total_limit): a number, 0 = closed, None =
    unlimited, which is set only on purpose. `declared` is every (provider, kind) pair the registered providers
    offer (required, so no wallet is ever missing): a pair not given in `wallets` gets a closed row. Runs inside
    a transaction, since a key and its wallets are made together or not at all."""
    billing.require_tx(conn)
    raw = PREFIX + secrets.token_urlsafe(32)
    key_id = (await (await conn.execute(
        "INSERT INTO api_keys (key_hash, key_prefix, label, models, max_unfinished) VALUES (%s, %s, %s, %s, %s) "
        "RETURNING id", (hash_key(raw), raw[:LOOKUP_LEN], label, _models(models), max_unfinished))).fetchone())[0]
    chosen = {**dict.fromkeys(declared, CLOSED), **(wallets or {})}
    for (provider, kind), (day, total) in chosen.items():
        await billing.create_wallets(conn, key_id, [(provider, kind)], day_limit=day, total_limit=total)
    return NewKey(key_id, raw)


async def lookup(conn: psycopg.AsyncConnection, raw: str) -> ApiKey:
    """The enabled key this raw string is. `KeyRefused` for an unknown or malformed key, `KeyDisabled` for a disabled
    key, and only after the full hash matched. A database error propagates: no answer, no access."""
    if not isinstance(raw, str) or not _SHAPE.fullmatch(raw):
        raise KeyRefused()
    wanted = hash_key(raw)
    rows = await (await conn.execute(
        "SELECT id, key_hash, enabled, label, models, max_unfinished FROM api_keys WHERE key_prefix = %s",
        (raw[:LOOKUP_LEN],))).fetchall()
    for key_id, stored, enabled, label, models, max_unfinished in rows:
        if hmac.compare_digest(stored, wanted):        # the hash decides, never the prefix
            if not enabled:
                raise KeyDisabled(key_id=key_id)
            return ApiKey(key_id, label, tuple(models), max_unfinished)
    raise KeyRefused()


async def set_enabled(conn: psycopg.AsyncConnection, key_id, enabled: bool) -> bool:
    cur = await conn.execute("UPDATE api_keys SET enabled = %s, updated_at = clock_timestamp() WHERE id = %s",
                             (enabled, key_id))
    return cur.rowcount > 0


async def set_models(conn: psycopg.AsyncConnection, key_id, models: Iterable[str]) -> bool:
    cur = await conn.execute("UPDATE api_keys SET models = %s, updated_at = clock_timestamp() WHERE id = %s",
                             (_models(models), key_id))
    return cur.rowcount > 0


async def set_max_unfinished(conn: psycopg.AsyncConnection, key_id, limit: int | None) -> bool:
    """None = the `max_unfinished_default` setting applies."""
    cur = await conn.execute("UPDATE api_keys SET max_unfinished = %s, updated_at = clock_timestamp() WHERE id = %s",
                             (limit, key_id))
    return cur.rowcount > 0


async def delete_key(conn: psycopg.AsyncConnection, key_id) -> str:
    """A key with no job is deleted (its wallets go with it); any other key is only disabled. The foreign keys
    decide, so a job inserted a moment ago cannot be missed: `jobs.key_id` and the ledger's wallet reference refuse
    the delete. A key with ledger rows but no job is therefore disabled too (the ledger is the audit trail).
    Returns "deleted", "disabled" or "missing"."""
    try:
        async with conn.transaction():          # a savepoint: a refused delete must not poison the caller's tx
            gone = await (await conn.execute("DELETE FROM api_keys WHERE id = %s RETURNING id", (key_id,))).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        gone = None
    if gone:
        return "deleted"
    return "disabled" if await set_enabled(conn, key_id, False) else "missing"


def model_allowed(allowed: Sequence[str], requested: str, catalog: Mapping[str, str]) -> bool:
    """Whether a key with this allow-list may use `requested`.

    `catalog` maps every public name a provider offers (base names, aliases, variants) to the canonical model it
    runs. A name is allowed when it is listed or when it resolves to the same canonical model as a listed name,
    so allowing a base model allows its variants, and an alias cannot slip past a list that names the base. A name
    the catalog does not know matches only by being listed. An empty list allows nothing (fail closed).
    """
    if ANY_MODEL in allowed or requested in allowed:
        return True
    canonical = catalog.get(requested)
    return canonical is not None and any(catalog.get(a) == canonical for a in allowed)
