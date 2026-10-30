"""The claim (I6, I9): drain, heads per cost, affordability before any browser, LRU, unchecked
accounts, live-lease slots, reservations, and the lease inserted with the claim."""

import asyncio
import dataclasses
from contextvars import Context

import pytest
from jobkit import POOLS, PROVIDER, SETTINGS, account, claim, create, key, new_job, run

from relay_core import failpoints, leases, pool
from relay_core.db import tx
from relay_core.jobs import store
from relay_core.jobs.claim import send_gap_s
from relay_core.testing.fake_provider import FakeSettings, FakeSpec
from relay_core.testing.mutation import mutation

K = "packages/relay-core/src/relay_core/jobs/claim.py"
TWO_SLOTS = FakeSpec(account_slots=2).caps
BROWSER = FakeSpec(execution="browser").caps
AFFORD = ("if not pool.can_afford(cap.left_n, held, cost):\n                    continue\n"
          "                afford = None if cap.left_n is None else cap.left_n - held")
UNKNOWN_IS_ZERO = "afford = None if cap.left_n is None else cap.left_n - held"
DRAIN_CHECK = 'if not settings.get(provider + ".claims_enabled"):'
LRU = "a.last_used_at NULLS FIRST, a.id"
UNTRIED = "ORDER BY a.id = ANY(%(tried)s::uuid[]), "
CHECKED = "AND a.last_checked_at IS NOT NULL"


class Token:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


def lease_row(db, claimed):
    return db.one("SELECT purpose, account_id, job_id, pool, until > clock_timestamp(), hard_until - clock_timestamp() "
                  "FROM leases WHERE id = %s", claimed.lease_id)


@mutation(K, "hard_s=timeout_s + leases.APPLY_MARGIN_S,", "hard_s=timeout_s,",
          expect="the job lease's hard bound is the submit timeout plus the apply margin")
def test_a_claim_moves_the_job_to_submitting_and_inserts_its_lease_in_the_same_transaction(db):
    k, acct = key(db), account(db)

    async def scenario(p):
        made = await create(p, k, "x", submit_timeout_s=45)
        return made, await claim(p)

    made, got = run(db, scenario)
    assert got.job_id == made.job_id and got.account_id == acct and got.pool == "video"
    assert db.one("SELECT status, account_id, claims, %s = ANY(tried), attempts FROM jobs WHERE id = %s",
                  acct, got.job_id) == ("submitting", acct, 1, True, 0)
    purpose, lease_account, lease_job, pool_name, live, hard_left = lease_row(db, got)
    assert (purpose, lease_account, lease_job, pool_name, live) == ("job", acct, got.job_id, "video", True)
    margin = leases.APPLY_MARGIN_S
    assert 40 + margin < hard_left.total_seconds() <= 45 + margin, \
        "the job lease's hard bound is the submit timeout plus the apply margin"
    assert db.one("SELECT last_used_at IS NOT NULL FROM accounts WHERE id = %s", acct)[0]
    assert db.one("SELECT reported_left, rest_until FROM account_pools WHERE account_id = %s AND pool = 'video'",
                  acct) == (None, None)                              # created by the claim; charged later


def test_nothing_to_claim_and_nothing_claimable(db):
    k = key(db)

    async def scenario(p):
        assert await claim(p) is None                                 # no job
        await create(p, k, "x")
        assert await claim(p) is None                                 # no account

    run(db, scenario)
    assert db.one("SELECT status FROM jobs")[0] == "queued"


@pytest.mark.parametrize("kind", ["dead", "disabled", "unchecked", "cooling", "pool_resting"])
@mutation(K, CHECKED, "AND TRUE", expect="assert run(db, scenario) is None")
def test_accounts_that_may_not_be_used_are_skipped(db, kind):
    k = key(db)
    acct = account(db, state=kind if kind in ("dead", "disabled") else "available", checked=kind != "unchecked",
                   cooldown_s=60 if kind == "cooling" else None)
    if kind == "pool_resting":
        db.run("INSERT INTO account_pools (account_id, pool, rest_until) VALUES "
               "(%s, 'video', clock_timestamp() + interval '1 hour')", acct)

    async def scenario(p):
        await create(p, k, "x")
        return await claim(p)

    assert run(db, scenario) is None


