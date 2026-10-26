"""The flow spec (caps, settings, catalog, error rules, `plan()`) and the credential import."""

import json
from datetime import UTC, datetime

import pytest
from flowkit import FIX, PSID, PSIDTS, export

from relay_core import errors, frames, pricing
from relay_core.api.doors import checked_prompt
from relay_core.pool import can_afford, day_pool, left_of, pool_configs
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
CATALOG_FILE = "packages/provider-flow/src/relay_flow/catalog.py"
FC_FIX = FIX / "live-261025b"
CREDENTIAL_FILE = "packages/provider-flow/src/relay_flow/credential.py"
IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)
MODELS = ("flow-nano-banana-2.1", "flow-nano-banana-pro", "flow-nano-banana-2-lite")
VIDEO = "flow-veo-3.1-lite"
FAST = "flow-veo-3.1-fast"
QUALITY = "flow-veo-3.1-quality"
OMNI = "flow-omni-flash-720p"
OMNI_360 = "flow-omni-flash-360p"


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
    assert [(p.name, p.unit) for p in c.pools] == [("image", "jobs"), ("video", "credits"), ("video_pro", "credits"),
                                                   ("video_ultra", "credits")]
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
    assert s["flow.account_min_gap_s"].default == 300 and s["flow.captcha_provider"].default == ["yescaptcha"]
    assert (s["flow.traffic_rest_s"].default, s["flow.traffic_rest_s"].min) == (1800, 0)
    assert s["flow.prices"].default == FC.DEFAULT_PRICES and s["flow.costs"].default == FC.DEFAULT_COSTS
    assert set(s["flow.prices"].default) == set(s["flow.costs"].default) == set(FC.PRICE_KEYS)
    pools = s["flow.pools"]
    assert pools.validate(pools.default) == pools.default, "the shipped pools pass their own rule"
    assert all(s[k].description for k in s) and _problems(SPEC) == []
    keys = [k for k in s if "key" in k.lower()]           # owner 25/10: the solver keys, and only as secrets
    assert keys and all(s[k].secret for k in keys), "a key that is a setting is a secret one"


@mutation(SPEC_FILE, 'POOL_DEFAULTS = {"image": day_pool({"free": 40}),',
          'POOL_DEFAULTS = {"image": {"combine": "sum", "windows": []},', expect="no image budget")
def test_the_image_pool_has_a_day_window_of_40_jobs_and_video_50_credits_a_day_and_1000_a_month_on_pro():
    """No image cap of flow is measured yet (an image takes no Flow credit): 40 a day per account is a guard the admin
    shows and can edit; a tier with no budget of its own takes the free one."""
    pools = SPEC.settings()["flow.pools"].default
    assert pools["image"] == day_pool({"free": 40}), "no image budget: %s" % pools["image"]
    assert pools["video"] == {"combine": "sum", "windows": [
        {"name": "24h", "kind": "rolling", "hours": 24, "budget": {"free": 50, "pro": 50}},
        {"name": "month", "kind": "month", "tz": "UTC", "budget": {"pro": 1000}}]}, "50 from first use + 1000 a month"


def test_the_token_source_setting_is_an_ordered_list_of_built_sources_never_empty():
    """Owner 25/10: a select of yescaptcha, capsolver, anticaptcha whose order is the try order; removing every choice
    is refused. A str stored before (comma-separated) is read as the list it names."""
    s = SPEC.settings()
    rule = s["flow.captcha_provider"]
    assert rule.type is list and rule.rule.choices == ("yescaptcha", "capsolver", "anticaptcha")
    for ok in (["yescaptcha"], ["capsolver", "yescaptcha"], ["anticaptcha", "capsolver", "yescaptcha"]):
        assert rule.validate(ok) == ok
    for bad in ([], ["2captcha"], ["capsolver", "capsolver"], ["capsolver", ""], "capsolver", [1]):
        with pytest.raises(ValueError):
            rule.validate(bad)
    assert rule.on_load("capsolver, yescaptcha") == ["capsolver", "yescaptcha"]
    assert rule.on_load("2captcha") == "2captcha" and rule.on_load(["anticaptcha"]) == ["anticaptcha"]
    keys = {k: v for k, v in s.items() if v.secret}
    assert sorted(keys) == ["flow.anticaptcha_key", "flow.capsolver_key", "flow.yescaptcha_key"]
    assert all(v.type is str and v.default == "" and v.basic for v in keys.values())
    assert (s["flow.captcha_timeout_s"].default, s["flow.captcha_poll_s"].default) == (120, 3)
    assert s["flow.capsolver_proxy_task"].default is True
    score = s["flow.anticaptcha_min_score"]
    assert score.default == 0.9 and score.validate(0.3) == 0.3
    with pytest.raises(ValueError):
        score.validate(0.5)


