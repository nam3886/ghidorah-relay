"""The command runner of one provider's worker (contracts 4.4, 5.1, 7.6, D37, D39; invariants I14, I24, I28).

A command is claimed with `FOR UPDATE SKIP LOCKED` (`attempts + 1`). A kind that works through an account runs under a
`command` lease on it (fenced, hard-bounded by `leases.command_hard_s(kind)`, stored as `lease_id`) and, for a browser
provider, a command token; its result is applied in one transaction that first fences the lease. Failure text and
results go through the redactors; the table has no error column, so a failure is `{"error": ...}` in `result`.

Kinds the core runs itself, all as the worker role (the api only inserts the row):
    delete_account   args {"account_id"}: an unused account only; the shot objects go FIRST (see below)
    wake             args {"account_id"}: clear the cooldown and every pool rest
    check            args {"account_id"} (or the row's account_id): one check now, under its own check lease
    check_all        every account of the provider (not `disabled`) becomes due now; the scheduler runs them
    proxy_recheck    args {"account_id"}: probe the proxy, store the verdict, make the account due
Kinds the engine runs (`run_command`): recover, list_conversations, refresh_catalog, titles, ...

Delete (R3, R4). The command takes no account lease and its own row has `account_id` NULL, so it neither blocks on
itself nor deletes itself (a row that names the account is detached first). The objects are removed BEFORE the
transaction: a crash at any point leaves either a row whose object is gone (the next check stores a new one) or
nothing at all, never an object without a row. The screenshot key is `<account_id>/debug/0.png`, so a stray one is
also an orphan for the storage sweep.
"""

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import errors as pgerrors
from psycopg.types.json import Jsonb

from relay_core import failpoints, leases, pool
from relay_core.budget import Purpose, Token
from relay_core.db import AccountInUse, AccountNotFound, delete_unused_account, tx
from relay_core.db.accounts import account_in_use
from relay_core.jobs import titles
from relay_core.jobs.checks import (
    Checks,
    Env,
    check_account,
    engine_call,
    error_text,
    proxy_bad_effects,
    remove_shots,
    shielded,
)
from relay_core.jobs.fetcher import FetchFailure, store_output
from relay_core.jobs.recheck import StoredOutput, recover_in_tx
from relay_core.providers import Output
from relay_core.redact import redact, redact_log

log = logging.getLogger(__name__)

CORE_KINDS = ("delete_account", "wake", "check", "check_all")
# Kinds that hold a command lease: every kind with a hard bound except `check`, which takes a check lease itself.
LEASED = tuple(k for k in leases.COMMAND_HARD_S if k != "check")
_PICK = 5
# A command whose transaction the database aborted to break a deadlock (or a serialization conflict) goes back to
# `queued`; this many claims in all, then it fails as any other error. Every core kind is idempotent: delete (a gone
# account is "already gone", the shot removal repeats), wake and check all (they set a state), check (a check is
# repeatable), and the leased kinds (recover skips an output the job already has by upstream id and removes the
# objects of a run that did not commit; the snapshot and title writers are upserts; a probe and a list only read).
MAX_DEADLOCK_ATTEMPTS = 3
_RETRYABLE = (pgerrors.DeadlockDetected, pgerrors.SerializationFailure)


class CommandError(Exception):
    """A command that cannot run as asked (bad arguments, an unknown kind); the text is stored, redacted."""


@dataclass
class Claimed:
    id: uuid.UUID
    kind: str
    account_id: Any
    job_id: Any
    args: Mapping[str, Any]
    lease_id: int | None
    token: Token | None


def _target(account_id, args: Mapping[str, Any]) -> uuid.UUID:
    raw = args.get("account_id") or account_id
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except ValueError:
        raise CommandError("the command names no valid account") from None


