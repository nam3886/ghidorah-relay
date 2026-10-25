"""The qwen spec (caps, settings, catalog, error rules, `plan()`) and the credential import."""

import json
from datetime import UTC, datetime

import pytest
from qwenkit import BLOB, BX_UA, PAIR, REFRESH, export, minted_blob, rec

from relay_core import errors, pricing
from relay_core.pool import can_afford, day_pool, left_of, pool_configs
from relay_core.providers import (
    AfterSent,
    Completion,
    CreateRequest,
    Dead,
    InputError,
    MediaIn,
    ProviderRegistry,
)
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA
from relay_flow.spec import SPEC as FLOW
from relay_meta.spec import SPEC as META
from relay_qwen import catalog as C
from relay_qwen import credential as cred
from relay_qwen.spec import SPEC
from relay_vibes.spec import SPEC as VIBES

SPEC_FILE = "packages/provider-qwen/src/relay_qwen/spec.py"
CATALOG_FILE = "packages/provider-qwen/src/relay_qwen/catalog.py"
CREDENTIAL_FILE = "packages/provider-qwen/src/relay_qwen/credential.py"
IMG = MediaIn(sha256="0" * 64, mime="image/jpeg", size=10)


def plan(model="qwen-image-3.0", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), cfg or FakeSettings(SPEC), {})


def priced(model="qwen-image-3.0", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    """The plan as the door makes it: the core's price and upstream cost from `qwen.prices` and `qwen.costs`."""
    cfg = cfg or FakeSettings(SPEC)
    return pricing.price_plan(SPEC, plan(model, prompt, cfg=cfg, **kw), cfg)


def refusal(model="qwen-image-3.0", prompt="a lighthouse at dawn", **kw):
    try:
        plan(model, prompt, **kw)
    except InputError as e:
        return e.code, e.param
    return None


# ---- caps, settings, catalog, codes --------------------------------------------------------------------------------
def test_the_caps_say_an_http_provider_with_a_video_credit_pool_and_an_image_pool():
    c = SPEC.caps
    assert SPEC.name == "qwen" and c.execution == "http"
    assert [(p.name, p.unit) for p in c.pools] == [("video", "credits"), ("image", "jobs"), ("video_pro", "credits"),
                                                   ("image_pro", "jobs")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold, c.strike_effect) == (1, 3, 3, Dead("signed_out"))
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy) == (True, True, False)
    # the check keeps the pair fresh: it comes at least twice in the pair's lifetime
    assert c.check_interval_s * 2 <= SPEC.settings()["qwen.bx_max_age_s"].default


@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="qwen must stay drained")
def test_qwen_stays_drained_by_default_and_declares_its_tunables():
    s = SPEC.settings()
    assert s["qwen.claims_enabled"].default is False, "qwen must stay drained until the owner turns it on"
    for key in ("render_deadline_s", "poll_interval_s", "account_min_gap_s", "bx_max_age_s", "image_lost_after_s",
                "refusal_grace_s", "prices", "costs", "pools", "disabled_models"):
        assert "qwen." + key in s, key
    # a free account's credits roll 24 h from their grant; images are 10 a UTC day
    pools = s["qwen.pools"]
    assert pools.validate(pools.default) == pools.default, "the shipped pools pass their own rule"
    assert {k: pools.default[k] for k in ("video", "image")} == {"video": {"combine": "sum", "windows": [
        {"name": "24h", "kind": "rolling", "hours": 24, "budget": {"free": 40}}]}, "image": day_pool({"free": 10})}
    assert {k for k, v in s.items() if v.basic} == {"qwen.claims_enabled", "qwen.prices", "qwen.disabled_models"}


def test_the_registry_takes_qwen_beside_every_other_provider():
    registry = ProviderRegistry()
    for spec in (DOLA, META, VIBES, FLOW, SPEC):
        registry.register(spec)
    assert registry.resolve("qwen-happyhorse-1.1")[0] is SPEC
    assert registry.resolve("qwen-wan-2.7")[0] is SPEC
    assert [m.id for m in SPEC.catalog()] == ["qwen-image-3.0", "qwen-image-3.0-pro", "qwen-happyhorse-1.1",
                                              "qwen-happyhorse-1.0", "qwen-wan-2.7"]


