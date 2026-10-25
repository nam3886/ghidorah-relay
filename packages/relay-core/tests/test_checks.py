"""Account checks (invariants I14, I24, I28): the schedule, the lease and token, the proxy probe,
the outcome rules, the bounded hang, the redacted account error and the duplicate merge on the first check."""

import asyncio
import dataclasses
import time
from contextvars import Context
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jobkit
import pytest
from accountkit import (
    KitEngine,
    KitSpec,
    account_row,
    beating,
    children,
    keys,
    make_env,
    new_account,
    new_command,
    object_store,
    put_shot,
    run_as,
)

from relay_core import failpoints
from relay_core.budget import BrowserBudget, Purpose
from relay_core.db import delete_unused_account, tx
from relay_core.jobs import checks
from relay_core.jobs.checks import Checks, check_account, due_accounts
from relay_core.jobs.claim import claim_one
from relay_core.providers import Balance, Cooldown, Health, ProxyBad, ProxyVerdict
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation

CHECKS = "packages/relay-core/src/relay_core/jobs/checks.py"
SPEC = KitSpec()


@pytest.fixture
def store():
    with object_store() as s:
        yield s


def worker(db, scenario, **kw):
    return run_as(db.worker_dsn, scenario, **kw)


# ---- schedule --------------------------------------------------------------------------------------------------
@mutation(CHECKS, "ORDER BY (last_checked_at IS NOT NULL), check_due_at NULLS FIRST, created_at, id LIMIT %s",
          "ORDER BY check_due_at NULLS FIRST, created_at, id LIMIT %s",
          expect="assert got == [never_early, never_late, early, late]")
@mutation(CHECKS, "WHERE provider = %s AND state <> 'disabled' ", "WHERE provider = %s ",
          expect="assert got == [never_early, never_late, early, late]")
def test_due_order_is_never_checked_first_then_by_due_time_and_disabled_or_not_yet_due_are_left_out(db):
    late = new_account(db, checked=True, due_s=-10)
    early = new_account(db, checked=True, due_s=-100)
    never_late = new_account(db, checked=False, due_s=-5)       # never checked outranks an older due time
    never_early = new_account(db, checked=False, due_s=-50)
    new_account(db, checked=True, due_s=600)                    # not due yet
    new_account(db, state="disabled", checked=False, due_s=-500)
    new_account(db, provider="other", checked=False, due_s=-500)
    got = worker(db, lambda p: due_accounts(p, "fake", 10))
    assert got == [never_early, never_late, early, late]
    assert worker(db, lambda p: due_accounts(p, "fake", 2)) == [never_early, never_late]


# ---- one check ---------------------------------------------------------------------------------------------------
def test_an_alive_check_revives_stamps_and_merges_the_state_patch_under_a_fenced_check_lease(db):
    a = new_account(db, state="dead", checked=False)
    db.run("UPDATE accounts SET strikes = 2, last_error = 'old', provider_state = '{\"keep\": 1}' WHERE id = %s", a)
    seen = {}

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x", display_name="Who",
                               state_patch={"composer": "new"})

        async def spy(ctx):
            seen["lease"] = db.one("SELECT purpose, hard_until IS NOT NULL, until <= hard_until FROM leases")
        engine.on_check = spy
        return await check_account(make_env(p, engine), a)

    result = worker(db, scenario)
    assert result.outcome == "checked"
    assert seen["lease"] == ("check", True, True)
    row = account_row(db, a)
    assert (row["state"], row["strikes"], row["last_error"], row["identity"], row["display_name"]) == (
        "available", 0, None, "who@x", "Who")
    assert row["provider_state"] == {"keep": 1, "composer": "new"}
    assert row["last_checked_at"] is not None
    assert db.one("SELECT check_due_at > clock_timestamp() + interval '50 min' FROM accounts WHERE id = %s", a)[0]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


@mutation(CHECKS, '{"gap": send_gap_s(env.settings, env.provider) > 0,', '{"gap": False,',
          expect="a check of a provider with a send gap did not count as a use")
@mutation(CHECKS, '{"gap": send_gap_s(env.settings, env.provider) > 0,', '{"gap": True,',
          expect="a check of a provider with no send gap counted as a use")
def test_a_check_counts_as_a_use_for_the_send_gap_only_when_the_provider_declares_one(db):
    gapped, plain = new_account(db, checked=True), new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("unknown")
        await check_account(make_env(p, engine, settings=FakeSettings(SPEC, fake__send_gap_s=60)), gapped)
        await check_account(make_env(p, engine), plain)

    worker(db, scenario)
    assert db.one("SELECT last_used_at > clock_timestamp() - interval '1 min' FROM accounts WHERE id = %s",
                  gapped)[0], "a check of a provider with a send gap did not count as a use"
    assert db.one("SELECT last_used_at IS NULL FROM accounts WHERE id = %s", plain)[0], (
        "a check of a provider with no send gap counted as a use")


