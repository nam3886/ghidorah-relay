"""One admin Regenerate action makes one charged job per source job, however often its request is sent (contracts §7.1,
§16): the page's operation key, the replay through jobs.idem_key, and a bulk answer that always says what happened."""

import uuid

from apikit import run_app
from jobkit import account, key, run
from test_admin_jobs import failed_job
from test_create_doors import VIDEO_PATH, video
from test_frame_plan import framed, put, seed, stored

from relay_core.admin import routes_jobs
from relay_core.jobs import recheck, store
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_jobs.py"
DOORS = "packages/relay-core/src/relay_core/api/doors.py"
EVENT_LOGGED = 'log.exception("the regenerated event of job %s was not written (new job %s)", job_id, created.job_id)'
EVENT_RAISES = "raise"
RECHECK_LOGGED = 'skipped[str(job_id)] = "internal"'
RECHECK_RAISES = "raise"
RECHECK = "packages/relay-core/src/relay_core/jobs/recheck.py"
FRESH_KEY = "idem_key = _regenerate_key(operation_key, job_id)"
FRESH_KEY_EVERY_TIME = "idem_key = uuid.uuid4().hex"
LOOK_FIRST = "if earlier is not None:"
NEVER_LOOK = "if False:"
EVENT_ONCE = "if not created.replayed:"
EVENT_ALWAYS = "if True:"
CATCH_ALL = "        except Exception:\n            log.exception"
CATCH_ONE = "        except KeyError:\n            log.exception"
SOURCE_IN_KEY = r'return "regenerate\x1f%s\x1f%s" % (operation_key, job_id)'
SOURCE_NOT_IN_KEY = r'return "regenerate\x1f%s" % operation_key'
SINGLE_BODY = "class RegenerateBody(Strict):\n    operation_key: str = Field(pattern=OPERATION_KEY)"
SINGLE_BODY_OPTIONAL = 'class RegenerateBody(Strict):\n    operation_key: str = Field("a" * 16, pattern=OPERATION_KEY)'
BULK_BODY = "class BulkRegenerateBody(BulkBody):\n    operation_key: str = Field(pattern=OPERATION_KEY)"
BULK_BODY_OPTIONAL = (
    'class BulkRegenerateBody(BulkBody):\n    operation_key: str = Field("a" * 16, pattern=OPERATION_KEY)')
KEY_PATTERN = 'OPERATION_KEY = r"^[A-Za-z0-9_-]{16,64}$"'
ANY_KEY = 'OPERATION_KEY = r"^.*$"'


async def post(env, path: str, body=None):
    return await env.client.post("/admin" + path, headers=await env.admin_headers(), json=body)


def charges(db, job_ids) -> int:
    return db.one("SELECT count(*) FROM ledger WHERE entry = 'charge' AND job_id = ANY(%s)",
                  [uuid.UUID(str(j)) for j in job_ids])[0]


def events(db, job) -> int:
    return db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'regenerated'", job)[0]


def op_key() -> str:
    return uuid.uuid4().hex


@mutation(ROUTES, FRESH_KEY, FRESH_KEY_EVERY_TIME, expect="the same request gives the same job")
@mutation(ROUTES, LOOK_FIRST, NEVER_LOOK, expect="inputs are copied once")
def test_the_same_single_request_again_returns_the_job_made_the_first_time_and_charges_and_copies_nothing(db):
    k = key(db)
    account(db)
    inputs = [store.InputMedia(0, "src/input/0.png", "image/png", 9, 4, 3)]
    old = run(db, lambda p: failed_job(p, k, "edit this", inputs=inputs))
    operation = op_key()

    async def scenario(env):
        await env.store.put_bytes("src/input/0.png", b"\x89PNG-input", "image/png")
        copies = []
        real = env.store.put_bytes

        async def counting(key_, data, mime):
            copies.append(key_)
            return await real(key_, data, mime)

        env.store.put_bytes = counting
        first = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        again = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        env.store.put_bytes = real
        return first, again, copies

    first, again, copies = run_app(db, scenario)
    assert first.status_code == 200 and again.status_code == 200, (first.text, again.text)
    assert first.json()["id"] == again.json()["id"], "the same request gives the same job"
    assert (first.json()["replayed"], again.json()["replayed"]) == (False, True)
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "one new job"
    assert charges(db, [first.json()["id"]]) == 1, "one ledger charge"
    assert events(db, old) == 1, "one event on the source job"
    assert len(copies) == 1, "inputs are copied once"


