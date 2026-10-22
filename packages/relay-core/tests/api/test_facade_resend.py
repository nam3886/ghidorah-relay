"""An SDK that re-sends a request must never make a second charged job: every facade error says
`x-should-retry: false`, a non-stream request answers before the SDK's own timeout, and a re-send that carries
`x-stainless-retry-count` and no Idempotency-Key is answered with the job of the first send. Also the order of the
idempotency lookup (before any download), and the facts the replay and the status of a job must keep."""

import time

import httpx
import psycopg
from apikit import run_app
from doorkit import PNG, VIDEO, charges, error_of, finish_done, finish_failed, finish_later, jobs
from jobkit import run

from relay_core.api import doors
from relay_core.api import facade_common as fc
from relay_core.jobs import store
from relay_core.testing.mutation import mutation

DOORS = "packages/relay-core/src/relay_core/api/doors.py"
FC = "packages/relay-core/src/relay_core/api/facade_common.py"
ERRORS = "packages/relay-core/src/relay_core/api/errors_http.py"
NATIVE = "packages/relay-core/src/relay_core/api/routes_native.py"
VIDEO_PATH, CHAT, GEMINI = "/v1/video/generations", "/v1/chat/completions", "/v1beta/models/fake-video:generateContent"
RETRY = "x-stainless-retry-count"


def chat(content="a cat", model=VIDEO) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": content}]}


def gemini() -> dict:
    return {"contents": [{"role": "user", "parts": [{"text": "a cat"}]}]}


FACADES = (("openai", CHAT, chat, lambda r: r["job_id"], lambda r: r["choices"][0]["finish_reason"]),
           ("gemini", GEMINI, gemini, lambda r: r["responseId"], lambda r: r["candidates"][0]["finishReason"]))


def quick(monkeypatch, *, hold=0.3):
    monkeypatch.setattr(fc, "HOLD_LIMIT_S", hold)
    monkeypatch.setattr(fc, "POLL_S", 0.05)


# ---- x-should-retry: false ---------------------------------------------------------------------------------
@mutation(ERRORS, "        headers = {**(headers or {}), NO_RETRY[0]: NO_RETRY[1]}", "        pass",
          expect="x-should-retry")
def test_every_error_answer_of_a_facade_says_x_should_retry_false_and_a_native_one_does_not(db, monkeypatch):
    monkeypatch.setattr(fc, "POLL_S", 0.05)

    async def scenario(env):
        for name, path, body_of, _, _ in FACADES:
            key = await env.new_key()
            bad_model = await env.client.post(path.replace("fake-video", "nope"), headers=key.headers,
                                              json=body_of(model="nope") if name == "openai" else body_of())
            bad_key = await env.client.post(path, json=body_of(), headers={"Authorization": "Bearer rk-not-a-key"})
            later = finish_later(db, key.id, lambda d, j: finish_failed(d, j, "render.deadline"), 0.2)
            failed = await env.client.post(path, json=body_of(), headers=key.headers)
            await later
            for what, r in (("an error before the job", bad_model), ("an unknown key", bad_key),
                            ("a failed job", failed)):
                assert r.status_code >= 400, (name, what, r.text)
                assert r.headers.get("x-should-retry") == "false", (
                    "%s %s: the answer must carry x-should-retry: false, got %s" % (name, what, dict(r.headers)))
        key = await env.new_key()
        native = await env.client.post(VIDEO_PATH, json={"model": "nope", "prompt": "a"}, headers=key.headers)
        assert native.status_code == 400 and "x-should-retry" not in native.headers, "native doors keep their answer"

    run_app(db, scenario)


# ---- a non-stream request answers within 540 s ---------------------------------------------------------------
@mutation(FC, "NON_STREAM_HOLD_S = 540.0", "NON_STREAM_HOLD_S = 700.0", expect="600")
def test_the_non_stream_hold_stays_below_the_sdk_timeout_and_a_stream_keeps_1800():
    assert fc.NON_STREAM_HOLD_S < 600, "the hold must end below the SDK timeout of 600 s: %s" % fc.NON_STREAM_HOLD_S
    assert fc.HOLD_LIMIT_S == 1800


@mutation(FC, "limit=min(HOLD_LIMIT_S, NON_STREAM_HOLD_S)", "limit=HOLD_LIMIT_S", expect="within the non-stream hold")
def test_a_non_stream_request_ends_at_its_own_hold_with_the_poll_hint_while_the_job_runs_on(db, monkeypatch):
    monkeypatch.setattr(fc, "POLL_S", 0.05)
    monkeypatch.setattr(fc, "NON_STREAM_HOLD_S", 0.4)
    # A shorter stream limit makes the wrong hold end on its own after 3 s: cancelling the request instead can be
    # swallowed by Starlette's is_disconnected (a cancelled anyio scope) and leave the handler holding.
    monkeypatch.setattr(fc, "HOLD_LIMIT_S", 3.0)

    async def scenario(env):
        for name, path, body_of, _, finish_of in FACADES:
            key = await env.new_key()
            began = time.monotonic()
            r = await env.client.post(path, json=body_of(), headers=key.headers)
            assert time.monotonic() - began < 2.0, "%s: not answered within the non-stream hold" % name
            assert r.status_code == 200 and finish_of(r.json()) in ("length", "MAX_TOKENS"), (name, r.text)
            [(job_id, status)] = jobs(db, key.id)
            assert status == "queued" and charges(db, key_id=key.id) == 1, "the job runs on, charged"
            assert "poll job %s" % job_id in r.text, r.text

    run_app(db, scenario)