def test_a_signed_out_result_kills_and_an_unreadable_one_changes_nothing_and_keeps_a_new_account_unclaimable(db):
    out = new_account(db, checked=True)
    unknown_new = new_account(db, checked=False)
    unknown_old = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()
        env = make_env(p, engine, retry_s=300)
        engine.health = Health("signed_out")
        first = await check_account(env, out)
        engine.health = Health("unknown")
        return first, await check_account(env, unknown_new), await check_account(env, unknown_old)

    first, second, third = worker(db, scenario)
    assert (first.outcome, second.outcome, third.outcome) == ("checked", "unknown", "unknown")
    assert account_row(db, out)["state"] == "dead"
    new = account_row(db, unknown_new)
    assert new["state"] == "available" and new["last_checked_at"] is None        # still never read: not claimable
    assert db.one("SELECT check_due_at BETWEEN clock_timestamp() AND clock_timestamp() + interval '6 min' "
                  "FROM accounts WHERE id = %s", unknown_new)[0]


@mutation(CHECKS, 'if row is None or row[0] == "disabled":', "if row is None:",
          expect="assert skipped.outcome == \"skipped\" and checked.outcome == \"checked\"")
def test_a_disabled_account_is_skipped_and_a_dead_one_is_still_checked(db):
    disabled = new_account(db, state="disabled", checked=True)
    dead = new_account(db, state="dead", checked=True)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("signed_out")
        env = make_env(p, engine)
        return await check_account(env, disabled), await check_account(env, dead), engine.checked

    skipped, checked, calls = worker(db, scenario)
    assert skipped.outcome == "skipped" and checked.outcome == "checked"
    assert calls == [str(dead)]
    assert account_row(db, disabled)["state"] == "disabled" and account_row(db, disabled)["last_error"] is None


def test_a_busy_account_is_skipped_and_nothing_changes(db):
    a = new_account(db, checked=True)
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES "
           "(%s, 'command', 'other', clock_timestamp() + interval '1 min', clock_timestamp() + interval '1 min')", a)

    async def scenario(p):
        engine = KitEngine()
        return await check_account(make_env(p, engine), a), engine.checked

    result, calls = worker(db, scenario)
    assert result.outcome == "busy" and calls == []
    assert db.one("SELECT count(*) FROM leases")[0] == 1


# ---- the proxy probe first -----------------------------------------------------------------------------------------
def test_the_proxy_probe_runs_first_a_bad_one_stops_the_check_and_a_good_one_is_recorded(db):
    bad = new_account(db, checked=True, proxy=("10.0.0.1", 8080))
    good = new_account(db, checked=True, proxy=("10.0.0.2", 8080))

    async def scenario(p):
        engine = KitEngine()
        env = make_env(p, engine, retry_s=300)
        engine.verdict = ProxyVerdict(False, "connection refused")
        first = await check_account(env, bad)
        engine.verdict = ProxyVerdict(True)
        second = await check_account(env, good)
        return first, second, engine

    first, second, engine = worker(db, scenario)
    assert (first.outcome, second.outcome) == ("proxy_bad", "checked")
    assert engine.checked == [str(good)]                           # the bad one never reached check_account
    assert [x.host for x in engine.probed] == ["10.0.0.1", "10.0.0.2"]
    b, g = account_row(db, bad), account_row(db, good)
    assert (b["proxy_ok"], b["last_error"]) == (False, "connection refused")
    assert g["proxy_ok"] is True and g["proxy_checked_at"] is not None


# ---- bounds ------------------------------------------------------------------------------------------------------
def test_a_hung_check_gives_the_account_back_within_its_bound_and_records_why(db):
    a = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()

        async def hang(ctx):
            await asyncio.sleep(60)
        engine.on_check = hang
        env = make_env(p, engine, check_hard_s=1.0)
        started = time.monotonic()
        async with beating(env):
            result = await check_account(env, a)
        return result, time.monotonic() - started

    result, took = worker(db, scenario)
    assert result.outcome == "failed" and took < 2.0
    assert "time limit" in account_row(db, a)["last_error"]
    assert db.one("SELECT count(*) FROM leases")[0] == 0           # the account is free again (I14)
    assert db.one("SELECT check_due_at > clock_timestamp() FROM accounts WHERE id = %s", a)[0]


@mutation(CHECKS, "expired_ok=report.error is not None", "expired_ok=False",
          expect="assert worker(db, scenario).outcome == \"failed\"")
def test_a_failure_is_recorded_after_the_lease_ran_out_while_the_recording_was_held_back(db):
    a = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()

        async def hang(ctx):
            await asyncio.sleep(60)
        engine.on_check = hang

        async def stall():                         # the recording starts past the lease's hard bound
            await asyncio.sleep(0.6)
        failpoints.arm("check.recording", stall)
        try:
            env = make_env(p, engine, check_hard_s=1.0)
            async with beating(env):
                return await check_account(env, a)
        finally:
            failpoints.disarm("check.recording")

    assert worker(db, scenario).outcome == "failed"
    assert "time limit" in account_row(db, a)["last_error"]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


@mutation(CHECKS, "return await _apply_report(env, account_id, lease_id, report or _Report(error=TIME_LIMIT), "
                  "command_id)", "raise", expect="lease 1 is lost")
