"""Admin stats cards and live alerts: computed from columns at request time, self-clearing."""

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
FOREIGN_GROUP = "FROM accounts a GROUP BY provider"
LIKE_QUERY = "FROM accounts a WHERE label LIKE 'x%' GROUP BY provider"
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
    assert body["accounts"] == {"usable": 1, "available": 1, "dead": 0, "disabled": 1, "in_use": 0, "asleep": 0}
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


IN_USE_SUM = '"in_use": sum(r[4] for r in chosen)'
ASLEEP_SUM = '"asleep": sum(r[5] for r in chosen)'
IN_USE_COUNT = "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM leases WHERE account_id = a.id AND until > "
IN_USE_TWICE = ("(SELECT count(*) FROM leases JOIN accounts x ON x.id = account_id "
                "WHERE x.provider = a.provider AND until > ")
LEASE_LIVE = "AND until > clock_timestamp())), "
COOLING = "count(*) FILTER (WHERE cooldown_until > clock_timestamp() "
RESTING = "OR EXISTS (SELECT 1 FROM account_pools c"
REST_LIVE = "c.rest_until > clock_timestamp())) "


@mutation(ROUTES, LEASE_LIVE, "AND true)), ", expect="in use: all")
@mutation(ROUTES, "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM leases",
          "count(*) FILTER (WHERE state = 'available' AND EXISTS (SELECT 1 FROM leases", expect="in use: all")
@mutation(ROUTES, IN_USE_COUNT + "clock_timestamp())), ", IN_USE_TWICE + "clock_timestamp()), ", expect="in use: all")
@mutation(ROUTES, COOLING, "count(*) FILTER (WHERE cooldown_until IS NOT NULL ", expect="asleep: all")
@mutation(ROUTES, COOLING, "count(*) FILTER (WHERE state = 'available' AND cooldown_until > clock_timestamp() ",
          expect="asleep: all")
@mutation(ROUTES, RESTING, "OR FALSE AND EXISTS (SELECT 1 FROM account_pools c", expect="asleep: all")
@mutation(ROUTES, REST_LIVE, "c.rest_until IS NOT NULL)) ", expect="asleep: all")
@mutation(ROUTES, IN_USE_SUM, '"in_use": sum(r[4] for r in accounts.values())', expect="in use: fake")
@mutation(ROUTES, ASLEEP_SUM, '"asleep": sum(r[5] for r in accounts.values())', expect="asleep: fake")
@mutation(ROUTES, IN_USE_SUM, '"in_use": sum(r[5] for r in chosen)', expect="in use: all")
def test_the_cards_count_the_accounts_the_list_marks_in_use_and_asleep(db):
    """The card and the rows count the same accounts: in use = any live lease, whatever the state, once however many
    leases; asleep = an own cooldown or a resting pool still in the future, whatever the state."""
    def lease(account_id, seconds):
        db.run("INSERT INTO leases (account_id, purpose, owner, until) "
               "VALUES (%s, 'check', 'test', clock_timestamp() + make_interval(secs => %s))", account_id, seconds)

    def rest(account_id, seconds):
        db.run("UPDATE account_pools SET rest_until = clock_timestamp() + make_interval(secs => %s) "
               "WHERE account_id = %s", seconds, account_id)

    lease(account(db), 600)                              # in use
    twice = account(db)
    lease(twice, 600)
    lease(twice, 900)                                    # two live leases: still one account in use
    lease(account(db), -600)                             # a lease that ran out: not in use
    account(db, cooldown_s=600)                          # asleep
    account(db, cooldown_s=300)                          # asleep
    account(db, cooldown_s=-600)                         # a cooldown that ended: not asleep
    lease(account(db, state="disabled", cooldown_s=600), 600)    # a disabled account: in use and asleep
    lease(account(db, state="dead", cooldown_s=600), 600)        # a dead account: in use and asleep
    lease(account(db, cooldown_s=600), 600)              # in use and asleep
    account(db)                                          # idle
    rest(account(db, left=3), 600)                       # a pool that rests: asleep
    rest(account(db, left=3, cooldown_s=600), 600)       # a cooldown and a resting pool: one account asleep
    rest(account(db, left=3), -600)                      # a pool whose rest ended: not asleep
    elsewhere = account(db, cooldown_s=600)              # another provider: in use and asleep there
    db.run("UPDATE accounts SET provider = 'other' WHERE id = %s", elsewhere)
    lease(elsewhere, 600)

    async def scenario(env):
        return await stats(env), await stats(env, "?provider=fake"), await stats(env, "?provider=other"), await stats(
            env, "?provider=nobody")

    everything, fake, other, nobody = (r.json()["accounts"] for r in run_app(db, scenario))
    problems = []                    # every failed check is named, so each marker finds its own
    for who, got, want in (("all", everything, (6, 8)), ("fake", fake, (5, 7)), ("other", other, (1, 1)),
                           ("nobody", nobody, (0, 0))):
        if got["in_use"] != want[0]:
            problems.append("in use: %s %d, want %d" % (who, got["in_use"], want[0]))
        if got["asleep"] != want[1]:
            problems.append("asleep: %s %d, want %d" % (who, got["asleep"], want[1]))
    assert not problems, "; ".join(problems)


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


