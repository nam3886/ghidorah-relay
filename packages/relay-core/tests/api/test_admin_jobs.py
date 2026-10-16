"""Admin jobs (contracts 7.1, 7.5; D14, D15): the list and its cursor, the detail the page shows, debug files served as
text, Re-check refusals, the Recover and title commands, rename, and Regenerate at today's price through the gate."""

import dataclasses
import json
import tomllib
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from apikit import run_app
from jobkit import POOLS, PROVIDER, account, claim, create, key, run, submit

from relay_core.admin import routes_jobs
from relay_core.admin.routes_jobs import _EPOCH
from relay_core.jobs import store
from relay_core.jobs.create import marker_for
from relay_core.jobs.evidence import job_sink
from relay_core.providers import FrameUse
from relay_core.storage import StorageError, media_key
from relay_core.testing.fake_provider import FakeEngine
from relay_core.testing.mutation import mutation

DETAIL_COLUMNS = "j.callback_last, j.updated_at, j.provider_state, j.frame_id, j.sent_text "
NO_FRAME_COLUMNS = "j.callback_last, j.updated_at, j.provider_state, NULL, NULL "


def op() -> dict:
    """The body of one Regenerate action: a fresh operation key, so each call is its own action."""
    return {"json": {"operation_key": uuid.uuid4().hex}}

ROUTES = "packages/relay-core/src/relay_core/admin/routes_jobs.py"
NEWEST_FIRST = "ORDER BY j.created_at DESC, j.id DESC LIMIT %(limit)s"
OLDEST_FIRST = "ORDER BY j.created_at ASC, j.id DESC LIMIT %(limit)s"
OFFSET_CHECK = "if offset is not None and (not 0 <= offset <= MAX_OFFSET or cursor is not None):"
AFTER_CURSOR ="(j.created_at, j.id) < (%(at)s::timestamptz"
FROM_CURSOR = "(j.created_at, j.id) <= (%(at)s::timestamptz"
SERVED_AS_TEXT = 'media_type=kind if kind in _RASTER else "text/plain; charset=utf-8"'
SERVED_AS_STORED = "media_type=mime"
REASON = 'raise ApiError(e.code) from None\n    return {"id": str(job_id), "recharged"'
ANY_REASON = 'raise ApiError("job.busy") from None\n    return {"id": str(job_id), "recharged"'
DOORS = "packages/relay-core/src/relay_core/api/doors.py"
TODAYS_PLAN = "        return spec, spec.plan(creq, gate.settings, snapshots)"
OLD_PRICE_PLAN = ('        return spec, __import__("dataclasses").replace(spec.plan(creq, gate.settings, snapshots), '
                  "price=3)")
ALLOW_LIST = "        ok = keys.model_allowed(allowed, requested, catalog)"
NO_ALLOW_LIST = "        ok = True"
PLAN_REFUSAL = "if isinstance(exc, InputError) and exc.code"
NO_PLAN_REFUSAL = "if False and exc.code"
TERMINAL_ONLY = "if status not in TERMINAL:"
ANY_STATUS = "if False:"


def code_of(r) -> str:
    return r.json()["error"]["code"]


async def waiting_job(p, k, prompt="x", **opts) -> uuid.UUID:
    made = await create(p, k, prompt, **opts)
    got = await claim(p)
    assert await submit(p, got, FakeEngine()) == "waiting"
    return made.job_id


async def failed_job(p, k, prompt="x", **opts) -> uuid.UUID:
    job_id = await waiting_job(p, k, prompt, **opts)
    assert await store.fail_job(p, job_id, expect=("waiting",), code="render.deadline", pools=POOLS)
    return job_id


def debug_row(db, job, idx: int, object_key: str, mime: str, size: int) -> None:
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) VALUES (%s, 'debug', %s, %s, %s, %s)",
           job, idx, object_key, mime, size)


def ids_of(r) -> list[str]:
    return [i["id"] for i in r.json()["items"]]


# ---- list -------------------------------------------------------------------------------------------------------
@mutation(ROUTES, NEWEST_FIRST, OLDEST_FIRST, expect="newest first")
@mutation(ROUTES, AFTER_CURSOR, FROM_CURSOR, expect="no job twice")
def test_the_list_is_newest_first_filters_by_status_and_pages_without_a_gap_or_a_repeat(db):
    k = key(db)
    account(db)

    async def plant(p):
        sent = await failed_job(p, k, "failed one")
        for i in range(5):
            await create(p, k, "queued %d" % i)
        return sent

    failed = run(db, plant)
    order = [str(r[0]) for r in db.all("SELECT id FROM jobs ORDER BY created_at DESC, id DESC")]

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs" + q, headers=h)  # noqa: E731
        everything = await get("")
        pages, cursor = [], None
        while True:
            r = await get("?limit=2" + ("&cursor=" + cursor if cursor else ""))
            pages.append(ids_of(r))
            cursor = r.json()["next_cursor"]
            if cursor is None:
                break
        return (everything, pages, await get("?status=failed"), await get("?status=queued&provider=fake"),
                await get("?key_id=%s" % uuid.uuid4()), await get("?limit=0"), await get("?limit=201"),
                await get("?cursor=nonsense"))

    everything, pages, only_failed, queued, other_key, zero, huge, bad = run_app(db, scenario)
    assert ids_of(everything) == order, "newest first"
    flat = [i for page in pages for i in page]
    assert flat == order and len(set(flat)) == len(flat), "no job twice, none missing"
    assert [len(p) for p in pages] == [2, 2, 2] and everything.json()["next_cursor"] is None
    assert ids_of(only_failed) == [str(failed)] and len(ids_of(queued)) == 5 and ids_of(other_key) == []
    for r in (zero, huge, bad):
        assert (r.status_code, code_of(r)) == (400, "input.invalid")


@mutation(ROUTES, "LIMIT %(limit)s OFFSET %(offset)s", "LIMIT %(limit)s", expect="the offset slice")
@mutation(ROUTES, "(%(status)s::text IS NULL OR j.status = %(status)s)", "(true)", expect="total follows status")
@mutation(ROUTES, "j.provider = %(provider)s)", "true)", expect="total follows provider")
@mutation(ROUTES, "(%(key)s::uuid IS NULL OR j.key_id = %(key)s)", "(true)", expect="total follows key")
@mutation(ROUTES, '_COUNT = "SELECT count(*) FROM jobs j WHERE " + _FILTERS',
          '_COUNT = "SELECT count(*) FROM jobs j WHERE true"', expect="total follows status")
@mutation(ROUTES, OFFSET_CHECK, "if offset is not None and not 0 <= offset <= MAX_OFFSET:",
          expect="cursor with offset")
