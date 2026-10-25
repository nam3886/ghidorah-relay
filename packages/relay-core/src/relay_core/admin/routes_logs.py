"""Admin: the request-log viewer.

Every `request_logs` row is a request that carried a known key. A row with a job is a generation request; a row with
no job and a failing status is a pre-job failure (a refused or malformed request). The filters are half-open on time
(`since_ms <= at < until_ms`, the viewer's own midnights), and the search looks at the job id prefix, the upstream
reference prefix and the title. Text matching uses `starts_with` and `strpos`, so a `%` or `_` the admin types is
only a character and no pattern needs escaping. Clear removes the log rows and nothing else: jobs, the ledger and
the alert audit stay. Every read is bounded in time by the retention sweep (`log_retention_days`), and the day's
numbers and the day totals range-scan `request_logs_at`.
"""

import csv
import io
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from psycopg_pool import AsyncConnectionPool

from relay_core.admin.routes_common import MAX_PAGE, ms
from relay_core.admin.routes_jobs import _zone, day_rows
from relay_core.admin.routes_stats import _DAY
from relay_core.api.deps import get_pool, get_settings
from relay_core.api.errors_http import ApiError
from relay_core.db import tx
from relay_core.jobs.titles import SHOWN_TITLE
from relay_core.redact import redact_shown
from relay_core.settings import Settings

router = APIRouter()

MAX_QUERY = 200
MAX_MS = 253402300799999      # 9999-12-31T23:59:59.999Z, the last moment a datetime holds
EXPORT_MAX = 10_000           # the newest rows of the view one CSV holds
EXPORT_CHUNK = 500            # rows per piece of the streamed CSV
_COLUMNS = ("id", "at", "key_id", "key_label", "key_prefix", "provider", "route", "status_code", "job_id",
            "job_title", "duration_ms", "error_code")
Status = Literal["all", "ok", "error", "2xx", "4xx", "5xx", "pre_job"]
# The request's own outcome. `2xx`, `4xx` and `5xx` are the Status chips (a 499, the client hung up, is a 4xx).
_STATUS = (
    "CASE %(status)s WHEN 'ok' THEN l.status_code < 400 WHEN 'error' THEN l.status_code >= 400 "
    "WHEN '2xx' THEN l.status_code BETWEEN 200 AND 299 WHEN '4xx' THEN l.status_code BETWEEN 400 AND 499 "
    "WHEN '5xx' THEN l.status_code >= 500 "
    "WHEN 'pre_job' THEN l.job_id IS NULL AND l.status_code >= 400 AND l.route !~ '^(GET|HEAD|OPTIONS) ' "
    "ELSE TRUE END ")
# Every filter but the status: the Status chips count under it.
_ANY = (
    "FROM request_logs l LEFT JOIN api_keys k ON k.id = l.key_id LEFT JOIN jobs j ON j.id = l.job_id "
    "WHERE (%(provider)s::text IS NULL OR l.provider = %(provider)s) "
    "AND (%(key)s::uuid IS NULL OR l.key_id = %(key)s) "
    "AND (%(since)s::timestamptz IS NULL OR l.at >= %(since)s) "
    "AND (%(until)s::timestamptz IS NULL OR l.at < %(until)s) "
    "AND (%(q)s::text IS NULL OR starts_with(l.job_id::text, %(q)s) OR starts_with(j.external_ref, %(q)s) "
    # the title, else the prompt's start: a cheap superset of the shown title (SHOWN_TITLE runs on the page rows only)
    "OR strpos(lower(coalesce(NULLIF(j.title, ''), left(j.prompt, 120), '')), lower(%(q)s)) > 0) ")
_FROM = _ANY + "AND " + _STATUS
CHIPS = ("all", "2xx", "4xx", "5xx", "pre_job")
# One pass for the page's total and each chip's count: a chip is the same CASE with its status inlined (a constant of
# CHIPS, never input), so a chip's count is what choosing it lists.
_COUNTS = ("SELECT count(*) FILTER (WHERE " + _STATUS + "), "
           + ", ".join("count(*) FILTER (WHERE %s)" % _STATUS.replace("%(status)s", "'%s'" % c) for c in CHIPS)
           + " " + _ANY)
