"""Account checks (invariants I14, I24, I28) and the worker environment the account work shares.

A check is a task under `asyncio.timeout`, holding a `check` lease with a hard bound and (browser providers) a check
token from the semaphore. The probe of a declared proxy runs first. The outcome is applied in one shielded
transaction fenced by the lease id: the `Health` effects through `pool.apply_effects`, the `state_patch` merged into
the account's `provider_state`, the schedule columns, and the identity (a known identity merges this new account into
the existing row and removes it through the same cleanup as a delete). A check that reads the account also measures its
proxy's exit address and latency (through the proxy only, never direct) and stores them with the result; every outcome
ends with the account's health recorded for its history, and a failure is kept among its errors.

`disabled` accounts are never checked (a person's decision, no sending); `dead` ones still are. Never-checked accounts
come first, then by `check_due_at`. An unreadable result changes nothing and keeps `last_checked_at`, so an account
that was never read stays unclaimable.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from relay_core import failpoints, history, leases, net, pool
from relay_core.budget import BrowserBudget, Purpose, Token
from relay_core.db import AccountInUse, delete_unused_account, tx
from relay_core.jobs.runctx import Merge, Renewal
from relay_core.providers import (
    AccountView,
    Caps,
    Cooldown,
    Dead,
    Health,
    ProxyBad,
    ProxyVerdict,
    Rest,
    Revive,
    Success,
    Suspect,
)
from relay_core.redact import redact, redact_log
from relay_core.storage import ObjectStore

log = logging.getLogger(__name__)

RETRY_S = 300.0              # next attempt after an unreadable result, a failure or a bad proxy
MAX_TASKS = 4               # account checks one worker runs at once
TIMEOUT_SHARE = 0.8          # the task timeout is this share of the lease bound: the failure is still recorded
SHOT_NAME = "debug/0.png"
TIME_LIMIT = "the check ran past its time limit"


def shot_key(account_id) -> str:
    """The one screenshot object of an account (latest only): `<account_id>/debug/0.png` follows the bucket layout."""
    return "%s/%s" % (account_id, SHOT_NAME)


@dataclass
class Env:
    """What the account loops of one provider's worker share (checks, commands, titles, catalog refresh)."""
    db: AsyncConnectionPool
    spec: Any
    engine: Any
    settings: Any
    owner: str
    keeper: leases.LeaseKeeper
    load_account: Callable[[Any], Coroutine[Any, Any, AccountView]]
    store: ObjectStore | None = None
    budget: BrowserBudget | None = None
    check_hard_s: float = leases.CHECK_HARD_S
    check_every_s: float | None = None   # a test or soak override; the provider's `Caps.check_interval_s` otherwise
    retry_s: float = RETRY_S
    ttl_s: float = leases.LEASE_TTL_S
    command_hard_s: Callable[[str], float] = leases.command_hard_s
    # the proxy's exit address and latency, measured through the account's proxy by every check (a test replaces it)
    exit_probe: Callable[[Any], Coroutine[Any, Any, tuple[str | None, int | None]]] = net.exit_address

    @property
    def caps(self) -> Caps:
        return self.spec.caps

    @property
    def provider(self) -> str:
        return self.spec.name

    def pools(self) -> dict[str, pool.PoolConfig]:
        return pool.pool_configs(self.spec.name, self.caps, self.settings.get)

    def drained(self) -> bool:
        return not self.settings.get(self.provider + ".claims_enabled")

    def take_token(self, purpose: Purpose) -> Token | None:
        """A semaphore token for a browser provider; None when none is free. Other providers need none."""
        if self.caps.execution != "browser":
            return None
        if self.budget is None:
            raise ValueError("a browser provider needs a browser budget")
        return self.budget.try_acquire(purpose)

    def needs_token(self) -> bool:
        return self.caps.execution == "browser"

    def check_interval_s(self) -> float:
        return self.caps.check_interval_s if self.check_every_s is None else self.check_every_s


