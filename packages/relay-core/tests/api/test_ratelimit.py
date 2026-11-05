"""Per-key request limits (`api/ratelimit.py`): the token buckets with a fake clock, the upload gate on a bare ASGI app,
and both through the real app (429 shape, Retry-After, CORS, the request log, the keyless paths)."""

import asyncio
from types import SimpleNamespace

import pytest
from apikit import request_logs, run_app
from fastapi import APIRouter
from starlette.requests import ClientDisconnect

from relay_core.api.ratelimit import SWEEP_EVERY_S, BigBodyGate, RateLimiter
from relay_core.settings import REGISTRY
from relay_core.testing.mutation import mutation

RL = "packages/relay-core/src/relay_core/api/ratelimit.py"
DEPS = "packages/relay-core/src/relay_core/api/deps.py"
APP = "packages/relay-core/src/relay_core/api/app.py"
DEFAULTS = {k: s.default for k, s in REGISTRY.specs.items() if k.startswith("api_")}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def limiter(**over):
    clock = Clock()
    return RateLimiter(clock=clock), clock, {**DEFAULTS, **over}


# ---- the buckets -----------------------------------------------------------------------------------------------------

@mutation(RL, "        if b.tokens >= 1:\n", "        if True:\n", expect="past the burst")
@mutation(RL, "min(float(burst), b.tokens + (now - b.at) * rate)", "b.tokens + (now - b.at) * rate",
          expect="never above the capacity")
@mutation(RL, "        return (1 - b.tokens) / rate\n", "        return 1.0\n", expect="past the burst")
def test_a_bucket_allows_its_burst_then_refills_at_its_rate_never_above_its_capacity():
    rl, clock, s = limiter(api_read_rate=2, api_read_burst=3)
    assert [rl.take("k", "GET", s) for _ in range(3)] == [0, 0, 0]
    assert rl.take("k", "GET", s) == pytest.approx(0.5), "past the burst"
    clock.now += 0.5
    assert rl.take("k", "GET", s) == 0, "one token back after 1/rate"
    clock.now += 0.25
    assert rl.take("k", "GET", s) == pytest.approx(0.25), "the wait for one token"
    clock.now += SWEEP_EVERY_S / 2                         # idle a while, short of the sweep that drops the bucket
    assert [rl.take("k", "GET", s) for _ in range(4)][-1] > 0, "never above the capacity"


@mutation(RL, '        kind = "read" if method in READS else "create"\n', '        kind = "read"\n',
          expect="reads and creates")
def test_reads_and_creates_have_their_own_bucket_and_each_key_its_own():
    rl, _, s = limiter(api_read_burst=1, api_create_burst=1)
    assert rl.take("a", "GET", s) == 0 and rl.take("a", "GET", s) > 0
    assert rl.take("a", "HEAD", s) > 0, "HEAD is a read"
    assert rl.take("a", "POST", s) == 0, "reads and creates are counted apart"
    assert rl.take("a", "POST", s) > 0
    assert rl.take("b", "GET", s) == 0 and rl.take("b", "POST", s) == 0, "key b is not limited by key a"


def test_a_changed_setting_applies_to_a_bucket_that_exists():
    rl, clock, s = limiter(api_read_rate=1, api_read_burst=10)
    assert rl.take("k", "GET", s) == 0                     # 9 left
    s["api_read_burst"] = 2                                # clamped to the new capacity at the next take
    assert [rl.take("k", "GET", s) for _ in range(3)] == [0, 0, pytest.approx(1)]
    s["api_read_rate"] = 4                                 # the new rate refills
    clock.now += 0.25
    assert rl.take("k", "GET", s) == 0


@mutation(RL, "                del self.buckets[k]\n", "                pass\n", expect="dropped")
def test_a_bucket_back_to_its_capacity_is_dropped_by_the_sweep():
    rl, clock, s = limiter(api_read_rate=1, api_read_burst=5)
    rl.take("idle", "GET", s)
    rl.take("busy", "GET", s)
    clock.now += SWEEP_EVERY_S + 1
    for _ in range(5):
        rl.take("busy", "GET", s)                          # this take sweeps first; busy is then drained
    assert set(rl.buckets) == {("busy", "read")}, "an idle, refilled bucket is dropped"


@mutation(RL, "        if b.at - b.logged < LOG_EVERY_S:\n", "        if False:\n", expect="one a second")
def test_a_refused_key_is_logged_at_most_once_a_second():
    rl, clock, s = limiter(api_read_rate=1, api_read_burst=1)
    rl.take("k", "GET", s)
    due = []
    for _ in range(5):
        assert rl.take("k", "GET", s) > 0
        due.append(rl.log_due("k", "GET"))
    assert due == [True, False, False, False, False], "one a second"
    clock.now += 0.5
    rl.take("k", "GET", s)
    assert not rl.log_due("k", "GET"), "one a second"


# ---- the upload gate, on a bare ASGI app -----------------------------------------------------------------------------

