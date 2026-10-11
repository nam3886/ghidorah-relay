"""Spaced strikes (D91): a doubt less than its gap after the last counted strike counts once; only the third spaced
strike ends in the strike effect; a logged-in read, a merge and an admin revive clear the count and its time; a
cooldown that asks for a recheck sets the next check at its end."""

from datetime import UTC, datetime, timedelta

import pytest
from accountkit import KitEngine, account_row, command, make_env, new_account, new_command, object_store, run_as
from test_checks import merge_setup
from test_pool import CAPS, account, effects, run

from relay_core import pool
from relay_core.db import tx
from relay_core.jobs.checks import check_account
from relay_core.jobs.commands import run_one
from relay_core.providers import Cooldown, Health, Success, Suspect
from relay_core.testing.mutation import mutation

P = "packages/relay-core/src/relay_core/pool.py"
CHECKS = "packages/relay-core/src/relay_core/jobs/checks.py"
GAP = 600
COUNTS = '"OR strike_at <= clock_timestamp() - make_interval(secs => %(g)s)) FROM accounts WHERE id = %(a)s",'
RECHECK_SQL = "\"check_due_at = CASE WHEN %(rc)s AND state <> 'dead' THEN %(u)s ELSE check_due_at END, \""
SETS_TIME = '"strike_at = CASE WHEN %(c)s THEN clock_timestamp() ELSE strike_at END, "'


@pytest.fixture
def store():
    with object_store() as s:
        yield s


def row(db, a):
    return db.one("SELECT state, state_reason, strikes, strike_at IS NOT NULL FROM accounts WHERE id = %s", a)


def age(db, a, minutes=11):
    """Make the last counted strike `minutes` old."""
    db.run("UPDATE accounts SET strike_at = clock_timestamp() - make_interval(mins => %s) WHERE id = %s", minutes, a)


def doubt(reason="login dialog"):
    return Suspect(reason, min_gap_s=GAP)


def guest():
    return (Suspect("guest read", min_gap_s=GAP),
            Cooldown("guest read, to be checked again", datetime.now(UTC) + timedelta(seconds=GAP), recheck=True))


@mutation(P, COUNTS, '"OR true) FROM accounts WHERE id = %(a)s",', expect="a doubt inside the gap added a strike")
@mutation(P, SETS_TIME, '"strike_at = strike_at, "', expect="a doubt inside the gap added a strike")
def test_doubts_inside_the_gap_count_once_two_dialogs_and_a_guest_read_are_one_strike(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, doubt())
        await effects(p, a, doubt("no conversation and no name"))
        await effects(p, a, *guest())

    run(db, scenario)
    assert row(db, a) == ("available", None, 1, True), "a doubt inside the gap added a strike: %r" % (row(db, a),)


@mutation(P, COUNTS, '"OR true) FROM accounts WHERE id = %(a)s",', expect="the spaced strikes were not counted right")
def test_one_and_two_spaced_guest_reads_do_not_mark_the_account_and_the_third_does(db):
    a = account(db)

    async def read(p):
        await effects(p, a, *guest())

    async def scenario(p):
        await read(p)
        assert row(db, a)[:3] == ("available", None, 1), "the spaced strikes were not counted right: 1"
        age(db, a)
        await read(p)
        assert row(db, a)[:3] == ("available", None, 2), "the spaced strikes were not counted right: 2"
        await read(p)                                      # inside the gap of the second: still two
        assert row(db, a)[:3] == ("available", None, 2), "the spaced strikes were not counted right: gap"
        age(db, a)
        await read(p)

    run(db, scenario)
    assert row(db, a)[:3] == ("dead", "signed_out", 3), "the third spaced guest read did not mark the account"


@mutation(P, '"strikes = 0, strike_at = NULL, last_error = NULL"', '"strikes = 0, last_error = NULL"',
          expect="a logged-in read did not clear the count and its time")
