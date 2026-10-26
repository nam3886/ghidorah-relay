"""Streaming through the facades: the job is created before any byte is sent, a heartbeat arrives
while it runs, a failure is one error event, and a client that leaves never cancels the job. These run against a
real server socket: the ASGI test transport buffers a whole response and cannot disconnect."""

import asyncio
import json
import re
import time

import httpx
import uvicorn
from apikit import request_logs, run_app
from doorkit import (
    VIDEO,
    charges,
    error_of,
    finish_done,
    finish_failed,
    finish_later,
    jobs,
    live,
)

import relay_cli.main as cli
from relay_core.api import facade_common as fc
from relay_core.testing.mutation import mutation

FC = "packages/relay-core/src/relay_core/api/facade_common.py"
CLI = "apps/relay/src/relay_cli/main.py"
OPENAI = "packages/relay-core/src/relay_core/api/routes_openai.py"
GEMINI = "packages/relay-core/src/relay_core/api/routes_gemini.py"
CHAT = "/v1/chat/completions"
GEN_STREAM = "/v1beta/models/fake-video:streamGenerateContent?alt=sse"
CHAT_BODY = {"model": VIDEO, "stream": True, "messages": [{"role": "user", "content": "a cat"}]}
GEN_BODY = {"contents": [{"role": "user", "parts": [{"text": "a cat"}]}]}
HELD_BODY = {**CHAT_BODY, "stream": False}
BEAT = re.compile(r"Generating… \(\d+s elapsed\)")


async def events(response: httpx.Response):
    """The `data:` frames of an event stream, parsed; `[DONE]` stays a string."""
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            yield line[6:] if line[6:] == "[DONE]" else json.loads(line[6:])


async def collect(client: httpx.AsyncClient, path: str, key, body: dict) -> list:
    async with client.stream("POST", path, json=body, headers=key.headers) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"), await r.aread()
        return [e async for e in events(r)]


# What a facade that cancelled the job when its client left would look like: the sleep of the wait loop, wrapped.
KILL_ON_LEAVE = (
    "        try:\n"
    "            await asyncio.sleep(POLL_S)\n"
    "        except (asyncio.CancelledError, GeneratorExit):\n"
    "            async def kill():\n"
    "                db = __import__('relay_core.db', fromlist=['tx'])\n"
    "                async with db.tx(pool) as c:\n"
    "                    await c.execute(\"UPDATE jobs SET status = 'failed', error_code = 'queue.never_started', \"\n"
    "                                    \"finished_at = now() WHERE id = %s\", (job_id,))\n"
    "            asyncio.ensure_future(kill())\n"
    "            raise")


def quick(monkeypatch, beat=0.3):
    monkeypatch.setattr(fc, "HEARTBEAT_S", beat)
    monkeypatch.setattr(fc, "POLL_S", 0.05)


@mutation(FC, "HEARTBEAT_S = 5.0", "HEARTBEAT_S = 50.0", expect="heartbeat")
def test_a_stream_sends_a_reasoning_content_heartbeat_within_6_seconds_while_the_job_runs(db):
    async def scenario(env):
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            started = time.monotonic()
            async with client.stream("POST", CHAT, json=CHAT_BODY, headers=key.headers) as r:
                assert r.status_code == 200
                beat = None
                try:
                    async with asyncio.timeout(6.0):
                        async for event in events(r):
                            delta = event["choices"][0]["delta"]
                            if "reasoning_content" in delta:
                                beat = delta["reasoning_content"]
                                break
                except TimeoutError:
                    raise AssertionError("no heartbeat within 6 s") from None
            assert 4.0 <= time.monotonic() - started < 6.0 and BEAT.fullmatch(beat.strip()), beat
        assert jobs(db, key.id)[0][1] == "queued"

    run_app(db, scenario)


