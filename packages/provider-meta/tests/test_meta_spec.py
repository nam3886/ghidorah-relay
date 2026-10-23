"""The meta spec: caps, settings, catalog next to dola's, error rules and `plan()`."""

import logging

import pytest

from relay_core import errors
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
from relay_meta import errors as meta_errors
from relay_meta.spec import SPEC

SPEC_FILE = "packages/provider-meta/src/relay_meta/spec.py"
IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)


def settings(**overrides) -> FakeSettings:
    """meta's own defaults; `meta__prices=...` overrides `meta.prices`."""
    return FakeSettings(SPEC, **overrides)


def plan(model="meta-image", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), cfg or settings(), {})


def refused(model="meta-image", prompt="a lighthouse at dawn", **kw) -> InputError:
    with pytest.raises(InputError) as caught:
        plan(model, prompt, **kw)
    return caught.value


# ---- caps, settings, catalog -----------------------------------------------------------------------------------------
def test_the_caps_say_a_browser_provider_with_two_pools_that_reconciles_and_polls_what_it_found():
    c = SPEC.caps
    assert SPEC.name == "meta" and c.execution == "browser" and c.wallet_kinds == ("image", "video")
    assert [(p.name, p.tz, p.budget_setting) for p in c.pools] == [
        ("image", "UTC", "meta.image_budget"), ("video", "UTC", "meta.video_budget")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold) == (1, 3, 3)
    assert c.strike_effect == Dead("signed_out")
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (True, True, False, False)
    assert SPEC.frames is None and SPEC.extras({}) == {}


@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="meta must stay drained")
def test_meta_stays_drained_by_default_and_declares_the_settings_the_core_reads():
    s = SPEC.settings()
    assert s["meta.claims_enabled"].default is False, "meta must stay drained until it can submit"
    assert s["meta.image_budget"].default == 40 and s["meta.video_budget"].default == 20
    assert s["meta.prices"].default == {"meta-image": 1, "meta-video": 1}
    for key in ("meta.image_submit_timeout_s", "meta.video_submit_timeout_s"):
        assert s[key].max == 720, "a submit timeout past the worker's stop grace"
    rest = s["meta.checkpoint_rest_s"]
    assert (rest.type, rest.default, rest.min) == (int, 1800, 300) and "checkpoint" in rest.description
    assert _problems(SPEC) == []


def test_the_prices_setting_refuses_a_bad_table():
    rule = SPEC.settings()["meta.prices"].rule
    for bad in ({"meta-image": 1.5}, {"meta-image": -1}, {" ": 1}, {"meta-image": True}):
        with pytest.raises(ValueError):
            rule.check(bad)
    rule.check({"meta-image": 0, "meta-video": 3})


def test_the_registry_takes_meta_beside_dola_and_resolves_both_models():
    reg = ProviderRegistry()
    reg.register(DOLA)
    reg.register(SPEC)
    assert reg.names() == ["dola", "meta"]
    image, video = reg.resolve("meta-image"), reg.resolve("meta-video")
    assert image is not None and image[0] is SPEC and image[1].operations == ("text_to_image", "image_to_image")
    assert video is not None and video[0] is SPEC and video[1].operations == ("text_to_video", "image_to_video")
    assert image[1].limits["max_images"] == video[1].limits["max_images"] == 1, "one input image, each model"


# ---- error rules -----------------------------------------------------------------------------------------------------
def test_meta_registers_the_codes_its_submit_uses_and_a_warning_is_no_rule():
    meta_codes = {code for code in errors.RULES if code.startswith("meta.")}
    assert set(SPEC.codes()) == meta_codes == {"meta.checkpoint", "meta.ui_page_not_ready", "meta.ui_send_failed",
                                               "meta.no_image", "meta.no_video"}
    assert meta_errors.WARN_PRICE_FALLBACK not in errors.RULES, "a warning is not a rule"


# ---- plan ------------------------------------------------------------------------------------------------------------
def test_an_image_plan_is_held_in_the_session_and_priced_from_the_settings():
    p = plan()
    assert (p.operation, p.media_type, p.model, dict(p.params)) == ("text_to_image", "image", "meta-image",
                                                                     {"kind": "image"})
    assert (p.completion, p.after_sent, p.wallet_kind, p.pool) == (Completion.HOLD, AfterSent.RECONCILE, "image",
                                                                   "image")
    assert (p.price, p.account_cost, p.submit_timeout_s, p.warnings) == (1, 1, 240, ())