@mutation(FC, "        except (psycopg.Error, PoolTimeout):", "        except ZeroDivisionError:",
          expect="database error")
def test_a_database_error_while_holding_ends_the_hold_with_the_poll_hint_not_a_500(db, monkeypatch):
    quick(monkeypatch)

    async def broken(pool, job_id):
        raise psycopg.OperationalError("connection lost")

    async def scenario(env):
        monkeypatch.setattr(fc, "job_state", broken)           # the job is created first, then the hold reads it
        for name, path, body_of, job_of, _ in FACADES:
            key = await env.new_key()
            r = await env.client.post(path, json=body_of(), headers=key.headers)
            assert r.status_code == 200, "%s: a database error while holding must not be a 500 (database error): %s" % (
                name, r.text)
            [(job_id, _)] = jobs(db, key.id)
            assert job_of(r.json()) == str(job_id) and "poll job %s" % job_id in r.text, r.text
            assert charges(db, key_id=key.id) == 1
        # a stream ends the same way: the poll hint, then its normal end, never a cut answer
        for name, path, body in (("openai", CHAT, {**chat(), "stream": True}),
                                 ("gemini", GEMINI.replace(":generateContent", ":streamGenerateContent"),
                                  gemini())):
            key = await env.new_key()
            r = await env.client.post(path, json=body, headers=key.headers)
            [(job_id, _)] = jobs(db, key.id)
            ended = "poll job %s" % job_id in r.text and (name == "gemini" or r.text.rstrip().endswith("[DONE]"))
            assert r.status_code == 200 and ended, "%s stream: a database error must end with the poll hint " \
                "(database error): %s" % (name, r.text[-300:])

    run_app(db, scenario)


# ---- a re-send finds the job of its first send ------------------------------------------------------------
@mutation(DOORS, '    elif req.door == "facade" and req.retry_count >= 1:', "    elif False:", expect="re-sent")
@mutation(DOORS, "req.retry_count >= 1:", "req.retry_count >= 0:", expect="first send")
@mutation(DOORS, "RETRY_WINDOW_S = 30 * 60", "RETRY_WINDOW_S = 30 * 60 * 1000", expect="30 min")
@mutation(DOORS, '"SELECT id, status, provider, warnings FROM jobs WHERE key_id = %s AND request_hash = %s "',
          '"SELECT id, status, provider, warnings FROM jobs WHERE %s::uuid IS NOT NULL AND request_hash = %s "',
          expect="another key")
def test_a_facade_re_send_without_an_idempotency_key_is_answered_with_the_job_of_its_first_send(db, monkeypatch):
    quick(monkeypatch)

    async def scenario(env):
        for name, path, body_of, job_of, _ in FACADES:
            key, other = await env.new_key(), await env.new_key()
            body = body_of()

            def post(k, count=None, path=path, body=body):
                headers = k.headers if count is None else {**k.headers, RETRY: str(count)}
                return env.client.post(path, json=body, headers=headers)

            first = await post(key)                                        # the first send: count absent
            assert first.status_code == 200, first.text
            again = await post(key, 1)
            assert job_of(again.json()) == job_of(first.json()), "%s: a re-sent request must get its job" % name
            assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1, "%s: re-sent made a job" % name
            assert job_of((await post(key, 2)).json()) == job_of(first.json())
            second = await post(key, 0)                                    # a count of 0 is a first send
            assert job_of(second.json()) != job_of(first.json()) and len(jobs(db, key.id)) == 2, (
                "%s: a first send makes its own job" % name)
            assert job_of((await post(key, 1)).json()) == job_of(second.json()), "the newest job answers"
            assert charges(db, key_id=key.id) == 2
            foreign = await post(other, 1)                                 # another key never sees this key's job
            assert foreign.status_code == 200, "%s: another key's re-send: %s" % (name, foreign.text)
            assert job_of(foreign.json()) not in (job_of(first.json()), job_of(second.json())), (
                "%s: another key got a job of this key" % name)
            assert len(jobs(db, other.id)) == 1 and charges(db, key_id=other.id) == 1
            db.run("UPDATE jobs SET created_at = clock_timestamp() - interval '31 minutes' WHERE key_id = %s", key.id)
            late = await post(key, 1)
            assert len(jobs(db, key.id)) == 3 and job_of(late.json()) not in (
                job_of(first.json()), job_of(second.json())), "%s: past 30 min a re-send is a new request" % name
            odd = await post(key, "x")                                     # not a number: a first send
            assert odd.status_code == 200 and len(jobs(db, key.id)) == 4, odd.text

    run_app(db, scenario)


