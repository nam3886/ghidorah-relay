"""Helpers for the create-door tests: bodies, a provider whose plan() can refuse, ways to end a job, a live server
for streaming, and the capture of real SDK request bodies (golden snapshots)."""

import asyncio
import base64
import contextlib
import dataclasses
import json
import os
import socket
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from jobkit import POOLS

from relay_core.db import open_pool
from relay_core.jobs import store
from relay_core.providers import Plan
from relay_core.testing.fake_provider import FakeSpec

SNAPSHOTS = Path(__file__).parent / "snapshots"
VIDEO, IMAGE = "fake-video", "fake-image"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
# Bytes whose standard base64 holds `+` and `/`, so the url-safe form differs from it.
PNG_URLSAFE_ONLY = b"\x89PNG\r\n\x1a\n" + b"\xfb\xff\xbe" * 5 + b"\x01"


def png_uri(data: bytes = PNG) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def error_of(r: httpx.Response) -> tuple[int, str, str | None]:
    """(status, code, param) of an OpenAI-style or Gemini-style error body."""
    assert "error" in r.json(), "expected an error, got %d: %s" % (r.status_code, r.text[:300])
    err = r.json()["error"]
    if isinstance(err.get("code"), int):                         # Gemini body
        d = err["details"][0]
        return r.status_code, d["reason"], d["metadata"].get("param")
    return r.status_code, err["code"], err["param"]


class StrictFake(FakeSpec):
    """A fake provider whose plan() refuses on request (`options.refuse`), crashes (`options.boom`), accepts model
    variants (`strict-video-5s`) and records what it was asked."""

    def __init__(self, name: str = "strict", frames=None):
        super().__init__(name, frames=frames)
        self.seen: list = []
        self.broken = False

    def catalog(self):
        if self.broken:
            raise RuntimeError("catalog down")
        return super().catalog()

    def plan(self, req, settings: Any, snapshots) -> Plan:
        self.seen.append(req)
        if req.options.get("boom"):
            raise ValueError("engine detail secret")
        if req.options.get("duck"):                     # carries a code but is not an InputError: a crash
            err = ValueError("duck")
            err.code, err.param = req.options["duck"], None  # type: ignore[attr-defined]
            raise err
        kind = "video" if "-video" in req.model else "image"
        plan = super().plan(dataclasses.replace(req, model="%s-%s" % (self.name, kind)), settings, snapshots)
        return dataclasses.replace(plan, model=req.model)


# ---- rows ---------------------------------------------------------------------------------------------------------
def charges(db, *, key_id=None, job_id=None) -> int:
    where, args = ("job_id = %s", [job_id]) if job_id else ("key_id = %s", [key_id])
    return db.one("SELECT count(*) FROM ledger WHERE entry = 'charge' AND scope = '*' AND " + where, *args)[0]


def jobs(db, key_id) -> list[tuple]:
    return db.all("SELECT id, status FROM jobs WHERE key_id = %s ORDER BY created_at", key_id)


def used(db, key_id, scope="*") -> int:
    return db.wallet(key_id, scope)[1]


def finish_done(db, job_id, n: int = 1) -> list[str]:
    """End a job as done with `n` shared outputs; returns their tokens."""
    kind = db.one("SELECT media_type FROM jobs WHERE id = %s", job_id)[0]
    db.run("UPDATE jobs SET status = 'done', finished_at = clock_timestamp() WHERE id = %s", job_id)
    tokens = []
    for i in range(n):
        tokens.append("tok-%s-%d" % (uuid.uuid4().hex, i))
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token) "
               "VALUES (%s, 'output', %s, %s, %s, 1, %s)", job_id, i, "%s/output/%d.bin" % (job_id, i),
               "video/mp4" if kind == "video" else "image/png", tokens[-1])
    return tokens


async def finish_failed(db, job_id, code: str = "render.deadline", detail: str = "", expect=("queued",)) -> None:
    """End a job as failed through the engine's own function (status, error, refund in one transaction)."""
    p = await open_pool(db.worker_dsn, max_size=2)
    try:
        assert await store.fail_job(p, job_id, expect=list(expect), code=code, detail=detail, pools=POOLS)
    finally:
        await p.close()