def test_a_resting_pool_does_not_stop_another_pool_of_the_same_account(db):
    k = key(db)
    acct = account(db)
    db.run("INSERT INTO account_pools (account_id, pool, rest_until) VALUES "
           "(%s, 'video', clock_timestamp() + interval '1h')", acct)

    async def scenario(p):
        await create(p, k, "v")
        image = await create(p, k, "i", model=PROVIDER + "-image")
        return image, await claim(p)

    image, got = run(db, scenario)
    assert got.job_id == image.job_id and got.pool == "image"


# ---- the oldest head across pools --------------------------------------------------------------------------
HEAD_ORDER = "heads.sort(key=lambda h: (h[0], h[1]))"


@mutation(K, HEAD_ORDER, "heads.sort(key=lambda h: 0)",
          expect="assert got.job_id == image.job_id and got.pool == \"image\"")
def test_an_older_image_job_beats_a_newer_video_job_when_one_account_can_take_either(db):
    k, _ = key(db), account(db)

    async def scenario(p):
        image = await create(p, k, "older image", model=PROVIDER + "-image")
        video = await create(p, k, "newer video")
        return image, video, await claim(p)

    image, video, got = run(db, scenario)
    assert got.job_id == image.job_id and got.pool == "image"
    assert db.one("SELECT status FROM jobs WHERE id = %s", video.job_id)[0] == "queued"


def test_an_unaffordable_older_head_never_blocks_a_cheaper_newer_job_of_another_pool(db):
    k, _ = key(db), account(db, left=1)

    async def scenario(p):
        dear = await create(p, k, "dear video", account_cost=4)
        image = await create(p, k, "newer image", model=PROVIDER + "-image")
        return dear, image, await claim(p)

    dear, image, got = run(db, scenario)
    assert got.job_id == image.job_id
    assert db.one("SELECT status FROM jobs WHERE id = %s", dear.job_id)[0] == "queued"


# ---- the lock order: the account row first, and a claim never waits ----------------------------------------------
ACCOUNTS = "packages/relay-core/src/relay_core/db/accounts.py"
WAITS = '    skip = " SKIP LOCKED" if skip_locked else ""\n'
WAITS_MUTATED = '    skip = ""\n'


@mutation(ACCOUNTS, WAITS, WAITS_MUTATED, expect="the claim waited for the account a wake holds")
def test_a_claim_skips_an_account_a_wake_holds_instead_of_waiting_and_runs_once_the_wake_is_over(db):
    k, acct = key(db), account(db)
    db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video'), (%s, 'image')", acct, acct)

    async def scenario(p):
        reached, gate = asyncio.Event(), asyncio.Event()

        async def pause():
            reached.set()
            await gate.wait()

        failpoints.arm("pool.wake.account_locked", pause)

        async def wake():
            async with tx(p) as conn:
                await pool.wake_account(conn, acct)

        try:
            waking = asyncio.create_task(wake())
            await asyncio.wait_for(reached.wait(), 5)
            await create(p, k, "x")
            claiming = asyncio.create_task(claim(p))
            await asyncio.sleep(1.5)                       # past Postgres's deadlock timeout
            waited = not claiming.done()
            gate.set()
            results = await asyncio.gather(waking, claiming, return_exceptions=True)
        finally:
            failpoints.disarm("pool.wake.account_locked")
            gate.set()
        return waited, results, await claim(p)

    waited, results, after = run(db, scenario)
    assert not waited, "the claim waited for the account a wake holds"
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert after is not None                               # the claim runs once the wake is over


@mutation(ACCOUNTS, WAITS, WAITS_MUTATED, expect="the second claim waited for the first")
def test_two_claims_on_one_account_never_wait_for_each_other_the_second_skips(db):
    k, acct = key(db), account(db)

    async def scenario(p):
        await create(p, k, "x")
        async with tx(p) as first:                          # the first claim holds the account and its capacity row
            await pool.lock_capacity(first, acct, POOLS["video"])
            second = asyncio.create_task(claim(p), context=Context())
            done, _ = await asyncio.wait([second], timeout=2)
            if not done:
                second.cancel()
                pytest.fail("the second claim waited for the first")
            return second.result()

    assert run(db, scenario) is None
    assert db.one("SELECT status FROM jobs")[0] == "queued"      # skipped, not stolen or half-taken


