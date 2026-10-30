"""The spec: catalog and price keys, the plan (what it takes and what it refuses), settings, codes and credentials."""

import json

import pytest

from relay_core import errors as core_errors
from relay_core.providers import CreateRequest, InputError, MediaIn, ProviderRegistry
from relay_core.testing.mutation import mutation
from relay_muse import catalog as C
from relay_muse.credential import jar_of, parse_credential, with_cookies
from relay_muse.errors import CODES
from relay_muse.spec import POOL_DEFAULTS, SPEC

SPEC_FILE = "packages/provider-muse/src/relay_muse/spec.py"
CAT_FILE = "packages/provider-muse/src/relay_muse/catalog.py"
CRED_FILE = "packages/provider-muse/src/relay_muse/credential.py"
S = {k: v.default for k, v in SPEC.settings().items()}
WEBP = MediaIn("a" * 64, "image/webp", 535500)


def plan(model="muse-image", prompt="a red fox in fresh snow", **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), S, {})


def refused(**kw) -> str | None:
    try:
        plan(**kw)
    except InputError as e:
        return e.code
    return None


def test_the_spec_registers_and_its_codes_are_sound():
    registry = ProviderRegistry()
    registry.register(SPEC)
    assert registry.resolve("muse-image")[0] is SPEC and registry.resolve("muse-video")[0] is SPEC
    assert not core_errors.violations({**core_errors.RULES, **CODES})
    assert set(CODES) == {"muse.http_error", "muse.vm_not_ready", "muse.refused", "muse.output_missing",
                          "muse.video_unavailable_on_accounts"}
    region = CODES["muse.video_unavailable_on_accounts"]
    assert region.terminal and region.refund and region.public_code == core_errors.TEMPORARILY_UNAVAILABLE
    assert all(core_errors.RULES[c] is r for c, r in CODES.items())
    for rule in CODES.values():         # the reference's sentence
        assert rule.public_text.endswith(".") and "again" in rule.public_text and rule.public_code != rule.code
    assert SPEC.caps.execution == "http" and SPEC.caps.account_slots == 1 and SPEC.caps.has_reconcile
    assert SPEC.ref_is_conversation and SPEC.frames.verbatim


REFUSAL_SETTING = "    refusal_max_accounts_setting(\n        PROVIDER, \"How many accounts one muse job"
REGION_REST = '    _setting("video_region_rest_s", int, 7 * 86400,'


@mutation(SPEC_FILE, REFUSAL_SETTING,
          "    (lambda *a: _setting(\"unused\", int, 1, \"x\"))(\n        PROVIDER, \"How many accounts one muse job",
          expect="muse.refusal_max_accounts")
@mutation(SPEC_FILE, REGION_REST, '    _setting("video_region_rest_s", int, 86400,', expect="a week")
def test_the_account_refusal_settings_are_declared_with_their_bounds():
    settings = SPEC.settings()
    assert "muse.refusal_max_accounts" in settings, "muse.refusal_max_accounts"
    cap = settings["muse.refusal_max_accounts"]
    assert (cap.type, cap.default, cap.min, cap.max) == (int, 2, 1, 9)
    assert "muse.video_unavailable_on_accounts" in cap.description
    rest = settings["muse.video_region_rest_s"]
    assert (rest.type, rest.default) == (int, 7 * 86400), "a week"
    assert rest.min == 3600 and rest.max == 30 * 86400


@mutation(CAT_FILE, "DEFAULT_COSTS = {**{image_key(n): n for n", "DEFAULT_COSTS = {**{image_key(n): 1 for n",
          expect="one use per output")
def test_every_output_costs_one_use_and_sells_at_one_point():
    assert C.PRICE_KEYS == ("muse-image", "muse-image@n2", "muse-image@n3", "muse-image@n4", "muse-video@5s",
                            "muse-video@10s")
    assert C.DEFAULT_COSTS == {"muse-image": 1, "muse-image@n2": 2, "muse-image@n3": 3, "muse-image@n4": 4,
                               "muse-video@5s": 1, "muse-video@10s": 1}, "one use per output"
    assert C.DEFAULT_PRICES == C.DEFAULT_COSTS
    image, video = C.catalog()
    assert image.price_keys == {"n=1": "muse-image", "n=2": "muse-image@n2", "n=3": "muse-image@n3",
                                "n=4": "muse-image@n4"}
    assert video.price_keys == {"5s": "muse-video@5s", "10s": "muse-video@10s"}
    assert image.limits["max_images"] == 0 and video.limits["max_images"] == 1


def test_the_pools_count_outputs_in_a_week_from_muses_reset():
    for pool, budget in (("image", 400), ("video", 40)):
        (window,) = POOL_DEFAULTS[pool]["windows"]
        assert window == {"name": "week", "kind": "rolling", "hours": 168, "anchor": "reset",
                          "budget": {"free": budget}}
    assert [(p.name, p.unit) for p in SPEC.caps.pools] == [("image", "uses"), ("video", "uses")]