# ---- claim ------------------------------------------------------------------------------------------------------
async def _pick_account(conn, env: Env) -> tuple[Any, int] | None:
    """An account for a leased command that names none (a catalog refresh): the least recently used one that is
    available, checked and free, with its lease: (account_id, lease_id), or None when none is free."""
    rows = await (await conn.execute(
        "SELECT id FROM accounts WHERE provider = %s AND state = 'available' AND last_checked_at IS NOT NULL "
        "ORDER BY last_used_at NULLS FIRST, id LIMIT %s", (env.provider, _PICK))).fetchall()
    for (account_id,) in rows:
        lease_id = await _lease(conn, env, account_id, "refresh_catalog")
        if lease_id is not None:
            return account_id, lease_id
    return None


async def _lease(conn, env: Env, account_id, kind: str) -> int | None:
    return await leases.acquire(conn, account_id=account_id, purpose="command", owner=env.owner,
                                hard_s=env.command_hard_s(kind), ttl_s=env.ttl_s, max_live=1,
                                skip_locked=True)


async def claim_command(env: Env) -> Claimed | None:
    """The oldest queued command of this provider that can start now, or None. A leased command whose account is
    busy, or that finds no token, stays queued for the next tick."""
    async with tx(env.db) as conn:
        waiting = await (await conn.execute(
            "SELECT id FROM commands WHERE provider = %s AND status = 'queued' ORDER BY created_at, id LIMIT 10",
            (env.provider,))).fetchall()
        for (candidate,) in waiting:
            # one row at a time: a runner that holds ten rows would make its neighbours think the queue is empty
            row = await (await conn.execute(
                "SELECT id, kind, account_id, job_id, args FROM commands WHERE id = %s AND status = 'queued' "
                "FOR UPDATE SKIP LOCKED", (candidate,))).fetchone()
            if row is None:
                continue
            cid, kind, account_id, job_id, args = row
            lease_id, token = None, None
            if kind in LEASED:
                token = env.take_token(Purpose.COMMAND)
                if env.needs_token() and token is None:
                    continue
                try:
                    picked = await _lease_for(conn, env, kind, account_id, args)
                except CommandError as e:
                    await _fail_in_tx(conn, cid, str(e))
                    if token is not None:
                        token.release()
                    continue
                if picked is None:
                    if token is not None:
                        token.release()
                    continue
                account_id, lease_id = picked
            await conn.execute("UPDATE commands SET status = 'running', attempts = attempts + 1, lease_id = %s, "
                               "started_at = clock_timestamp() "
                               "WHERE id = %s", (lease_id, cid))
            return Claimed(cid, kind, account_id, job_id, args, lease_id, token)
    return None


async def _lease_for(conn, env: Env, kind: str, account_id, args: Mapping[str, Any]):
    """(account_id, lease_id) for a leased command, or None when the account is busy."""
    if account_id is None and not args.get("account_id"):
        return await _pick_account(conn, env)
    target = _target(account_id, args)
    if await (await conn.execute("SELECT 1 FROM accounts WHERE id = %s", (target,))).fetchone() is None:
        raise CommandError("no such account")
    lease_id = await _lease(conn, env, target, kind)
    return None if lease_id is None else (target, lease_id)


# ---- finish -----------------------------------------------------------------------------------------------------
async def _fail_in_tx(conn, command_id, message: str, lease_id: int | None = None) -> None:
    if lease_id is not None:                            # lease row before command row
        await leases.release(conn, lease_id)
    await conn.execute(
        "UPDATE commands SET status = 'failed', result = %s, finished_at = clock_timestamp() "
        "WHERE id = %s AND status IN ('queued', 'running')", (Jsonb({"error": redact_log(message)}), command_id))


