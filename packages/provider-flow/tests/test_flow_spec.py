"""The flow spec (caps, settings, catalog, error rules, `plan()`) and the credential import."""

import dataclasses
import json

import pytest
from flowkit import PSID, PSIDTS, export

from relay_core import errors, frames, pricing
from relay_core.api.doors import checked_prompt
from relay_core.pool import day_pool
from relay_core.providers import (
    AfterSent,
    Completion,
    CreateRequest,
    Dead,
    InputError,
    MediaIn,
    ProviderRegistry,
    _problems,
)
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA
from relay_flow import catalog as FC
from relay_flow import credential as cred
from relay_flow.spec import SPEC
from relay_meta.spec import SPEC as META
from relay_vibes.spec import SPEC as VIBES

SPEC_FILE = "packages/provider-flow/src/relay_flow/spec.py"
CREDENTIAL_FILE = "packages/provider-flow/src/relay_flow/credential.py"
IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)
MODELS = ("flow-nano-banana-2.1", "flow-nano-banana-pro", "flow-nano-banana-2-lite")
VIDEO = "flow-veo-3.1-lite"
FAST = "flow-veo-3.1-fast"


def plan(model="flow-nano-banana-2.1", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), cfg or FakeSettings(SPEC), {})


def priced(model="flow-nano-banana-2.1", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    """The plan as the door makes it: the core's price and upstream cost from `flow.prices` and `flow.costs`."""
    cfg = cfg or FakeSettings(SPEC)
    return pricing.price_plan(SPEC, plan(model, prompt, cfg=cfg, **kw), cfg)


def refusal(model="flow-nano-banana-2.1", prompt="a lighthouse at dawn", **kw):
    try:
        plan(model, prompt, **kw)
    except InputError as e:
        return e.code, e.param
    return None


# ---- caps, settings, catalog, codes --------------------------------------------------------------------------------
def test_the_caps_say_an_http_provider_with_an_image_and_a_video_pool_checked_every_five_minutes():
    c = SPEC.caps
    assert SPEC.name == "flow" and c.execution == "http"
    assert [(p.name, p.unit) for p in c.pools] == [("image", "jobs"), ("video", "credits")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold, c.strike_effect) == (1, 3, 3, Dead("signed_out"))
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (True, True, False, False)
    # the check rotates the session, which ages out in ~20 min: it must come at least twice per rotation interval
    assert c.check_interval_s * 2 <= SPEC.settings()["flow.rotate_interval_s"].default


@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="flow must stay drained")
def test_flow_stays_drained_by_default_and_declares_its_tunables():
    s = SPEC.settings()
    assert s["flow.claims_enabled"].default is False, "flow must stay drained until the owner turns it on"
    assert (s["flow.rotate_interval_s"].default, s["flow.rotate_interval_s"].max) == (600, 900)
    assert (s["flow.captcha_max_tries"].default, s["flow.captcha_retry_gap_s"].default) == (5, 60)
    assert s["flow.account_min_gap_s"].default == 300 and s["flow.captcha_provider"].default == "yescaptcha"
    assert (s["flow.traffic_rest_s"].default, s["flow.traffic_rest_s"].min) == (1800, 0)
    assert s["flow.prices"].default == {**dict.fromkeys(MODELS, 1), VIDEO + "@8s": 2, FAST + "@8s": 4}
    assert s["flow.costs"].default == {**dict.fromkeys(MODELS, 1), VIDEO + "@8s": 10, FAST + "@8s": 20,
                                       **FC.TABLE_ONLY_COSTS}
    assert all(s[k].description for k in s) and _problems(SPEC) == []
    assert not [k for k in s if "key" in k.lower()], "a secret is never a setting"


@mutation(SPEC_FILE, 'POOL_DEFAULTS = {"image": day_pool({"free": 40}),',
          'POOL_DEFAULTS = {"image": {"combine": "sum", "windows": []},', expect="no image budget")
def test_the_image_pool_has_a_day_window_of_40_jobs_for_every_tier_and_video_counts_credits():
    """No image cap of flow is measured yet (an image takes no Flow credit): 40 a day per account is a guard the admin
    shows and can edit; a tier with no budget of its own takes the free one."""
    pools = SPEC.settings()["flow.pools"].default
    assert pools["image"] == day_pool({"free": 40}), "no image budget: %s" % pools["image"]
    assert pools["video"] == day_pool({"free": 50, "pro": 50})