@mutation(ROUTES, OFFSET_CHECK, "if offset is not None and cursor is not None:", expect="negative offset")
def test_the_list_pages_by_offset_in_the_same_order_and_counts_the_jobs_the_filters_match(db):
    k = key(db)
    account(db)

    async def plant(p):
        await failed_job(p, k, "failed one")
        for i in range(5):
            await create(p, k, "queued %d" % i)

    run(db, plant)
    order = [str(r[0]) for r in db.all("SELECT id FROM jobs ORDER BY created_at DESC, id DESC")]

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs" + q, headers=h)  # noqa: E731
        by_cursor = await get("?limit=2")
        return (await get(""), await get("?limit=2&offset=0"), await get("?limit=2&offset=2"),
                await get("?limit=2&offset=4"), await get("?limit=2&offset=6"), by_cursor,
                await get("?limit=2&cursor=%s" % by_cursor.json()["next_cursor"]),
                await get("?status=failed&limit=1"), await get("?status=queued&provider=fake&limit=1"),
                await get("?provider=other"), await get("?key_id=%s" % k), await get("?key_id=%s" % uuid.uuid4()),
                await get("?offset=-1"), await get("?limit=2&offset=0&cursor=%s" % by_cursor.json()["next_cursor"]),
                await get("?limit=2&offset=2&cursor=%s" % by_cursor.json()["next_cursor"]), await get("?offset=x"))

    (everything, o0, o2, o4, o6, c0, c1, failed, queued, other_provider, own_key, other_key, negative, both0, both2,
     junk) = run_app(db, scenario)
    assert [ids_of(r) for r in (o0, o2, o4)] == [order[0:2], order[2:4], order[4:6]], "the offset slice"
    assert ids_of(o6) == [] and o6.json()["next_cursor"] is None
    assert o0.json()["next_cursor"] is not None and o4.json()["next_cursor"] is None
    assert ids_of(c0) == ids_of(o0) and ids_of(c1) == ids_of(o2), "the cursor and the offset agree"
    assert [r.json()["total"] for r in (everything, o0, o2, o6, c0, c1)] == [6] * 6, "total ignores the page"
    assert failed.json()["total"] == 1 and queued.json()["total"] == 5, "total follows status"
    assert other_provider.json()["total"] == 0, "total follows provider"
    assert own_key.json()["total"] == 6 and other_key.json()["total"] == 0, "total follows key"
    assert (negative.status_code, code_of(negative), negative.json()["error"]["param"]) == (
        400, "input.invalid", "offset"), "negative offset"
    assert code_of(both0) == code_of(both2) == code_of(junk) == "input.invalid", "cursor with offset"
    assert both0.status_code == both2.status_code == 400


OFFSET_NO_CAP = "if offset is not None and (offset < 0 or cursor is not None):"
OFFSET_CAP_OFF_BY_ONE = "if offset is not None and (not 0 <= offset < MAX_OFFSET or cursor is not None):"
OFFSET_TRUTHY_CURSOR = "if offset is not None and (not 0 <= offset <= MAX_OFFSET or cursor):"
CURSOR_PARSE = "at, cid = _cursor(cursor) if cursor is not None else (None, None)"
CURSOR_PARSE_TRUTHY = "at, cid = _cursor(cursor) if cursor else (None, None)"


@mutation(ROUTES, OFFSET_CHECK, OFFSET_NO_CAP, expect="offset above the cap")
@mutation(ROUTES, OFFSET_CHECK, OFFSET_CAP_OFF_BY_ONE, expect="offset at the cap is accepted")
@mutation(ROUTES, OFFSET_CHECK, OFFSET_TRUTHY_CURSOR, expect="empty cursor with offset")
@mutation(ROUTES, CURSOR_PARSE, CURSOR_PARSE_TRUTHY, expect="empty cursor alone")
def test_the_offset_is_capped_and_an_empty_cursor_counts_as_a_cursor(db):
    key(db)

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs?" + q, headers=h)  # noqa: E731
        return (await get("offset=1000000"), await get("offset=1000001"), await get("offset=%d" % 10**30),
                await get("offset=0&cursor="), await get("cursor="))

    at_cap, above, huge, empty_with_offset, empty = run_app(db, scenario)
    assert at_cap.status_code == 200, "offset at the cap is accepted"
    for r, what in ((above, "offset above the cap"), (huge, "offset above the cap"),
                    (empty_with_offset, "empty cursor with offset"), (empty, "empty cursor alone")):
        assert r.status_code == 400, what + ": " + r.text
        assert code_of(r) == "input.invalid", what
    assert above.json()["error"]["param"] == huge.json()["error"]["param"] == "offset", "offset above the cap"
    assert empty_with_offset.json()["error"]["param"] == "offset", "empty cursor with offset"
    assert empty.json()["error"]["param"] == "cursor", "empty cursor alone"


FROM_BOUND = '_FROM = " AND j.created_at >= %(from)s"'
FROM_EXCLUSIVE = '_FROM = " AND j.created_at > %(from)s"'
FROM_IGNORED = '_FROM = ""'
FROM_UNSARGABLE = '_FROM = " AND j.created_at + interval \'0\' >= %(from)s"'
TO_BOUND = '_TO = " AND j.created_at < %(to)s"'
TO_INCLUSIVE = '_TO = " AND j.created_at <= %(to)s"'
TO_IGNORED = '_TO = ""'
COUNT_BOUNDED = "conn.execute(_COUNT + bounds, args)"
COUNT_NO_WINDOW = "conn.execute(_COUNT, args)"
PAGE_BOUNDED = "_LIST_HEAD + bounds + _LIST_TAIL"
PAGE_NO_WINDOW = "_LIST_HEAD + _LIST_TAIL"
NEGATIVE_BOUND = "        if value < 0:"
NEGATIVE_ALLOWED = "        if value < -5:"
EMPTY_WINDOW = 'window["from"] >= window["to"]'
INVERTED_ONLY = 'window["from"] > window["to"]'
T0 = 1_700_000_000_000                               # a fixed epoch (ms): the window starts here
HOUR = 3_600_000


@mutation(ROUTES, FROM_BOUND, FROM_EXCLUSIVE, expect="window start is inclusive")
@mutation(ROUTES, FROM_BOUND, FROM_IGNORED, expect="window start")
@mutation(ROUTES, TO_BOUND, TO_INCLUSIVE, expect="window end is exclusive")
@mutation(ROUTES, TO_BOUND, TO_IGNORED, expect="window end")
@mutation(ROUTES, COUNT_BOUNDED, COUNT_NO_WINDOW, expect="total follows the window")
@mutation(ROUTES, PAGE_BOUNDED, PAGE_NO_WINDOW, expect="window start is inclusive")
def test_the_window_bounds_the_rows_and_the_total_on_every_page(db):
    k = key(db)
    # created_at epochs: before, at the start, inside, the last ms inside, at the end (excluded), after
    spots = [T0 - 1, T0, T0 + 1000, T0 + HOUR - 1, T0 + HOUR, T0 + 2 * HOUR]
    jobs = [db.job(k, provider="fake") for _ in spots]
    for job, spot in zip(jobs, spots, strict=True):
        db.run("UPDATE jobs SET created_at = to_timestamp(%s::double precision / 1000) WHERE id = %s", spot, job)
    inside = [str(j) for j in (jobs[3], jobs[2], jobs[1])]          # newest first
    window = "created_from_ms=%d&created_to_ms=%d" % (T0, T0 + HOUR)

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs?" + q, headers=h)  # noqa: E731
        return (await get(window), await get(window + "&limit=2&offset=0"), await get(window + "&limit=2&offset=2"),
                await get("created_from_ms=%d" % T0), await get("created_to_ms=%d" % T0),
                await get("created_from_ms=%d&created_to_ms=%d&status=failed" % (T0, T0 + HOUR)),
                await get("created_from_ms=%d&created_to_ms=%d" % (T0 + 5 * HOUR, T0 + 6 * HOUR)))

    whole, page1, page2, only_from, only_to, other_status, empty = run_app(db, scenario)
    assert ids_of(whole) == inside, "window start is inclusive; window end is exclusive"
    assert [ids_of(page1), ids_of(page2)] == [inside[:2], inside[2:]], "the window applies before paging"
    assert [r.json()["total"] for r in (whole, page1, page2)] == [3, 3, 3], "total follows the window"
    assert only_from.json()["total"] == 5 and only_to.json()["total"] == 1, "window start; window end"
    assert other_status.json()["total"] == 0 and empty.json()["total"] == 0, "total follows the window"