class AccountCtx:
    """The RunCtx of an account run that is not a job (check, command, title sweep). It has no job to mark."""

    def __init__(self, env: Env, lease_id: int | None, account: AccountView, token: Token | None):
        self._env, self._lease_id = env, lease_id
        self.account, self.browser_token, self.settings = account, token, env.settings
        self.log = logging.getLogger("relay.engine.%s" % env.provider)
        self._renewal = Renewal(env.db, account)

    async def fence(self) -> None:
        if self._lease_id is not None:        # a read-only call (the title sweep) holds no lease: nothing to lose
            await leases.fence_once(self._env.db, self._lease_id)

    async def mark_sent(self) -> None:
        raise RuntimeError("an account run has no job to mark sent")

    async def report_ref(self, ref: str, state: Any) -> None:
        raise RuntimeError("an account run has no job to report a ref for")

    async def event(self, text: str, stage: str, source: str) -> None:
        self.log.info("%s %s: %s", source, stage, redact_log(text))

    async def evidence(self, name: str, data: bytes, mime: str) -> None:
        self.log.info("evidence %r not kept: an account run has no job to attach evidence to", name)

    async def renew_credential(self, blob: bytes, merge: Merge | None = None) -> bytes | None:
        return await self._renewal(blob, merge)


async def shielded(work: Coroutine) -> Any:
    """Run `work` to its end even if the caller is cancelled (a result that was earned is recorded)."""
    inner = asyncio.ensure_future(work)
    return await asyncio.shield(inner)


async def engine_call(env: Env, lease_id: int, token: Token | None, account_id, kind: str,
                      call: Callable[[AccountCtx], Coroutine[Any, Any, Any]]) -> Any:
    """Run `call(ctx)` for an account that holds the command lease `lease_id`: the account is loaded and the call is
    cut at the kind's hard bound (a share of it, so a failure can still be recorded). Raises `TimeoutError` at the
    bound. No connection is held while it runs. The caller keeps the lease alive (`env.keeper.holding`) until its
    result is applied."""
    async with asyncio.timeout(env.command_hard_s(kind) * TIMEOUT_SHARE):
        view = await env.load_account(account_id)
        return await call(AccountCtx(env, lease_id, view, token))


# ---- remove screenshot objects ----------------------------------------------------------------------------------
async def remove_shots(env: Env, account_id) -> list[str]:
    """Remove the account's screenshot objects: the key its row names and the layout key. Foreign I/O: never call it
    inside a transaction. A missing object is not an error (the store answers success)."""
    if env.store is None:
        return []
    async with tx(env.db) as conn:
        rows = await (await conn.execute("SELECT object_key FROM account_shots WHERE account_id = %s",
                                         (account_id,))).fetchall()
    keys = sorted({r[0] for r in rows} | {shot_key(account_id)})
    for key in keys:
        await env.store.remove(key)
    return keys


# ---- schedule ---------------------------------------------------------------------------------------------------
async def due_accounts(db: AsyncConnectionPool, provider: str, limit: int) -> list[uuid.UUID]:
    """Accounts whose check is due, never-checked first, then by `check_due_at`. `disabled` never."""
    async with tx(db) as conn:
        rows = await (await conn.execute(
            "SELECT id FROM accounts WHERE provider = %s AND state <> 'disabled' "
            "AND (check_due_at IS NULL OR check_due_at <= clock_timestamp()) "
            "ORDER BY (last_checked_at IS NOT NULL), check_due_at NULLS FIRST, created_at, id LIMIT %s",
            (provider, limit))).fetchall()
    return [r[0] for r in rows]


@dataclass(frozen=True)
class CheckResult:
    outcome: str          # checked | unknown | signed_out | proxy_bad | failed | merged | busy | no_token | skipped
    #                       | lease_lost | superseded
    detail: str = ""
    account_id: Any = None   # the surviving account after a merge


