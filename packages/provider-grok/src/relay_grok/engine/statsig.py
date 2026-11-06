"""The `x-statsig-id` header generator, dormant: `grok.statsig_mode = off` (the default) builds none.

Ported from the 06/11 spike's own `g.py` (`Statsig`, `js_hex`), which was written from the published description of
the algorithm, not from any other project's code: SHA-256 of `METHOD!PATH!<seconds since EPOCH><SALT><HEX>`, a 48-byte
seed from the page's `grok-site-verification` meta, the HEX from one of the page's SVG `"curves"`, all XORed with a
one-byte key. On 06/11 grok.com checked no such header and the page carried no seed, so `generate` sends nothing today
and logs "statsig seed not found" once per page read. Never calls an outside signer. No network library here: the
page comes from the `fetch_page` the engine hands in."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, NamedTuple

from relay_grok.constants import PROVIDER, STATSIG_SEED_MAX_AGE_S

log = logging.getLogger("relay_grok.statsig")

EPOCH = 1682924400          # the algorithm's time origin (protocol fact, not a tunable)
SALT = "obfiowerehiring"
SEED_BYTES = 48
TAIL_MARK = b"\x03"
_DASH = "[‐‑‒–—―-]"
_META = (re.compile(rf"<meta[^>]*name=[\"']grok{_DASH}site{_DASH}verification[\"'][^>]*content=[\"']([^\"']+)"),
         re.compile(rf"content=[\"']([^\"']+)[\"'][^>]*name=[\"']grok{_DASH}site{_DASH}verification"))
_CURVES = '"curves":'


class Seed(NamedTuple):
    seed: bytes   # 48 bytes
    hex: str      # the animation HEX the seed selects


def js_hex(v: float) -> str:
    """JS `Number.prototype.toString(16)` (V8 DoubleToRadixCString)."""
    if v == 0:
        return "0"
    neg = v < 0
    v = abs(v)
    integer = math.floor(v)
    frac = v - integer
    delta = max(0.5 * (math.nextafter(v, math.inf) - v), math.nextafter(0.0, 1.0))
    digits: list[int] = []
    if frac >= delta:
        while True:
            frac *= 16
            delta *= 16
            d = int(frac)
            digits.append(d)
            frac -= d
            if frac > 0.5 or (frac == 0.5 and (d & 1)):
                if frac + delta > 1:
                    while True:  # round up
                        if not digits:
                            integer += 1
                            break
                        last = digits.pop() + 1
                        if last < 16:
                            digits.append(last)
                            break
                    break
            if not frac >= delta:
                break
    out = format(int(integer), "x")
    if digits:
        out += "." + "".join("0123456789abcdef"[d] for d in digits)
    return ("-" if neg else "") + out


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _to_fixed(v: float, p: int = 2) -> float:
    return float(f"{v:.{p}f}")  # ponytail: Python rounding == JS toFixed for these magnitudes (|v| <= 255)


def _bezier_y(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0

    def f(t, a1, a2):
        return ((1 - 3 * a2 + 3 * a1) * t + (3 * a2 - 6 * a1)) * t * t + 3 * a1 * t

    def df(t, a1, a2):
        return (3 * (1 - 3 * a2 + 3 * a1) * t + 2 * (3 * a2 - 6 * a1)) * t + 3 * a1

    t = x
    for _ in range(8):
        e = f(t, x1, x2) - x
        if abs(e) < 1e-7:
            return f(t, y1, y2)
        d = df(t, x1, x2)
        if abs(d) < 1e-7:
            break
        t -= e / d
    lo, hi, t = 0.0, 1.0, x
    while lo < hi:
        xt = f(t, x1, x2)
        if abs(xt - x) < 1e-7:
            break
        lo, hi = (t, hi) if x > xt else (lo, t)
        t = (hi + lo) / 2
    return f(t, y1, y2)


def hex_of(values: Iterable[float]) -> str:
    """The HEX string: each value rounded to 2 places, in JS hex, with every `.` and `-` dropped."""
    return re.sub(r"[.-]", "", "".join(js_hex(_to_fixed(v)) for v in values))


def _anim_hex(curve: list[dict[str, Any]], seed: bytes) -> str:
    """`curve` = a list of `{color[6], deg, bezier[4]}` segments (one of the page's curves)."""
    c = curve[seed[5] % len(curve)]
    seg = [float(x) for x in (*c["color"], c["deg"], *c["bezier"])]

    def sc(n, lo, hi):
        return _to_fixed(n * ((hi - lo) / 255) + lo)

    x1, y1, x2, y2 = sc(seg[7], 0, 1), sc(seg[8], -1, 1), sc(seg[9], 0, 1), sc(seg[10], -1, 1)
    seek = _js_round(((seed[24] % 16) * (seed[22] % 16) * (seed[23] % 16)) / 10) * 10
    p = _bezier_y(x1, y1, x2, y2, seek / 4096.0)

    def ch(a, b):
        return max(0, min(255, _js_round(a + (b - a) * p)))

    ang = math.floor(seg[6] * (300 / 255) + 60) * p * math.pi / 180
    return hex_of([ch(seg[0], seg[3]), ch(seg[1], seg[4]), ch(seg[2], seg[5]),
                   math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang), 0, 0])