def test_every_code_is_registered_and_the_firewall_refusal_is_a_free_requeue():
    for code, rule in SPEC.codes().items():
        assert errors.RULES[code] == rule
    waf = errors.RULES["qwen.waf_refused"]
    assert (waf.terminal, waf.counts_attempt, waf.free_retries) == (False, False, 2)
    for code in ("qwen.refused", "qwen.out_of_credits", "qwen.render_failed", "qwen.image_lost"):
        assert errors.RULES[code].terminal and errors.RULES[code].refund, code


@mutation(CATALOG_FILE, '"720p": {5: 30, 10: 60, 15: 90}, "1080p": {5: 40,',
          '"720p": {5: 20, 10: 60, 15: 90}, "1080p": {5: 40,', expect="hh11T2v720P5s")
@mutation(CATALOG_FILE, '"720p": {5: 20, 10: 40, 15: 60}, "1080p": {5: 30,',
          '"720p": {5: 20, 10: 40, 15: 60}, "1080p": {5: 20,', expect="wan27T2v1080P5s")
def test_the_credits_of_a_video_are_qwens_own_price_list():
    """Every option's credits are the recorded list's (23/10), text and image to video alike, and the same as its
    multi-reference (HappyHorse) or first-last-frame (Wan) scene."""
    scenes = {s["scene"]: s["consumeAmount"] for s in rec("credits_pricing")["body"]["data"]["video_generation"]}
    names = {"qwen-happyhorse-1.1": "hh11", "qwen-happyhorse-1.0": "hh", "qwen-wan-2.7": "wan27"}
    for model, by_res in C.VIDEO_CREDITS.items():
        for res, by_seconds in by_res.items():
            for seconds, credits in by_seconds.items():
                tail = "%s%s" % (res.upper(), "" if seconds == 5 else "%ds" % seconds)
                found = ["%s%s%s%ds" % (names[model], op, res.upper(), seconds) for op in ("T2v", "I2v")] + [
                    names[model] + ("MultiRef" if C.VIDEO_MODELS[model][2] == "multi-reference" else "FrameI2v") + tail]
                for scene in found:
                    assert scenes[scene] == credits, "%s: %s credits, the list says %s" % (scene, credits,
                                                                                          scenes[scene])


# ---- plan ----------------------------------------------------------------------------------------------------------
def test_an_image_is_held_in_the_image_pool_at_16_9_unless_a_ratio_is_asked():
    p = plan()
    assert (p.operation, p.media_type, p.completion, p.after_sent, p.pool, priced().account_cost) == (
        "text_to_image", "image", Completion.HOLD, AfterSent.RECONCILE, "image", 1)
    assert dict(p.params) == {"kind": "image", "model": "qwen-image-3.0", "ratio": "16:9"}
    assert plan(ratio="3:2").params["ratio"] == "3:2"
    assert plan(images=(IMG,)).operation == "image_to_image"


CREDITS = '            credits = settings.get("qwen.costs").get(C.price_key(req.model, duration, resolution))'


@mutation(SPEC_FILE, CREDITS, "            credits = 1", expect="'credits': 1")
@mutation(CATALOG_FILE, "VIDEO_CREDITS[m][r][d] for m, r, d in _VIDEO_OPTIONS", "1 for m, r, d in _VIDEO_OPTIONS",
          expect="account cost")
def test_a_video_is_detached_in_the_video_pool_and_costs_the_account_its_credits():
    p = plan("qwen-happyhorse-1.1")
    assert (p.operation, p.media_type, p.completion, p.pool) == ("text_to_video", "video", Completion.DETACH, "video")
    cost = priced("qwen-happyhorse-1.1").account_cost
    assert cost == 30, "the account cost is the video's qwen credits: %s" % cost
    assert dict(p.params) == {"kind": "video", "model": "happyhorse11", "ratio": "16:9", "duration": 5,
                              "credits": 30, "resolution": "720P", "mode": "standard"}
    assert plan("qwen-happyhorse-1.0", images=(IMG,)).params["model"] == "happyhorse"
    assert plan("qwen-happyhorse-1.0", images=(IMG,)).operation == "image_to_video"


def test_a_video_that_needs_more_credits_than_an_account_has_a_day_is_refused():
    with pytest.raises(pricing.TooCostly):
        priced("qwen-happyhorse-1.1", duration=10)
    cfg = FakeSettings(SPEC, qwen__pools={"video": day_pool({"free": 100})})
    assert priced("qwen-happyhorse-1.1", duration=10, cfg=cfg).account_cost == 60