def test_a_logged_in_read_clears_the_count_and_the_time_of_the_last_strike(db):
    a = account(db)

    async def scenario(p):
        await effects(p, a, doubt())
        age(db, a)
        await effects(p, a, doubt())
        assert row(db, a)[2] == 2
        await effects(p, a, Success())
        assert row(db, a) == ("available", None, 0, False), "a logged-in read did not clear the count and its time"
        await effects(p, a, doubt())                      # right after: the first strike of a new count
        assert row(db, a)[2] == 1, "a logged-in read did not clear the count and its time: new count"

    run(db, scenario)


@mutation(P, RECHECK_SQL, '""',
          expect="a recheck cooldown did not set the next check")
def test_a_cooldown_that_asks_for_a_recheck_sets_the_next_check_at_its_end(db):
    a, b = new_account(db, checked=True, due_s=6 * 3600), new_account(db, checked=True, due_s=6 * 3600)
    until = datetime.now(UTC) + timedelta(seconds=GAP)

    async def scenario(p):
        await effects(p, a, Cooldown("guest read", until, recheck=True))
        await effects(p, b, Cooldown("throttled", until))

    run(db, scenario)
    near = "SELECT abs(extract(epoch FROM check_due_at - %s)) < 5 FROM accounts WHERE id = %s"
    assert db.one(near, until, a)[0], "a recheck cooldown did not set the next check at its end"
    assert not db.one(near, until, b)[0], "a plain cooldown must leave the schedule alone"


@mutation(CHECKS, '"ru": max(recheck) if recheck else None, "a": account_id})', '"ru": None, "a": account_id})',
          expect="the check after a guest read was not due at the end of its cooldown")
def test_the_check_after_a_guest_read_is_due_when_its_rest_ends_not_at_the_retry_or_the_interval(db):
    a = new_account(db, checked=True, due_s=-1)

    async def scenario(p):
        engine = KitEngine()
        until = datetime.now(UTC) + timedelta(seconds=GAP)
        engine.health = Health("unknown", effects=(Suspect("guest read", min_gap_s=GAP),
                                                   Cooldown("guest read", until, recheck=True)))
        return await check_account(make_env(p, engine, retry_s=30), a), until

    result, until = run_as(db.worker_dsn, scenario)
    assert result.outcome == "unknown"
    got = db.one("SELECT extract(epoch FROM check_due_at - %s) FROM accounts WHERE id = %s", until, a)[0]
    assert abs(got) < 5, "the check after a guest read was not due at the end of its cooldown: %s s off" % got
    assert account_row(db, a)["strikes"] == 1 and account_row(db, a)["state"] == "available"
    assert db.one("SELECT strike_at IS NOT NULL FROM accounts WHERE id = %s", a)[0]


@mutation(CHECKS, "'available' END, strikes = 0, strike_at = NULL, ", "'available' END, strikes = 0, ",
          expect="a revived account kept the time of its last strike")
def test_a_credential_merge_the_admin_import_path_clears_the_strike_count_and_its_time(db, store):
    old, new, cmd, other, shot = merge_setup(db, store)
    db.run("UPDATE accounts SET strikes = 2, strike_at = clock_timestamp(), state = 'dead', "
           "state_reason = 'signed_out' WHERE id = %s", old)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        return await check_account(make_env(p, engine, store=store), new, command_id=cmd)

    result = run_as(db.worker_dsn, scenario)
    assert result.outcome == "merged"
    assert row(db, old) == ("available", None, 0, False), \
        "a revived account kept the time of its last strike: %r" % (row(db, old),)


def test_a_strike_without_a_gap_counts_every_time_as_before(db):
    a = account(db)
    run(db, lambda p: effects(p, a, Suspect("a"), Suspect("b"), Suspect("c"), caps=CAPS))
    assert row(db, a)[:3] == ("dead", "signed_out", 3)