@mutation(RECHECK, EVENT_ONCE, EVENT_ALWAYS, expect="one event on the source job")
def test_two_requests_that_both_pass_the_first_look_still_make_one_job_one_charge_and_one_event(db, monkeypatch):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    operation = op_key()

    async def nothing_yet(pool, job_id, idem_key):      # what two concurrent requests both see before either wrote
        return None

    monkeypatch.setattr(routes_jobs, "_made_before", nothing_yet)

    async def scenario(env):
        first = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        again = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        return first, again

    first, again = run_app(db, scenario)
    assert first.json()["id"] == again.json()["id"] and again.json()["replayed"] is True
    assert charges(db, [first.json()["id"]]) == 1
    assert events(db, old) == 1, "one event on the source job"


def test_a_new_operation_key_is_a_new_action_and_makes_a_second_charged_job(db):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))

    async def scenario(env):
        first = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": op_key()})
        second = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": op_key()})
        return first, second

    first, second = run_app(db, scenario)
    assert first.json()["id"] != second.json()["id"]
    assert (first.json()["replayed"], second.json()["replayed"]) == (False, False)
    assert charges(db, [first.json()["id"], second.json()["id"]]) == 2
    assert events(db, old) == 2


@mutation(ROUTES, FRESH_KEY, FRESH_KEY_EVERY_TIME, expect="the same job ids")
def test_the_same_bulk_request_again_returns_the_same_ids_and_charges_each_source_job_once(db):
    k = key(db)
    account(db)
    sources = [run(db, lambda p, n=n: failed_job(p, k, "job %d" % n)) for n in range(3)]
    operation = op_key()
    body = {"job_ids": [str(s) for s in sources], "operation_key": operation}

    async def scenario(env):
        return await post(env, "/jobs/bulk-regenerate", body), await post(env, "/jobs/bulk-regenerate", body)

    first, again = run_app(db, scenario)
    ids = [c["id"] for c in first.json()["created"]]
    assert [c["id"] for c in again.json()["created"]] == ids, "the same job ids"
    assert [c["replayed"] for c in first.json()["created"]] == [False] * 3
    assert [c["replayed"] for c in again.json()["created"]] == [True] * 3
    assert db.one("SELECT count(*) FROM jobs")[0] == 6
    assert charges(db, ids) == 3, "one charge per source job"


@mutation(ROUTES, CATCH_ALL, CATCH_ONE, expect="never a 500 after some jobs were made")
def test_an_unexpected_error_at_one_job_is_listed_the_rest_go_on_and_a_retry_finishes_only_it(db, monkeypatch):
    k = key(db)
    account(db)
    sources = [run(db, lambda p, n=n: failed_job(p, k, "job %d" % n)) for n in range(3)]
    operation = op_key()
    body = {"job_ids": [str(s) for s in sources], "operation_key": operation}
    broken = {"on": True}
    real = routes_jobs.fresh_plan

    async def flaky(gate, job_id):
        if broken["on"] and job_id == sources[1]:
            raise RuntimeError("injected failure")
        return await real(gate, job_id)

    monkeypatch.setattr(routes_jobs, "fresh_plan", flaky)

    async def scenario(env):
        first = await post(env, "/jobs/bulk-regenerate", body)
        broken["on"] = False
        return first, await post(env, "/jobs/bulk-regenerate", body)

    first, again = run_app(db, scenario)
    assert first.status_code == 200, "never a 500 after some jobs were made: %s" % first.text
    made = first.json()
    assert [c["from"] for c in made["created"]] == [str(sources[0]), str(sources[2])]
    assert made["failed"] == {str(sources[1]): "internal"}
    assert again.status_code == 200
    final = {c["from"]: c for c in again.json()["created"]}
    assert again.json()["failed"] == {}
    assert [final[str(s)]["replayed"] for s in sources] == [True, False, True]
    assert final[str(sources[0])]["id"] == made["created"][0]["id"], "the jobs made before keep their ids"
    assert final[str(sources[2])]["id"] == made["created"][1]["id"]
    assert db.one("SELECT count(*) FROM jobs")[0] == 6
    assert charges(db, [c["id"] for c in final.values()]) == 3, "no source job was charged twice"