def test_the_registry_takes_flow_beside_the_others_and_no_model_name_clashes():
    reg = ProviderRegistry()
    for spec in (DOLA, META, VIBES, SPEC):
        reg.register(spec)
    for name in MODELS:
        spec, model = reg.resolve(name)
        assert spec is SPEC and model.operations == ("text_to_image", "image_to_image") and model.media_type == "image"
        assert model.limits["max_images"] == 10 and len(model.limits["ratios"]) == (14 if name == MODELS[0] else 5)
    ops = ("text_to_video", "image_to_video", "frames_to_video", "references_to_video")
    for name, durations, refs in ((VIDEO, [4, 6, 8], 3), (FAST, [4, 6, 8], 3), (QUALITY, [4, 6, 8], 2),
                                  (OMNI, [4, 6, 8, 10], 7), (OMNI_360, [4, 6, 8, 10], 7)):
        spec, model = reg.resolve(name)
        assert spec is SPEC and model.media_type == "video"
        assert model.operations == (ops[:3] if name == QUALITY else ops), name
        assert dict(model.limits) | {"description": ""} == {
            "max_images": refs, "ratios": ["16:9", "9:16"], "default_ratio": "16:9", "durations": durations,
            "default_duration": 8, "modes": ["frames"] if name == QUALITY else ["frames", "references"],
            "description": ""}, name
        assert dict(model.price_keys) == {"%ds" % d: "%s@%ds" % (name, d) for d in durations}
    assert [m.display_name for m in SPEC.catalog()] == [
        "Nano Banana 2.1", "Nano Banana Pro", "Nano Banana 2 Lite", "Veo 3.1 Lite", "Veo 3.1 Fast", "Veo 3.1 Quality",
        "Omni 1.1 Flash 720p", "Omni 1.1 Flash 360p"]
    assert "4/6 s on an AI Ultra account only" in reg.resolve(VIDEO)[1].limits["description"]
    # the pause list takes every model, the video too
    assert SPEC.settings()["flow.disabled_models"].validate([VIDEO]) == [VIDEO]


def test_flow_registers_its_codes_on_load():
    codes = {"flow.http_error", "flow.captcha_failed", "flow.captcha_refused", "flow.too_much_traffic",
             "flow.model_access_denied", "flow.captcha_timeout", "flow.upload_failed", "flow.tier_required"}
    ours = {"flow.captcha_key_invalid", "flow.captcha_no_balance"}       # the solver's key or balance: ours to fix
    assert set(SPEC.codes()) == codes | ours and codes | ours <= set(errors.RULES)
    for code in codes | ours:   # before the send or nothing made: an attempt, refunded when they run out
        r = errors.rule(code)
        counts = code != "flow.tier_required"      # not the order's fault: uses no attempt (a bounded few times)
        assert (r.terminal, r.refund, r.counts_attempt, errors.hidden(r)) == (False, counts, counts, code in ours), code
        assert r.public_code == (errors.INTERNAL_ERROR if code in ours else errors.GENERATION_FAILED
                                 if code == "flow.upload_failed" else errors.TEMPORARILY_UNAVAILABLE), code


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




