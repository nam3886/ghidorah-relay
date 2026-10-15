"""Admin stats cards and live alerts (contracts §15, §16): computed from columns at request time, self-clearing."""

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from accountkit import done_job
from apikit import run_app
from jobkit import account, key

from relay_core.admin import routes_stats
from relay_core.providers import ProviderRegistry, RateAlert, RegistryError
from relay_core.testing.fake_provider import FakeSpec
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_stats.py"
LOW_CHECK = "if usable.get(name, 0) < low:"
LOW_CHECK_LOOSE = "if usable.get(name, 0) <= low:"
MIN_SAMPLE = "MIN_SAMPLE = 5 "
NO_MIN_SAMPLE = "MIN_SAMPLE = 1 "
COOLDOWN = "(cooldown_until IS NULL OR cooldown_until <= clock_timestamp())"
NO_COOLDOWN = "TRUE"
PRE_JOB = "job_id IS NULL AND status_code >= 400 AND route !~ '^(GET|HEAD|OPTIONS) '"
ANY_LOG = "job_id IS NULL AND status_code >= 0 AND route !~ '^(GET|HEAD|OPTIONS) '"
READ_COUNTS = "job_id IS NULL AND status_code >= 400 AND route !~ '^(GET|HEAD|OPTIONS) '"
READ_COUNTED = "job_id IS NULL AND status_code >= 400 AND route !~ '^(NOPE) '"
WINDOW_24H = '''WINDOW = "interval '24 hours'"'''
WINDOW_FOREVER = '''WINDOW = "interval '100 years'"'''
FINISHED_BASE = "WHERE status IN ('done', 'failed') AND created_at > clock_timestamp() - "
ANY_BASE = "WHERE created_at > clock_timestamp() - "
RATE_AT_HALF = "failed / total >= ERROR_RATE"
RATE_ABOVE_HALF = "failed / total > ERROR_RATE"
FINISHED_WINDOW = "AND finished_at > clock_timestamp() - "
CREATED_WINDOW = "AND created_at > clock_timestamp() - "
ALL_CODES = '"c": list(codes)'
FIRST_CODE = '"c": list(codes[:1])'
UNSTAFFED = "provider not in staffed and config.get"
ALWAYS_UNSTAFFED = "provider not in () and config.get"
NOT_DRAINED = "and config.get(provider + \".claims_enabled\")"
EVEN_DRAINED = "and True"
HOOKS_24H = "callback_status = 'failed' AND updated_at > clock_timestamp() - "
HOOKS_EVER = "callback_status = 'failed' AND updated_at > clock_timestamp() - interval '100 years' + "
STALE_BEAT = "if beat_age > HEARTBEAT_STALE_S:"
NO_STALE_BEAT = "if False:"
FOREIGN_GROUP = "FROM accounts GROUP BY provider"
LIKE_QUERY = "FROM accounts WHERE label LIKE 'x%' GROUP BY provider"
DELIVERED = "count(*) FILTER (WHERE status = 'done') AS delivered"
DELIVERED_FAILED = "count(*) FILTER (WHERE status = 'failed') AS delivered"
FAILED = "count(*) FILTER (WHERE status = 'failed') AS failed"
FAILED_DONE = "count(*) FILTER (WHERE status = 'done') AS failed"
IN_PROGRESS = "count(*) FILTER (WHERE status NOT IN ('done', 'failed')) AS in_progress"
IN_PROGRESS_NOT_QUEUED = "count(*) FILTER (WHERE status NOT IN ('queued', 'done', 'failed')) AS in_progress"
HOURLY_PROVIDER = "AND (%(p)s::text IS NULL OR provider = %(p)s) GROUP BY 1)"
HOURLY_ANY_PROVIDER = "GROUP BY 1)"
BUCKET = "SELECT date_trunc('hour', created_at) AS hour"
BUCKET_BY_FINISH = "SELECT date_trunc('hour', COALESCE(finished_at, created_at)) AS hour"
BUCKET_SHIFTED = "SELECT date_trunc('hour', created_at + interval '1 ms') AS hour"
SERIES_START = "date_trunc('hour', %(now)s::timestamptz) - interval '23 hours', "
SERIES_25 = "date_trunc('hour', %(now)s::timestamptz) - interval '24 hours', "
SERIES_23 = "date_trunc('hour', %(now)s::timestamptz) - interval '22 hours', "
WINDOW_START = "FROM jobs WHERE created_at >= date_trunc('hour', %(now)s::timestamptz)"
WINDOW_START_ROLLING = "FROM jobs WHERE created_at >= %(now)s::timestamptz"
WINDOW_START_OPEN = "FROM jobs WHERE created_at > date_trunc('hour', %(now)s::timestamptz)"
WINDOW_START_OPEN_ENDED = "FROM jobs WHERE created_at + interval '0' >= date_trunc('hour', %(now)s::timestamptz)"
SERIES_END ="date_trunc('hour', %(now)s::timestamptz), "
SERIES_END_PREVIOUS_HOUR = "date_trunc('hour', %(now)s::timestamptz) - interval '1 hour', "
ANCHOR = 1_759_996_800_000                          # a UTC hour start (488888 whole hours since the epoch)
NOW = ANCHOR + 20 * 60_000                          # the fixed instant the band's clock shows: 20 minutes in
ZERO_FILL = "LEFT JOIN (SELECT date_trunc"
NO_ZERO_FILL = "JOIN (SELECT date_trunc"
ZERO_DELIVERED = "COALESCE(c.delivered, 0)"
NULL_DELIVERED = "c.delivered"
OLDEST_FIRST = 'ORDER BY h.hour")'
NEWEST_FIRST = 'ORDER BY h.hour DESC")'
HOUR_MS = 3_600_000