def test_the_token_source_setting_takes_only_a_built_source():
    rule = SPEC.settings()["flow.captcha_provider"]
    assert rule.validate("yescaptcha") == "yescaptcha"
    with pytest.raises(ValueError):
        rule.validate("capsolver")


def test_the_registry_takes_flow_beside_the_others_and_no_model_name_clashes():
    reg = ProviderRegistry()
    for spec in (DOLA, META, VIBES, SPEC):
        reg.register(spec)
    for name in MODELS:
        spec, model = reg.resolve(name)
        assert spec is SPEC and model.operations == ("text_to_image",) and model.media_type == "image"
    for name in (VIDEO, FAST):
        spec, model = reg.resolve(name)
        assert spec is SPEC and model.operations == ("text_to_video",) and model.media_type == "video"
        assert dict(model.limits) | {"description": ""} == {
            "max_images": 0, "ratios": ["16:9", "9:16"], "default_ratio": "16:9", "durations": [8],
            "default_duration": 8, "description": ""}, name
        assert dict(model.price_keys) == {"8s": name + "@8s"}
    assert {m.id for m in SPEC.catalog()} == {*MODELS, VIDEO, FAST}
    assert [m.display_name for m in SPEC.catalog()] == ["Nano Banana 2.1", "Nano Banana Pro", "Nano Banana 2 Lite",
                                                        "Veo 3.1 Lite", "Veo 3.1 Fast"]
    # the pause list takes every model, the video too
    assert SPEC.settings()["flow.disabled_models"].validate([VIDEO]) == [VIDEO]


def test_flow_registers_its_codes_on_load():
    codes = {"flow.http_error", "flow.captcha_failed", "flow.captcha_refused", "flow.too_much_traffic"}
    assert set(SPEC.codes()) == codes and codes <= set(errors.RULES)
    for code in codes:          # before the send or nothing made: an attempt, refunded when they run out
        r = errors.rule(code)
        assert (r.terminal, r.refund, r.counts_attempt, r.public) == (False, True, True, True), code


# ---- plan ----------------------------------------------------------------------------------------------------------
@mutation(SPEC_FILE, '                params={"model": image[0], "aspect": C.ASPECTS[ratio], "ratio": ratio},',
          '                params={"model": image[0], "aspect": 1, "ratio": ratio},', expect="aspect")
def test_each_model_and_ratio_maps_to_flows_own_keys_and_the_image_is_held():
    keys = {"flow-nano-banana-2.1": "BELUGA", "flow-nano-banana-pro": "GEM_PIX_2",
            "flow-nano-banana-2-lite": "HARBOR_SEAL"}
    for name, key in keys.items():
        for ratio, aspect in (("1:1", 1), ("9:16", 2), ("16:9", 3), ("3:4", 4), ("4:3", 5)):
            p = plan(name, ratio=ratio)
            assert dict(p.params) == {"model": key, "aspect": aspect, "ratio": ratio}, "aspect %s" % ratio
    p = plan()
    assert p.params["ratio"] == "16:9" and p.params["aspect"] == 3
    assert (p.operation, p.media_type, p.completion, p.after_sent) == (
        "text_to_image", "image", Completion.HOLD, AfterSent.RECONCILE)
    assert (p.pool, p.price_key, p.submit_timeout_s) == ("image", "flow-nano-banana-2.1", 900)
    p = priced()
    assert (p.price, p.account_cost) == (1, 1), "an image costs no Flow credit, counts one job and sells at 1"


@mutation(SPEC_FILE, "        if req.images:", "        if False:", expect="an input image")
@mutation(SPEC_FILE, '            raise _refuse("input.unsupported",',
          '            raise _refuse("input.too_many_images",', expect="an input image")
@mutation(SPEC_FILE, "        if req.n not in (None, 1):", "        if False:", expect="more than one image")
@mutation(SPEC_FILE, "        if C.is_blank_prompt(req.prompt):", "        if False:", expect="a blank prompt")
def test_what_flow_cannot_do_yet_is_refused_rather_than_ignored():
    for model in (*MODELS, VIDEO):       # image to image, image to video: the upload is not proven yet
        assert refusal(model, images=(IMG,)) == ("input.unsupported", "images"), "an input image was ignored"
    assert refusal(n=2) == ("input.invalid", "n"), "more than one image was planned"
    assert refusal(prompt=" ​ ") == ("input.empty_prompt", "prompt"), "a blank prompt was planned"
    assert refusal(duration=8) == ("input.bad_duration", "duration")
    for ratio in ("21:9", "16x9", "2:3"):
        assert refusal(ratio=ratio) == ("input.bad_ratio", "ratio"), ratio
    for name in ("flow-veo-3.1", "BELUGA", "nano-banana-pro", "flow"):
        assert refusal(name) == ("input.bad_model", "model"), name
    assert plan(n=1).params["aspect"] == 3


