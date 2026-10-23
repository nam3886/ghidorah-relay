"""The admin request-log viewer: filters, half-open dates, search without pattern characters,
pagination, the detail, and Clear removing only the log rows."""

import json

from accountkit import done_job
from apikit import run_app
from jobkit import key

from relay_core.admin import routes_logs
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_logs.py"
READS = "AND l.route !~ '^(GET|HEAD|OPTIONS) ' "
READS_COUNT = "AND l.route !~ '^(NOPE) ' "
YEAR_9999 = "MAX_MS = 253402300799999"
YEAR_BEYOND = "MAX_MS = 2**53"
UNTIL = "l.at < %(until)s"
UNTIL_CLOSED = "l.at <= %(until)s"
PRE_JOB = "WHEN 'pre_job' THEN l.job_id IS NULL AND l.status_code >= 400"
ANY_ERROR = "WHEN 'pre_job' THEN l.status_code >= 400"
ID_PREFIX = "starts_with(l.job_id::text, %(q)s)"
ID_ANYWHERE = "strpos(l.job_id::text, %(q)s) > 0"
CLEAR = 'await conn.execute("DELETE FROM request_logs")'
CLEAR_NOTHING = 'await conn.execute("DELETE FROM request_logs WHERE FALSE")'
NEWEST = "ORDER BY l.at DESC, l.id DESC LIMIT"
OLDEST = "ORDER BY l.at ASC, l.id DESC LIMIT"

T0 = "2026-01-10 12:00:00+00"


def log(db, k, *, status=200, job=None, provider="fake", route="/v1/video/generations", at=T0, error=None,
        body="{}") -> int:
    return db.one("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, error_code, "
                  "body_redacted, at) VALUES (%s, %s, %s, %s, %s, 7, %s, %s::jsonb, %s) RETURNING id",
                  k, provider, route, status, job, error, body, at)[0]


def ms_of(text: str) -> int:
    import datetime
    return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)


async def get(env, query: str = ""):
    return await env.client.get("/admin/logs" + query, headers=await env.admin_headers())


def ids(r) -> list[int]:
    return [int(i["id"]) for i in r.json()["items"]]


@mutation(ROUTES, NEWEST, OLDEST, expect="newest first")
def test_the_list_is_newest_first_with_a_total_and_pages_without_a_gap(db):
    k = key(db)
    made = [log(db, k, at="2026-01-10 12:00:0%d+00" % i) for i in range(5)]

    async def scenario(env):
        return await get(env), await get(env, "?limit=2"), await get(env, "?limit=2&offset=2"), \
            await get(env, "?limit=2&offset=4")

    everything, first, second, last = run_app(db, scenario)
    assert ids(everything) == made[::-1], "newest first"
    assert everything.json()["total"] == 5 and first.json()["total"] == 5
    assert ids(first) + ids(second) + ids(last) == made[::-1] and len(ids(last)) == 1


@mutation(ROUTES, PRE_JOB, ANY_ERROR, expect="pre-job filter")
def test_provider_and_status_filters_including_failures_that_made_no_job(db):
    k = key(db)
    job = done_job(db, k)
    ok = log(db, k, job=job)
    pre = log(db, k, status=422, error="input.invalid")
    failed_with_job = log(db, k, status=502, job=job, error="render.deadline")
    other = log(db, k, provider="other", status=403, error="auth.key_disabled")

    async def scenario(env):
        return [await get(env, q) for q in ("?status=ok", "?status=error", "?status=pre_job", "?provider=other",
                                            "?provider=fake&status=pre_job", "?status=nonsense")]

    ok_r, err_r, pre_r, other_r, both_r, bad_r = run_app(db, scenario)
    assert ids(ok_r) == [ok] and sorted(ids(err_r)) == sorted([pre, failed_with_job, other])
    assert sorted(ids(pre_r)) == sorted([pre, other]), "pre-job filter: a failure that made a job is not one"
    assert ids(other_r) == [other] and ids(both_r) == [pre]
    assert bad_r.status_code == 422 or bad_r.status_code == 400
    row = pre_r.json()["items"][0]
    assert row["job_id"] is None and row["error_code"] in ("input.invalid", "auth.key_disabled")


