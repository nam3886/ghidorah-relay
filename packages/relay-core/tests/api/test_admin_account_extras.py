"""What the accounts table shows beyond the phase 3 list (contracts §16): videos and images made per account, and the
drift flag of a counted value that disagrees with the budget minus the day's unrefunded charges."""

import pytest
from accountkit import done_job, new_account
from apikit import run_app
from jobkit import key

from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_accounts.py"
COMMON = "packages/relay-core/src/relay_core/admin/routes_common.py"       # the per-row pool rule lives here
DRIFT_ON = "if counted else None"
DRIFT_OFF = "if False else None"
COMPOSER = "a.provider_state->>'composer'"
NO_COMPOSER = "NULL::text"
MADE = 'by_id[str(account_id)]["made"][media_type] = count'
MADE_ZERO = 'by_id[str(account_id)]["made"][media_type] = 0'
TODAY = "(clock_timestamp() AT TIME ZONE 'UTC')::date"


async def listed(env):
    r = await env.client.get("/admin/accounts?provider=fake", headers=await env.admin_headers())
    return {i["label"]: i for i in r.json()["items"]}


@mutation(ROUTES, MADE, MADE_ZERO, expect="made")
def test_each_account_shows_how_many_videos_and_images_it_made(db):
    k = key(db)
    busy, idle = new_account(db, label="busy", checked=True), new_account(db, label="idle", checked=True)
    for i, kind in enumerate(("video", "video", "image")):
        job = done_job(db, k, busy, ref="m%d" % i)
        db.run("UPDATE jobs SET media_type = %s WHERE id = %s", kind, job)
    done_job(db, k, busy, ref="failed", status="failed")
    db.run("UPDATE jobs SET error_code = 'render.deadline' WHERE status = 'failed'")

    async def scenario(env):
        return await listed(env)

    rows = run_app(db, scenario)
    assert rows["busy"]["made"] == {"video": 2, "image": 1}, "made"
    assert rows["idle"]["made"] == {"video": 0, "image": 0} and idle


@mutation(COMMON, DRIFT_ON, DRIFT_OFF, expect="drift")
@pytest.mark.fabricated_rows("plants account charges that are not the job account_cost", "account_cost")
def test_a_counted_value_that_disagrees_with_the_charges_is_flagged_and_a_reported_one_never_is(db):
    k = key(db)
    names = ("exact", "off", "reported", "refunded")
    exact, off, reported, refunded = (new_account(db, label=n, checked=True) for n in names)
    for who, left, source in ((exact, 3, "counted"), (off, 2, "counted"), (reported, 1, "reported"),
                              (refunded, 4, "reset")):
        db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) VALUES "
               "(%s, 'video', %s, %s, " + TODAY + ")", who, left, source)
    for i, who in enumerate((exact, off, reported)):          # fake.video_budget = 4, one point spent today
        job = done_job(db, k, who, ref="c%d" % i)
        db.run("UPDATE jobs SET taken_at = clock_timestamp() WHERE id = %s", job)
        db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) VALUES "
               "(%s, %s, 'video', 1, " + TODAY + ")", who, job)
    job = done_job(db, k, refunded, ref="c-refunded")      # a refunded charge is not spent
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day, refunded_at) VALUES "
           "(%s, %s, 'video', 1, " + TODAY + ", clock_timestamp())", refunded, job)

    async def scenario(env):
        return await listed(env)

    rows = run_app(db, scenario)
    cap = {name: rows[name]["capacity"][0] for name in rows}
    assert cap["exact"]["drift"] is False and cap["exact"]["budget"] == 4
    assert cap["off"]["drift"] is True, "drift: 2 left but the charges say 3"
    assert cap["reported"]["drift"] is None, "the upstream's own number is never called drift"
    assert cap["refunded"]["drift"] is False, "a refunded charge does not count as spent"


@mutation(ROUTES, COMPOSER, NO_COMPOSER, expect="composer")
def test_the_account_list_shows_only_the_composer_of_the_provider_state(db):
    with_composer = new_account(db, label="with", checked=True)
    new_account(db, label="without", checked=True)
    db.run("UPDATE accounts SET provider_state = %s::jsonb WHERE id = %s",
           '{"composer": "agent", "secret_cursor": "do not show"}', with_composer)

    async def scenario(env):
        headers = await env.admin_headers()
        return await env.client.get("/admin/accounts?provider=fake", headers=headers), await listed(env)

    raw, rows = run_app(db, scenario)
    assert rows["with"]["composer"] == "agent", "composer"
    assert rows["without"]["composer"] is None
    assert "provider_state" not in raw.text and "do not show" not in raw.text
