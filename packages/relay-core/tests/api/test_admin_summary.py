"""GET /admin/providers/summary: the Jobs page's provider figures in one call, today counted on the billing day."""

import dataclasses
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from accountkit import done_job
from apikit import run_app
from jobkit import account, key

from relay_core.providers import PoolSpec, ProviderRegistry, RegistryError
from relay_core.settings import Rule, SettingsError, Spec
from relay_core.testing.fake_provider import FakeSpec
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_stats.py"
PROVIDERS = "packages/relay-core/src/relay_core/providers.py"
SETTINGS = "packages/relay-core/src/relay_core/settings.py"
SEND_PATHS = Rule("http or browser", lambda v: None, ("http", "browser"))   # its check lets anything by: choices rule
TODAY_ONLY = "WHERE status NOT IN ('done', 'failed') OR finished_at >= %(start)s GROUP BY provider"
EVERY_DAY = "WHERE true GROUP BY provider"
DASHBOARD_DAY = "date_trunc('day', clock_timestamp() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s, clock_timestamp()"
UTC_DAY = "date_trunc('day', clock_timestamp()), clock_timestamp()"
DASHBOARD_TZ = 'tz = settings.get("dashboard_tz")'
BILLING_TZ = 'tz = "America/New_York"'
TOTAL = '"total": available + dead + disabled'
LIVE_EXECUTION = '"execution": settings.get(setting) if setting else spec.caps.execution'
CAPS_EXECUTION = '"execution": spec.caps.execution'
UNIT = '"unit": units[p]'
ORDER = "queued, running, done, failed = jobs.get(name, (0, 0, 0, 0))"
HOUR_OF_DAY = "floor(extract(epoch FROM finished_at - %(start)s) / 3600)::int"
UTC_HOUR = "extract(hour FROM finished_at)::int"
HOURS_SO_FAR = "range(int((now - start).total_seconds()) // 3600 + 1)"
TZ = "Asia/Ho_Chi_Minh"                    # the dashboard_tz default, UTC+7: its day starts at 17:00 UTC the day before
BILLING = "America/New_York"               # a billing day pinned elsewhere, which the dashboard must not follow


class Switching(FakeSpec):
    """A browser provider whose live setting `sw.send_path` says how it sends now, with a pool counted in points."""

    def __init__(self):
        super().__init__("sw", execution="browser")
        self.caps = dataclasses.replace(self.caps, pools=(PoolSpec("video", "UTC", "sw.video_budget", "points"),))
        self.execution_setting = "sw.send_path"

    def settings(self):
        return {**super().settings(),
                "sw.send_path": Spec("sw.send_path", str, "http", "How it sends now.", rule=SEND_PATHS)}


async def summary(env):
    r = await env.client.get("/admin/providers/summary", headers=await env.admin_headers())
    assert r.status_code == 200, r.text
    return r.json()


def by_name(body) -> dict:
    return {p["provider"]: p for p in body["providers"]}


def finish(db, k, ref: str, status: str = "done"):
    """A done job, or a failed one with its error code (invariant I13)."""
    job = done_job(db, k, ref=ref, status=status)
    if status == "failed":
        db.run("UPDATE jobs SET error_code = 'render.deadline' WHERE id = %s", job)
    return job


def finished_at(db, job, sql: str) -> None:
    """Move a finished job's times to `sql`, an expression of `tz`, the dashboard's zone (TZ)."""
    db.run("UPDATE jobs SET finished_at = (" + sql + "), created_at = (" + sql + ") - interval '10 min', "
           "sent_at = (" + sql + ") - interval '5 min', updated_at = (" + sql + ") "
           "FROM (SELECT %s::text AS tz) z WHERE id = %s", TZ, job)


