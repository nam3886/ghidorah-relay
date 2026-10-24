"""Admin bulk Re-check and Regenerate, webhook resend and link revoke."""

import uuid

from accountkit import done_job
from apikit import run_app
from jobkit import account, create, key, run
from test_admin_jobs import failed_job, waiting_job

from relay_core import storage
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_jobs.py"
BULK = "result = await jobs.recheck_bulk(pool, dict.fromkeys(body.job_ids))"
BULK_NOTHING = "result = await jobs.recheck_bulk(pool, [])"
DEDUPE = "for job_id in dict.fromkeys(body.job_ids):"
NO_DEDUPE = "for job_id in body.job_ids * 2:"
RESEND = 'raise ApiError("resource.not_found", "webhook")'
RESEND_ALWAYS = "pass"
REVOKE = 'return {"id": str(job_id), "revoked": await PgShareTable(pool).clear_job(str(job_id))}'
REVOKE_NOTHING = 'return {"id": str(job_id), "revoked": 0}'


async def post(env, path: str, body=None):
    return await env.client.post("/admin" + path, headers=await env.admin_headers(), json=body)


@mutation(ROUTES, BULK, BULK_NOTHING, expect="rechecked")
def test_bulk_recheck_revives_what_it_can_and_names_why_it_left_the_others(db):
    k = key(db)
    account(db)

    async def plant(p):
        revivable = await failed_job(p, k, "a")
        flagged = await failed_job(p, k, "b")
        waiting = await waiting_job(p, k, "c")
        queued = (await create(p, k, "d")).job_id
        return revivable, flagged, waiting, queued

    revivable, flagged, waiting, queued = run(db, plant)
    done = done_job(db, k)
    db.run("UPDATE jobs SET provider_state = '{\"no_recheck\": true}' WHERE id = %s", flagged)

    async def scenario(env):
        everything = [revivable, flagged, waiting, queued, done, uuid.uuid4()]
        return (await post(env, "/jobs/bulk-recheck", {"job_ids": [str(j) for j in everything]}),
                await post(env, "/jobs/bulk-recheck", {"job_ids": []}),
                await post(env, "/jobs/bulk-recheck", {"job_ids": [str(uuid.uuid4())] * 101}),
                await post(env, "/jobs/bulk-recheck", {"job_ids": [str(done)], "extra": 1}),
                await post(env, "/jobs/bulk-recheck", {"job_ids": ["nonsense"]}))

    r, empty, huge, extra, junk = run_app(db, scenario)
    body = r.json()
    assert sorted(body["rechecked"]) == sorted([str(revivable), str(waiting)]), "rechecked"
    assert body["skipped"][str(flagged)].startswith("the provider flagged"), "a flagged job is skipped"
    assert body["skipped"][str(queued)] == "job.not_submitted" and body["skipped"][str(done)] == "job.already_finished"
    assert len(body["skipped"]) == 4
    assert db.one("SELECT status FROM jobs WHERE id = %s", flagged) == ("failed",)
    for refused in (empty, huge, extra, junk):
        assert refused.status_code in (400, 422), refused.text


@mutation(ROUTES, DEDUPE, NO_DEDUPE, expect="once")
def test_a_job_named_twice_is_acted_on_once(db):
    k = key(db)
    account(db)
    job = run(db, lambda p: failed_job(p, k, "a"))

    async def scenario(env):
        body = {"job_ids": [str(job), str(job)], "operation_key": uuid.uuid4().hex}
        return await post(env, "/jobs/bulk-regenerate", body)

    r = run_app(db, scenario)
    assert len(r.json()["created"]) == 1, "each job is regenerated once"
    assert db.one("SELECT count(*) FROM jobs")[0] == 2


def test_bulk_regenerate_makes_new_jobs_and_lists_the_ones_it_could_not(db):
    k = key(db)
    account(db)
    first = run(db, lambda p: failed_job(p, k, "one"))
    second = run(db, lambda p: failed_job(p, k, "two"))
    old = run(db, lambda p: failed_job(p, k, "three"))
    db.run("UPDATE jobs SET request = NULL WHERE id = %s", old)
    ghost = uuid.uuid4()

    async def scenario(env):
        return await post(env, "/jobs/bulk-regenerate", {"job_ids": [str(j) for j in (first, old, second, ghost)],
                                                     "operation_key": uuid.uuid4().hex})

    body = run_app(db, scenario).json()
    assert [c["from"] for c in body["created"]] == [str(first), str(second)]
    assert body["failed"] == {str(old): "input.invalid", str(ghost): "resource.not_found"}
    made = {c["id"] for c in body["created"]}
    assert {str(r[0]) for r in db.all("SELECT id FROM jobs WHERE status = 'queued'")} == made
    charges = db.one("SELECT count(*) FROM ledger WHERE entry = 'charge' AND scope = '*' AND job_id = ANY(%s)",
                     [uuid.UUID(i) for i in made])
    assert charges == (2,)


