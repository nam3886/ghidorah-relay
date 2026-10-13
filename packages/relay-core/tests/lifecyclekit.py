"""Helpers for the poller, fetcher, reaper and reconcile tests: jobs driven to a state, loops with test timings, and a
fake engine that serves real (tiny) media files and records what its ctx carried."""

import asyncio
import functools
import os
import subprocess
import tempfile
import uuid
import zlib
from collections import deque

from enginekit import until  # noqa: F401  (re-exported for the tests)
from jobkit import SPEC, claim, create, submit

from relay_core.jobs import store
from relay_core.jobs.fetcher import Fetcher
from relay_core.jobs.poller import Poller
from relay_core.jobs.reaper import Reaper
from relay_core.jobs.reconcile import Reconciler
from relay_core.providers import Accepted, AccountView, Ambiguous, Completed, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSettings, sent


async def load_account(account_id) -> AccountView:
    return AccountView(str(account_id), "acct", b"", 1, None, {}, {})


@functools.cache
def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(3))           # a 4 x 3 red picture
    header = (4).to_bytes(4, "big") + (3).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@functools.cache
def mp4() -> bytes:
    """A real 0.5 s, 64 x 48 video made with ffmpeg once per test process."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=0.5",
                        "-pix_fmt", "yuv420p", path], check=True)
        with open(path, "rb") as f:
            return f.read()


class MediaEngine(FakeEngine):
    """FakeEngine whose `fetch` serves a real file of the output's mime. `fetch_modes` is consumed one per output:
    ok | error | empty | garbage (the last repeats). It also records the browser token each poll and reconcile saw."""

    def __init__(self) -> None:
        super().__init__()
        self.fetch_modes: deque[str] = deque()
        self.tokens: list[object] = []

    def mode(self, *modes: str) -> None:
        self.fetch_modes.extend(modes)

    async def fetch(self, ctx, out):
        mode = (self.fetch_modes.popleft() if len(self.fetch_modes) > 1 else
                self.fetch_modes[0] if self.fetch_modes else "ok")
        if mode == "error":
            raise ConnectionError("upstream said 502")
        if mode == "empty":
            return
        if mode == "garbage":
            yield b"this is not a media file" * 20
            return
        data = mp4() if out.mime.startswith("video/") else png()
        for i in range(0, len(data), 1024):
            yield data[i:i + 1024]

    async def poll(self, ctx, job):
        self.tokens.append(ctx.browser_token)
        return await super().poll(ctx, job)

    async def reconcile(self, ctx, job):
        self.tokens.append(ctx.browser_token)
        return await super().reconcile(ctx, job)


# ---- loops with test timings ---------------------------------------------------------------------------------------
def poller(p, engine, *, spec=SPEC, settings=None, **kw) -> Poller:
    return Poller(p, spec, engine, settings or FakeSettings(spec), owner="w1", load_account=load_account,
                  tick_s=0.02, **kw)


def fetcher(p, engine, objects, *, spec=SPEC, settings=None, **kw) -> Fetcher:
    kw.setdefault("retry_s", 1.5)
    return Fetcher(p, spec, engine, settings or FakeSettings(spec), owner="w1", load_account=load_account,
                   objects=objects, tick_s=0.02, **kw)


def reconciler(p, engine, *, spec=SPEC, settings=None, **kw) -> Reconciler:
    kw.setdefault("lease_beat_s", 0.1)
    return Reconciler(p, spec, engine, settings or FakeSettings(spec), owner="w1", load_account=load_account,
                      tick_s=0.02, **kw)


def reaper(p, *, spec=SPEC, settings=None, owner_gone_s: float = 0.0, grace=None) -> Reaper:
    """`owner_gone_s=0`: a lease that expired seconds ago belongs to a dead owner even when its worker's heartbeat row
    is fresh (the tests' lease TTLs are seconds; a real worker's row is older than the bound by the time it matters)."""
    return Reaper(p, spec, settings or FakeSettings(spec), interval_s=0.05, owner_gone_s=owner_gone_s,
                  grace=grace)


async def pass_once(loop) -> None:
    """One tick of a poller, fetcher or reconciler and everything it started."""
    await loop.tick()
    await loop.idle()


# ---- jobs driven to a state ----------------------------------------------------------------------------------------
async def to_waiting(p, k, *, prompt="x", engine=None, **opts) -> str:
    """A job accepted upstream: `waiting`, with its first poll not yet due."""
    got = await claim_new(p, k, prompt, **opts)
    await submit(p, got, engine or FakeEngine())
    return got.job_id


async def to_fetching(p, k, *, prompt="x", n=1, **opts) -> str:
    """A hold job whose submit completed in the session with `n` outputs: `fetching`, due now."""
    got = await claim_new(p, k, prompt, completion="hold", **opts)
    engine = FakeEngine()
    outs = engine.outputs(await store.job_view(p, got.job_id), n)
    engine.script_submit("*", sent(SubmitResult(Completed(outs))))
    await submit(p, got, engine)
    return got.job_id


async def to_unknown(p, k, *, prompt="x", **opts) -> str:
    """A job sent whose outcome is unknown, reconcile due after 60 s."""
    got = await claim_new(p, k, prompt, **opts)
    engine = FakeEngine()
    engine.script_submit("*", sent(SubmitResult(Ambiguous("no answer"))))
    await submit(p, got, engine)
    return got.job_id


async def claim_new(p, k, prompt, *, owner="w1", **opts):
    created = await create(p, k, prompt, **opts)
    got = await claim(p, owner=owner)
    assert got is not None and got.job_id == created.job_id
    return got


def accepted(ref: str | None = None) -> SubmitResult:
    return SubmitResult(Accepted(ref or "ref-%s" % uuid.uuid4().hex[:8]))


def due(db, job_id) -> None:
    """The job's next poll or reconcile is due now."""
    db.run("UPDATE jobs SET next_poll_at = clock_timestamp() - interval '1 second' WHERE id = %s", job_id)