TO_INCLUSIVE_UNSARGABLE = '_TO = " AND j.created_at + interval \'0\' < %(to)s"'
JOB_COLUMNS = ("id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, after_sent, "
               "submit_timeout_s, wallet_kind, price")
BASE_MS = 1_759_996_800_000                         # the newest seeded job; the window sits 10 days before it
SEED_SPREAD = 259                                   # seconds between seeded jobs: 10 000 of them cover 30 days


@mutation(ROUTES, FROM_BOUND, FROM_UNSARGABLE, expect="the window is served by jobs_created_at_idx")
@mutation(ROUTES, TO_BOUND, TO_INCLUSIVE_UNSARGABLE, expect="the window is served by jobs_created_at_idx")
def test_an_hour_window_is_served_by_the_created_at_index_on_a_realistic_table(db):
    """10 000 jobs over 30 days, ANALYZEd, the planner free to choose (no setting forced): a one-hour window, with
    either bound or both, must range-scan jobs_created_at_idx for the page and for the count."""
    seed = db.job(key(db), provider="fake")
    db.run("INSERT INTO jobs (" + JOB_COLUMNS + ", created_at) SELECT gen_random_uuid(), provider, key_id, operation, "
           "media_type, model, prompt, 'seed-' || g, status, completion, after_sent, submit_timeout_s, wallet_kind, "
           "price, to_timestamp(%s::double precision / 1000) - g * %s * interval '1 second' "
           "FROM jobs, generate_series(1, 10000) g WHERE id = %s", BASE_MS, SEED_SPREAD, seed)
    db.run("ANALYZE jobs")
    start = _EPOCH + timedelta(milliseconds=BASE_MS - 10 * 24 * HOUR)
    windows = {"from and to": {"from": start, "to": start + timedelta(hours=1)}, "from only": {"from": start,
               "to": None}, "to only": {"from": None, "to": start}}
    with db.connect() as conn:
        for what, window in windows.items():
            given = {k: v for k, v in window.items() if v is not None}
            args = {"status": None, "provider": None, "key": None, "at": None, "cid": None, "limit": 51, "offset": 0,
                    **given}
            bounds = routes_jobs._window_sql(window)
            for name, sql in (("COUNT", routes_jobs._COUNT + bounds),
                              ("LIST", routes_jobs._LIST_HEAD + bounds + routes_jobs._LIST_TAIL)):
                plan = "\n".join(r[0] for r in psycopg.ClientCursor(conn).execute("EXPLAIN " + sql, args))
                print("\nWINDOWED JOBS %s PLAN (%s)\n%s" % (name, what, plan))
                if what != "from and to":
                    continue                          # open-ended windows: the plan is printed, the planner decides
                cond = [line for line in plan.splitlines() if "Index Cond" in line and "created_at >=" in line
                        and "created_at <" in line]
                assert "jobs_created_at_idx" in plan and cond and "Seq Scan on jobs" not in plan, (
                    "the window is served by jobs_created_at_idx (%s):\n%s" % (name, plan))


@mutation(ROUTES, NEGATIVE_BOUND, NEGATIVE_ALLOWED, expect="negative bound")
@mutation(ROUTES, EMPTY_WINDOW, INVERTED_ONLY, expect="from equal to is empty")
def test_a_bad_window_is_refused_not_ignored(db):
    key(db)

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs?" + q, headers=h)  # noqa: E731
        return (await get("created_from_ms=x"), await get("created_to_ms=1.5"), await get("created_from_ms=-1"),
                await get("created_to_ms=-1"), await get("created_from_ms=%d&created_to_ms=%d" % (T0 + 1, T0)),
                await get("created_from_ms=%d&created_to_ms=%d" % (T0, T0)), await get("created_from_ms=" + "9" * 30),
                await get("created_from_ms=0&created_to_ms=1"))

    not_int, fractional, negative_from, negative_to, inverted, equal, huge, fine = run_app(db, scenario)
    for r, what in ((not_int, "not an integer"), (fractional, "not an integer"), (negative_from, "negative bound"),
                    (negative_to, "negative bound"), (inverted, "from after to"), (equal, "from equal to is empty"),
                    (huge, "out of range")):
        assert r.status_code == 400, what + ": " + r.text
        assert code_of(r) == "input.invalid", what
    assert negative_from.json()["error"]["param"] == "created_from_ms", "negative bound"
    assert negative_to.json()["error"]["param"] == "created_to_ms", "negative bound"
    assert fine.status_code == 200 and fine.json()["total"] == 0


# ---- per-day totals (`tz`) ---------------------------------------------------------------------------------------
TZ = "Asia/Ho_Chi_Minh"                  # Saigon: UTC+7, no clock change (the canonical name of Asia/Saigon)
SAIGON = ZoneInfo(TZ)
DAY_TOTALS_SQL = '"SELECT (j.created_at AT TIME ZONE %(tz)s)::date, count(*) FROM jobs j WHERE " + _FILTERS)'
DAY_TOTALS_UNFILTERED = '"SELECT (j.created_at AT TIME ZONE %(tz)s)::date, count(*) FROM jobs j WHERE " + "true")'
DAY_FIRST = "j.created_at >= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s"
DAY_FIRST_UNSARGABLE = "j.created_at + interval '0' >= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s"


def saigon_ms(day: str, hhmm: str) -> int:
    """Epoch ms of a Saigon wall-clock time, so a test names the local time it means."""
    return int(datetime.fromisoformat("%sT%s:00" % (day, hhmm)).replace(tzinfo=SAIGON).timestamp() * 1000)


def place(db, job, day: str, hhmm: str) -> None:
    db.run("UPDATE jobs SET created_at = to_timestamp(%s::double precision / 1000) WHERE id = %s",
           saigon_ms(day, hhmm), job)


class Soft:
    """Every failed check of a test is named in its one failure, so each marker's `expect=` finds its own."""

    def __init__(self):
        self.failed = []

    def __call__(self, ok: bool, what: str) -> None:
        if not ok:
            self.failed.append(what)

    def done(self) -> None:
        assert not self.failed, "; ".join(self.failed)