def test_the_openai_stream_is_role_heartbeats_urls_with_stop_then_done_under_one_chatcmpl_id(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        later = finish_later(db, key.id, lambda d, j: finish_done(d, j, 2), delay_s=2.0)
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            frames = await collect(client, CHAT, key, CHAT_BODY)
        await later
        job_id = str(jobs(db, key.id)[0][0])
        assert frames[-1] == "[DONE]"
        chunks = frames[:-1]
        assert {c["id"] for c in chunks} == {"chatcmpl-" + job_id} and all(c["model"] == VIDEO for c in chunks)
        deltas = [c["choices"][0]["delta"] for c in chunks]
        assert deltas[0] == {"role": "assistant"}
        beats = [d["reasoning_content"] for d in deltas[1:-1]]
        assert len(beats) >= 2 and all(BEAT.fullmatch(b.strip()) for b in beats), deltas
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and all(
            c["choices"][0]["finish_reason"] is None for c in chunks[:-1])
        assert len(deltas[-1]["content"].splitlines()) == 2 and deltas[-1]["content"].startswith("http")

    run_app(db, scenario)


@mutation(OPENAI, "yield fc.sse(errors.envelope(fc.failure_code(value)))",
          'yield _chunk(cid, model, {"content": "generation failed"}, "stop")', expect="error")
def test_a_failed_job_ends_the_openai_stream_with_one_error_event_then_done(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        later = finish_later(db, key.id, lambda d, j: finish_failed(d, j, "render.deadline", "engine secret"), 0.6)
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            frames = await collect(client, CHAT, key, CHAT_BODY)
        await later
        assert frames[-1] == "[DONE]" and "secret" not in str(frames)
        errors = [f for f in frames if isinstance(f, dict) and "error" in f]
        assert len(errors) == 1 and frames[-2] is errors[0], "exactly one error event, right before [DONE]: %s" % frames
        assert errors[0] == {"error": {"code": "timeout", "type": "api_error", "param": None,
                                       "message": errors[0]["error"]["message"]}}
        assert not any(isinstance(f, dict) and f.get("choices") and f["choices"][0]["delta"].get("content")
                       for f in frames), "no result text after a failure"

    run_app(db, scenario)


@mutation(GEMINI, "yield fc.sse(gemini_body(fc.failure_code(value)))",
          'yield fc.sse(_reply(model, job_id, "x", "STOP"))', expect="gemini error")
def test_the_gemini_stream_sends_thought_heartbeats_then_the_result_or_one_gemini_error(db, monkeypatch):
    async def scenario(env):
        quick(monkeypatch)
        key = await env.new_key()
        later = finish_later(db, key.id, finish_done, 2.0)
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            frames = await collect(client, GEN_STREAM, key, GEN_BODY)
        await later
        parts = [f["candidates"][0]["content"]["parts"][0] for f in frames]
        assert len(frames) >= 3 and all(p.get("thought") is True and BEAT.fullmatch(p["text"].strip())
                                        for p in parts[:-1]), frames
        assert "finishReason" not in frames[0]["candidates"][0]
        assert frames[-1]["candidates"][0]["finishReason"] == "STOP" and parts[-1]["text"].startswith("http")
        assert frames[-1]["responseId"] == str(jobs(db, key.id)[0][0])

        later = finish_later(db, key.id, lambda d, j: finish_failed(d, j, "render.deadline", "engine secret"), 0.8,
                             which=1)
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            frames = await collect(client, GEN_STREAM, key, GEN_BODY)
        await later
        err = frames[-1]["error"] if "error" in frames[-1] else None
        assert err is not None, "gemini error: %s" % frames[-1]
        shown = (err["code"], err["status"], err["details"][0]["reason"])
        assert shown == (504, "DEADLINE_EXCEEDED", "timeout"), shown
        assert not any("error" in f for f in frames[:-1]) and "secret" not in str(frames)

    run_app(db, scenario)


def test_a_refused_create_is_a_real_http_error_before_any_stream_byte_on_both_facades(db):
    async def scenario(env):
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            for path, body, expected in (
                    (CHAT, {**CHAT_BODY, "model": "nope"}, (400, "input.bad_model")),
                    (CHAT, {**CHAT_BODY, "temperature": 1}, (400, "input.invalid")),
                    ("/v1beta/models/nope:streamGenerateContent", GEN_BODY, (400, "input.bad_model")),
                    ("/v1beta/models/fake-video:streamGenerateContent", {"contents": []}, (400, "input.empty_prompt"))):
                r = await client.post(path, json=body, headers=key.headers)
                assert error_of(r)[:2] == expected, r.text
                assert r.headers["content-type"].startswith("application/json")
            assert not jobs(db, key.id)
        assert charges(db, key_id=key.id) == 0

    run_app(db, scenario)


@mutation(FC, "        await asyncio.sleep(POLL_S)", KILL_ON_LEAVE, expect="still queued")
def test_a_client_that_disconnects_stops_only_the_stream_and_the_job_stays_charged_and_runs(db, monkeypatch):
    async def scenario(env):
        monkeypatch.setattr(fc, "HEARTBEAT_S", 0.3)
        monkeypatch.setattr(fc, "POLL_S", 0.5)
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            for path, body in ((CHAT, CHAT_BODY), (GEN_STREAM, GEN_BODY)):
                async with client.stream("POST", path, json=body, headers=key.headers) as r:
                    assert r.status_code == 200
                    async for _ in r.aiter_lines():        # the first frame is proof the job exists, then we leave
                        break
                await asyncio.sleep(2.5)                   # the server notices the closed socket
        rows = jobs(db, key.id)
        assert len(rows) == 2 and {s for _, s in rows} == {"queued"}, "still queued after a disconnect: %s" % rows
        assert charges(db, key_id=key.id) == 2
        assert db.one("SELECT count(*) FROM ledger WHERE entry = 'refund'")[0] == 0, "a disconnect refunds nothing"
        # and the job goes on to finish normally, readable by the client afterwards
        finish_done(db, rows[0][0])
        r = await env.client.get("/v1/video/generations/%s" % rows[0][0], headers=key.headers)
        assert r.json()["status"] == "done" and len(r.json()["result_urls"]) == 1

    run_app(db, scenario)


@mutation(FC, "HOLD_LIMIT_S = 1800.0", "HOLD_LIMIT_S = 180.0", expect="1800")
def test_the_hold_limit_is_1800_s_and_a_stream_that_reaches_it_says_to_poll_and_leaves_the_job_running(db, monkeypatch):
    assert fc.HOLD_LIMIT_S == 1800, "the hold limit is 1800 s"

    async def scenario(env):
        quick(monkeypatch)
        monkeypatch.setattr(fc, "HOLD_LIMIT_S", 0.8)
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=20) as client:
            frames = await collect(client, CHAT, key, CHAT_BODY)
        job_id = jobs(db, key.id)[0][0]
        last = frames[-2]
        assert frames[-1] == "[DONE]" and last["choices"][0]["finish_reason"] == "length"
        assert last["choices"][0]["delta"]["content"] == "still rendering — poll job %s" % job_id
        assert jobs(db, key.id)[0][1] == "queued" and charges(db, key_id=key.id) == 1

    run_app(db, scenario)


@mutation(FC, "        if gone is not None and await gone():\n", "        if False:\n",
          expect="stops reading the job once its client has gone")
def test_a_held_request_stops_reading_the_job_once_its_client_has_gone_and_the_job_runs_on(db, monkeypatch):
    """A non-stream hold may last 30 min; a client that left must not keep a database poll alive for that long."""
    reads = []
    real_state = fc.job_state

    async def counted(pool, job_id):
        reads.append(job_id)
        return await real_state(pool, job_id)

    async def scenario(env):
        quick(monkeypatch)
        monkeypatch.setattr(fc, "HOLD_LIMIT_S", 8.0)      # a hold that never notices ends anyway, so a red run ends
        monkeypatch.setattr(fc, "job_state", counted)
        key = await env.new_key()
        async with live(env.app) as base, httpx.AsyncClient(base_url=base, timeout=1.0) as client:
            try:
                await client.post(CHAT, json=HELD_BODY, headers=key.headers)
                raise AssertionError("the request should have been held past the client's timeout")
            except httpx.ReadTimeout:
                pass                                       # the client leaves; its socket is closed
            await asyncio.sleep(2.0)                       # the server notices at its next wait
            after_leaving = len(reads)
            await asyncio.sleep(1.0)
            assert len(reads) == after_leaving, (
                "the hold stops reading the job once its client has gone: %d more reads" % (len(reads) - after_leaving))
        [(job_id, status)] = jobs(db, key.id)
        assert status == "queued" and charges(db, key_id=key.id) == 1, "the job runs on, charged"
        rows = [r for r in request_logs(db) if r["route"] == "POST " + CHAT]
        assert [r["status_code"] for r in rows] == [499], rows

    run_app(db, scenario)


@mutation(CLI, "timeout_graceful_shutdown=SHUTDOWN_GRACE_S)\n", ")\n", expect="serve stops within its grace")
def test_serve_stops_within_its_grace_while_a_request_is_still_held(db, monkeypatch):
    """A stop signal does not wait for a held request (up to 30 min): it is cut after SHUTDOWN_GRACE_S, and its job
    runs on, so `relay serve` can still stop its runners and close the pool inside the container's stop grace."""
    assert cli.SHUTDOWN_GRACE_S < 30, "the grace stays under the api container stop grace"

    async def scenario(env):
        quick(monkeypatch)
        monkeypatch.setattr(fc, "HOLD_LIMIT_S", 20.0)
        monkeypatch.setattr(cli, "SHUTDOWN_GRACE_S", 1)
        key = await env.new_key()
        sock = __import__("socket").socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(cli.uvicorn_config(env.app, 0))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            await asyncio.sleep(0.01)
        base = "http://127.0.0.1:%d" % sock.getsockname()[1]
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            held = asyncio.create_task(client.post(CHAT, json=HELD_BODY, headers=key.headers))
            while not jobs(db, key.id):
                await asyncio.sleep(0.05)
            server.should_exit = True
            try:
                async with asyncio.timeout(6):
                    await serving
            except TimeoutError:
                serving.cancel()
                raise AssertionError("serve stops within its grace while a request is held") from None
            finally:
                held.cancel()
        assert jobs(db, key.id)[0][1] == "queued" and charges(db, key_id=key.id) == 1, "the cut request's job runs on"

    run_app(db, scenario)