@mutation(CATALOG_FILE, '"21:9": 10,', '"21:9": 11,', expect="21:9")
@mutation(CATALOG_FILE, 'IMAGE_RATIOS = {"flow-nano-banana-2.1": tuple(ASPECTS),',
          'IMAGE_RATIOS = {"flow-nano-banana-2.1": tuple(ASPECTS)[:5],',
          expect="Nano Banana 2.1's own ratios")
@mutation(SPEC_FILE, '                operation="image_to_image" if req.images else "text_to_image",',
          '                operation="text_to_image",', expect="image_to_image")
@mutation(SPEC_FILE, "            if len(req.images) > C.MAX_IMAGE_REFS:", "            if False:", expect="11 images")
def test_nano_banana_takes_up_to_ten_reference_images_and_2_1_its_fourteen_ratios():
    """GetModels 25/10: each image model takes up to 10 references (`[9]`); BELUGA lists aspect codes 1-14, the other
    two 1-5. The codes 6-14 were measured by the size of an image drawn at each (live-261025b/image_ratios.json)."""
    measured = json.loads((FC_FIX / "image_ratios.json").read_text(encoding="utf-8"))
    for ratio, code in FC.ASPECTS.items():
        assert plan(MODELS[0], ratio=ratio).params["aspect"] == code, "Nano Banana 2.1's own ratios: %s" % ratio
        if code > 5:
            w, h = map(int, ratio.split(":"))
            (mw, mh), = [map(int, d) for d in measured[str(code)]["dims"]]
            assert abs(mw / mh / (w / h) - 1) < 0.06, "%s: flow drew %sx%s for code %d" % (ratio, mw, mh, code)
            for other in MODELS[1:]:
                assert refusal(other, ratio=ratio) == ("input.bad_ratio", "ratio"), (other, ratio)
    for name in MODELS:
        p = plan(name, images=(IMG,) * 10)
        assert (p.operation, p.pool, p.price_key, p.completion) == ("image_to_image", "image", name, Completion.HOLD)
        assert refusal(name, images=(IMG,) * 11) == ("input.too_many_images", "images"), "11 images"
    assert plan(images=(IMG,)).operation == "image_to_image"


@mutation(SPEC_FILE, "        if req.n not in (None, 1):", "        if False:", expect="more than one image")
@mutation(SPEC_FILE, "        if C.is_blank_prompt(req.prompt):", "        if False:", expect="a blank prompt")
def test_what_flow_cannot_do_is_refused_rather_than_ignored():
    assert refusal(n=2) == ("input.invalid", "n"), "more than one image was planned"
    assert refusal(prompt=" ​ ") == ("input.empty_prompt", "prompt"), "a blank prompt was planned"
    assert refusal(prompt=" ​ ", images=(IMG,)) == ("input.empty_prompt", "prompt"), "a blank prompt was planned"
    assert refusal(duration=8) == ("input.bad_duration", "duration")
    for ratio in ("16x9", "2:1", "9:21"):
        assert refusal(ratio=ratio) == ("input.bad_ratio", "ratio"), ratio
    for name in ("flow-veo-3.1", "BELUGA", "nano-banana-pro", "flow", "flow-omni-flash"):
        assert refusal(name) == ("input.bad_model", "model"), name
    assert plan(n=1).params["aspect"] == 3




@mutation(CATALOG_FILE, "DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)",
          "DEFAULT_PRICES = {k: max(1, v // 5) for k, v in DEFAULT_COSTS.items()}", expect="one point per credit")
@mutation(CATALOG_FILE, '    "flow-veo-3.1-quality": {4: 100, 6: 100, 8: 100},',
          '    "flow-veo-3.1-quality": {4: 100, 6: 100, 8: 10},', expect="GetModels")