@mutation(SPEC_FILE, '            operation = "image_to_video" if req.images else "text_to_video"',
          '            operation = "text_to_video"', expect="a video from an image")
def test_a_video_plan_takes_one_image_and_an_optional_motion_text():
    p = plan("meta-video", "", images=(IMG,))
    assert (p.operation, p.media_type, p.wallet_kind, p.pool, p.submit_timeout_s) == (
        "image_to_video", "video", "video", "video", 420), "a video from an image: %r" % (p.operation,)
    assert plan("meta-video", "slow pan to the left", images=(IMG,)).completion == Completion.HOLD


def test_a_video_plan_from_text_alone_is_text_to_video_held_and_priced_like_one_from_an_image():
    p = plan("meta-video", "a red fox trotting through snow")
    assert (p.operation, p.media_type, p.wallet_kind, p.pool, p.submit_timeout_s, p.price) == (
        "text_to_video", "video", "video", "video", 420, 1)
    assert (p.completion, p.after_sent, dict(p.params)) == (Completion.HOLD, AfterSent.RECONCILE, {"kind": "video"})


@mutation(SPEC_FILE, "            if not req.images and C.is_blank_prompt(req.prompt):", "            if False:",
          expect="a video from no image and no text")
def test_a_video_with_neither_an_image_nor_a_visible_text_is_refused():
    for blank in ("", "   ", "\u200b\u200b"):
        try:
            planned = plan("meta-video", blank)
        except InputError as e:
            assert (e.code, e.param) == ("input.empty_prompt", "prompt"), (e.code, blank)
        else:
            pytest.fail("planned a video from no image and no text: %r -> %r" % (blank, planned))


@mutation(SPEC_FILE, '            operation = "image_to_image" if req.images else "text_to_image"',
          '            operation = "text_to_image"', expect="an edit of the input image")
def test_an_image_plan_with_one_input_image_is_an_edit_and_still_needs_a_visible_text():
    p = plan(images=(IMG,))
    assert (p.operation, p.media_type, p.wallet_kind, p.pool, p.submit_timeout_s) == (
        "image_to_image", "image", "image", "image", 240), "an edit of the input image: %r" % (p.operation,)
    assert (p.completion, p.after_sent, dict(p.params)) == (Completion.HOLD, AfterSent.RECONCILE, {"kind": "image"})
    with pytest.raises(InputError) as caught:
        plan(prompt=" ", images=(IMG,))
    assert caught.value.code == "input.empty_prompt"


def test_the_price_and_the_timeout_come_from_the_live_settings():
    cfg = settings(meta__prices={"meta-image": 3, "meta-video": 5}, meta__video_submit_timeout_s=600)
    assert plan(cfg=cfg).price == 3
    p = plan("meta-video", images=(IMG,), cfg=cfg)
    assert (p.price, p.submit_timeout_s) == (5, 600)


@mutation(SPEC_FILE, "        if missing is None:\n            return price, []",
          "        if True:\n            return price, []", expect="a missing price must warn")
def test_a_model_missing_from_the_prices_is_charged_the_fallback_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        p = plan(cfg=settings(meta__prices={"meta-video": 2}))
    assert p.price == 1
    assert [w.code for w in p.warnings] == ["meta.price_fallback"], "a missing price must warn"
    assert "meta-image" in caplog.text


@mutation(SPEC_FILE, "            if C.is_blank_prompt(req.prompt):", "            if False:",
          expect="an image prompt that shows nothing was planned")
def test_an_image_prompt_that_shows_nothing_is_refused():
    for blank in ("", "   ", "​​", " ⁠﻿\n"):
        try:
            planned = plan(prompt=blank)
        except InputError as e:
            assert (e.code, e.param) == ("input.empty_prompt", "prompt"), (e.code, blank)
        else:
            pytest.fail("an image prompt that shows nothing was planned: %r -> %r" % (blank, planned))


@mutation(SPEC_FILE, "        if len(req.images) > 1:", "        if len(req.images) > 2:", expect="two input images")
def test_a_request_with_two_input_images_is_refused_by_either_model():
    for model in ("meta-image", "meta-video"):
        try:
            planned = plan(model, images=(IMG, IMG))
        except InputError as e:
            assert (e.code, e.param) == ("input.too_many_images", "images"), (model, e.code)
        else:
            pytest.fail("planned %s with two input images: %r" % (model, planned))


