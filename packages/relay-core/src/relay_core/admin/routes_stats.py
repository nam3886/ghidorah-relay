"""Admin: the stats cards and the live alerts (contracts §15, §16).

Everything here is computed from columns at request time: no text pattern matching, no rows kept, nothing to
acknowledge, so an alert clears itself when its condition does. The conditions that are stored (ledger drift,
clamps, unclear replies) come from the alerts routes, not from here.
"""

import shutil
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from relay_core.admin.routes_common import ms, pool_point
from relay_core.api.deps import get_pool, get_registry, get_settings
from relay_core.db import tx
from relay_core.pool import pool_configs
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings

router = APIRouter()

WINDOW = "interval '24 hours'"
MIN_SAMPLE = 5                      # a rate over fewer jobs than this says nothing
ERROR_RATE = 0.5                    # ponytail: a constant; make it a setting if an operator needs to tune it
HEARTBEAT_STALE_S = 60              # a worker beats every 15 s: four missed beats is stale
CONSECUTIVE_ERRORS = 5
DISK_PATH = "/"                     # ponytail: the api host's disk; the object store's volume is not visible here

_JOB_COUNTS = (
    "SELECT count(*) FILTER (WHERE status = 'queued'), "
    "count(*) FILTER (WHERE status NOT IN ('queued', 'done', 'failed')), "
    "count(*) FILTER (WHERE status = 'done'), count(*) FILTER (WHERE status = 'failed'), "
    "count(*) FILTER (WHERE status = 'done' AND media_type = 'video'), "
    "count(*) FILTER (WHERE status = 'done' AND media_type = 'image') "
    "FROM jobs WHERE (%(p)s::text IS NULL OR provider = %(p)s)")
# A request that could create a job: not a read. The log keeps the method in front of the path ("POST /v1/..."), and a
# failed read (a poll of a wrong job id, a disabled key) is not a failure to create anything.
PRE_JOB = "job_id IS NULL AND status_code >= 400 AND route !~ '^(GET|HEAD|OPTIONS) '"
_OPEN = "('queued', 'submitting', 'unknown', 'waiting', 'fetching')"
_MATCH = {"error_code": "error_code = ANY(%(c)s)",
          "warning": "EXISTS (SELECT 1 FROM jsonb_array_elements(warnings) w WHERE w->>'code' = ANY(%(c)s))"}
_USABLE = (
    "SELECT provider, count(*) FILTER (WHERE state = 'available' AND last_checked_at IS NOT NULL AND "
    "(cooldown_until IS NULL OR cooldown_until <= clock_timestamp())), "
    "count(*) FILTER (WHERE state = 'available'), count(*) FILTER (WHERE state = 'dead'), "
    "count(*) FILTER (WHERE state = 'disabled'), "
    # in_use and asleep MUST count the same accounts the Accounts list marks: the card and the rows may never differ.
    # in use: any live lease, whatever the account's state (routes_accounts `_LIST`, `in_use`); an account that holds
    # two counts once. asleep: its own cooldown still in the future, or a pool that rests (`rest_until`), whatever the
    # state (account-model.ts `asleepLines`). Both on the database clock, like the list; one account can be both.
    "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM leases WHERE account_id = a.id AND until > clock_timestamp())), "
    "count(*) FILTER (WHERE cooldown_until > clock_timestamp() "
    "OR EXISTS (SELECT 1 FROM account_capacity c WHERE c.account_id = a.id AND c.rest_until > clock_timestamp())) "
    "FROM accounts a GROUP BY provider")


def _alert(kind: str, severity: str, message: str, provider: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "message": message, "provider": provider}