def test_every_option_has_its_getmodels_credits_and_a_price_of_one_point_per_credit():
    """GetModels 25/10 `[4]`: Veo 8 s on free and Pro, Veo 4/6 s at Ultra's (the only plan with them), Omni alike on
    every plan. Prices: one point per credit, at least 1 (owner, 25/10)."""
    costs, prices = SPEC.settings()["flow.costs"].default, SPEC.settings()["flow.prices"].default
    want = {VIDEO: {4: 5, 6: 5, 8: 10}, FAST: {4: 10, 6: 10, 8: 20}, QUALITY: {4: 100, 6: 100, 8: 100},
            OMNI: {4: 7, 6: 10, 8: 12, 10: 15}, OMNI_360: {4: 4, 6: 5, 8: 6, 10: 7}}
    for model, by in want.items():
        for d, credits in by.items():
            key = "%s@%ds" % (model, d)
            assert costs[key] == credits, "GetModels: %s costs %s" % (key, credits)
            assert prices[key] == credits, "one point per credit: %s" % key
    assert all(costs[m] == prices[m] == 1 for m in MODELS), "an image costs one job and sells at 1"
    assert priced(QUALITY).account_cost == 100 and priced(OMNI, duration=10).price == 15


@mutation(SPEC_FILE, '"flow-veo-3.1-fast": ("flow-veo-3.1-fast@8s",)}', "}", expect="the stored video value")
@mutation(SPEC_FILE, '        unit="points", basic=True, renamed=RENAMED)),', '        unit="points", basic=True)),',
          expect="the stored video value")
@mutation(SPEC_FILE, 'unit="jobs or Flow credits", renamed=RENAMED)),', 'unit="jobs or Flow credits")),',
          expect="the stored video value")
def test_a_table_stored_by_an_earlier_build_keeps_the_video_values():
    """Earlier builds keyed a video by its model alone (`flow-veo-3.1-lite`, `flow-veo-3.1-fast`): a stored price or
    cost moves to its key with the length, and every option the stored table does not name keeps its default."""
    for name, default in (("flow.prices", FC.DEFAULT_PRICES), ("flow.costs", FC.DEFAULT_COSTS)):
        loaded = SPEC.settings()[name].on_load({VIDEO: 7, FAST: 9, "flow-nano-banana-pro": 4})
        assert loaded == {**default, "flow-nano-banana-pro": 4, VIDEO + "@8s": 7, FAST + "@8s": 9}, (
            "the stored video value was lost from %s: %s" % (name, loaded))


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
VIDEO_CREDITS = '        credits = settings.get("flow.costs").get(C.price_key(req.model, duration))'


@mutation(SPEC_FILE, VIDEO_CREDITS, "        credits = 1", expect="credits")
@mutation(SPEC_FILE, '"aspect": C.VIDEO_ASPECTS[ratio], "ratio": ratio,', '"aspect": 2, "ratio": ratio,',
          expect="portrait")
@mutation(SPEC_FILE, '{"model": keys[0] if ratio == "16:9" else keys[1],', '{"model": keys[0],',
          expect="portrait")
@mutation(CATALOG_FILE, '(fast, "text", 8): ("veo_3_1_t2v_fast", "veo_3_1_t2v_fast_portrait"),',
          '(fast, "text", 8): ("veo_3_1_t2v_fast", "veo_3_1_t2v_fast"),', expect="portrait")
@mutation(CATALOG_FILE, '    "flow-veo-3.1-fast": {4: 10, 6: 10, 8: 20},',
          '    "flow-veo-3.1-fast": {4: 10, 6: 10, 8: 10},', expect="portrait or credits")
@mutation(SPEC_FILE, "            completion=Completion.DETACH,", "            completion=Completion.HOLD,",
          expect="a video is polled")
def test_a_veo_video_is_planned_as_recorded_and_costs_its_flow_credits_against_the_video_pool():
    p = plan(VIDEO, ratio="16:9", duration=8)
    assert dict(p.params) == {"model": "veo_3_1_t2v_lite", "aspect": 2, "ratio": "16:9", "duration": 8,
                              "credits": 10}, "credits"
    assert priced(VIDEO).account_cost == 10, "the account cost of a video is its Flow credits"
    assert p.completion == Completion.DETACH, "a video is polled"
    assert (p.operation, p.media_type, p.after_sent, p.pool, priced(VIDEO).price, p.price_key,
            p.submit_timeout_s) == ("text_to_video", "video", AfterSent.RECONCILE, "video", 10, VIDEO + "@8s", 900)
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
    assert priced(FAST).price == 20