@dataclass
class _Report:
    health: Health | None = None
    proxy: ProxyVerdict | None = None
    error: str | None = None
    credential_gen: int | None = None    # the credential the engine read; None when it never got that far
    exit: tuple[str | None, int | None] = (None, None)   # the proxy's exit address and latency (ms); none without one


async def check_account(env: Env, account_id, *, command_id=None) -> CheckResult:
    """One check of one account. `busy` and `no_token` change nothing (the scheduler tries again); a `disabled` or a
    missing account is `skipped`. `command_id` is the command that asked: a merge re-points it at the survivor."""
    token = env.take_token(Purpose.CHECK)
    if env.needs_token() and token is None:
        return CheckResult("no_token")
    try:
        status, lease_id = await _take_lease(env, account_id)
        if lease_id is None:
            return CheckResult(status)
        return await _run_and_record(env, account_id, lease_id, token, command_id)
    finally:
        if token is not None:
            token.release()


async def _take_lease(env: Env, account_id) -> tuple[str, int | None]:
    async with tx(env.db) as conn:
        row = await (await conn.execute("SELECT state FROM accounts WHERE id = %s", (account_id,))).fetchone()
        if row is None or row[0] == "disabled":
            return "skipped", None
        lease_id = await leases.acquire(conn, account_id=account_id, purpose="check", owner=env.owner,
                                        hard_s=env.check_hard_s, ttl_s=env.ttl_s, max_live=1)
    return ("busy", None) if lease_id is None else ("ok", lease_id)


def proxy_bad_effects(env: Env, reason: str) -> list:
    """A failed proxy probe: the account is marked bad and rests on EVERY pool for the provider's proxy rest, so
    the claim does not open a browser through a dead proxy. A provider with no pools cools the account down instead."""
    until = datetime.now(UTC) + timedelta(seconds=env.caps.proxy_rest_s)
    why = "proxy_bad"
    rests = [Rest(p.name, until, why) for p in env.caps.pools] or [Cooldown(why, until)]
    return [ProxyBad(reason or "proxy check failed"), *rests]


def error_text(e: BaseException) -> str:
    return redact_log(str(e) or type(e).__name__)


async def _run_and_record(env: Env, account_id, lease_id: int, token: Token | None, command_id) -> CheckResult:
    """The engine part, then its outcome recorded, inside one `holding`: the keeper extends the lease right up to the
    fenced write. A lease the keeper could not extend cuts the task at its hard bound; the failure is then recorded
    after the block, in a new transaction, by `_apply` (which accepts an expired lease that is still there, and
    refuses a deleted one)."""
    report: _Report | None = None
    try:
        async with env.keeper.holding(lease_id) as settle:
            report = await _run(env, account_id, lease_id, token)
            try:
                return await _apply_report(env, account_id, lease_id, report, command_id, settle)
            except Exception:
                # a crash while recording (a database error): give the account back now, not at the lease's TTL
                with contextlib.suppress(Exception):
                    async with tx(env.db) as conn:
                        await leases.release(conn, lease_id)
                raise
    except leases.LeaseLost:
        if report is not None and report.error is None:
            log.warning("check of %s: lease %s lost before its result was recorded", account_id, lease_id)
            return CheckResult("lease_lost")        # a healthy result needs a live lease
        log.warning("check of %s: lease %s lost, recording the failure", account_id, lease_id)
        return await _apply_report(env, account_id, lease_id, report or _Report(error=TIME_LIMIT), command_id)