def sign(method: str, path: str, seed: bytes, hex_: str, *, now_s: int, key: int) -> str:
    """One header value: `key`, then the seed and `<n LE32><sha256[:16]><0x03>` each XORed with `key`, base64 with no
    padding; `n` = `now_s - EPOCH`."""
    n = now_s - EPOCH
    sha = hashlib.sha256(f"{method}!{path}!{n}{SALT}{hex_}".encode()).digest()
    tail = n.to_bytes(4, "little") + sha[:16] + TAIL_MARK
    out = bytes([key]) + bytes(b ^ key for b in seed) + bytes(b ^ key for b in tail)
    return base64.b64encode(out).decode().rstrip("=")


def _curves(text: str) -> list | None:
    blob = text.replace('\\"', '"')
    i = blob.find(_CURVES + "[")
    if i < 0:
        return None
    try:
        curves, _ = json.JSONDecoder().raw_decode(blob[i + len(_CURVES):])
    except ValueError:
        return None
    return curves if isinstance(curves, list) and curves else None


def find_seed(page_html: str, chunks: Iterable[str]) -> Seed | None:
    """The seed from the page's `grok-site-verification` meta and the HEX from its `"curves"` (in the page or a chunk),
    or None when either is missing or unusable."""
    m = _META[0].search(page_html) or _META[1].search(page_html)
    if not m:
        return None
    text = m.group(1)
    try:
        seed = base64.b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(seed) != SEED_BYTES:
        return None
    curves = _curves(page_html) or next((c for c in map(_curves, chunks) if c), None)
    if curves is None:
        return None
    try:
        return Seed(seed, _anim_hex(curves[seed[5] % len(curves)], seed))
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return None


class Statsig:
    """Signs requests with the seed read from the page, reread every `max_age_s` (a missing seed is cached as well, so
    a page without one is read once per `max_age_s`, not once per request)."""

    def __init__(self, fetch_page: Callable[[], Awaitable[tuple[str, list[str]]]], clock: Callable[[], float],
                 max_age_s: float = STATSIG_SEED_MAX_AGE_S, *, wall: Callable[[], float] = time.time,
                 key: Callable[[], int] = lambda: secrets.randbits(8)):
        self.fetch_page, self.clock, self.max_age_s, self.wall, self.key = fetch_page, clock, max_age_s, wall, key
        self._seed: Seed | None = None
        self._read_at: float | None = None

    async def header(self, method: str, path: str) -> str | None:
        if self._read_at is None or self.clock() - self._read_at >= self.max_age_s:
            html, chunks = await self.fetch_page()
            self._seed, self._read_at = find_seed(html, chunks), self.clock()
            if self._seed is None:
                log.warning("statsig seed not found")
        if self._seed is None:
            return None
        return sign(method, path, self._seed.seed, self._seed.hex, now_s=int(self.wall()), key=self.key())


def factory(settings: Any, fetch_page: Callable[[], Awaitable[tuple[str, list[str]]]], *,
            clock: Callable[[], float] = time.monotonic) -> Callable[[str, str], Awaitable[str | None]] | None:
    """`Statsig(...).header` when `grok.statsig_mode` is `generate`; None (no header at all) when it is `off`."""
    if settings.get(PROVIDER + ".statsig_mode") == "off":
        return None
    return Statsig(fetch_page, clock).header
