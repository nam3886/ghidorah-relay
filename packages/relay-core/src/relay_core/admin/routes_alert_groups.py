"""Admin: the alerts hub. One list of every stored alert kind (billing clamps and drifts, unclear
provider replies, kinds added later) in one shape, a detail per group and an acknowledge per group.

Thin: the SQL, the shapes and the group rules are `relay_core.alerts`; this module checks the bounds before any SQL
runs, so a bad value is a 400 or a 404 and never a database error. `api/app.py` mounts this router (`ADMIN_ROUTES`).
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import StrictBool, StrictInt

from relay_core import alerts
from relay_core.admin.routes_common import MAX_PAGE, Strict
from relay_core.admin.routes_stats import CONSECUTIVE_ERRORS, ERROR_RATE, HEARTBEAT_STALE_S, MIN_SAMPLE
from relay_core.api.deps import get_pool, get_registry, get_settings
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.providers import ProviderRegistry
from relay_core.settings import Settings

router = APIRouter()

MAX_OFFSET = 2**31
MAX_ID = 2**63 - 1
RESOLVED_LIMIT = 50


class AcknowledgeBody(Strict):
    through_id: StrictInt


class RuleChange(Strict):
    """Either or both: `enabled` turns the kind on or off, `mute_minutes` mutes it from now (0 ends a mute)."""
    enabled: StrictBool | None = None
    mute_minutes: StrictInt | None = None


@dataclass(frozen=True)
class RuleInfo:
    """A kind the relay raises: where it comes from, what raises it and its threshold, either the settings that set it
    (`settings`, editable where they already are) or a figure fixed in code (`fixed`). Not a rule engine: the code
    that raises each kind is unchanged, the admin can only turn a kind off or mute it."""
    source: Literal["stored", "live"]
    kind: str
    severity: str
    text: str
    settings: tuple[str, ...] = ()
    fixed: str | None = None


# Stored: the kinds of the hub (`alerts.SOURCES`). Live: the kinds `routes_stats.live_alerts` computes; the live drift's
# thresholds are each provider's own rate settings (`ProviderSpec.rate_alerts`), read from the registry.
RULES = (
    RuleInfo("stored", "clamp", "error", "A refund would have taken a key's wallet counter below 0."),
    RuleInfo("stored", "drift", "error", "A key's wallet counters disagree with its ledger."),
    RuleInfo("stored", "unclear_reply", "warning", "A provider replied with words the relay could not classify."),
    RuleInfo("live", "worker_stale", "error", "A worker stopped reporting.",
             fixed="no report for %d s" % HEARTBEAT_STALE_S),
    RuleInfo("live", "worker_errors", "warning", "A worker failed several times in a row.",
             fixed="%d errors in a row" % CONSECUTIVE_ERRORS),
    RuleInfo("live", "accounts_low", "warning", "A provider has fewer usable accounts than the threshold.",
             settings=("alert_accounts_low",)),
    RuleInfo("live", "account_dead", "error", "An account's session died."),
    RuleInfo("live", "no_worker", "error", "A provider has open jobs and no live worker."),
    RuleInfo("live", "error_rate", "warning", "Too many of a provider's finished jobs failed in 24 h.",
             fixed="%d%% of at least %d jobs" % (round(ERROR_RATE * 100), MIN_SAMPLE)),
    RuleInfo("live", "disk_low", "error", "The api container's disk has less free space than the threshold.",
             settings=("alert_disk_free_gb",)),
    RuleInfo("live", "drift", "warning", "A provider's jobs show a declared error or warning too often in 24 h."),
    RuleInfo("live", "webhooks_failed", "warning", "Webhooks failed for good in 24 h.", fixed="any"),
)
_RULE_KEYS = {(r.source, r.kind) for r in RULES}


def _thresholds(rule: RuleInfo, settings: Settings, registry: ProviderRegistry) -> list[dict]:
    keys = rule.settings
    if (rule.source, rule.kind) == ("live", "drift"):
        keys = tuple(a.setting for p in registry.names() for a in getattr(registry.spec(p), "rate_alerts", ()))
    return [{"setting": k, "value": settings.get(k)} for k in keys]


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    return limit


def _known(kind: str, group: str) -> None:
    """A path kind that is no hub kind, or a group not written as its kind writes it: 404, never a query."""
    if kind not in alerts.kinds() or not alerts.group_form(kind, group):
        raise ApiError("resource.not_found", "group")


@router.get("/alerts/groups")
async def list_groups(status: Literal["open", "acknowledged", "all"] = "open", kind: str | None = None,
                      limit: int = 100, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    if kind is not None and kind not in alerts.kinds():
        raise ApiError("input.invalid", "kind")
    async with tx(pool) as conn:
        return {"items": await alerts.list_groups(conn, status=status, kind=kind, limit=_limit(limit))}


@router.get("/alerts/groups/{kind}/{group}")
async def group_detail(kind: str, group: str, limit: int = 100, offset: int = 0,
                       pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    _limit(limit)
    if not 0 <= offset <= MAX_OFFSET:
        raise ApiError("input.invalid", "offset")
    _known(kind, group)
    async with tx(pool) as conn:
        found = await alerts.get_group(conn, kind, group)
        if found is None:
            raise ApiError("resource.not_found", "group")
        if kind == alerts.KIND_UNCLEAR:
            found["items"] = await alerts.unclear_items(conn, group, limit, offset)
            found.update(await alerts.unclear_outcomes(conn, group))
        else:
            found["items"] = await alerts.billing_items(conn, kind, group, limit, offset)
    return found


@router.post("/alerts/groups/{kind}/{group}/acknowledge")
async def acknowledge_group(kind: str, group: str, body: AcknowledgeBody, request: Request,
                            pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    if not 1 <= body.through_id <= MAX_ID:
        raise ApiError("input.invalid", "through_id")
    _known(kind, group)
    async with tx(pool) as conn:
        if await alerts.get_group(conn, kind, group) is None:
            raise ApiError("resource.not_found", "group")
        count = await alerts.acknowledge(conn, kind, group, body.through_id, request.state.admin_user.username)
        return {"acknowledged": count, "group": await alerts.get_group(conn, kind, group)}


@router.get("/alerts/resolved")
async def resolved(pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """The groups acknowledged on the relay's billing day, the newest first (at most 50)."""
    async with tx(pool) as conn:
        return await alerts.resolved_today(conn, RESOLVED_LIMIT)