@mutation(ROUTES, SINGLE_BODY, SINGLE_BODY_OPTIONAL, expect="no key is refused")
@mutation(ROUTES, BULK_BODY, BULK_BODY_OPTIONAL, expect="no key is refused")
@mutation(ROUTES, KEY_PATTERN, ANY_KEY, expect="a malformed key is refused")
def test_a_request_without_a_valid_operation_key_is_refused_before_anything_is_charged(db):
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    before = db.one("SELECT count(*) FROM jobs")[0]

    async def scenario(env):
        out = {}
        out["single, no key"] = await post(env, "/jobs/%s/regenerate" % old, {})
        out["single, no body"] = await post(env, "/jobs/%s/regenerate" % old)
        out["bulk, no key"] = await post(env, "/jobs/bulk-regenerate", {"job_ids": [str(old)]})
        for name, bad in (("short", "abc"), ("characters", "a" * 15 + "\x1f"), ("long", "a" * 65)):
            out["single, " + name] = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": bad})
            out["bulk, " + name] = await post(env, "/jobs/bulk-regenerate",
                                              {"job_ids": [str(old)], "operation_key": bad})
        return out

    out = run_app(db, scenario)
    for name, r in out.items():
        is_key = name.endswith("no key") or name.endswith("no body")
        message = "no key is refused: %s" % name if is_key else "a malformed key is refused: %s" % name
        error = r.json().get("error", {})
        assert (r.status_code, error.get("code"), error.get("param")) == (
            400, "input.invalid", "operation_key"), message
    assert db.one("SELECT count(*) FROM jobs")[0] == before and charges(db, [old]) == 1


@mutation(ROUTES, SOURCE_IN_KEY, SOURCE_NOT_IN_KEY, expect="each source job gets its own job")
def test_one_operation_key_used_for_two_source_jobs_gives_each_its_own_job(db):
    k = key(db)
    account(db)
    one = run(db, lambda p: failed_job(p, k, "one"))
    two = run(db, lambda p: failed_job(p, k, "two"))
    operation = op_key()

    async def scenario(env):
        single_one = await post(env, "/jobs/%s/regenerate" % one, {"operation_key": operation})
        single_two = await post(env, "/jobs/%s/regenerate" % two, {"operation_key": operation})
        bulk = await post(env, "/jobs/bulk-regenerate",
                          {"job_ids": [str(one), str(two)], "operation_key": operation})
        return single_one, single_two, bulk

    single_one, single_two, bulk = run_app(db, scenario)
    assert single_one.json()["id"] != single_two.json()["id"], "each source job gets its own job"
    assert [c["id"] for c in bulk.json()["created"]] == [single_one.json()["id"], single_two.json()["id"]]
    assert all(c["replayed"] for c in bulk.json()["created"])
    prompts = [db.one("SELECT prompt FROM jobs WHERE id = %s", uuid.UUID(i))[0]
               for i in (single_one.json()["id"], single_two.json()["id"])]
    assert prompts == ["one", "two"], "each new job carries its own source's prompt"


@mutation(DOORS, "or not req.idem_key.isprintable()", "or False", expect="a customer key cannot be an admin key")
def test_a_customer_idempotency_key_cannot_be_the_key_of_an_admin_regenerate(db):
    """The derived key contains a unit separator. The customer doors refuse an Idempotency-Key that is not printable,
    so no customer request can store, replay or block such a key, not even one of the source job's own API key."""
    account(db)
    operation = op_key()

    async def scenario(env):
        customer = await env.new_key()
        source = await env.client.post(VIDEO_PATH, json=video(), headers=customer.headers)
        old = source.json()["id"]
        derived = routes_jobs._regenerate_key(operation, uuid.UUID(old))
        header = lambda idem: {**customer.headers, "Idempotency-Key": idem}  # noqa: E731
        forged = await env.client.post(VIDEO_PATH, json=video(prompt="forged"), headers=header(derived))
        plain = await env.client.post(VIDEO_PATH, json=video(prompt="plain"),
                                      headers=header("regenerate-" + operation))
        regenerate = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        return forged, plain, regenerate

    forged, plain, regenerate = run_app(db, scenario)
    assert (forged.status_code, forged.json().get("error", {}).get("code")) == (400, "input.invalid"), (
        "a customer key cannot be an admin key")
    assert plain.status_code == 200 and regenerate.status_code == 200 and regenerate.json()["replayed"] is False
    assert plain.json()["id"] != regenerate.json()["id"]


