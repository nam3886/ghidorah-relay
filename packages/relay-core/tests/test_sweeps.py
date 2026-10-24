"""Sweeps (invariant I25): retention, orphans, sessions, input links and the ledger check."""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from accountkit import new_account, put_shot
from jobkit import create, key, run
from test_storage import minio_settings

from relay_core.jobs import sweeps
from relay_core.jobs.checks import shot_key
from relay_core.storage import ObjectStore, StorageError, media_key
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/jobs/sweeps.py"

TOTAL_CHECK = "if used_total != ledger_total:"
DAY_CHECK = "if used_day != ledger_day:"
SWITCH = "now=now or datetime.now(UTC), enabled=enabled)"
SWITCH_IGNORED = "now=now or datetime.now(UTC), enabled=True)"
RETENTION_CUTOFF = "\"at < clock_timestamp() - \" + old, (retention_days,))\n    events"
RETENTION_ALL = "\"at < clock_timestamp()\", ())\n    events"


@pytest.fixture
def store():
    s = ObjectStore(*minio_settings(), bucket="relay-test-" + uuid.uuid4().hex[:12])
    asyncio.run(s.ensure_bucket())
    yield s
    for o in s._client.list_objects(s.bucket, recursive=True):
        s._client.remove_object(s.bucket, o.object_name)
    s._client.remove_bucket(s.bucket)


def put(store, job_id, role="output", mime="video/mp4") -> str:
    k = media_key(str(job_id), role, 0, mime)
    asyncio.run(store.put_bytes(k, b"data", mime))
    return k


def media_row(db, job_id, role, object_key, *, age_days=0, token=None, expires="NULL"):
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token, share_expires_at, "
           "created_at) VALUES (%%s, %%s, 0, %%s, 'video/mp4', 4, %%s, %s, "
           "clock_timestamp() - make_interval(days => %%s))" % expires, job_id, role, object_key, token, age_days)


def keys(store) -> list[str]:
    return sorted(o.key for o in asyncio.run(store.list_prefix("")))


# ---- retention -----------------------------------------------------------------------------------------------------
@mutation(SRC, RETENTION_CUTOFF, RETENTION_ALL,
           expect="result == sweeps.Retained(request_logs=1, job_events=1, debug_media=1)")
def test_retention_removes_old_logs_events_and_debug_media_only(db, store):
    job_id = db.job(db.key())
    for days in (45, 1):
        db.run("INSERT INTO request_logs (at, route, status_code, duration_ms) "
               "VALUES (clock_timestamp() - make_interval(days => %s), '/x', 200, 1)", days)
        db.run("INSERT INTO job_events (job_id, at, source, stage, text) VALUES "
               "(%s, clock_timestamp() - make_interval(days => %s), 'system', 's', 't')", job_id, days)
    old_debug = put(store, job_id, "debug", "text/html")
    media_row(db, job_id, "debug", old_debug, age_days=45)
    out = put(store, job_id, "output")
    media_row(db, job_id, "output", out, age_days=90)                  # outputs are kept indefinitely
    result = run(db, lambda p: sweeps.sweep_retention(p, store, retention_days=30))
    assert result == sweeps.Retained(request_logs=1, job_events=1, debug_media=1)
    assert db.one("SELECT count(*) FROM request_logs")[0] == 1
    assert db.one("SELECT count(*) FROM job_events WHERE job_id = %s", job_id)[0] == 1
    assert db.one("SELECT count(*) FROM job_media WHERE role = 'debug'")[0] == 0 and keys(store) == [out]


def test_a_debug_object_that_cannot_be_removed_keeps_its_row(db, store):
    job_id = db.job(db.key())
    k = put(store, job_id, "debug", "text/html")
    media_row(db, job_id, "debug", k, age_days=45)

    class Broken:
        async def remove(self, key):
            raise StorageError("object store unreachable")

    with pytest.raises(StorageError):
        run(db, lambda p: sweeps.sweep_retention(p, Broken(), retention_days=30))
    assert db.one("SELECT count(*) FROM job_media WHERE role = 'debug'")[0] == 1


# ---- orphans -------------------------------------------------------------------------------------------------------
def orphan_setup(db, store):
    owned = db.job(db.key())
    gone = uuid.uuid4()
    return put(store, owned), put(store, gone), asyncio.run(store.put_bytes("notauuid/output/0.png", b"x", "image/png"))