def test_a_check_the_keeper_cuts_at_the_hard_bound_still_records_why(db, monkeypatch):
    a = new_account(db, checked=True)
    monkeypatch.setattr(checks, "TIMEOUT_SHARE", 5.0)       # the task timeout is past the bound: the keeper cuts it

    async def scenario(p):
        engine = KitEngine()

        async def hang(ctx):
            await asyncio.sleep(60)
        engine.on_check = hang
        env = make_env(p, engine, check_hard_s=1.5)
        async with beating(env):
            return await check_account(env, a)

    assert worker(db, scenario).outcome == "failed"
    assert "time limit" in account_row(db, a)["last_error"]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


def test_a_failure_is_not_recorded_when_the_lease_row_is_gone(db):
    a = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()

        async def hang(ctx):
            await asyncio.sleep(60)
        engine.on_check = hang

        async def reaped():
            async with tx(p) as conn:
                await conn.execute("DELETE FROM leases")
        failpoints.arm("check.recording", reaped)
        try:
            env = make_env(p, engine, check_hard_s=1.5)
            return await check_account(env, a)
        finally:
            failpoints.disarm("check.recording")

    assert worker(db, scenario).outcome == "lease_lost"
    assert account_row(db, a)["last_error"] is None


@mutation(CHECKS, "                with contextlib.suppress(Exception):\n"
                  "                    async with tx(env.db) as conn:\n"
          "                        await leases.release(conn, lease_id)\n",
          "                pass\n", expect="the crashed check")
def test_a_check_that_crashes_while_recording_gives_its_lease_back_at_once(db):
    """A database error while the result is written (the soak met a deadlock there) must not leave the account
    leased until the TTL runs out."""
    a = new_account(db, checked=True)

    async def scenario(p):
        async def boom():
            raise RuntimeError("deadlock detected")
        failpoints.arm("check.recording", boom)
        try:
            with pytest.raises(RuntimeError):
                await check_account(make_env(p, KitEngine()), a)
        finally:
            failpoints.disarm("check.recording")

    worker(db, scenario)
    assert db.one("SELECT count(*) FROM leases") == (0,), "the crashed check still holds its lease"


def test_a_check_whose_lease_was_taken_away_writes_nothing(db):
    a = new_account(db, checked=False)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")

        async def steal(ctx):
            async with tx(p) as conn:
                await conn.execute("DELETE FROM leases")
        engine.on_check = steal
        return await check_account(make_env(p, engine), a)

    assert worker(db, scenario).outcome == "lease_lost"
    row = account_row(db, a)
    assert row["identity"] is None and row["last_checked_at"] is None


@mutation(CHECKS, "    return redact_log(str(e) or type(e).__name__)", "    return str(e) or type(e).__name__",
          expect="assert stored == \"[omitted len=%d]\" % len(secret)")
def test_an_account_error_over_2000_characters_is_stored_as_omitted_len(db):
    a = new_account(db, checked=True)
    secret = "sk-" + "A1b2C3d4" * 300                                # a planted long secret

    async def scenario(p):
        engine = KitEngine()

        async def boom(ctx):
            raise RuntimeError(secret)
        engine.on_check = boom
        return await check_account(make_env(p, engine), a)

    assert worker(db, scenario).outcome == "failed"
    stored = account_row(db, a)["last_error"]
    assert stored == "[omitted len=%d]" % len(secret)
    assert secret not in stored


@mutation(CHECKS, 'redact_log(result.detail) if result.detail else ""', '"" if result.detail else ""',
          expect="the failure reason was not in the log line")
def test_the_log_line_of_a_failed_check_names_its_reason_and_a_good_one_has_none(db, caplog):
    bad, good = new_account(db, checked=True), new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()

        async def boom(ctx):
            if str(ctx.account.id) == str(bad):
                raise RuntimeError("upstream refused the session")
        engine.on_check = boom
        sched = Checks(make_env(p, engine), max_tasks=2)
        with caplog.at_level("INFO", logger=checks.log.name):
            for account_id in (bad, good):
                db.run("UPDATE accounts SET check_due_at = clock_timestamp() - interval '1 minute' WHERE id = %s",
                       account_id)
            await sched.tick()
            await sched.wait()

    worker(db, scenario)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("check of ")]
    assert any(str(bad) in m and "failed" in m and "upstream refused the session" in m for m in lines), \
        "the failure reason was not in the log line: %r" % lines
    assert any(m == "check of %s: checked" % good for m in lines), lines


# ---- the browser token --------------------------------------------------------------------------------------------
def test_a_browser_check_holds_a_check_token_and_waits_when_the_check_and_command_share_is_taken(db):
    a = new_account(db, checked=True)
    spec = KitSpec(execution="browser")

    async def scenario(p):
        budget = BrowserBudget(4)
        engine = KitEngine()
        engine.health = Health("alive")
        busy = {}

        async def spy(ctx):
            busy["during"] = budget.busy_by(Purpose.CHECK)
        engine.on_check = spy
        env = make_env(p, engine, spec=spec, budget=budget)
        held = budget.try_acquire(Purpose.COMMAND)                  # check + command <= 1 together
        blocked = await check_account(env, a)
        held.release()
        ok = await check_account(env, a)
        return blocked, ok, busy, budget.busy, engine.tokens

    blocked, ok, busy, after, tokens = worker(db, scenario)
    assert blocked.outcome == "no_token" and ok.outcome == "checked"
    assert busy["during"] == 1 and after == 0 and tokens[0] is not None