# (model, images, mode, seconds, ratio) -> (operation, GetModels key, extra params): every option flow sells
PLANS = {
    (VIDEO, 1, None, 8, "16:9"): ("image_to_video", "veo_3_1_i2v_lite", {}),
    (VIDEO, 2, "frames", 8, "9:16"): ("frames_to_video", "veo_3_1_interpolation_lite", {}),
    (VIDEO, 3, "references", 8, "16:9"): ("references_to_video", "veo_3_1_r2v_lite", {}),
    (VIDEO, 0, None, 4, "16:9"): ("text_to_video", "veo_3_1_t2v_lite_4s", {"tiers": ["ultra"]}),
    (VIDEO, 1, None, 6, "9:16"): ("image_to_video", "veo_3_1_i2v_s_lite_6s", {"tiers": ["ultra"]}),
    (VIDEO, 2, None, 4, "16:9"): ("frames_to_video", "veo_3_1_i2v_s_lite_4s_fl", {"tiers": ["ultra"]}),
    (FAST, 1, None, 8, "9:16"): ("image_to_video", "veo_3_1_i2v_s_fast_portrait", {}),
    (FAST, 2, None, 8, "16:9"): ("frames_to_video", "veo_3_1_i2v_s_fast_fl", {}),
    (FAST, 2, None, 8, "9:16"): ("frames_to_video", "veo_3_1_i2v_s_fast_portrait_fl", {}),
    (FAST, 3, "references", 8, "16:9"): ("references_to_video", "veo_3_1_r2v_fast_landscape", {}),
    (FAST, 1, "references", 8, "9:16"): ("references_to_video", "veo_3_1_r2v_fast_portrait", {}),
    (FAST, 0, None, 6, "9:16"): ("text_to_video", "veo_3_1_t2v_fast_6s", {"tiers": ["ultra"]}),
    (QUALITY, 0, None, 8, "16:9"): ("text_to_video", "veo_3_1_t2v", {"tiers": ["pro", "ultra"]}),
    (QUALITY, 0, None, 8, "9:16"): ("text_to_video", "veo_3_1_t2v_portrait", {"tiers": ["pro", "ultra"]}),
    (QUALITY, 1, None, 8, "16:9"): ("image_to_video", "veo_3_1_i2v_s", {"tiers": ["pro", "ultra"]}),
    (QUALITY, 2, None, 8, "9:16"): ("frames_to_video", "veo_3_1_i2v_s_portrait_fl", {"tiers": ["pro", "ultra"]}),
    (QUALITY, 1, None, 4, "16:9"): ("image_to_video", "veo_3_1_i2v_s_quality_4s", {"tiers": ["ultra"]}),
    (OMNI, 0, None, 10, "9:16"): ("text_to_video", "abra_t2v_10s", {}),
    (OMNI, 1, None, 4, "16:9"): ("image_to_video", "abra_i2v_4s", {}),
    (OMNI, 2, None, 6, "16:9"): ("frames_to_video", "omni_flash_i2v_6s_first_last", {}),
    (OMNI, 7, "references", 8, "16:9"): ("references_to_video", "abra_r2v_8s", {}),
    (OMNI_360, 0, None, 4, "16:9"): ("text_to_video", "abra_t2v_4s_360p", {"resolution": 4}),
    (OMNI_360, 2, None, 10, "9:16"): ("frames_to_video", "omni_flash_i2v_10s_first_last_360p", {"resolution": 4}),
    (OMNI_360, 1, "references", 6, "16:9"): ("references_to_video", "abra_r2v_6s_360p", {"resolution": 4}),
}


@mutation(CATALOG_FILE, '"video" if tiers is None else "video_ultra" if tiers == ("ultra",) else "video_pro"',
          '"video"', expect="the plans' pool")
