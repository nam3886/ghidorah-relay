"""Admin: the stats cards and the live alerts.

Everything here is computed from columns at request time: no text pattern matching, no rows kept, nothing to
acknowledge, so an alert clears itself when its condition does. The conditions that are stored (ledger drift,
clamps, unclear replies) come from the alerts routes, not from here.
"""

import shutil
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from psycopg_pool import AsyncConnectionPool

from relay_core import alerts as alert_hub
from relay_core.admin.routes_common import ms, pool_budget
from relay_core.api.deps import get_pool, get_registry, get_settings
from relay_core.api.doors import model_disabled
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.pool import pool_configs, pool_states
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings

router = APIRouter()

WINDOW = "interval '24 hours'"
MIN_SAMPLE = 5                      # a rate over fewer jobs than this says nothing
ERROR_RATE = 0.5                    # ponytail: a constant; make it a setting if an operator needs to tune it
HEARTBEAT_STALE_S = 60              # a worker beats every 15 s: four missed beats is stale
CONSECUTIVE_ERRORS = 5
DISK_PATH = "/"                     # ponytail: the api host's disk; the object store's volume is not visible here

_QUEUED = "count(*) FILTER (WHERE status = 'queued')"
_RUNNING = "count(*) FILTER (WHERE status NOT IN ('queued', 'done', 'failed'))"     # claimed and not finished
_JOB_COUNTS = (
    "SELECT " + _QUEUED + ", " + _RUNNING + ", "
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
    "OR EXISTS (SELECT 1 FROM account_pools c WHERE c.account_id = a.id AND c.rest_until > clock_timestamp())) "
    "FROM accounts a GROUP BY provider")


def _alert(kind: str, severity: str, message: str, provider: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "message": message, "provider": provider}


def provider_off(config: Settings, registry: ProviderRegistry, name: str) -> bool:
    """The admin turned the provider off on purpose: drained (`<p>.claims_enabled` false) or every model paused
    (`<p>.disabled_models`), so it takes no job and needs no account."""
    spec = registry.spec(name)
    return not config.get(name + ".claims_enabled") or all(model_disabled(config, spec, m) for m in spec.catalog())


# Claimable some time: available and checked once (claim.py `_CANDIDATES`); a cooldown or a pool at rest ends by itself.
_CLAIMABLE = ("SELECT provider, count(*) FROM accounts WHERE last_checked_at IS NOT NULL AND state = 'available' "
              "GROUP BY provider")


async def _no_accounts(conn, config: Settings, registry: ProviderRegistry) -> list[dict]:
    """One error per provider that is on and has no claimable account (all dead, disabled or never checked): its
    jobs cannot run. Computed per read like every live condition, so it is one row while the outage lasts and gone
    once an account is back."""
    claimable = dict(await (await conn.execute(_CLAIMABLE)).fetchall())
    return [_alert("no_accounts", "error", "%s has no usable account: its jobs cannot run" % name, name)
            for name in registry.names() if not claimable.get(name) and not provider_off(config, registry, name)]


async def live_alerts(conn, config: Settings, registry: ProviderRegistry, usable: dict[str, int]) -> list[dict]:
    """The computed conditions, every provider registered (a provider with no row yet still has 0 usable accounts)."""
    alerts = await _no_accounts(conn, config, registry)        # first: the banner words the first live condition
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
            count = usable.get(name, 0)
            text = "%s has %d usable %s (alert below %d)" % (name, count, "account" if count == 1 else "accounts", low)
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
# account hands out nothing. Budgets come from the providers' `<p>.pools` settings and each account's tier, not from the
# database.
_POOL_ACCOUNTS = (
    "SELECT id, provider, tier FROM accounts WHERE state = 'available' AND (%(p)s::text IS NULL OR provider = %(p)s)")
_POOL_PROXY = (
    "SELECT count(*) FROM accounts WHERE (%(p)s::text IS NULL OR provider = %(p)s) AND "
    "state = 'available' AND proxy_ok = false")
_POOL_CHECK = (
    "SELECT max(finished_at) FROM commands WHERE kind = 'check_all' AND "
    "status = 'done' AND (%(p)s::text IS NULL OR provider = %(p)s)")


def _zero() -> dict[str, int]:
    return {"left": 0, "budget": 0, "unknown": 0}