async def _run(env: Env, account_id, lease_id: int, token: Token | None) -> _Report:
    """The engine part, with no connection held, and nothing recorded."""
    report = _Report()
    try:
        async with asyncio.timeout(env.check_hard_s * TIMEOUT_SHARE):
            view = await env.load_account(account_id)
            report.credential_gen = view.credential_gen
            if env.caps.has_probe_proxy and view.proxy is not None:
                report.proxy = await env.engine.probe_proxy(view.proxy)
            if report.proxy is None or report.proxy.ok:
                report.health = await env.engine.check_account(AccountCtx(env, lease_id, view, token))
                if view.proxy is not None:      # never measured direct: an account without a proxy has no exit
                    try:
                        report.exit = await env.exit_probe(view.proxy)
                    except Exception as e:      # a bonus fact: it never fails the check it rides on
                        log.warning("exit address of %s: %s", account_id, error_text(e))
    except TimeoutError:
        report.error = TIME_LIMIT
    except leases.LeaseLost:
        raise
    except Exception as e:      # an engine or a decrypt failure: recorded, never raised into the loop
        report.error = error_text(e)
    return report


# ---- apply ------------------------------------------------------------------------------------------------------
async def _apply_report(env: Env, account_id, lease_id: int, report: _Report, command_id,
                        settle: Callable[[], None] = lambda: None) -> CheckResult:
    """Outside the timeout, shielded, one transaction fenced by the lease id. Screenshot I/O comes around it, still
    inside the caller's `holding` (the lease is kept alive until the fenced write); `settle` is called right before
    that write, so the keeper stops judging the lease the write deletes (a beat after the commit, during the
    screenshot upload, must not turn a recorded check into `lease_lost`)."""
    health = report.health
    if health is not None and health.identity:
        twin, gen = await _twin(env, account_id, health.identity)
        # objects first: the delete below runs in a transaction. Not for a report on a credential a merge has replaced
        # (`_apply` refuses it): its shots are the account's current ones.
        if twin is not None and report.credential_gen in (None, gen):
            await remove_shots(env, account_id)
    await failpoints.hit("check.recording")
    settle()
    try:
        result, dropped = await shielded(_apply(env, account_id, lease_id, report, command_id))
    except leases.LeaseLost:
        log.warning("check of %s: result dropped, lease %s is not live", account_id, lease_id)
        return CheckResult("lease_lost")
    except AccountInUse as e:
        log.error("check of %s: identity collision with an account in use: %s", account_id, e)
        report = _Report(error="the identity belongs to another account and this one is in use")
        try:
            result, dropped = await shielded(_apply(env, account_id, lease_id, report, command_id))
        except leases.LeaseLost:
            return CheckResult("lease_lost")
    for key in dropped:                                # shots a merge removed after the object-first pass
        if env.store is not None:
            await env.store.remove(key)
    if health is not None and health.screenshot and result.outcome in ("checked", "merged", "signed_out"):
        await _store_shot(env, result.account_id or account_id, health.screenshot)
    return result


async def _twin_in(conn: AsyncConnection, account_id, identity: str):
    """The other account of the same provider that already has this identity (a plain read: no lock)."""
    row = await (await conn.execute(
        "SELECT o.id FROM accounts a JOIN accounts o ON o.provider = a.provider AND o.identity = %s "
        "AND o.id <> a.id WHERE a.id = %s", (identity, account_id))).fetchone()
    return None if row is None else row[0]


async def _twin(env: Env, account_id, identity: str):
    """The twin, and the account's credential generation now (None when the account is gone)."""
    async with tx(env.db) as conn:
        gen = await (await conn.execute("SELECT credential_gen FROM accounts WHERE id = %s", (account_id,))).fetchone()
        return await _twin_in(conn, account_id, identity), gen and gen[0]


async def _store_shot(env: Env, account_id, data: bytes) -> None:
    """The latest screenshot only: one object key per account, then its row. The object goes first; an account that
    vanished meanwhile loses it again."""
    if env.store is None:
        return
    key = shot_key(account_id)
    await env.store.put_bytes(key, data, "image/png")
    try:
        async with tx(env.db) as conn:
            await conn.execute(
                "INSERT INTO account_shots (account_id, object_key) VALUES (%s, %s) "
                "ON CONFLICT (account_id) DO UPDATE SET object_key = EXCLUDED.object_key, "
                "taken_at = clock_timestamp()", (account_id, key))
    except Exception:
        await env.store.remove(key)
        raise