def old_streak(db, a, strikes=3):
    """A row as an earlier build left it: strikes at the threshold and a strike time inside the gap."""
    db.run("UPDATE accounts SET strikes = %s, strike_at = clock_timestamp() WHERE id = %s", strikes, a)


@mutation(P, "            if counted and strikes >= caps.suspect_threshold:",
          "            if strikes >= caps.suspect_threshold:", expect="a doubt that did not count ended the account")
def test_only_a_strike_that_counted_can_reach_the_threshold(db):
    a = account(db)
    old_streak(db, a)
    run(db, lambda p: effects(p, a, doubt()))
    assert row(db, a)[:3] == ("available", None, 3), "a doubt that did not count ended the account"


WAKE_CLEARS = '"cooldown_until = NULL, cooldown_reason = NULL, strikes = 0, strike_at = NULL"'


@mutation(P, WAKE_CLEARS, '"cooldown_until = NULL, cooldown_reason = NULL"',
          expect="an enabled account kept its strikes")
def test_an_account_that_was_disabled_and_enabled_again_starts_with_no_strikes(db):
    a = account(db, "dead")
    old_streak(db, a)
    db.run("UPDATE accounts SET state = 'disabled', state_reason = 'disabled by admin' WHERE id = %s", a)
    db.run("UPDATE accounts SET state = 'available', state_reason = NULL WHERE id = %s", a)      # the api's enable

    async def scenario(p):
        async with tx(p) as conn:
            await pool.wake_account(conn, a)             # the wake the enable queued, run by the worker
        await effects(p, a, doubt())

    run(db, scenario)
    assert row(db, a)[:3] == ("available", None, 1), "an enabled account kept its strikes: %r" % (row(db, a),)


@mutation(P, "AND state <> 'dead' THEN %(u)s", "THEN %(u)s", expect="a dead account was rechecked early")
def test_a_recheck_cooldown_does_not_pull_a_dead_accounts_check_forward(db):
    a = new_account(db, state="dead", checked=True, due_s=6 * 3600)
    until = datetime.now(UTC) + timedelta(seconds=GAP)
    run(db, lambda p: effects(p, a, Cooldown("login doubt", until, recheck=True)))
    got = db.one("SELECT extract(epoch FROM check_due_at - clock_timestamp()) FROM accounts WHERE id = %s", a)[0]
    assert got > 5 * 3600, "a dead account was rechecked early: due in %s s" % got


DEAD_RULE = "WHEN state <> 'dead' AND %(ru)s::timestamptz IS NOT NULL"


@mutation(CHECKS, DEAD_RULE, "WHEN %(ru)s::timestamptz IS NOT NULL",
          expect="a dead jar was rechecked sooner than its interval")
@mutation(CHECKS, "if read or recheck else env.retry_s", "if read else env.retry_s",
          expect="a dead jar was rechecked sooner than its interval")
def test_a_dead_account_that_reads_as_a_doubt_keeps_the_normal_check_interval(db):
    a = new_account(db, state="dead", checked=True, due_s=-1)

    async def scenario(p):
        engine = KitEngine()
        until = datetime.now(UTC) + timedelta(seconds=GAP)
        engine.health = Health("unknown", effects=(Suspect("guest read", min_gap_s=GAP),
                                                   Cooldown("login doubt", until, recheck=True)))
        return await check_account(make_env(p, engine), a)

    run_as(db.worker_dsn, scenario)
    got = db.one("SELECT extract(epoch FROM check_due_at - clock_timestamp()) FROM accounts WHERE id = %s", a)[0]
    assert got > 5 * 3600, "a dead jar was rechecked sooner than its interval: due in %s s" % got


OUTCOME = '"signed_out" if now == "dead" and was != "dead" else "unknown"'


@mutation(CHECKS, OUTCOME, '"unknown"', expect="the check that ended the account reported unknown")
@mutation(CHECKS, OUTCOME, '"signed_out" if now == "dead" else "unknown"',
          expect="a dead account reported signed_out again")