# ---- the scheduler -------------------------------------------------------------------------------------------------
def test_the_scheduler_starts_due_checks_as_tasks_up_to_its_limit_and_never_twice_for_one_account(db):
    ids = [new_account(db, checked=False, due_s=-(10 - i)) for i in range(3)]

    async def scenario(p):
        engine = KitEngine()
        gate = asyncio.Event()

        async def hold(ctx):
            await gate.wait()
        engine.on_check = hold
        sched = Checks(make_env(p, engine), max_tasks=2)
        first = await sched.tick()
        again = await sched.tick()                                  # full: starts nothing
        gate.set()
        await sched.wait()
        rest = await sched.tick()
        await sched.wait()
        return first, again, rest, engine.checked

    first, again, rest, checked = worker(db, scenario)
    assert first == ids[:2] and again == [] and rest == [ids[2]]
    assert sorted(checked) == sorted(str(i) for i in ids)


@mutation(CHECKS, "        if len(self._tasks) >= self.max_tasks:",
          "        if self.env.drained() or len(self._tasks) >= self.max_tasks:",
          expect="a drained provider stopped its account checks")
def test_a_drained_provider_still_checks_its_accounts_and_claims_no_job(db):
    """The owner's ruling (23/10): drain stops new jobs only; the checks go on (they keep sessions alive)."""
    acct = new_account(db, checked=False)

    async def scenario(p):
        engine = KitEngine()
        drained = FakeSettings(SPEC, fake__claims_enabled=False)
        env = make_env(p, engine, settings=drained)
        sched = Checks(env)
        started = await sched.tick()
        await sched.wait()
        claimed = await claim_one(p, provider=env.provider, caps=env.caps, settings=drained, owner="w1")
        return started, engine.checked, claimed

    started, checked, claimed = worker(db, scenario)
    assert started == [acct] and checked == [str(acct)], "a drained provider stopped its account checks"
    assert claimed is None, "a drained provider claimed a job"


# ---- the duplicate merge -------------------------------------------------------------------------------------------
def merge_setup(db, store, *, old_state="available", new_proxy=None, old_proxy=("10.0.0.9", 1)):
    old = new_account(db, identity="who@x", checked=True, state=old_state, credential=b"old-cred", proxy=old_proxy)
    new = new_account(db, checked=False, credential=b"new-cred", proxy=new_proxy)
    db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video')", new)
    cmd = new_command(db, "check", account_id=new)
    other = new_command(db, "wake", account_id=new)
    shot = put_shot(db, store, new)
    return old, new, cmd, other, shot


@mutation(CHECKS, "    dropped = await delete_unused_account(conn, new_id, own_leases=[lease_id])",
          "    dropped = []",
          expect="assert db.one(\"SELECT count(*) FROM accounts\")[0] == 1 and account_row(db, new)")
@mutation(CHECKS, "own_leases=[lease_id])", "own_leases=[])",
          expect="assert result.outcome == \"merged\" and result.account_id == old")
def test_a_known_identity_moves_the_credential_and_removes_the_new_row_with_everything_it_owned(db, store):
    old, new, cmd, other, shot = merge_setup(db, store, new_proxy=("10.0.0.5", 3128))

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x", display_name="Who", state_patch={"composer": "new"},
                               screenshot=b"\x89PNG-new-shot")
        return await check_account(make_env(p, engine, store=store), new, command_id=cmd)

    result = worker(db, scenario)
    assert result.outcome == "merged" and result.account_id == old
    assert db.one("SELECT count(*) FROM accounts")[0] == 1 and account_row(db, new) is None
    assert children(db, new) == dict.fromkeys(children(db, new), 0)             # no orphan child row
    row = account_row(db, old)
    assert bytes(row["credential_enc"]) == b"new-cred" and row["credential_gen"] == 2
    assert (row["state"], row["proxy_host"], row["proxy_ok"]) == ("available", "10.0.0.5", None)
    assert row["provider_state"] == {"composer": "new"}
    assert db.one("SELECT account_id FROM commands WHERE id = %s", cmd)[0] == old   # the asking command survives
    assert db.one("SELECT count(*) FROM commands WHERE id = %s", other)[0] == 0
    assert keys(store) == ["%s/debug/0.png" % old]       # the new row's screenshot is gone, the survivor has one
    assert db.one("SELECT object_key FROM account_shots WHERE account_id = %s", old)[0] == "%s/debug/0.png" % old


class BeatOnPut:
    """An object store whose screenshot upload is preceded by one lease heartbeat: a beat that lands after the check
    recorded its result (which deleted the lease) and before the `holding` block ends."""

    def __init__(self, inner):
        self.inner, self.keeper = inner, None

    async def put_bytes(self, key, data, mime):
        await self.keeper.beat()
        await asyncio.sleep(0)
        return await self.inner.put_bytes(key, data, mime)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@mutation(CHECKS, "    settle()\n    try:\n        result, dropped", "    try:\n        result, dropped",
          expect="a recorded check reported lease_lost")