@mutation(ROUTES, UNTIL, UNTIL_CLOSED, expect="half-open")
def test_dates_are_half_open_since_included_until_excluded(db):
    k = key(db)
    midnight = log(db, k, at="2026-01-10 00:00:00+00")
    inside = log(db, k, at="2026-01-10 23:59:59+00")
    next_midnight = log(db, k, at="2026-01-11 00:00:00+00")
    before = log(db, k, at="2026-01-09 23:59:59+00")
    since, until = ms_of("2026-01-10T00:00:00+00:00"), ms_of("2026-01-11T00:00:00+00:00")

    async def scenario(env):
        return (await get(env, "?since_ms=%d&until_ms=%d" % (since, until)), await get(env, "?since_ms=-1"),
                await get(env, "?until_ms=99999999999999999999"))

    window, negative, huge = run_app(db, scenario)
    assert sorted(ids(window)) == sorted([midnight, inside]), "half-open: since included, until excluded"
    assert next_midnight not in ids(window) and before not in ids(window)
    assert negative.status_code == 400 and huge.status_code in (400, 422)


@mutation(ROUTES, ID_PREFIX, ID_ANYWHERE, expect="prefix")
@mutation(ROUTES, "OR strpos(lower(coalesce(\" + SHOWN_TITLE + \", '')), lower(%(q)s)) > 0)",
          "OR strpos(lower(coalesce(j.title, '')), lower(%(q)s)) > 0)", expect="found by its prompt")
def test_search_matches_a_job_id_prefix_an_external_ref_prefix_or_part_of_a_title_and_pattern_characters_are_plain(db):
    k = key(db)
    plain = done_job(db, k, ref="ref-alpha-1")
    percent = done_job(db, k, ref="ref-beta-2")
    db.run("UPDATE jobs SET title = 'A Quiet Lake' WHERE id = %s", plain)
    db.run("UPDATE jobs SET title = %s WHERE id = %s", "100% sure_thing", percent)
    on_plain, on_percent = log(db, k, job=plain), log(db, k, job=percent)
    no_job = log(db, k, status=422)
    on_untitled = log(db, k, job=done_job(db, k, ref="ref-gamma-3", prompt="A paper boat drifting on a calm pond"))

    async def scenario(env):
        search = lambda q: get(env, "?q=" + q)  # noqa: E731
        return {
            "id": await search(str(plain)[:8]), "id_inside": await search(str(plain)[2:10]),
            "ref": await search("ref-beta"), "ref_inside": await search("beta-2"),
            "title": await search("quiet%20lake"),
            "percent": await search("%25"), "underscore": await search("_"), "lone_wildcards": await search("%25%25"),
            "blank": await search("%20"), "prompt_words": await search("PAPER%20BOAT"),
        }

    got = run_app(db, scenario)
    assert ids(got["id"]) == [on_plain], "prefix"
    assert ids(got["id_inside"]) == [], "a job id matches by prefix only"
    assert ids(got["ref"]) == [on_percent] and ids(got["ref_inside"]) == []
    assert ids(got["title"]) == [on_plain], "a title matches by part, any case"
    assert ids(got["percent"]) == [on_percent], "a % is a character, not a wildcard"
    assert ids(got["underscore"]) == [on_percent] and ids(got["lone_wildcards"]) == []
    assert sorted(ids(got["blank"])) == sorted([on_plain, on_percent, no_job, on_untitled]), (
        "a blank search is no search")
    assert [(int(i["id"]), i["job_title"]) for i in got["prompt_words"].json()["items"]] == [
        (on_untitled, "A paper boat drifting on a calm pond")], "an untitled job is named and found by its prompt"


def test_limits_and_offsets_out_of_range_are_refused(db):
    async def scenario(env):
        return [await get(env, q) for q in ("?limit=0", "?limit=201", "?offset=-1", "?offset=99999999999",
                                            "?q=" + "x" * 201)]

    for r in run_app(db, scenario):
        assert r.status_code == 400 and r.json()["error"]["code"] == "input.invalid"


