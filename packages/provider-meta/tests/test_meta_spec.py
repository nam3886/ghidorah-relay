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
def test_the_caps_say_a_browser_provider_with_two_pools_and_no_poll():
    c = SPEC.caps
    assert SPEC.name == "meta" and c.execution == "browser" and c.wallet_kinds == ("image", "video")
    assert [(p.name, p.tz, p.budget_setting) for p in c.pools] == [
        ("image", "UTC", "meta.image_budget"), ("video", "UTC", "meta.video_budget")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold) == (1, 3, 3)
    assert c.strike_effect == Dead("signed_out")
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (False, False, False, False)
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
    assert image is not None and image[0] is SPEC and image[1].operations == ("text_to_image",)
    assert video is not None and video[0] is SPEC and video[1].operations == ("image_to_video",)


# ---- error rules -----------------------------------------------------------------------------------------------------
def test_meta_registers_the_codes_its_submit_uses_and_a_warning_is_no_rule():
    meta_codes = {code for code in errors.RULES if code.startswith("meta.")}
    assert set(SPEC.codes()) == meta_codes == {"meta.checkpoint", "meta.ui_page_not_ready", "meta.ui_send_failed",
                                               "meta.no_image"}
    assert meta_errors.WARN_PRICE_FALLBACK not in errors.RULES, "a warning is not a rule"


# ---- plan ------------------------------------------------------------------------------------------------------------
def test_an_image_plan_is_held_in_the_session_and_priced_from_the_settings():
    p = plan()
    assert (p.operation, p.media_type, p.model, dict(p.params)) == ("text_to_image", "image", "meta-image",
                                                                     {"kind": "image"})
    assert (p.completion, p.after_sent, p.wallet_kind, p.pool) == (Completion.HOLD, AfterSent.RECONCILE, "image",
                                                                   "image")
    assert (p.price, p.account_cost, p.submit_timeout_s, p.warnings) == (1, 1, 240, ())


def test_a_video_plan_takes_one_image_and_an_optional_motion_text():
    p = plan("meta-video", "", images=(IMG,))
    assert (p.operation, p.media_type, p.wallet_kind, p.pool, p.submit_timeout_s) == (
        "image_to_video", "video", "video", "video", 420)
    assert plan("meta-video", "slow pan to the left", images=(IMG,)).completion == Completion.HOLD


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


@mutation(SPEC_FILE, "            if req.images:\n", "            if False:\n", expect="image with an input image")
def test_an_image_request_with_an_input_image_is_refused():
    try:
        planned = plan(images=(IMG,))
    except InputError as e:
        assert (e.code, e.param) == ("input.too_many_images", "images")
    else:
        pytest.fail("planned an image with an input image: %r" % (planned,))


@mutation(SPEC_FILE, "            if not req.images:\n", "            if False:\n", expect="video without an image")
@mutation(SPEC_FILE, "            if len(req.images) > 1:", "            if len(req.images) > 2:",
          expect="video with two images")
def test_a_video_request_needs_exactly_one_image():
    for images, what in (((), "video without an image"), ((IMG, IMG), "video with two images")):
        try:
            planned = plan("meta-video", images=images)
        except InputError as e:
            assert e.param == "images" and e.code in ("input.invalid", "input.too_many_images"), (e.code, what)
        else:
            pytest.fail("planned a %s: %r" % (what, planned))


@mutation(SPEC_FILE, "        if req.ratio is not None:", "        if False:", expect="a ratio was ignored")
@mutation(SPEC_FILE, "        if req.duration is not None:", "        if False:", expect="a duration was ignored")
def test_a_ratio_or_a_duration_is_refused_rather_than_ignored():
    for model, images in (("meta-image", ()), ("meta-video", (IMG,))):
        for kw, code, what in ((dict(ratio="16:9"), "input.bad_ratio", "a ratio was ignored"),
                               (dict(duration=10), "input.bad_duration", "a duration was ignored")):
            try:
                planned = plan(model, images=images, **kw)
            except InputError as e:
                assert e.code == code, (model, e.code)
            else:
                pytest.fail("%s: %s -> %r" % (what, model, planned))


@mutation(SPEC_FILE, "        if kind is None:", "        if False:", expect="an unknown model")
def test_an_unknown_or_extended_model_name_is_refused():
    for name in ("meta-image-hd", "seedance-2.5", "meta"):
        try:
            planned = plan(name)
        except InputError as e:
            assert (e.code, e.param) == ("input.bad_model", "model"), "an unknown model: %s -> %s" % (name, e.code)
        else:
            pytest.fail("an unknown model was planned: %s -> %r" % (name, planned))


def test_the_options_never_set_the_price():
    assert plan(options={"price": 0}).price == 1