async def _pool_points(conn, settings: Settings, registry: ProviderRegistry,
                       provider: str | None) -> dict[str, dict[str, dict[str, int]]]:
    """Per provider, per pool (spec order): what is left over the budget of the accounts that know it (the account
    list's own figure: reported, else computed from the charges), and the accounts that do not (no budget for their
    tier, or no window). Never summed across providers: a unit means one thing per provider."""
    accounts = await (await conn.execute(_POOL_ACCOUNTS, {"p": provider})).fetchall()
    configs = {name: pool_configs(name, registry.spec(name).caps, settings.get) for name in registry.names()}
    _, states = await pool_states(conn, {a: (tier, configs.get(prov, {})) for a, prov, tier in accounts})
    per: dict[str, dict[str, dict[str, int]]] = {}
    for name, cfgs in configs.items():
        for pool_name in cfgs if provider in (None, name) else ():
            per.setdefault(name, {})[pool_name] = _zero()
    for account_id, account_provider, _tier in accounts:
        for pool_name in configs.get(account_provider, {}):
            total = per.setdefault(account_provider, {}).setdefault(pool_name, _zero())
            state = states[(account_id, pool_name)]
            budget = pool_budget(state)
            if state.standing.left is None or budget is None:
                total["unknown"] += 1
                continue
            total["budget"] += budget
            total["left"] += state.standing.left
    return per