async def live_alerts(conn, config: Settings, registry: ProviderRegistry, usable: dict[str, int]) -> list[dict]:
    """The computed conditions, every provider registered (a provider with no row yet still has 0 usable accounts)."""
    alerts: list[dict] = []
    for worker, provider, beat_age, errors in await (await conn.execute(
            "SELECT worker_id, provider, extract(epoch FROM clock_timestamp() - at), consecutive_errors "
            "FROM worker_heartbeats ORDER BY worker_id")).fetchall():
        if beat_age > HEARTBEAT_STALE_S:
            text = "worker %s has not reported for %d s" % (worker, beat_age)
            alerts.append(_alert("worker_stale", "error", text, provider))
        elif errors >= CONSECUTIVE_ERRORS:
            text = "worker %s: %d errors in a row" % (worker, errors)
            alerts.append(_alert("worker_errors", "warning", text, provider))
    low = config.get("alert_accounts_low")
    for name in registry.names():
        if usable.get(name, 0) < low:
            text = "%s has %d usable accounts (alert below %d)" % (name, usable.get(name, 0), low)
            alerts.append(_alert("accounts_low", "warning", text, name))
    # A dead account (its session died; a disabled account is never dead, the state is one of the two): one condition
    # each, until it is signed in again, disabled or removed. It only reads the state: no notification, no webhook.
    for provider, label, account_id in await (await conn.execute(
            "SELECT provider, label, id FROM accounts WHERE state = 'dead' ORDER BY label, id")).fetchall():
        text = "%s has a dead session" % (label or str(account_id)[:8])
        alerts.append(_alert("account_dead", "error", text, provider))
    staffed = {r[0] for r in await (await conn.execute(
        "SELECT DISTINCT provider FROM worker_heartbeats WHERE at > clock_timestamp() - make_interval(secs => %s)",
        (HEARTBEAT_STALE_S,))).fetchall()}
    for provider, open_jobs in await (await conn.execute(
            "SELECT provider, count(*) FROM jobs WHERE status IN " + _OPEN + " GROUP BY provider")).fetchall():
        if provider in registry.names() and provider not in staffed and config.get(provider + ".claims_enabled"):
            text = "%s has %d jobs waiting or open and no live worker" % (provider, open_jobs)
            alerts.append(_alert("no_worker", "error", text, provider))
    for provider, total, failed in await (await conn.execute(
            "SELECT provider, count(*), count(*) FILTER (WHERE status = 'failed') FROM jobs "
            "WHERE status IN ('done', 'failed') AND created_at > clock_timestamp() - " + WINDOW
            + " GROUP BY provider")).fetchall():
        if total >= MIN_SAMPLE and failed / total >= ERROR_RATE:
            text = "%s: %d of %d finished jobs failed in 24 h" % (provider, failed, total)
            alerts.append(_alert("error_rate", "warning", text, provider))
    free_gb = shutil.disk_usage(DISK_PATH).free / 1e9
    if free_gb < config.get("alert_disk_free_gb"):
        alerts.append(_alert("disk_low", "error", "%.1f GB free on the api container's root disk" % free_gb))
    alerts += await _drift(conn, config, registry)
    failed_hooks = (await (await conn.execute(
        "SELECT count(*) FROM jobs WHERE callback_status = 'failed' AND updated_at > clock_timestamp() - "
        + WINDOW)).fetchone())[0]
    if failed_hooks:
        alerts.append(_alert("webhooks_failed", "warning", "%d webhooks failed for good in 24 h" % failed_hooks))
    return alerts


async def _drift(conn, config: Settings, registry: ProviderRegistry) -> list[dict]:
    """The drift alerts each provider declares (`ProviderSpec.rate_alerts`): the share of its jobs finished in the last
    24 h that ended with the declared error code, or carry the declared warning, against the provider's live setting.
    A rate over fewer than `MIN_SAMPLE` finished jobs says nothing. A provider that declares none raises none."""
    alerts = []
    for provider in registry.names():
        for alert in getattr(registry.spec(provider), "rate_alerts", ()):
            codes = (alert.code,) if isinstance(alert.code, str) else alert.code
            total, hits = await (await conn.execute(
                "SELECT count(*), count(*) FILTER (WHERE " + _MATCH[alert.source] + ") FROM jobs "
                "WHERE provider = %(p)s AND status IN ('done', 'failed') AND finished_at > clock_timestamp() - "
                + WINDOW, {"p": provider, "c": list(codes)})).fetchone()
            if total >= MIN_SAMPLE and hits / total >= config.get(alert.setting):
                text = "%s: %d of %d jobs in 24 h show %s" % (provider, hits, total, " or ".join(codes))
                alerts.append(_alert("drift", "warning", text, provider))
    return alerts