def test_the_check_that_ends_an_account_reports_signed_out_to_the_admin_and_no_other_does(db):
    ending = new_account(db, checked=True, due_s=-1)
    db.run("UPDATE accounts SET strikes = 2, strike_at = clock_timestamp() - interval '11 min' WHERE id = %s",
           ending)
    fresh = new_account(db, checked=True, due_s=-1)

    async def scenario(p):
        engine = KitEngine()
        until = datetime.now(UTC) + timedelta(seconds=GAP)
        engine.health = Health("unknown", effects=(Suspect("guest read", min_gap_s=GAP),
                                                   Cooldown("login doubt", until, recheck=True)))
        env = make_env(p, engine)
        first = await check_account(env, ending)
        db.run("UPDATE accounts SET strike_at = clock_timestamp() - interval '11 min' WHERE id = %s", ending)
        again = await check_account(env, ending)             # already dead
        return first, again, await check_account(env, fresh)

    first, again, one = run_as(db.worker_dsn, scenario)
    assert first.outcome == "signed_out" and row(db, ending)[0] == "dead", \
        "the check that ended the account reported %s" % first.outcome
    assert again.outcome == "unknown", "a dead account reported signed_out again"
    assert one.outcome == "unknown" and row(db, fresh)[:3] == ("available", None, 1)


COMMAND_OUTCOMES = 'in ("checked", "unknown", "merged", "proxy_bad", "signed_out"):'
COMMANDS = "packages/relay-core/src/relay_core/jobs/commands.py"


@mutation(COMMANDS, COMMAND_OUTCOMES, 'in ("checked", "unknown", "merged", "proxy_bad"):',
          expect="the admin's check command failed for a signed_out result")
def test_the_check_command_returns_a_signed_out_result_to_the_admin(db):
    a = new_account(db, checked=True, due_s=-1)
    db.run("UPDATE accounts SET strikes = 2, strike_at = clock_timestamp() - interval '11 min' WHERE id = %s", a)
    cid = new_command(db, "check", args={"account_id": str(a)})

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("unknown", effects=(Suspect("guest read", min_gap_s=GAP),))
        await run_one(make_env(p, engine))

    run_as(db.worker_dsn, scenario)
    got = command(db, cid)
    assert got["status"] == "done" and got["result"]["outcome"] == "signed_out", \
        "the admin's check command failed for a signed_out result: %r" % (got,)


SHOT_RULE = 'health.screenshot and result.outcome in ("checked", "merged", "signed_out")'


@mutation(CHECKS, SHOT_RULE, 'health.screenshot and result.outcome in ("checked", "merged")',
          expect="the screenshot of the check that ended the account was dropped")
def test_the_check_that_ends_an_account_keeps_its_screenshot_and_a_plain_doubt_does_not(db, store):
    ending = new_account(db, checked=True, due_s=-1)
    db.run("UPDATE accounts SET strikes = 2, strike_at = clock_timestamp() - interval '11 min' WHERE id = %s",
           ending)
    plain = new_account(db, checked=True, due_s=-1)

    async def scenario(p):
        engine = KitEngine()
        until = datetime.now(UTC) + timedelta(seconds=GAP)
        engine.health = Health("unknown", screenshot=b"\x89PNG-guest-page",
                               effects=(Suspect("guest read", min_gap_s=GAP), Cooldown("doubt", until, recheck=True)))
        env = make_env(p, engine, store=store)
        return await check_account(env, ending), await check_account(env, plain)

    first, second = run_as(db.worker_dsn, scenario)
    assert first.outcome == "signed_out" and second.outcome == "unknown"
    shots = "SELECT count(*) FROM account_shots WHERE account_id = %s"
    assert db.one(shots, ending) == (1,), "the screenshot of the check that ended the account was dropped"
    assert db.one(shots, plain) == (0,), "a plain doubt stored a screenshot"
