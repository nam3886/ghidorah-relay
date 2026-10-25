"""What the accounts table shows beyond the basic list: videos and images made per account, and each pool's windows
with the budget of the account's tier and what its unrefunded charges spent in the window."""

import pytest
from accountkit import done_job, new_account
from apikit import run_app
from jobkit import key

from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_accounts.py"
POOL = "packages/relay-core/src/relay_core/pool.py"                         # what the list reads is computed here
SPENT = '"AND refunded_at IS NULL AND at >= %s ORDER BY at", (ids, since))'
SPENT_REFUNDED = '"AND at >= %s ORDER BY at", (ids, since))'
COMPOSER = "a.provider_state->>'composer'"
NO_COMPOSER = "NULL::text"
MADE = 'by_id[str(account_id)]["made"][media_type] = count'
MADE_ZERO = 'by_id[str(account_id)]["made"][media_type] = 0'


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


@mutation(POOL, SPENT, SPENT_REFUNDED, expect="a refunded charge does not count as spent")
@pytest.mark.fabricated_rows("plants account charges that are not the job account_cost", "account_cost")
def test_each_pool_lists_its_window_with_the_tiers_budget_and_what_the_unrefunded_charges_spent(db):
    k = key(db)
    spent, refunded, pro = (new_account(db, label=n, checked=True) for n in ("spent", "refunded", "pro"))
    db.run("UPDATE accounts SET tier = 'pro', cycle_start = '2030-01-15' WHERE id = %s", pro)
    job = done_job(db, k, spent, ref="c0")
    db.run("UPDATE jobs SET taken_at = clock_timestamp() WHERE id = %s", job)
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount) VALUES (%s, %s, 'video', 1)", spent, job)
    job = done_job(db, k, refunded, ref="c-refunded")      # a refunded charge is not spent
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, refunded_at) "
           "VALUES (%s, %s, 'video', 1, clock_timestamp())", refunded, job)

    async def scenario(env):
        return await listed(env)

    rows = run_app(db, scenario)
    video = {name: next(c for c in rows[name]["capacity"] if c["pool"] == "video") for name in rows}
    window = {name: video[name]["windows"][0] for name in video}
    assert (window["spent"]["name"], window["spent"]["kind"], window["spent"]["budget"], window["spent"]["spent"]) == (
        "day", "day", 4, 1)
    assert video["spent"]["left_today"] == 3 and video["spent"]["budget"] == 4 and video["spent"]["unit"] == "jobs"
    assert window["refunded"]["spent"] == 0, "a refunded charge does not count as spent"
    assert video["refunded"]["left_today"] == 4
    assert isinstance(window["spent"]["refill_ms"], int)
    assert (video["pro"]["left_today"], video["pro"]["source"], window["pro"]["budget"]) == (4, "computed", 4), (
        "the fake provider names a budget for the free tier only: a pro account takes it, never unlimited")
    assert (rows["pro"]["tier"], rows["pro"]["tier_read"], rows["pro"]["cycle_start"]) == ("pro", False, "2030-01-15")
    assert all(c["drift"] is None for r in rows.values() for c in r["capacity"]), "drift is gone"


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