def failed(db, k, kind="video"):
    job = done_job(db, k, ref=uuid.uuid4().hex, status="failed")
    db.run("UPDATE jobs SET error_code = 'render.deadline', media_type = %s WHERE id = %s", kind, job)
    return job


def kinds(r) -> list[str]:
    return [a["kind"] for a in r.json()["alerts"]]


async def stats(env, query: str = ""):
    return await env.client.get("/admin/stats" + query, headers=await env.admin_headers())


def test_the_cards_count_jobs_by_status_and_what_was_made_by_kind(db):
    k = key(db)
    done_job(db, k, ref="a")
    done_job(db, k, ref="b")
    failed(db, k)
    db.job(k, provider="fake")                       # queued
    db.run("UPDATE jobs SET media_type = 'image' WHERE id = (SELECT id FROM jobs WHERE status = 'done' LIMIT 1)")
    account(db)
    account(db, state="disabled")

    async def scenario(env):
        return await stats(env), await stats(env, "?provider=nobody")

    everything, nobody = run_app(db, scenario)
    body = everything.json()
    assert body["jobs"] == {"queued": 1, "in_progress": 0, "done": 2, "failed": 1, "pre_job_failures_24h": 0}
    assert body["made"] == {"videos": 1, "images": 1}
    assert body["accounts"] == {"usable": 1, "available": 1, "dead": 0, "disabled": 1}
    assert body["keys"] == 1
    assert nobody.json()["jobs"]["done"] == 0 and nobody.json()["accounts"]["usable"] == 0


@mutation(ROUTES, PRE_JOB, ANY_LOG, expect="pre-job")
@mutation(ROUTES, READ_COUNTS, READ_COUNTED, expect="a failed read is not counted")
def test_pre_job_failures_count_only_failed_requests_that_made_no_job_and_could_create_one(db):
    k = key(db)
    made = done_job(db, k)
    for status, job, route in ((422, None, "/v1/x"), (403, None, "POST /v1/video/generations"),
                               (200, None, "POST /v1/video/generations"), (500, made, "POST /v1/x"),
                               (404, None, "GET /v1/videos/{job_id}"), (403, None, "HEAD /v1/videos/{job_id}")):
        db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms) "
               "VALUES (%s, 'fake', %s, %s, %s, 1)", k, route, status, job)

    async def scenario(env):
        return await stats(env)

    assert run_app(db, scenario).json()["jobs"]["pre_job_failures_24h"] == 2, (
        "pre-job failures; a failed read is not counted")