def test_the_detail_shows_the_redacted_request_and_unknown_rows_are_404(db):
    k = key(db)
    row = log(db, k, status=422, error="input.invalid", body='{"prompt": "a cat", "api_key": "[redacted]"}')

    async def scenario(env):
        h = await env.admin_headers()
        return (await env.client.get("/admin/logs/%d" % row, headers=h),
                await env.client.get("/admin/logs/999999999", headers=h),
                await env.client.get("/admin/logs/%d" % (2**63), headers=h))

    found, ghost, huge = run_app(db, scenario)
    body = found.json()
    assert body["request"] == {"prompt": "a cat", "api_key": "[redacted]"} and body["status_code"] == 422
    assert body["key_id"] == str(k) and body["response"] is None
    assert ghost.status_code == 404 and huge.status_code == 404


LIST_PREFIX = ('k.key_prefix, l.provider, l.route, l.status_code, l.job_id, "\n            + SHOWN_TITLE + '
               '", l.duration_ms, l.error_code " + _FROM')
LIST_NO_PREFIX = ('NULL, l.provider, l.route, l.status_code, l.job_id, "\n            + SHOWN_TITLE + '
                  '", l.duration_ms, l.error_code " + _FROM')
DETAIL_PREFIX = ('k.key_prefix, l.provider, l.route, l.status_code, l.job_id, "\n            + SHOWN_TITLE + '
                 '", l.duration_ms, l.error_code, l.body')
DETAIL_NO_PREFIX = ('NULL, l.provider, l.route, l.status_code, l.job_id, "\n            + SHOWN_TITLE + '
                    '", l.duration_ms, l.error_code, l.body')


@mutation(ROUTES, LIST_PREFIX, LIST_NO_PREFIX, expect="the list names")
@mutation(ROUTES, DETAIL_PREFIX, DETAIL_NO_PREFIX, expect="the detail names")
def test_a_row_carries_its_key_prefix_beside_the_label_and_null_without_a_key(db):
    named, bare = key(db), key(db)
    db.run("UPDATE api_keys SET label = 'Golden key' WHERE id = %s", named)
    prefix = {k: db.one("SELECT key_prefix FROM api_keys WHERE id = %s", k)[0] for k in (named, bare)}
    rows = {log(db, named): prefix[named], log(db, bare): prefix[bare], log(db, None, status=404): None}

    async def scenario(env):
        h = await env.admin_headers()
        return await get(env), [await env.client.get("/admin/logs/%d" % row, headers=h) for row in rows]

    listed, details = run_app(db, scenario)
    check = Soft()
    by_id = {int(i["id"]): i for i in listed.json()["items"]}
    check(all(p for p in prefix.values()), "every test key has a prefix")
    check({row: by_id[row]["key_prefix"] for row in rows} == rows,
          "the list names each row's key by its prefix too, and a row without a key has none")
    check({int(d.json()["id"]): d.json()["key_prefix"] for d in details} == rows,
          "the detail names its key by its prefix too, and a row without a key has none")
    check.done()


@mutation(ROUTES, CLEAR, CLEAR_NOTHING, expect="cleared")
def test_clear_removes_only_the_log_rows_and_keeps_jobs_the_ledger_and_the_audit(db):
    k = key(db)
    job = done_job(db, k)
    log(db, k, job=job)
    log(db, k, status=422)
    before = {t: db.one("SELECT count(*) FROM " + t)[0] for t in ("jobs", "billing_alerts", "ledger", "api_keys")}

    async def scenario(env):
        h = await env.admin_headers()
        cleared = await env.client.delete("/admin/logs", headers=h)
        return cleared, await get(env), await env.client.delete("/admin/logs", headers=h)

    cleared, after, again = run_app(db, scenario)
    assert cleared.json() == {"deleted": 2} and after.json() == {"items": [], "total": 0}, "cleared"
    assert again.json() == {"deleted": 0}
    assert {t: db.one("SELECT count(*) FROM " + t)[0] for t in before} == before
    assert db.one("SELECT count(*) FROM admin_sessions")[0] >= 1, "the admin session survives a clear"


def test_the_logs_need_an_admin_session(db):
    async def scenario(env):
        return [await env.client.get("/admin/logs"), await env.client.delete("/admin/logs"),
                await env.client.get("/admin/logs/5")]

    for r in run_app(db, scenario):
        assert r.status_code == 401