@mutation(ROUTES, "(j.created_at AT TIME ZONE %(tz)s)::date", "(j.created_at AT TIME ZONE 'UTC')::date",
          expect="the local day")
@mutation(ROUTES, ">= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s",
          ">= (%(day_first)s::date)::timestamp AT TIME ZONE 'UTC'", expect="the start of the earliest day")
@mutation(ROUTES, "((%(day_last)s::date + 1)::timestamp", "((%(day_last)s::date + 0)::timestamp",
          expect="the end of the latest day")
@mutation(ROUTES, "if zone is not None and page:", "if zone is not None:", expect="an empty page")
def test_day_totals_count_every_job_of_a_local_day_not_only_those_on_the_page(db):
    k = key(db)
    jobs = [db.job(k, provider=PROVIDER) for _ in range(6)]
    # Saigon: 23:30 and 00:30 are one hour apart, share a UTC day and sit on two local days; 05:00 is before the
    # UTC midnight (07:00) of the earliest local day
    when = [("2026-03-09", "05:00"), ("2026-03-10", "01:00"), ("2026-03-10", "12:00"), ("2026-03-10", "23:30"),
            ("2026-03-11", "00:30"), ("2026-03-11", "08:00")]
    for job, (day, hhmm) in zip(jobs, when, strict=True):
        place(db, job, day, hhmm)

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs?tz=" + TZ + q, headers=h)  # noqa: E731
        return (await get("&limit=2"), await get("&limit=2&offset=2"), await get("&limit=4"), await get("&limit=50"),
                await get("&limit=2&offset=9"), await env.client.get("/admin/jobs?tz=UTC&limit=50", headers=h))

    newest, middle, four, everything, past_the_end, utc = run_app(db, scenario)
    check = Soft()
    check(newest.json()["day_totals"] == {"2026-03-11": 2}, "the local day: newest page")
    check(middle.json()["day_totals"] == {"2026-03-10": 3}, "a day counted beyond the page")
    check(four.json()["day_totals"] == {"2026-03-10": 3, "2026-03-11": 2}, "the end of the latest day")
    check(everything.json()["day_totals"] == {"2026-03-09": 1, "2026-03-10": 3, "2026-03-11": 2},
          "the start of the earliest day")
    check(list(everything.json()["day_totals"]) == sorted(everything.json()["day_totals"]), "days in order")
    check(past_the_end.json().get("day_totals") == {}, "an empty page")
    check(utc.json()["day_totals"] == {"2026-03-08": 1, "2026-03-09": 1, "2026-03-10": 3, "2026-03-11": 1},
          "the local day: UTC")
    check.done()


@mutation(ROUTES, "_DAY_TOTALS + bounds + _DAY_RANGE", "_DAY_TOTALS + _DAY_RANGE", expect="totals follow the window")
@mutation(ROUTES, DAY_TOTALS_SQL, DAY_TOTALS_UNFILTERED, expect="totals follow the filters")
@mutation(ROUTES, "j.provider = %(provider)s)", "true)", expect="totals follow the filters")
@mutation(ROUTES, "(%(key)s::uuid IS NULL OR j.key_id = %(key)s)", "(true)", expect="totals follow the filters")
@mutation(ROUTES, "(%(status)s::text IS NULL OR j.status = %(status)s)", "(true)", expect="totals follow the filters")
def test_every_filter_of_the_list_changes_the_day_totals_the_way_it_changes_the_list(db):
    k, other_key = key(db), key(db)
    account(db)

    async def plant(p):
        return [await failed_job(p, k, "failed"), await create(p, k, "queued a"), await create(p, k, "queued b"),
                await create(p, other_key, "queued c")]

    jobs = [j if isinstance(j, uuid.UUID) else j.job_id for j in run(db, plant)]
    for job, hhmm in zip(jobs, ("10:00", "11:00", "12:00", "13:00"), strict=True):
        place(db, job, "2026-03-10", hhmm)
    window = "created_from_ms=%d&created_to_ms=%d" % (
        saigon_ms("2026-03-10", "12:00"), saigon_ms("2026-03-10", "14:00"))
    filters = {"": 4, "status=failed": 1, "status=queued": 3, "key_id=%s" % k: 3, "key_id=%s" % other_key: 1,
               "provider=" + PROVIDER: 4, "provider=nobody": 0, window: 2, window + "&status=failed": 0,
               window + "&key_id=%s" % k: 1}

    async def scenario(env):
        h = await env.admin_headers()
        return {q: await env.client.get("/admin/jobs?tz=%s&limit=1&%s" % (TZ, q), headers=h) for q in filters}

    check = Soft()
    for q, r in run_app(db, scenario).items():
        body = r.json()
        check(body["total"] == filters[q], "the list's own total: " + q)
        # one row on the page, yet the day's count is every job the same filters match that day
        want = {"2026-03-10": filters[q]} if filters[q] else {}
        check(body["day_totals"] == want, ("totals follow the window: " if "created_" in q else
                                           "totals follow the filters: ") + q)
    check.done()


@mutation(ROUTES, '    if zone is not None:\n        out["day_totals"] = day_totals',
          '    out["day_totals"] = day_totals', expect="no tz, no field")
@mutation(ROUTES, "zone = None if tz is None else _zone(tz)", "zone = None if tz is None else ZoneInfo(tz)",
          expect="a bad tz")
def test_without_tz_the_answer_has_no_day_totals_and_a_bad_tz_is_refused(db):
    k = key(db)
    db.run("UPDATE api_keys SET label = 'Golden key' WHERE id = %s", k)
    job = db.job(k, provider=PROVIDER)
    place(db, job, "2026-03-10", "10:00")
    # the answer a client without `tz` has always got, byte for byte (key order and compact separators included)
    golden = {"items": [{
        "id": str(job), "provider": PROVIDER, "key_id": str(k), "key_label": "Golden key", "operation": "generate",
        "media_type": "video", "model": "m", "title": None, "status": "queued", "error_code": None, "price": 0,
        "account_id": None, "account_label": None, "external_ref": None, "created_ms": saigon_ms("2026-03-10", "10:00"),
        "finished_ms": None, "error_status": None, "can_recheck": False, "outputs": 0, "inputs": 0, "net_charged": 0,
        "model_substituted": False}], "total": 1, "next_cursor": None}

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get("/admin/jobs" + q, headers=h)  # noqa: E731
        refused = ("Mars/Olympus", "", "..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd")
        return [await get("")] + [await get("?tz=" + bad) for bad in refused]

    plain, *bad = run_app(db, scenario)
    check = Soft()
    check(plain.status_code == 200 and plain.content == json.dumps(golden, separators=(",", ":")).encode(),
          "no tz, no field: the old answer, byte for byte")
    for r in bad:
        check((r.status_code, code_of(r), r.json()["error"]["param"]) == (400, "input.invalid", "tz"), "a bad tz")
    check.done()


