"""Admin: the request-log viewer.

Every `request_logs` row is a request that carried a known key. A row with a job is a generation request; a row with
no job and a failing status is a pre-job failure (a refused or malformed request). The filters are half-open on time
(`since_ms <= at < until_ms`, the viewer's own midnights), and the search looks at the job id prefix, the upstream
reference prefix and the title. Text matching uses `starts_with` and `strpos`, so a `%` or `_` the admin types is
only a character and no pattern needs escaping. Clear removes the log rows and nothing else: jobs, the ledger and
the alert audit stay.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from relay_core.admin.routes_common import MAX_PAGE, ms
from relay_core.admin.routes_jobs import _zone, day_rows
from relay_core.api.deps import get_pool
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.jobs.titles import SHOWN_TITLE

router = APIRouter()

MAX_QUERY = 200
MAX_MS = 253402300799999      # 9999-12-31T23:59:59.999Z, the last moment a datetime holds
_COLUMNS = ("id", "at", "key_id", "key_label", "key_prefix", "provider", "route", "status_code", "job_id",
            "job_title", "duration_ms", "error_code")
_FROM = (
    "FROM request_logs l LEFT JOIN api_keys k ON k.id = l.key_id LEFT JOIN jobs j ON j.id = l.job_id "
    "WHERE (%(provider)s::text IS NULL OR l.provider = %(provider)s) "
    "AND (%(since)s::timestamptz IS NULL OR l.at >= %(since)s) "
    "AND (%(until)s::timestamptz IS NULL OR l.at < %(until)s) "
    "AND CASE %(status)s WHEN 'ok' THEN l.status_code < 400 WHEN 'error' THEN l.status_code >= 400 "
    "WHEN 'pre_job' THEN l.job_id IS NULL AND l.status_code >= 400 AND l.route !~ '^(GET|HEAD|OPTIONS) ' "
    "ELSE TRUE END "
    "AND (%(q)s::text IS NULL OR starts_with(l.job_id::text, %(q)s) OR starts_with(j.external_ref, %(q)s) "
    # the title, else the prompt's start: a cheap superset of the shown title (SHOWN_TITLE runs on the page rows only)
    "OR strpos(lower(coalesce(NULLIF(j.title, ''), left(j.prompt, 120), '')), lower(%(q)s)) > 0)")

# Per local day under the list's own filters, bounded to the earliest..latest day of the page so the planner keeps a
# plain range on request_logs_at. The day bounds are local midnights in `tz`.
_DAY_TOTALS = "SELECT (l.at AT TIME ZONE %(tz)s)::date, count(*) "
_DAY_RANGE = (" AND l.at >= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s"
              " AND l.at < ((%(day_last)s::date + 1)::timestamp AT TIME ZONE %(tz)s) GROUP BY 1")


def _view(row: tuple) -> dict:
    item = dict(zip(_COLUMNS, row, strict=True))
    item["at_ms"] = ms(item.pop("at"))
    for name in ("id", "key_id", "job_id"):
        item[name] = None if item[name] is None else str(item[name])
    return item


def _moment(value_ms: int | None, param: str) -> datetime | None:
    if value_ms is None:
        return None
    if not 0 <= value_ms <= MAX_MS:
        raise ApiError("input.invalid", param)
    return datetime.fromtimestamp(value_ms / 1000, UTC)


@router.get("/logs")
async def list_logs(provider: str | None = None, status: Literal["all", "ok", "error", "pre_job"] = "all",
                    since_ms: int | None = None, until_ms: int | None = None, q: str | None = None,
                    limit: int = 50, offset: int = 0, tz: str | None = None,
                    pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Newest first. `status` is the request's own outcome, not the job's (the Jobs tab filters by job status): `ok`
    (< 400), `error` (>= 400) or `pre_job` (a failed request that could create a job and made none). With `tz` (an
    IANA zone) the answer also holds `day_totals`: for each local day (`YYYY-MM-DD`) on the page, the rows of that
    day matching the same filters."""
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    if not 0 <= offset < 2**31:
        raise ApiError("input.invalid", "offset")
    search = (q or "").strip()
    if len(search) > MAX_QUERY:
        raise ApiError("input.invalid", "q")
    zone = None if tz is None else _zone(tz)
    params = {"provider": provider, "status": status, "since": _moment(since_ms, "since_ms"),
              "until": _moment(until_ms, "until_ms"), "q": search or None}
    day_totals: dict[str, int] = {}
    async with tx(pool) as conn:
        total = (await (await conn.execute("SELECT count(*) " + _FROM, params)).fetchone())[0]
        rows = await (await conn.execute(
            "SELECT l.id, l.at, l.key_id, k.label, k.key_prefix, l.provider, l.route, l.status_code, l.job_id, "
            + SHOWN_TITLE + ", l.duration_ms, l.error_code " + _FROM
            + " ORDER BY l.at DESC, l.id DESC LIMIT %(limit)s OFFSET %(offset)s",
            {**params, "limit": limit, "offset": offset})).fetchall()
        if zone is not None and rows:
            days = [r[1].astimezone(zone).date() for r in rows]
            found = await day_rows(conn, _DAY_TOTALS + _FROM + _DAY_RANGE, {
                **params, "tz": tz, "day_first": min(days), "day_last": max(days)})
            day_totals = {d.isoformat(): n for d, n in sorted(found)}
    out = {"items": [_view(r) for r in rows], "total": total}
    if zone is not None:
        out["day_totals"] = day_totals
    return out


@router.get("/logs/{log_id}")
async def log_detail(log_id: int, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """One row with the redacted request and answer it kept."""
    if not 0 < log_id < 2**63:
        raise ApiError("resource.not_found")
    async with tx(pool) as conn:
        row = await (await conn.execute(
            "SELECT l.id, l.at, l.key_id, k.label, k.key_prefix, l.provider, l.route, l.status_code, l.job_id, "
            + SHOWN_TITLE + ", l.duration_ms, l.error_code, l.body_redacted, l.response_redacted FROM request_logs l "
            "LEFT JOIN api_keys k ON k.id = l.key_id LEFT JOIN jobs j ON j.id = l.job_id WHERE l.id = %s",
            (log_id,))).fetchone()
    if row is None:
        raise ApiError("resource.not_found")
    return {**_view(row[:12]), "request": row[12], "response": row[13]}


@router.delete("/logs")
async def clear_logs(pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Remove every log row. Jobs, the ledger and the alert audit are not touched."""
    async with tx(pool) as conn:
        cur = await conn.execute("DELETE FROM request_logs")
    return {"deleted": cur.rowcount}

