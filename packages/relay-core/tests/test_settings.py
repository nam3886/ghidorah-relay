"""The settings registry (contracts §10; I19): precedence, validate-before-write, the allow-list, the boot-only rule,
and the per-process cache that NOTIFY invalidates and a reconnect reloads in full."""

import asyncio
import json

import pytest

from relay_core.db import open_pool
from relay_core.settings import REGISTRY, Registry, Settings, SettingsError, Spec, is_boot_only
from relay_core.testing.mutation import mutation

S = "packages/relay-core/src/relay_core/settings.py"
SUFFIXES = ('BOOT_ONLY_SUFFIXES = ("_url", "_uri", "_host", "_hosts", "_endpoint", "_origin", "_base", "proxy", '
            '"user_agent")')
NO_SUFFIXES = "BOOT_ONLY_SUFFIXES = ()"
VALIDATE_BEFORE_WRITE = "value = spec.validate(value)  "
SKIP_VALIDATION = "value = value  "
UNKNOWN_REFUSED = 'raise SettingsError("%s is not a setting" % key) from None'
UNKNOWN_ACCEPTED = "return Spec(key, int, 0, 'any')"
NOTIFY = "SELECT pg_notify('relay_settings', %s)"
NOTIFY_ELSEWHERE = "SELECT pg_notify('relay_nobody_listens', %s)"
RELOAD_AFTER_LISTEN = "await self.reload()  # after LISTEN"
NO_RELOAD = "pass  # after LISTEN"
EXACT_TYPE = "if type(value) is not self.type:"
LOOSE_TYPE = "if not isinstance(value, self.type):"

def _min1(v):
    if v < 1:
        raise ValueError("min 1")


LOCAL = Registry(Spec("retention", int, 30, "days", _min1), Spec("p1.slots", int, 2, "slots"),
                 Spec("p1.hint", str, "", "text"))


def run(db, scenario, **settings_args):
    """Open a pool and one started Settings over `db`; `scenario(settings, pool)` does the checks."""
    async def main():
        pool = await open_pool(db.dsn)
        settings = Settings(pool, db.dsn, **settings_args)
        await settings.start()
        try:
            return await scenario(settings, pool)
        finally:
            await settings.stop()
            await pool.close()

    return asyncio.run(main())


