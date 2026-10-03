"""Helpers for the crash-window tests: run a short script in a real subprocess with a failpoint armed to
`os._exit(137)`, so the process dies mid-write exactly like a `kill -9`, and wait for what a dead worker leaves
behind to expire."""

import os
import subprocess
import sys
import time
from pathlib import Path

from relay_core.testing.checker import violations

TESTS = Path(__file__).resolve().parent

CLAIM_LOOP = """
import asyncio, os
from enginekit import make_loop
from relay_core.db import open_pool
from relay_core.testing.fake_provider import FakeEngine

async def main():
    p = await open_pool(os.environ["T_DSN"], max_size=4)
    loop = make_loop(p, FakeEngine(), lease_ttl_s=0.4, lease_beat_s=0.1)
    asyncio.create_task(loop.run())
    await asyncio.sleep(30)
    raise SystemExit("the failpoint did not fire")

asyncio.run(main())
"""

FETCHER = """
import asyncio, os
from lifecyclekit import MediaEngine, fetcher, pass_once
from relay_core.db import open_pool
from relay_core.storage import ObjectStore

async def main():
    p = await open_pool(os.environ["T_DSN"], max_size=4)
    store = ObjectStore(os.environ["T_MINIO"], os.environ["T_AK"], os.environ["T_SK"], os.environ["T_BUCKET"])
    await pass_once(fetcher(p, MediaEngine(), store))
    raise SystemExit("the failpoint did not fire")

asyncio.run(main())
"""

# The receiver got the request and the sender dies before it can record any answer: the 2xx came too late to mark.
WEBHOOK_SENDER = """
import asyncio, os
import httpx
from hookkit import resolver
from relay_core.db import open_pool
from relay_core.jobs import store

def handler(request):
    with open(os.environ["T_SEEN"], "w") as f:
        f.write(request.headers["x-relay-event-id"])
    os._exit(137)

async def main():
    p = await open_pool(os.environ["T_DSN"], max_size=4)
    await store.deliver_due(p, resolver=resolver, transport=httpx.MockTransport(handler))
    raise SystemExit("the sender survived")

asyncio.run(main())
"""


def crash_child(code: str, dsn: str, failpoint: str | None = None, **env_extra: str) -> int:
    """Run `code` in a subprocess against `dsn`; with `failpoint` armed to crash. Returns the exit status."""
    # the parent's own PYTHONPATH stays behind the kits: scripts/mutate.py puts its mutated copy of the sources there
    path = os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH", "")]))
    env = {**os.environ, "T_DSN": dsn, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1", **env_extra}
    if failpoint:
        env["RELAY_FAILPOINTS"] = failpoint + "=crash"
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=90)
    if done.returncode not in (0, 137):
        raise AssertionError("the child failed: %s" % done.stderr[-2000:])
    return done.returncode


def wait_for(condition, *, timeout_s: float = 6.0, what: str = "the condition") -> None:
    """Poll a sync predicate every 50 ms (the dead worker's lease needs its short TTL to run out)."""
    end = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("timed out waiting for %s" % what)
        time.sleep(0.05)


def strict(db) -> list[str]:
    """The checker's strict tier over everything the platform wrote (what the platform's own writers promise)."""
    with db.connect() as conn:
        return violations(conn, strict=True)