TABLE_ONLY = "+ tuple(TABLE_ONLY_COSTS)"
CATALOG_FILE = "packages/provider-flow/src/relay_flow/catalog.py"


@mutation(CATALOG_FILE, TABLE_ONLY, "+ ()", expect="not an option")
def test_the_table_only_options_are_costed_priceable_and_refused_until_priced():
    """Veo 3.1 Fast and Quality and Gemini Omni Flash (720p and 360p by length) are not sold yet: their Flow credits
    are known (the official table), the owner may price them ahead, and a job naming one is refused while unpriced."""
    costs = SPEC.settings()["flow.costs"].default
    assert (costs["flow-veo-3.1-fast@8s"], costs["flow-veo-3.1-quality@8s"]) == (20, 100)
    assert [costs["flow-omni-flash-720p@%ds" % d] for d in (4, 6, 8, 10)] == [7, 10, 12, 15]
    assert [costs["flow-omni-flash-360p@%ds" % d] for d in (4, 6, 8, 10)] == [4, 5, 6, 7]
    prices = SPEC.settings()["flow.prices"]
    assert not set(FC.TABLE_ONLY_COSTS) & set(prices.default), "a table-only option has no default price"
    assert prices.validate({"flow-veo-3.1-quality@8s": 5}) == {"flow-veo-3.1-quality@8s": 5}, "not an option"
    quality = dataclasses.replace(plan(VIDEO), price_key="flow-veo-3.1-quality@8s")
    with pytest.raises(pricing.Unpriced):
        pricing.price_plan(SPEC, quality, FakeSettings(SPEC))


@mutation(SPEC_FILE, 'renamed={"flow-veo-3.1-lite": ("flow-veo-3.1-lite@8s",)}', "renamed={}",
          expect="the stored video price")
def test_a_price_table_stored_by_the_earlier_build_keeps_the_videos_price():
    """The earlier build keyed the video by its model alone (`flow-veo-3.1-lite`): the stored price moves to its key
    with the length, and every option the stored table does not name keeps its default."""
    loaded = SPEC.settings()["flow.prices"].on_load({"flow-veo-3.1-lite": 7, "flow-nano-banana-pro": 4})
    assert loaded == {**dict.fromkeys(MODELS, 1), "flow-nano-banana-pro": 4, VIDEO + "@8s": 7, FAST + "@8s": 4}, (
        "the stored video price was lost: %s" % loaded)


def test_the_price_comes_from_the_settings_and_a_missing_one_is_refused():
    cfg = FakeSettings(SPEC, flow__prices={"flow-nano-banana-pro": 4})
    assert priced("flow-nano-banana-pro", cfg=cfg).price == 4 and priced(options={"price": 0}).price == 1
    with pytest.raises(pricing.Unpriced):
        priced(cfg=cfg)


# ---- the credential ------------------------------------------------------------------------------------------------
def sealed(raw) -> dict:
    return json.loads(SPEC.parse_credential(json.dumps(raw).encode()).blob)


def refused(raw) -> str:
    try:
        SPEC.parse_credential(raw if isinstance(raw, bytes) else json.dumps(raw).encode())
    except ValueError as e:
        assert PSID not in str(e) and PSIDTS not in str(e)
        return str(e)
    return "accepted"


@mutation(CREDENTIAL_FILE, '    cookies = [c for c in cookies if on_google(c["domain"])]', "",
          expect="a cookie of another site was sealed")