def sweep_orphans(db, store, **kw):
    now = datetime.now(UTC) + timedelta(hours=7)
    return run(db, lambda p: sweeps.sweep_orphans(p, store, min_age_h=6, now=now, **kw))


def test_orphan_sweep_removes_objects_no_job_owns(db, store):
    kept, orphan, _ = orphan_setup(db, store)
    removed = sweep_orphans(db, store, enabled=True)
    assert sorted(removed) == sorted([orphan, "notauuid/output/0.png"]) and keys(store) == [kept]


def test_orphan_sweep_leaves_young_objects(db, store):
    _, orphan, _ = orphan_setup(db, store)
    assert run(db, lambda p: sweeps.sweep_orphans(p, store, min_age_h=6, enabled=True)) == []
    assert orphan in keys(store)


@mutation(SRC, SWITCH, SWITCH_IGNORED, expect="sweep_orphans(db, store, enabled=False) == [] and keys(store) == before")
def test_orphan_sweep_off_switch_removes_nothing(db, store):
    orphan_setup(db, store)
    before = keys(store)
    assert sweep_orphans(db, store, enabled=False) == [] and keys(store) == before


@mutation(SRC, '"SELECT id FROM accounts WHERE id = ANY(%(f)s)"',
          '"SELECT id FROM accounts WHERE false AND id = ANY(%(f)s)"',
           expect="removed == [shot_key(gone)] and keys(store) == [kept]")
def test_a_live_accounts_screenshot_survives_both_sweeps_and_a_deleted_accounts_is_an_orphan(db, store):
    live = new_account(db, checked=True)
    gone = uuid.uuid4()                                           # an account with no row any more
    kept = put_shot(db, store, live)
    asyncio.run(store.put_bytes(shot_key(gone), b"shot", "image/png"))
    db.run("UPDATE account_shots SET taken_at = clock_timestamp() - interval '90 days'")

    retained = run(db, lambda p: sweeps.sweep_retention(p, store, retention_days=30))
    assert retained.debug_media == 0 and kept in keys(store)
    removed = sweep_orphans(db, store, enabled=True)
    assert removed == [shot_key(gone)] and keys(store) == [kept]


# ---- sessions, input links, housekeeping ---------------------------------------------------------------------------
def test_expired_admin_sessions_are_removed(db):
    uid = uuid.uuid4()
    db.run("INSERT INTO admin_users (id, username, password_hash) VALUES (%s, 'a', 'h')", uid)
    for name, delta in (("old", "-1 hour"), ("live", "1 hour")):
        db.run("INSERT INTO admin_sessions (token_hash, user_id, expires_at) "
               "VALUES (%s, %s, clock_timestamp() + %s::interval)", name, uid, delta)
    assert run(db, sweeps.expire_admin_sessions) == 1
    assert [r[0] for r in db.all("SELECT token_hash FROM admin_sessions")] == ["live"]


def test_housekeeping_runs_every_step_and_survives_a_failing_one(db, store):
    job_id = db.job(db.key())
    media_row(db, job_id, "input", "x/input/0.png", token="t" * 22, expires="clock_timestamp() - interval '1 hour'")

    async def scenario(p):
        done = await sweeps.run_housekeeping(p, store, retention_days=30, orphan_age_h=6, orphans_enabled=True)

        class Down:
            async def list_prefix(self, prefix):
                raise StorageError("object store unreachable")

        part = await sweeps.run_housekeeping(p, Down(), retention_days=30, orphan_age_h=6, orphans_enabled=True)
        return done, part

    done, part = run(db, scenario)
    assert set(done) == {"retention", "orphans", "sessions", "input_links"} and done["input_links"] == 1
    assert set(part) == {"retention", "sessions", "input_links"}
    assert db.one("SELECT share_token FROM job_media WHERE role = 'input'")[0] is None


# ---- ledger check --------------------------------------------------------------------------------------------------
async def charged_wallet(p, db):
    k = key(db)
    await create(p, k, "one")                                      # a video job: price 10 on the key's wallets
    return k