@pytest.mark.parametrize("kw, want", [
    (dict(model="nope"), ("input.bad_model", "model")),
    (dict(images=(IMG,) * 4), ("input.too_many_images", "images")),
    (dict(model="qwen-image-3.0-pro", images=(IMG,) * 4), ("input.too_many_images", "images")),
    (dict(model="qwen-happyhorse-1.1", images=(IMG,) * 10), ("input.too_many_images", "images")),
    (dict(model="qwen-wan-2.7", images=(IMG,) * 3), ("input.too_many_images", "images")),
    (dict(model="qwen-wan-2.7", options={"resolution": "4k"}), ("input.invalid", "options")),
    (dict(options={"resolution": "720p"}), ("input.invalid", "options")),
    (dict(ratio="auto"), ("input.bad_ratio", "ratio")),
    (dict(n=2), ("input.invalid", "n")),
    (dict(prompt="  \n "), ("input.empty_prompt", "prompt")),
    (dict(ratio="21:9"), ("input.bad_ratio", "ratio")),
    (dict(model="qwen-happyhorse-1.1", ratio="3:2"), ("input.bad_ratio", "ratio")),
    (dict(duration=5), ("input.bad_duration", "duration")),
    (dict(model="qwen-happyhorse-1.0", duration=7), ("input.bad_duration", "duration")),
])
def test_what_qwen_cannot_make_is_refused_before_any_charge(kw, want):
    assert refusal(**kw) == want


@mutation(SPEC_FILE, 'price_key=C.price_key(req.model, params.get("duration"), resolution)', "price_key=req.model",
          expect="no price for qwen-happyhorse-1.1")
def test_a_video_is_priced_by_model_and_seconds_and_a_missing_key_is_refused():
    cfg = FakeSettings(SPEC, qwen__prices={"qwen-image-3.0": 2, "qwen-happyhorse-1.1@5s": 7})
    assert priced(cfg=cfg).price == 2
    assert priced("qwen-happyhorse-1.1", cfg=cfg).price == 7, "price key"
    with pytest.raises(pricing.Unpriced):
        priced("qwen-happyhorse-1.0", cfg=cfg)


# ---- the full catalog (25/10): Wan 2.7, 1080p, reference images, first and last frames, the pro options ----------
P1080 = {"resolution": "1080p"}
VIDEO_POOL = '    return "video_pro" if resolution == "1080p" and model in PRO_1080P else "video"'


@mutation(CATALOG_FILE, VIDEO_POOL, '    return "video"', expect="HappyHorse 1080P outside video_pro")
@mutation(CATALOG_FILE, 'PRO_1080P = ("qwen-happyhorse-1.1", "qwen-happyhorse-1.0")',
          'PRO_1080P = ("qwen-happyhorse-1.1", "qwen-happyhorse-1.0", "qwen-wan-2.7")', expect="Wan 1080P")
def test_wan_is_a_free_video_at_720p_or_1080p_and_happyhorse_1080p_draws_on_the_pro_pool():
    wan = priced("qwen-wan-2.7")
    assert (wan.pool, wan.price_key, wan.account_cost, wan.price) == ("video", "qwen-wan-2.7@5s", 20, 1)
    assert dict(wan.params) == {"kind": "video", "model": "wan27", "ratio": "16:9", "duration": 5, "credits": 20,
                                "resolution": "720P", "mode": "standard"}
    wan_hd = priced("qwen-wan-2.7", options=P1080)
    assert (wan_hd.pool, wan_hd.price_key, wan_hd.account_cost, wan_hd.params["resolution"]) == (
        "video", "qwen-wan-2.7@1080p@5s", 30, "1080P"), "Wan 1080P is for every plan: %s" % wan_hd.pool
    for model, credits in (("qwen-happyhorse-1.1", 40), ("qwen-happyhorse-1.0", 70)):
        hd = priced(model, options=P1080)
        assert hd.pool == "video_pro", "HappyHorse 1080P outside video_pro: %s" % hd.pool
        assert (hd.price_key, hd.account_cost, hd.price) == ("%s@1080p@5s" % model, credits, 2)
    # the longest pro option still fits the pro budget (never refused as too costly), the free one stays refused
    assert priced("qwen-happyhorse-1.0", options=P1080, duration=15).account_cost == 210
    with pytest.raises(pricing.TooCostly):
        priced("qwen-wan-2.7", options=P1080, duration=15)


