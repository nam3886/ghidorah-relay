"""Job creation (contracts 7.3; I3, I15, D12, D14): idempotency before the cap, one charge, the cap, atomicity."""

import asyncio
import dataclasses
import re
import uuid

import pytest
from jobkit import POOLS, PROVIDER, SETTINGS, create, events, key, new_job, run

from relay_core import failpoints
from relay_core.billing import QuotaExceeded
from relay_core.jobs import store
from relay_core.jobs.create import marker_for
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation

C = "packages/relay-core/src/relay_core/jobs/create.py"
# The lookup that runs before the cap, replaced by one that runs after it.
LOOKUP_FIRST = ("hit = await _replay(conn, new)\n        if hit is None:\n"
                "            await failpoints.hit(\"job.create.lookup_missed\")\n"
                "            limit = await _lock_key(conn, new, settings)\n"
                "            hit = await _replay(conn, new)     # a creator that held the lock "
                "may have made it while we waited\n"
                "        if hit is not None:\n            return hit\n"
                "        await _check_cap(conn, new, limit)\n")
LOOKUP_AFTER_CAP = ("limit = await _lock_key(conn, new, settings)\n        await _check_cap(conn, new, limit)\n"
                    "        hit = await _replay(conn, new)\n        if hit is not None:\n            return hit\n")
# The second lookup, taken under the key lock, is what makes a concurrent duplicate replay instead of fail.
LOOKUP_UNDER_LOCK = ("            hit = await _replay(conn, new)     # a creator that held the lock "
                     "may have made it while we waited")
CHARGE = "await billing.charge(conn, new.key_id, new.provider, new.plan.wallet_kind, new.plan.price, new.job_id)"
CHARGE_OUTSIDE_TX = ("async with pool.connection() as other:\n            await other.execute("
                     "\"UPDATE key_wallets SET used_total = used_total + %s, used_day = used_day + %s "
                     "WHERE key_id = %s AND provider = %s AND kind = %s\", (new.plan.price, new.plan.price, "
                     "new.key_id, new.provider, new.plan.wallet_kind))")
COUNT_ALL = "AND status NOT IN ('done', 'failed')"


def wallet_rows(db, key_id):
    return db.one("SELECT used_day, used_total FROM key_wallets WHERE key_id = %s AND kind = 'video'", key_id)


def job_count(db, key_id) -> int:
    return db.one("SELECT count(*) FROM jobs WHERE key_id = %s", key_id)[0]


def test_a_new_job_is_charged_queued_and_has_its_rows(db):
    k = key(db)
    media = (store.InputMedia(0, "in/0.png", "image/png", 10, 4, 4), store.InputMedia(1, "in/1.png", "image/png", 20))

    async def scenario(p):
        first = await store.create_job(p, new_job(k, "one", inputs=media, price=7), SETTINGS)
        second = await create(p, k, "two", price=7)
        return first, second

    first, second = run(db, scenario)
    assert (first.status, first.queue_position, first.replayed) == ("queued", 1, False)
    assert second.queue_position == 2
    assert wallet_rows(db, k) == (14, 14)
    row = db.one("SELECT status, price, marker, wallet_kind, pool, account_cost, submit_timeout_s, completion "
                 "FROM jobs WHERE id = %s", first.job_id)
    assert row == ("queued", 7, marker_for(first.job_id), "video", "video", 1, 60, "detach")
    assert db.one("SELECT entry, amount FROM ledger WHERE job_id = %s", first.job_id) == ("charge", 7)
    assert events(db, first.job_id) == ["queued"]
    inputs = db.all("SELECT idx, object_key, share_token IS NOT NULL, share_expires_at > clock_timestamp() "
                    "+ interval '6 days' FROM job_media WHERE job_id = %s AND role = 'input' ORDER BY idx",
                    first.job_id)
    assert inputs == [(0, "in/0.png", True, True), (1, "in/1.png", True, True)]


def test_the_body_hash_ignores_key_order_and_changes_with_content():
    assert store.body_hash({"a": 1, "b": [1, 2]}) == store.body_hash({"b": [1, 2], "a": 1})
    assert store.body_hash({"a": 1}) != store.body_hash({"a": 2})


def test_an_idempotency_key_needs_its_hash():
    with pytest.raises(ValueError):
        store.NewJob(PROVIDER, uuid.uuid4(), new_job(uuid.uuid4()).plan, "p", idem_key="k")


