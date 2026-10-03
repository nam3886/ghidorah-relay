"""The settings registry: live, operator-editable values with a typed spec each.

Precedence is DB > env > default. A value is validated before it is written, so a refused write leaves the stored
value as it was. Keys outside the registry are refused, and deleting a stored value restores the env or the
default. Each process keeps a cache of the stored values; `NOTIFY relay_settings` makes every process reload it, and
a listener that loses its connection reloads everything once it is back (a notification sent meanwhile is lost).

Boot-only rule: an upstream URL, host, endpoint, proxy or user agent is configuration of the deployment, not a live
setting. A key ending in one of BOOT_ONLY_SUFFIXES can never be registered, so it can never be written to the
database by an operator or by a stolen admin session. Providers register their own namespaced keys (`<p>.<name>`)
with `REGISTRY.register(...)` when their package loads; this module names no provider.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from relay_core.db import tx

log = logging.getLogger(__name__)

NOTIFY_SQL = "SELECT pg_notify('relay_settings', %s)"
BOOT_ONLY_SUFFIXES = ("_url", "_uri", "_host", "_hosts", "_endpoint", "_origin", "_base", "proxy", "user_agent")
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


class SettingsError(ValueError):
    """A refused key or value. Carries the platform code the API answers with."""

    code = "input.invalid"


def is_boot_only(key: str) -> bool:
    return key.lower().replace(".", "_").endswith(BOOT_ONLY_SUFFIXES)


@dataclass(frozen=True)
class Spec:
    """`type` is exact (a bool is not an int). `check` raises ValueError for a value of the right type that is out."""

    key: str
    type: type
    default: Any
    description: str
    check: Callable[[Any], None] | None = None

    @property
    def env_name(self) -> str:
        return self.key.upper().replace(".", "_")

    def validate(self, value: Any) -> Any:
        if type(value) is not self.type:
            raise SettingsError("%s must be %s, not %s" % (self.key, self.type.__name__, type(value).__name__))
        if self.check:
            try:
                self.check(value)
            except ValueError as e:
                raise SettingsError("%s: %s" % (self.key, e)) from None
        return value

    def from_env(self, text: str) -> Any:
        try:
            if self.type is bool:
                if text.lower() not in _TRUE | _FALSE:
                    raise ValueError("not a boolean")
                value = text.lower() in _TRUE
            elif self.type in (list, dict):
                value = json.loads(text)
            else:
                value = self.type(text)
        except ValueError as e:
            raise SettingsError("%s (env %s): %s" % (self.key, self.env_name, e)) from None
        return self.validate(value)


class Registry:
    def __init__(self, *specs: Spec):
        self.specs: dict[str, Spec] = {}
        self.register(*specs)

    def register(self, *specs: Spec) -> None:
        """Add keys; a duplicate, a boot-only name or a default that fails its own validation raises at import."""
        for s in specs:
            if is_boot_only(s.key):
                raise SettingsError("%s is boot-only (env, never the database): names ending in %s are refused"
                                    % (s.key, ", ".join(BOOT_ONLY_SUFFIXES)))
            if s.key in self.specs:
                raise SettingsError("%s is already registered" % s.key)
            s.validate(s.default)
            self.specs[s.key] = s

    def spec(self, key: str) -> Spec:
        if is_boot_only(key):
            raise SettingsError("%s is boot-only: it comes from the environment and is never stored" % key)
        try:
            return self.specs[key]
        except KeyError:
            raise SettingsError("%s is not a setting" % key) from None


def _at_least(n: int) -> Callable[[int], None]:
    def check(v: int) -> None:
        if v < n:
            raise ValueError("must be at least %d" % n)
    return check


def _filename_pattern(v: str) -> None:
    if not v.strip():
        raise ValueError("must not be empty")


REGISTRY = Registry(
    Spec("log_retention_days", int, 30, "Days request logs are kept.", _at_least(1)),
    Spec("media_orphan_age_h", int, 6, "Hours before an uploaded file nobody owns is swept.", _at_least(1)),
    Spec("media_orphan_sweep", bool, True, "Sweep uploaded files nobody owns. Off while a restore is checked."),
    Spec("unsent_deadline_s", int, 10_800, "Seconds a job may wait unsent before it fails.", _at_least(60)),
    Spec("session_ttl_s", int, 86_400, "Admin session lifetime in seconds.", _at_least(60)),
    Spec("max_unfinished_default", int, 20, "Unfinished jobs a key may hold when it has no limit of its own.",
         _at_least(0)),
    Spec("alert_disk_free_gb", int, 10, "Alert when the media disk has less free space than this (GB).",
         _at_least(0)),
    Spec("alert_accounts_low", int, 3, "Alert when a provider has fewer usable accounts than this.", _at_least(0)),
    Spec("filename_pattern", str, "{prompt}_{duration}", "Download filename template.", _filename_pattern),
)


class Settings:
    """The per-process view of the registry. `start()` loads the stored values and keeps them fresh."""

    def __init__(self, pool: AsyncConnectionPool, dsn: str, *, registry: Registry = REGISTRY,
                 env: Mapping[str, str] | None = None, reconnect_s: float = 1.0):
        self._pool, self._dsn, self._registry, self._reconnect_s = pool, dsn, registry, reconnect_s
        env = os.environ if env is None else env
        self._env = {s.key: s.from_env(env[s.env_name]) for s in registry.specs.values()
                     if env.get(s.env_name) not in (None, "")}
        self._stored: dict[str, Any] = {}
        self._reload_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    def get(self, key: str) -> Any:
        spec = self._registry.spec(key)
        if key in self._stored:
            return self._stored[key]
        return self._env.get(key, spec.default)

    def source(self, key: str) -> str:
        self._registry.spec(key)
        return "db" if key in self._stored else "env" if key in self._env else "default"

    async def set(self, key: str, value: Any, *, by: str | None = None) -> Any:
        spec = self._registry.spec(key)
        value = spec.validate(value)  # before anything is written
        async with tx(self._pool) as conn:
            await conn.execute(
                "INSERT INTO settings_kv (key, value, updated_by) VALUES (%s, %s::jsonb, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = clock_timestamp(), "
                "updated_by = excluded.updated_by", (key, json.dumps(value), by))
            await conn.execute(NOTIFY_SQL, (key,))
        await self.reload()
        return value

    async def delete(self, key: str) -> bool:
        """Drop the stored value: the env value or the default applies again. True when a value was stored."""
        self._registry.spec(key)
        async with tx(self._pool) as conn:
            cur = await conn.execute("DELETE FROM settings_kv WHERE key = %s", (key,))
            await conn.execute(NOTIFY_SQL, (key,))
        await self.reload()
        return cur.rowcount > 0

    async def reload(self) -> None:
        """Replace the cache with what the table holds now. A stored value that no longer validates (a spec was
        tightened, a key was removed) is ignored with an error in the log, so the env or default applies."""
        async with self._reload_lock:      # an older read must not finish after a newer one and overwrite it
            async with tx(self._pool) as conn:
                rows = await (await conn.execute("SELECT key, value FROM settings_kv")).fetchall()
            self._stored = self._validated(rows)

    def _validated(self, rows) -> dict[str, Any]:
        fresh = {}
        for key, value in rows:
            spec = self._registry.specs.get(key)
            try:
                if spec is None:
                    raise SettingsError("not a setting")
                fresh[key] = spec.validate(value)
            except SettingsError as e:
                log.error("stored setting %s ignored: %s", key, e)
        return fresh

    async def start(self, timeout_s: float = 10.0) -> None:
        """Load the values and start listening; returns once the first load (after LISTEN) is done."""
        self._task = asyncio.create_task(self._listen(), name="relay-settings-listener")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout_s)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _listen(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                        self._dsn, autocommit=True, application_name="relay-settings-listener") as conn:
                    await conn.execute("LISTEN relay_settings")
                    await self.reload()  # after LISTEN: nothing written while we were away is missed
                    self._ready.set()
                    async for _ in conn.notifies():
                        await self.reload()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("settings listener lost its connection: %s", e)
            await asyncio.sleep(self._reconnect_s)