@mutation(SPEC_FILE, "        if req.duration is not None:", "        if False:", expect="a duration was ignored")
def test_a_duration_is_refused_rather_than_ignored():
    for model, images in (("meta-image", ()), ("meta-video", (IMG,))):
        try:
            planned = plan(model, images=images, duration=10)
        except InputError as e:
            assert e.code == "input.bad_duration", (model, e.code)
        else:
            pytest.fail("a duration was ignored: %s -> %r" % (model, planned))


@mutation(SPEC_FILE, '            params={"kind": kind, **({"ratio": req.ratio} if req.ratio else {})}, completion',
          '            params={"kind": kind}, completion', expect="the ratio reaches the engine")
def test_a_proven_ratio_from_text_alone_reaches_the_engine_and_keeps_the_model_name():
    for model, ratios in (("meta-image", ("16:9", "9:16", "1:1", "4:3", "3:4")), ("meta-video", ("16:9", "9:16"))):
        for ratio in ratios:
            p = plan(model, "a red fox in the snow, %s" % ratio, ratio=ratio)    # the same ratio in the prompt is fine
            assert (p.model, p.params.get("ratio")) == (model, ratio), "the ratio reaches the engine: %r" % (p.params,)
    assert "ratio" not in plan("meta-video").params, "no ratio sent: the job is unchanged"


@mutation(SPEC_FILE, "        if req.ratio is not None:", "        if False:", expect="a ratio meta never honoured")
@mutation(SPEC_FILE, "            if req.images or req.ratio not in RATIOS[kind]:",
          "            if req.ratio not in RATIOS[kind]:", expect="a ratio with an input image")
@mutation(SPEC_FILE, "            if req.images or req.ratio not in RATIOS[kind]:", "            if req.images:",
          expect="a ratio meta never honoured")
def test_an_unproven_ratio_or_a_ratio_with_an_input_image_is_refused():
    for model, ratio in (("meta-video", "1:1"), ("meta-video", "4:3"),("meta-image", "21:9"), ("meta-image", "2:3")):
        try:
            planned = plan(model, ratio=ratio)
        except InputError as e:
            assert (e.code, e.param) == ("input.bad_ratio", "ratio"), (model, ratio, e.code)
        else:
            pytest.fail("a ratio meta never honoured: %s %s -> %r" % (model, ratio, planned))
    for model in ("meta-image", "meta-video"):
        try:
            planned = plan(model, images=(IMG,), ratio="9:16")
        except InputError as e:
            assert (e.code, e.param) == ("input.bad_ratio", "ratio"), (model, e.code)
        else:
            pytest.fail("a ratio with an input image: %s -> %r" % (model, planned))


@mutation(SPEC_FILE, "            if other:", "            if False:", expect="a prompt naming another ratio")
def test_a_prompt_that_names_another_ratio_is_refused_but_a_time_is_not_a_ratio():
    try:
        planned = plan("meta-image", "a red fox, 9:16, in the snow", ratio="16:9")
    except InputError as e:
        assert e.code == "input.prompt_ratio_conflict" and "9:16" in e.detail, (e.code, e.detail)
    else:
        pytest.fail("a prompt naming another ratio: %r" % (planned,))
    assert plan("meta-image", "a station clock at 10:30, 1:16:9 scale", ratio="16:9").params["ratio"] == "16:9"
    assert plan("meta-image", "a red fox, 9:16").params == {"kind": "image"}, "no ratio sent: the prompt is not read"


@mutation(SPEC_FILE, "        if kind is None:", "        if False:", expect="an unknown model")
def test_an_unknown_or_extended_model_name_is_refused():
    for name in ("meta-image-hd", "seedance-2.5", "meta"):
        try:
            planned = plan(name)
        except InputError as e:
            assert (e.code, e.param) == ("input.bad_model", "model"), "an unknown model: %s -> %s" % (name, e.code)
        except Exception as e:      # noqa: BLE001 - past the model check a text-only plan reaches the settings
            pytest.fail("an unknown model got past the model check: %s -> %r" % (name, e))
        else:
            pytest.fail("an unknown model was planned: %s -> %r" % (name, planned))


def test_the_options_never_set_the_price():
    assert plan(options={"price": 0}).price == 1