class Gate:
    """A BigBodyGate over an app that holds each request until told, records what got through, and can fail."""

    def __init__(self, **over):
        self.settings = {**DEFAULTS, "api_big_body_wait_s": 0, **over}
        self.release: dict[str, asyncio.Event] = {}
        self.passed: list[str] = []
        self.gate = BigBodyGate(self.app, is_gated=lambda path: path.startswith("/v1"))

    async def app(self, scope, receive, send):
        name = scope["name"]
        self.passed.append(name)
        if name in self.release:
            await self.release[name].wait()
        if scope.get("boom"):
            raise scope["boom"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def call(self, name, *, key="k1", length: int | None = 9 << 20, method="POST", path="/v1/x", boom=None,
                   hold=False):
        if hold:
            self.release[name] = asyncio.Event()
        headers = [(b"authorization", b"Bearer " + key.encode())]
        if length is not None:
            headers.append((b"content-length", str(length).encode()))
        scope = {"type": "http", "method": method, "path": path, "headers": headers, "name": name, "boom": boom,
                 "app": SimpleNamespace(state=SimpleNamespace(settings=self.settings))}
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.gate(scope, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        return start["status"], dict((k.decode(), v.decode()) for k, v in start["headers"])

    def hold(self, name, **kw):
        return asyncio.create_task(self.call(name, hold=True, **kw))


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


@mutation(RL, '            return int(length) >= limit_mb << 20\n', "            return False\n", expect="waits")
def test_the_fifth_big_request_waits_for_a_slot_then_gets_429_with_retry_after():
    async def main():
        g = Gate(api_big_body_wait_s=1)
        held = [g.hold("big%d" % i, key="k%d" % i) for i in range(4)]
        await settle()
        status, headers = await g.call("fifth", key="k9")
        assert status == 429 and headers["retry-after"] == "1", "waits up to the wait setting, then 429"
        assert "fifth" not in g.passed, "the refused request never reached the app (its body was not read)"
        late = asyncio.create_task(g.call("late", key="k9"))
        await asyncio.sleep(0.1)
        assert "late" not in g.passed, "waits"
        g.release["big0"].set()
        assert (await late)[0] == 200, "takes the slot given back"
        for name in ("big1", "big2", "big3"):
            g.release[name].set()
        await asyncio.gather(*held)
        assert g.gate.total == 0 and g.gate.held == {}

    asyncio.run(main())


@mutation(RL, '            and self.held.get(who, 0) < settings.get("api_big_body_slots_per_key"))',
          ")", expect="per key")
def test_one_key_holds_at_most_its_slots_and_another_key_still_gets_one():
    async def main():
        g = Gate(api_big_body_slots_per_key=2)
        held = [g.hold("a%d" % i, key="a") for i in range(2)]
        await settle()
        assert (await g.call("a3", key="a"))[0] == 429, "per key"
        g.release["b"] = asyncio.Event()
        other = asyncio.create_task(g.call("b", key="b"))
        await settle()
        assert "b" in g.passed, "another key gets a slot"
        for e in g.release.values():
            e.set()
        await asyncio.gather(*held, other)

    asyncio.run(main())


@mutation(RL, "            self.total -= 1                 # no await before the counts are back",
          "            pass                 # no await before the counts are back", expect="given back")
def test_a_slot_is_given_back_after_an_error_a_disconnect_and_a_cancel():
    async def main():
        g = Gate(api_big_body_slots=1, api_big_body_slots_per_key=1)
        for boom in (RuntimeError("crash"), ClientDisconnect()):
            with pytest.raises(type(boom)):
                await g.call("x", boom=boom)
            assert g.gate.total == 0, "given back"
        task = g.hold("cancelled")
        await settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert g.gate.total == 0 and g.gate.held == {}, "given back"
        assert (await g.call("next"))[0] == 200

    asyncio.run(main())


@mutation(RL, '            return request.method == "POST"\n', "            return False\n",
          expect="no Content-Length")
def test_small_requests_never_wait_and_a_post_without_length_counts_as_big():
    async def main():
        g = Gate(api_big_body_slots=1, api_big_body_slots_per_key=1)
        held = g.hold("big")
        await settle()
        assert (await g.call("small", length=1000))[0] == 200, "a small body never waits"
        assert (await g.call("get", length=None, method="GET"))[0] == 200, "a GET with no body never waits"
        assert (await g.call("other-path", path="/admin/x"))[0] == 200, "only the gated paths"
        assert (await g.call("chunked", length=None))[0] == 429, "a POST with no Content-Length is big"
        g.release["big"].set()
        await held

    asyncio.run(main())


# ---- through the app -------------------------------------------------------------------------------------------------

def hold_routes(release: asyncio.Event) -> APIRouter:
    r = APIRouter()

    @r.post("/v1/test-hold")
    async def hold():
        await release.wait()
        return {"ok": True}

    return r


@mutation(APP, "    app.add_middleware(BigBodyGate, is_gated=_is_keyed)\n", "", expect="upload_busy")
def test_the_upload_gate_answers_upload_busy_with_cors_and_retry_after(db):
    release = asyncio.Event()

    async def scenario(env):
        await env.settings.set("api_big_body_slots_per_key", 1)
        await env.settings.set("api_big_body_wait_s", 0)
        key = await env.new_key()
        body = b"x" * (8 << 20)
        first = asyncio.create_task(env.client.post("/v1/test-hold", content=body, headers=key.headers))
        await asyncio.sleep(0.2)
        try:
            second = await asyncio.wait_for(env.client.post(
                "/v1/test-hold", content=body, headers={**key.headers, "Origin": "https://app.example"}), 5)
        except TimeoutError:
            second = None                  # it went through to the held route: no gate
        release.set()
        return (await first).status_code, second

    first, second = run_app(db, scenario, routers=[hold_routes(release)])
    assert first == 200
    assert second is not None and second.status_code == 429, "upload_busy"
    assert second.json()["error"]["code"] == "quota.upload_busy"
    assert second.headers["retry-after"] == "1" and second.headers["access-control-allow-origin"] == "*"
    assert not [r for r in request_logs(db) if r["status_code"] == 429], "the gate names no key: no log row"


@mutation(DEPS, "            _rate_limit(request, key.id)\n", "", expect="rate_limited")
def test_a_key_over_its_rate_gets_429_with_retry_after_cors_and_both_error_shapes(db):
    async def scenario(env):
        clock = Clock()
        env.app.state.limiter = RateLimiter(clock=clock)
        await env.settings.set("api_read_burst", 2)
        await env.settings.set("api_create_burst", 1)
        a, b = await env.new_key(), await env.new_key()
        origin = {"Origin": "https://app.example"}
        reads = [await env.client.get("/v1/models", headers=a.headers) for _ in range(2)]
        over = await env.client.get("/v1/models", headers={**a.headers, **origin})
        gemini = await env.client.get("/v1beta/models", headers=a.headers)
        other = await env.client.get("/v1/models", headers=b.headers)
        create = await env.client.post("/v1/chat/completions", json={"model": "nope", "messages": []},
                                       headers=a.headers)
        create_over = await env.client.post("/v1/chat/completions", json={"model": "nope", "messages": []},
                                            headers=a.headers)
        clock.now += 1
        again = await env.client.get("/v1/models", headers=a.headers)
        return reads, over, gemini, other, create, create_over, again

    reads, over, gemini, other, create, create_over, again = run_app(db, scenario)
    assert [r.status_code for r in reads] == [200, 200]
    assert over.status_code == 429, "rate_limited"
    assert over.json()["error"] == {"code": "quota.rate_limited", "type": "rate_limit_error", "param": None,
                                    "message": over.json()["error"]["message"]}, "rate_limited"
    assert over.headers["retry-after"] == "1" and over.headers["access-control-allow-origin"] == "*"
    err = gemini.json()["error"]
    assert (gemini.status_code, err["status"], err["details"][0]["reason"]) == (
        429, "RESOURCE_EXHAUSTED", "quota.rate_limited"), "the Gemini shape on the Gemini routes"
    assert other.status_code == 200, "another key is not limited"
    assert create.status_code == 400, "creates have their own bucket"
    assert create_over.status_code == 429 and create_over.headers["retry-after"] == "1"
    assert again.status_code == 200, "a token is back after a second"


def test_unknown_keys_and_keyless_paths_are_never_limited_and_refusals_log_once_a_second(db):
    async def scenario(env):
        clock = Clock()
        env.app.state.limiter = RateLimiter(clock=clock)
        await env.settings.set("api_read_burst", 1)
        await env.settings.set("api_read_rate", 1)
        key = await env.new_key()
        unknown = [await env.client.get("/v1/models", headers={"Authorization": "Bearer rk_nope"})
                   for _ in range(3)]
        keyless = [await env.client.get(p) for p in ("/healthz", "/openapi.json", "/llms.txt", "/llms-full.txt",
                                                      "/healthz", "/m/nothing-here")]
        statuses = [(await env.client.get("/v1/models", headers=key.headers)).status_code for _ in range(6)]
        clock.now += 0.5
        statuses.append((await env.client.get("/v1/models", headers=key.headers)).status_code)
        return [r.status_code for r in unknown], [r.status_code for r in keyless], statuses, key, \
            dict(env.app.state.limiter.buckets)

    unknown, keyless, statuses, key, buckets = run_app(db, scenario)
    assert unknown == [401, 401, 401] and set(buckets) == {(key.id, "read")}, "an unknown key is not bucketed"
    assert keyless == [200, 200, 200, 200, 200, 404], "the keyless paths are not limited"
    assert statuses == [200] + [429] * 6
    refused = [r for r in request_logs(db) if r["status_code"] == 429]
    assert len(refused) == 1 and refused[0]["key_id"] == key.id and refused[0]["error_code"] == "quota.rate_limited"