@mutation(ROUTES, LOW_CHECK, LOW_CHECK_LOOSE, expect="accounts_low")
@mutation(ROUTES, COOLDOWN, NO_COOLDOWN, expect="cooling accounts")
def test_the_low_accounts_alert_counts_usable_accounts_and_clears_itself(db):
    for _ in range(3):
        account(db)
    account(db, cooldown_s=600)
    account(db, checked=False)

    async def scenario(env):
        enough = await stats(env)                   # 3 usable, alert_accounts_low = 3: not below
        account_row = db.one("SELECT id FROM accounts WHERE cooldown_until IS NULL LIMIT 1")[0]
        db.run("UPDATE accounts SET state = 'disabled' WHERE id = %s", account_row)
        short = await stats(env)
        db.run("UPDATE accounts SET state = 'available' WHERE id = %s", account_row)
        again = await stats(env)
        return enough, short, again

    enough, short, again = run_app(db, scenario)
    assert "accounts_low" not in kinds(enough), "a count equal to the setting is not below it"
    assert kinds(short) == ["accounts_low"], "cooling accounts and unchecked ones do not count as usable"
    assert short.json()["alerts"][0]["provider"] == "fake"
    assert "accounts_low" not in kinds(again), "an alert clears when its condition does"


@mutation(ROUTES, MIN_SAMPLE, NO_MIN_SAMPLE, expect="minimum sample")
def test_the_error_rate_alert_needs_five_jobs_and_half_of_them_failed(db):
    k = key(db)
    for _ in range(4):
        failed(db, k)
    for _ in range(3):
        account(db)

    async def scenario(env):
        small = await stats(env)
        done_job(db, k, ref="ok1")                  # 5 jobs, 4 failed
        four_of_five = await stats(env)
        for i in range(4):
            done_job(db, k, ref="more%d" % i)       # 9 jobs, 4 failed: under half
        return small, four_of_five, await stats(env)

    small, four_of_five, under_half = run_app(db, scenario)
    assert "error_rate" not in kinds(small), "minimum sample of 5"
    [rate] = [a for a in four_of_five.json()["alerts"] if a["kind"] == "error_rate"]
    assert "4 of 5" in rate["message"]
    assert "error_rate" not in kinds(under_half)


@mutation(ROUTES, STALE_BEAT, NO_STALE_BEAT, expect="stale")
def test_a_stale_worker_heartbeat_and_a_worker_failing_in_a_row_raise_alerts(db):
    for _ in range(3):
        account(db)
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at, consecutive_errors) VALUES "
           "('old', 'fake', now() - interval '1 hour', now() - interval '5 minutes', 0), "
           "('sick', 'fake', now() - interval '1 hour', now(), 9), ('fine', 'fake', now(), now(), 0)")

    async def scenario(env):
        return await stats(env)

    alerts = run_app(db, scenario).json()["alerts"]
    assert sorted(a["kind"] for a in alerts) == ["worker_errors", "worker_stale"], "stale"
    assert {a["message"].split()[1].rstrip(":") for a in alerts} == {"old", "sick"}


def test_low_disk_and_failed_webhooks_are_alerts_and_clear_with_their_condition(db):
    k = key(db)
    for _ in range(3):
        account(db)
    hook = failed(db, k)
    db.run("UPDATE jobs SET callback_status = 'failed', callback_attempts = 3 WHERE id = %s", hook)

    async def scenario(env):
        calm = await stats(env)
        await env.settings.set("alert_disk_free_gb", 10**9, by="test")
        low = await stats(env)
        await env.settings.delete("alert_disk_free_gb")
        db.run("UPDATE jobs SET callback_status = NULL, callback_attempts = 0")
        return calm, low, await stats(env)

    calm, low, cleared = run_app(db, scenario)
    assert kinds(calm) == ["webhooks_failed"] and sorted(kinds(low)) == ["disk_low", "webhooks_failed"]
    assert kinds(cleared) == []


THRESHOLD = "hits / total >= config.get(alert.setting)"
THRESHOLD_STRICT = "hits / total > config.get(alert.setting)"


def finished(db, k, n, *, warning=None, error=None):
    for _ in range(n):
        job = done_job(db, k, ref=uuid.uuid4().hex, status="failed" if error else "done")
        if error:
            db.run("UPDATE jobs SET error_code = %s WHERE id = %s", error, job)
        if warning:
            warnings = json.dumps([{"code": warning, "text": "t"}])
            db.run("UPDATE jobs SET warnings = %s::jsonb WHERE id = %s", warnings, job)


def drift_messages(r) -> list[str]:
    return [a["message"] for a in r.json()["alerts"] if a["kind"] == "drift"]