# ---- idempotency -----------------------------------------------------------------------------------------------
def test_same_key_and_body_replays_the_job_without_a_second_charge(db):
    k = key(db)

    async def scenario(p):
        return [await create(p, k, "x", idem="K", price=5) for _ in range(3)]

    got = run(db, scenario)
    assert [g.replayed for g in got] == [False, True, True]
    assert len({g.job_id for g in got}) == 1
    assert job_count(db, k) == 1 and wallet_rows(db, k) == (5, 5)
    assert db.one("SELECT count(*) FROM ledger WHERE key_id = %s", k)[0] == 1


def test_same_key_with_another_body_is_a_conflict_and_charges_nothing(db):
    k = key(db)

    async def scenario(p):
        await create(p, k, "x", idem="K", body={"prompt": "x"}, price=5)
        with pytest.raises(store.IdempotencyConflict) as e:
            await create(p, k, "y", idem="K", body={"prompt": "y"}, price=5)
        return e.value.code

    assert run(db, scenario) == "input.idempotency_conflict"
    assert job_count(db, k) == 1 and wallet_rows(db, k) == (5, 5)


def test_the_same_idempotency_key_of_two_api_keys_makes_two_jobs(db):
    a, b = key(db), key(db)

    async def scenario(p):
        return await create(p, a, "x", idem="K"), await create(p, b, "x", idem="K")

    one, two = run(db, scenario)
    assert one.job_id != two.job_id


@mutation(C, LOOKUP_FIRST, LOOKUP_AFTER_CAP, expect="relay_core.jobs.create.TooManyQueued")
def test_a_replay_is_answered_even_when_its_own_job_fills_the_cap(db):
    k = key(db, max_unfinished=1)

    async def scenario(p):
        first = await create(p, k, "x", idem="K")
        again = await create(p, k, "x", idem="K")           # the lookup comes before the cap (I3)
        with pytest.raises(store.TooManyQueued):
            await create(p, k, "other", idem="K2")
        return first, again

    first, again = run(db, scenario)
    assert again.job_id == first.job_id and again.replayed


def _all_wait_at(count: int):
    """Arms the failpoint that every creator reaches after a lookup miss and before the lock: all of them
    arrive before any of them commits, so the race is real."""
    gate = asyncio.Barrier(count)

    async def wait():
        await asyncio.wait_for(gate.wait(), 10)

    failpoints.arm("job.create.lookup_missed", wait)


@mutation(C, LOOKUP_UNDER_LOCK, "            pass", expect="relay_core.jobs.create.TooManyQueued")
def test_eight_concurrent_creates_with_one_key_make_one_job_and_one_charge(db):
    k = key(db, max_unfinished=1)           # the winner fills the cap: a loser must still replay, not be refused

    async def scenario(p):
        _all_wait_at(8)
        try:
            return await asyncio.gather(*(create(p, k, "x", idem="K", price=5) for _ in range(8)))
        finally:
            failpoints.disarm()

    got = run(db, scenario)
    assert len({g.job_id for g in got}) == 1 and sum(not g.replayed for g in got) == 1
    assert job_count(db, k) == 1 and wallet_rows(db, k) == (5, 5)
    assert db.one("SELECT count(*) FROM ledger WHERE key_id = %s", k)[0] == 1


def test_eight_concurrent_creates_with_two_bodies_keep_the_winners_body_only(db):
    k = key(db)

    async def scenario(p):
        _all_wait_at(8)
        try:
            return await asyncio.gather(*(create(p, k, "x", idem="K", body={"b": i % 2}, price=5)
                                          for i in range(8)), return_exceptions=True)
        finally:
            failpoints.disarm()

    got = run(db, scenario)
    made = [g for g in got if isinstance(g, store.Created)]
    refused = [g for g in got if isinstance(g, store.IdempotencyConflict)]
    assert len(made) == 4 and len(refused) == 4 and len({m.job_id for m in made}) == 1
    assert job_count(db, k) == 1 and wallet_rows(db, k) == (5, 5)
    stored = db.one("SELECT idem_body_hash FROM jobs WHERE key_id = %s", k)[0]
    assert stored in {store.body_hash({"b": 0}), store.body_hash({"b": 1})}