# ---- affordability before any browser (I6) ---------------------------------------------------------------------
@mutation(K, AFFORD, "afford = None", expect="assert first.job_id == cheap.job_id")
def test_a_head_no_account_can_afford_does_not_stall_the_cheaper_jobs_behind_it(db):
    k = key(db)
    for _ in range(2):
        account(db, left=2)

    async def scenario(p):
        dear = await create(p, k, "dear", account_cost=4)            # the oldest job
        cheap = await create(p, k, "cheap", account_cost=2)
        tokens = []

        def take():
            tokens.append(Token())
            return tokens[-1]

        first = await claim(p, take_token=take)
        return dear, cheap, first, tokens

    dear, cheap, first, tokens = run(db, scenario)
    assert first.job_id == cheap.job_id
    assert db.one("SELECT status FROM jobs WHERE id = %s", dear.job_id)[0] == "queued"
    assert len(tokens) == 1                                          # a token only for the job that can run


@mutation(K, AFFORD, "afford = None", expect="assert run(db, scenario) is None and taken == []")
def test_no_browser_token_is_taken_when_no_account_can_afford_any_head(db):
    k = key(db)
    account(db, left=1)
    taken = []

    async def scenario(p):
        await create(p, k, "dear", account_cost=2)
        return await claim(p, take_token=lambda: taken.append(1) or Token())

    assert run(db, scenario) is None and taken == []


@mutation(K, UNKNOWN_IS_ZERO, "afford = 0 if cap.left_n is None else cap.left_n - held",
          expect="assert got is not None and db.one(\"SELECT reported_left FROM account_pools WHERE pool")
def test_an_unknown_balance_allows_any_cost(db):
    k = key(db)
    account(db, pool_name="image")                                     # the image pool has no window: unknown

    async def scenario(p):
        await create(p, k, "big", model=PROVIDER + "-image", account_cost=1000)
        return await claim(p)

    got = run(db, scenario)
    assert got is not None and db.one("SELECT reported_left FROM account_pools WHERE pool = 'image'")[0] is None


def test_a_reservation_counts_against_the_account_until_its_job_is_charged(db):
    k = key(db)
    only = account(db, left=2)

    async def scenario(p):
        await create(p, k, "a", account_cost=2)
        await create(p, k, "b", account_cost=2)
        first = await claim(p, caps=TWO_SLOTS)
        second = await claim(p, caps=TWO_SLOTS)                       # a slot is free, but 2 - 2 reserved < 2
        return first, second

    first, second = run(db, scenario)
    assert first.account_id == only and second is None


def test_pool_less_jobs_need_no_capacity(db):
    k = key(db)
    account(db, left=0)

    async def scenario(p):
        job = new_job(k, "x")
        await store.create_job(p, dataclasses.replace(job, plan=dataclasses.replace(job.plan, pool=None)), SETTINGS)
        return await claim(p)

    assert run(db, scenario) is not None


# ---- which account ---------------------------------------------------------------------------------------------
@mutation(K, LRU, "a.last_used_at DESC NULLS FIRST, a.id", expect="assert run(db, scenario) == [new, old, recent]")
def test_the_least_recently_used_account_goes_first_and_a_new_one_before_any(db):
    k = key(db)
    new = account(db)
    old = account(db, used_ago_s=300)
    recent = account(db, used_ago_s=100)

    async def scenario(p):
        for i in range(3):
            await create(p, k, str(i))
        return [(await claim(p)).account_id for _ in range(3)]

    assert run(db, scenario) == [new, old, recent]


@mutation(K, UNTRIED, "ORDER BY ", expect="assert run(db, scenario).account_id == other")
def test_an_account_the_job_already_tried_is_the_last_choice(db):
    k = key(db)
    tried = account(db)                                                # never used: first by LRU alone
    other = account(db, used_ago_s=100)

    async def scenario(p):
        made = await create(p, k, "x")
        db.run("UPDATE jobs SET tried = ARRAY[%s]::uuid[] WHERE id = %s", tried, made.job_id)
        return await claim(p)

    assert run(db, scenario).account_id == other


def test_jobs_are_claimed_oldest_first(db):
    k = key(db)
    account(db)
    account(db)

    async def scenario(p):
        ids = [(await create(p, k, str(i))).job_id for i in range(3)]
        return ids, [(await claim(p)).job_id for _ in range(2)]

    ids, claimed = run(db, scenario)
    assert claimed == ids[:2]


def test_an_account_serves_up_to_its_live_lease_slots_and_an_expired_lease_frees_one(db):
    k = key(db)
    account(db)

    async def scenario(p):
        for i in range(3):
            await create(p, k, str(i))
        first = await claim(p)
        assert await claim(p) is None                                  # one slot, held by a live lease
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", first.lease_id)
        return await claim(p)                                          # the expired one does not count

    assert run(db, scenario) is not None