async def _fence(conn: AsyncConnection, lease_id: int, *, expired_ok: bool) -> None:
    """The lease fence. A failure record (`expired_ok`) is also accepted when the lease has run out but its row is
    still there: it writes only the error and the retry time, and the reaper that would take the lease changes
    nothing else. A healthy result needs a live lease; a deleted lease is `LeaseLost` for both."""
    if not expired_ok:
        await leases.fence(conn, lease_id)
    elif await (await conn.execute("SELECT 1 FROM leases WHERE id = %s FOR UPDATE", (lease_id,))).fetchone() is None:
        raise leases.LeaseLost(lease_id)


async def _apply(env: Env, account_id, lease_id: int, report: _Report, command_id) -> tuple[CheckResult, list[str]]:
    async with tx(env.db) as conn:
        await _fence(conn, lease_id, expired_ok=report.error is not None)
        health = report.health
        merging = report.error is None and health is not None and bool(health.identity) and (
            report.proxy is None or report.proxy.ok)
        # A merge touches two accounts. The twin is read before any lock; then both account rows are locked in id
        # order, first of everything (a twin delete, a claim and another merge take the account row first too, so no
        # cycle can form). Capacity rows are created or locked after that, in any order.
        twin = await _twin_in(conn, account_id, health.identity) if merging else None
        ids = [account_id] if twin is None else [account_id, twin]
        rows = await (await conn.execute("SELECT id, credential_gen FROM accounts WHERE id = ANY(%s) ORDER BY id "
                                         "FOR NO KEY UPDATE", (ids,))).fetchall()
        await failpoints.hit("check.apply.accounts_locked")
        gens = dict(rows)
        if account_id not in gens:
            return CheckResult("skipped", "the account is gone"), []
        if report.credential_gen is not None and gens[account_id] != report.credential_gen:
            # a merge moved another credential into this row while the engine read the old one: what it saw says
            # nothing about the row now (an old signed-out jar must not kill a fresh one). Nothing is written: the
            # merge already made the next check due now, and the account did not fail
            await leases.release(conn, lease_id)
            return CheckResult("superseded", "a merge replaced the credential; a new check is due now"), []
        if report.error is not None:
            await _fail(conn, env, account_id, report.error)
            await leases.release(conn, lease_id)
            await history.record_health(conn, account_id)
            return CheckResult("failed", report.error), []
        if report.proxy is not None and not report.proxy.ok:
            await pool.apply_effects(conn, account_id, proxy_bad_effects(env, report.proxy.reason), env.caps,
                                     env.pools())
            await _fail(conn, env, account_id, None)
            await leases.release(conn, lease_id)
            await history.record_health(conn, account_id)
            return CheckResult("proxy_bad", report.proxy.reason), []
        if merging and await _twin_in(conn, account_id, health.identity) != twin:
            # the other account changed while this check ran (it took the identity, or lost it): the locks held are
            # not the ones the merge needs, so nothing is merged now; the next check starts from the new state
            await _fail(conn, env, account_id, "the identity match changed while the check was recorded")
            await leases.release(conn, lease_id)
            await history.record_health(conn, account_id)
            return CheckResult("failed", "the identity match changed; the check is retried"), []
        if twin is not None:
            await pool.ensure_rows(conn, twin, env.pools())         # the survivor has every declared pool row
            dropped = await _merge(conn, account_id, twin, lease_id, health, command_id)
            return CheckResult("merged", account_id=twin), dropped
        await pool.ensure_rows(conn, account_id, env.pools())
        was = (await (await conn.execute("SELECT state FROM accounts WHERE id = %s", (account_id,))).fetchone())[0]
        await _record(conn, env, account_id, health, report.proxy, report.exit)
        now = (await (await conn.execute("SELECT state FROM accounts WHERE id = %s", (account_id,))).fetchone())[0]
        await leases.release(conn, lease_id)
        await history.record_health(conn, account_id)
        if health.state == "unknown":
            # a check that read nothing for certain but whose strike ended the account tells the admin so
            why = next((e.reason for e in health.effects if isinstance(e, Suspect)), "")
            return CheckResult("signed_out" if now == "dead" and was != "dead" else "unknown", why), []
        return CheckResult("checked"), []


