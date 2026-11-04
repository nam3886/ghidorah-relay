"""The sentinel requirements token and proof of work, as the live sdk.js 20260810913b builds them."""

import base64
import json
from itertools import count

from relay_core.testing.mutation import mutation
from relay_gpt.constants import USER_AGENT
from relay_gpt.engine.pow import FAILURE_PREFIX, config, fnv1a_fmix, requirements_token, solve

POW_FILE = "packages/provider-gpt/src/relay_gpt/engine/pow.py"
FMIX = "h = (h * 2246822507) & 0xFFFFFFFF"
FMIX_WRONG = "h = (h * 2246822519) & 0xFFFFFFFF"
MEETS = "<= difficulty"
MEETS_STRICT = "< difficulty"
SDK_URL = "https://chatgpt.com/sentinel/20260810913b/sdk.js"
NOW = 1_793_800_000.0          # 2026-11-04 UTC


def cfg():
    return config("did-1", "build-1", SDK_URL, now=NOW, rand=lambda: 0.25)


def decode(token: str, prefix: str) -> list:
    assert token.startswith(prefix) and token.endswith("~S")
    return json.loads(base64.b64decode(token[len(prefix):-2]))


def fixed_clock():
    return 100.0


@mutation(POW_FILE, FMIX, FMIX_WRONG, expect="fnv vector")
def test_fnv1a_fmix_known_vectors():
    # pinned once from the spike's sentinel.fnv (plans/gpt-spike-261104/sentinel.py)
    got = [fnv1a_fmix(""), fnv1a_fmix("a"), fnv1a_fmix("seed-0.123gAAAA")]
    assert got == ["ab3e7c0b", "1a80b1b3", "b76cc8ca"], "fnv vector"


def test_requirements_token_shape_and_25_fields():
    c = cfg()
    fields = decode(requirements_token(c), "gAAAAAC")
    assert len(fields) == 25 and fields == c
    assert fields[4] == USER_AGENT and fields[5] == SDK_URL and fields[6] == "build-1" and fields[14] == "did-1"
    assert fields[1].endswith(" GMT+0700 (Indochina Time)") and fields[1].startswith("Wed Nov 04 2026 ")
    assert 3000 <= fields[13] <= 60000 and fields[17] == NOW * 1000 - fields[13]
    assert fields[18:] == [0] * 7


@mutation(POW_FILE, MEETS, MEETS_STRICT, expect="the solution must meet the difficulty")
def test_solve_finds_a_token_whose_hash_meets_the_difficulty():
    token, tries = solve("seed-x", "0fffff", cfg(), clock=fixed_clock)
    fields = decode(token, "gAAAAAB")
    assert tries >= 0 and fields[3] == tries and fields[9] == 0
    assert fnv1a_fmix("seed-x" + token[7:-2])[:6] <= "0fffff"
    # a difficulty equal to the first try's hash prefix is met by that try (the SDK compares with <=)
    first = cfg()
    first[3], first[9] = 0, 0
    enc = base64.b64encode(json.dumps(first, separators=(",", ":"), ensure_ascii=False).encode()).decode()
    exact = fnv1a_fmix("seed-y" + enc)[:4]
    assert solve("seed-y", exact, cfg(), clock=fixed_clock)[1] == 0, "the solution must meet the difficulty"


def test_exhaustion_returns_the_sdk_failure_token():
    assert solve("seed", "000000", cfg(), clock=fixed_clock, max_tries=3) == (
        "gAAAAAB" + FAILURE_PREFIX + "ImUi~S", -1)


def test_no_wall_clock_in_solve():
    ticks = count()
    a = solve("seed-z", "00ffff", cfg(), clock=lambda: next(ticks) / 1000)
    ticks = count()
    b = solve("seed-z", "00ffff", cfg(), clock=lambda: next(ticks) / 1000)
    assert a == b and a[1] >= 0
    assert decode(a[0], "gAAAAAB")[9] == a[1] + 1   # elapsed ms comes from the given clock only


def test_solve_leaves_the_given_config_alone():
    c = cfg()
    solve("seed", "0fffff", c, clock=fixed_clock)
    assert c == cfg()