def test_a_browser_provider_skips_an_account_whose_unknown_job_is_due_for_a_reconcile(db):
    k = key(db)
    busy, free = account(db), account(db, used_ago_s=50)
    db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion,"
           " after_sent, submit_timeout_s, price_key, price, account_id, next_poll_at) VALUES "
           "(gen_random_uuid(), %s, %s, 'generate', 'video', 'm', 'x', 'm', 'unknown', 'detach', 'reconcile', 60, "
           "'fake-video', 0, %s, clock_timestamp() - interval '1 second')", PROVIDER, k, busy)

    async def scenario(p):
        await create(p, k, "a")
        await create(p, k, "b")
        return (await claim(p, caps=BROWSER)).account_id, await claim(p, caps=BROWSER), (await claim(p)).account_id

    first, second, http_choice = run(db, scenario)
    assert first == free and second is None                           # the busy account is out for a browser provider
    assert http_choice == busy                                         # an HTTP provider reconciles without a browser


# ---- drain, deadline, tokens, concurrency ----------------------------------------------------------------------
@mutation(K, DRAIN_CHECK, "if False:", expect="assert await claim(p, settings=drained) is None")
def test_a_drained_provider_claims_nothing_until_it_is_resumed(db):
    k = key(db)
    account(db)
    drained = FakeSettings(fake__claims_enabled=False)

    async def scenario(p):
        made = await create(p, k, "x")
        assert await claim(p, settings=drained) is None
        assert (await claim(p)).job_id == made.job_id

    run(db, scenario)


SEND_GAP = "   AND (a.last_used_at IS NULL OR a.last_used_at <= clock_timestamp() - make_interval(secs => %(gap)s))"


@mutation(K, SEND_GAP, "   AND (%(gap)s IS NOT NULL)", expect="claimed inside the send gap")
@mutation(K, '        gap = settings.get(provider + ".send_gap_s")', "        gap = 0",
          expect="claimed inside the send gap")
def test_an_account_claimed_less_than_the_send_gap_ago_is_not_claimed_and_no_gap_is_declared_by_default(db):
    k = key(db)
    acct = account(db, used_ago_s=30)
    gapped = FakeSettings(fake__send_gap_s=60)

    async def scenario(p):
        await create(p, k, "x")
        await create(p, k, "y")
        assert await claim(p, settings=gapped) is None, "claimed inside the send gap"
        assert send_gap_s(SETTINGS, PROVIDER) == 0, "a provider that declares no gap has none"
        short = FakeSettings(fake__send_gap_s=20)
        assert (await claim(p, settings=short, caps=TWO_SLOTS)).account_id == acct
        assert await claim(p, settings=short, caps=TWO_SLOTS) is None, (    # a slot is free, the gap is not
            "claimed inside the send gap: the claim itself starts it")
        db.run("UPDATE accounts SET last_used_at = clock_timestamp() - interval '21 seconds' WHERE id = %s", acct)
        assert (await claim(p, settings=short, caps=TWO_SLOTS)).account_id == acct

    run(db, scenario)


@mutation(K, '    return min(gap, settings.get("unsent_deadline_s") * GAP_DEADLINE_SHARE)', "    return gap",
          expect="a gap past half the unsent deadline")
def test_a_send_gap_is_cut_to_half_the_unsent_deadline_so_queued_jobs_cannot_all_expire():
    assert send_gap_s(FakeSettings(fake__send_gap_s=3600, unsent_deadline_s=1800), PROVIDER) == 900, (
        "a gap past half the unsent deadline")
    assert send_gap_s(FakeSettings(fake__send_gap_s=600, unsent_deadline_s=10800), PROVIDER) == 600


RECHECK = ('            if not await (await conn.execute(_CANDIDATES, {**args, "only": account_id})).fetchone():\n'
           "                continue")


@mutation(K, RECHECK, "            if False:\n                continue", expect="a stale candidate list beat the gap")
def test_a_claim_that_read_its_candidates_before_another_claim_committed_still_keeps_the_send_gap(db):
    k = key(db)
    acct = account(db)
    gapped = FakeSettings(fake__send_gap_s=60)

    async def scenario(p):
        await create(p, k, "x")
        await create(p, k, "y")
        reached, gate, paused = asyncio.Event(), asyncio.Event(), []

        async def pause():
            if not paused:                         # only the first worker stops, after it read its list
                paused.append(True)
                reached.set()
                await gate.wait()

        failpoints.arm("claim.candidates_read", pause)
        try:
            first = asyncio.create_task(claim(p, owner="w1", settings=gapped, caps=TWO_SLOTS))
            await asyncio.wait_for(reached.wait(), 5)
            second = await claim(p, owner="w2", settings=gapped, caps=TWO_SLOTS)   # takes the account, commits
            gate.set()
            return second, await first
        finally:
            failpoints.disarm("claim.candidates_read")
            gate.set()

    second, first = run(db, scenario)
    assert second is not None and second.account_id == acct
    assert first is None, "a stale candidate list beat the gap: %r" % (first,)