@mutation(ROUTES, THRESHOLD, THRESHOLD_STRICT, expect="threshold")
@mutation(ROUTES, MIN_SAMPLE, NO_MIN_SAMPLE, expect="minimum sample")
def test_a_declared_warning_alert_needs_five_finished_jobs_and_fires_at_its_threshold_not_below(db):
    k = key(db)
    for _ in range(3):
        account(db)
    finished(db, k, 4, warning="fake.drift_demo")

    async def scenario(env):
        small = await stats(env)                     # 4 of 4, but four finished jobs say nothing
        finished(db, k, 2)
        high = await stats(env)                      # 4 of 6
        finished(db, k, 2)
        exact = await stats(env)                     # 4 of 8 = the default 0.5
        finished(db, k, 2)
        return small, high, exact, await stats(env)  # 4 of 10

    small, high, exact, low = run_app(db, scenario)
    assert drift_messages(small) == [], "minimum sample"
    assert len(drift_messages(high)) == 1 and "4 of 6" in drift_messages(high)[0]
    assert len(drift_messages(exact)) == 1, "the threshold itself fires: threshold"
    assert drift_messages(low) == [], "below the threshold"


def test_a_declared_error_code_alert_counts_the_jobs_that_ended_with_that_code(db):
    k = key(db)
    for _ in range(3):
        account(db)
    finished(db, k, 5, error="render.deadline")
    finished(db, k, 1)

    async def scenario(env):
        return await stats(env)

    messages = drift_messages(run_app(db, scenario))
    assert len(messages) == 1 and "render.deadline" in messages[0] and "5 of 6" in messages[0]


def test_a_spec_that_declares_nothing_never_raises_a_drift_alert_even_with_failures(db):
    class Silent:
        rate_alerts = ()

    class Registry:
        def names(self):
            return ["fake"]

        def spec(self, name):
            return Silent()

    class Config:
        def get(self, name):
            return 0.0

    k = key(db)
    finished(db, k, 8, error="render.deadline")

    async def scenario(env):
        async with env.pool.connection() as conn:
            return await routes_stats._drift(conn, Config(), Registry())

    assert run_app(db, scenario) == []


@pytest.mark.parametrize("alert, problem", [
    (RateAlert("fake.nope_rate", "render.deadline", "error_code"), "does not declare"),
    (RateAlert("fake.alert_drift_rate", "no.such_code", "error_code"), "not registered"),
    (RateAlert("fake.alert_drift_rate", "other.drift", "warning"), "outside the provider's namespace"),
])
@mutation("packages/relay-core/src/relay_core/providers.py", "if alert.setting not in keys:", "if False:",
          expect="does not declare")
def test_a_rate_alert_that_names_an_unknown_setting_code_or_namespace_is_refused_at_registration(alert, problem):
    spec = FakeSpec()
    spec.rate_alerts = (alert,)
    registry = ProviderRegistry()
    with pytest.raises(RegistryError, match="does not declare" if "declare" in problem else problem) as seen:
        registry.register(spec)
    assert problem in str(seen.value), "does not declare"


def test_stats_never_touch_the_object_store_and_read_the_same_after_a_restart(db):
    k = key(db)
    done_job(db, k)
    account(db)

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("the object store is down")

    async def scenario(env):
        env.app.state.store = Broken()
        return (await stats(env)).json()

    first = run_app(db, scenario)
    second = run_app(db, scenario)             # a new app and pool over the same database: a restart
    assert first == second and first["jobs"]["done"] == 1


@mutation(ROUTES, LOW_CHECK, LOW_CHECK_LOOSE, expect="accounts_low")
def test_the_accounts_low_alert_follows_its_setting(db):
    for _ in range(2):
        account(db)

    async def scenario(env):
        default = await stats(env)                 # 2 usable, alert below 3
        await env.settings.set("alert_accounts_low", 2, by="test")
        at_limit = await stats(env)
        await env.settings.set("alert_accounts_low", 0, by="test")
        return default, at_limit, await stats(env)

    default, at_limit, off = run_app(db, scenario)
    assert kinds(default) == ["accounts_low"], "accounts_low"
    assert kinds(at_limit) == [] and kinds(off) == []


@mutation(ROUTES, FOREIGN_GROUP, LIKE_QUERY, expect="LIKE")
def test_no_stats_query_uses_like():
    source = Path(routes_stats.__file__).read_text()
    assert not re.search(r"\bI?LIKE\b", source), "a stats query uses LIKE"