def _now() -> datetime:
    """The band's clock: one value bound into both parts of the query; tests replace it with a fixed instant."""
    return datetime.now(UTC)


# The last 24 UTC hours, current one last, each zero-filled. Every series buckets by created_at (the hour a job was
# submitted), split by its current outcome, so one index range scan on jobs_created_at_idx serves it. The session
# timezone is UTC (db/pool.py), so date_trunc cuts at UTC hour starts.
_HOURLY = (
    "SELECT (extract(epoch FROM h.hour) * 1000)::bigint, COALESCE(c.delivered, 0), COALESCE(c.failed, 0), "
    "COALESCE(c.in_progress, 0) "
    "FROM generate_series(date_trunc('hour', %(now)s::timestamptz) - interval '23 hours', "
    "date_trunc('hour', %(now)s::timestamptz), "
    "interval '1 hour') AS h(hour) "
    "LEFT JOIN (SELECT date_trunc('hour', created_at) AS hour, "
    "count(*) FILTER (WHERE status = 'done') AS delivered, "
    "count(*) FILTER (WHERE status = 'failed') AS failed, "
    "count(*) FILTER (WHERE status NOT IN ('done', 'failed')) AS in_progress "
    "FROM jobs WHERE created_at >= date_trunc('hour', %(now)s::timestamptz) - interval '23 hours' "
    "AND (%(p)s::text IS NULL OR provider = %(p)s) GROUP BY 1) c ON c.hour = h.hour "
    "ORDER BY h.hour")