def age(db, job_id, column: str, seconds: float, *, answered: bool = True) -> None:
    """Move a timestamp column of the job into the past (a deadline test without waiting for it). Aging the render
    clock (`render_since`, `sent_at`) also records an answered read just now, so the job is past its deadline AND has
    had its final read; `answered=False` leaves the final read still to come."""
    assert column in ("created_at", "render_since", "sent_at")
    db.run("UPDATE jobs SET %s = clock_timestamp() - make_interval(secs => %%s) WHERE id = %%s" % column,
           seconds, job_id)
    if answered and column != "created_at":
        db.run("UPDATE jobs SET answered_at = clock_timestamp() WHERE id = %s", job_id)


def late_seen(db, job_id, seconds: float) -> None:
    """A running worker's reaper first saw the job late `seconds` ago (it stamps `jobs.late_seen_at`)."""
    db.run("UPDATE jobs SET late_seen_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
           seconds, job_id)


def answered_ago(db, job_id, seconds: float) -> None:
    """The job's newest answered read started `seconds` ago: a live job is read every 30 s before its deadline, so
    this is the shape of every real late job (a read from BEFORE the deadline is not the final read)."""
    db.run("UPDATE jobs SET answered_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
           seconds, job_id)


def age_found(db, job_id, seconds: float) -> None:
    """Move the moment the video was found (the job's `completed` events) into the past, with `age`: a video found
    before the deadline, not by a final read after it."""
    db.run("UPDATE job_events SET at = at - make_interval(secs => %s) WHERE job_id = %s AND stage = 'completed'",
           seconds, job_id)


def row(db, job_id, names: str):
    return db.one("SELECT %s FROM jobs WHERE id = %%s" % names, job_id)


async def settle(seconds: float = 0.05) -> None:
    await asyncio.sleep(seconds)
