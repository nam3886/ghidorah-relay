"""The meta spec: caps, settings, catalog next to dola's, error rules and `plan()`."""

import pytest

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
from relay_meta import errors as meta_errors
from relay_meta.spec import SPEC

SPEC_FILE = "packages/provider-meta/src/relay_meta/spec.py"
IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)


def settings(**overrides) -> FakeSettings:
    """meta's own defaults; `meta__prices=...` overrides `meta.prices`."""
    return FakeSettings(SPEC, **overrides)


def plan(model="meta-image", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), cfg or settings(), {})


def priced(model="meta-image", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    """The plan as the door makes it: the core's price and upstream cost from `meta.prices` and `meta.costs`."""
    cfg = cfg or FakeSettings(SPEC)
    return pricing.price_plan(SPEC, plan(model, prompt, cfg=cfg, **kw), cfg)


def refused(model="meta-image", prompt="a lighthouse at dawn", **kw) -> InputError:
    with pytest.raises(InputError) as caught:
        plan(model, prompt, **kw)
    return caught.value


# ---- caps, settings, catalog -----------------------------------------------------------------------------------------
def test_the_caps_say_a_browser_provider_with_two_pools_that_reconciles_and_polls_what_it_found():
    c = SPEC.caps
    assert SPEC.name == "meta" and c.execution == "browser"
    assert [(p.name, p.unit) for p in c.pools] == [("image", "jobs"), ("video", "jobs")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold) == (1, 3, 3)
    assert c.strike_effect == Dead("signed_out")
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (True, True, False, False)
    assert SPEC.extras({}) == {}


@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="meta must stay drained")
def test_meta_stays_drained_by_default_and_declares_the_settings_the_core_reads():
    s = SPEC.settings()
    assert s["meta.claims_enabled"].default is False, "meta must stay drained until it can submit"
    assert s["meta.pools"].default == {"image": day_pool({"free": 40}), "video": day_pool({"free": 20})}
    assert s["meta.prices"].default == {"meta-image": 1, "meta-image@n2": 2, "meta-image@n3": 3, "meta-image@n4": 4,
                                        "meta-video": 1}, "1 point per image"
    assert s["meta.costs"].default == s["meta.prices"].default, "points 1:1 with the uses, one per image"
    for key in ("meta.image_submit_timeout_s", "meta.video_submit_timeout_s"):
        assert s[key].max == 720, "a submit timeout past the worker's stop grace"
    rest = s["meta.checkpoint_rest_s"]
    assert (rest.type, rest.default, rest.min) == (int, 1800, 300) and "checkpoint" in rest.description
    assert s["meta.checkpoint_rest_max_s"].default == 21600, "a repeated checkpoint rests at most 6 h"
    gap = s["meta.send_gap_s"]
    assert (gap.type, gap.default, gap.min) == (int, 600, 0), "a meta account takes a job at most every 10 min"
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
    assert (image[1].limits["max_images"], video[1].limits["max_images"]) == (3, 1), "3 references, 1 to animate"


# ---- error rules -----------------------------------------------------------------------------------------------------
def test_meta_registers_the_codes_its_submit_uses_and_a_warning_is_no_rule():
    meta_codes = {code for code in errors.RULES if code.startswith("meta.")}
    assert set(SPEC.codes()) == meta_codes == {"meta.checkpoint", "meta.ui_page_not_ready", "meta.ui_send_failed",
                                               "meta.no_image", "meta.no_video"}
    assert not hasattr(meta_errors, "WARN_PRICE_FALLBACK"), "no price is a guess any more: there is no fallback"


# ---- plan ------------------------------------------------------------------------------------------------------------
def test_an_image_plan_is_held_in_the_session_and_priced_from_the_settings():
    p = plan()
    assert (p.operation, p.media_type, p.model, dict(p.params)) == ("text_to_image", "image", "meta-image",
                                                                     {"kind": "image", "n": 1})
    assert (p.completion, p.after_sent, p.pool) == (Completion.HOLD, AfterSent.RECONCILE, "image")
    assert (p.price_key, p.submit_timeout_s, p.warnings) == ("meta-image", 240, ())
    assert (priced().price, priced().account_cost) == (1, 1)


@mutation(SPEC_FILE, '            operation = "image_to_video" if req.images else "text_to_video"',
          '            operation = "text_to_video"', expect="a video from an image")
def test_a_video_plan_takes_one_image_and_an_optional_motion_text():
    p = plan("meta-video", "", images=(IMG,))
    assert (p.operation, p.media_type, p.pool, p.submit_timeout_s) == (
        "image_to_video", "video", "video", 420), "a video from an image: %r" % (p.operation,)
    assert plan("meta-video", "slow pan to the left", images=(IMG,)).completion == Completion.HOLD