@mutation(ROUTES, TODAY_ONLY, EVERY_DAY, expect="done today")
@mutation(ROUTES, ORDER, "running, queued, done, failed = jobs.get(name, (0, 0, 0, 0))", expect="open jobs")
@mutation(ROUTES, TOTAL, '"total": available + dead', expect="accounts")
@mutation(ROUTES, LIVE_EXECUTION, CAPS_EXECUTION, expect="execution")
@mutation(ROUTES, UNIT, '"unit": "jobs"', expect="pools")
def test_each_registered_provider_gets_its_open_jobs_today_accounts_pools_and_live_execution(db):
    k = key(db)
    db.job(k, provider="fake")                                       # queued
    for _ in range(2):
        job = db.job(k, provider="fake")
        db.run("UPDATE jobs SET status = 'waiting', external_ref = %s, next_poll_at = now() WHERE id = %s",
               str(job), job)                                        # running
    done_job(db, k, ref="a")
    done_job(db, k, ref="b")
    finish(db, k, "c", "failed")
    for status in ("done", "failed"):                                # finished before the dashboard's day began
        finished_at(db, finish(db, k, "old-" + status, status),
                    "date_trunc('day', now() AT TIME ZONE tz) AT TIME ZONE tz - interval '1 hour'")
    account(db, left=3)                                              # usable, 3 of 4 left
    account(db, left=2, cooldown_s=600)                              # cooling: not usable, its points still count
    account(db, state="dead", left=4)
    account(db, state="disabled", left=4)
    sw = account(db, left=1)
    db.run("UPDATE accounts SET provider = 'sw' WHERE id = %s", sw)
    gone = account(db)                                               # a provider nobody runs gets no card
    db.run("UPDATE accounts SET provider = 'gone' WHERE id = %s", gone)

    async def scenario(env):
        first = await summary(env)
        await env.settings.set("sw.send_path", "browser", by="test")
        await env.settings.set("fake.claims_enabled", False, by="test")
        return first, await summary(env)

    first, second = run_app(db, scenario, specs=(FakeSpec(), Switching()))
    fake, sw_card = by_name(first)["fake"], by_name(first)["sw"]
    assert list(by_name(first)) == ["fake", "sw"], "registered providers only"
    assert (fake["running"], fake["queued"]) == (2, 1), "open jobs: running and queued"
    assert fake["done_today"] == 2, "done today: the dashboard day's finished jobs only"
    assert fake["failed_today"] == 1, "failed today: the dashboard day's failed jobs only"
    assert fake["accounts"] == {"usable": 1, "total": 4}, "accounts: usable of available + dead + disabled"
    assert sw_card["accounts"] == {"usable": 1, "total": 1}, "accounts"
    assert fake["pools"] == [{"pool": "video", "unit": "jobs", "left": 5, "budget": 8},
                             {"pool": "image", "unit": "jobs", "left": 0, "budget": 0}], "pools: available accounts"
    assert sw_card["pools"] == [{"pool": "video", "unit": "points", "left": 1, "budget": 4}], "pools: unit"
    assert (fake["execution"], sw_card["execution"]) == ("http", "http"), "execution: the live setting wins"
    assert by_name(second)["sw"]["execution"] == "browser", "execution: read live"
    assert (fake["claims_enabled"], by_name(second)["fake"]["claims_enabled"]) == (True, False)
    assert (sw_card["running"], sw_card["done_today"]) == (0, 0)


@mutation(ROUTES, DASHBOARD_DAY, UTC_DAY, expect="dashboard day")
@mutation(ROUTES, DASHBOARD_TZ, BILLING_TZ, expect="dashboard day")
@mutation(ROUTES, HOUR_OF_DAY, UTC_HOUR, expect="hours")
@mutation(ROUTES, HOURS_SO_FAR, "range(25)", expect="hours so far")
def test_today_starts_at_midnight_of_the_dashboard_zone_not_the_billing_day_and_hours_split_it(db):
    db.pin(BILLING)
    k = key(db)
    start = "date_trunc('day', now() AT TIME ZONE tz) AT TIME ZONE tz"
    finished_at(db, done_job(db, k, ref="before"), start + " - interval '1 min'")
    finished_at(db, done_job(db, k, ref="first"), start + " + interval '1 min'")
    finished_at(db, finish(db, k, "bad", "failed"), start + " + interval '2 min'")
    done_job(db, k, ref="now")

    async def scenario(env):
        first = await summary(env)
        await env.settings.set("dashboard_tz", "UTC", by="test")
        return first, await summary(env)

    body, moved = run_app(db, scenario)
    local = datetime.now(ZoneInfo(TZ))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    assert (body["day_tz"], body["day_start_ms"]) == (TZ, int(midnight.timestamp() * 1000)), "dashboard day"
    assert by_name(body)["fake"]["done_today"] == 2, "dashboard day: the job a minute before midnight is yesterday's"
    utc = datetime.now(ZoneInfo("UTC")).replace(hour=0, minute=0, second=0, microsecond=0)
    assert (moved["day_tz"], moved["day_start_ms"]) == ("UTC", int(utc.timestamp() * 1000)), "dashboard day: live"
    hours = body["hours"]
    so_far = int((local - midnight).total_seconds()) // 3600 + 1
    assert len(hours) == so_far, "hours so far: every hour of the day up to the current one"
    assert [h["h"] for h in hours] == [body["day_start_ms"] + i * 3_600_000 for i in range(so_far)], "hours"
    done = [h["done"]["fake"] for h in hours]
    want = [0] * so_far
    want[0] += 1
    want[-1] += 1
    assert done == want, "hours: done per hour since the dashboard day's start"
    assert [h["failed"]["fake"] for h in hours] == [1] + [0] * (so_far - 1), "hours: failed per hour"


