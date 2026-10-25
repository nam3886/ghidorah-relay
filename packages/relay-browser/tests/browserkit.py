"""Shared helpers for the relay-browser suites: an account, a site profile that stays inside tmp_path, the real
redactor, and a sync wrapper for async tests. A site profile here is headless on a Mac (the Mac tier must not raise
windows) and headful elsewhere (the box runs Xvfb), as production is."""

import asyncio
import functools
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from relay_browser.session import SiteProfile
from relay_core.redact import redact_text

PLANTED_SECRET = "PLANTED-sessionid-9f8e7d6c5b4a39281706"


@dataclass(frozen=True)
class Account:
    id: str = "acct-1"
    credential_gen: int = 1
    proxy: object = None


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    auth: str | None = None


def site_for(tmp_path: Path, **overrides) -> SiteProfile:
    fields = dict(name="test", host="127.0.0.1", profile_root=str(tmp_path / "profiles"),
                  executable_path=os.environ.get("RELAY_TEST_CHROME") or None, headless=sys.platform == "darwin")
    return SiteProfile(**{**fields, **overrides})


def redact(text: str) -> str:
    return redact_text(text)


def sync(test):
    """Run an `async def` test to the end on its own event loop."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


async def settle(recorder, count: int, timeout: float = 10.0) -> None:
    """Wait until the recorder holds `count` ready events (response bodies are read in the background)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while len(recorder.events()) < count:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("recorder holds %d events, wanted %d" % (len(recorder.events()), count))
        await asyncio.sleep(0.05)


async def loop_gap_during(work, tick: float = 0.01):
    """Run the awaitable `work` while a heartbeat ticks every `tick` s on the same loop; returns (result, the longest
    time the loop went without getting to run the heartbeat, in seconds)."""
    loop = asyncio.get_running_loop()
    gaps: list[float] = []
    stop = asyncio.Event()

    async def beat():
        last = loop.time()
        while not stop.is_set():
            await asyncio.sleep(tick)
            now = loop.time()
            gaps.append(now - last - tick)
            last = now
    heart = asyncio.ensure_future(beat())
    await asyncio.sleep(tick * 3)            # the heartbeat is running before the work starts
    try:
        result = await work
    finally:
        stop.set()
        await heart
    return result, max(gaps)


def json_text(chars: int, seed: int = 1) -> str:
    """JSON-looking text with no secret in it, the shape of a captured reply: what a redactor spends its time on."""
    import random
    import string
    rng, parts, size = random.Random(seed), [], 0
    while size < chars:
        part = '"%s":"%s",' % ("".join(rng.choices(string.ascii_lowercase, k=8)),
                               "".join(rng.choices(string.ascii_letters + string.digits + " ", k=60)))
        parts.append(part)
        size += len(part)
    return ("{" + "".join(parts))[:chars]


def cookie_text(value_chars: int, start: int = 19_900) -> tuple[str, str]:
    """(text, first value word): a Cookie-Editor object whose value begins just before character 20 000, so the part of
    the value inside a 20 000-character print would show if the object were cut before it was redacted whole. The
    value is spaced words, which the long-token rule does not take: only the object rule can hide it."""
    value = " ".join("w%04d" % n for n in range(value_chars // 6))
    obj = '{"domain": ".s.test", "name": "sessionid", "value": "%s"}' % value
    return " " * start + obj, "w0000"


def big_redact(text: str) -> str:
    """The core redactor plus a provider-registered rule for a cookie object up to 15 000 characters."""
    import re
    text = redact_text(text)
    return re.sub(r"\{[^{}]{0,15000}\}", lambda m: re.sub(r'("value"\s*:\s*")[^"]*(")', r"\1[redacted]\2", m.group(0))
                  if '"sessionid"' in m.group(0) else m.group(0), text)