def test_a_heartbeat_after_the_checks_own_commit_neither_fails_the_check_nor_drops_its_screenshot(db, store):
    a = new_account(db, checked=False, due_s=None)

    async def scenario(p):
        spy = BeatOnPut(store)
        env = make_env(p, KitEngine(), store=spy)
        spy.keeper = env.keeper
        env.engine.health = Health("alive", identity="who@x", screenshot=b"\x89PNG-shot")
        return await check_account(env, a)

    result = worker(db, scenario)
    assert result.outcome == "checked", "a recorded check reported %s" % result.outcome
    assert db.one("SELECT count(*) FROM account_shots WHERE account_id = %s", a) == (1,), "the screenshot was dropped"
    assert account_row(db, a)["identity"] == "who@x" and db.one("SELECT count(*) FROM leases")[0] == 0


class SpyStore:
    """An object store that notes, for every remove, whether the lease keeper held a lease at that moment."""

    def __init__(self, inner):
        self.inner, self.keeper, self.held_at_remove = inner, None, []

    async def remove(self, key):
        self.held_at_remove.append(bool(self.keeper._held))
        return await self.inner.remove(key)

    def __getattr__(self, name):
        return getattr(self.inner, name)


@mutation(CHECKS, "await pool.ensure_rows(conn, twin, env.pools())", "pass",
          expect="the surviving account has no capacity row for")
def test_a_merge_makes_sure_the_surviving_twin_has_all_its_capacity_rows(db, store):
    old, new, cmd, other, shot = merge_setup(db, store)
    assert db.one("SELECT count(*) FROM account_pools WHERE account_id = %s", old) == (0,)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        env = make_env(p, engine, store=store)
        return await check_account(env, new, command_id=cmd), set(env.pools())

    result, declared = worker(db, scenario)
    got = {r[0] for r in db.all("SELECT pool FROM account_pools WHERE account_id = %s", old)}
    assert result.outcome == "merged" and declared
    assert got == declared, "the surviving account has no capacity row for %s" % sorted(declared - got)


@mutation(CHECKS, "async with env.keeper.holding(lease_id) as settle:\n            report = await _run(",
          "if (settle := lambda: None):\n            report = await _run(",
          expect="an object-store call ran outside the lease")
def test_a_merge_removes_the_screenshot_objects_while_the_lease_is_still_held(db, store):
    old, new, cmd, other, shot = merge_setup(db, store)

    async def scenario(p):
        spy = SpyStore(store)
        env = make_env(p, KitEngine(), store=spy)
        spy.keeper = env.keeper
        env.engine.health = Health("alive", identity="who@x")
        return (await check_account(env, new)).outcome, spy.held_at_remove

    outcome, held = worker(db, scenario)
    assert outcome == "merged" and held, "the merge removed no object"
    assert all(held), "an object-store call ran outside the lease"


def twin_pair(db):
    """Two accounts that each report the identity the other has stored (a swapped pair): each is the other's twin."""
    a = new_account(db, identity="a@x", checked=True, credential=b"cred-a")
    b = new_account(db, identity="b@x", checked=True, credential=b"cred-b")
    for each in (a, b):                 # a checked account has every declared pool row
        for name in ("image", "video"):
            db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, %s)", each, name)
    return a, b


@mutation(CHECKS, "ids = [account_id] if twin is None else [account_id, twin]", "ids = [account_id]",
          expect="deadlock detected")
def test_two_checks_that_find_each_other_as_twins_do_not_deadlock_and_leave_one_survivor(db):
    """Each check merges into the other. Both lock the account rows of BOTH accounts in id order, so one waits for the
    other; the loser then finds its own row holding the winner's credential and records nothing (superseded)."""
    a, b = twin_pair(db)

    async def scenario(p):
        engine = KitEngine()
        engine.health_by_account = {str(a): Health("alive", identity="b@x"), str(b): Health("alive", identity="a@x")}
        env = make_env(p, engine)
        entered = []

        async def both_reading(ctx):                   # both hold their lease and read their own credential first:
            entered.append(ctx)                        # a check that starts after the merge reads the moved one
            while len(entered) < 2:
                await asyncio.sleep(0.01)
        engine.on_check = both_reading

        async def both_locked():                       # each has taken its account locks and waits for the other
            await asyncio.sleep(1.5)
        failpoints.arm("check.apply.accounts_locked", both_locked)
        try:
            return await asyncio.gather(check_account(env, a), check_account(env, b), return_exceptions=True)
        finally:
            failpoints.disarm("check.apply.accounts_locked")

    results = worker(db, scenario)
    assert not [r for r in results if isinstance(r, BaseException)], "deadlock detected: %r" % (results,)
    assert sorted(r.outcome for r in results) == ["merged", "superseded"]
    assert db.one("SELECT count(*) FROM accounts")[0] == 1 and db.one("SELECT count(*) FROM leases")[0] == 0