def test_a_job_past_the_unsent_deadline_is_not_claimed(db):
    k = key(db)
    account(db)

    async def scenario(p):
        made = await create(p, k, "x")
        db.run("UPDATE jobs SET created_at = clock_timestamp() - interval '2 hours' WHERE id = %s", made.job_id)
        assert await claim(p, settings=FakeSettings(unsent_deadline_s=3600)) is None
        assert await claim(p) is not None

    run(db, scenario)


def test_no_free_browser_token_means_no_claim(db):
    k = key(db)
    account(db)

    async def scenario(p):
        await create(p, k, "x")
        assert await claim(p, take_token=lambda: None) is None
        token = Token()
        got = await claim(p, take_token=lambda: token)
        return got, token

    got, token = run(db, scenario)
    assert got.token is token and token.released == 0                 # the caller releases it after the submit


def test_concurrent_claims_never_double_book_a_job_or_an_account(db):
    k = key(db)
    for _ in range(3):
        account(db, left=10)

    async def scenario(p):
        for i in range(6):
            await create(p, k, str(i), price=0)
        first = await asyncio.gather(*(claim(p, owner="w%d" % i) for i in range(6)))
        rest = [c for c in [await claim(p) for _ in range(6)] if c]    # a skipped capacity row is retried next tick
        return [c for c in first if c] + rest

    got = run(db, scenario)
    assert len(got) == 3 and len({c.job_id for c in got}) == 3 and len({c.account_id for c in got}) == 3
    assert db.one("SELECT count(*) FROM jobs WHERE status = 'queued'")[0] == 3


# ---- one job per account (muse: a job's results are what is new on the account since its send) ------------------
ONE = dataclasses.replace(FakeSpec().caps, one_job_per_account=True)
ONE_TWO_SLOTS = dataclasses.replace(TWO_SLOTS, one_job_per_account=True)
QUIET = FakeSettings(fake__quiet_after_fail_s=1200)      # a provider with the flag declares its quiet window
IN_FLIGHT = "   AND (NOT %(single)s OR NOT EXISTS (\n        SELECT 1 FROM jobs f\n"
IN_FLIGHT_OFF = "   AND (TRUE OR NOT EXISTS (\n        SELECT 1 FROM jobs f\n"
IN_FLIGHT_STATES = "f.status IN ('submitting', 'unknown', 'waiting', 'fetching')"
SINGLE_ARG = '"single": caps.one_job_per_account,'


def in_flight(db, k, acct, status):
    """A job of `acct` claimed earlier and not final, holding no lease (taken past its hold, followed by polls)."""
    db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion,"
           " after_sent, submit_timeout_s, price_key, price, account_id, next_poll_at, sent_at, external_ref) VALUES "
           "(gen_random_uuid(), %s, %s, 'generate', 'video', 'm', 'x', 'm', %s, 'detach', 'reconcile', 60, "
           "'fake-video', 0, %s, clock_timestamp() + interval '1 hour', clock_timestamp(), 'chat-1')",
           PROVIDER, k, status, acct)


@pytest.mark.parametrize("status", ["waiting", "unknown", "fetching"])
@mutation(K, IN_FLIGHT, IN_FLIGHT_OFF, expect="an account with a job in flight took another")
@mutation(K, SINGLE_ARG, '"single": False,', expect="an account with a job in flight took another")
@mutation(K, IN_FLIGHT_STATES, "f.status IN ('submitting', 'unknown', 'fetching')", expect="waiting:")
@mutation(K, IN_FLIGHT_STATES, "f.status IN ('submitting', 'waiting', 'fetching')", expect="unknown:")
@mutation(K, IN_FLIGHT_STATES, "f.status IN ('submitting', 'unknown', 'waiting')", expect="fetching:")
def test_one_job_per_account_skips_an_account_with_any_job_in_flight_and_the_flag_off_does_not(db, status):
    k = key(db)
    busy, free = account(db), account(db, used_ago_s=50)        # LRU: `busy` would go first
    in_flight(db, k, busy, status)

    async def scenario(p):
        for i in range(3):
            await create(p, k, str(i))
        first = await claim(p, caps=ONE, settings=QUIET)
        second = await claim(p, caps=ONE, settings=QUIET)     # `free`: a submitting job, `busy`: one in flight
        return first, second, await claim(p)     # flag off: `busy` is taken (it holds no lease)

    first, second, off = run(db, scenario)
    assert first is not None and first.account_id == free, \
        "%s: an account with a job in flight took another: %r" % (status, first)
    assert second is None, "%s: an account with a job in flight took another: %r" % (status, second)
    assert off is not None and off.account_id == busy


