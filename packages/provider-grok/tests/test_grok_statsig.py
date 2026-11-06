"""The dormant x-statsig-id generator: the spike's pinned vectors, the signature layout, the seed cache, `off` mode."""

import ast
import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path

import pytest
from grokkit import Clock, fixture, settings

from relay_core.testing.mutation import mutation
from relay_grok.constants import STATSIG_SEED_MAX_AGE_S
from relay_grok.engine import statsig
from relay_grok.engine.statsig import EPOCH, SALT, Statsig, factory, find_seed, hex_of, js_hex, sign

STATSIG_FILE = "packages/provider-grok/src/relay_grok/engine/statsig.py"
# The published seed/HEX pair the spike tried (data from the write-up, not code), and a synthetic page carrying it.
PUB_SEED = base64.b64decode("t2ODAFY4ozXd0K2Y8MdI2XfxTDiJoakZPuoaKfcQn8VuasZMcKliyhA1pJ+o1oMf")
PUB_HEX = "3bab9506b851eb851eb840e8f5c28f5c28f80e8f5c28f5c28f806b851eb851eb8400"
SEGMENT = {"color": [10, 20, 30, 200, 210, 220], "deg": 128, "bezier": [64, 128, 192, 32]}
CURVES = [[SEGMENT] * 4] * 4


def page(seed: bytes = PUB_SEED, curves=CURVES) -> str:
    meta = '<meta name="grok-site-verification" content="%s"/>' % base64.b64encode(seed).decode().rstrip("=")
    body = "" if curves is None else "<script>self.__next_f.push(%s)</script>" % json.dumps(
        json.dumps({"curves": curves}, separators=(",", ":")))
    return "<html><head>%s</head><body>%s</body></html>" % (meta, body)


class Pages:
    def __init__(self, html: str, chunks=()):
        self.html, self.chunks, self.reads = html, list(chunks), 0

    async def __call__(self):
        self.reads += 1
        return self.html, self.chunks


def test_js_hex_matches_the_published_pairs():
    assert js_hex(0.96) == "0.f5c28f5c28f5c"
    assert js_hex(0.42) == "0.6b851eb851eb84"
    assert js_hex(0.91) == "0.e8f5c28f5c28f8"
    assert js_hex(255) == "ff" and js_hex(-0.5) == "-0.8" and js_hex(0) == "0"
    # the published HEX is the colour 3b ab 95 and the rotation 0.42 / 0.91 / -0.91 / 0.42, then 0 0
    assert hex_of([59, 171, 149, 0.42, 0.91, -0.91, 0.42, 0, 0]) == PUB_HEX


def test_sign_is_deterministic_with_a_frozen_clock_and_key():
    now = EPOCH + 123_456_789
    value = sign("POST", "/rest/app-chat/conversations/new", PUB_SEED, PUB_HEX, now_s=now, key=0x5A)
    assert value == sign("POST", "/rest/app-chat/conversations/new", PUB_SEED, PUB_HEX, now_s=now, key=0x5A)
    assert "=" not in value
    raw = base64.b64decode(value + "=" * (-len(value) % 4))
    assert len(raw) == 1 + 48 + 4 + 16 + 1 and raw[0] == 0x5A
    plain = bytes(b ^ 0x5A for b in raw[1:])
    assert plain[:48] == PUB_SEED
    assert int.from_bytes(plain[48:52], "little") == 123_456_789
    want = hashlib.sha256(f"POST!/rest/app-chat/conversations/new!123456789{SALT}{PUB_HEX}".encode()).digest()[:16]
    assert plain[52:68] == want and plain[68:] == b"\x03"
    assert value != sign("POST", "/rest/media/imagine/quota_info", PUB_SEED, PUB_HEX, now_s=now, key=0x5A)
    assert value != sign("POST", "/rest/app-chat/conversations/new", PUB_SEED, PUB_HEX, now_s=now, key=0x5B)


def test_find_seed_reads_the_meta_and_the_curves_from_the_page_or_a_chunk():
    seed = find_seed(page(), [])
    assert seed is not None and seed.seed == PUB_SEED
    assert seed.hex and set(seed.hex) <= set("0123456789abcdef")
    chunk = "var x={\"curves\":%s};" % json.dumps(CURVES)
    assert find_seed(page(curves=None), [chunk]) == seed
    assert find_seed(page(curves=None), []) is None                 # no curves anywhere
    assert find_seed(page(seed=PUB_SEED[:40]), []) is None          # a seed of the wrong size
    assert find_seed(page(curves=[[{"color": [1]}]]), []) is None   # curves of another shape
    assert find_seed(fixture("page_home.html"), []) is None         # the real page of 06/11 has neither


def test_no_seed_gives_no_header_and_one_log_line(caplog):
    pages, clock = Pages(fixture("page_home.html")), Clock()
    sig = Statsig(pages, clock)
    with caplog.at_level(logging.INFO, logger="relay_grok.statsig"):
        assert asyncio.run(sig.header("POST", "/rest/media/imagine/quota_info")) is None
        assert asyncio.run(sig.header("POST", "/rest/app-chat/conversations/new")) is None
    assert pages.reads == 1
    assert [r.getMessage() for r in caplog.records] == ["statsig seed not found"]


def test_a_found_seed_signs_with_the_wall_clock_and_key():
    sig = Statsig(Pages(page()), Clock(), wall=lambda: EPOCH + 99.7, key=lambda: 7)
    value = asyncio.run(sig.header("POST", "/x"))
    seed = find_seed(page(), [])
    assert value == sign("POST", "/x", seed.seed, seed.hex, now_s=EPOCH + 99, key=7)


@mutation(STATSIG_FILE, "self.clock() - self._read_at >= self.max_age_s:",
          "self.clock() - self._read_at >= self.max_age_s * 1000:", expect="the seed must be reread after max age")
def test_the_seed_is_reread_after_max_age():
    pages, clock = Pages(fixture("page_home.html")), Clock()
    sig = Statsig(pages, clock)
    asyncio.run(sig.header("GET", "/"))
    clock.advance(STATSIG_SEED_MAX_AGE_S - 1)
    asyncio.run(sig.header("GET", "/"))
    assert pages.reads == 1, "a fresh seed was read again"
    clock.advance(1)
    pages.html = page()
    assert asyncio.run(sig.header("GET", "/")) is not None, "the seed must be reread after max age"
    assert pages.reads == 2


@mutation(STATSIG_FILE, 'if settings.get(PROVIDER + ".statsig_mode") == "off":',
          'if settings.get(PROVIDER + ".statsig_mode") != "off":', expect="statsig off must send no header")
def test_off_mode_builds_no_generator():
    pages = Pages(page())
    assert factory(settings(), pages) is None, "statsig off must send no header"
    gen = factory(settings(statsig_mode="generate"), pages, clock=Clock())
    assert gen is not None and asyncio.run(gen("POST", "/x")) is not None
    assert pages.reads == 1


@pytest.mark.parametrize("banned", ["curl_cffi", "httpx", "requests", "aiohttp", "websockets", "urllib"])
def test_the_module_imports_no_network_library(banned):
    tree = ast.parse(Path(statsig.__file__).read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert banned not in names