def stale_check_beside_a_merge(db, store, old, new, cmd, stale: Health):
    """The old account is checked with its old jar (the engine answers `stale`) while a new import of the same
    identity merges into it: the old check records only after the merge committed."""
    async def scenario(p):
        engine = KitEngine()
        engine.health_by_account = {str(old): stale, str(new): Health("alive", identity="who@x")}
        env = make_env(p, engine, store=store, retry_s=300)
        merged = asyncio.Event()

        async def old_waits_for_the_merge(ctx):
            if ctx.account.id == str(old):
                await merged.wait()
        engine.on_check = old_waits_for_the_merge

        async def merge():
            try:
                return await check_account(env, new, command_id=cmd)
            finally:
                merged.set()
        return await asyncio.gather(check_account(env, old), merge())

    stale_result, merging = worker(db, scenario)
    assert merging.outcome == "merged"
    return stale_result


SUPERSEDED = '            await leases.release(conn, lease_id)\n            return CheckResult("superseded"'


@mutation(CHECKS, "if report.credential_gen is not None and gens[account_id] != report.credential_gen:", "if False:",
          expect="a check of the old credential wrote over the merged one")
@mutation(CHECKS, SUPERSEDED, '            await _fail(conn, env, account_id, "stale")\n' + SUPERSEDED,
          expect="the stale check moved the merge's recheck")
def test_a_check_that_read_a_credential_a_merge_replaced_records_nothing_and_leaves_the_recheck_due(db, store):
    """The old check's result is about a credential the row no longer holds: it must not kill the fresh one, and it
    must not push back the recheck the merge made due now, nor leave an error behind."""
    old, new, cmd, other, shot = merge_setup(db, store)
    result = stale_check_beside_a_merge(db, store, old, new, cmd, Health("signed_out"))
    row = account_row(db, old)
    assert (row["state"], row["credential_gen"], bytes(row["credential_enc"])) == ("available", 2, b"new-cred"), \
        "a check of the old credential wrote over the merged one: %r" % row["state"]
    assert result.outcome == "superseded"
    assert db.one("SELECT check_due_at <= clock_timestamp() FROM accounts WHERE id = %s", old) == (True,), \
        "the stale check moved the merge's recheck"
    assert row["last_error"] is None
    assert db.one("SELECT count(*) FROM leases")[0] == 0


@mutation(CHECKS, "if twin is not None and report.credential_gen in (None, gen):", "if twin is not None:",
          expect="the stale check removed the account's screenshot")
def test_a_check_whose_credential_a_merge_replaced_leaves_the_accounts_screenshot_alone(db, store):
    """The old jar reports an identity a third account has: a twin, whose merge would first remove this account's
    screenshot objects. The report is refused (the credential changed), so the screenshot stays."""
    old, new, cmd, other, shot = merge_setup(db, store)
    new_account(db, identity="y@x", checked=True)
    kept = put_shot(db, store, old)
    result = stale_check_beside_a_merge(db, store, old, new, cmd, Health("alive", identity="y@x"))
    assert result.outcome == "superseded"
    assert keys(store) == [kept], "the stale check removed the account's screenshot: %r" % keys(store)
    assert db.one("SELECT object_key FROM account_shots WHERE account_id = %s", old) == (kept,)


@mutation(CHECKS, "if merging and await _twin_in(conn, account_id, health.identity) != twin:", "if False:",
          expect="accounts_provider_identity_key")
def test_a_check_fails_for_a_retry_when_its_twin_changed_between_the_lookup_and_the_locks(db):
    a = new_account(db, checked=True)
    b = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")

        async def take_identity():                     # the other account is checked and records the identity now
            db.run("UPDATE accounts SET identity = 'who@x' WHERE id = %s", b)
        failpoints.arm("check.apply.accounts_locked", take_identity)
        try:
            return await check_account(make_env(p, engine), a)
        finally:
            failpoints.disarm("check.apply.accounts_locked")

    result = worker(db, scenario)
    assert result.outcome == "failed", "a twin that appeared while the check ran was not noticed"
    assert account_row(db, a)["identity"] is None and account_row(db, b)["credential_gen"] == 1
    assert db.one("SELECT check_due_at > clock_timestamp() FROM accounts WHERE id = %s", a)[0]
    assert db.one("SELECT count(*) FROM leases")[0] == 0


@mutation(CHECKS, "        await pool.ensure_rows(conn, account_id, env.pools())\n", "        pass\n",
          expect="the first check did not insert every declared pool row")
def test_the_first_check_inserts_every_declared_pool_row_of_the_account_in_pool_order(db):
    a = new_account(db, checked=False)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive")
        return (await check_account(make_env(p, engine), a)).outcome

    assert worker(db, scenario) == "checked"
    rows = [r[0] for r in db.all("SELECT pool FROM account_pools WHERE account_id = %s ORDER BY pool", a)]
    assert rows == sorted(p.name for p in SPEC.caps.pools), "the first check did not insert every declared pool row"