PRO_FREE = '"budget": {"free": 0, "pro": 10500}}]},'
IMAGE_PRO_FREE = '"image_pro": day_pool({"free": 0, "pro": 100})}'


@mutation(SPEC_FILE, PRO_FREE, '"budget": {"pro": 10500}}]},', expect="a free account could take")
@mutation(SPEC_FILE, IMAGE_PRO_FREE, '"image_pro": day_pool({"pro": 100})}', expect="a free account could take")
@mutation(SPEC_FILE, PRO_FREE, '"budget": {"free": 0}}]},', expect="a pro account had no budget")
def test_only_a_pro_account_can_ever_afford_a_pro_option():
    """The tier gate is the pool's budget: 0 for a free account (and one whose tier was never read, as a go or plus
    plan stays), so the claim never picks it; a pro account (`memberType: pro`) has its own budget."""
    cfgs = pool_configs("qwen", SPEC.caps, FakeSettings(SPEC).get)
    now = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
    for pool in ("video_pro", "image_pro"):
        for tier in ("free", None):
            left = left_of(cfgs[pool], tier, [], now)
            assert left == 0 and not can_afford(left, 0, 1), "a free account could take %s: %s" % (pool, left)
        assert left_of(cfgs[pool], "pro", [], now), "a pro account had no budget in %s" % pool
    assert left_of(cfgs["video"], "free", [], now) == 40 and left_of(cfgs["image"], "free", [], now) == 10


IMAGE_POOL = '        return IMAGE_MODELS[model][2]'


@mutation(CATALOG_FILE, IMAGE_POOL, '        return "image"', expect="image_pro")
def test_the_pro_image_model_draws_on_the_pro_image_pool_with_the_auto_ratio():
    p = priced("qwen-image-3.0-pro")
    assert (p.pool, p.price_key, p.account_cost, p.price) == ("image_pro", "qwen-image-3.0-pro", 1, 2)
    assert dict(p.params) == {"kind": "image", "model": "qwen-image-3.0-pro", "ratio": "auto"}
    assert plan("qwen-image-3.0-pro", ratio="2:3").params["ratio"] == "2:3"
    assert C.pool_of("qwen-image-3.0-pro", p.params) == "image_pro", "the engine's pool differs from the plan's"
    assert C.pool_of("qwen-image-3.0", plan().params) == "image"
    edit = plan("qwen-image-3.0-pro", images=(IMG,) * 3)
    assert (edit.operation, edit.pool) == ("image_to_image", "image_pro")


MODE = '            mode = "standard" if len(req.images) <= 1 else video[2]'


@mutation(SPEC_FILE, MODE, '            mode = "standard"', expect="multi-reference")
def test_more_than_one_image_is_happyhorses_reference_mode_or_wans_first_and_last_frames():
    assert plan("qwen-happyhorse-1.1", images=(IMG,)).params["mode"] == "standard"
    refs = plan("qwen-happyhorse-1.1", images=(IMG,) * 9)
    assert (refs.operation, refs.params["mode"]) == ("image_to_video", "multi-reference")
    assert plan("qwen-happyhorse-1.0", images=(IMG,) * 2).params["mode"] == "multi-reference"
    assert plan("qwen-wan-2.7", images=(IMG,)).params["mode"] == "standard"
    frames2 = plan("qwen-wan-2.7", images=(IMG,) * 2)
    assert (frames2.operation, frames2.params["mode"]) == ("image_to_video", "first-last-frame")
    assert plan("qwen-image-3.0", images=(IMG,) * 3).operation == "image_to_image"
    limits = {m.id: m.limits for m in SPEC.catalog()}
    assert [limits[m]["max_images"] for m in limits] == [3, 3, 9, 9, 2]
    assert limits["qwen-wan-2.7"]["resolutions"] == ["720p", "1080p"]


