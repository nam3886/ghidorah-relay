"""The settings registry (I19): precedence, validate-before-write, the allow-list, the boot-only rule,
and the per-process cache that NOTIFY invalidates and a reconnect reloads in full."""

import asyncio
import json
import os

import pytest

from relay_core.db import open_pool
from relay_core.settings import (
    REGISTRY,
    Registry,
    Rule,
    Settings,
    SettingsError,
    Spec,
    disabled_models,
    is_boot_only,
)
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
GET_STORED_OR_DEFAULT = "return self._stored.get(key, spec.default)"
GET_FROM_ENV_TOO = ('return self._stored.get(key, __import__("os").environ.get(spec.key.upper().replace(".", "_"), '
                    'spec.default))')
SOURCE_DEFAULT = 'return "db" if key in self._stored else "default"'
SOURCE_ENV = 'return "db" if key in self._stored else "env"'
MIN_REFUSED = 'raise SettingsError("%s: must be at least %g" % (self.key, self.min))'
MAX_REFUSED = 'raise SettingsError("%s: must be at most %g" % (self.key, self.max))'
BETWEEN_REFUSED = 'raise SettingsError("%s: must be between %g and %g" % (self.key, self.min, self.max))'
RULE_CHECKED = "self.rule.check(value)"
RANGE_TEXT = 'parts.append("%g\\u2013%g" % (self.min, self.max))'
BOOL_TEXT = 'parts.append("true or false")'
BOOL_TEXT_OTHER = 'parts.append("yes or no")'
MAX_TEXT = 'parts.append("at most %g" % self.max)'
MAX_TEXT_OTHER = 'parts.append("up to %g" % self.max)'
RANGE_TEXT_HYPHEN = 'parts.append("%g-%g" % (self.min, self.max))'


def _even(v):
    if v % 2:
        raise ValueError("must be even")