def test_a_merge_keeps_the_existing_proxy_when_the_import_gave_none_and_never_wakes_a_disabled_account(db, store):
    old, new, cmd, other, shot = merge_setup(db, store, old_state="disabled")

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        return await check_account(make_env(p, engine, store=store), new)

    assert worker(db, scenario).outcome == "merged"
    row = account_row(db, old)
    assert (row["state"], row["proxy_host"], row["credential_gen"]) == ("disabled", "10.0.0.9", 2)
    assert keys(store) == []


def test_an_identity_that_belongs_to_an_account_in_use_is_not_merged_and_the_check_fails_cleanly(db, store):
    old = new_account(db, identity="who@x", checked=True)
    new = new_account(db, checked=False)
    key = db.key(("fake", None, None))
    job = db.job(key, provider="fake")
    db.run("UPDATE jobs SET account_id = %s, status = 'failed', error_code = 'render.deadline', error = 'x', "
           "finished_at = now() WHERE id = %s",
           new, job)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        return await check_account(make_env(p, engine, store=store), new)

    result = worker(db, scenario)
    assert result.outcome == "failed"
    assert account_row(db, new) is not None and account_row(db, old)["credential_gen"] == 1
    assert "in use" in account_row(db, new)["last_error"]


def test_effects_from_a_check_go_through_the_pool(db):
    a = new_account(db, checked=True)

    async def scenario(p):
        engine = KitEngine()
        until = datetime.now(UTC) + timedelta(hours=1)
        engine.health = Health("alive", effects=(Cooldown("busy upstream", until),))
        return await check_account(make_env(p, engine), a)

    assert worker(db, scenario).outcome == "checked"
    assert db.one("SELECT cooldown_until > clock_timestamp() FROM accounts WHERE id = %s", a)[0]


# ---- the provider's own constants: proxy rest, check interval, lock order ----------------------------------------
REST_ALL = "rests = [Rest(p.name, until, why) for p in env.caps.pools] or [Cooldown(why, until)]"
REST_NONE = "rests = []"
REST_FIXED = "until = datetime.now(UTC) + timedelta(seconds=env.caps.proxy_rest_s)"
REST_FIXED_MUTATED = "until = datetime.now(UTC) + timedelta(seconds=1800)"
INTERVAL = "return self.caps.check_interval_s if self.check_every_s is None else self.check_every_s"
INTERVAL_FIXED = "return 3600.0"
ACCOUNT_LOCK = ('        rows = await (await conn.execute("SELECT id, credential_gen FROM accounts WHERE id = ANY(%s) '
                'ORDER BY id "\n')
CAPACITY_FIRST = ('        await conn.execute("SELECT 1 FROM account_pools WHERE account_id = ANY(%s) '
                  'ORDER BY account_id, pool FOR UPDATE", (ids,))\n'
                  '        rows = await (await conn.execute("SELECT id, credential_gen FROM accounts '
                  'WHERE id = ANY(%s) ORDER BY id "\n')
NO_ACCOUNT_LOCK = '"FOR NO KEY UPDATE", (ids,))).fetchall()'


def with_caps(spec: KitSpec, **kw) -> KitSpec:
    spec.caps = dataclasses.replace(spec.caps, **kw)
    return spec


@mutation(CHECKS, REST_ALL, REST_NONE, expect="the claim must not open a browser through a dead proxy")
@mutation(CHECKS, REST_FIXED, REST_FIXED_MUTATED,
          expect="assert db.all(\"SELECT pool FROM account_pools WHERE account_id = %s AND rest_")
def test_a_failed_proxy_probe_rests_the_account_on_every_pool_for_the_providers_proxy_rest(db):
    a = new_account(db, checked=True, proxy=("10.0.0.1", 8080))
    k = jobkit.key(db)
    jobkit.run(db, lambda p: jobkit.create(p, k, "x"))

    async def scenario(p):
        engine = KitEngine()
        engine.verdict = ProxyVerdict(False, "refused")
        env = make_env(p, engine, spec=with_caps(KitSpec(), proxy_rest_s=600))
        return (await check_account(env, a)).outcome, await jobkit.claim(p)

    outcome, claimed = worker(db, scenario)
    assert outcome == "proxy_bad"
    assert claimed is None, "the claim must not open a browser through a dead proxy"
    assert db.all("SELECT pool FROM account_pools WHERE account_id = %s AND rest_until > clock_timestamp() "
                  "+ interval '500 s' AND rest_until < clock_timestamp() + interval '700 s' ORDER BY pool",
                  a) == [("image",), ("video",)]


def test_a_provider_with_no_pool_cools_the_account_down_instead_of_resting_a_pool():
    env = SimpleNamespace(caps=with_caps(KitSpec(), pools=(), proxy_rest_s=900).caps)
    effects = checks.proxy_bad_effects(env, "")
    assert [type(e) for e in effects] == [ProxyBad, Cooldown] and effects[0].reason == "proxy check failed"


@mutation(CHECKS, INTERVAL, INTERVAL_FIXED,
          expect="assert db.one(\"SELECT extract(epoch FROM check_due_at - clock_timestamp()) BETWE")