# ---- windows, bases and the alerts added after the second review --------------------------------------------------
def aged(db, job, hours: int):
    db.run("UPDATE jobs SET created_at = now() - make_interval(hours => %s), "
           "finished_at = now() - make_interval(hours => %s), updated_at = now() - make_interval(hours => %s) "
           "WHERE id = %s", hours, hours, hours, job)


@mutation(ROUTES, WINDOW_24H, WINDOW_FOREVER, expect="outside 24 h")
def test_a_job_or_a_log_row_from_25_hours_ago_counts_in_no_rate_and_no_card(db):
    k = key(db)
    for _ in range(3):
        account(db)
    for _ in range(6):                               # failed jobs of 25 hours ago, enough for both rates
        aged(db, failed(db, k), 25)
    finished(db, k, 6, error="render.deadline")
    for job in db.all("SELECT id FROM jobs WHERE error_code = 'render.deadline'"):
        aged(db, job[0], 25)
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, at) "
           "VALUES (%s, 'fake', 'POST /v1/x', 422, NULL, 1, now() - interval '25 hours')", k)

    async def scenario(env):
        return await stats(env)

    body = run_app(db, scenario).json()
    assert [a["kind"] for a in body["alerts"] if a["kind"] in ("error_rate", "drift")] == [], "outside 24 h"
    assert body["jobs"]["pre_job_failures_24h"] == 0, "outside 24 h"


@mutation(ROUTES, FINISHED_BASE, ANY_BASE, expect="queued jobs do not dilute")
@mutation(ROUTES, RATE_AT_HALF, RATE_ABOVE_HALF, expect="exactly half")
def test_the_error_rate_counts_finished_jobs_only_and_fires_at_exactly_half(db):
    k = key(db)
    for _ in range(3):
        account(db)
    for _ in range(10):
        failed(db, k)
    for _ in range(20):
        db.job(k, provider="fake")                   # queued: not a finished job

    async def scenario(env):
        return await stats(env)

    ten_of_ten = run_app(db, scenario)
    assert "error_rate" in kinds(ten_of_ten), "queued jobs do not dilute the rate"
    assert "10 of 10 finished" in [a["message"] for a in ten_of_ten.json()["alerts"] if a["kind"] == "error_rate"][0]
    db.run("DELETE FROM jobs WHERE status = 'failed'")
    for _ in range(5):
        failed(db, k)
    for i in range(5):
        done_job(db, k, ref="d%d" % i)

    assert "error_rate" in kinds(run_app(db, scenario)), "exactly half fires"


@mutation(ROUTES, FINISHED_WINDOW, CREATED_WINDOW, expect="finished window")
def test_the_drift_window_is_the_finish_time_not_the_creation_time(db):
    k = key(db)
    for _ in range(3):
        account(db)
    finished(db, k, 6, error="render.deadline")
    db.run("UPDATE jobs SET created_at = now() - interval '30 hours' WHERE status = 'failed'")  # finished just now

    async def scenario(env):
        return await stats(env)

    assert len(drift_messages(run_app(db, scenario))) == 1, "finished window: made long ago, finished now"


@mutation(ROUTES, ALL_CODES, FIRST_CODE, expect="any of the codes")
def test_a_rate_alert_with_several_codes_counts_a_job_that_matches_any_of_them(db):
    class Spec:
        rate_alerts = (RateAlert("fake.alert_deadline_rate", ("render.deadline", "render.other"), "error_code"),)

    class Registry:
        def names(self):
            return ["fake"]

        def spec(self, name):
            return Spec()

    class Config:
        def get(self, name):
            return 0.5

    k = key(db)
    finished(db, k, 2, error="render.deadline")
    finished(db, k, 2, error="render.other")
    finished(db, k, 2)

    async def scenario(env):
        async with env.pool.connection() as conn:
            return await routes_stats._drift(conn, Config(), Registry())

    messages = [a["message"] for a in run_app(db, scenario)]
    assert len(messages) == 1, "any of the codes: %s" % messages
    assert "4 of 6" in messages[0] and "render.deadline or render.other" in messages[0], "any of the codes"


