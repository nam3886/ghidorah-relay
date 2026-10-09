"""Admin: the stats cards and the live alerts (contracts §15, §16).

Everything here is computed from columns at request time: no text pattern matching, no rows kept, nothing to
acknowledge, so an alert clears itself when its condition does. The conditions that are stored (ledger drift,
clamps, unclear replies) come from the alerts routes, not from here.
"""

import shutil
from typing import Any

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from relay_core.api.deps import get_pool, get_registry, get_settings
from relay_core.db import tx
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
    "count(*) FILTER (WHERE state = 'disabled') FROM accounts GROUP BY provider")


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
                     "dead": sum(r[2] for r in chosen), "disabled": sum(r[3] for r in chosen)},
        "keys": keys,
        "alerts": alerts,
    }