PYPROJECT = "packages/relay-core/pyproject.toml"
TZDATA = '    "tzdata>=2024.1",            # zoneinfo falls back to it'
TZDATA_WIN_ONLY = '    "tzdata>=2024.1; sys_platform == \'win32\'",            # zoneinfo falls back to it'


@mutation(PYPROJECT, TZDATA, TZDATA_WIN_ONLY, expect="tzdata on every platform")
def test_relay_core_depends_on_the_tzdata_package_on_every_platform():
    """The image has no system tz database and Chrome in Vietnam reports `Asia/Saigon` (a legacy alias): zoneinfo
    must find its zones in the package, not only on Windows."""
    root = Path(__file__).resolve().parents[4]
    deps = tomllib.loads((root / PYPROJECT).read_text())["project"]["dependencies"]
    tz = [d for d in deps if d.replace(">", " ").split()[0] == "tzdata"]
    assert len(tz) == 1 and ";" not in tz[0], "tzdata on every platform: %s" % tz
    lock = tomllib.loads((root / "uv.lock").read_text())
    core = next(p for p in lock["package"] if p["name"] == "relay-core")
    assert {"name": "tzdata"} in core["dependencies"], "tzdata on every platform: the lock names it"


@mutation(ROUTES, "        return ZoneInfo(name)", '        return ZoneInfo(name.replace("Saigon", "Nowhere"))',
          expect="the alias answers")
def test_the_browser_zone_asia_saigon_gives_the_same_day_totals_as_asia_ho_chi_minh(db):
    """Chrome in Vietnam reports `Asia/Saigon`: it must answer, and group by the same local days (this also proves
    Postgres `AT TIME ZONE` takes the alias)."""
    k = key(db)
    for day, hhmm in (("2026-03-10", "23:30"), ("2026-03-11", "00:30"), ("2026-03-11", "08:00")):
        place(db, db.job(k, provider=PROVIDER), day, hhmm)

    async def scenario(env):
        h = await env.admin_headers()
        return [await env.client.get("/admin/jobs?tz=" + zone, headers=h) for zone in ("Asia/Saigon", TZ)]

    alias, canonical = run_app(db, scenario)
    check = Soft()
    check(alias.status_code == 200 and "day_totals" in alias.json(), "the alias answers")
    check(alias.json().get("day_totals") == {"2026-03-10": 1, "2026-03-11": 2}, "the alias gives the same days")
    check(alias.json().get("day_totals") == canonical.json()["day_totals"], "the alias gives the same days")
    check.done()


@mutation(ROUTES, "    except psycopg.errors.InvalidParameterValue:", "    except psycopg.errors.UndefinedTable:",
          expect="a zone Postgres does not know")
def test_a_zone_python_knows_and_postgres_does_not_is_a_bad_tz_not_a_server_error(db, monkeypatch):
    """Python's tzdata and Postgres's own zone database can differ. Python is made to accept a name Postgres has never
    heard of: the query's `unrecognized time zone` error is the caller's bad `tz`: 400 `input.invalid`, never 500."""
    k = key(db)
    place(db, db.job(k, provider=PROVIDER), "2026-03-10", "10:00")
    monkeypatch.setattr(routes_jobs, "_zone", lambda name: SAIGON)

    async def scenario(env):
        h = await env.admin_headers()
        return [await env.client.get("/admin/jobs?tz=" + zone, headers=h) for zone in ("Mars/Olympus", TZ)]

    unknown, known = run_app(db, scenario)
    check = Soft()
    check((unknown.status_code, code_of(unknown)) == (400, "input.invalid"), "a zone Postgres does not know")
    check(unknown.status_code != 400 or unknown.json()["error"]["param"] == "tz", "a zone Postgres does not know")
    check(known.status_code == 200 and known.json()["day_totals"] == {"2026-03-10": 1}, "a known zone still answers")
    check.done()


@mutation(ROUTES, DAY_FIRST, DAY_FIRST_UNSARGABLE, expect="the day totals are served by jobs_created_at_idx")
def test_the_day_totals_query_is_served_by_the_created_at_index_on_a_realistic_table(db):
    """10 000 jobs over 30 days, ANALYZEd, nothing forced: the one-day totals range-scan jobs_created_at_idx."""
    seed = db.job(key(db), provider="fake")
    db.run("INSERT INTO jobs (" + JOB_COLUMNS + ", created_at) SELECT gen_random_uuid(), provider, key_id, operation, "
           "media_type, model, prompt, 'seed-' || g, status, completion, after_sent, submit_timeout_s, wallet_kind, "
           "price, to_timestamp(%s::double precision / 1000) - g * %s * interval '1 second' "
           "FROM jobs, generate_series(1, 10000) g WHERE id = %s", BASE_MS, SEED_SPREAD, seed)
    db.run("ANALYZE jobs")
    day = (_EPOCH + timedelta(milliseconds=BASE_MS - 10 * 24 * HOUR)).date()
    args = {"status": None, "provider": None, "key": None, "tz": TZ, "day_first": day, "day_last": day}
    with db.connect() as conn:
        plan = "\n".join(r[0] for r in psycopg.ClientCursor(conn).execute(
            "EXPLAIN " + routes_jobs._DAY_TOTALS + routes_jobs._DAY_RANGE, args))
    print("\nDAY TOTALS PLAN\n" + plan)
    cond = [line for line in plan.splitlines() if "Index Cond" in line and "created_at >=" in line
            and "created_at <" in line]
    assert "jobs_created_at_idx" in plan and cond and "Seq Scan on jobs" not in plan, (
        "the day totals are served by jobs_created_at_idx:\n" + plan)


# ---- detail -----------------------------------------------------------------------------------------------------
@mutation(ROUTES, DETAIL_COLUMNS, NO_FRAME_COLUMNS, expect="the frame version")
def test_the_detail_shows_the_frame_version_and_the_exact_text_sent_and_nulls_for_a_job_made_before_frames(db):
    k = key(db)
    account(db)
    frame_id = db.one("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES "
                      "('fake', 'video', NULL, 'Own. {{client_prompt}} {{tracking_code}}', 'test') RETURNING id")[0]
    own = FrameUse(frame_id, "Own. {{client_prompt}} {{tracking_code}}")
    framed = run(db, lambda p: failed_job(p, k, "a cat", frame=own))
    plain = run(db, lambda p: failed_job(p, k, "a dog"))

    async def scenario(env):
        h = await env.admin_headers()
        return [(await env.client.get("/admin/jobs/%s" % j, headers=h)).json() for j in (framed, plain)]

    with_frame, without = run_app(db, scenario)
    assert with_frame["frame_id"] == frame_id, "the frame version the job used"
    assert with_frame["sent_text"] == "Own. a cat [%s]" % with_frame["marker"], "the exact text that was sent"
    assert without["frame_id"] is None and without["sent_text"].startswith("a dog"), "the core frame has no row"


