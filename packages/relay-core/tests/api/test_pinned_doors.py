"""`source_job_id` on the native doors: a request that works on an output of the key's own earlier job. Only a finished
job of the same key and provider is a source, only a model whose plan uses it takes one, the status names it, a
request without one hashes as before, and Admin Regenerate keeps the pin."""

import uuid

from accountkit import done_job
from apikit import run_app
from doorkit import charges, error_of, finish_failed
from jobkit import account
from test_admin_regenerate_once import op_key, post
from test_create_doors import IMAGE_PATH, VIDEO_PATH, video

from relay_core.jobs.create import body_hash
from relay_core.testing.fake_provider import FakeSpec
from relay_core.testing.mutation import mutation

DOORS = "packages/relay-core/src/relay_core/api/doors.py"
ROUTES = "packages/relay-core/src/relay_core/admin/routes_jobs.py"
RECHECK = "packages/relay-core/src/relay_core/jobs/recheck.py"


@mutation(DOORS, "                         door=req.door, source=source)", "                         door=req.door)",
          expect="the plan sees the source")
def test_a_request_with_a_source_job_is_pinned_and_its_status_names_the_source(db):
    acct = account(db)

    async def scenario(env):
        key = await env.new_key()
        src = done_job(db, key.id, acct)
        made = await env.client.post(VIDEO_PATH, json=video(source_job_id=str(src)), headers=key.headers)
        assert made.status_code == 200, "the plan sees the source: %s" % made.text
        status = await env.client.get("%s/%s" % (VIDEO_PATH, made.json()["id"]), headers=key.headers)
        return src, made.json()["id"], status.json()

    src, job, status = run_app(db, scenario)
    assert status["source_job_id"] == str(src)
    assert db.one("SELECT source_job_id, pin_account_id, request->>'source_job_id' FROM jobs WHERE id = %s",
                  uuid.UUID(job)) == (src, acct, str(src))


def test_only_a_finished_job_of_the_same_key_and_provider_is_a_source_and_nothing_is_charged_otherwise(db):
    acct = account(db)

    async def scenario(env):
        env.add_provider(FakeSpec("other"))
        key, stranger = await env.new_key(), await env.new_key()
        cases = {"not an id": "nope", "unknown": str(uuid.uuid4()),
                 "another key's job": str(done_job(db, stranger.id, acct)),
                 "a job that is not done": str(db.job(key.id, provider="fake")),
                 "another provider's job": str(done_job(db, key.id, acct))}
        db.run("UPDATE jobs SET provider = 'other' WHERE id = %s", uuid.UUID(cases["another provider's job"]))
        out = {}
        for name, source in cases.items():
            r = await env.client.post(VIDEO_PATH, json=video(source_job_id=source), headers=key.headers)
            out[name] = error_of(r)
        return key, out

    key, out = run_app(db, scenario)
    for name, got in out.items():
        assert got == (400, "input.bad_source", "source_job_id"), "%s: %s" % (name, got)
    assert charges(db, key_id=key.id) == 0


@mutation(DOORS, "    if creq.source is not None and not plan.uses_source:", "    if False:",
          expect="never runs on another account")
def test_a_model_that_works_on_no_source_refuses_one_rather_than_run_it_on_any_account(db):
    acct = account(db)

    async def scenario(env):
        key = await env.new_key()
        src = done_job(db, key.id, acct)
        r = await env.client.post(IMAGE_PATH, json={"model": "fake-image", "prompt": "a cat", "source_job_id": str(src),
                                                    "options": {"ignores_source": True}}, headers=key.headers)
        return key, r

    key, r = run_app(db, scenario)
    assert error_of(r) == (400, "input.source_unsupported", "source_job_id"), "never runs on another account"
    assert charges(db, key_id=key.id) == 0


@mutation(DOORS, '    named = {} if req.source_job_id is None else {"source_job_id": req.source_job_id}',
          '    named = {"source_job_id": req.source_job_id}', expect="hashes as it always did")
def test_a_request_without_a_source_hashes_as_it_always_did(db):
    async def scenario(env):
        key = await env.new_key()
        r = await env.client.post(VIDEO_PATH, json=video(), headers=key.headers)
        return r.json()["id"]

    job = run_app(db, scenario)
    before = body_hash({"endpoint": "video", "model": "fake-video", "prompt": "a cat", "duration": None,
                        "ratio": None, "n": None, "options": {}, "title": None, "callback_url": None, "images": []})
    assert db.one("SELECT request_hash FROM jobs WHERE id = %s", uuid.UUID(job))[0] == before, (
        "a request without a source hashes as it always did")


@mutation(RECHECK, "                 source_job_id=None if source is None else uuid.UUID(source))",
          "                 source_job_id=None)", expect="Regenerate keeps the pin")
def test_admin_regenerate_of_a_pinned_job_makes_a_job_pinned_to_the_same_source(db):
    acct = account(db)

    async def scenario(env):
        key = await env.new_key()
        src = done_job(db, key.id, acct)
        made = await env.client.post(VIDEO_PATH, json=video(source_job_id=str(src)), headers=key.headers)
        old = uuid.UUID(made.json()["id"])
        await finish_failed(db, old)
        again = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": op_key()})
        return src, again

    src, again = run_app(db, scenario)
    assert again.status_code == 200, "Regenerate keeps the pin: %s" % again.text
    assert db.one("SELECT source_job_id, pin_account_id FROM jobs WHERE id = %s", uuid.UUID(again.json()["id"])) == (
        src, acct), "Regenerate keeps the pin"

