"""An account's history for the admin detail: the check measures its proxy's exit through the proxy only, a failed
check is stamped and kept among the errors, every outcome records the hour's worst health (bounded to 15 days), and a
job's account effects keep their error with the job (bounded to the newest 20)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import accountkit
import httpx
import jobkit
import pytest
from accountkit import KitEngine, make_env, new_account, run_as
from jobkit import CAPS, POOLS

from relay_core import history, net, pool
from relay_core.db import tx
from relay_core.jobs import store
from relay_core.jobs.checks import check_account
from relay_core.providers import (
    Accepted,
    Cooldown,
    Facts,
    Health,
    Pending,
    PollResult,
    Proxy,
    Rest,
    SubmitResult,
    Suspect,
)
from relay_core.testing.fake_provider import FakeEngine, sent
from relay_core.testing.mutation import mutation

CHECKS = "packages/relay-core/src/relay_core/jobs/checks.py"
HISTORY = "packages/relay-core/src/relay_core/history.py"
POOL = "packages/relay-core/src/relay_core/pool.py"
APPLY = "packages/relay-core/src/relay_core/jobs/apply.py"


@pytest.fixture(autouse=True)
def _fresh_probe():
    accountkit.EXIT_PROBED.clear()


def worker(db, scenario):
    return run_as(db.worker_dsn, scenario)


def hours(db, account_id) -> list[int]:
    return [r[0] for r in db.all("SELECT worst FROM account_health WHERE account_id = %s ORDER BY hour", account_id)]


def errors(db, account_id) -> list[tuple]:
    return db.all("SELECT code, job_id FROM account_errors WHERE account_id = %s ORDER BY id", account_id)


# ---- the check -----------------------------------------------------------------------------------------------------
@mutation(CHECKS, "if view.proxy is not None:      # never measured direct", "if True:      # never measured direct",
          expect="measured without a proxy")
@mutation(CHECKS, '"proxy_exit_ip = %(ip)s, proxy_latency_ms = %(ms)s, "', '"proxy_exit_ip = NULL, '
          'proxy_latency_ms = NULL, "', expect="the exit was not stored")
def test_a_check_measures_the_exit_through_the_accounts_proxy_and_never_measures_one_without_a_proxy(db):
    proxied = new_account(db, checked=True, proxy=("10.0.0.5", 8080))
    direct = new_account(db, checked=True)

    async def scenario(p):
        env = make_env(p, KitEngine())
        return await check_account(env, proxied), await check_account(env, direct)

    assert [r.outcome for r in worker(db, scenario)] == ["checked", "checked"]
    assert accountkit.EXIT_PROBED == [Proxy("10.0.0.5", 8080)], "measured without a proxy"
    got = db.one("SELECT proxy_exit_ip, proxy_latency_ms, proxy_exit_at IS NOT NULL FROM accounts WHERE id = %s",
                 proxied)
    assert got == (*accountkit.EXIT, True), "the exit was not stored: %r" % (got,)
    assert db.one("SELECT proxy_exit_ip, proxy_latency_ms, proxy_exit_at FROM accounts WHERE id = %s",
                  direct) == (None, None, None)


def test_an_exit_probe_that_crashes_never_fails_the_check(db):
    a = new_account(db, checked=True, proxy=("10.0.0.6", 8080))

    async def broken(proxy):
        raise RuntimeError("echo service down")

    async def scenario(p):
        return await check_account(make_env(p, KitEngine(), exit_probe=broken), a)

    assert worker(db, scenario).outcome == "checked"
    assert db.one("SELECT proxy_exit_ip, proxy_latency_ms FROM accounts WHERE id = %s", a) == (None, None)


@mutation(CHECKS, "check_failed_at = NULL, ", "", expect="a read clears check_failed_at")
@mutation(CHECKS, "    if error is not None:\n        await history.record_error(conn, account_id, error)\n", "",
          expect="the failure is not among the errors")
def test_a_failed_check_is_stamped_and_kept_among_the_errors_and_the_next_read_clears_the_stamp(db):
    a = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()

        async def boom(ctx):
            raise RuntimeError("upstream broke")
        engine.on_check = boom
        env = make_env(p, engine)
        first = await check_account(env, a)
        stamped = db.one("SELECT check_failed_at IS NOT NULL FROM accounts WHERE id = %s", a)[0]
        engine.on_check = None
        return first, stamped, await check_account(env, a)

    first, stamped, second = worker(db, scenario)
    assert (first.outcome, second.outcome, stamped) == ("failed", "checked", True)
    assert errors(db, a) == [("upstream broke", None)], "the failure is not among the errors"
    assert db.one("SELECT check_failed_at FROM accounts WHERE id = %s", a)[0] is None, "a read clears check_failed_at"


# ---- the health of each hour -------------------------------------------------------------------------------------
@mutation(HISTORY, "DO UPDATE SET worst = GREATEST(account_health.worst, excluded.worst)",
          "DO UPDATE SET worst = excluded.worst", expect="the worst of the hour wins")
@mutation(HISTORY, "WHEN a.state = 'dead' OR (a.proxy_host IS NOT NULL AND a.proxy_ok IS FALSE) THEN %(down)s ",
          "WHEN false THEN %(down)s ", expect="a dead account is down")
def test_each_check_records_the_hours_worst_health_and_rows_past_15_days_are_pruned(db):
    a = new_account(db, checked=True)
    dead = new_account(db, checked=True)
    db.run("INSERT INTO account_health (account_id, hour, worst) VALUES (%s, now() - interval '16 days', 2), "
           "(%s, date_trunc('hour', now(), 'UTC') - interval '3 days', 1)", a, a)

    async def scenario(p):
        engine = KitEngine()
        env = make_env(p, engine)
        engine.health = Health("alive")
        await check_account(env, a)
        first = db.one("SELECT worst FROM account_health WHERE account_id = %s ORDER BY hour DESC LIMIT 1", a)[0]
        until = datetime.now(UTC) + timedelta(hours=1)
        engine.health = Health("alive", effects=(Cooldown("busy upstream", until),))
        await check_account(env, a)
        db.run("UPDATE accounts SET cooldown_until = NULL WHERE id = %s", a)
        engine.health = Health("alive")
        await check_account(env, a)
        engine.health = Health("signed_out")
        await check_account(env, dead)
        return first

    assert worker(db, scenario) == 0
    assert hours(db, a) == [1, 1], "the worst of the hour wins (asleep, then usable again in the same hour)"
    assert hours(db, dead) == [2], "a dead account is down"


@mutation("packages/relay-core/src/relay_core/net.py", "proxy=None if transport else account_proxy_url(proxy)",
          "proxy=None", expect="the probe did not use the proxy")
def test_the_exit_probe_goes_through_the_proxy_url_and_answers_none_for_a_failure_or_a_non_address(monkeypatch):
    assert net.account_proxy_url(Proxy("10.0.0.1", 3128, "us:er:p@ss")) == "http://us:er%3Ap%40ss@10.0.0.1:3128"
    answers = iter([httpx.Response(200, text="198.51.100.4\n"), httpx.Response(200, text="<html>"),
                    httpx.Response(502, text="198.51.100.4")])

    def reply(request):
        assert request.url == httpx.URL(net.EXIT_IP_URL)
        return next(answers)

    async def three():
        mock = httpx.MockTransport(reply)
        return [await net.exit_address(Proxy("10.0.0.1", 3128), transport=mock) for _ in range(3)]

    got = asyncio.run(three())
    assert got[0][0] == "198.51.100.4" and isinstance(got[0][1], int)
    assert got[1:] == [(None, None), (None, None)]
    seen = {}

    class Client(httpx.AsyncClient):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="203.0.113.1")))

    monkeypatch.setattr(net.httpx, "AsyncClient", Client)
    assert asyncio.run(net.exit_address(Proxy("10.0.0.2", 80, "u:p")))[0] == "203.0.113.1"
    assert seen["proxy"] == "http://u:p@10.0.0.2:80" and seen["trust_env"] is False, "the probe did not use the proxy"


def test_a_switched_off_account_records_no_health(db):
    a = new_account(db, checked=True, state="disabled")

    async def scenario(p):
        async with tx(p) as conn:
            await history.record_health(conn, a)

    worker(db, scenario)
    assert hours(db, a) == []


# ---- a job's account effects -------------------------------------------------------------------------------------
@mutation(APPLY, "await pool.apply_effects(conn, job.account_id, effects, caps, pools, job_id=job.id)",
          "await pool.apply_effects(conn, job.account_id, effects, caps, pools)", expect="the job is not named")
@mutation(APPLY, "        await leases.release(conn, claim.lease_id)\n        await _health(conn, job)\n",
          "        await leases.release(conn, claim.lease_id)\n", expect="the submit recorded no health")
def test_a_submit_with_a_strike_keeps_its_error_with_the_job_and_records_the_hours_health(db):
    k, a = jobkit.key(db), jobkit.account(db)

    async def scenario(p):
        await jobkit.create(p, k, "x")
        got = await jobkit.claim(p)
        engine = FakeEngine()
        engine.script_submit("*", sent(SubmitResult(Accepted("ref-1", {}, Facts(5, "m", "reply")),
                                                    account=(Suspect("dola.content_refused"),))))
        await jobkit.submit(p, got, engine)
        return got.job_id

    job = jobkit.run(db, scenario)
    assert errors(db, a) == [("dola.content_refused", job)], "the job is not named"
    assert hours(db, a) == [0], "the submit recorded no health"


@mutation(POOL, "    if error and exists:\n", "    if False:\n", expect="a strike keeps its error")
@mutation(HISTORY, "WHERE account_id = %(a)s ORDER BY id DESC OFFSET %(n)s LIMIT 1)",
          "WHERE account_id = %(a)s ORDER BY id DESC OFFSET %(n)s * 100 LIMIT 1)", expect="at most 20 errors")
def test_effects_keep_one_error_each_a_rest_alone_none_and_only_the_newest_20(db):
    a = jobkit.account(db)
    job = uuid.uuid4()
    until = datetime.now(UTC) + timedelta(hours=1)

    async def scenario(p):
        async with tx(p) as conn:
            await pool.apply_effects(conn, a, [Suspect("strike one"), Cooldown("follows", until)], CAPS, POOLS,
                                     job_id=job)
            await pool.apply_effects(conn, a, [Cooldown("rate limited", until)], CAPS, POOLS)
            await pool.apply_effects(conn, a, [Rest("video", until, "no balance")], CAPS, POOLS)
        async with tx(p) as conn:
            got = [r[0] for r in await (await conn.execute(
                "SELECT code FROM account_errors WHERE account_id = %s ORDER BY id", (a,))).fetchall()]
            for n in range(25):
                await pool.apply_effects(conn, a, [Cooldown("again %d" % n, until)], CAPS, POOLS)
        return got

    assert jobkit.run(db, scenario) == ["strike one", "rate limited"], "a strike keeps its error"
    rows = errors(db, a)
    assert len(rows) == 20, "at most 20 errors per account: %d" % len(rows)
    assert rows[-1][0] == "again 24"


def test_a_job_that_is_still_rendering_records_no_health(db):
    k, a = jobkit.key(db), jobkit.account(db)
    token = uuid.uuid4()

    async def scenario(p):
        await jobkit.create(p, k, "x")
        got = await jobkit.claim(p)
        engine = FakeEngine()
        engine.script_submit("*", sent(SubmitResult(Accepted("ref-1", {}, Facts(5, "m", "reply")))))
        await jobkit.submit(p, got, engine)
        db.run("DELETE FROM account_health")
        db.run("UPDATE jobs SET poll_token = %s", token)
        return await store.apply_poll(p, got.job_id, token, PollResult(Pending("rendering", 60)), caps=CAPS,
                                      pools=POOLS)

    assert jobkit.run(db, scenario) == "waiting"
    assert hours(db, a) == []