@mutation(SRC, TOTAL_CHECK, "if used_day != ledger_total:",
           expect="found == [sweeps.Drift(k, \"*\", \"used_total\", 13, 10)]")
def test_ledger_check_finds_a_planted_total_drift(db):
    async def scenario(p):
        k = await charged_wallet(p, db)
        assert await sweeps.ledger_check(p) == []
        db.run("UPDATE key_wallets SET used_total = used_total + 3 WHERE key_id = %s AND scope = '*'", k)
        found = await sweeps.ledger_check(p)
        db.run("UPDATE key_wallets SET used_total = used_total - 3 WHERE key_id = %s AND scope = '*'", k)
        return k, found

    k, found = run(db, scenario)
    assert found == [sweeps.Drift(k, "*", "used_total", 13, 10)]


@mutation(SRC, DAY_CHECK, "if used_total != ledger_day:",
           expect="found == [sweeps.Drift(k, \"*\", \"used_day\", 6, 10)]")
def test_ledger_check_finds_a_planted_day_drift(db):
    async def scenario(p):
        k = await charged_wallet(p, db)
        db.run("UPDATE key_wallets SET used_day = used_day - 4 WHERE key_id = %s AND scope = '*'", k)
        found = await sweeps.ledger_check(p)
        db.run("UPDATE key_wallets SET used_day = used_day + 4 WHERE key_id = %s AND scope = '*'", k)
        return k, found

    k, found = run(db, scenario)
    assert found == [sweeps.Drift(k, "*", "used_day", 6, 10)]


LEDGER_TIMEOUT = ('        await conn.execute("SET LOCAL statement_timeout = \'%s\'" % SWEEP_STATEMENT_TIMEOUT)\n'
                  '        rows = await')
LEDGER_NO_TIMEOUT = "        rows = await"
SLOW_LEDGER = ("SELECT NULL::uuid, '*', 0, 0, 0, 0 FROM generate_series(1, 1) g "
               "WHERE (SELECT pg_sleep(6)) IS NOT NULL")


@mutation(SRC, LEDGER_TIMEOUT, LEDGER_NO_TIMEOUT, expect="canceling statement due to statement timeout")
def test_the_ledger_check_holds_past_the_default_five_second_statement_timeout(db, monkeypatch):
    monkeypatch.setattr(sweeps, "_LEDGER_VS_COUNTERS", SLOW_LEDGER)
    started = time.monotonic()
    assert run(db, sweeps.ledger_check) == []
    assert time.monotonic() - started > 5.5


@mutation(SRC, "SWEEP_HOLD_S = 70.0", "SWEEP_HOLD_S = 5.0", expect="AssertionError: [5.0, 5.0]")
def test_the_sweeps_open_their_transactions_with_a_hold_above_their_statement_timeout(db, monkeypatch):
    holds = []
    real = sweeps.tx

    def spy(pool, **kw):
        holds.append(kw.get("hold_s"))
        return real(pool, **kw)

    monkeypatch.setattr(sweeps, "tx", spy)

    async def scenario(p):
        await sweeps.ledger_check(p)
        await sweeps.expire_admin_sessions(p)

    run(db, scenario)
    assert len(holds) >= 2 and all(h is not None and h >= 60 for h in holds), holds


DEDUPE = "AND at > clock_timestamp() - make_interval(hours => %(h)s))"


@mutation(SRC, DEDUPE, "AND false)",
           expect="rows == [(\"drift\", k, \"*\", None, 3, 13, \"used_total is 13")
def test_a_drift_is_stored_as_an_alert_row_once_per_day(db):
    async def scenario(p):
        k = await charged_wallet(p, db)
        db.run("UPDATE key_wallets SET used_total = used_total + 3 WHERE key_id = %s AND scope = '*'", k)
        first = await sweeps.ledger_check(p)
        again = await sweeps.ledger_check(p)
        db.run("UPDATE key_wallets SET used_total = used_total - 3 WHERE key_id = %s AND scope = '*'", k)
        return k, first, again

    k, first, again = run(db, scenario)
    assert first == again and len(first) == 1
    rows = db.all("SELECT alert, key_id, scope, ledger_id, amount, used_total, detail FROM billing_alerts")
    assert rows == [("drift", k, "*", None, 3, 13, "used_total is 13, the ledger says 10")]