@router.get("/alerts/rules")
async def list_rules(pool: AsyncConnectionPool = Depends(get_pool), settings: Settings = Depends(get_settings),
                     registry: ProviderRegistry = Depends(get_registry)) -> dict:
    """Every kind the relay raises with its threshold, whether it is on and the end of its mute (null when none).
    `money` marks a money-integrity kind: it cannot be turned off and `max_mute_minutes` is a day."""
    async with tx(pool) as conn:
        rows = await alerts.rule_rows(conn)
    unset = {"enabled": True, "muted_until_ms": None, "updated_by": None, "updated_ms": None}
    return {"items": [{"source": r.source, "kind": r.kind, "severity": r.severity, "text": r.text, "fixed": r.fixed,
                       "thresholds": _thresholds(r, settings, registry), "money": alerts.is_money(r.source, r.kind),
                       "max_mute_minutes": alerts.max_mute(r.source, r.kind),
                       **rows.get((r.source, r.kind), unset)}
                      for r in RULES]}


@router.post("/alerts/rules/{source}/{kind}")
async def change_rule(source: str, kind: str, body: RuleChange, request: Request,
                      pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    if (source, kind) not in _RULE_KEYS:
        raise ApiError("resource.not_found", "rule")
    if body.enabled is None and body.mute_minutes is None:
        raise ApiError("input.invalid", "enabled")
    if body.enabled is False and alerts.is_money(source, kind):
        raise ApiError("input.invalid", "enabled")         # a money-integrity alert is never off, only muted a while
    if body.mute_minutes is not None and not 0 <= body.mute_minutes <= alerts.max_mute(source, kind):
        raise ApiError("input.invalid", "mute_minutes")
    async with tx(pool) as conn:
        await alerts.set_rule(conn, source, kind, request.state.admin_user.username, enabled=body.enabled,
                              mute_minutes=body.mute_minutes)
        return {"source": source, "kind": kind, **(await alerts.rule_rows(conn))[(source, kind)]}