def test_an_image_job_plans_n_images_in_a_ratio_on_the_image_pool():
    p = plan(n=4, ratio="16:9")
    assert (p.operation, p.media_type, p.pool, p.price_key) == ("text_to_image", "image", "image", "muse-image@n4")
    assert dict(p.params) == {"kind": "image", "ratio": "16:9", "n": 4}
    assert p.submit_timeout_s == 240 and p.completion == "hold"
    assert dict(plan().params) == {"kind": "image", "n": 1} and plan().price_key == "muse-image"
    for ratio in C.IMAGE_RATIOS:
        assert plan(ratio=ratio).params["ratio"] == ratio


def test_a_video_plans_its_length_and_its_ratio():
    p = plan("muse-video", "waves on volcanic rocks")
    assert (p.operation, p.pool, p.price_key) == ("text_to_video", "video", "muse-video@5s")
    assert dict(p.params) == {"kind": "video", "ratio": "16:9", "duration": 5} and p.submit_timeout_s == 420
    p = plan("muse-video", "a balloon rising", duration=10, ratio="9:16")
    assert (p.price_key, p.params["ratio"], p.params["duration"]) == ("muse-video@10s", "9:16", 10)
    p = plan("muse-video", "", images=(WEBP,))
    assert (p.operation, dict(p.params)) == ("image_to_video", {"kind": "video", "duration": 5})


@mutation(SPEC_FILE, "        if (kind == \"video\" and n != 1) or not 1 <= n <= C.MAX_N:",
          "        if False:", expect="n out of range")
def test_the_plan_refuses_what_muse_cannot_make():
    assert refused(model="nope") == "input.bad_model"
    assert refused(n=5) == refused(n=0) == "input.invalid", "n out of range"
    assert refused(model="muse-video", prompt="x", n=2) == "input.invalid", "n out of range"
    assert refused(images=(WEBP,)) == "input.too_many_images"
    assert refused(model="muse-video", images=(WEBP, WEBP)) == "input.too_many_images"
    assert refused(prompt="   ") == "input.empty_prompt"
    assert refused(model="muse-video", prompt="​") == "input.empty_prompt"
    assert refused(ratio="21:9") == refused(model="muse-video", ratio="1:1") == "input.bad_ratio"
    assert refused(model="muse-video", prompt="", images=(WEBP,), ratio="16:9") == "input.bad_ratio"
    assert refused(duration=5) == refused(model="muse-video", duration=15) == "input.bad_duration"
    big = MediaIn("a" * 64, "image/webp", 8 * 2**20 + 1)
    gif = MediaIn("a" * 64, "image/gif", 100)
    assert refused(model="muse-video", images=(big,)) == refused(model="muse-video", images=(gif,)) == "input.invalid"


@mutation(SPEC_FILE, "        if prompt_counts(req.prompt, kind) - {n}:", "        if False:",
          expect="a count in the prompt")
@mutation(SPEC_FILE, "        if other:\n", "        if False:\n", expect="a ratio in the prompt")
@mutation(SPEC_FILE, "            if prompt_seconds(req.prompt) - {duration}:",
          "            if False:", expect="a length in the prompt")
def test_a_prompt_that_writes_out_another_count_ratio_or_length_is_refused():
    assert refused(prompt="4 images of a cat", n=2) == "input.invalid", "a count in the prompt"
    assert refused(prompt="two cat photos") == "input.invalid", "a count in the prompt"
    assert refused(model="muse-video", prompt="three clips of a dog") == "input.invalid", "a count in the prompt"
    assert plan(prompt="4 images of a cat", n=4).params["n"] == 4
    assert refused(prompt="a cat, 9:16", ratio="16:9") == "input.prompt_ratio_conflict", "a ratio in the prompt"
    assert refused(model="muse-video", prompt="a cat 1:1") == "input.prompt_ratio_conflict", "a ratio in the prompt"
    assert plan(prompt="a cat at 10:30, 16:9", ratio="16:9").params["ratio"] == "16:9"
    assert refused(model="muse-video", prompt="a 10 second clip of rain") == "input.prompt_duration_conflict", \
        "a length in the prompt"
    assert refused(model="muse-video", prompt="rain for 10s", duration=5) == "input.prompt_duration_conflict"
    assert plan("muse-video", "a 1990s diner in 50s style, 10 seconds", duration=10).params["duration"] == 10
    assert plan("muse-video", "a 5-sec shot of rain").price_key == "muse-video@5s"


@mutation(CRED_FILE, "    kept = [c for c in cookies if on_site(c[\"domain\"])]", "    kept = cookies",
          expect="another site's cookie")
@mutation(CRED_FILE, '    if not any(c["name"] == SESSION_COOKIE and c["value"] for c in kept):', "    if False:",
          expect="DID NOT RAISE")