_PAGE = ("SELECT l.id, l.at, l.key_id, k.label, k.key_prefix, l.provider, l.route, l.status_code, l.job_id, "
         + SHOWN_TITLE + ", l.duration_ms, l.error_code " + _FROM
         + " ORDER BY l.at DESC, l.id DESC LIMIT %(limit)s OFFSET %(offset)s")

# Per local day under the list's own filters, bounded to the earliest..latest day of the page so the planner keeps a
# plain range on request_logs_at. The day bounds are local midnights in `tz`.
_DAY_TOTALS = "SELECT (l.at AT TIME ZONE %(tz)s)::date, count(*) "
_DAY_RANGE = (" AND l.at >= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s"
              " AND l.at < ((%(day_last)s::date + 1)::timestamp AT TIME ZONE %(tz)s) GROUP BY 1")

# Today in numbers, [midnight, the next midnight) in `dashboard_tz`: the requests by status family and the duration
# percentiles.
_TODAY_RANGE = "at >= %(start)s AND at < %(end)s"
_TODAY = ("SELECT count(*), count(*) FILTER (WHERE status_code BETWEEN 200 AND 299), "
          "count(*) FILTER (WHERE status_code BETWEEN 400 AND 499), count(*) FILTER (WHERE status_code >= 500), "
          "percentile_disc(0.5) WITHIN GROUP (ORDER BY duration_ms), "
          "percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms) FROM request_logs WHERE " + _TODAY_RANGE)
# The commonest error code of the 4xx and of the 5xx today (a tie goes to the first code by name).
_TOP_CODES = ("SELECT DISTINCT ON (status_code / 100) status_code / 100, error_code, count(*) FROM request_logs "
              "WHERE " + _TODAY_RANGE + " AND status_code >= 400 AND error_code IS NOT NULL "
              "GROUP BY status_code / 100, error_code ORDER BY status_code / 100, count(*) DESC, error_code")

_CSV_HEAD = ("time_utc", "status", "route", "job_id", "job_title", "provider", "key", "key_prefix", "error_code",
             "duration_ms")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


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


def _filters(provider: str | None, key_id: uuid.UUID | None, status: str, since_ms: int | None,
             until_ms: int | None, q: str | None) -> dict:
    """The list's filters as query parameters, shared by the list and its export."""
    search = (q or "").strip()
    if len(search) > MAX_QUERY:
        raise ApiError("input.invalid", "q")
    return {"provider": provider, "key": key_id, "status": status, "since": _moment(since_ms, "since_ms"),
            "until": _moment(until_ms, "until_ms"), "q": search or None}