def finish_later(db, key_id, how: Callable, delay_s: float = 0.0, which: int = 0) -> asyncio.Task:
    """A task that ends the key's job number `which` (as it appears) with `how(db, job_id)`, awaitable or not."""

    async def run():
        while True:
            rows = jobs(db, key_id)
            if len(rows) > which:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(delay_s)
        result = how(db, rows[which][0])
        if asyncio.iscoroutine(result):
            await result

    return asyncio.create_task(run())


# ---- a real server, for streaming and disconnects -----------------------------------------------------------------
@contextlib.asynccontextmanager
async def live(app):
    """The app behind a real uvicorn on a free loopback port (httpx's ASGI transport buffers a whole response)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield "http://127.0.0.1:%d" % sock.getsockname()[1]
    finally:
        server.should_exit = True
        await task


# ---- real SDK request bodies --------------------------------------------------------------------------------------
def _recorder(seen: list) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"method": request.method, "path": request.url.path, "query": request.url.query.decode(),
                     "auth": "x-goog-api-key" if "x-goog-api-key" in request.headers else "authorization",
                     "body": json.loads(request.content)})
        return httpx.Response(400, json={"error": {"message": "recorded", "code": 400, "status": "INVALID_ARGUMENT"}})

    return httpx.MockTransport(handler)


def openai_request(**kwargs) -> dict:
    """The request the `openai` SDK sends for `chat.completions.create(**kwargs)`; nothing leaves the process."""
    from openai import OpenAI
    seen: list = []
    client = OpenAI(api_key="rk-sdk", base_url="http://relay.test/v1", max_retries=0,
                    http_client=httpx.Client(transport=_recorder(seen)))
    with contextlib.suppress(Exception):
        client.chat.completions.create(**kwargs)
    assert len(seen) == 1, "the SDK sent %d requests" % len(seen)
    return seen[0]


def genai_request(call: Callable[[Any], Any]) -> dict:
    """The request the `google-genai` SDK sends for `call(client)`; nothing leaves the process."""
    from google import genai
    from google.genai import types
    seen: list = []
    client = genai.Client(api_key="rk-sdk", http_options=types.HttpOptions(
        base_url="http://relay.test", httpx_client=httpx.Client(transport=_recorder(seen)),
        retry_options=types.HttpRetryOptions(attempts=1)))
    with contextlib.suppress(Exception):
        call(client)
    assert len(seen) == 1, "the SDK sent %d requests" % len(seen)
    return seen[0]


def golden(name: str, captured: dict) -> dict:
    """Compare a captured SDK request with the committed snapshot (`UPDATE_SNAPSHOTS=1` rewrites it). A new SDK
    version that sends something else fails here, on purpose: the doors must be shown to accept it."""
    path = SNAPSHOTS / (name + ".json")
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n")
    assert path.exists(), "no snapshot %s: run with UPDATE_SNAPSHOTS=1" % path.name
    assert json.loads(path.read_text()) == captured, "the SDK request changed since snapshot %s" % path.name
    return captured


async def replay(env, key, snap: dict, *, extra_headers: dict | None = None) -> httpx.Response:
    """Send a snapshot's request to the app with a real key, the way the SDK would (its auth header form)."""
    headers = {"x-goog-api-key": key.raw} if snap["auth"] == "x-goog-api-key" else key.headers
    url = snap["path"] + ("?" + snap["query"] if snap["query"] else "")
    return await env.client.post(url, json=snap["body"], headers={**headers, **(extra_headers or {})})


__all__ = ["IMAGE", "PNG", "PNG_URLSAFE_ONLY", "VIDEO", "StrictFake", "charges", "error_of",
           "finish_done", "finish_failed", "finish_later", "genai_request", "golden", "jobs", "live",
           "openai_request", "png_uri", "replay", "used"]
