"""The qwen spec (caps, settings, catalog, error rules, `plan()`) and the credential import."""

import json

import pytest
from qwenkit import BLOB, BX_UA, PAIR, REFRESH, export, minted_blob, rec

from relay_core import errors
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


def refusal(model="qwen-image-3.0", prompt="a lighthouse at dawn", **kw):
    try:
        plan(model, prompt, **kw)
    except InputError as e:
        return e.code, e.param
    return None


# ---- caps, settings, catalog, codes --------------------------------------------------------------------------------
def test_the_caps_say_an_http_provider_with_a_video_credit_pool_and_an_image_pool():
    c = SPEC.caps
    assert SPEC.name == "qwen" and c.execution == "http" and c.wallet_kinds == ("video", "image")
    assert [(p.name, p.budget_setting) for p in c.pools] == [("video", "qwen.video_budget"),
                                                              ("image", "qwen.image_budget")]
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
                "refusal_grace_s", "prices", "image_budget", "video_budget", "disabled_models"):
        assert "qwen." + key in s, key
    assert s["qwen.video_budget"].default == 40          # a free account's daily credits
    assert {k for k, v in s.items() if v.basic} == {"qwen.claims_enabled", "qwen.prices", "qwen.image_budget",
                                                    "qwen.video_budget", "qwen.disabled_models"}


def test_the_registry_takes_qwen_beside_every_other_provider():
    registry = ProviderRegistry()
    for spec in (DOLA, META, VIBES, FLOW, SPEC):
        registry.register(spec)
    assert registry.resolve("qwen-happyhorse-1.1")[0] is SPEC
    assert [m.id for m in SPEC.catalog()] == ["qwen-image-3.0", "qwen-happyhorse-1.1", "qwen-happyhorse-1.0"]


def test_every_code_is_registered_and_the_firewall_refusal_is_a_free_requeue():
    for code, rule in SPEC.codes().items():
        assert errors.RULES[code] == rule
    waf = errors.RULES["qwen.waf_refused"]
    assert (waf.terminal, waf.counts_attempt, waf.free_retries) == (False, False, 2)
    for code in ("qwen.refused", "qwen.out_of_credits", "qwen.render_failed", "qwen.image_lost"):
        assert errors.RULES[code].terminal and errors.RULES[code].refund, code


@mutation(CATALOG_FILE, '    "qwen-happyhorse-1.1": {5: 30, 10: 60, 15: 90},',
          '    "qwen-happyhorse-1.1": {5: 20, 10: 60, 15: 90},', expect="hh11T2v720P5s")
def test_the_credits_of_a_video_are_qwens_own_price_list():
    scenes = {s["scene"]: s["consumeAmount"] for s in rec("credits_pricing")["body"]["data"]["video_generation"]}
    names = {"qwen-happyhorse-1.1": "hh11", "qwen-happyhorse-1.0": "hh"}
    for model, by_seconds in C.VIDEO_CREDITS.items():
        for seconds, credits in by_seconds.items():
            for op in ("T2v", "I2v"):
                scene = "%s%s720P%ds" % (names[model], op, seconds)
                assert scenes[scene] == credits, "%s: %s credits, the list says %s" % (scene, credits, scenes[scene])


# ---- plan ----------------------------------------------------------------------------------------------------------
def test_an_image_is_held_in_the_image_pool_at_16_9_unless_a_ratio_is_asked():
    p = plan()
    assert (p.operation, p.media_type, p.completion, p.after_sent, p.pool, p.account_cost) == (
        "text_to_image", "image", Completion.HOLD, AfterSent.RECONCILE, "image", 1)
    assert dict(p.params) == {"kind": "image", "model": "qwen-image-3.0", "ratio": "16:9"}
    assert plan(ratio="3:2").params["ratio"] == "3:2"
    assert plan(images=(IMG,)).operation == "image_to_image"


@mutation(SPEC_FILE, "            cost = credits\n", "            cost = 1\n", expect="account cost")
def test_a_video_is_detached_in_the_video_pool_and_costs_the_account_its_credits():
    p = plan("qwen-happyhorse-1.1")
    assert (p.operation, p.media_type, p.completion, p.pool) == ("text_to_video", "video", Completion.DETACH, "video")
    assert p.account_cost == 30, "the account cost is the video's qwen credits: %s" % p.account_cost
    assert dict(p.params) == {"kind": "video", "model": "happyhorse11", "ratio": "16:9", "duration": 5,
                              "credits": 30, "resolution": "720P"}
    assert plan("qwen-happyhorse-1.0", images=(IMG,)).params["model"] == "happyhorse"
    assert plan("qwen-happyhorse-1.0", images=(IMG,)).operation == "image_to_video"


@mutation(SPEC_FILE, "            if credits > budget:\n", "            if False:\n", expect="more credits than")
def test_a_video_that_needs_more_credits_than_an_account_has_a_day_is_refused():
    assert refusal("qwen-happyhorse-1.1", duration=10) == ("input.bad_duration", "duration"), \
        "a video that needs more credits than an account has a day was taken"
    cfg = FakeSettings(SPEC, qwen__video_budget=100)
    assert plan("qwen-happyhorse-1.1", duration=10, cfg=cfg).account_cost == 60


@pytest.mark.parametrize("kw, want", [
    (dict(model="nope"), ("input.bad_model", "model")),
    (dict(images=(IMG, IMG)), ("input.too_many_images", "images")),
    (dict(n=2), ("input.invalid", "n")),
    (dict(prompt="  \n "), ("input.empty_prompt", "prompt")),
    (dict(ratio="21:9"), ("input.bad_ratio", "ratio")),
    (dict(model="qwen-happyhorse-1.1", ratio="3:2"), ("input.bad_ratio", "ratio")),
    (dict(duration=5), ("input.bad_duration", "duration")),
    (dict(model="qwen-happyhorse-1.0", duration=7), ("input.bad_duration", "duration")),
])
def test_what_qwen_cannot_make_is_refused_before_any_charge(kw, want):
    assert refusal(**kw) == want


@mutation(SPEC_FILE, 'self._price(settings, C.price_key(req.model, params.get("duration")))',
          "self._price(settings, req.model)", expect="price key")
def test_a_video_is_priced_by_model_and_seconds_and_a_missing_key_warns():
    cfg = FakeSettings(SPEC, qwen__prices={"qwen-image-3.0": 2, "qwen-happyhorse-1.1@5s": 7})
    assert plan(cfg=cfg).price == 2
    assert plan("qwen-happyhorse-1.1", cfg=cfg).price == 7, "price key"
    missing = plan("qwen-happyhorse-1.0", cfg=cfg)
    assert missing.price == 1 and [w.code for w in missing.warnings] == ["qwen.price_fallback"]


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