def test_the_detail_shows_the_account_label_the_charged_price_a_clean_prompt_events_media_and_epoch_ms(db):
    k = key(db)
    acct = account(db)
    db.run("UPDATE accounts SET label = 'main acct' WHERE id = %s", acct)

    async def plant(p):
        job = await failed_job(p, k, "a cat on a roof", inputs=[store.InputMedia(0, "o/input/0.png", "image/png", 5)])
        queued = await create(p, k, "still queued")
        return job, queued.job_id

    failed, queued = run(db, plant)
    db.run("UPDATE jobs SET params = params || '{\"price\": 999, \"cost\": 5}'::jsonb WHERE id = %s", failed)
    debug_row(db, failed, 0, "d/debug/0.html", "text/html", 12)
    token = db.one("SELECT share_token FROM job_media WHERE job_id = %s AND role = 'input'", failed)[0]

    async def scenario(env):
        h = await env.admin_headers()
        return [(await env.client.get("/admin/jobs/%s" % j, headers=h)) for j in (failed, queued, uuid.uuid4())]

    done, wait, ghost = run_app(db, scenario)
    d = done.json()
    assert d["status"] == "failed" and d["error_code"] == "render.deadline" and d["error_status"] == 504
    assert d["account_label"] == "main acct" and d["account_id"] == str(acct), "the label, not only the id"
    assert d["price"] == 10 and d["net_charged"] == 0, "money is the job's own charged price and its ledger"
    assert d["prompt"] == "a cat on a roof" and d["marker"] == marker_for(failed) and d["marker"] not in d["prompt"]
    assert d["can_recheck"] is True and wait.json()["can_recheck"] is False
    assert [e["source"] for e in d["events"]][0] == "system" and d["events"][0]["stage"] == "queued"
    assert all(isinstance(e["at_ms"], int) for e in d["events"]) and isinstance(d["created_ms"], int)
    assert d["finished_ms"] is not None and wait.json()["finished_ms"] is None
    [media] = d["media"]
    assert media["role"] == "input" and media["url"] == "/m/" + token, "only a same-origin /m/ link"
    assert d["debug_files"] == [{"idx": 0, "mime": "text/html", "size": 12,
                                 "url": "/admin/jobs/%s/debug/0" % failed}]
    assert "d/debug/0.html" not in done.text, "no object key leaves the api"
    assert (ghost.status_code, code_of(ghost)) == (404, "resource.not_found")


@mutation(ROUTES, SERVED_AS_TEXT, SERVED_AS_STORED, expect="served as text/plain")
def test_a_debug_file_is_served_as_text_so_a_captured_page_cannot_run_in_the_admin_origin(db):
    k = key(db)
    account(db)
    job = run(db, lambda p: failed_job(p, k))
    page = b"<html><script>alert(document.cookie)</script></html>"
    png = b"\x89PNG\r\n\x1a\n-debug"

    async def scenario(env):
        h = await env.admin_headers()
        for idx, (mime, data) in enumerate((("text/html", page), ("image/png", png), ("application/json", b"{}"))):
            key_ = media_key(str(job), "debug", idx, mime)
            await env.store.put_bytes(key_, data, mime)
            debug_row(db, job, idx, key_, mime, len(data))
        debug_row(db, job, 3, "nope/debug/3.txt", "text/plain", 1)
        got = [await env.client.get("/admin/jobs/%s/debug/%d" % (job, i), headers=h) for i in range(5)]
        for i in range(3):
            await env.store.remove(media_key(str(job), "debug", i, ("text/html", "image/png", "application/json")[i]))
        return got

    html, image, data, missing_object, missing_row = run_app(db, scenario)
    assert html.content == page and html.headers["content-type"].startswith("text/plain"), "served as text/plain"
    assert image.content == png and image.headers["content-type"] == "image/png"
    assert data.headers["content-type"].startswith("text/plain")
    for r in (html, image, data):
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert missing_object.status_code == 404 and missing_row.status_code == 404


# ---- Re-check ---------------------------------------------------------------------------------------------------
@mutation(ROUTES, REASON, ANY_REASON, expect="the right reason")
def test_recheck_revives_a_failed_job_and_refuses_with_the_reason_of_the_state_it_is_in(db):
    k = key(db)
    account(db)

    async def plant(p):
        failed = await failed_job(p, k)
        busy = (await create(p, k, "being sent")).job_id
        await claim(p)                                                          # queued -> submitting: a run holds it
        queued = (await create(p, k, "never sent")).job_id
        return failed, queued, busy

    failed, queued, busy = run(db, plant)

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda j: env.client.post("/admin/jobs/%s/recheck" % j, headers=h)  # noqa: E731
        return [await post(j) for j in (failed, queued, busy, uuid.uuid4())]

    ok, never, running, ghost = run_app(db, scenario)
    assert ok.status_code == 200 and ok.json() == {"id": str(failed), "recharged": 10}
    assert db.one("SELECT status, error_code, poll_token IS NOT NULL FROM jobs WHERE id = %s", failed) == (
        "waiting", None, True)
    reasons = [(r.status_code, code_of(r)) for r in (never, running, ghost)]
    assert reasons == [(409, "job.not_submitted"), (409, "job.busy"), (404, "resource.not_found")], "the right reason"


# ---- Recover and titles -----------------------------------------------------------------------------------------
@mutation(ROUTES, TERMINAL_ONLY, ANY_STATUS, expect="a running job is not recovered")
def test_recover_queues_a_command_for_a_finished_job_and_refuses_a_running_one_or_an_unclear_account(db):
    k = key(db)
    main, twin_a, twin_b = account(db), account(db), account(db)
    db.run("UPDATE accounts SET label = 'main' WHERE id = %s", main)
    db.run("UPDATE accounts SET label = 'twin' WHERE id IN (%s, %s)", twin_a, twin_b)
    db.run("INSERT INTO accounts (provider, label, credential_enc) VALUES ('other', 'elsewhere', '\\x01')")

    async def plant(p):
        return await failed_job(p, k), await waiting_job(p, k, "running")

    failed, running = run(db, plant)

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda j, account_: env.client.post("/admin/jobs/%s/recover" % j, json={"account": account_}, headers=h)  # noqa: E731
        return {"label": await post(failed, "main"), "id": await post(failed, str(twin_a)),
                "running": await post(running, "main"), "twins": await post(failed, "twin"),
                "none": await post(failed, "nobody"), "other": await post(failed, "elsewhere"),
                "ghost": await post(uuid.uuid4(), "main"),
                "extra": await env.client.post("/admin/jobs/%s/recover" % failed, json={"account": "main", "x": 1},
                                               headers=h)}

    out = run_app(db, scenario)
    running = out["running"]
    assert running.status_code == 409, "a running job is not recovered"
    assert code_of(running) == "job.busy"
    assert out["label"].status_code == 202 and out["id"].status_code == 202
    rows = db.all("SELECT provider, kind, account_id, job_id, status, args->>'job_id' FROM commands "
                  "ORDER BY created_at")
    assert rows == [("fake", "recover", main, failed, "queued", str(failed)),
                    ("fake", "recover", twin_a, failed, "queued", str(failed))]
    for name in ("twins", "none", "other", "extra"):
        assert (out[name].status_code, code_of(out[name])) == (400, "input.invalid"), name
    assert out["twins"].json()["error"]["param"] == "account" and out["ghost"].status_code == 404