@router.get("/stats/hourly")
async def stats_hourly(provider: str | None = None, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Jobs submitted in each of the last 24 UTC hours, oldest first, split by their current outcome."""
    async with tx(pool) as conn:
        rows = await (await conn.execute(_HOURLY, {"p": provider, "now": _now()})).fetchall()
    return {"hours": [{"hour_ms": hour, "delivered": delivered, "failed": failed, "in_progress": in_progress}
                      for hour, delivered, failed, in_progress in rows]}


# The Accounts screen's pool panel. Only accounts that are `available` count toward points: a dead or a disabled
# account hands out nothing. Budgets come from the providers' settings, not from the database.
_POOL_ACCOUNTS = (
    "SELECT id, provider FROM accounts WHERE state = 'available' AND (%(p)s::text IS NULL OR provider = %(p)s)")
_POOL_CAPACITY = (
    "SELECT c.account_id, c.pool, c.left_n, c.source, c.day_anchor FROM account_capacity c "
    "JOIN accounts a ON a.id = c.account_id WHERE a.state = 'available' AND "
    "(%(p)s::text IS NULL OR a.provider = %(p)s)")
_POOL_SPENT = (
    "SELECT c.account_id, c.pool, sum(c.amount) FROM account_charges c "
    "JOIN account_capacity k ON k.account_id = c.account_id AND k.pool = c.pool AND k.day_anchor = c.account_day "
    "JOIN accounts a ON a.id = c.account_id WHERE c.refunded_at IS NULL AND a.state = 'available' AND "
    "(%(p)s::text IS NULL OR a.provider = %(p)s) GROUP BY 1, 2")
_POOL_PROXY = (
    "SELECT count(*) FROM accounts WHERE (%(p)s::text IS NULL OR provider = %(p)s) AND "
    "state = 'available' AND proxy_ok = false")
_POOL_CHECK = (
    "SELECT max(finished_at) FROM commands WHERE kind = 'check_all' AND "
    "status = 'done' AND (%(p)s::text IS NULL OR provider = %(p)s)")


@router.get("/stats/pool")
async def stats_pool(provider: str | None = None, pool: AsyncConnectionPool = Depends(get_pool),
                     settings: Settings = Depends(get_settings),
                     registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """Per pool (video, image, ...): points left over the budget of the accounts that know their points, the accounts
    that do not, and the counted values that disagree with the ledger (the accounts list's own drift rule); plus the
    proxy checks that failed and when the last `check_all` finished. A pool with no budget has every account unknown."""
    args = {"p": provider}
    async with tx(pool) as conn:
        accounts = await (await conn.execute(_POOL_ACCOUNTS, args)).fetchall()
        rows = {(a, p): (left, source, anchor) for a, p, left, source, anchor in await (
            await conn.execute(_POOL_CAPACITY, args)).fetchall()}
        spent = {(a, p): n for a, p, n in await (await conn.execute(_POOL_SPENT, args)).fetchall()}
        proxy_failed = (await (await conn.execute(_POOL_PROXY, args)).fetchone())[0]
        last_check = (await (await conn.execute(_POOL_CHECK, args)).fetchone())[0]
        now = (await (await conn.execute("SELECT clock_timestamp()")).fetchone())[0]   # the clock the claim uses
    configs = {name: pool_configs(registry.spec(name).caps, settings.get) for name in registry.names()}
    totals: dict[str, dict[str, int]] = {}
    for name, cfgs in configs.items():
        for pool_name in cfgs if provider in (None, name) else ():
            totals.setdefault(pool_name, {"left": 0, "budget": 0, "unknown": 0, "drift": 0})
    for account_id, account_provider in accounts:
        for pool_name, cfg in configs.get(account_provider, {}).items():
            total = totals.setdefault(pool_name, {"left": 0, "budget": 0, "unknown": 0, "drift": 0})
            row = rows.get((account_id, pool_name))
            if cfg.budget is None or row is None:
                total["unknown"] += 1
                continue
            left_n, source, anchor = row
            # the accounts list's own rule for a row: a day that is over is the budget, drift is a counted mismatch
            _, left_today, drift = pool_point(cfg, left_n, source, anchor, now, spent.get((account_id, pool_name), 0))
            if left_today is None:
                total["unknown"] += 1
                continue
            total["budget"] += cfg.budget
            total["left"] += left_today
            total["drift"] += bool(drift)
    return {"pools": [{"pool": name, **total} for name, total in totals.items()],
            "proxy_failed": proxy_failed, "last_full_check_ms": ms(last_check)}


@router.get("/stats")
async def stats(provider: str | None = None, pool: AsyncConnectionPool = Depends(get_pool),
                settings: Settings = Depends(get_settings), registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """The cards for one provider (or all) and the live alerts of the whole deployment."""
    async with tx(pool) as conn:
        queued, running, done, failed, videos, images = await (
            await conn.execute(_JOB_COUNTS, {"p": provider})).fetchone()
        accounts = {r[0]: r[1:] for r in await (await conn.execute(_USABLE)).fetchall()}
        pre_job = (await (await conn.execute(
            "SELECT count(*) FROM request_logs WHERE " + PRE_JOB + " AND "
            "(%(p)s::text IS NULL OR provider = %(p)s) AND at > clock_timestamp() - " + WINDOW,
            {"p": provider})).fetchone())[0]
        keys = (await (await conn.execute("SELECT count(*) FROM api_keys WHERE enabled")).fetchone())[0]
        usable = {name: row[0] for name, row in accounts.items()}
        alerts = await live_alerts(conn, settings, registry, usable)
    chosen = [row for name, row in accounts.items() if provider in (None, name)]
    return {
        "jobs": {"queued": queued, "in_progress": running, "done": done, "failed": failed,
                 "pre_job_failures_24h": pre_job},
        "made": {"videos": videos, "images": images},
        "accounts": {"usable": sum(r[0] for r in chosen), "available": sum(r[1] for r in chosen),
                     "dead": sum(r[2] for r in chosen), "disabled": sum(r[3] for r in chosen),
                     "in_use": sum(r[4] for r in chosen), "asleep": sum(r[5] for r in chosen)},
        "keys": keys,
        "alerts": alerts,
    }
