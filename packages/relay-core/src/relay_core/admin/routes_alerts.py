"""Admin: billing alerts. A `clamp` (a refund that would have taken a counter below 0) or a `drift`
(a wallet whose counters disagree with its ledger) stays in the table as the audit trail; acknowledging only stamps who
looked at it and when, so it stops being listed as open. `api/app.py` mounts this router (`ADMIN_ROUTES`)."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool

from relay_core import alerts
from relay_core.admin.routes_common import MAX_PAGE
from relay_core.api.deps import get_pool
from relay_core.api.errors_http import ApiError
from relay_core.db import tx

router = APIRouter()

_SELECT = "SELECT " + ", ".join(alerts.BILLING_COLUMNS) + " FROM billing_alerts"


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
    return {"items": [alerts.billing_item(r) for r in rows]}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, request: Request, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Idempotent: a second acknowledge keeps the first one's time and name."""
    if not 0 < alert_id < 2**63:
        raise ApiError("resource.not_found")
    async with tx(pool) as conn:
        row = await (await conn.execute(
            "UPDATE billing_alerts SET acknowledged_at = COALESCE(acknowledged_at, clock_timestamp()), "
            "acknowledged_by = COALESCE(acknowledged_by, %s) WHERE id = %s RETURNING "
            + ", ".join(alerts.BILLING_COLUMNS),
            (request.state.admin_user.username, alert_id))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    return alerts.billing_item(row)
