"""The admin request-log viewer (contracts §4.4, §16): filters, half-open dates, search without pattern characters,
pagination, the detail, and Clear removing only the log rows."""

from accountkit import done_job
from apikit import run_app
from jobkit import key

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
def test_search_matches_a_job_id_prefix_an_external_ref_prefix_or_part_of_a_title_and_pattern_characters_are_plain(db):
    k = key(db)
    plain = done_job(db, k, ref="ref-alpha-1")
    percent = done_job(db, k, ref="ref-beta-2")
    db.run("UPDATE jobs SET title = 'A Quiet Lake' WHERE id = %s", plain)
    db.run("UPDATE jobs SET title = %s WHERE id = %s", "100% sure_thing", percent)
    on_plain, on_percent = log(db, k, job=plain), log(db, k, job=percent)
    no_job = log(db, k, status=422)

    async def scenario(env):
        search = lambda q: get(env, "?q=" + q)  # noqa: E731
        return {
            "id": await search(str(plain)[:8]), "id_inside": await search(str(plain)[2:10]),
            "ref": await search("ref-beta"), "ref_inside": await search("beta-2"),
            "title": await search("quiet%20lake"),
            "percent": await search("%25"), "underscore": await search("_"), "lone_wildcards": await search("%25%25"),
            "blank": await search("%20"),
        }

    got = run_app(db, scenario)
    assert ids(got["id"]) == [on_plain], "prefix"
    assert ids(got["id_inside"]) == [], "a job id matches by prefix only"
    assert ids(got["ref"]) == [on_percent] and ids(got["ref_inside"]) == []
    assert ids(got["title"]) == [on_plain], "a title matches by part, any case"
    assert ids(got["percent"]) == [on_percent], "a % is a character, not a wildcard"
    assert ids(got["underscore"]) == [on_percent] and ids(got["lone_wildcards"]) == []
    assert sorted(ids(got["blank"])) == sorted([on_plain, on_percent, no_job]), "a blank search is no search"


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
