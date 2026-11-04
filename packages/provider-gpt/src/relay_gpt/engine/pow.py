"""The sentinel requirements token and proof of work, as the live sdk.js 20260810913b builds them (pure).

The SDK hashes with FNV-1a 32-bit plus a murmur3 finaliser (`Math.imul`), not SHA3; its answer is
`"gAAAAAB" + base64(config JSON) + "~S"` once the hash's prefix is <= the difficulty, within 500,000 tries
(plans/gpt-spike-261104.md, "Anti-bot"). Time and randomness come in as arguments, so a run is reproducible.
"""

import base64
import json
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from relay_gpt.constants import LANGUAGE, TIMEZONE, USER_AGENT

POW_MAX_TRIES = 500_000
FAILURE_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"   # the SDK's answer when no try met the difficulty
SCREEN_SUM = 1512 + 982                             # screen width + height
TZ_NAME = "Indochina Time"                          # the JS Date's zone name for TIMEZONE
PERF_MIN_MS, PERF_MAX_MS = 3000, 60000              # performance.now() of a page open a few seconds to a minute
NAVIGATOR_PROBES = ("webdriver−false", "geolocation−[object Geolocation]", "mediaDevices−[object MediaDevices]")
DOCUMENT_PROBES = ("location", "_reactListening8in7sfyhjvp")
WINDOW_PROBES = ("onfocus", "requestIdleCallback", "fetch")


def fnv1a_fmix(text: str) -> str:
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 2246822507) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 3266489909) & 0xFFFFFFFF
    h ^= h >> 16
    return format(h, "08x")


def _b64(cfg: list) -> str:
    return base64.b64encode(json.dumps(cfg, separators=(",", ":"), ensure_ascii=False).encode()).decode()


def _pick(options: tuple, rand: Callable[[], float]) -> str:
    return options[min(int(rand() * len(options)), len(options) - 1)]


def config(device_id: str, build: str, sdk_url: str, *, now: float, rand: Callable[[], float]) -> list:
    """The SDK's 25-field browser config; `now` is epoch seconds, `rand` gives floats in [0, 1)."""
    date = datetime.fromtimestamp(now, ZoneInfo(TIMEZONE)).strftime("%a %b %d %Y %H:%M:%S GMT%z")
    perf = PERF_MIN_MS + rand() * (PERF_MAX_MS - PERF_MIN_MS)
    return [SCREEN_SUM, f"{date} ({TZ_NAME})", 4294967296, 1, USER_AGENT, sdk_url, build, LANGUAGE,
            f"{LANGUAGE},{LANGUAGE.split('-')[0]}", rand(), _pick(NAVIGATOR_PROBES, rand),
            _pick(DOCUMENT_PROBES, rand), _pick(WINDOW_PROBES, rand), perf, device_id, "", 8, now * 1000 - perf,
            0, 0, 0, 0, 0, 0, 0]


def requirements_token(cfg: list) -> str:
    return "gAAAAAC" + _b64(cfg) + "~S"


def solve(seed: str, difficulty: str, cfg: list, *, clock: Callable[[], float],
          max_tries: int = POW_MAX_TRIES) -> tuple[str, int]:
    """The proof token and the tries it took; the SDK's failure token and -1 when none met the difficulty."""
    cfg, t0 = list(cfg), clock()
    for i in range(max_tries):
        cfg[3] = i
        cfg[9] = round((clock() - t0) * 1000)
        enc = _b64(cfg)
        if fnv1a_fmix(seed + enc)[:len(difficulty)] <= difficulty:
            return "gAAAAAB" + enc + "~S", i
    return "gAAAAAB" + FAILURE_PREFIX + base64.b64encode(b'"e"').decode() + "~S", -1