async def _requeue(env: Env, c: Claimed) -> bool:
    """Give a command whose transaction was aborted by the database back to the queue, keeping its attempts, unless it
    has used `MAX_DEADLOCK_ATTEMPTS` claims. False: the caller fails it."""
    async with tx(env.db) as conn:
        if c.lease_id is not None:                      # lease row before command row
            await leases.release(conn, c.lease_id)
        cur = await conn.execute(
            "UPDATE commands SET status = 'queued', lease_id = NULL, started_at = NULL "
            "WHERE id = %s AND status = 'running' AND attempts < %s", (c.id, MAX_DEADLOCK_ATTEMPTS))
        return cur.rowcount == 1


async def _fail(env: Env, c: Claimed, error: BaseException | str) -> None:
    message = error if isinstance(error, str) else str(error) or type(error).__name__
    async with tx(env.db) as conn:
        await _fail_in_tx(conn, c.id, message, c.lease_id)


async def _done(env: Env, c: Claimed, result: Mapping[str, Any], *, apply=None, raw=None) -> None:
    """Mark done in one transaction; a leased command first fences its lease (`LeaseLost` = nothing written), and
    `apply(conn, env, claimed, raw)` adds the database part of its result (and a dict to merge into it) to the same
    transaction."""
    async with tx(env.db) as conn:
        if c.lease_id is not None:
            await leases.fence(conn, c.lease_id)
        if apply is not None:
            result = {**result, **await apply(conn, env, c, raw)}
        await conn.execute("UPDATE commands SET status = 'done', result = %s, finished_at = clock_timestamp() "
                           "WHERE id = %s", (Jsonb(redact(dict(result))), c.id))
        if c.lease_id is not None:
            await leases.release(conn, c.lease_id)


# ---- core kinds -------------------------------------------------------------------------------------------------
async def _delete_account(env: Env, c: Claimed) -> dict:
    target = _target(None, c.args)
    async with tx(env.db) as conn:
        reason = await account_in_use(conn, target)
    if reason is not None:                              # a used account keeps its row AND its objects
        raise AccountInUse("account %s cannot be deleted: %s" % (target, reason))
    removed = await remove_shots(env, target)           # the objects go first, outside any transaction
    await failpoints.hit("account.delete.removed")
    try:
        async with tx(env.db) as conn:
            await conn.execute("UPDATE commands SET account_id = NULL WHERE id = %s", (c.id,))
            late = await delete_unused_account(conn, target)
    except AccountNotFound:
        return {"deleted": str(target), "already_gone": True}
    await failpoints.hit("account.delete.committed")
    if env.store is not None:
        for key in late:                                # a shot stored after the first pass
            if key not in removed:
                await env.store.remove(key)
    return {"deleted": str(target), "objects_removed": len(removed)}


async def _wake(env: Env, c: Claimed) -> dict:
    target = _target(c.account_id, c.args)
    async with tx(env.db) as conn:
        if not await pool.wake_account(conn, target):
            raise CommandError("no such account")
    return {"woke": str(target)}


async def _check_all(env: Env, c: Claimed) -> dict:
    async with tx(env.db) as conn:
        # the rows are locked in id order, like a merge locks two accounts: a heap-order scan could meet it head on
        n = (await conn.execute(
            "UPDATE accounts SET check_due_at = clock_timestamp() WHERE id IN (SELECT id FROM accounts "
            "WHERE provider = %s AND state <> 'disabled' ORDER BY id FOR NO KEY UPDATE)",
            (env.provider,))).rowcount
    return {"scheduled": n}


async def _check_one(env: Env, c: Claimed) -> dict:
    result = await check_account(env, _target(c.account_id, c.args), command_id=c.id)
    if result.outcome in ("checked", "unknown", "merged", "proxy_bad"):
        return {"outcome": result.outcome, "account_id": str(result.account_id) if result.account_id else None}
    raise CommandError("the check did not run: %s%s" % (result.outcome, " - " + result.detail if result.detail else ""))