@router.get("/stats/pool")
async def stats_pool(provider: str | None = None, pool: AsyncConnectionPool = Depends(get_pool),
                     settings: Settings = Depends(get_settings),
                     registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """Per provider and pool (video, image, ...) with the pool's unit, the figures of `_pool_points`; never summed
    across providers (credits, jobs and uses are not one thing). Plus the proxy checks that failed and when the last
    `check_all` finished."""
    args = {"p": provider}
    async with tx(pool) as conn:
        per = await _pool_points(conn, settings, registry, provider)
        proxy_failed = (await (await conn.execute(_POOL_PROXY, args)).fetchone())[0]
        last_check = (await (await conn.execute(_POOL_CHECK, args)).fetchone())[0]
    units = {name: {p.name: p.unit for p in registry.spec(name).caps.pools} for name in per}
    return {"pools": [{"provider": name, "pool": pool_name, "unit": units[name][pool_name], **figures}
                      for name, pools in per.items() for pool_name, figures in pools.items()],
            "proxy_failed": proxy_failed, "last_full_check_ms": ms(last_check)}


# The Jobs page's provider figures. "Today" starts at midnight in the `dashboard_tz` setting (not the billing day:
# billing and the pools keep their own), counted on finished_at.
_DAY = "SELECT date_trunc('day', clock_timestamp() AT TIME ZONE %(tz)s) AT TIME ZONE %(tz)s, clock_timestamp()"
_SUMMARY_JOBS = (
    "SELECT provider, " + _QUEUED + ", " + _RUNNING + ", "
    "count(*) FILTER (WHERE status = 'done'), count(*) FILTER (WHERE status = 'failed') "
    "FROM jobs WHERE status NOT IN ('done', 'failed') OR finished_at >= %(start)s GROUP BY provider")
# ponytail: no index on finished_at, so both read the whole jobs table, as `/stats` already does; add one (a
# migration) when the table makes the 10 s refresh slow
# The same day's finished jobs per whole hour since the day's start (an hour of a half-hour zone starts at :30 UTC).
_SUMMARY_HOURS = (
    "SELECT floor(extract(epoch FROM finished_at - %(start)s) / 3600)::int, provider, "
    "count(*) FILTER (WHERE status = 'done'), count(*) FILTER (WHERE status = 'failed') "
    "FROM jobs WHERE status IN ('done', 'failed') AND finished_at >= %(start)s GROUP BY 1, 2")
# A longer range is cut in local days of the same zone: the day index since the range's first day.
_SUMMARY_DAYS = (
    "SELECT ((finished_at AT TIME ZONE %(tz)s)::date - (%(start)s AT TIME ZONE %(tz)s)::date)::int, provider, "
    "count(*) FILTER (WHERE status = 'done'), count(*) FILTER (WHERE status = 'failed') "
    "FROM jobs WHERE status IN ('done', 'failed') AND finished_at >= %(start)s GROUP BY 1, 2")
# The jobs done over the same stretch one range earlier: today against yesterday up to the same clock, 7 days against
# the 7 days before them.
_PREV_DONE = "SELECT count(*) FROM jobs WHERE status = 'done' AND finished_at >= %(from)s AND finished_at < %(to)s"
HOUR_MS = 3_600_000
RANGES = {"today": 1, "7d": 7, "30d": 30}             # the Jobs page's range switch: its length in local days


def previous_window(day_start: datetime, now: datetime, tz: str, days: int) -> tuple[datetime, datetime]:
    """The same stretch one range earlier, in local calendar days of `tz`: from the midnight `days` local days before
    the range's first day, to the same wall clock `days` local days before `now`. Across a DST change the two ends
    keep their local times (yesterday 00:00 to 12:00 is twelve local hours, not 24 h before)."""
    zone = ZoneInfo(tz)
    first = range_start(day_start, tz, days).date() - timedelta(days=days)
    clock = now.astimezone(zone)
    back = (clock.replace(tzinfo=None) - timedelta(days=days)).replace(tzinfo=zone)
    return datetime.combine(first, time(), tzinfo=zone), back


def range_start(day_start: datetime, tz: str, days: int) -> datetime:
    """Midnight `days - 1` local days before the day that starts at `day_start`, in `tz` (a DST change is kept)."""
    zone = ZoneInfo(tz)
    first = day_start.astimezone(zone).date() - timedelta(days=days - 1)
    return datetime.combine(first, time(), tzinfo=zone)


@router.get("/providers/summary")
async def providers_summary(span: str = Query("today", alias="range"), pool: AsyncConnectionPool = Depends(get_pool),
                            settings: Settings = Depends(get_settings),
                            registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """One card per registered provider, in a single call: how it sends now, whether it takes jobs, its open jobs,
    what finished since the start of the dashboard's day (`dashboard_tz`), its usable accounts and each pool's points
    left; and `hours`, every hour of that day so far (oldest first, zero-filled, `h` = its start) with the done and
    failed jobs of each provider. No alert is computed here (`/stats` does that).
    `range` (`today`, `7d`, `30d`) widens "finished" to that many local days ending today (`range_start_ms`):
    `done_today` and `failed_today` then count the whole range and `hours` holds one entry per local day (`bucket`
    says which). `prev_done` counts the jobs done over the same stretch one range earlier."""
    if span not in RANGES:
        raise ApiError("input.invalid", "range")
    days = RANGES[span]
    async with tx(pool) as conn:
        tz = settings.get("dashboard_tz")
        day_start, now = await (await conn.execute(_DAY, {"tz": tz})).fetchone()
        start = day_start if days == 1 else range_start(day_start, tz, days)
        jobs = {r[0]: r[1:] for r in await (await conn.execute(_SUMMARY_JOBS, {"start": start})).fetchall()}
        hourly = await (await conn.execute(_SUMMARY_HOURS if days == 1 else _SUMMARY_DAYS,
                                           {"start": start, "tz": tz})).fetchall()
        before, until = previous_window(day_start, now, tz, days)
        prev_done = (await (await conn.execute(_PREV_DONE, {"from": before, "to": until})).fetchone())[0]
        accounts = {r[0]: r[1:] for r in await (await conn.execute(_USABLE)).fetchall()}
        per = await _pool_points(conn, settings, registry, None)
    cards = []
    for name in registry.names():
        spec = registry.spec(name)
        setting = getattr(spec, "execution_setting", None)
        queued, running, done, failed = jobs.get(name, (0, 0, 0, 0))
        usable, available, dead, disabled = accounts.get(name, (0, 0, 0, 0, 0, 0))[:4]
        units = {p.name: p.unit for p in spec.caps.pools}
        cards.append({
            "provider": name,
            "execution": settings.get(setting) if setting else spec.caps.execution,
            "claims_enabled": settings.get(name + ".claims_enabled"),
            "running": running, "queued": queued, "done_today": done, "failed_today": failed,
            "accounts": {"usable": usable, "total": available + dead + disabled},
            "pools": [{"pool": p, "unit": units[p], "left": f["left"], "budget": f["budget"]}
                      for p, f in per.get(name, {}).items()],
        })
    names = registry.names()
    day_ms = int(day_start.timestamp() * 1000)
    if days == 1:
        starts = [day_ms + i * HOUR_MS for i in range(int((now - start).total_seconds()) // 3600 + 1)]
    else:
        starts = [int(range_start(day_start, tz, days - i).timestamp() * 1000) for i in range(days)]
    hours = [{"h": h, "done": dict.fromkeys(names, 0), "failed": dict.fromkeys(names, 0)} for h in starts]
    for i, name, done, failed in hourly:
        if name in names and i < len(hours):         # a finish stamped after the read's clock waits for the next read
            hours[i]["done"][name], hours[i]["failed"][name] = done, failed
    return {"day_tz": tz, "day_start_ms": day_ms, "range": span, "range_start_ms": int(start.timestamp() * 1000),
            "bucket": "hour" if days == 1 else "day", "prev_done": prev_done, "providers": cards, "hours": hours}


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
        # the admin's rules (Alerts > Alert rules): a kind turned off is not raised, a muted one is listed apart
        alerts, muted = alert_hub.apply_live(await live_alerts(conn, settings, registry, usable),
                                             await alert_hub.rule_rows(conn))
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
        "muted_alerts": muted,
    }