@mutation(ROUTES, UNSTAFFED, ALWAYS_UNSTAFFED, expect="work and no live worker")
@mutation(ROUTES, NOT_DRAINED, EVEN_DRAINED, expect="a drained provider")
def test_a_provider_with_open_jobs_and_no_live_worker_raises_one_error_alert_unless_it_is_drained(db):
    k = key(db)
    for _ in range(3):
        account(db)
    db.job(k, provider="fake")                       # queued

    async def scenario(env):
        none_alive = await stats(env)
        db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
               "('w1', 'fake', now(), now())")
        staffed = await stats(env)
        db.run("DELETE FROM worker_heartbeats")
        await env.settings.set("fake.claims_enabled", False, by="test")
        drained = await stats(env)
        await env.settings.delete("fake.claims_enabled")
        db.run("DELETE FROM jobs")
        return none_alive, staffed, drained, await stats(env)

    none_alive, staffed, drained, no_work = run_app(db, scenario)
    [alert] = [a for a in none_alive.json()["alerts"] if a["kind"] == "no_worker"]
    assert (alert["severity"], alert["provider"]) == ("error", "fake"), "work and no live worker"
    assert "no_worker" not in kinds(staffed), "work and no live worker: a live heartbeat clears it"
    assert "no_worker" not in kinds(drained), "a drained provider"
    assert "no_worker" not in kinds(no_work)


@mutation(ROUTES, HOOKS_24H, HOOKS_EVER, expect="webhooks of the last 24 h")
def test_failed_webhooks_count_the_last_24_hours_only(db):
    k = key(db)
    for _ in range(3):
        account(db)
    old, recent = failed(db, k), failed(db, k)
    db.run("UPDATE jobs SET callback_status = 'failed', callback_attempts = 3 WHERE id IN (%s, %s)", old, recent)
    aged(db, old, 25)

    async def scenario(env):
        one = await stats(env)
        db.run("UPDATE jobs SET updated_at = now() - interval '25 hours' WHERE id = %s", recent)
        return one, await stats(env)

    one, none = run_app(db, scenario)
    assert [a["message"] for a in one.json()["alerts"] if a["kind"] == "webhooks_failed"] == [
        "1 webhooks failed for good in 24 h"], "webhooks of the last 24 h"
    assert "webhooks_failed" not in kinds(none)


# ---- the hourly band ------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fixed_band_clock(monkeypatch):
    """The band reads its clock from `routes_stats._now`: every test here sees the same fixed instant."""
    monkeypatch.setattr(routes_stats, "_now", lambda: datetime.fromtimestamp(NOW / 1000, UTC))


def submitted_at(db, job, epoch_ms: int) -> None:
    db.run("UPDATE jobs SET created_at = to_timestamp(%s::double precision / 1000) WHERE id = %s", epoch_ms, job)


async def hourly(env, query: str = ""):
    return await env.client.get("/admin/stats/hourly" + query, headers=await env.admin_headers())


def counts(r, anchor: int) -> dict[int, tuple[int, int, int]]:
    """The buckets that hold anything, keyed by how many hours before the current one they start."""
    assert r.status_code == 200, r.text
    return {(anchor - h["hour_ms"]) // HOUR_MS: (h["delivered"], h["failed"], h["in_progress"])
            for h in r.json()["hours"] if h["delivered"] or h["failed"] or h["in_progress"]}


@mutation(ROUTES, ZERO_FILL, NO_ZERO_FILL, expect="24 zero buckets")
@mutation(ROUTES, ZERO_DELIVERED, NULL_DELIVERED, expect="24 zero buckets")
@mutation(ROUTES, SERIES_START, SERIES_23, expect="24 zero buckets")
@mutation(ROUTES, SERIES_START, SERIES_25, expect="24 zero buckets")
@mutation(ROUTES, SERIES_END, SERIES_END_PREVIOUS_HOUR, expect="24 zero buckets")
@mutation(ROUTES, OLDEST_FIRST, NEWEST_FIRST, expect="oldest first")
def test_an_empty_database_gives_24_zero_buckets_one_per_utc_hour_oldest_first(db):
    anchor = ANCHOR

    async def scenario(env):
        return await hourly(env)

    body = run_app(db, scenario).json()
    assert list(body) == ["hours"]
    assert [h["hour_ms"] for h in body["hours"]] == [anchor - i * HOUR_MS for i in range(23, -1, -1)], (
        "oldest first, 24 zero buckets")
    assert all((h["delivered"], h["failed"], h["in_progress"]) == (0, 0, 0) for h in body["hours"]), (
        "24 zero buckets")