@mutation(ROUTES, READS, READS_COUNT, expect="a failed read is not a pre-job failure")
def test_the_pre_job_filter_leaves_out_failed_reads(db):
    k = key(db)
    post = log(db, k, status=422, route="POST /v1/video/generations")
    log(db, k, status=404, route="GET /v1/videos/{job_id}")
    log(db, k, status=403, route="HEAD /v1/videos/{job_id}")

    async def scenario(env):
        return await get(env, "?status=pre_job"), await get(env, "?status=error")

    pre, error = run_app(db, scenario)
    assert ids(pre) == [post], "a failed read is not a pre-job failure"
    assert len(ids(error)) == 3, "the error filter still shows every failed request"


@mutation(ROUTES, YEAR_9999, YEAR_BEYOND, expect="beyond year 9999")
def test_a_moment_beyond_the_year_9999_is_refused_not_a_500(db):
    async def scenario(env):
        return [await get(env, "?%s=%d" % (name, value)) for name in ("since_ms", "until_ms")
                for value in (253402300800000, 2**53)]

    for r in run_app(db, scenario):
        assert (r.status_code, r.json()["error"]["code"]) == (400, "input.invalid"), "beyond year 9999: %s" % r.text


# ---- per-day totals (`tz`) ---------------------------------------------------------------------------------------
TZ = "Asia/Ho_Chi_Minh"                  # Saigon: UTC+7, no clock change (the canonical name of Asia/Saigon)
DAY_TOTALS_FROM = "_DAY_TOTALS + _FROM + _DAY_RANGE"
DAY_TOTALS_UNFILTERED = '_DAY_TOTALS + "FROM request_logs l WHERE TRUE" + _DAY_RANGE'


class Soft:
    """Every failed check of a test is named in its one failure, so each marker's `expect=` finds its own."""

    def __init__(self):
        self.failed = []

    def __call__(self, ok: bool, what: str) -> None:
        if not ok:
            self.failed.append(what)

    def done(self) -> None:
        assert not self.failed, "; ".join(self.failed)


@mutation(ROUTES, DAY_TOTALS_FROM, DAY_TOTALS_UNFILTERED, expect="totals follow the filters")
@mutation(ROUTES, "(l.at AT TIME ZONE %(tz)s)::date", "(l.at AT TIME ZONE 'UTC')::date", expect="the local day")
@mutation(ROUTES, ">= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s",
          ">= (%(day_first)s::date)::timestamp AT TIME ZONE 'UTC'", expect="the start of the earliest day")
@mutation(ROUTES, "((%(day_last)s::date + 1)::timestamp", "((%(day_last)s::date + 0)::timestamp",
          expect="the end of the latest day")
