"""The core title sweep (contracts 7.5, v1.3 #29): which jobs it asks about, how it groups them, and that a title
that is already set is never overwritten."""

import asyncio

import pytest
from accountkit import KitEngine, KitSpec, beating, make_env, new_account, run_as

from relay_core.budget import BrowserBudget
from relay_core.jobs.checks import check_account
from relay_core.jobs.titles import apply_titles, clean_title, sweep_titles
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/jobs/titles.py"
CALL = 'try:\n        async with asyncio.timeout(env.command_hard_s("titles") * TIMEOUT_SHARE):'
CALL_AFTER_A_LEASE_CHECK = (
    'async with tx(env.db) as c:\n'
    '        if (await (await c.execute("SELECT count(*) FROM leases WHERE account_id = %s", '
    '(account_id,))).fetchone())[0]:\n            return None\n    try:\n'
    '        async with asyncio.timeout(env.command_hard_s("titles") * TIMEOUT_SHARE):')


def worker(db, scenario):
    return run_as(db.worker_dsn, scenario)


def titled_job(db, key, account, ref, *, title=None, sent_ago_s=300, age_s=600, status="done"):
    job = db.job(key, provider="fake")
    db.run("UPDATE jobs SET account_id = %s, external_ref = %s, title = %s, status = %s, "
           "sent_at = clock_timestamp() - make_interval(secs => %s), "
           "finished_at = CASE WHEN %s IN ('done', 'failed') THEN clock_timestamp() END, "
           "created_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
           account, ref, title, status, sent_ago_s, status, age_s, job)
    if status == "done":                       # a done job owns an output (invariant I11)
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, upstream_id) "
               "VALUES (%s, 'output', 0, %s, 'video/mp4', 1, %s)", job, "%s/output/0.mp4" % job, "up-" + ref)
    return job


def title_of(db, job):
    return db.one("SELECT title FROM jobs WHERE id = %s", job)[0]


def make_key(db):
    return db.key(("fake", "video", None, None))


@mutation(SRC, "AND external_ref IS NOT NULL AND account_id IS NOT NULL ",
          "AND external_ref IS NOT NULL AND account_id IS NOT NULL AND status <> 'done' ", expect="assert 2 == 3")
def test_the_sweep_names_untitled_jobs_including_done_ones_and_leaves_a_set_title_alone(db):
    key, a = make_key(db), new_account(db, checked=True)
    done = titled_job(db, key, a, "r-done", status="done")
    waiting = titled_job(db, key, a, "r-wait", status="waiting")
    empty = titled_job(db, key, a, "r-empty", title="", status="failed")
    mine = titled_job(db, key, a, "r-mine", title="client title")

    async def scenario(p):
        engine = KitEngine()
        engine.results["titles"] = lambda args: {"titles": {r: "named " + r for r in args["refs"]}}
        n = await sweep_titles(make_env(p, engine))
        return n, engine.commanded

    n, commanded = worker(db, scenario)
    assert n == 3
    assert [title_of(db, j) for j in (done, waiting, empty)] == ["named r-done", "named r-wait", "named r-empty"]
    assert title_of(db, mine) == "client title"
    assert [(k, sorted(a["refs"])) for k, a in commanded] == [("titles", ["r-done", "r-empty", "r-wait"])]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


def test_the_sweep_skips_a_job_that_is_too_young_too_old_or_has_no_ref(db):
    key, a = make_key(db), new_account(db, checked=True)
    titled_job(db, key, a, "young", sent_ago_s=10)
    titled_job(db, key, a, "old", age_s=25 * 3600)
    no_ref = db.job(key, provider="fake")
    db.run("UPDATE jobs SET account_id = %s, status = 'failed', error = 'x', finished_at = now(), "
           "sent_at = now() - interval '5 min' WHERE id = %s", a, no_ref)
    ok = titled_job(db, key, a, "fine")

    async def scenario(p):
        engine = KitEngine()
        engine.results["titles"] = {"titles": {"fine": "T", "young": "no", "old": "no"}}
        await sweep_titles(make_env(p, engine))
        return engine.commanded

    assert [a["refs"] for _, a in worker(db, scenario)] == [["fine"]]
    assert title_of(db, ok) == "T"


def test_the_sweep_groups_by_account_in_batches_of_fifty(db):
    key = make_key(db)
    a, b = new_account(db, checked=True), new_account(db, checked=True)
    for i in range(120):
        titled_job(db, key, a, "a%03d" % i)
    titled_job(db, key, b, "b000")

    async def scenario(p):
        engine = KitEngine()
        seen = []

        async def spy(ctx, kind, args):
            seen.append((ctx.account.id, len(args["refs"])))
        engine.on_command = spy
        engine.results["titles"] = {"titles": {}}
        await sweep_titles(make_env(p, engine))
        return seen

    got = worker(db, scenario)
    assert [x for x in got if x[0] == str(a)] == [(str(a), 50), (str(a), 50), (str(a), 20)]
    assert [x for x in got if x[0] == str(b)] == [(str(b), 1)]