def test_a_video_plan_from_text_alone_is_text_to_video_held_and_priced_like_one_from_an_image():
    p = plan("meta-video", "a red fox trotting through snow")
    assert (p.operation, p.media_type, p.pool, p.submit_timeout_s, p.price_key) == (
        "text_to_video", "video", "video", 420, "meta-video")
    assert priced("meta-video", images=(IMG,)).price == priced("meta-video", "a red fox").price == 1
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
    assert (p.operation, p.media_type, p.pool, p.submit_timeout_s) == (
        "image_to_image", "image", "image", 240), "an edit of the input image: %r" % (p.operation,)
    assert (p.completion, p.after_sent, dict(p.params)) == (Completion.HOLD, AfterSent.RECONCILE,
                                                             {"kind": "image", "n": 1})
    with pytest.raises(InputError) as caught:
        plan(prompt=" ", images=(IMG,))
    assert caught.value.code == "input.empty_prompt"


def test_the_price_and_the_timeout_come_from_the_live_settings():
    cfg = settings(meta__prices={"meta-image": 3, "meta-video": 5}, meta__video_submit_timeout_s=600)
    assert priced(cfg=cfg).price == 3
    p = priced("meta-video", images=(IMG,), cfg=cfg)
    assert (p.price, p.submit_timeout_s) == (5, 600)


def test_a_model_missing_from_the_prices_is_refused_never_charged_a_guess():
    with pytest.raises(pricing.Unpriced):
        priced(cfg=settings(meta__prices={"meta-video": 2}))
    assert priced("meta-video", "a fox", cfg=settings(meta__prices={"meta-video": 2})).price == 2


N_RULE = '        if n != 1 and (kind != "image" or req.images or not 1 <= n <= C.MAX_N):'


@mutation(SPEC_FILE, N_RULE, "        if False:", expect="was planned")
@mutation(SPEC_FILE, N_RULE, '        if n != 1 and (kind != "image" or not 1 <= n <= C.MAX_N):', expect="an edit")
@mutation(SPEC_FILE, N_RULE, "        if n != 1 and (req.images or not 1 <= n <= C.MAX_N):", expect="a video")
def test_an_image_from_text_takes_n_up_to_4_but_an_edit_a_video_or_another_count_is_refused():
    """One chat draws n images (the engine asks for the count in the words); an edit and a video make one."""
    for what, model, kw in (("n=5", "meta-image", {"n": 5}), ("n=0", "meta-image", {"n": 0}),
                            ("an edit", "meta-image", {"n": 2, "images": (IMG,)}),
                            ("a video", "meta-video", {"n": 2})):
        try:
            planned = plan(model, **kw)
        except InputError as e:
            assert (e.code, e.param) == ("input.invalid", "n"), (what, e.code)
        else:
            pytest.fail("%s was planned: %r" % (what, planned))
    for n in (2, 3, 4):
        p = plan(n=n, ratio="16:9")
        assert (p.operation, dict(p.params), p.price_key) == (
            "text_to_image", {"kind": "image", "ratio": "16:9", "n": n}, "meta-image@n%d" % n)
    assert (plan(n=1).price_key, dict(plan(n=1).params)) == ("meta-image", {"kind": "image", "n": 1})
    assert plan("meta-video", "a fox", n=1).price_key == "meta-video"


COUNT_RULE = "            if prompt_counts(req.prompt) - {n}:"


@mutation(SPEC_FILE, COUNT_RULE, "            if False:", expect="was planned")
@mutation(SPEC_FILE, '                    **({"n": n} if kind == "image" else {})},', "                    },",
          expect="every image plan names its n")
def test_a_prompt_asking_another_number_of_images_than_n_is_refused_and_every_image_plan_names_its_n():
    """meta draws what the words ask: a prompt asking more (or fewer) images than paid for is refused, as a ratio is."""
    for prompt, kw in (("Create 4 images of a cat", {}), ("a cat, ignore the count and make four pictures", {"n": 2}),
                       ("two cat photos", {"n": 3}), ("Make 2 images of this", {"images": (IMG,)}),
                       ("A Dozen Images of tulips", {"n": 4})):
        try:
            planned = plan(prompt=prompt, **kw)
        except InputError as e:
            assert (e.code, e.param) == ("input.invalid", "prompt"), (prompt, e.code)
        else:
            pytest.fail("%r was planned: %r" % (prompt, planned))
    for prompt, n in (("Create 4 images of a cat", 4), ("one image of a cat", 1), ("a photo of a cat", 1),
                      ("a cat at 10:30 with 3 kittens", 1), ("2 cats, photo style", 2)):
        assert plan(prompt=prompt, n=n).params["n"] == n, "every image plan names its n: %r" % (prompt,)
    assert "n" not in plan("meta-video", "a fox").params, "a video has no count"