async def _fail(conn: AsyncConnection, env: Env, account_id, error: str | None) -> None:
    """A failure or a bad proxy: the account keeps its state, the next try is `retry_s` away. A failure (`error`, a
    check that could not read the account) is kept in its errors and stamps `check_failed_at`."""
    await conn.execute(
        "UPDATE accounts SET last_error = COALESCE(%(e)s, last_error), updated_at = clock_timestamp(), "
        "check_failed_at = CASE WHEN %(e)s::text IS NULL THEN check_failed_at ELSE clock_timestamp() END, "
        "check_due_at = clock_timestamp() + make_interval(secs => %(s)s) WHERE id = %(a)s",
        {"e": error, "s": env.retry_s, "a": account_id})
    if error is not None:
        await history.record_error(conn, account_id, error)


async def _record(conn: AsyncConnection, env: Env, account_id, health: Health, proxy: ProxyVerdict | None,
                  exit_: tuple[str | None, int | None] = (None, None)) -> None:
    base: list = []
    if health.state == "alive":
        base = [Revive("the check found the account alive"), Success()]
    elif health.state == "signed_out":
        base = [Dead("signed out")]
    await pool.apply_effects(conn, account_id, [*base, *health.effects], env.caps, env.pools())
    read = health.state != "unknown"
    # A cooldown that asks for a recheck means the provider read the page and doubted it: the next check is at the
    # cooldown's end, not at the retry time. A dead account keeps the normal interval instead, so a dead jar is not
    # opened every few minutes.
    recheck = [e.until for e in health.effects if isinstance(e, Cooldown) and e.recheck]
    await conn.execute(
        "UPDATE accounts SET identity = COALESCE(%(i)s, identity), display_name = COALESCE(%(d)s, display_name), "
        "provider_state = provider_state || %(p)s::jsonb, "
        "last_checked_at = CASE WHEN %(read)s THEN clock_timestamp() ELSE last_checked_at END, "
        "check_due_at = CASE WHEN state <> 'dead' AND %(ru)s::timestamptz IS NOT NULL THEN %(ru)s::timestamptz "
        "ELSE clock_timestamp() + make_interval(secs => %(due)s) END, "
        "proxy_ok = CASE WHEN %(px)s THEN true ELSE proxy_ok END, "
        "proxy_checked_at = CASE WHEN %(px)s THEN clock_timestamp() ELSE proxy_checked_at END, "
        "proxy_exit_ip = %(ip)s, proxy_latency_ms = %(ms)s, "
        "proxy_exit_at = CASE WHEN proxy_host IS NULL THEN NULL ELSE clock_timestamp() END, check_failed_at = NULL, "
        "updated_at = clock_timestamp() WHERE id = %(a)s",
        {"i": health.identity, "d": health.display_name, "p": Jsonb(redact(dict(health.state_patch))),
         "read": read, "due": env.check_interval_s() if read or recheck else env.retry_s,
         "px": proxy is not None and proxy.ok, "ip": exit_[0], "ms": exit_[1],
         "ru": max(recheck) if recheck else None, "a": account_id})