# ---- leased kinds -----------------------------------------------------------------------------------------------
async def _probe(env: Env, ctx) -> dict:
    if not env.caps.has_probe_proxy:
        raise CommandError("this provider has no proxy probe")
    if ctx.account.proxy is None:
        raise CommandError("the account has no proxy")
    verdict = await env.engine.probe_proxy(ctx.account.proxy)
    return {"ok": verdict.ok, "reason": verdict.reason}


async def _apply_probe(conn, env: Env, c: Claimed, verdict: Mapping[str, Any]) -> dict:
    if verdict["ok"]:
        await conn.execute("UPDATE accounts SET proxy_ok = true, proxy_checked_at = clock_timestamp(), "
                           "updated_at = clock_timestamp() WHERE id = %s", (c.account_id,))
    else:
        await pool.apply_effects(conn, c.account_id, proxy_bad_effects(env, verdict["reason"]), env.caps, env.pools())
    await conn.execute("UPDATE accounts SET check_due_at = clock_timestamp() WHERE id = %s", (c.account_id,))
    return {"proxy_ok": verdict["ok"], "reason": verdict["reason"]}


def recover_slot(command_id: uuid.UUID, n: int) -> int:
    """The object-key index of the n-th output one Recover stores. It comes from the command's id, never from a count
    of the job's rows read before the transaction, so two Recovers of one job (two accounts at once) never write the
    same object key. The row's own `idx` is assigned later, inside the fenced transaction."""
    return command_id.int * 1000 + n


async def _store_outputs(env: Env, c: Claimed, ctx, raw: Mapping[str, Any], stored: list[StoredOutput]) -> None:
    """Recover: fetch each new output through the engine and store the object. Foreign I/O only; the rows come
    after, in `recover_in_tx`. An output the job already has (by upstream id) is skipped before any fetch. Each
    stored output is appended to `stored` as it lands, so the caller can remove them if the result is refused."""
    if env.store is None:
        raise CommandError("recover needs the object store")
    if c.job_id is None:
        raise CommandError("recover names no job")
    outputs = [o for o in raw.get("outputs", ()) if isinstance(o, Output)]
    if not outputs or any(not o.upstream_id for o in outputs):
        raise CommandError("the engine recovered no output with an upstream id")
    async with tx(env.db) as conn:
        have = {r[0] for r in await (await conn.execute(
            "SELECT upstream_id FROM job_media WHERE job_id = %s AND role = 'output'", (c.job_id,))).fetchall()}
    for out in outputs:
        if out.upstream_id in have:
            continue
        try:                                            # the fetcher's own path: streamed, size, probe, put_file
            slot = recover_slot(c.id, len(stored))
            stored.append(await store_output(env.engine, env.store, ctx, c.job_id, slot, out))
        except FetchFailure as e:
            raise CommandError("%s: %s" % (e.code, e.detail)) from e


async def _apply_snapshots(conn, env: Env, c: Claimed, raw: Mapping[str, Any]) -> dict:
    snaps = raw.get("snapshots")
    if not isinstance(snaps, Mapping) or not snaps:
        raise CommandError("the engine returned no snapshots")
    for name, data in snaps.items():
        await conn.execute(
            "INSERT INTO provider_snapshots (provider, name, data) VALUES (%s, %s, %s) "
            "ON CONFLICT (provider, name) DO UPDATE SET data = EXCLUDED.data, fetched_at = clock_timestamp()",
            (env.provider, str(name), Jsonb(redact(data))))
    return {"snapshots": sorted(str(n) for n in snaps)}


async def _apply_titles(conn, env: Env, c: Claimed, raw: Mapping[str, Any]) -> dict:
    return {"set": await titles.apply_titles(conn, env.provider, raw.get("titles") or {})}