@mutation(ROUTES, RESEND, RESEND_ALWAYS, expect="no failed webhook")
def test_resend_restarts_only_a_webhook_that_failed(db):
    k = key(db)
    account(db)
    failed = run(db, lambda p: failed_job(p, k, "a"))
    plain = run(db, lambda p: failed_job(p, k, "b"))
    db.run("UPDATE jobs SET callback_url = 'https://hooks.example/test', callback_status = 'failed', "
           "callback_attempts = 3, callback_for = clock_timestamp() WHERE id = %s", failed)

    async def scenario(env):
        resend = lambda j: post(env, "/jobs/%s/resend-webhook" % j)  # noqa: E731
        return await resend(failed), await resend(plain), await resend(uuid.uuid4())

    ok, none, ghost = run_app(db, scenario)
    assert ok.json() == {"id": str(failed), "resent": True}
    assert db.one("SELECT callback_status, callback_attempts FROM jobs WHERE id = %s", failed) == ("pending", 0)
    assert (none.status_code, ghost.status_code) == (404, 404), "no failed webhook"
    assert none.json()["error"]["param"] == "webhook"


@mutation(ROUTES, REVOKE, REVOKE_NOTHING, expect="revoked")
def test_revoke_makes_every_share_link_of_the_job_unknown_and_keeps_the_files(db):
    k = key(db)
    job = done_job(db, k)
    other = done_job(db, k, ref="other")
    token, kept = storage.new_share_token(), storage.new_share_token()
    db.run("UPDATE job_media SET share_token = %s WHERE job_id = %s", token, job)
    db.run("UPDATE job_media SET share_token = %s WHERE job_id = %s", kept, other)

    async def scenario(env):
        revoked = await post(env, "/jobs/%s/revoke-links" % job)
        again = await post(env, "/jobs/%s/revoke-links" % job)
        return revoked, again, await env.client.get("/m/" + token), await post(
            env, "/jobs/%s/revoke-links" % uuid.uuid4())

    revoked, again, after, ghost = run_app(db, scenario)
    assert revoked.json()["revoked"] == 1, "revoked"
    assert again.json()["revoked"] == 0 and after.status_code == 404 and ghost.status_code == 404
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s", job) == (1,)
    assert db.one("SELECT share_token FROM job_media WHERE job_id = %s", other) == (kept,), "other jobs keep theirs"


LIST_COUNTS = "(SELECT count(*) FROM job_media m WHERE m.job_id = j.id AND m.role = 'output')"
LIST_NO_COUNTS = "(SELECT 0)"


@mutation(ROUTES, LIST_COUNTS, LIST_NO_COUNTS, expect="result count")
def test_the_list_shows_result_and_reference_counts_the_net_charge_and_a_model_substitution(db):
    k = key(db)
    account(db)
    plain = done_job(db, k, ref="plain")
    swapped = done_job(db, k, ref="swapped")
    db.run("UPDATE jobs SET warnings = '[{\"code\": \"fake.model_substituted\", \"text\": \"t\"}]' WHERE id = %s",
           swapped)
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) VALUES (%s, 'input', 0, 'k/input/0.png', "
           "'image/png', 1)", plain)
    refunded = run(db, lambda p: failed_job(p, k, "gone"))

    async def scenario(env):
        r = await env.client.get("/admin/jobs", headers=await env.admin_headers())
        return {i["id"]: i for i in r.json()["items"]}

    items = run_app(db, scenario)
    assert (items[str(plain)]["outputs"], items[str(plain)]["inputs"]) == (1, 1), "result count"
    assert items[str(plain)]["model_substituted"] is False and items[str(swapped)]["model_substituted"] is True
    assert items[str(refunded)]["net_charged"] == 0 and items[str(refunded)]["price"] == 10