@mutation(ROUTES, "if zone is not None and rows:", "if zone is not None:", expect="an empty page")
def test_day_totals_count_every_row_of_a_local_day_under_the_same_filters(db):
    k = key(db)
    # Saigon: 23:30 and 00:30 share a UTC day and sit on two local days
    at = ["2026-03-08 22:00:00+00", "2026-03-09 18:00:00+00", "2026-03-10 05:00:00+00", "2026-03-10 16:30:00+00",
          "2026-03-10 17:30:00+00", "2026-03-11 01:00:00+00"]
    titled = done_job(db, k)
    db.run("UPDATE jobs SET title = 'A Quiet Lake' WHERE id = %s", titled)
    for i, stamp in enumerate(at):
        log(db, k, at=stamp, status=500 if i in (1, 5) else 200, provider="other" if i == 5 else "fake",
            job=titled if i == 2 else None)

    async def scenario(env):
        def day(text):
            return ms_of(text + "T00:00:00+07:00")
        window = "&since_ms=%d&until_ms=%d" % (day("2026-03-10"), day("2026-03-11"))
        return [await get(env, "?tz=%s%s" % (TZ, q)) for q in (
            "&limit=2", "&limit=2&offset=2", "&limit=4", "&limit=50", "&limit=2&offset=9", "&limit=50&status=error",
            "&limit=50&provider=other", "&limit=1" + window, "&limit=50&q=quiet", "&limit=50&status=ok",
            "&limit=50&status=pre_job", "&limit=50&provider=fake", "&limit=50&provider=nobody",
            "&limit=50&q=nomatch", "&limit=1&since_ms=%d&until_ms=%d" % (day("2026-03-12"), day("2026-03-13")))] + [
            await get(env, "?tz=UTC&limit=50")]

    (newest, middle, four, everything, past_end, error, other, window, quiet, ok, pre_job, fake, nobody, nomatch,
     later, utc) = run_app(db, scenario)
    check = Soft()
    check(ok.json()["day_totals"] == {"2026-03-09": 1, "2026-03-10": 2, "2026-03-11": 1},
          "totals follow the filters: status ok leaves rows out")
    check(pre_job.json()["day_totals"] == {"2026-03-10": 1, "2026-03-11": 1},
          "totals follow the filters: pre_job, a request that made no job")
    check(fake.json()["day_totals"] == {"2026-03-09": 1, "2026-03-10": 3, "2026-03-11": 1},
          "totals follow the filters: provider leaves rows out")
    for r, what in ((nobody, "provider"), (nomatch, "search"), (later, "window")):
        check((r.json().get("total"), r.json().get("day_totals")) == (0, {}),
              "totals follow the filters: none for " + what)
    check(newest.json()["day_totals"] == {"2026-03-11": 2}, "the local day: newest page")
    check(middle.json()["day_totals"] == {"2026-03-10": 3}, "a day counted beyond the page")
    check(four.json()["day_totals"] == {"2026-03-10": 3, "2026-03-11": 2}, "the end of the latest day")
    check(everything.json()["day_totals"] == {"2026-03-09": 1, "2026-03-10": 3, "2026-03-11": 2},
          "the start of the earliest day")
    check(list(everything.json()["day_totals"]) == sorted(everything.json()["day_totals"]), "days in order")
    check(past_end.json().get("day_totals") == {}, "an empty page")
    check(error.json()["day_totals"] == {"2026-03-10": 1, "2026-03-11": 1}, "totals follow the filters: status")
    check(other.json()["day_totals"] == {"2026-03-11": 1}, "totals follow the filters: provider")
    check(window.json()["total"] == 3 and window.json()["day_totals"] == {"2026-03-10": 3},
          "totals follow the filters: window")
    check(quiet.json()["day_totals"] == {"2026-03-10": 1}, "totals follow the filters: search")
    check(utc.json()["day_totals"] == {"2026-03-08": 1, "2026-03-09": 1, "2026-03-10": 3, "2026-03-11": 1},
          "the local day: UTC")
    check.done()


@mutation(ROUTES, '    if zone is not None:\n        out["day_totals"] = day_totals',
          '    out["day_totals"] = day_totals', expect="no tz, no field")
@mutation(ROUTES, "zone = None if tz is None else _zone(tz)",
          'zone = None if tz is None else __import__("zoneinfo").ZoneInfo(tz)', expect="a bad tz")
def test_logs_without_tz_have_no_day_totals_and_a_bad_tz_is_refused(db):
    k = key(db)
    db.run("UPDATE api_keys SET label = 'Golden key' WHERE id = %s", k)
    row = log(db, k)
    prefix = db.one("SELECT key_prefix FROM api_keys WHERE id = %s", k)[0]
    # the answer a client without `tz` has always got, byte for byte (key order and compact separators included)
    golden = {"items": [{
        "id": str(row), "key_id": str(k), "key_label": "Golden key", "key_prefix": prefix, "provider": "fake",
        "route": "/v1/video/generations", "status_code": 200, "job_id": None, "job_title": None,
        "duration_ms": 7, "error_code": None, "at_ms": ms_of(T0.replace(" ", "T"))}], "total": 1}

    async def scenario(env):
        refused = ("Mars/Olympus", "", "..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd")
        return [await get(env)] + [await get(env, "?tz=" + bad) for bad in refused]

    plain, *bad = run_app(db, scenario)
    check = Soft()
    check(plain.status_code == 200 and plain.content == json.dumps(golden, separators=(",", ":")).encode(),
          "no tz, no field: the old answer, byte for byte")
    for r in bad:
        check((r.status_code, r.json()["error"]["code"], r.json()["error"]["param"]) == (
            400, "input.invalid", "tz"), "a bad tz")
    check.done()