async def _run_leased(env: Env, c: Claimed) -> None:
    """Engine part with no connection held, then the result applied outside the timeout, shielded, under the fence."""
    stored: list[StoredOutput] = []

    async def call(ctx):
        if c.kind == "proxy_recheck":
            return await _probe(env, ctx)
        raw = await env.engine.run_command(ctx, c.kind, dict(c.args))
        if c.kind == "recover":
            await _store_outputs(env, c, ctx, raw, stored)
        return raw

    try:
        async with env.keeper.holding(c.lease_id) as settle:    # the call AND the fenced write: no gap in the cover
            raw = await engine_call(env, c.lease_id, c.token, c.account_id, c.kind, call)
            settle()
            await shielded(_finish_leased(env, c, raw, stored))
    except Exception:                                   # the objects of this run must not stay under a live job
        await _remove_unreferenced(env, stored)
        raise
    # a Recover that lost the race for an output leaves its own copy of it: remove what no row names
    await _remove_unreferenced(env, stored)


async def _remove_unreferenced(env: Env, stored: list[StoredOutput]) -> None:
    """Remove the objects this run stored, unless a job row now names them (a concurrent recovery that won)."""
    for o in stored:
        try:
            async with tx(env.db) as conn:
                used = await (await conn.execute("SELECT 1 FROM job_media WHERE object_key = %s", (o.object_key,))
                              ).fetchone()
            if used is None:
                await env.store.remove(o.object_key)
        except Exception as e:                          # the orphan sweep is the backstop
            log.warning("could not remove the unused object %s: %s", o.object_key, error_text(e))


async def _finish_leased(env: Env, c: Claimed, raw: Any, stored: list[StoredOutput]) -> None:
    if not isinstance(raw, Mapping):
        raise CommandError("the engine returned no result mapping")
    apply = _APPLIERS.get(c.kind)
    if c.kind == "recover":
        async def apply(conn, env, c, raw):             # in _done's transaction, under its lease fence: the
            if not stored:                              # reaper cannot take the lease between the check and the
                return {"attached": 0}                  # change of the job and its money
            got = await recover_in_tx(conn, c.job_id, stored, account_id=c.account_id, note="recovered by command")
            return {"status": got.status, "attached": got.attached, "recharged": got.recharged}
    await _done(env, c, {}, apply=apply, raw=raw)


_APPLIERS = {"proxy_recheck": _apply_probe, "refresh_catalog": _apply_snapshots, "titles": _apply_titles}


# ---- execute and loop -------------------------------------------------------------------------------------------
async def execute(env: Env, c: Claimed) -> None:
    """Run one claimed command to its end: done, or failed with the redacted reason. A lost lease writes nothing. A
    recovery write that itself fails is logged (redacted) and left to the reaper's bound."""
    try:
        await _run(env, c)
    except asyncio.CancelledError:
        raise                                           # the worker is stopping: the lease expires, the reaper fails it
    except Exception as e:
        log.error("command %s (%s): the recovery write failed, the reaper will fail it: %s", c.id, c.kind,
                  error_text(e))
    finally:
        if c.token is not None:
            c.token.release()


async def _run(env: Env, c: Claimed) -> None:
    try:
        if c.kind == "delete_account":
            await _done(env, c, await _delete_account(env, c))
        elif c.kind == "wake":
            await _done(env, c, await _wake(env, c))
        elif c.kind == "check_all":
            await _done(env, c, await _check_all(env, c))
        elif c.kind == "check":
            await _done(env, c, await _check_one(env, c))
        elif c.kind in LEASED:
            await _run_leased(env, c)
        else:
            raise CommandError("unknown command kind %r" % c.kind)
    except asyncio.CancelledError:
        raise                                           # the worker is stopping: the lease expires, the reaper fails it
    except leases.LeaseLost:
        log.warning("command %s (%s): lease %s lost, nothing written", c.id, c.kind, c.lease_id)
        await _fail(env, c, "the lease was lost before the result could be applied")
    except TimeoutError:
        await _fail(env, c, "the command ran past its time limit")
    except _RETRYABLE as e:                             # the database picked this transaction to abort
        if await _requeue(env, c):
            log.warning("command %s (%s) was aborted by the database, queued again: %s", c.id, c.kind, error_text(e))
        else:
            log.warning("command %s (%s) failed: %s", c.id, c.kind, error_text(e))
            await _fail(env, c, e)
    except Exception as e:                              # a refusal, a bad result or an engine failure
        log.warning("command %s (%s) failed: %s", c.id, c.kind, error_text(e))
        await _fail(env, c, e)