DEAD_ONLY = "WHERE state = 'dead' ORDER BY label, id"


@mutation(ROUTES, DEAD_ONLY, "WHERE state IN ('dead', 'disabled') ORDER BY label, id", expect="disabled: not shown")
@mutation(ROUTES, DEAD_ONLY, "WHERE state = 'dead' AND false ORDER BY label, id", expect="dead and enabled: shown")
@mutation(ROUTES, '"%s has a dead session" % (label or str(account_id)[:8])', '"%s has a dead session" % label',
          expect="without a label it is named by its id start")
def test_a_dead_account_is_a_live_condition_until_it_recovers_is_disabled_or_is_removed(db):
    for _ in range(3):
        account(db)                                              # enough usable accounts: no accounts_low
    dead = account(db, state="dead")
    db.run("UPDATE accounts SET label = 'acct-n11-08' WHERE id = %s", dead)
    nameless = account(db, state="dead")
    off = account(db, state="disabled")
    db.run("UPDATE accounts SET label = 'acct-off' WHERE id = %s", off)

    async def scenario(env):
        both = await stats(env)
        db.run("UPDATE accounts SET state = 'disabled' WHERE id = %s", nameless)
        one = await stats(env)
        db.run("UPDATE accounts SET state = 'available' WHERE id = %s", dead)
        recovered = await stats(env)
        db.run("UPDATE accounts SET state = 'dead' WHERE id = %s", dead)
        db.run("DELETE FROM accounts WHERE id = %s", dead)
        removed = await stats(env)
        return both, one, recovered, removed

    both, one, recovered, removed = run_app(db, scenario)
    dead_rows = lambda r: [(a["severity"], a["message"], a["provider"])  # noqa: E731
                           for a in r.json()["alerts"] if a["kind"] == "account_dead"]
    assert not [r for r in dead_rows(both) if "acct-off" in r[1]], "disabled: not shown, %r" % dead_rows(both)
    assert ("error", "acct-n11-08 has a dead session", "fake") in dead_rows(both), \
        "dead and enabled: shown, %r" % dead_rows(both)
    assert ("error", "%s has a dead session" % str(nameless)[:8], "fake") in dead_rows(both), \
        "without a label it is named by its id start: %r" % dead_rows(both)
    assert len(dead_rows(both)) == 2, "one condition per dead account: %r" % dead_rows(both)
    assert dead_rows(one) == [("error", "acct-n11-08 has a dead session", "fake")], "disabled later: gone"
    assert dead_rows(recovered) == [], "recovered: gone"
    assert dead_rows(removed) == [], "removed: gone"
    assert kinds(both) == ["account_dead", "account_dead"], "a dead account raises no other condition"


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
        db.run("UPDATE accounts SET state = 'disabled' WHERE id = (SELECT id FROM accounts LIMIT 1)")
        one = await stats(env)                     # 1 usable
        db.run("UPDATE accounts SET state = 'available'")
        await env.settings.set("alert_accounts_low", 2, by="test")
        at_limit = await stats(env)
        await env.settings.set("alert_accounts_low", 0, by="test")
        return default, one, at_limit, await stats(env)

    default, one, at_limit, off = run_app(db, scenario)
    assert kinds(default) == ["accounts_low"], "accounts_low"
    assert kinds(at_limit) == [] and kinds(off) == []
    # the count agrees with its noun: one account, two accounts
    assert default.json()["alerts"][0]["message"] == "fake has 2 usable accounts (alert below 3)"
    assert one.json()["alerts"][0]["message"] == "fake has 1 usable account (alert below 3)"


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


