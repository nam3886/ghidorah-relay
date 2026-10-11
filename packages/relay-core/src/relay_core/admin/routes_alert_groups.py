"""Admin: the alerts hub (contracts 4.8, 16). One list of every stored alert kind (billing clamps and drifts, unclear
provider replies, kinds added later) in one shape, a detail per group and an acknowledge per group.

Thin: the SQL, the shapes and the group rules are `relay_core.alerts`; this module checks the bounds before any SQL
runs, so a bad value is a 400 or a 404 and never a database error. `api/app.py` mounts this router (`ADMIN_ROUTES`).
"""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from psycopg_pool import AsyncConnectionPool
from pydantic import StrictInt

from relay_core import alerts
from relay_core.admin.routes_common import MAX_PAGE, Strict
from relay_core.api.deps import get_pool
from relay_core.api.errors_http import ApiError
from relay_core.db import tx

router = APIRouter()

MAX_OFFSET = 2**31
MAX_ID = 2**63 - 1


class AcknowledgeBody(Strict):
    through_id: StrictInt


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