@mutation(ROUTES, 'command_id = await enqueue(conn, job[0], "titles", account_id=job[2], job_id=job_id,',
          'command_id = await enqueue(conn, job[0], "titles", job_id=job_id,', expect="own account")
@mutation(ROUTES, "if job[1] is None or job[2] is None:", "if job[1] is None:", expect="no account")
def test_a_title_fetch_queues_the_titles_command_for_the_jobs_reference_on_the_jobs_account(db):
    k = key(db)
    account_id = account(db)

    async def plant(p):
        sent, orphan = await waiting_job(p, k), await waiting_job(p, k, "orphan")
        return sent, (await create(p, k, "unsent")).job_id, orphan

    sent, unsent, orphan = run(db, plant)
    db.run("UPDATE jobs SET account_id = NULL WHERE id = %s", orphan)       # its account was deleted
    ref = db.one("SELECT external_ref FROM jobs WHERE id = %s", sent)[0]

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda j: env.client.post("/admin/jobs/%s/fetch-title" % j, headers=h)  # noqa: E731
        return [await post(j) for j in (sent, unsent, uuid.uuid4(), orphan)]

    ok, no_ref, ghost, no_account = run_app(db, scenario)
    assert ok.status_code == 202 and (no_ref.status_code, code_of(no_ref)) == (409, "job.not_submitted")
    assert ghost.status_code == 404
    assert (no_account.status_code, code_of(no_account)) == (409, "job.not_submitted"), "no account: " + no_account.text
    assert db.all("SELECT kind, account_id, job_id, args FROM commands") == [
        ("titles", account_id, sent, {"refs": [ref], "account_id": str(account_id)})], (
        "the titles command runs on the own account of the job")


# ---- rename -----------------------------------------------------------------------------------------------------
def test_rename_sets_one_clean_line_clears_with_null_and_refuses_a_long_title(db):
    k = key(db)
    job = run(db, lambda p: create(p, k))

    async def scenario(env):
        h = await env.admin_headers()
        patch = lambda body, j=job.job_id: env.client.patch("/admin/jobs/%s" % j, json=body, headers=h)  # noqa: E731
        return {"set": await patch({"title": "  my\n  title  "}), "long": await patch({"title": "x" * 201}),
                "edge": await patch({"title": "y" * 200}), "clear": await patch({"title": None}),
                "empty": await patch({"title": "   "}), "extra": await patch({"title": "a", "price": 0}),
                "ghost": await patch({"title": "a"}, uuid.uuid4())}

    out = run_app(db, scenario)
    assert out["set"].json()["title"] == "my title"
    assert out["edge"].status_code == 200 and (out["long"].status_code, code_of(out["long"])) == (400, "input.invalid")
    assert out["long"].json()["error"]["param"] == "title"
    assert out["clear"].json()["title"] is None and out["empty"].json()["title"] is None
    assert db.one("SELECT title FROM jobs WHERE id = %s", job.job_id) == (None,), "a cleared title is NULL"
    assert out["extra"].status_code == 400 and out["ghost"].status_code == 404


# ---- Regenerate -------------------------------------------------------------------------------------------------
SELECT_REQUEST = "SELECT j.request, k.models FROM jobs j JOIN"
SELECT_PARAMS = ("SELECT j.params || jsonb_build_object('model', j.model, 'prompt', j.prompt), k.models "
                 "FROM jobs j JOIN")


@mutation(ROUTES, SELECT_REQUEST, SELECT_PARAMS, expect="stored request")
@mutation("packages/relay-core/src/relay_core/jobs/recheck.py", ", title, request=request)", ", title)",
          expect="the regenerated job keeps the client request")
def test_regenerate_plans_from_the_request_the_client_sent_never_from_the_plans_params(db, monkeypatch):
    seen = []

    async def scenario(env):
        spec = env.registry.spec(PROVIDER)
        real = spec.plan

        def plan(req, settings, snapshots):                   # a plan that writes its own keys into params
            seen.append(req)
            made = real(req, settings, snapshots)
            return dataclasses.replace(made, params={**made.params, "prompt_hint": "hint", "internal_token": "t-1"})

        monkeypatch.setattr(spec, "plan", plan)
        api_key = await env.new_key()
        made = await env.client.post("/v1/video/generations", headers=api_key.headers, json={
            "model": "fake-video", "prompt": "a cat", "duration": 5, "ratio": "16:9", "n": 2,
            "options": {"account_cost": 2}, "title": "t"})
        assert made.status_code == 200, made.text
        again = await env.client.post("/admin/jobs/%s/regenerate" % made.json()["id"],
                                      headers=await env.admin_headers(), **op())
        assert again.status_code == 200, again.text
        return made.json()["id"], again.json()["id"]

    old, new = run_app(db, scenario)
    first, second = seen
    assert second == dataclasses.replace(first, door=second.door), (
        "regenerate must plan from the stored request, not from params: %r vs %r" % (second, first))
    assert dict(second.options) == {"account_cost": 2} and (second.duration, second.ratio, second.n) == (5, "16:9", 2)
    assert db.one("SELECT params ? 'internal_token' FROM jobs WHERE id = %s", new) == (True,), "the plan keeps its own"
    assert db.one("SELECT request = (SELECT request FROM jobs WHERE id = %s), request_hash FROM jobs WHERE id = %s",
                  old, new) == (True, None), "the regenerated job keeps the client request, and is no client retry"


@mutation(ROUTES, "if job[0] is None:", "if False:", expect="no stored request")
def test_a_job_with_no_stored_request_cannot_be_regenerated(db):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    db.run("UPDATE jobs SET request = NULL WHERE id = %s", old)

    async def scenario(env):
        return await env.client.post("/admin/jobs/%s/regenerate" % old, headers=await env.admin_headers(), **op())

    r = run_app(db, scenario)
    got = (r.status_code, code_of(r), r.json()["error"]["param"])
    assert got == (400, "input.invalid", "job"), "no stored request: %s" % (got,)
    assert db.one("SELECT count(*) FROM jobs") == (1,)


@mutation(DOORS, TODAYS_PLAN, OLD_PRICE_PLAN, expect="today's price")
def test_regenerate_makes_a_new_job_at_todays_price_keeping_the_prompt_title_and_key(db):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat", price=3))             # sold at 3; the fake spec charges 10 today
    db.run("UPDATE jobs SET title = 'my title' WHERE id = %s", old)

    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.post("/admin/jobs/%s/regenerate" % old, headers=h, **op())

    r = run_app(db, scenario)
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["queue_position"] == 1
    new = uuid.UUID(r.json()["id"])
    cols = "key_id, provider, prompt, title, model, price, status, marker"
    assert db.one("SELECT %s FROM jobs WHERE id = %%s" % cols, new) == (
        k, PROVIDER, "a cat", "my title", "fake-video", 10, "queued", marker_for(new)), "today's price, a new marker"
    assert db.one("SELECT price, status FROM jobs WHERE id = %s", old) == (3, "failed")
    assert db.one("SELECT amount FROM ledger WHERE job_id = %s AND entry = 'charge'", new) == (10,)
    assert db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'regenerated'", old) == (1,)