@mutation(SPEC_FILE, '            mode = "start" if len(req.images) == 1 else "frames"', '            mode = "frames"',
          expect="image_to_video")
@mutation(SPEC_FILE, "            params[\"tiers\"] = list(C.TIERS[req.model, duration])", "            pass",
          expect="tiers")
@mutation(SPEC_FILE, "            params[\"resolution\"] = C.OMNI_360P", "            pass", expect="resolution")
@mutation(CATALOG_FILE, '         ("flow-veo-3.1-quality", 8): ("pro", "ultra")}', "}", expect="tiers")
@mutation(CATALOG_FILE, '        (lite, "frames", 8): ("veo_3_1_interpolation_lite",) * 2,',
          '        (lite, "frames", 8): ("veo_3_1_i2v_lite",) * 2,', expect="GetModels key")
def test_each_video_mode_length_and_ratio_names_its_getmodels_key_and_the_plans_that_have_it():
    """One start frame is image to video, two frames are a first and a last frame, `options.mode` "references" takes
    reference images. Veo's 4 s and 6 s run on Ultra only, Quality on Pro and Ultra; Omni 360p names its resolution."""
    for (model, n, mode, seconds, ratio), (op, key, extra) in PLANS.items():
        p = plan(model, images=(IMG,) * n, duration=seconds, ratio=ratio, options={"mode": mode} if mode else {})
        why = "%s %d images %s %ds %s" % (model, n, mode, seconds, ratio)
        assert p.operation == op, "image_to_video, frames or references: %s -> %s" % (why, p.operation)
        assert p.params["model"] == key, "GetModels key: %s -> %s" % (why, p.params["model"])
        assert p.params["aspect"] == (2 if ratio == "16:9" else 1) and p.params["duration"] == seconds
        assert {k: p.params[k] for k in ("tiers", "resolution") if k in p.params} == extra, \
            "tiers or resolution: %s -> %s" % (why, dict(p.params))
        assert p.price_key == "%s@%ds" % (model, seconds) and p.params["credits"] == FC.VIDEO_CREDITS[model][seconds]
        pool = {"ultra": "video_ultra", "pro": "video_pro"}.get((extra.get("tiers") or ["video"])[0], "video")
        assert (p.pool, p.completion) == (pool, Completion.DETACH), "the plans' pool: %s -> %s" % (why, p.pool)
    assert all(key in GETMODELS for _o, key, _e in PLANS.values()), "a key GetModels does not list"
    assert set(GETMODELS) >= {k for keys in FC.VIDEO_KEYS.values() for k in keys}, "a key GetModels does not list"


GETMODELS = {e[0] for fam in json.loads((FC_FIX / "models_HTrJv.json").read_text(encoding="utf-8"))["payload"][0][4]
             for e in fam[1]}


@mutation(SPEC_FILE, "        if keys is None:", "        if False:", expect="no such mode")
@mutation(SPEC_FILE, "            if len(req.images) > 2:", "            if False:", expect="three frames")
@mutation(SPEC_FILE, "        if mode == \"references\" and len(req.images) > C.MAX_VIDEO_REFS[req.model]:",
          "        if False:", expect="too many references")
@mutation(SPEC_FILE, '        if mode not in ("frames", "references"):', "        if False:", expect="an unknown mode")
@mutation(SPEC_FILE, "        if duration not in C.durations(req.model):", "        if False:",
          expect="an unoffered length")
@mutation(SPEC_FILE, "        aspects = C.VIDEO_ASPECTS if video else C.IMAGE_RATIOS[req.model]",
          "        aspects = C.ASPECTS", expect="an unoffered video ratio")