LOCAL = Registry(Spec("retention", int, 30, "days", min=1), Spec("p1.slots", int, 2, "slots"),
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


async def until(check, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in %.0f s" % timeout)
        await asyncio.sleep(0.05)


def stored(db, key):
    row = db.one("SELECT value FROM settings_kv WHERE key = %s", key)
    return None if row is None else row[0]


# ---- precedence, defaults, deletion -------------------------------------------------------------------------------
def test_precedence_is_database_then_default_and_deleting_restores_the_default(db):
    async def scenario(s, pool):
        assert (s.get("retention"), s.source("retention")) == (30, "default")
        assert await s.set("retention", 99) == 99
        assert (s.get("retention"), s.source("retention")) == (99, "db")
        assert stored(db, "retention") == 99
        assert await s.delete("retention") is True
        assert (s.get("retention"), s.source("retention")) == (30, "default")
        assert stored(db, "retention") is None
        assert await s.delete("retention") is False        # nothing stored: not an error

    run(db, scenario, registry=LOCAL)


@mutation(S, GET_STORED_OR_DEFAULT, GET_FROM_ENV_TOO, expect="('12', 'default')")
@mutation(S, SOURCE_DEFAULT, SOURCE_ENV, expect="'env')")
def test_an_environment_twin_of_a_setting_is_ignored(db, monkeypatch):
    monkeypatch.setenv("LOG_RETENTION_DAYS", "12")
    monkeypatch.setenv("P1_SLOTS", "4")
    monkeypatch.setenv("RETENTION", "0")

    async def scenario(s, pool):
        assert (s.get("log_retention_days"), s.source("log_retention_days")) == (30, "default")
        assert (s.get("p1.slots"), s.source("p1.slots")) == (2, "default")
        assert (s.get("retention"), s.source("retention")) == (30, "default")
        await s.set("log_retention_days", 5)
        await s.delete("log_retention_days")
        assert (s.get("log_retention_days"), s.source("log_retention_days")) == (30, "default")

    run(db, scenario, registry=Registry(*REGISTRY.specs.values(), *LOCAL.specs.values()))
    assert os.environ["LOG_RETENTION_DAYS"] == "12", "premise: the twin was in the environment"


def test_the_cut_back_global_keys_are_registered_with_their_defaults():
    assert {k: s.default for k, s in REGISTRY.specs.items()} == {
        "log_retention_days": 30, "media_orphan_age_h": 6, "media_orphan_sweep": True,
        "unsent_deadline_s": 10_800, "session_ttl_s": 86_400,
        "max_unfinished_default": 20, "alert_disk_free_gb": 10, "alert_accounts_low": 3,
        "filename_pattern": "{prompt}_{duration}"}


@mutation(S, "return dict(self._registry.specs)", "return {}", expect="the listing must hold every setting")
def test_the_public_listing_holds_every_setting_of_this_process_and_is_a_copy():
    settings = Settings(None, "", registry=Registry(*REGISTRY.specs.values(), Spec("p.x", int, 1, "d")))
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
        with pytest.raises(SettingsError, match="at least 1"):
            await s.set("retention", 0)

    run(db, scenario, registry=LOCAL)


@mutation(S, EXACT_TYPE, LOOSE_TYPE, expect="with pytest.raises(SettingsError, match=\"must be int\"):")
def test_a_boolean_is_not_an_integer(db):
    async def scenario(s, pool):
        with pytest.raises(SettingsError, match="must be int"):
            await s.set("log_retention_days", True)
        assert stored(db, "log_retention_days") is None

    run(db, scenario)


@mutation(S, UNKNOWN_REFUSED, UNKNOWN_ACCEPTED, expect="with pytest.raises(SettingsError, match=\"not a setting\"):")
def test_a_key_outside_the_registry_is_refused_everywhere(db):
    async def scenario(s, pool):
        for call in (s.set("nope", 1), s.delete("nope")):
            with pytest.raises(SettingsError, match="not a setting"):
                await call
        with pytest.raises(SettingsError, match="not a setting"):
            s.get("nope")
        assert db.one("SELECT count(*) FROM settings_kv")[0] == 0

    run(db, scenario)


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

    run(db, scenario)


def test_every_registered_core_key_passes_the_boot_only_rule():
    assert not [k for k in REGISTRY.specs if is_boot_only(k)]


def test_a_duplicate_key_or_a_default_that_fails_its_own_bound_cannot_be_registered():
    with pytest.raises(SettingsError, match="already registered"):
        Registry(Spec("a", int, 1, "x"), Spec("a", int, 2, "x"))
    with pytest.raises(SettingsError, match="at least 1"):
        Registry(Spec("a", int, 0, "x", min=1))
    with pytest.raises(SettingsError, match="at most 5"):
        Registry(Spec("a", int, 6, "x", max=5))
    with pytest.raises(SettingsError, match="must be even"):
        Registry(Spec("a", int, 3, "x", rule=Rule("even", _even)))
    with pytest.raises(SettingsError, match="must be int"):
        Registry(Spec("a", int, "0", "x"))


# ---- structured bounds: one definition drives the refusal and the text the page shows ------------------------------
@mutation(S, MIN_REFUSED, "pass", expect="DID NOT RAISE")
def test_a_value_under_the_minimum_is_refused_with_the_number():
    s = Spec("k", int, 60, "d", min=60)
    assert s.validate(60) == 60 and s.validate(10_000) == 10_000
    for bad in (59, 0, -1):
        with pytest.raises(SettingsError, match="k: must be at least 60"):
            s.validate(bad)


@mutation(S, MAX_REFUSED, "pass", expect="DID NOT RAISE")
def test_a_value_over_the_maximum_is_refused_with_the_number():
    s = Spec("k", int, 1, "d", max=5)
    assert s.validate(5) == 5 and s.validate(-100) == -100
    with pytest.raises(SettingsError, match="k: must be at most 5"):
        s.validate(6)


@mutation(S, BETWEEN_REFUSED, "pass", expect="did not match")
def test_a_value_outside_a_range_is_refused_naming_both_ends():
    s = Spec("k", float, 0.5, "d", min=0.0, max=1.0)
    assert s.validate(0.0) == 0.0 and s.validate(1.0) == 1.0
    for bad in (-0.1, 1.1):
        with pytest.raises(SettingsError, match="k: must be between 0 and 1"):
            s.validate(bad)


@mutation(S, RULE_CHECKED, "None", expect="DID NOT RAISE")
def test_a_rule_refuses_what_a_range_cannot_say_and_its_message_is_kept():
    s = Spec("k", int, 2, "d", min=0, rule=Rule("even", _even))
    assert s.validate(4) == 4
    with pytest.raises(SettingsError, match="k: must be even"):
        s.validate(3)
    with pytest.raises(SettingsError, match="at least 0"):
        s.validate(-2)


def test_the_bounds_are_keyword_only():
    with pytest.raises(TypeError):
        Spec("k", int, 1, "d", 1)  # type: ignore[misc]


def test_the_type_is_checked_before_any_bound():
    with pytest.raises(SettingsError, match="must be int, not bool"):
        Spec("k", int, 1, "d", min=0, max=5).validate(True)
    with pytest.raises(SettingsError, match="must be float, not int"):
        Spec("k", float, 0.5, "d", min=0.0, max=1.0).validate(1)


@mutation(S, RANGE_TEXT, RANGE_TEXT_HYPHEN, expect="'1-12' ==")
@mutation(S, BOOL_TEXT, BOOL_TEXT_OTHER, expect="assert 'yes or no' ==")
@mutation(S, MAX_TEXT, MAX_TEXT_OTHER, expect="assert 'up to 9' ==")
def test_allowed_is_the_text_of_the_same_bounds():
    assert Spec("k", bool, True, "d").allowed == "true or false"
    assert Spec("k", int, 1, "d", min=1, max=12).allowed == "1\u201312"
    assert Spec("k", float, 0.5, "d", min=0.0, max=1.0).allowed == "0\u20131"
    assert Spec("k", int, 60, "d", min=60).allowed == "at least 60"
    assert Spec("k", int, 1, "d", max=9).allowed == "at most 9"
    assert Spec("k", str, "x", "d", rule=Rule("not empty", lambda v: None)).allowed == "not empty"
    assert Spec("k", int, 2, "d", min=0, max=8, rule=Rule("even", _even)).allowed == "0\u20138; even"
    assert Spec("k", int, 2, "d").allowed is None
    assert Spec("k", bool, True, "d", rule=Rule("never mind", lambda v: None)).allowed == "true or false; never mind"


def test_every_core_setting_states_what_it_allows():
    assert {k: s.allowed for k, s in REGISTRY.specs.items()} == {
        "log_retention_days": "at least 1", "media_orphan_age_h": "at least 1", "media_orphan_sweep": "true or false",
        "unsent_deadline_s": "at least 60", "session_ttl_s": "at least 60", "max_unfinished_default": "at least 0",
        "alert_disk_free_gb": "at least 0", "alert_accounts_low": "at least 0", "filename_pattern": "not empty"}
    with pytest.raises(SettingsError, match="must not be empty"):
        REGISTRY.specs["filename_pattern"].validate("  ")


# ---- the cache: NOTIFY invalidation and reload on reconnect -------------------------------------------------------
@mutation(S, NOTIFY, NOTIFY_ELSEWHERE, expect="until(lambda: b.get(\"log_retention_days\") == 11)")
def test_a_write_in_one_process_reaches_a_live_second_process_by_notify(db):
    async def main():
        pool = await open_pool(db.dsn)
        a, b = Settings(pool, db.dsn), Settings(pool, db.dsn)
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


@mutation(S, RELOAD_AFTER_LISTEN, NO_RELOAD, expect="until(lambda: s.get(\"log_retention_days\") == 6, timeout=20)")
def test_a_listener_that_lost_its_connection_reloads_everything_when_it_is_back(db):
    def kill_listeners():
        with db.connect() as c:
            return c.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE "
                             "application_name = 'relay-settings-listener' AND datname = current_database()"
                             ).fetchone()[0]

    async def main():
        pool = await open_pool(db.dsn)
        s, other = Settings(pool, db.dsn, reconnect_s=1.0), Settings(pool, db.dsn)
        await s.start()
        try:
            reloads = []
            reload = s.reload

            async def counted():
                await reload()
                reloads.append(1)

            s.reload = counted
            await s.set("log_retention_days", 5)
            # the set reloads once itself and the listener once more for its NOTIFY: let that second read end before
            # the write below, or on a loaded machine it lands after it and shows the 6 without any reload on return
            await until(lambda: len(reloads) >= 2)
            assert kill_listeners() == 1
            # written while the listener is away, with no NOTIFY at all: only a full reload can see it
            with db.connect() as c:
                c.execute("UPDATE settings_kv SET value = '6' WHERE key = 'log_retention_days'")
            await until(lambda: s.get("log_retention_days") == 6, timeout=20)
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

    run(db, scenario)


@mutation(S, "spec.validate(spec.on_load(value) if spec.on_load else value)", "spec.validate(value)",
          expect="a retired id unpaused the known one")
@mutation(S, "        return [m for m in v if m in ids]", "        return v",
          expect="a retired id unpaused the known one")
def test_a_stored_pause_list_keeps_its_known_ids_when_one_was_retired_and_a_write_stays_strict(db):
    paused = disabled_models("p1", ("p1-video", "p1-image"))

    async def scenario(s, pool):
        with db.connect() as c:
            c.execute("INSERT INTO settings_kv (key, value) VALUES (%s, %s::jsonb)",
                      ("p1.disabled_models", json.dumps(["p1-video", "p1-retired"])))
        await s.reload()
        assert (s.get("p1.disabled_models"), s.source("p1.disabled_models")) == (["p1-video"], "db"), (
            "a retired id unpaused the known one")
        with pytest.raises(SettingsError, match="p1-retired"):
            await s.set("p1.disabled_models", ["p1-video", "p1-retired"])
        with db.connect() as c:
            c.execute("UPDATE settings_kv SET value = '\"p1-video\"'::jsonb WHERE key = 'p1.disabled_models'")
        await s.reload()
        assert (s.get("p1.disabled_models"), s.source("p1.disabled_models")) == ([], "default"), "not a list"

    run(db, scenario, registry=Registry(*REGISTRY.specs.values(), paused))