@mutation("packages/relay-core/src/relay_core/admin/routes_jobs.py", "        return ZoneInfo(name)",
          '        return ZoneInfo(name.replace("Saigon", "Nowhere"))', expect="the alias answers")
def test_the_browser_zone_asia_saigon_gives_the_same_log_day_totals_as_asia_ho_chi_minh(db):
    """Chrome in Vietnam reports `Asia/Saigon`: it must answer and group by the same local days."""
    k = key(db)
    for stamp in ("2026-03-10 16:30:00+00", "2026-03-10 17:30:00+00", "2026-03-11 01:00:00+00"):
        log(db, k, at=stamp)

    async def scenario(env):
        return [await get(env, "?tz=" + zone) for zone in ("Asia/Saigon", TZ)]

    alias, canonical = run_app(db, scenario)
    check = Soft()
    check(alias.status_code == 200 and "day_totals" in alias.json(), "the alias answers")
    check(alias.json().get("day_totals") == {"2026-03-10": 1, "2026-03-11": 2}, "the alias gives the same days")
    check(alias.json().get("day_totals") == canonical.json()["day_totals"], "the alias gives the same days")
    check.done()


@mutation("packages/relay-core/src/relay_core/admin/routes_jobs.py", "    except psycopg.errors.InvalidParameterValue:",
          "    except psycopg.errors.UndefinedTable:", expect="a zone Postgres does not know")
def test_a_zone_python_knows_and_postgres_does_not_is_a_bad_tz_for_the_logs_too(db, monkeypatch):
    """The same translation on the logs route: Python accepts a made-up name, Postgres refuses it: 400, never 500."""
    from zoneinfo import ZoneInfo

    log(db, key(db))
    monkeypatch.setattr(routes_logs, "_zone", lambda name: ZoneInfo(TZ))

    async def scenario(env):
        return [await get(env, "?tz=" + zone) for zone in ("Mars/Olympus", TZ)]

    unknown, known = run_app(db, scenario)
    check = Soft()
    check((unknown.status_code, unknown.json()["error"]["code"]) == (400, "input.invalid"),
          "a zone Postgres does not know")
    check(unknown.json()["error"].get("param") == "tz", "a zone Postgres does not know: param")
    check(known.status_code == 200 and known.json()["day_totals"] == {"2026-01-10": 1}, "a known zone still answers")
    check.done()


@mutation(ROUTES, "AND l.at >= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s",
          "AND l.at + interval '0' >= (%(day_first)s::date)::timestamp AT TIME ZONE %(tz)s",
          expect="the day totals are served by request_logs_at")
def test_the_log_day_totals_query_is_served_by_the_at_index_on_a_realistic_table(db):
    """10 000 rows over 30 days, ANALYZEd, nothing forced: one day's totals range-scan request_logs_at."""
    import psycopg

    from relay_core.admin import routes_logs

    k = key(db)
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms, body_redacted, at) "
           "SELECT %s, 'fake', '/v1/x', 200, 7, '{}'::jsonb, timestamptz '2026-03-30 00:00+00' - g * interval '259 s' "
           "FROM generate_series(1, 10000) g", k)
    db.run("ANALYZE request_logs")
    args = {"provider": None, "status": "all", "since": None, "until": None, "q": None, "tz": TZ,
            "day_first": "2026-03-10", "day_last": "2026-03-10"}
    with db.connect() as conn:
        plan = "\n".join(r[0] for r in psycopg.ClientCursor(conn).execute(
            "EXPLAIN " + routes_logs._DAY_TOTALS + routes_logs._FROM + routes_logs._DAY_RANGE, args))
    print("\nLOG DAY TOTALS PLAN\n" + plan)
    cond = [line for line in plan.splitlines() if "Index Cond" in line and "(at >=" in line and "(at <" in line]
    assert "request_logs_at" in plan and cond and "Seq Scan on request_logs" not in plan, (
        "the day totals are served by request_logs_at:\n" + plan)