@mutation(DOORS, ALLOW_LIST, NO_ALLOW_LIST, expect="auth.model_forbidden")
def test_regenerate_goes_through_the_gate_the_allow_list_the_key_the_wallet_and_the_cap_refuse_it(db):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    day_limit = "UPDATE key_wallets SET day_limit = %s WHERE key_id = %s AND kind = 'video'"

    async def scenario(env):
        h = await env.admin_headers()
        again = lambda: env.client.post("/admin/jobs/%s/regenerate" % old, headers=h, **op())  # noqa: E731
        out = {}
        db.run("UPDATE api_keys SET models = '{fake-image}' WHERE id = %s", k)
        out["forbidden"] = await again()
        db.run("UPDATE api_keys SET models = '{*}', enabled = false WHERE id = %s", k)
        out["disabled"] = await again()
        db.run("UPDATE api_keys SET enabled = true WHERE id = %s", k)
        db.run(day_limit, 5, k)
        out["wallet"] = await again()
        db.run(day_limit, None, k)
        db.run("UPDATE api_keys SET max_unfinished = 0 WHERE id = %s", k)
        out["ok"] = await again()
        db.run("UPDATE api_keys SET max_unfinished = 1 WHERE id = %s", k)
        out["cap"] = await again()
        return out

    out = run_app(db, scenario)
    got = {n: (r.status_code, r.json().get("error", {}).get("code")) for n, r in out.items() if n != "ok"}
    assert got == {"forbidden": (403, "auth.model_forbidden"), "disabled": (403, "auth.key_disabled"),
                   "wallet": (429, "quota.key_exceeded"), "cap": (429, "quota.too_many_queued")}, (
        "the gate: auth.model_forbidden, the key, the wallet and the cap")
    assert out["ok"].status_code == 200
    assert db.one("SELECT count(*) FROM jobs") == (2,), "only the one that passed the gate made a job"


@mutation(DOORS, PLAN_REFUSAL, NO_PLAN_REFUSAL, expect="its own code")
def test_regenerate_answers_a_plan_refusal_with_the_codes_of_a_client_request(db):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    db.run("""UPDATE jobs SET request = jsonb_set(request, '{options}', '{"refuse": "input.bad_duration"}') """
           "WHERE id = %s", old)

    async def scenario(env):
        return await env.client.post("/admin/jobs/%s/regenerate" % old, headers=await env.admin_headers(), **op())

    r = run_app(db, scenario)
    got = (r.status_code, code_of(r), r.json()["error"]["param"])
    assert got == (400, "input.bad_duration", "duration"), "the refusal must keep its own code: %s" % r.text
    assert db.one("SELECT count(*) FROM jobs") == (1,)


def test_regenerate_copies_the_input_objects_for_the_new_job_and_a_lost_input_creates_nothing(db):
    k = key(db)
    account(db)
    inputs = [store.InputMedia(0, "src/input/0.png", "image/png", 9, 4, 3)]
    old = run(db, lambda p: failed_job(p, k, "edit this", inputs=inputs))
    lost = run(db, lambda p: failed_job(p, k, "lost", inputs=[store.InputMedia(0, "lost/input/0.png", "image/png", 9)]))

    async def scenario(env):
        h = await env.admin_headers()
        await env.store.put_bytes("src/input/0.png", b"\x89PNG-input", "image/png")
        ok = await env.client.post("/admin/jobs/%s/regenerate" % old, headers=h, **op())
        before = db.one("SELECT count(*) FROM jobs")[0]
        gone = await env.client.post("/admin/jobs/%s/regenerate" % lost, headers=h, **op())
        copied = await env.store.read(media_key(ok.json()["id"], "input", 0, "image/png"))
        for key_ in ("src/input/0.png", media_key(ok.json()["id"], "input", 0, "image/png")):
            await env.store.remove(key_)
        return ok, gone, copied, before

    ok, gone, copied, before = run_app(db, scenario)
    assert ok.status_code == 200 and copied == b"\x89PNG-input"
    new_key = media_key(ok.json()["id"], "input", 0, "image/png")
    assert db.one("SELECT object_key, mime, size, width, height FROM job_media WHERE job_id = %s AND role = 'input'",
                  uuid.UUID(ok.json()["id"])) == (new_key, "image/png", 9, 4, 3)
    assert (gone.status_code, code_of(gone), gone.json()["error"]["param"]) == (404, "resource.not_found", "input")
    assert db.one("SELECT count(*) FROM jobs")[0] == before, "no job, no charge for a lost input"


def test_regenerate_of_a_missing_job_is_404(db):
    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.post("/admin/jobs/%s/regenerate" % uuid.uuid4(), headers=h, **op())

    r = run_app(db, scenario)
    assert (r.status_code, code_of(r)) == (404, "resource.not_found")
    assert db.one("SELECT count(*) FROM jobs")[0] == 0


def test_a_debug_file_the_sink_stored_is_never_an_output_a_download_or_a_shared_media_of_the_job(db):
    k = key(db)
    account(db)
    job = run(db, lambda p: failed_job(p, k))

    async def scenario(env):
        h = await env.admin_headers()
        await job_sink(env.pool, env.store)(str(job), "ui-error-x-1.png", b"\x89PNG" + b"\x00" * 32, "image/png")
        detail = await env.client.get("/admin/jobs/%s" % job, headers=h)
        content = await env.client.get("/admin/jobs/%s/content" % job, headers=h)
        return detail.json(), content.status_code

    detail, content_status = run_app(db, scenario)
    assert detail["media"] == [] and len(detail["debug_files"]) == 1, "a debug file is not an output of the job"
    assert detail["debug_files"][0]["url"] == "/admin/jobs/%s/debug/0" % job
    assert content_status == 404, "a debug file was delivered as the content of the job"
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND role <> 'debug'", job)[0] == 0
    assert db.one("SELECT count(*) FROM job_media WHERE share_token IS NOT NULL")[0] == 0


@mutation(ROUTES, "except storage.StorageError as e:", "except storage.ObjectNotFound as e:",
          expect="a store that is down became a server error")
def test_a_debug_file_the_store_cannot_read_is_not_available_never_a_server_error(db):
    k = key(db)
    account(db)
    job = run(db, lambda p: failed_job(p, k))

    async def scenario(env):
        h = await env.admin_headers()
        await job_sink(env.pool, env.store)(str(job), "ui-error-x-1.png", b"\x89PNG" + b"\x00" * 32, "image/png")

        async def down(*args, **kw):
            raise StorageError("the store is down")

        kept = env.store.read
        env.store.read = down               # the store is shared by the whole test process: put it back
        try:
            r = await env.client.get("/admin/jobs/%s/debug/0" % job, headers=h)
        except StorageError:
            return None
        finally:
            env.store.read = kept
        return r.status_code, code_of(r)

    assert run_app(db, scenario) == (404, "resource.not_found"), "a store that is down became a server error"