def test_every_price_key_has_a_price_and_a_cost_and_every_catalog_option_names_one():
    assert set(C.DEFAULT_PRICES) == set(C.DEFAULT_COSTS) == set(C.PRICE_KEYS)
    named = {k for m in SPEC.catalog() for k in m.price_keys.values()}
    assert named == set(C.PRICE_KEYS)
    # the keys the owner already priced keep their names
    assert {"qwen-image-3.0", "qwen-happyhorse-1.1@5s", "qwen-happyhorse-1.0@15s"} <= named


# ---- credential ----------------------------------------------------------------------------------------------------
def test_an_export_keeps_the_qwen_ai_cookies_and_needs_the_refresh_token():
    sealed = json.loads(BLOB)
    assert sealed["bx"] is None and sealed["minted_at"] == 0.0
    assert {(c["domain"], c["name"]) for c in sealed["cookies"]} == {(".qwen.ai", "refresh_token"),
                                                                     ("chat.qwen.ai", "ssxmod_itna")}
    for bad in (b"", b"not json", json.dumps([e for e in export() if e["name"] != "refresh_token"]).encode(),
                json.dumps([{**export()[0], "value": "a;b"}]).encode()):
        with pytest.raises(ValueError) as e:
            cred.parse_credential(bad)
        assert REFRESH not in str(e.value)


@mutation(CREDENTIAL_FILE, '        if name not in BX_HEADERS or not isinstance(value, str)',
          '        if not isinstance(value, str)', expect="DID NOT RAISE")
def test_the_sealed_shape_round_trips_with_its_pair_and_only_clean_kept_headers():
    back = cred.cred_of(cred.parse_credential(minted_blob()).blob)
    assert back.bx == PAIR and back.minted_at > 0
    for bad in ({**PAIR, "cookie": "x"}, {**PAIR, "bx-ua": "a\r\nb"}, {"bx-v": "2.5.37"}):
        blob = json.dumps({"cookies": export(), "bx": bad, "minted_at": 1}).encode()
        with pytest.raises(ValueError) as e:
            cred.parse_credential(blob)
        assert BX_UA not in str(e.value)


@mutation(CREDENTIAL_FILE, "    minted = mine if mine.minted_at >= other.minted_at else other\n",
          "    minted = mine if mine.minted_at > other.minted_at else other\n", expect="the drop of the same pair")
def test_a_renewal_merge_keeps_the_newer_mint_and_a_drop_of_the_same_pair_wins():
    old, new = minted_blob(100.0), minted_blob(200.0)
    assert cred.cred_of(cred.merge_blobs(new, old)).minted_at == 200.0, "an older mint undid a newer one"
    assert cred.cred_of(cred.merge_blobs(old, new)).minted_at == 200.0
    dropped = cred.cred_of(old)
    dropped.bx = None
    assert cred.cred_of(cred.merge_blobs(old, dropped.blob())).bx is None, "the drop of the same pair was lost"


SESSION = (".qwen.ai", "/", "refresh_token")


@mutation(CREDENTIAL_FILE, "    refreshed = mine if mine.refreshed_at >= other.refreshed_at else other\n",
          "    refreshed = minted\n", expect="a newer mint dropped the rotated session token")
def test_a_renewal_merge_keeps_the_pair_by_its_mint_and_the_session_token_by_its_refresh():
    rotated = cred.cred_of(minted_blob(100.0))           # an older pair, but a session token rotated at 150
    rotated.cookies[SESSION], rotated.refreshed_at = "rt-rotated", 150.0
    newer_mint = cred.cred_of(minted_blob(200.0))        # a newer pair, the session token as imported
    for stored, mine in ((newer_mint, rotated), (rotated, newer_mint)):
        merged = cred.cred_of(cred.merge_blobs(stored.blob(), mine.blob()))
        assert merged.cookies[SESSION] == "rt-rotated", "a newer mint dropped the rotated session token"
        assert (merged.minted_at, merged.refreshed_at, merged.bx) == (200.0, 150.0, PAIR)
        assert ("chat.qwen.ai", "/", "cna") in merged.cookies


@mutation(SPEC_FILE, '        return "render.deadline"\n', '        return "qwen.http_error"\n',
          expect="refunded at the deadline")
def test_an_unknown_job_is_ended_at_its_render_deadline_with_a_refund_and_never_sent_again():
    cause = SPEC.deadline_cause("", {"chat": "c1"})
    rule = errors.RULES[cause]
    assert cause == "render.deadline" and rule.terminal and rule.refund, "not refunded at the deadline: %s" % cause