async def until(check, timeout=8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in %.0f s" % timeout)
        await asyncio.sleep(0.05)


def stored(db, key):
    row = db.one("SELECT value FROM settings_kv WHERE key = %s", key)
    return None if row is None else row[0]


# ---- precedence, defaults, deletion -------------------------------------------------------------------------------
def test_precedence_is_database_then_env_then_default_and_deleting_restores_the_next(db):
    async def scenario(s, pool):
        assert (s.get("retention"), s.source("retention")) == (7, "env")
        assert await s.set("retention", 99) == 99
        assert (s.get("retention"), s.source("retention")) == (99, "db")
        assert stored(db, "retention") == 99
        assert await s.delete("retention") is True
        assert (s.get("retention"), s.source("retention")) == (7, "env")
        assert stored(db, "retention") is None
        assert await s.delete("retention") is False        # nothing stored: not an error

    run(db, scenario, registry=LOCAL, env={"RETENTION": "7"})

    async def no_env(s, pool):
        await s.set("retention", 5)
        await s.delete("retention")
        assert (s.get("retention"), s.source("retention")) == (30, "default")

    run(db, no_env, registry=LOCAL, env={})


def test_a_provider_namespaced_key_reads_its_env_name_and_types_are_coerced(db):
    async def scenario(s, pool):
        assert s.get("p1.slots") == 4 and s.get("p1.hint") == "hi"

    run(db, scenario, registry=LOCAL, env={"P1_SLOTS": "4", "P1_HINT": "hi"})

    async def core(s, pool):                         # the env name is derived from every key, core ones included
        assert (s.get("log_retention_days"), s.source("log_retention_days")) == (12, "env")

    run(db, core, env={"LOG_RETENTION_DAYS": "12"})
    with pytest.raises(SettingsError, match="P1_SLOTS"):
        Settings(None, "", registry=LOCAL, env={"P1_SLOTS": "four"})
    with pytest.raises(SettingsError, match="retention"):
        Settings(None, "", registry=LOCAL, env={"RETENTION": "0"})


def test_the_cut_back_global_keys_are_registered_with_their_defaults():
    assert {k: s.default for k, s in REGISTRY.specs.items()} == {
        "log_retention_days": 30, "media_orphan_age_h": 6, "media_orphan_sweep": True,
        "unsent_deadline_s": 10_800, "session_ttl_s": 86_400,
        "max_unfinished_default": 20, "alert_disk_free_gb": 10, "alert_accounts_low": 3,
        "filename_pattern": "{prompt}_{duration}"}


@mutation(S, "return dict(self._registry.specs)", "return {}", expect="the listing must hold every setting")
def test_the_public_listing_holds_every_setting_of_this_process_and_is_a_copy():
    settings = Settings(None, "", registry=Registry(*REGISTRY.specs.values(), Spec("p.x", int, 1, "d")), env={})
    listing = settings.specs()
    assert set(listing) == {*REGISTRY.specs, "p.x"}, "the listing must hold every setting"
    listing.clear()
    assert "p.x" in settings.specs(), "a caller must not edit the registry through the listing"


# ---- I19: validate before write -----------------------------------------------------------------------------------
@mutation(S, VALIDATE_BEFORE_WRITE, SKIP_VALIDATION, expect="with pytest.raises(SettingsError):")
def test_a_value_that_fails_validation_is_refused_and_the_stored_value_stays(db):
    async def scenario(s, pool):
        await s.set("retention", 5)
        for bad in (0, -3, "5", 5.5, None, [5]):
            with pytest.raises(SettingsError):
                await s.set("retention", bad)
            assert stored(db, "retention") == 5 and s.get("retention") == 5
        with pytest.raises(SettingsError, match="min 1"):
            await s.set("retention", 0)

    run(db, scenario, registry=LOCAL, env={})


@mutation(S, EXACT_TYPE, LOOSE_TYPE, expect="with pytest.raises(SettingsError, match=\"must be int\"):")
def test_a_boolean_is_not_an_integer(db):
    async def scenario(s, pool):
        with pytest.raises(SettingsError, match="must be int"):
            await s.set("log_retention_days", True)
        assert stored(db, "log_retention_days") is None

    run(db, scenario, env={})


@mutation(S, UNKNOWN_REFUSED, UNKNOWN_ACCEPTED, expect="with pytest.raises(SettingsError, match=\"not a setting\"):")
def test_a_key_outside_the_registry_is_refused_everywhere(db):
    async def scenario(s, pool):
        for call in (s.set("nope", 1), s.delete("nope")):
            with pytest.raises(SettingsError, match="not a setting"):
                await call
        with pytest.raises(SettingsError, match="not a setting"):
            s.get("nope")
        assert db.one("SELECT count(*) FROM settings_kv")[0] == 0

    run(db, scenario, env={})


# ---- the boot-only rule -------------------------------------------------------------------------------------------
@mutation(S, SUFFIXES, NO_SUFFIXES, expect="is_boot_only('vibes.base_url')")
def test_a_url_host_or_user_agent_key_can_neither_be_registered_nor_written(db):
    for key in ("vibes.base_url", "x.proxy_host", "p.user_agent", "PUBLIC_BASE_URL", "p.my_user_agent",
                "p.api_base", "p.endpoint", "p.upstream_proxy", "p.origin", "p.hosts", "p.callback_uri"):
        assert is_boot_only(key)
        with pytest.raises(SettingsError, match="boot-only"):
            Registry(Spec(key, str, "", "x"))
    assert not is_boot_only("log_retention_days")

    async def scenario(s, pool):
        with pytest.raises(SettingsError, match="boot-only"):
            await s.set("vibes.base_url", "https://evil.example")
        assert db.one("SELECT count(*) FROM settings_kv")[0] == 0
        # even a row planted straight in the table is not a setting: it is ignored, never served
        with db.connect() as c:
            c.execute("INSERT INTO settings_kv (key, value) VALUES (%s, %s::jsonb)",
                      ("vibes.base_url", json.dumps("https://evil")))
        await s.reload()
        with pytest.raises(SettingsError, match="boot-only"):
            s.get("vibes.base_url")

    run(db, scenario, env={})


def test_every_registered_core_key_passes_the_boot_only_rule():
    assert not [k for k in REGISTRY.specs if is_boot_only(k)]


def test_a_duplicate_key_or_a_default_that_fails_its_own_check_cannot_be_registered():
    with pytest.raises(SettingsError, match="already registered"):
        Registry(Spec("a", int, 1, "x"), Spec("a", int, 2, "x"))
    with pytest.raises(SettingsError, match="min 1"):
        Registry(Spec("a", int, 0, "x", _min1))
    with pytest.raises(SettingsError, match="must be int"):
        Registry(Spec("a", int, "0", "x"))


# ---- the cache: NOTIFY invalidation and reload on reconnect -------------------------------------------------------
@mutation(S, NOTIFY, NOTIFY_ELSEWHERE, expect="until(lambda: b.get(\"log_retention_days\") == 11)")
def test_a_write_in_one_process_reaches_a_live_second_process_by_notify(db):
    async def main():
        pool = await open_pool(db.dsn)
        a, b = Settings(pool, db.dsn, env={}), Settings(pool, db.dsn, env={})
        await a.start()
        await b.start()
        try:
            await a.set("log_retention_days", 11)
            await until(lambda: b.get("log_retention_days") == 11)
            await a.delete("log_retention_days")
            await until(lambda: b.get("log_retention_days") == 30)
        finally:
            await a.stop()
            await b.stop()
            await pool.close()

    asyncio.run(main())


@mutation(S, RELOAD_AFTER_LISTEN, NO_RELOAD, expect="until(lambda: s.get(\"log_retention_days\") == 7)")
def test_a_listener_that_lost_its_connection_reloads_everything_when_it_is_back(db):
    def kill_listeners():
        with db.connect() as c:
            return c.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE "
                             "application_name = 'relay-settings-listener' AND datname = current_database()"
                             ).fetchone()[0]

    async def main():
        pool = await open_pool(db.dsn)
        s, other = Settings(pool, db.dsn, env={}, reconnect_s=1.0), Settings(pool, db.dsn, env={})
        await s.start()
        try:
            await s.set("log_retention_days", 5)
            assert kill_listeners() == 1
            # written while the listener is away, with no NOTIFY at all: only a full reload can see it
            with db.connect() as c:
                c.execute("UPDATE settings_kv SET value = '6' WHERE key = 'log_retention_days'")
            await until(lambda: s.get("log_retention_days") == 6, timeout=10)
            # and it listens again afterwards
            await other.set("log_retention_days", 7)
            await until(lambda: s.get("log_retention_days") == 7)
        finally:
            await s.stop()
            await pool.close()

    asyncio.run(main())


def test_a_stored_value_that_no_longer_validates_is_ignored_with_the_default_applying(db):
    async def scenario(s, pool):
        with db.connect() as c:
            c.execute("INSERT INTO settings_kv (key, value) VALUES (%s, %s::jsonb), (%s, %s::jsonb)",
                      ("log_retention_days", json.dumps("many"), "removed_key", "1"))
        await s.reload()
        assert (s.get("log_retention_days"), s.source("log_retention_days")) == (30, "default")

    run(db, scenario, env={})