@mutation(K, IN_FLIGHT_STATES, "f.status IN ('unknown', 'waiting', 'fetching')", expect="two jobs on one account")
def test_one_job_per_account_holds_when_another_claim_committed_after_the_list_was_read(db):
    k = key(db)
    acct = account(db)

    async def scenario(p):
        await create(p, k, "x")
        await create(p, k, "y")
        reached, gate, paused = asyncio.Event(), asyncio.Event(), []

        async def pause():
            if not paused:
                paused.append(True)
                reached.set()
                await gate.wait()

        failpoints.arm("claim.candidates_read", pause)
        try:
            first = asyncio.create_task(claim(p, owner="w1", caps=ONE_TWO_SLOTS, settings=QUIET))
            await asyncio.wait_for(reached.wait(), 5)
            second = await claim(p, owner="w2", caps=ONE_TWO_SLOTS, settings=QUIET)    # takes the account, commits
            gate.set()
            return second, await first
        finally:
            failpoints.disarm("claim.candidates_read")
            gate.set()

    second, first = run(db, scenario)
    assert second is not None and second.account_id == acct
    assert first is None, "a stale candidate list put two jobs on one account: %r" % (first,)


QUIET_CLAUSE = "   AND (NOT %(single)s OR NOT EXISTS (\n        SELECT 1 FROM jobs q\n"
QUIET_SENT = "         WHERE q.account_id = a.id AND q.status = 'failed' AND q.sent_at IS NOT NULL\n"
QUIET_READ = "    quiet_s = settings.get(provider + QUIET_AFTER_FAIL) if caps.one_job_per_account else 0\n"


def failed_job(db, k, acct, *, sent: bool, ago_s: float):
    """A job of `acct` that failed `ago_s` seconds ago, sent (upstream may still be making it) or not."""
    db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion,"
           " after_sent, submit_timeout_s, price_key, price, account_id, sent_at, finished_at, error_code, error)"
           " VALUES (gen_random_uuid(), %s, %s, 'generate', 'video', 'm', 'x', 'm', 'failed', 'detach', 'reconcile', "
           "60, 'fake-video', 0, %s, CASE WHEN %s THEN clock_timestamp() - interval '1 hour' END, "
           "clock_timestamp() - make_interval(secs => %s), 'render.deadline', 'late')", PROVIDER, k, acct, sent, ago_s)


@pytest.mark.parametrize("case", ["within", "after", "never_sent"])
@mutation(K, QUIET_CLAUSE, "   AND (TRUE OR NOT EXISTS (\n        SELECT 1 FROM jobs q\n",
          expect="within: a failed sent job's account took a job")
@mutation(K, QUIET_SENT, "         WHERE q.account_id = a.id AND q.status = 'failed'\n", expect="never_sent: blocked")
@mutation(K, QUIET_READ, "    quiet_s = 0\n", expect="within: a failed sent job's account took a job")
def test_one_job_per_account_keeps_an_account_quiet_after_its_sent_job_failed(db, case):
    k = key(db)
    quiet, free = account(db), account(db, used_ago_s=50)       # LRU: `quiet` would go first
    failed_job(db, k, quiet, sent=case != "never_sent", ago_s=7200 if case == "after" else 60)

    async def scenario(p):
        for i in range(3):
            await create(p, k, str(i))
        first = await claim(p, caps=ONE, settings=QUIET)
        second = await claim(p, caps=ONE, settings=QUIET)
        return first, second, await claim(p)                    # flag off: no quiet window

    first, second, off = run(db, scenario)
    if case == "within":
        assert first.account_id == free, "within: a failed sent job's account took a job: %r" % (first,)
        assert second is None, "within: a failed sent job's account took a job: %r" % (second,)
        assert off is not None and off.account_id == quiet, "the flag off changes nothing"
    else:
        assert first is not None and first.account_id == quiet, "%s: blocked: %r" % (case, first)