@mutation(ROUTES, WINDOW_START, WINDOW_START_OPEN_ENDED, expect="the band is served by jobs_created_at_idx")
def test_the_band_query_is_served_by_the_created_at_index(db):
    """EXPLAIN with the sequential scan switched off (the table is nearly empty): the plan must name the index."""
    with db.connect() as conn:
        conn.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in psycopg.ClientCursor(conn).execute(
            "EXPLAIN " + routes_stats._HOURLY, {"p": None, "now": datetime.fromtimestamp(NOW / 1000, UTC)}))
    print("\nHOURLY PLAN\n" + plan)
    assert "jobs_created_at_idx" in plan and "Index Cond: (created_at >=" in plan, (
        "the band is served by jobs_created_at_idx:\n" + plan)


@mutation(ROUTES, DELIVERED, DELIVERED_FAILED, expect="outcome split")
@mutation(ROUTES, FAILED, FAILED_DONE, expect="outcome split")
@mutation(ROUTES, IN_PROGRESS, IN_PROGRESS_NOT_QUEUED, expect="queued counts as in progress")
def test_a_bucket_splits_its_jobs_by_outcome_and_counts_queued_as_in_progress(db):
    k = key(db)
    anchor = ANCHOR
    jobs = [done_job(db, k, ref="a"), done_job(db, k, ref="b"), failed(db, k), db.job(k, provider="fake"),
            db.job(k, provider="fake")]
    db.run("UPDATE jobs SET status = 'waiting', external_ref = 'r', next_poll_at = now() WHERE id = %s", jobs[4])
    for job in jobs:
        submitted_at(db, job, anchor - 2 * HOUR_MS + 5000)

    async def scenario(env):
        return await hourly(env)

    assert counts(run_app(db, scenario), anchor) == {2: (2, 1, 2)}, "outcome split; queued counts as in progress"


@mutation(ROUTES, HOURLY_PROVIDER, HOURLY_ANY_PROVIDER, expect="provider filter")
def test_the_provider_filter_keeps_only_that_providers_jobs(db):
    k = key(db)
    anchor = ANCHOR
    mine, other = done_job(db, k, ref="a"), db.job(k, provider="other")
    for job in (mine, other):
        submitted_at(db, job, anchor + 1000)

    async def scenario(env):
        return (await hourly(env, "?provider=fake"), await hourly(env, "?provider=other"),
                await hourly(env, "?provider=nobody"), await hourly(env))

    fake, theirs, nobody, everyone = run_app(db, scenario)
    assert counts(fake, anchor) == {0: (1, 0, 0)}, "provider filter"
    assert counts(theirs, anchor) == {0: (0, 0, 1)}, "provider filter"
    assert counts(nobody, anchor) == {}, "provider filter"
    assert counts(everyone, anchor) == {0: (1, 0, 1)}, "provider filter"


@mutation(ROUTES, BUCKET, BUCKET_SHIFTED, expect="hour boundary")
@mutation(ROUTES, WINDOW_START, WINDOW_START_OPEN, expect="window start is inclusive")
@mutation(ROUTES, WINDOW_START, WINDOW_START_ROLLING, expect="window start is inclusive")
@mutation(ROUTES, BUCKET, BUCKET_BY_FINISH, expect="bucketed by created_at")
def test_rows_sit_in_the_hour_they_were_created_and_the_window_is_the_last_24_whole_hours(db):
    k = key(db)
    anchor = ANCHOR
    # fixed epochs: the last ms of an hour and the first ms of the next, the window's first ms and the ms before it,
    # 23 h and 24 h before the clock's instant, and a job from 30 hours ago
    for epoch in (anchor - 1, anchor, anchor - 23 * HOUR_MS, anchor - 23 * HOUR_MS - 1, anchor - 30 * HOUR_MS,
                  NOW - 23 * HOUR_MS, NOW - 24 * HOUR_MS):
        submitted_at(db, db.job(k, provider="fake"), epoch)
    early = done_job(db, k, ref="early")             # created 5 h ago, finished just now: counts where it was created
    submitted_at(db, early, anchor - 5 * HOUR_MS + 1234)

    async def scenario(env):
        return await hourly(env)

    got = run_app(db, scenario)
    assert counts(got, anchor) == {1: (0, 0, 1), 0: (0, 0, 1), 23: (0, 0, 2), 5: (1, 0, 0)}, (
        "hour boundary; window start is inclusive; bucketed by created_at; 24 whole hours, nothing older")
    assert len(got.json()["hours"]) == 24