def test_what_flow_does_not_offer_for_a_video_is_refused():
    for model in (VIDEO, FAST, QUALITY, OMNI):
        for ratio in ("1:1", "4:3", "3:4"):
            try:
                got = refusal(model, ratio=ratio)
            except KeyError as e:       # planned on a ratio with no aspect code
                got = e
            assert got == ("input.bad_ratio", "ratio"), "an unoffered video ratio %s: %r" % (ratio, got)
    for model, duration in ((VIDEO, 10), (FAST, 5), (QUALITY, 10), (OMNI, 12), (OMNI_360, 8.5)):
        assert refusal(model, duration=duration) == ("input.bad_duration", "duration"), "an unoffered length"
    no_such = ((QUALITY, 1, "references", 8), (VIDEO, 1, "references", 4), (FAST, 2, "references", 6))
    for model, n, mode, seconds in no_such:
        try:
            got = refusal(model, images=(IMG,) * n, duration=seconds, options={"mode": mode})
        except (TypeError, KeyError) as e:      # planned on a key that does not exist
            got = e
        assert got == ("input.unsupported", "options"), "no such mode: %s %s %ds: %r" % (model, mode, seconds, got)
    assert refusal(VIDEO, images=(IMG,) * 3) == ("input.too_many_images", "images"), "three frames"
    for model, most in ((VIDEO, 3), (FAST, 3), (OMNI, 7), (OMNI_360, 7)):
        assert plan(model, images=(IMG,) * most, options={"mode": "references"}).operation == "references_to_video"
        assert refusal(model, images=(IMG,) * (most + 1), options={"mode": "references"}) == (
            "input.too_many_images", "images"), "too many references: %s" % model
    assert refusal(VIDEO, images=(IMG,), options={"mode": "start"}) == ("input.invalid", "options"), "an unknown mode"
    assert plan(VIDEO, options={"mode": "references"}).operation == "text_to_video", "no image: text to video"
    assert refusal(VIDEO, n=2) == ("input.invalid", "n")
    with pytest.raises(pricing.TooCostly):           # more credits than a day's budget: no account could take it
        priced(VIDEO, cfg=FakeSettings(SPEC, flow__pools={"video": day_pool({"free": 9})}))


@mutation(SPEC_FILE, '"budget": {"free": 0, "pro": 1050,', '"budget": {"pro": 1050,',
          expect="a free account could take")
@mutation(SPEC_FILE, '"budget": {"free": 0, "pro": 0,', '"budget": {"free": 0, "pro": 1000,',
          expect="a pro account could take")
@mutation(SPEC_FILE, "            pool=C.video_pool(req.model, duration), submit_timeout_s=SUBMIT_TIMEOUT_S)",
          '            pool="video", submit_timeout_s=SUBMIT_TIMEOUT_S)', expect="a pro account could take")
def test_the_claim_never_offers_quality_to_a_free_account_nor_4_s_below_ultra():
    """Codex review 25/10: the tier is the pool's budget, so the claim (`can_afford`) passes over an account whose plan
    lacks the option: with three free accounts ahead of a Pro one, a Quality job can only land on the Pro one (the
    plan read before the send stays as a backstop that uses no attempt)."""
    cfgs = pool_configs("flow", SPEC.caps, FakeSettings(SPEC).get)
    now = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)

    def takers(plan, tiers):
        p = pricing.price_plan(SPEC, plan, FakeSettings(SPEC))
        return [i for i, tier in enumerate(tiers)
                if can_afford(left_of(cfgs[p.pool], tier, [], now), 0, p.account_cost)]
    accounts = ["free", "free", "free", "pro"]
    assert takers(plan(QUALITY), accounts) == [3], "a free account could take a Quality job"
    assert takers(plan(QUALITY), [None, "pro"]) == [1], "a free account could take a Quality job (tier never read)"
    assert takers(plan(VIDEO, duration=4), accounts + ["ultra"]) == [4], "a pro account could take Veo 4 s"
    assert takers(plan(VIDEO), accounts) == [0, 1, 2, 3], "Lite 8 s runs on every plan"
    r = errors.rule("flow.tier_required")
    assert (r.counts_attempt, r.terminal) == (False, False) and 0 < r.free_retries < 10, \
        "the plan backstop uses no attempt, a bounded number of times"
