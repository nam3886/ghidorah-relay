"""Admin jobs (contracts 7.1, 7.5; D14, D15): the list and its cursor, the detail the page shows, debug files served as
text, Re-check refusals, the Recover and title commands, rename, and Regenerate at today's price through the gate."""

import dataclasses
import uuid

from apikit import run_app
from jobkit import POOLS, PROVIDER, account, claim, create, key, run, submit

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
AFTER_CURSOR = "(j.created_at, j.id) < (%(at)s::timestamptz"
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