async def _merge(conn: AsyncConnection, new_id, old_id, lease_id: int, health: Health, command_id) -> list[str]:
    """The first check found a known identity: the credential moves to the existing row (generation + 1, its proxy
    kept unless this import gave one, `available` unless `disabled`) and this new row goes through the delete cleanup.
    Returns the shot keys the cleanup removed. `AccountInUse` rolls everything back."""
    new = await (await conn.execute(
        "SELECT credential_enc, proxy_host, proxy_port, proxy_auth_enc FROM accounts WHERE id = %s",
        (new_id,))).fetchone()
    if command_id is not None:        # the command that asked outlives the account it was queued for
        await conn.execute("UPDATE commands SET account_id = %s WHERE id = %s", (old_id, command_id))
    dropped = await delete_unused_account(conn, new_id, own_leases=[lease_id])
    credential, host, port, auth = new
    await conn.execute(
        "UPDATE accounts SET credential_enc = %(c)s, credential_gen = credential_gen + 1, "
        "state_reason = CASE WHEN state = 'disabled' THEN state_reason END, "
        "state = CASE WHEN state = 'disabled' THEN state ELSE 'available' END, strikes = 0, strike_at = NULL, "
        "last_error = NULL, "
        "display_name = COALESCE(%(d)s, display_name), provider_state = provider_state || %(p)s::jsonb, "
        "check_due_at = clock_timestamp(), updated_at = clock_timestamp() WHERE id = %(a)s",
        {"c": credential, "d": health.display_name, "p": Jsonb(redact(dict(health.state_patch))), "a": old_id})
    if host is not None:
        await conn.execute("UPDATE accounts SET proxy_host = %s, proxy_port = %s, proxy_auth_enc = %s, "
                           "proxy_ok = NULL, proxy_checked_at = NULL WHERE id = %s", (host, port, auth, old_id))
    return dropped


# ---- the scheduler ----------------------------------------------------------------------------------------------
class Checks:
    """Starts due checks as tasks, at most `max_tasks` at once. Drain mode does not stop them (owner, 23/10): a drain
    stops new jobs only, and a check is what keeps an account's session alive while no job runs (a provider that
    rotates its session in the check, flow's Google cookies, loses the account within ~20 min without one). A person
    stops the checks of an account by disabling it."""

    def __init__(self, env: Env, *, max_tasks: int = MAX_TASKS):
        self.env, self.max_tasks = env, max_tasks
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}

    async def tick(self) -> list[uuid.UUID]:
        """Start the checks that are due and fit; returns their account ids in start order."""
        if len(self._tasks) >= self.max_tasks:
            return []
        want = self.max_tasks - len(self._tasks)
        due = await due_accounts(self.env.db, self.env.provider, want + len(self._tasks))
        started = [a for a in due if a not in self._tasks][:want]
        for account_id in started:
            task = asyncio.create_task(self._one(account_id), name="check-%s" % account_id)
            self._tasks[account_id] = task
        return started

    async def _one(self, account_id) -> None:
        try:
            result = await check_account(self.env, account_id)
            # the reason too: `accounts.last_error` keeps it only until the next good check clears it
            log.info("check of %s: %s%s", account_id, result.outcome,
                     ": " + redact_log(result.detail) if result.detail else "")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("check of %s crashed: %s", account_id, error_text(e))
        finally:
            self._tasks.pop(account_id, None)

    async def wait(self) -> None:
        """Until every started check has ended."""
        while self._tasks:
            waited = list(self._tasks.items())
            await asyncio.gather(*(t for _, t in waited), return_exceptions=True)
            for account_id, task in waited:      # a task cancelled before its first step never ran its own `finally`
                if self._tasks.get(account_id) is task:
                    del self._tasks[account_id]

    async def run(self, stop: asyncio.Event, tick_s: float = 5.0) -> None:
        while not stop.is_set():
            try:
                await self.tick()
            except Exception as e:
                log.error("check scheduler tick failed: %s", error_text(e))
            try:
                await asyncio.wait_for(stop.wait(), tick_s)
            except TimeoutError:
                pass
        await self.wait()