def test_a_unique_violation_from_a_writer_without_the_lock_replays_the_winner(db):
    """A job row for the same key appears between this creator's charge and its insert (a writer that does not
    take the key lock): the insert violates the unique index, the whole transaction (charge included) rolls
    back and the winner is re-read under the same rules."""
    k = key(db)
    winner = uuid.uuid4()
    body = store.body_hash({"prompt": "x"})

    async def plant():
        db.run("INSERT INTO jobs (id, provider, key_id, idem_key, idem_body_hash, operation, media_type, model, "
               "prompt, marker, status, completion, after_sent, submit_timeout_s, wallet_kind, price) VALUES "
               "(%s, %s, %s, 'K', %s, 'generate', 'video', 'm', 'x', 'm', 'queued', 'detach', 'reconcile', 60, "
               "'video', 5)", winner, PROVIDER, k, body)

    async def scenario(p):
        failpoints.arm("job.create.charged", plant)
        try:
            return await create(p, k, "x", idem="K", price=5)
        finally:
            failpoints.disarm()

    got = run(db, scenario)
    assert got.job_id == winner and got.replayed
    assert wallet_rows(db, k) == (0, 0) and job_count(db, k) == 1      # the charge rolled back with its transaction


# ---- the cap (D12, I15) ----------------------------------------------------------------------------------------
def test_over_the_cap_is_refused_with_no_charge_and_no_job(db):
    k = key(db, max_unfinished=2)

    async def scenario(p):
        await create(p, k, "1")
        await create(p, k, "2")
        with pytest.raises(store.TooManyQueued) as e:
            await create(p, k, "3")
        return e.value.code

    assert run(db, scenario) == "quota.too_many_queued"
    assert job_count(db, k) == 2 and wallet_rows(db, k) == (2 * 10, 2 * 10)
    assert db.one("SELECT count(*) FROM ledger WHERE key_id = %s", k)[0] == 2


@mutation(C, COUNT_ALL, "AND status NOT IN ('failed')", expect="relay_core.jobs.create.TooManyQueued")
def test_finished_jobs_do_not_count_toward_the_cap(db):
    k = key(db, max_unfinished=1)

    async def scenario(p):
        first = await create(p, k, "1")
        db.run("UPDATE jobs SET status = 'done' WHERE id = %s", first.job_id)
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'output', 0, 'o', 'x', 1)", first.job_id)
        second = await create(p, k, "2")                           # the done job no longer counts
        await store.fail_job(p, second.job_id, expect=("queued",), code="render.deadline", pools=POOLS)
        await create(p, k, "3")                                    # nor does the failed one

    run(db, scenario)
    assert job_count(db, k) == 3


def test_zero_is_unlimited_and_empty_means_the_global_default(db):
    unlimited, default = key(db, max_unfinished=0), key(db)
    low = FakeSettings(max_unfinished_default=1)

    async def scenario(p):
        for i in range(25):
            await create(p, unlimited, str(i), price=0)
        await create(p, default, "1", settings=low)
        with pytest.raises(store.TooManyQueued):
            await create(p, default, "2", settings=low)

    run(db, scenario)
    assert job_count(db, unlimited) == 25 and job_count(db, default) == 1


def test_the_cap_holds_under_concurrent_creates(db):
    k = key(db, max_unfinished=3)

    async def scenario(p):
        return await asyncio.gather(*(create(p, k, "j%d" % i) for i in range(8)), return_exceptions=True)

    got = run(db, scenario)
    assert sum(isinstance(g, store.Created) for g in got) == 3
    assert sum(isinstance(g, store.TooManyQueued) for g in got) == 5
    assert job_count(db, k) == 3 and wallet_rows(db, k) == (30, 30)


# ---- the charge and the job row commit together (I3) -----------------------------------------------------------
@mutation(C, CHARGE, CHARGE_OUTSIDE_TX, expect="assert wallet_rows(db, k) == (0, 0) and job_count(db, k) == 0")
def test_a_failure_between_the_charge_and_the_job_row_leaves_no_charge(db):
    k = key(db)

    async def scenario(p):
        failpoints.arm("job.create.charged", _raise)
        try:
            with pytest.raises(failpoints.FailpointError):
                await create(p, k, "x", price=5)
        finally:
            failpoints.disarm()

    run(db, scenario)
    assert wallet_rows(db, k) == (0, 0) and job_count(db, k) == 0
    assert db.one("SELECT count(*) FROM ledger")[0] == 0