def test_the_cookie_import_takes_the_usual_shapes_and_keeps_only_muse_cookies():
    editor = [{"name": "hatch_sess", "value": "s1", "domain": ".muse.ai", "path": "/"},
              {"name": "hatch_vml", "value": "v1", "domain": "muse.ai"},
              {"name": "datr", "value": "x", "domain": ".facebook.com"}]
    for raw in (json.dumps(editor), json.dumps({"cookies": editor, "origins": []}),
                "Cookie: hatch_sess=s1; hatch_vml=v1"):
        parsed = parse_credential(raw.encode())
        assert parsed.hint == "muse"
        assert jar_of(parsed.blob) == {"hatch_sess": "s1", "hatch_vml": "v1"}, "another site's cookie"
    for bad in (b"", b"{nope", json.dumps([{"name": "hatch_vml", "value": "v", "domain": ".muse.ai"}]).encode(),
                json.dumps([{"name": "hatch_sess", "value": "s", "domain": ".other.com"}]).encode(),
                json.dumps([{"name": "hatch_sess", "value": "a b", "domain": ".muse.ai"}]).encode(),
                json.dumps([{"name": "x", "value": "1", "domain": ".muse.ai"}] * 201).encode()):
        with pytest.raises(ValueError) as caught:
            parse_credential(bad)
        assert "s1" not in str(caught.value)
    with pytest.raises(ValueError, match="no muse.ai hatch_sess"):
        parse_credential(json.dumps([{"name": "hatch_vml", "value": "v", "domain": ".muse.ai"}]).encode())


def test_a_rotated_cookie_replaces_its_value_and_a_new_one_is_added_for_muse():
    blob = parse_credential(b"hatch_sess=s1; hatch_vml=v1").blob
    out = with_cookies(blob, {"hatch_vml": "v2", "hatch_gw": "g1"})
    assert jar_of(out) == {"hatch_sess": "s1", "hatch_vml": "v2", "hatch_gw": "g1"}
    assert json.loads(out)["cookies"][-1]["domain"] == ".muse.ai"
    with pytest.raises(ValueError):
        jar_of(b"not json")


GROUPS = "        found |= {_GROUPS[w.lower()] for w in _PROMPT_GROUP_IMAGES.findall(text)}\n"
SHAPES = '        found.append((word, _SHAPE_WORDS["ultrawide" if word.startswith("ultra") else word]))'
WORD_SECONDS = "    return {_number(a) if a else"


@mutation(SPEC_FILE, GROUPS, "", expect="a pair is two")
@mutation(SPEC_FILE, SHAPES, "        pass", expect="widescreen is a ratio")
@mutation(SPEC_FILE, WORD_SECONDS, "    return {int(a) if a.isdigit() else 5 if a else", expect="twenty seconds")
def test_the_word_forms_of_a_count_a_shape_or_a_length_are_refused_too():
    assert refused(prompt="a pair of photos of a dog") == "input.invalid", "a pair is two"
    assert plan(prompt="a pair of photos of a dog", n=2).params["n"] == 2
    assert refused(prompt="a couple of pictures of rain", n=3) == "input.invalid"
    assert refused(prompt="a triptych of a forest") == "input.invalid" and plan(prompt="a triptych", n=3)
    assert refused(prompt="twenty images of cats") == refused(prompt="seventeen photos") == "input.invalid"
    assert refused(model="muse-video", prompt="a pair of clips of the sea") == "input.invalid"
    assert refused(prompt="a widescreen city at night", ratio="1:1") == "input.prompt_ratio_conflict", \
        "widescreen is a ratio"
    assert plan(prompt="a widescreen city at night", ratio="16:9").params["ratio"] == "16:9"
    assert refused(model="muse-video", prompt="a balloon, portrait orientation") == "input.prompt_ratio_conflict"
    assert plan("muse-video", "a balloon, portrait orientation", ratio="9:16").params["ratio"] == "9:16"
    assert refused(prompt="a square image of a cat", ratio="16:9") == "input.prompt_ratio_conflict"
    assert refused(prompt="an ultra-wide desert road", ratio="16:9") == "input.prompt_ratio_conflict"
    for free in ("a portrait of a woman in oils", "a busy town square at dusk", "a vertical garden on a wall"):
        assert plan(prompt=free, ratio="16:9").params["ratio"] == "16:9", free
    assert refused(model="muse-video", prompt="make it twenty seconds long") == "input.prompt_duration_conflict", \
        "twenty seconds"
    assert refused(model="muse-video", prompt="rain for twenty-five seconds") == "input.prompt_duration_conflict"
    assert refused(model="muse-video", prompt="half a minute of rain", duration=10) == "input.prompt_duration_conflict"
    assert refused(model="muse-video", prompt="a one minute timelapse") == "input.prompt_duration_conflict"
    assert plan("muse-video", "ten seconds of rain", duration=10).price_key == "muse-video@10s"
    assert plan("muse-video", "a mini cooper in the rain, five-second shot").price_key == "muse-video@5s"