# ---- the pool panel -------------------------------------------------------------------------------------------------
POOL = "packages/relay-core/src/relay_core/pool.py"
POOL_ACCOUNTS = "FROM accounts WHERE state = 'available' AND"
POOL_WITH_DEAD = "FROM accounts WHERE state <> 'disabled' AND"
POOL_WITH_DISABLED = "FROM accounts WHERE state <> 'dead' AND"
POOL_ACCOUNTS_ANY_PROVIDER = "WHERE state = 'available' AND (%(p)s::text IS NULL OR provider = %(p)s)"
POOL_ACCOUNTS_ANY_PROVIDER_OFF = "WHERE state = 'available' AND true"
REFUNDED_OUT = '"AND refunded_at IS NULL AND at >= %s ORDER BY at", (ids, since))'
REFUNDED_IN = '"AND at >= %s ORDER BY at", (ids, since))'
REPORT_WINS = "    if reported is not None and now < _refill_of_report(cfg, reported):\n"
REPORT_ALWAYS = "    if reported is not None:\n"
UNKNOWN_LEFT = "if state.standing.left is None or budget is None:"
UNKNOWN_LEFT_OFF = "if False:"
SEEDED_POOLS = "for pool_name in cfgs if provider in (None, name) else ():"
NO_SEEDED_POOLS = "for pool_name in ():"
PROXY_FAILED = "AND proxy_ok = false"
PROXY_SET = "AND proxy_ok IS NOT NULL"
PROXY_ANY_PROVIDER = "FROM accounts WHERE (%(p)s::text IS NULL OR provider = %(p)s)"
PROXY_ANY_PROVIDER_OFF = "FROM accounts WHERE true"
PROXY_AVAILABLE = "state = 'available' AND proxy_ok = false"
PROXY_ANY_STATE = "true AND proxy_ok = false"
FULL_CHECK = "WHERE kind = 'check_all' AND"
ANY_KIND = "WHERE kind <> 'zzz' AND"
CHECK_DONE = "status = 'done' AND (%(p)s::text IS NULL OR provider = %(p)s)"
CHECK_NOT_QUEUED = "status <> 'queued' AND (%(p)s::text IS NULL OR provider = %(p)s)"
CHECK_PROVIDER = "status = 'done' AND (%(p)s::text IS NULL OR provider = %(p)s)"
CHECK_PROVIDER_OFF = "status = 'done' AND true"
LATER = "clock_timestamp() + interval '1 hour'"          # a refill still ahead
EARLIER = "clock_timestamp() - interval '1 hour'"        # a refill that has passed
YESTERDAY = "clock_timestamp() - interval '2 days'"


def reported(db, account, pool: str, left, refill: str = LATER) -> None:
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at) VALUES (%s, %s, %s, "
           "clock_timestamp() - interval '1 second', " + refill + ")", account, pool, left)


def charged(db, k, account, pool: str, amount: int, when: str = "clock_timestamp()", refunded: bool = False) -> None:
    job = done_job(db, k, account, ref=uuid.uuid4().hex)
    db.run("UPDATE jobs SET taken_at = clock_timestamp(), account_cost = %s WHERE id = %s", amount, job)
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, at, refunded_at) "
           "VALUES (%s, %s, %s, %s, " + when + ", CASE WHEN %s THEN now() END)", account, job, pool, amount, refunded)