async def _raise():
    raise failpoints.FailpointError("job.create.charged")


def test_a_refused_charge_leaves_no_job_row(db):
    k = key(db, wallets=((PROVIDER, "video", 5, None), (PROVIDER, "image", None, None)))

    async def scenario(p):
        await create(p, k, "1", price=5)
        with pytest.raises(QuotaExceeded):
            await create(p, k, "2", price=1)

    run(db, scenario)
    assert job_count(db, k) == 1 and wallet_rows(db, k) == (5, 5)


# ---- queue position and the text that is sent ------------------------------------------------------------------------
@mutation(C, 'AND j.pool IS NOT DISTINCT FROM me.pool "', 'AND true "',
          expect="behind two video jobs, but first of its own pool")
def test_the_queue_position_counts_only_the_jobs_of_the_same_pool(db):
    k = key(db)

    async def scenario(p):
        videos = [await store.create_job(p, new_job(k, "v%d" % i), SETTINGS) for i in range(2)]
        image = await store.create_job(p, new_job(k, "i", model=PROVIDER + "-image"), SETTINGS)
        return videos, image

    videos, image = run(db, scenario)
    assert [v.queue_position for v in videos] == [1, 2]
    assert image.queue_position == 1, "behind two video jobs, but first of its own pool"


@mutation(C, 'parts = [" ".join(prompt.split())]', "parts = [prompt]",
          expect="assert hinted.sent_text == \"a cat on a mat no questions [%s]\" % hinted.marker")
@mutation(C, "if isinstance(hint, str) and hint.strip():", "if False:",
          expect="assert hinted.sent_text == \"a cat on a mat no questions [%s]\" % hinted.marker")
@mutation(C, 'return " ".join(parts) + " [%s]" % marker', 'return " ".join(parts)',
          expect="assert hinted.sent_text == \"a cat on a mat no questions [%s]\" % hinted.marker")
def test_the_job_view_carries_the_text_that_is_sent_upstream(db):
    k = key(db)

    async def scenario(p):
        plan = new_job(k, "x").plan
        plan = dataclasses.replace(plan, params={**plan.params, "prompt_hint": "  no\nquestions "})
        made = await store.create_job(p, store.NewJob(PROVIDER, k, plan, "a  cat\non\ta mat"), SETTINGS)
        plain = await store.create_job(p, new_job(k, "plain"), SETTINGS)
        return [await store.job_view(p, j.job_id) for j in (made, plain)]

    hinted, plain = run(db, scenario)
    assert hinted.sent_text == "a cat on a mat no questions [%s]" % hinted.marker
    assert plain.sent_text == "plain [%s]" % plain.marker
    assert re.fullmatch(r"[3-9]\d{3}", plain.marker), "the marker is four digits, 3000 to 9999"


# What dola_v4 `queue.py` computes for the same strings: 3000 + int(sha1(id)[:8], 16) % 7000.
DOLA_MARKERS = {"00000000-0000-0000-0000-000000000001": "5461", "123e4567-e89b-12d3-a456-426614174000": "8117",
                "ffffffff-ffff-4fff-bfff-ffffffffffff": "8327"}
MARKER_BASE = "3000 + int(hashlib.sha1("
MARKER_WIDTH = "[:8], 16) % 7000)"
MARKER_SOURCE = "hashlib.sha1(str(job_id).encode())"


@mutation(C, MARKER_BASE, "1000 + int(hashlib.sha1(", expect="the dola marker of")
@mutation(C, MARKER_WIDTH, "[:8], 16) % 6000)", expect="the dola marker of")
@mutation(C, MARKER_WIDTH, "[:6], 16) % 7000)", expect="the dola marker of")
@mutation(C, MARKER_SOURCE, "hashlib.sha1(job_id.hex.encode())", expect="the dola marker of")
def test_a_job_marker_is_the_dola_marker_3000_to_9999_derived_from_sha1_of_the_job_id():
    for job_id, want in DOLA_MARKERS.items():
        assert marker_for(uuid.UUID(job_id)) == want, "the dola marker of %s" % job_id
    assert all(3000 <= int(marker_for(uuid.uuid4())) <= 9999 for _ in range(300))