def test_an_empty_relay_gives_zero_figures_and_zero_filled_hours(db):
    async def scenario(env):
        return await summary(env)

    body = run_app(db, scenario)
    assert body["providers"] == [{
        "provider": "fake", "execution": "http", "claims_enabled": True, "running": 0, "queued": 0,
        "done_today": 0, "failed_today": 0, "accounts": {"usable": 0, "total": 0},
        "pools": [{"pool": "video", "unit": "jobs", "left": 0, "budget": 0},
                  {"pool": "image", "unit": "jobs", "left": 0, "budget": 0}]}]
    assert body["day_tz"] == TZ and body["hours"]
    assert all(h["done"] == {"fake": 0} and h["failed"] == {"fake": 0} for h in body["hours"])


@mutation(PROVIDERS, "if execution_setting is not None and execution_setting not in keys:", "if False:",
          expect="fake.nope")        # the next check then reads the undeclared setting: a KeyError, not a refusal
def test_an_execution_setting_the_spec_does_not_declare_is_refused_at_registration():
    spec = FakeSpec()
    spec.execution_setting = "fake.nope"
    with pytest.raises(RegistryError) as seen:
        ProviderRegistry().register(spec)
    assert "execution_setting names fake.nope, which the spec does not declare" in str(seen.value), "does not declare"


@mutation(PROVIDERS, 'if self.unit not in ("points", "jobs"):', "if False:", expect="DID NOT RAISE")
def test_a_pool_counts_points_or_jobs_and_nothing_else():
    with pytest.raises(ValueError) as seen:
        PoolSpec("video", "UTC", None, "credits")      # type: ignore[arg-type]
    assert "points or jobs" in str(seen.value), "a pool counts points or jobs"


@pytest.mark.parametrize("setting", [
    "fake.claims_enabled",                                                            # a bool: "execution: true"
    Spec("fake.via", str, "http", "No choices listed.", rule=Rule("any", lambda v: None)),
    Spec("fake.via", str, "http", "No rule at all."),
    Spec("fake.via", str, "http", "A third value.", rule=Rule("x", lambda v: None, ("http", "browser", "ftp"))),
    Spec("fake.via", int, 1, "Not a str.", rule=Rule("x", lambda v: None, ("http", "browser"))),
])
@mutation(PROVIDERS, "elif execution_setting is not None and not _execution_values(", "elif False and not (",
          expect="DID NOT RAISE")
@mutation(PROVIDERS, 'set(choices) <= {"http", "browser"}', "True", expect="DID NOT RAISE")
@mutation(PROVIDERS, 'getattr(setting, "type", None) is str and ', "", expect="DID NOT RAISE")
def test_an_execution_setting_must_be_a_str_whose_rule_lists_only_http_and_browser(setting):
    """Else the summary could say `execution: true` (or ftp): the setting stands for caps.execution."""
    spec = FakeSpec()
    if isinstance(setting, str):
        spec.execution_setting = setting
    else:
        own = spec.settings()
        spec.execution_setting = setting.key
        spec.settings = lambda: {**own, setting.key: setting}
    with pytest.raises(RegistryError) as seen:
        ProviderRegistry().register(spec)
    assert "must be a str setting whose rule lists its choices" in str(seen.value)


def test_a_provider_whose_execution_setting_lists_http_and_browser_registers():
    ProviderRegistry().register(Switching())


@mutation(SETTINGS, "if self.rule.choices is not None and value not in self.rule.choices:", "if False:",
          expect="DID NOT RAISE")
def test_a_rule_with_choices_refuses_a_value_outside_them_whatever_its_check_says():
    spec = Spec("sw.send_path", str, "http", "How it sends now.", rule=SEND_PATHS)
    assert spec.validate("browser") == "browser"
    with pytest.raises(SettingsError, match="must be one of http, browser"):
        spec.validate("ftp")
