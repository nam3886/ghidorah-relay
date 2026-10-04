"""Admin: billing alerts (contracts 4.1, 15). A `clamp` (a refund that would have taken a counter below 0) or a `drift`
(a wallet whose counters disagree with its ledger) stays in the table as the audit trail; acknowledging only stamps who
looked at it and when, so it stops being listed as open. `api/app.py` mounts this router (`ADMIN_ROUTES`)."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core.admin.routes_common import MAX_PAGE, ms
from relay_core.api.deps import get_pool
from relay_core.api.errors_http import ApiError
from relay_core.db import tx

router = APIRouter()

_COLUMNS = ("id", "at", "alert", "key_id", "provider", "kind", "ledger_id", "amount", "used_day", "used_total",
            "detail", "acknowledged_at", "acknowledged_by")
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM billing_alerts"


def _view(row: tuple) -> dict:
    item = dict(zip(_COLUMNS, row, strict=True))
    item["at_ms"], item["acknowledged_ms"] = ms(item.pop("at")), ms(item.pop("acknowledged_at"))
    item["key_id"] = str(item["key_id"])
    return item


@router.get("/alerts")
async def list_alerts(status: Literal["open", "acknowledged", "all"] = "open", limit: int = 100,
                      pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    where = {"open": " WHERE acknowledged_at IS NULL", "acknowledged": " WHERE acknowledged_at IS NOT NULL",
             "all": ""}[status]
    async with tx(pool) as conn:
        rows = await (await conn.execute(_SELECT + where + " ORDER BY at DESC, id DESC LIMIT %s",
                                         (limit,))).fetchall()
    return {"items": [_view(r) for r in rows]}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, request: Request, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Idempotent: a second acknowledge keeps the first one's time and name."""
    if not 0 < alert_id < 2**63:
        raise ApiError("resource.not_found")
    async with tx(pool) as conn:
        row = await (await conn.execute(
            "UPDATE billing_alerts SET acknowledged_at = COALESCE(acknowledged_at, clock_timestamp()), "
            "acknowledged_by = COALESCE(acknowledged_by, %s) WHERE id = %s RETURNING " + ", ".join(_COLUMNS),
            (request.state.admin_user.username, alert_id))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    return _view(row)