def test_the_next_check_is_due_after_the_providers_check_interval(db):
    a = new_account(db, checked=True)

    async def scenario(p):
        env = make_env(p, KitEngine(), spec=with_caps(KitSpec(), check_interval_s=7200), retry_s=300)
        return (await check_account(env, a)).outcome

    assert worker(db, scenario) == "checked"
    assert db.one("SELECT extract(epoch FROM check_due_at - clock_timestamp()) BETWEEN 7100 AND 7200 FROM accounts "
                  "WHERE id = %s", a) == (True,)


@mutation(CHECKS, ACCOUNT_LOCK, CAPACITY_FIRST, expect="deadlock detected")
def test_a_check_takes_the_account_row_before_the_capacity_rows_like_the_claim(db):
    """The claim holds the account row and then takes a capacity row; a check that took a capacity row first and then
    the account row (for a Balance effect) would deadlock with it."""
    a = new_account(db, checked=True)
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at) "
           "VALUES (%s, 'video', 3, clock_timestamp(), clock_timestamp() + interval '2 days')", a)
    db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'image')", a)       # a checked account has both

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", effects=(Balance("video", 2),))
        env = make_env(p, engine)
        reached, gate = asyncio.Event(), asyncio.Event()

        async def pause():                              # the check holds its lease and is about to record
            reached.set()
            await gate.wait()

        async def claimer():
            await reached.wait()
            async with tx(p) as conn:
                await conn.execute("SELECT 1 FROM accounts WHERE id = %s FOR NO KEY UPDATE", (a,))
                gate.set()                              # the check now records, and meets the claim's account lock
                await asyncio.sleep(1.5)
                await conn.execute("SELECT 1 FROM account_pools WHERE account_id = %s FOR UPDATE", (a,))

        async def checker():
            return (await check_account(env, a)).outcome

        failpoints.arm("check.recording", pause)
        try:
            _, outcome = await asyncio.gather(claimer(), checker())
        finally:
            failpoints.disarm("check.recording")
            gate.set()
        return outcome

    assert worker(db, scenario) == "checked"
    assert db.one("SELECT reported_left FROM account_pools WHERE account_id = %s AND pool = 'video'", a) == (2,)


@mutation(CHECKS, NO_ACCOUNT_LOCK, '"", (ids,))).fetchall()', expect="cannot be deleted")
def test_a_delete_beside_the_first_check_of_an_account_with_some_pool_rows_missing_waits_and_neither_fails(db):
    """The account has its video row only. The check inserts the image row while the delete wants the account row
    FOR UPDATE: the delete waits for the check's whole transaction, then removes what is there. No deadlock, no
    foreign key error, and the account is gone at the end."""
    a = new_account(db, checked=False, due_s=None)
    db.run("INSERT INTO account_pools (account_id, pool) VALUES (%s, 'video')", a)

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive")
        env = make_env(p, engine)
        reached, gate = asyncio.Event(), asyncio.Event()

        async def pause():
            reached.set()
            await gate.wait()

        async def delete():
            async with tx(p) as conn:
                return await delete_unused_account(conn, a)

        failpoints.arm("check.apply.accounts_locked", pause)
        try:
            checking = asyncio.create_task(check_account(env, a))
            await asyncio.wait_for(reached.wait(), 5)           # the check holds the account row, rows still missing
            deleting = asyncio.create_task(delete())
            await asyncio.sleep(1.5)                            # past Postgres's deadlock timeout
            gate.set()
            return await asyncio.gather(checking, deleting, return_exceptions=True)
        finally:
            failpoints.disarm("check.apply.accounts_locked")
            gate.set()

    checked, deleted = worker(db, scenario)
    assert not isinstance(checked, BaseException) and not isinstance(deleted, BaseException), (checked, deleted)
    assert checked.outcome == "checked" and deleted == []
    assert db.one("SELECT count(*) FROM accounts WHERE id = %s", a)[0] == 0
    assert db.one("SELECT count(*) FROM account_pools WHERE account_id = %s", a)[0] == 0


@mutation(CHECKS, NO_ACCOUNT_LOCK, '"", (ids,))).fetchall()', expect="violates foreign key constraint")
def test_a_twin_deleted_while_a_merge_creates_its_rows_fails_the_check_for_a_retry_without_a_database_error(db):
    """The twin has no capacity rows. A delete of the twin is still open (it holds the row FOR UPDATE) when the merge
    wants to create the twin's rows: the merge waits for the account lock, finds the twin gone and retries later,
    instead of inserting a row for an account that vanishes."""
    twin = new_account(db, identity="who@x", checked=True, credential=b"old-cred")
    new = new_account(db, checked=False, credential=b"new-cred")

    async def scenario(p):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x")
        env = make_env(p, engine)
        async with tx(p) as side:
            await delete_unused_account(side, twin)             # not committed yet
            checking = asyncio.create_task(check_account(env, new), context=Context())
            await asyncio.sleep(1.0)                            # the merge now waits for the twin's row
        return await checking

    result = worker(db, scenario)
    assert result.outcome == "failed", result
    assert db.one("SELECT count(*) FROM accounts WHERE id = %s", twin)[0] == 0
    assert account_row(db, new)["identity"] is None and db.one("SELECT count(*) FROM leases")[0] == 0