@router.get("/logs")
async def list_logs(provider: str | None = None, status: Status = "all", since_ms: int | None = None,
                    until_ms: int | None = None, q: str | None = None, key_id: uuid.UUID | None = None,
                    limit: int = 50, offset: int = 0, tz: str | None = None, counts: bool = False,
                    pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """Newest first. `status` is the request's own outcome, not the job's (the Jobs tab filters by job status): `ok`
    (< 400), `error` (>= 400), `2xx`, `4xx`, `5xx`, or `pre_job` (a failed request that could create a job and made
    none). `key_id` keeps one key's rows. With `tz` (an IANA zone) the answer also holds `day_totals`: for each local
    day (`YYYY-MM-DD`) on the page, the rows of that day matching the same filters. With `counts` it holds
    `status_counts`: the rows of each Status chip (`all`, `2xx`, `4xx`, `5xx`, `pre_job`) under every other filter."""
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError("input.invalid", "limit")
    if not 0 <= offset < 2**31:
        raise ApiError("input.invalid", "offset")
    params = _filters(provider, key_id, status, since_ms, until_ms, q)
    zone = None if tz is None else _zone(tz)
    day_totals: dict[str, int] = {}
    chips: dict[str, int] = {}
    async with tx(pool) as conn:
        if counts:
            total, *found = await (await conn.execute(_COUNTS, params)).fetchone()
            chips = dict(zip(CHIPS, found, strict=True))
        else:
            total = (await (await conn.execute("SELECT count(*) " + _FROM, params)).fetchone())[0]
        rows = await (await conn.execute(_PAGE, {**params, "limit": limit, "offset": offset})).fetchall()
        if zone is not None and rows:
            days = [r[1].astimezone(zone).date() for r in rows]
            found = await day_rows(conn, _DAY_TOTALS + _FROM + _DAY_RANGE, {
                **params, "tz": tz, "day_first": min(days), "day_last": max(days)})
            day_totals = {d.isoformat(): n for d, n in sorted(found)}
    out = {"items": [_view(r) for r in rows], "total": total}
    if zone is not None:
        out["day_totals"] = day_totals
    if counts:
        out["status_counts"] = chips
    return out


@router.get("/logs/today")
async def logs_today(pool: AsyncConnectionPool = Depends(get_pool),
                     settings: Settings = Depends(get_settings)) -> dict:
    """The page's "Today in numbers", from midnight in the `dashboard_tz` setting: the logged requests, those per
    status family, the commonest error code of the 4xx and of the 5xx (`null` when none), and the median and 95th
    percentile duration (`null` with no request). `kept_days` is the `log_retention_days` setting."""
    tz = settings.get("dashboard_tz")
    async with tx(pool) as conn:
        day_start, _ = await (await conn.execute(_DAY, {"tz": tz})).fetchone()
        zone = ZoneInfo(tz)
        # the next local midnight from the calendar date, so a day with a clock change keeps its 23 or 25 hours
        day = {"start": day_start, "end": datetime.combine(
            day_start.astimezone(zone).date() + timedelta(days=1), time(), tzinfo=zone)}
        requests, ok, client, server, p50, p95 = await (await conn.execute(_TODAY, day)).fetchone()
        top = {family: {"code": code, "count": n}
               for family, code, n in await (await conn.execute(_TOP_CODES, day)).fetchall()}
    return {"day_tz": tz, "day_start_ms": ms(day_start), "kept_days": settings.get("log_retention_days"),
            "requests": requests, "by_class": {"2xx": ok, "4xx": client, "5xx": server},
            "top_code": {"4xx": top.get(4), "5xx": top.get(5)}, "p50_ms": p50, "p95_ms": p95}


def _cell(value) -> object:
    """A spreadsheet runs a cell that starts with a formula sign: such a text is written with a quote before it."""
    return "'" + value if isinstance(value, str) and value.startswith(_FORMULA) else value


def _csv_row(row: tuple) -> list:
    v = _view(row)
    at = datetime.fromtimestamp(v["at_ms"] / 1000, UTC).isoformat(timespec="milliseconds") if v["at_ms"] else ""
    shown = redact_shown([v["route"], v["job_title"], v["key_label"], v["error_code"]])
    return [_cell(c) for c in (at, v["status_code"], shown[0], v["job_id"], shown[1], v["provider"], shown[2],
                               v["key_prefix"], shown[3], v["duration_ms"])]


@router.get("/logs/export")
async def export_logs(provider: str | None = None, status: Status = "all", since_ms: int | None = None,
                      until_ms: int | None = None, q: str | None = None, key_id: uuid.UUID | None = None,
                      pool: AsyncConnectionPool = Depends(get_pool)) -> StreamingResponse:
    """The list's rows under the same filters as CSV, newest first, at most `EXPORT_MAX`. Only the columns the list
    shows, redacted as the admin is shown them: no body and no answer, so no secret a body may carry."""
    params = _filters(provider, key_id, status, since_ms, until_ms, q)
    async with tx(pool) as conn:
        rows = await (await conn.execute(_PAGE, {**params, "limit": EXPORT_MAX, "offset": 0})).fetchall()

    def pieces():
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(_CSV_HEAD)
        for at in range(0, len(rows), EXPORT_CHUNK):
            out.writerows(_csv_row(r) for r in rows[at:at + EXPORT_CHUNK])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
        if buf.tell():
            yield buf.getvalue()

    return StreamingResponse(pieces(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="request-logs.csv"',
                                      # request data: no browser or proxy may keep a copy
                                      "Cache-Control": "no-store", "Pragma": "no-cache"})


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