def test_an_export_seals_the_google_cookies_only_with_their_domains_and_rotates_at_first_use():
    blob = sealed(export())
    assert blob["rotated_at"] == 0
    assert [(c["name"], c["domain"], c["path"]) for c in blob["cookies"]] == [
        ("__Secure-1PSID", ".google.com", "/"), ("__Secure-1PSIDTS", ".google.com", "/"),
        ("SIDCC", ".google.com", "/"), ("OSID", "flow.google.com", "/")], "a cookie of another site was sealed"
    assert all(set(c) == {"name", "value", "domain", "path"} for c in blob["cookies"])
    assert SPEC.parse_credential(json.dumps(export()).encode()).hint == "flow"
    wrapped = sealed({"url": "https://flow.google.com/", "cookies": [{"name": "__Secure-1PSID", "value": PSID,
                                                                       "domain": ".google.com"}]})
    assert wrapped["cookies"][0]["value"] == PSID
    # the sealed shape imports as itself (a jar the provider wrote back), its rotation time kept
    again = sealed({"cookies": blob["cookies"], "rotated_at": 1792737600.5})
    assert again == {**blob, "rotated_at": 1792737600.5}
    assert cred.jar_of(json.dumps(again).encode()) == (blob["cookies"], 1792737600.5)
    host_only = sealed([{"name": "__Secure-1PSID", "value": PSID, "domain": "google.com", "hostOnly": False},
                        {"name": "OSID", "value": "o", "domain": ".flow.google.com", "hostOnly": True}])
    assert [c["domain"] for c in host_only["cookies"]] == [".google.com", "flow.google.com"]


NEEDS_PSID = '    if not any(c["name"] == SESSION_COOKIE and c["value"] for c in cookies):'


@mutation(CREDENTIAL_FILE, NEEDS_PSID, "    if not cookies:", expect="no google.com __Secure-1PSID cookie")
def test_an_export_without_the_google_session_cookie_is_refused():
    no_psid = [c for c in export() if c["name"] != "__Secure-1PSID"]
    elsewhere = [{**export()[0], "domain": ".notgoogle.com"}]
    empty = [{**export()[0], "value": ""}]
    for raw in (no_psid, elsewhere, empty):
        assert "no google.com __Secure-1PSID cookie" in refused(raw)


def test_malformed_input_is_refused_with_a_fixed_reason():
    assert refused(b"") == "empty credential"
    assert refused(b"SID=abc; HSID=def") == "the credential is not a JSON cookie export"
    assert refused([]) == "no cookies" and refused({"cookies": []}) == "no cookies"
    assert refused(["x"]) == "a cookie must be an object"
    assert refused([{"name": "__Secure-1PSID", "value": PSID}]) == "a cookie needs a name, a value and a domain"
    for value in ("a b", "a;b", 'a"b', "a\r\nb", "café"):
        assert refused([{**export()[0], "value": value}]) == "a cookie holds characters a cookie cannot carry"
    assert refused(export() * 61) == "too many cookies"
    with pytest.raises(ValueError, match="not readable"):
        cred.jar_of(b"[not json")


# ---- prompt frames ---------------------------------------------------------------------------------------------------
MULTI = "  a lantern\n\tfloats over a lake,\r\n  été — 日本語  \n"


@mutation(SPEC_FILE, "max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)",
          "max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX)", expect="not byte for byte")
@mutation(SPEC_FILE, "    frames = FRAMES\n", "    frames = None\n", expect="flow declares no frames")
def test_the_shipped_frames_send_the_customers_words_byte_for_byte_as_before_frames():
    """Before frames flow got the customer's words as the door took them (trimmed, line breaks kept, no tracking
    code). The goldens are literals."""
    assert SPEC.frames is not None, "flow declares no frames"
    assert set(SPEC.frames.defaults) == {m.media_type for m in SPEC.catalog()} == {"image", "video"}
    golden = {"a calm sea at dusk": "a calm sea at dusk",
              MULTI: "a lantern\n\tfloats over a lake,\r\n  été — 日本語",
              "see {{tracking_code}} [1234]": "see {{tracking_code}} [1234]",
              "\u200b\u2060": "\u200b\u2060"}          # a blank-looking prompt (an image to video) stays as it is
    for kind, frame in SPEC.frames.defaults.items():
        for words, want in golden.items():
            got = frames.render(frame, checked_prompt(words, SPEC.max_prompt_chars), "7908", SPEC.frames.verbatim)
            assert got == want, "the %s default is not byte for byte: %r" % (kind, got)
        longest = frames.render(frame, "x" * SPEC.max_prompt_chars, "7908", SPEC.frames.verbatim)
        assert len(longest) <= SPEC.frames.max_sent_chars, "the longest prompt flow takes is refused for length"


# ---- the video -----------------------------------------------------------------------------------------------------
VIDEO_CREDITS = '                    "duration": C.VIDEO_DURATION, "credits": credits},'
VIDEO_ASPECT = '            params={"model": video[0][ratio][0], "aspect": video[0][ratio][1], "ratio": ratio,'