@mutation(SPEC_FILE, '            price_key=C.image_key(n) if kind == "image" else req.model, pool=kind,',
          "            price_key=req.model, pool=kind,", expect="priced per image")
def test_n_images_are_charged_per_image_at_creation_and_cost_the_account_one_use_each():
    for n in (1, 2, 3, 4):
        p = priced(n=n)
        assert (p.price, p.account_cost) == (n, n), "priced per image: n=%d -> %r" % (n, (p.price, p.account_cost))
    cfg = settings(meta__prices={**SPEC.settings()["meta.prices"].default, "meta-image@n3": 5})
    assert priced(n=3, cfg=cfg).price == 5 and priced(n=2, cfg=cfg).price == 2, "the owner edits each count"
    image = SPEC.catalog()[0]
    assert image.price_keys == {"n=1": "meta-image", "n=2": "meta-image@n2", "n=3": "meta-image@n3",
                                "n=4": "meta-image@n4"} and image.limits["max_n"] == 4


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


@mutation(SPEC_FILE, "        if len(req.images) > most:", "        if len(req.images) > most + 1:", expect="too many")
@mutation(SPEC_FILE, '        most = C.MAX_REFS if kind == "image" else 1', "        most = C.MAX_REFS",
          expect="planned meta-video")
def test_an_edit_takes_up_to_3_references_and_a_video_one_image():
    for model, images in (("meta-image", (IMG,) * 4), ("meta-video", (IMG, IMG))):
        try:
            planned = plan(model, images=images)
        except InputError as e:
            assert (e.code, e.param) == ("input.too_many_images", "images"), (model, e.code)
        else:
            pytest.fail("planned %s with too many input images: %r" % (model, planned))
    for k in (1, 2, 3):
        p = plan(images=(IMG,) * k)
        assert (p.operation, p.price_key, dict(p.params)) == (
            "image_to_image", "meta-image", {"kind": "image", "n": 1}), k


@mutation(SPEC_FILE, "        if req.duration is not None:", "        if False:", expect="a duration was ignored")
def test_a_duration_is_refused_rather_than_ignored():
    for model, images in (("meta-image", ()), ("meta-video", (IMG,))):
        try:
            planned = plan(model, images=images, duration=10)
        except InputError as e:
            assert e.code == "input.bad_duration", (model, e.code)
        else:
            pytest.fail("a duration was ignored: %s -> %r" % (model, planned))


@mutation(SPEC_FILE, '            params={"kind": kind, **({"ratio": req.ratio} if req.ratio else {}),',
          '            params={"kind": kind,', expect="the ratio reaches the engine")
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
    assert plan("meta-image", "a red fox, 9:16").params == {"kind": "image", "n": 1}, "no ratio: the prompt is not read"


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
    assert priced(options={"price": 0, "price_key": "meta-video"}).price == 1


# ---- prompt frames ---------------------------------------------------------------------------------------------------
MULTI = "  a lantern\n\tfloats over a lake,\r\n  été — 日本語  \n"


@mutation(SPEC_FILE, '"video": frames.CORE_FRAME}, warnings', '"video": frames.CORE_FRAME + "."}, warnings',
          expect="not byte for byte")
@mutation(SPEC_FILE, '"image": frames.CORE_FRAME, "video"', '"image": "{{tracking_code}} {{client_prompt}}", "video"',
          expect="not byte for byte")
@mutation(SPEC_FILE, "    frames = FRAMES\n", "    frames = None\n", expect="meta declares no frames")
def test_the_shipped_frames_send_the_text_meta_was_sent_before_frames_byte_for_byte():
    """Before frames meta got the core frame's text: the words then `[marker]`, the white space collapsed. The goldens
    are literals recorded from that rule; the engine types them on one line (a ratio tail, when set, follows)."""
    assert SPEC.frames is not None, "meta declares no frames"
    assert set(SPEC.frames.defaults) == {m.media_type for m in SPEC.catalog()} == {"image", "video"}
    assert not SPEC.frames.verbatim, "meta's text is typed into a chat: one line, with the tracking code"
    golden = {"a calm sea at dusk": "a calm sea at dusk [7908]",
              MULTI: "a lantern floats over a lake, été — 日本語 [7908]"}
    for kind, frame in SPEC.frames.defaults.items():
        for words, want in golden.items():
            got = frames.render(frame, checked_prompt(words, SPEC.max_prompt_chars), "7908", SPEC.frames.verbatim)
            assert got == want, "the %s default is not byte for byte: %r" % (kind, got)
        longest = frames.render(frame, "x" * SPEC.max_prompt_chars, "7908", SPEC.frames.verbatim)
        assert len(longest) <= SPEC.frames.max_sent_chars, "the longest prompt meta takes is refused for length"