def command(db, provider: str, kind: str, status: str, finished_ms: int | None) -> None:
    db.run("INSERT INTO commands (provider, kind, status, finished_at) VALUES "
           "(%s, %s, %s, CASE WHEN %s::bigint IS NULL THEN NULL ELSE to_timestamp(%s::double precision / 1000) END)",
           provider, kind, status, finished_ms, finished_ms)


async def pool_stats(env, query: str = ""):
    return await env.client.get("/admin/stats/pool" + query, headers=await env.admin_headers())


def plant_pool(db):
    """`fake.pools`: video is 4 a day for a free account, images have no window. Six available accounts count, a dead
    and a disabled one do not. Video left: a report 3, a charge of 1 today (yesterday's does not count) 3, a refunded
    charge 4, nothing yet 4, a report whose refill passed 4, a pro account (no pro budget: the free 4) 4 = 22 of 24.
    Every image pool is unknown (no window)."""
    k = key(db)
    ok, charged_ok, refunded, fresh, stale, pro = [account(db) for _ in range(6)]
    dead, disabled = account(db, state="dead"), account(db, state="disabled")
    reported(db, ok, "video", 3)                                    # 3, not the budget's 4: the report wins
    charged(db, k, charged_ok, "video", 1)
    charged(db, k, charged_ok, "video", 1, YESTERDAY)               # yesterday's charge is not today's
    charged(db, k, refunded, "video", 3, refunded=True)             # a refunded charge is not spent
    reported(db, stale, "video", 0, EARLIER)                        # its refill has passed: the budget again
    db.run("UPDATE accounts SET tier = 'pro' WHERE id = %s", pro)
    reported(db, dead, "video", 2)
    reported(db, disabled, "video", 2)
    reported(db, ok, "image", 5)
    db.run("UPDATE accounts SET proxy_ok = false WHERE id IN (%s, %s, %s)", ok, dead, disabled)
    db.run("UPDATE accounts SET proxy_ok = true WHERE id = %s", charged_ok)
    db.run("INSERT INTO accounts (provider, credential_enc, proxy_ok) VALUES ('other', '\\x01', false)")
    assert fresh
    command(db, "fake", "check_all", "done", ANCHOR - 2 * HOUR_MS)
    command(db, "fake", "check_all", "done", ANCHOR - HOUR_MS)
    command(db, "fake", "check_all", "failed", ANCHOR - HOUR_MS // 2)
    command(db, "fake", "check_all", "queued", None)
    command(db, "fake", "check", "done", ANCHOR - 600_000)
    command(db, "other", "check_all", "done", ANCHOR - 300_000)


@mutation(ROUTES, POOL_ACCOUNTS, POOL_WITH_DEAD, expect="dead and disabled accounts")
@mutation(ROUTES, POOL_ACCOUNTS, POOL_WITH_DISABLED, expect="dead and disabled accounts")
@mutation(POOL, REFUNDED_OUT, REFUNDED_IN, expect="left and budget")
@mutation(POOL, REPORT_WINS, REPORT_ALWAYS, expect="left and budget")
@mutation(ROUTES, UNKNOWN_LEFT, UNKNOWN_LEFT_OFF, expect="pool figures")
@mutation(ROUTES, PROXY_FAILED, PROXY_SET, expect="proxy checks failed")
@mutation(ROUTES, PROXY_AVAILABLE, PROXY_ANY_STATE, expect="proxy checks failed")
@mutation(ROUTES, FULL_CHECK, ANY_KIND, expect="last full check")
@mutation(ROUTES, CHECK_DONE, CHECK_NOT_QUEUED, expect="last full check")
def test_the_pool_panel_counts_points_unknowns_proxy_failures_and_the_last_full_check(db):
    plant_pool(db)

    async def scenario(env):
        return await pool_stats(env, "?provider=fake")

    r = run_app(db, scenario)
    assert r.status_code == 200, "pool figures: " + r.text
    body = r.json()
    video, image = body["pools"]
    assert (video["provider"], video["pool"], image["pool"]) == ("fake", "video", "image"), (
        "pool figures: one entry per pool, spec order")
    assert (video["left"], video["budget"]) == (22, 24), (
        "left and budget exclude dead and disabled accounts: reports, today's unrefunded charges and the budget")
    assert video["unknown"] == 0, "unknown points: a tier with no budget takes the free one, dead and disabled add none"
    assert video["unit"] == "jobs"
    assert image == {"provider": "fake", "pool": "image", "unit": "jobs", "left": 0, "budget": 0, "unknown": 6}, (
        "no window: every available account is unknown")
    assert "drift" not in video, "spend is computed from the charges: nothing can drift"
    assert body["proxy_failed"] == 1, "proxy checks failed: available accounts only, a dead or disabled one is not"
    assert body["last_full_check_ms"] == ANCHOR - HOUR_MS, "last full check: the newest finished check_all"


def test_stats_pool_never_sums_two_providers(db):
    for _ in range(2):
        reported(db, account(db), "video", 3)
    other_accounts = [account(db) for _ in range(3)]
    db.run("UPDATE accounts SET provider = 'other' WHERE id = ANY(%s)", other_accounts)
    for who in other_accounts:
        reported(db, who, "video", 2)

    async def scenario(env):
        env.add_provider(FakeSpec("other"))
        return await pool_stats(env), await pool_stats(env, "?provider=other")

    everyone, other = run_app(db, scenario)
    rows = {(r["provider"], r["pool"]): r for r in everyone.json()["pools"]}
    assert set(rows) == {("fake", "video"), ("fake", "image"), ("other", "video"), ("other", "image")}, (
        "one row per provider and pool, never a sum across providers")
    assert (rows["fake", "video"]["left"], rows["fake", "video"]["budget"]) == (6, 8)
    assert (rows["other", "video"]["left"], rows["other", "video"]["budget"]) == (6, 12)
    assert [r["provider"] for r in other.json()["pools"]] == ["other", "other"]


@mutation(ROUTES, POOL_ACCOUNTS_ANY_PROVIDER, POOL_ACCOUNTS_ANY_PROVIDER_OFF, expect="provider filter")
@mutation(ROUTES, PROXY_ANY_PROVIDER, PROXY_ANY_PROVIDER_OFF, expect="provider filter")
@mutation(ROUTES, CHECK_PROVIDER, CHECK_PROVIDER_OFF, expect="provider filter")
def test_the_pool_panel_follows_the_provider_filter(db):
    plant_pool(db)

    async def scenario(env):
        return (await pool_stats(env, "?provider=other"), await pool_stats(env),
                await pool_stats(env, "?provider=fake"))

    other, everyone, fake = run_app(db, scenario)
    assert other.json() == {"pools": [], "proxy_failed": 1, "last_full_check_ms": ANCHOR - 300_000}, (
        "provider filter: a provider nobody runs has no pools, but its own failures and checks")
    assert everyone.json()["proxy_failed"] == 2 and everyone.json()["last_full_check_ms"] == ANCHOR - 300_000, (
        "provider filter: all providers")
    assert everyone.json()["pools"] == fake.json()["pools"], "provider filter: an unknown provider adds nothing"
    assert (fake.json()["proxy_failed"], fake.json()["last_full_check_ms"]) == (1, ANCHOR - HOUR_MS), (
        "provider filter: the other provider's failures and checks are not counted")


@mutation(ROUTES, SEEDED_POOLS, NO_SEEDED_POOLS, expect="empty database")
def test_an_empty_pool_gives_zero_figures_for_every_pool_and_no_last_check(db):
    async def scenario(env):
        return await pool_stats(env)

    assert run_app(db, scenario).json() == {
        "pools": [{"provider": "fake", "pool": "video", "unit": "jobs", "left": 0, "budget": 0, "unknown": 0},
                  {"provider": "fake", "pool": "image", "unit": "jobs", "left": 0, "budget": 0, "unknown": 0}],
        "proxy_failed": 0, "last_full_check_ms": None}, "empty database: zero figures for every pool"