@mutation(SPEC_FILE, VIDEO_CREDITS, '                    "duration": C.VIDEO_DURATION, "credits": 0},',
          expect="credits")
@mutation(SPEC_FILE, VIDEO_ASPECT, '            params={"model": video[0][ratio][0], "aspect": 2, "ratio": ratio,',
          expect="portrait")
@mutation(SPEC_FILE, VIDEO_ASPECT,
          '            params={"model": video[0]["16:9"][0], "aspect": video[0][ratio][1], "ratio": ratio,',
          expect="portrait")
@mutation(CATALOG_FILE, '"9:16": ("veo_3_1_t2v_fast_portrait", 1)', '"9:16": ("veo_3_1_t2v_fast", 1)',
          expect="portrait")
@mutation(CATALOG_FILE, 'price_key("flow-veo-3.1-fast"): 20,', 'price_key("flow-veo-3.1-fast"): 10,',
          expect="portrait or credits")
@mutation(SPEC_FILE, '        credits = settings.get("flow.costs").get(C.price_key(req.model))',
          '        credits = 1', expect="credits")
@mutation(SPEC_FILE, "            completion=Completion.DETACH,", "            completion=Completion.HOLD,",
          expect="a video is polled")
def test_a_veo_video_is_planned_as_recorded_and_costs_its_flow_credits_against_the_video_pool():
    p = plan(VIDEO, ratio="16:9", duration=8)
    assert dict(p.params) == {"model": "veo_3_1_t2v_lite", "aspect": 2, "ratio": "16:9", "duration": 8,
                              "credits": 10}, "credits"
    assert priced(VIDEO).account_cost == 10, "the account cost of a video is its Flow credits"
    assert p.completion == Completion.DETACH, "a video is polled"
    assert (p.operation, p.media_type, p.after_sent, p.pool, priced(VIDEO).price, p.price_key,
            p.submit_timeout_s) == ("text_to_video", "video", AfterSent.RECONCILE, "video", 2, VIDEO + "@8s", 900)
    assert dict(plan(VIDEO).params) == dict(p.params)
    assert priced(VIDEO, cfg=FakeSettings(SPEC, flow__prices={VIDEO + "@8s": 7})).price == 7
    cheap = FakeSettings(SPEC, flow__costs={**SPEC.settings()["flow.costs"].default, VIDEO + "@8s": 5})
    assert plan(VIDEO, cfg=cheap).params["credits"] == 5, "credits: the submit checks the balance against the cost"
    # GetModels 25/10: Lite has both orientations in one key; Fast has a key for each; the aspect code 1 is portrait
    want = {(VIDEO, "9:16"): ("veo_3_1_t2v_lite", 1, 10), (FAST, "16:9"): ("veo_3_1_t2v_fast", 2, 20),
            (FAST, "9:16"): ("veo_3_1_t2v_fast_portrait", 1, 20)}
    for (model, ratio), (key, aspect, credits) in want.items():
        p = plan(model, ratio=ratio)
        assert dict(p.params) == {"model": key, "aspect": aspect, "ratio": ratio, "duration": 8,
                                  "credits": credits}, "portrait or credits: %s %s: %s" % (model, ratio, dict(p.params))
        assert (p.pool, p.price_key, p.completion) == ("video", model + "@8s", Completion.DETACH)
    assert plan(FAST).params["aspect"] == 2 and priced(FAST).account_cost == 20, "Fast takes 20 Flow credits"
    assert priced(FAST).price == 4


@mutation(SPEC_FILE, "        aspects = video[0] if video else C.ASPECTS", "        aspects = C.ASPECTS",
          expect="an unrecorded video ratio")
@mutation(SPEC_FILE, "        if req.duration not in (None, C.VIDEO_DURATION):", "        if False:",
          expect="an unrecorded duration")
def test_what_the_video_capture_did_not_prove_is_refused():
    for model in (VIDEO, FAST):
        for ratio in ("1:1", "4:3", "3:4"):
            assert refusal(model, ratio=ratio) == ("input.bad_ratio", "ratio"), "an unrecorded video ratio %s" % ratio
        for duration in (4, 6, 10):
            assert refusal(model, duration=duration) == ("input.bad_duration", "duration"), "an unrecorded duration"
    assert refusal(VIDEO, n=2) == ("input.invalid", "n")
    with pytest.raises(pricing.TooCostly):           # more credits than a day's budget: no account could take it
        priced(VIDEO, cfg=FakeSettings(SPEC, flow__pools={"video": day_pool({"free": 9})}))