def test_a_title_set_while_the_engine_was_asking_is_not_overwritten(db):
    key, a = make_key(db), new_account(db, checked=True)
    job = titled_job(db, key, a, "r1")

    async def scenario(p):
        engine = KitEngine()

        async def client_renames(ctx, kind, args):
            db.run("UPDATE jobs SET title = 'set by the admin' WHERE id = %s", job)
        engine.on_command = client_renames
        engine.results["titles"] = {"titles": {"r1": "from upstream"}}
        return await sweep_titles(make_env(p, engine))

    assert worker(db, scenario) == 0
    assert title_of(db, job) == "set by the admin"


STILL_EMPTY = "\"AND (title IS NULL OR title = '')\", (title, provider, str(ref))"


@mutation(SRC, STILL_EMPTY, "\"\", (title, provider, str(ref))", expect="assert 2 == 1")
def test_apply_sets_a_title_only_while_it_is_empty(db):
    key, a = make_key(db), new_account(db, checked=True)
    keep, empty = titled_job(db, key, a, "k", title="mine"), titled_job(db, key, a, "e", title="")

    async def scenario(p):
        from relay_core.db import tx
        async with tx(p) as conn:
            return await apply_titles(conn, "fake", {"k": "new", "e": "  one\nline  ", "missing": "x", "blank": " "})

    assert worker(db, scenario) == 1
    assert (title_of(db, keep), title_of(db, empty)) == ("mine", "one line")


def test_a_failing_account_does_not_stop_the_round_and_drain_does_not_stop_the_sweep(db):
    key = make_key(db)
    bad, good = (new_account(db, checked=True) for _ in range(2))
    jbad, jg = (titled_job(db, key, a, r) for a, r in ((bad, "rbad"), (good, "rg")))

    async def scenario(p):
        engine = KitEngine()

        async def fail_for_bad(ctx, kind, args):
            if ctx.account.id == str(bad):
                raise RuntimeError("upstream down")
        engine.on_command = fail_for_bad
        engine.results["titles"] = lambda args: {"titles": {r: "T-" + r for r in args["refs"]}}
        env = make_env(p, engine, settings=FakeSettings(KitSpec(), fake__claims_enabled=False))   # drained
        return await sweep_titles(env)

    assert worker(db, scenario) == 1
    assert [title_of(db, j) for j in (jbad, jg)] == [None, "T-rg"]


@mutation(SRC, CALL, CALL_AFTER_A_LEASE_CHECK, expect="assert (0, 1, 'checked') == (1, 1, 'checked')")
def test_a_title_sweep_takes_no_lease_and_no_token_so_it_never_blocks_a_check_of_the_same_account(db):
    key, a = make_key(db), new_account(db, checked=True)
    job = titled_job(db, key, a, "r1")

    async def scenario(p):
        engine, budget = KitEngine(), BrowserBudget(4)
        engine.results["titles"] = {"titles": {"r1": "T-r1"}}
        env = make_env(p, engine, spec=KitSpec(execution="browser"), budget=budget)
        in_check, release = asyncio.Event(), asyncio.Event()

        async def hold_the_check(ctx):
            in_check.set()
            await release.wait()
        engine.on_check = hold_the_check
        async with beating(env):
            check = asyncio.create_task(check_account(env, a))
            await asyncio.wait_for(in_check.wait(), 5)
            leases_during = (await asyncio.wait_for(_count(p, "SELECT count(*) FROM leases"), 5))
            swept = await asyncio.wait_for(sweep_titles(env), 5)       # the check still holds the lease and a token
            release.set()
            return swept, leases_during, (await check).outcome, engine.tokens, engine.commanded

    swept, leases_during, outcome, _, commanded = worker(db, scenario)
    assert (swept, leases_during, outcome) == (1, 1, "checked")
    assert title_of(db, job) == "T-r1" and commanded == [("titles", {"refs": ["r1"]})]


async def _count(p, sql):
    from relay_core.db import tx
    async with tx(p) as conn:
        return (await (await conn.execute(sql)).fetchone())[0]


def test_a_provider_without_titles_asks_nobody(db):
    key, a = make_key(db), new_account(db, checked=True)
    titled_job(db, key, a, "r")

    async def scenario(p):
        engine = KitEngine()
        return await sweep_titles(make_env(p, engine, spec=KitSpec(titles=False))), engine.commanded

    assert worker(db, scenario) == (0, [])


LONG = " ".join(["ab"] * 100)


@pytest.mark.parametrize("raw, want", [("  a  b\n c ", "a b c"), ("", None), (None, None), (LONG, LONG[:200])])
def test_clean_title_is_one_short_line(raw, want):
    assert clean_title(raw) == want


def test_a_planted_secret_in_a_title_is_redacted():
    assert "sk-" not in (clean_title("name sk-" + "A1b2C3d4" * 10) or "")