async def run_one(env: Env) -> bool:
    """Claim and run one command. False when nothing could start (none queued, drained, busy, no token)."""
    if env.drained():
        return False
    claimed = await claim_command(env)
    if claimed is None:
        return False
    await execute(env, claimed)
    return True


async def schedule_catalog_refresh(env: Env) -> bool:
    """Queue a `refresh_catalog` command when `Caps.catalog_refresh_s` has passed since the last one was queued and
    none is waiting or running. At most one per interval, also across workers (an advisory lock)."""
    every = env.caps.catalog_refresh_s
    if not every or env.drained():
        return False
    async with tx(env.db) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("catalog:" + env.provider,))
        row = await (await conn.execute(
            "INSERT INTO commands (provider, kind) SELECT %(p)s, 'refresh_catalog' WHERE NOT EXISTS ("
            "SELECT 1 FROM commands WHERE provider = %(p)s AND kind = 'refresh_catalog' AND (status IN "
            "('queued', 'running') OR created_at > clock_timestamp() - make_interval(secs => %(s)s))) RETURNING id",
            {"p": env.provider, "s": every})).fetchone()
    return row is not None


class CommandLoop:
    """Runs queued commands as tasks (at most `max_tasks`), and every `housekeeping_s` queues the catalog refresh and
    runs the title sweep and starts due account checks."""

    def __init__(self, env: Env, *, max_tasks: int = 2, housekeeping_s: float = titles.SWEEP_EVERY_S,
                 checks: Checks | None = None):
        self.env, self.max_tasks, self.housekeeping_s = env, max_tasks, housekeeping_s
        self.checks = checks if checks is not None else Checks(env)
        self._tasks: set[asyncio.Task] = set()
        self._sweep: asyncio.Task | None = None

    async def tick(self) -> int:
        """Start queued commands that fit; returns how many started."""
        started = 0
        while len(self._tasks) < self.max_tasks and not self.env.drained():
            claimed = await claim_command(self.env)
            if claimed is None:
                break
            task = asyncio.create_task(execute(self.env, claimed), name="command-%s" % claimed.id)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            started += 1
        return started

    async def housekeeping(self) -> None:
        await schedule_catalog_refresh(self.env)
        await self.checks.tick()
        if self._sweep is None or self._sweep.done():        # a slow account must not hold up the command claims
            self._sweep = asyncio.create_task(self._sweep_titles(), name="title-sweep")

    async def _sweep_titles(self) -> None:
        try:
            await titles.sweep_titles(self.env)
        except Exception as e:
            log.error("title sweep failed: %s", error_text(e))

    async def wait(self) -> None:
        while self._tasks:
            waited = list(self._tasks)
            await asyncio.gather(*waited, return_exceptions=True)
            self._tasks.difference_update(waited)       # not left to the done-callbacks: see JobLoop.idle
        if self._sweep is not None:
            await asyncio.gather(self._sweep, return_exceptions=True)
        await self.checks.wait()

    async def run(self, stop: asyncio.Event, tick_s: float = 1.0) -> None:
        loop, last = asyncio.get_running_loop(), float("-inf")
        while not stop.is_set():
            try:
                await self.tick()
                if loop.time() - last >= self.housekeeping_s:
                    last = loop.time()
                    await self.housekeeping()
            except Exception as e:
                log.error("command loop tick failed: %s", error_text(e))
            try:
                await asyncio.wait_for(stop.wait(), tick_s)
            except TimeoutError:
                pass
        await self.wait()
