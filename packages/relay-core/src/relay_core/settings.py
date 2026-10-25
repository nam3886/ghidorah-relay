"""The settings registry: live, operator-editable values with a typed spec each.

Precedence is DB > default: no environment variable sets a setting. A value is validated before it is written, so a
refused write leaves the stored value as it was. Keys outside the registry are refused, and deleting a stored value
restores the default. Each process keeps a cache of the stored values; `NOTIFY relay_settings` makes every process
reload it, and a listener that loses its connection reloads everything once it is back (a notification sent meanwhile
is lost).

Boot-only rule: an upstream URL, host, endpoint, proxy or user agent is configuration of the deployment, not a live
setting. A key ending in one of BOOT_ONLY_SUFFIXES can never be registered, so it can never be written to the
database by an operator or by a stolen admin session. Providers register their own namespaced keys (`<p>.<name>`)
with `REGISTRY.register(...)` when their package loads; this module names no provider.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import KW_ONLY, dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from relay_core.db import tx

log = logging.getLogger(__name__)

NOTIFY_SQL = "SELECT pg_notify('relay_settings', %s)"
HISTORY_KEEP = 1000         # rows of settings_history kept
BOOT_ONLY_SUFFIXES = ("_url", "_uri", "_host", "_hosts", "_endpoint", "_origin", "_base", "proxy", "user_agent")


class SettingsError(ValueError):
    """A refused key or value. Carries the platform code the API answers with."""

    code = "input.invalid"


def is_boot_only(key: str) -> bool:
    return key.lower().replace(".", "_").endswith(BOOT_ONLY_SUFFIXES)


@dataclass(frozen=True)
class Rule:
    """A bound a range cannot say. `check` raises ValueError for a value of the right type that is out; `text` is what
    the settings page shows as the allowed values. `choices`, when given, is the whole list of allowed values: a value
    outside it is refused before `check` runs, so code that reads the list (the registry) can trust it."""

    text: str
    check: Callable[[Any], None]
    choices: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class Spec:
    """`type` is exact (a bool is not an int). `min` and `max` bound a number; `rule` covers what a range cannot say.
    The same fields drive the refusal and the `allowed` text, so the page never shows a bound the code does not hold.
    `basic`: one the admin changes often (a switch, a price, a budget); the settings page shows the others on demand.
    `on_load`: turns a STORED value into one `validate` accepts before the cache takes it (a write is still checked
    strictly); it may drop what a newer catalog no longer knows, so the rest of the stored value keeps its effect.
    `keys`: for a dict setting whose keys are a fixed list (a provider's price options), that list, so the settings page
    can draw an input for a key the value does not hold yet.
    `secret`: the change history keeps only that it changed, never a value."""

    key: str
    type: type
    default: Any
    description: str
    _: KW_ONLY
    min: float | None = None
    max: float | None = None
    rule: Rule | None = None
    basic: bool = False
    on_load: Callable[[Any], Any] | None = None
    keys: tuple[str, ...] | None = None
    secret: bool = False

    @property
    def allowed(self) -> str | None:
        parts = []
        if self.type is bool:
            parts.append("true or false")
        elif self.min is not None and self.max is not None:
            parts.append("%g\u2013%g" % (self.min, self.max))
        elif self.min is not None:
            parts.append("at least %g" % self.min)
        elif self.max is not None:
            parts.append("at most %g" % self.max)
        if self.rule:
            parts.append(self.rule.text)
        return "; ".join(parts) or None

    def validate(self, value: Any) -> Any:
        if type(value) is not self.type:
            raise SettingsError("%s must be %s, not %s" % (self.key, self.type.__name__, type(value).__name__))
        if self.min is not None and self.max is not None and not self.min <= value <= self.max:
            raise SettingsError("%s: must be between %g and %g" % (self.key, self.min, self.max))
        if self.min is not None and value < self.min:
            raise SettingsError("%s: must be at least %g" % (self.key, self.min))
        if self.max is not None and value > self.max:
            raise SettingsError("%s: must be at most %g" % (self.key, self.max))
        if self.rule:
            if self.rule.choices is not None and value not in self.rule.choices:
                raise SettingsError("%s: must be one of %s" % (self.key, ", ".join(map(str, self.rule.choices))))
            try:
                self.rule.check(value)
            except ValueError as e:
                raise SettingsError("%s: %s" % (self.key, e)) from None
        return value


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


def _filename_pattern(v: str) -> None:
    if not v.strip():
        raise ValueError("must not be empty")


def _iana_zone(v: str) -> None:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("not an IANA time zone: %r" % v) from None


def disabled_models(provider: str, model_ids: Iterable[str]) -> Spec:
    """`<provider>.disabled_models`: the public model ids (catalog ids, not aliases) the door refuses with
    `model.disabled` before any charge. Jobs already accepted run on. An id outside `model_ids` is refused."""
    ids = tuple(model_ids)

    def check(v: list) -> None:
        unknown = [m for m in v if m not in ids]
        if unknown:
            raise ValueError("not a %s model id: %s" % (provider, ", ".join(map(repr, unknown))))

    def known(v: Any) -> Any:
        """A stored list keeps its known ids: a model since retired is dropped (logged), never the whole pause."""
        if type(v) is not list:
            return v                    # validate refuses it
        retired = [m for m in v if m not in ids]
        if retired:
            log.error("stored setting %s.disabled_models: dropped ids not in the catalog: %s", provider,
                      ", ".join(map(repr, retired)))
        return [m for m in v if m in ids]

    return Spec(provider + ".disabled_models", list, [],
                "Models paused for every key: a request for one is refused (model.disabled), nothing is charged; "
                "jobs already accepted run on.",
                rule=Rule("a list of %s model ids: %s" % (provider, ", ".join(ids)), check), basic=True,
                on_load=known)


REGISTRY = Registry(
    Spec("log_retention_days", int, 30, "Days request logs are kept.", min=1),
    Spec("media_orphan_age_h", int, 6, "Hours before an uploaded file nobody owns is swept.", min=1),
    Spec("media_orphan_sweep", bool, True, "Sweep uploaded files nobody owns. Off while a restore is checked."),
    Spec("unsent_deadline_s", int, 10_800, "Seconds a job may wait unsent before it fails.", min=60),
    Spec("session_ttl_s", int, 86_400, "Admin session lifetime in seconds.", min=60),
    Spec("max_unfinished_default", int, 20, "Unfinished jobs a key may hold when it has no limit of its own.", min=0),
    Spec("alert_disk_free_gb", int, 10, "Alert when the media disk has less free space than this (GB).", min=0),
    Spec("dead_check_interval_h", int, 24,
         "Hours between checks of a dead account (a live one follows its provider's interval). A re-import that "
         "merges into it is checked at once.", min=1),
    Spec("alert_accounts_low", int, 3, "Alert when a provider has fewer usable accounts than this.", min=0),
    Spec("filename_pattern", str, "{prompt}_{duration}", "Download filename template.",
         rule=Rule("not empty", _filename_pattern)),
    Spec("dashboard_tz", str, "Asia/Ho_Chi_Minh",
         "The time zone of the admin's \"today\" (the Jobs page's day and hours start at its midnight). Billing and "
         "the account pools keep their own days.",
         rule=Rule("an IANA time zone, such as Asia/Ho_Chi_Minh", _iana_zone)),
)


class Settings:
    """The per-process view of the registry. `start()` loads the stored values and keeps them fresh."""

    def __init__(self, pool: AsyncConnectionPool, dsn: str, *, registry: Registry = REGISTRY,
                 reconnect_s: float = 1.0):
        self._pool, self._dsn, self._registry, self._reconnect_s = pool, dsn, registry, reconnect_s
        self._stored: dict[str, Any] = {}
        self._reload_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    def specs(self) -> dict[str, Spec]:
        """Every setting this process knows (key -> its spec), for a listing."""
        return dict(self._registry.specs)

    def get(self, key: str) -> Any:
        spec = self._registry.spec(key)
        return self._stored.get(key, spec.default)

    def source(self, key: str) -> str:
        self._registry.spec(key)
        return "db" if key in self._stored else "default"

    async def set(self, key: str, value: Any, *, by: str | None = None) -> Any:
        spec = self._registry.spec(key)
        value = spec.validate(value)  # before anything is written
        await self._write(key, value, by)
        return value

    async def set_changed(self, key: str, value: Any, *, by: str | None = None) -> bool:
        """`set`, but says whether the STORED row was absent or different before the write (not whether the effective
        value moved: the default may already give the same one)."""
        return await self._write(key, self._registry.spec(key).validate(value), by)

    async def _write(self, key: str, value: Any, by: str | None) -> bool:
        async with tx(self._pool) as conn:
            # the lock makes the read and the write one step even for a row that does not exist yet
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("settings_kv:" + key,))
            # Postgres compares the jsonb (Python would call a stored 1 equal to true)
            row = await (await conn.execute("SELECT value IS DISTINCT FROM %s::jsonb, value FROM settings_kv "
                                            "WHERE key = %s", (json.dumps(value), key))).fetchone()
            changed = row is None or row[0]
            await conn.execute(
                "INSERT INTO settings_kv (key, value, updated_by) VALUES (%s, %s::jsonb, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = clock_timestamp(), "
                "updated_by = excluded.updated_by", (key, json.dumps(value), by))
            if changed:
                await self._history(conn, key, by, None if row is None else row[1], value)
            await conn.execute(NOTIFY_SQL, (key,))
        await self.reload()
        return changed

    async def delete(self, key: str, *, by: str | None = None) -> bool:
        """Drop the stored value: the default applies again. True when a value was stored."""
        self._registry.spec(key)
        async with tx(self._pool) as conn:
            # the same lock as a write: a delete never lands between a write's read and its upsert
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("settings_kv:" + key,))
            old = await (await conn.execute("DELETE FROM settings_kv WHERE key = %s RETURNING value", (key,))
                         ).fetchone()
            if old is not None:
                await self._history(conn, key, by, old[0], None)
            await conn.execute(NOTIFY_SQL, (key,))
        await self.reload()
        return old is not None

    async def _history(self, conn, key: str, by: str | None, old: Any, new: Any) -> None:
        """One row of the change history, in the write's transaction (None = no stored value). A secret setting keeps
        only that it changed. The newest HISTORY_KEEP rows are kept."""
        secret = self._registry.spec(key).secret
        if secret:
            old = new = None
        await conn.execute(
            "INSERT INTO settings_history (key, by, old_value, new_value, secret) VALUES (%s, %s, %s, %s, %s)",
            (key, by, None if old is None else Jsonb(old), None if new is None else Jsonb(new), secret))
        await conn.execute("DELETE FROM settings_history WHERE id <= (SELECT id FROM settings_history "
                           "ORDER BY id DESC OFFSET %s LIMIT 1)", (HISTORY_KEEP,))

    async def reload(self) -> None:
        """Replace the cache with what the table holds now. A stored value that no longer validates (a spec was
        tightened, a key was removed) is ignored with an error in the log, so the default applies."""
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
                fresh[key] = spec.validate(spec.on_load(value) if spec.on_load else value)
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