# ---- the idempotency lookup comes before any download -----------------------------------------------------------
@mutation(DOORS, "    if req.idem_key is not None:                 # first,",
          "    if req.idem_key is not None and not (req.images or req.callback_url):                 # first,",
          expect="replay with an unreachable")
def test_a_replay_with_an_image_url_that_no_longer_downloads_or_a_callback_host_gone_answers_the_job(db, monkeypatch):
    seen = {"status": 200, "ip": "93.184.216.34"}

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(seen["status"], content=PNG, headers={"content-type": "image/png"})

    async def resolve(host: str, port: int) -> list[str]:
        return [seen["ip"]]

    monkeypatch.setitem(doors.NET_OPTIONS, "transport", httpx.MockTransport(serve))
    monkeypatch.setitem(doors.NET_OPTIONS, "resolver", resolve)

    async def scenario(env):
        key = await env.new_key()
        h = {**key.headers, "Idempotency-Key": "k-1"}
        body = {"model": VIDEO, "prompt": "a cat", "image": "https://files.example.test/cat.png",
                "callback_url": "https://hooks.example.test/done"}
        first = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert first.status_code == 200, first.text
        seen["status"] = 404                                  # the image URL no longer downloads
        gone = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert gone.status_code == 200 and gone.json()["id"] == first.json()["id"], (
            "replay with an unreachable image: %s" % gone.text)
        seen["ip"] = "10.0.0.1"                               # and the hosts now resolve to a private address
        unreachable = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert unreachable.status_code == 200 and unreachable.json()["id"] == first.json()["id"], (
            "replay with an unreachable callback host: %s" % unreachable.text)
        other = await env.client.post(VIDEO_PATH, json={**body, "prompt": "a dog"}, headers=h)
        assert error_of(other)[:2] == (409, "input.idempotency_conflict"), "another body is 409: %s" % other.text
        assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


# ---- the facts a replay and a status must keep -----------------------------------------------------------------
@mutation(DOORS, 'request_hash = body_hash({\n        "endpoint": req.endpoint, "model"',
          'request_hash = body_hash({\n        "model"', expect="endpoint")
def test_the_same_idempotency_key_and_the_same_request_on_two_endpoints_is_409(db, monkeypatch):
    quick(monkeypatch)                                  # a red run replays the job, which a facade then holds

    async def scenario(env):
        key = await env.new_key()
        h = {**key.headers, "Idempotency-Key": "k-1"}
        first = await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a cat"}, headers=h)
        other = await env.client.post(CHAT, json=chat(), headers=h)
        assert first.status_code == 200
        assert error_of(other)[:2] == (409, "input.idempotency_conflict"), (
            "another endpoint is another request (the endpoint is in the hash): %s" % other.text)
        assert len(jobs(db, key.id)) == 1 and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


@mutation(NATIVE, "made.queue_position or None", "made.queue_position", expect="queue_position")
def test_a_replay_of_a_job_that_left_the_queue_answers_queue_position_null(db):
    async def scenario(env):
        key = await env.new_key()
        h = {**key.headers, "Idempotency-Key": "k-1"}
        body = {"model": VIDEO, "prompt": "a cat"}
        first = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert first.json()["queue_position"] == 1
        finish_done(db, first.json()["id"])
        again = await env.client.post(VIDEO_PATH, json=body, headers=h)
        assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
        assert again.json()["queue_position"] is None, "queue_position: a job off the queue has none: %s" % again.text

    run_app(db, scenario)


@mutation(DOORS, 'bool(refunded) and status == "failed"', "bool(refunded)", expect="refunded")
def test_a_refunded_job_that_a_recheck_charged_again_is_not_refunded_in_status(db):
    async def first(env):
        key = await env.new_key()
        made = await env.client.post(VIDEO_PATH, json={"model": VIDEO, "prompt": "a cat"}, headers=key.headers)
        await finish_failed(db, made.json()["id"])
        status = (await env.client.get("%s/%s" % (VIDEO_PATH, made.json()["id"]), headers=key.headers)).json()
        assert (status["status"], status["refunded"]) == ("failed", True)
        return key, made.json()["id"]

    key, job_id = run_app(db, first)
    db.run("UPDATE jobs SET external_ref = 'ref-1' WHERE id = %s", job_id)
    assert run(db, lambda p: store.recheck_job(p, job_id)) == 10, "the refunded points are charged again"

    async def second(env):
        return (await env.client.get("%s/%s" % (VIDEO_PATH, job_id), headers=key.headers)).json()

    status = run_app(db, second)
    assert status["status"] == "running" and status["refunded"] is False, (
        "refunded must be false once the job was charged again: %s" % status)
    assert db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'refund'", job_id) == (1,)