@mutation(RECHECK, EVENT_LOGGED, EVENT_RAISES, expect="the new job is returned")
def test_a_failing_event_write_after_the_new_job_was_committed_still_returns_the_new_job(db, monkeypatch):
    k = key(db)
    account(db)
    sources = [run(db, lambda p, n=n: failed_job(p, k, "job %d" % n)) for n in range(2)]

    async def broken(*args, **kwargs):
        raise RuntimeError("the event table is down")

    monkeypatch.setattr(recheck, "add_event", broken)

    async def scenario(env):
        single = await post(env, "/jobs/%s/regenerate" % sources[0], {"operation_key": op_key()})
        bulk = await post(env, "/jobs/bulk-regenerate",
                          {"job_ids": [str(sources[1])], "operation_key": op_key()})
        return single, bulk

    single, bulk = run_app(db, scenario)
    assert single.status_code == 200 and single.json()["replayed"] is False, "the new job is returned"
    assert bulk.json()["failed"] == {} and len(bulk.json()["created"]) == 1, "listed as made"
    assert db.one("SELECT count(*) FROM jobs")[0] == 4 and charges(db, [single.json()["id"]]) == 1


@mutation(RECHECK, RECHECK_LOGGED, RECHECK_RAISES, expect="every per-job failure")
def test_bulk_recheck_lists_an_unexpected_failure_of_one_job_and_goes_on(db, monkeypatch):
    k = key(db)
    account(db)
    sources = [run(db, lambda p, n=n: failed_job(p, k, "job %d" % n)) for n in range(3)]
    real = recheck.recheck_job

    async def flaky(pool, job_id, **kwargs):
        if job_id == sources[1]:
            raise RuntimeError("injected failure")
        return await real(pool, job_id, **kwargs)

    monkeypatch.setattr(recheck, "recheck_job", flaky)

    async def scenario(env):
        return await post(env, "/jobs/bulk-recheck", {"job_ids": [str(s) for s in sources]})

    r = run_app(db, scenario)
    assert r.status_code == 200, "every per-job failure is listed, never a 500: %s" % r.text
    assert r.json()["skipped"].get(str(sources[1])) == "internal"
    assert str(sources[1]) not in r.json()["rechecked"]


def test_a_regenerate_with_frames_sends_todays_frame_once_and_a_replay_changes_nothing(db):
    seed(db)
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    put(db, "video", "fake-video", "Today. {{client_prompt}} {{tracking_code}}")
    operation = op_key()

    async def scenario(env):
        first = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        # the frame changes between the two sends: a replay must not render or charge again
        put(db, "video", "fake-video", "Later. {{client_prompt}} {{tracking_code}}")
        again = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": operation})
        return first, again

    first, again = run_app(db, scenario, spec=framed())
    assert first.status_code == again.status_code == 200, (first.text, again.text)
    new = first.json()["id"]
    assert again.json()["id"] == new and again.json()["replayed"] is True
    text, marker, frame_id = stored(db, new)
    assert text == "Today. a cat [%s]" % marker and frame_id is not None, "the new job carries today's frame"
    assert stored(db, new) == (text, marker, frame_id), "a replay rewrites nothing"
    assert charges(db, [new]) == 1 and events(db, old) == 1


@mutation(ROUTES, '    prompt = checked_prompt(sent["prompt"], prompt_cap(gate.registry, sent["model"]))',
          '    prompt = sent["prompt"]', expect="a blank prompt")
def test_a_stored_prompt_the_door_would_refuse_is_refused_with_its_own_code_before_any_charge(db):
    seed(db)
    k = key(db)
    account(db)
    old = run(db, lambda p: failed_job(p, k, "a cat"))
    db.run("UPDATE jobs SET request = jsonb_set(request, '{prompt}', to_jsonb(%s::text)) WHERE id = %s", "   ", old)

    async def scenario(env):
        single = await post(env, "/jobs/%s/regenerate" % old, {"operation_key": op_key()})
        bulk = await post(env, "/jobs/bulk-regenerate", {"job_ids": [str(old)], "operation_key": op_key()})
        return single, bulk

    single, bulk = run_app(db, scenario, spec=framed())
    assert single.status_code == 400 and single.json()["error"]["code"] == "input.empty_prompt", (
        "a blank prompt reached the creation: %s" % single.text)
    assert bulk.json()["failed"] == {str(old): "input.empty_prompt"}, bulk.text
    assert db.one("SELECT count(*) FROM jobs")[0] == 1
