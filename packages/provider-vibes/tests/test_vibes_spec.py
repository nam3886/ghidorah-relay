"""The vibes spec: caps, settings, catalog next to dola's and meta's, error rules and `plan()`."""

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
    SourceIn,
    _problems,
    check_catalog,
)
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA
from relay_meta.spec import SPEC as META
from relay_vibes import errors as vibes_errors
from relay_vibes.spec import SPEC

SPEC_FILE = "packages/provider-vibes/src/relay_vibes/spec.py"
IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)


def settings(**overrides) -> FakeSettings:
    """vibes' own defaults; `vibes__prices=...` overrides `vibes.prices`."""
    return FakeSettings(SPEC, **overrides)


def plan(model="vibes-image", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), cfg or settings(), {})


def priced(model="vibes-image", prompt="a lighthouse at dawn", *, cfg=None, **kw):
    """The plan as the door makes it: the core's price and upstream cost from `vibes.prices` and `vibes.costs`."""
    cfg = cfg or FakeSettings(SPEC)
    return pricing.price_plan(SPEC, plan(model, prompt, cfg=cfg, **kw), cfg)


def refusal(model="vibes-image", prompt="a lighthouse at dawn", **kw) -> tuple[str, str | None] | None:
    """(code, param) of the refusal, or None when the request was planned."""
    try:
        plan(model, prompt, **kw)
    except InputError as e:
        return e.code, e.param
    return None


# ---- caps, settings, catalog -----------------------------------------------------------------------------------------
def test_the_caps_say_an_http_provider_with_a_video_and_an_image_pool():
    c = SPEC.caps
    assert SPEC.name == "vibes" and c.execution == "http"
    assert [(p.name, p.unit) for p in c.pools] == [("video", "jobs"), ("image", "jobs")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold) == (1, 3, 3)
    assert c.strike_effect == Dead("signed_out")
    # a video is polled after the send, and a send whose answer was lost is reconciled from its batch
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (True, True, False, False)
    assert SPEC.extras({}) == {}


@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="vibes must stay drained")
def test_vibes_stays_drained_by_default_and_declares_the_settings_the_core_reads():
    s = SPEC.settings()
    assert s["vibes.claims_enabled"].default is False, "vibes must stay drained until it can submit"
    assert "vibes.browser_slots" not in s, "an http provider has no browser"
    every = {"vibes-image": 1, "vibes-video@480p": 1, "vibes-video@720p": 1, "vibes-video-extend": 1,
             "vibes-video-edit": 1, "vibes-image-edit": 1}
    assert s["vibes.prices"].default == every and s["vibes.costs"].default == every
    # not measured yet: far above any real day
    assert s["vibes.pools"].default == {"video": day_pool({"free": 1000}), "image": day_pool({"free": 1000})}
    assert s["vibes.render_deadline_s"].default >= 600, "a video renders for about 5 min"
    assert (s["vibes.poll_interval_s"].default, s["vibes.poll_interval_s"].min) == (20, 10), "slower than 2-6 s"
    assert s["vibes.checkpoint_rest_s"].default == 600 and s["vibes.checkpoint_rest_s"].min == 60
    assert s["vibes.checkpoint_rest_max_s"].default == 7200, "a repeated checkpoint rests at most 2 h"
    gap = s["vibes.send_gap_s"]
    assert (gap.type, gap.default, gap.min) == (int, 300, 0), "a vibes account takes a job at most every 5 min"
    assert all(s[k].description for k in s)
    assert _problems(SPEC) == []


def test_the_prices_setting_refuses_a_bad_table():
    rule = SPEC.settings()["vibes.prices"].rule
    for bad in ({"vibes-image": 1.5}, {"vibes-image": -1}, {" ": 1}, {"vibes-image": True}):
        with pytest.raises(ValueError):
            rule.check(bad)
    rule.check({"vibes-image": 0, "vibes-video@720p": 3})


def test_the_registry_takes_vibes_beside_dola_and_meta_and_no_model_name_clashes():
    reg = ProviderRegistry()
    for spec in (DOLA, META, SPEC):
        reg.register(spec)
    assert reg.names() == ["dola", "meta", "vibes"]
    names = check_catalog([DOLA, META, SPEC])
    assert {n for n, (p, _m) in names.items() if p == "vibes"} == {
        "vibes-image", "vibes-video", "vibes-video-extend", "vibes-video-edit", "vibes-image-edit"}
    image, video = reg.resolve("vibes-image"), reg.resolve("vibes-video")
    assert image is not None and image[0] is SPEC and image[1].operations == ("text_to_image",)
    assert video is not None and video[0] is SPEC and video[1].operations == ("text_to_video", "image_to_video")


# ---- error rules -----------------------------------------------------------------------------------------------------
def test_vibes_registers_its_codes_on_load_and_a_warning_is_no_rule():
    codes = {"vibes.http_error", "vibes.generate_refused", "vibes.render_failed", "vibes.content_refused",
             "vibes.output_missing", "vibes.source_missing"}
    assert set(SPEC.codes()) == codes and codes <= set(errors.RULES), "the codes are registered when the package loads"
    assert {c for c in errors.RULES if c.startswith("vibes.")} == codes and errors.violations(errors.RULES) == []
    assert not hasattr(vibes_errors, "WARN_PRICE_FALLBACK"), "no price is a guess any more: there is no fallback"
    for code in ("vibes.generate_refused", "vibes.render_failed"):   # the job ends now, refunded; never sent again
        r = errors.rule(code)
        assert (r.terminal, r.refund, r.counts_attempt, errors.hidden(r)) == (True, True, False, False), code
    r = errors.rule("vibes.http_error")                               # before the send: an attempt, refunded at the end
    assert (r.terminal, r.refund, r.counts_attempt, r.public_code) == (False, True, True,
                                                                       errors.TEMPORARILY_UNAVAILABLE)


# ---- plan ------------------------------------------------------------------------------------------------------------
HOLD_OR_DETACH = "            completion=Completion.HOLD if kind == \"image\" else Completion.DETACH,"


@mutation(SPEC_FILE, HOLD_OR_DETACH, "            completion=Completion.HOLD,", expect="a video is polled")
def test_an_image_is_held_in_the_request_and_a_video_is_detached_and_polled():
    p = plan(ratio="4:3")
    assert (p.operation, p.media_type, p.model, dict(p.params)) == (
        "text_to_image", "image", "vibes-image", {"kind": "image", "model": "midjen-base", "ratio": "4:3"})
    assert (p.completion, p.after_sent, p.pool) == (Completion.HOLD, AfterSent.RECONCILE, "image")
    assert (p.price_key, p.submit_timeout_s, p.warnings) == ("vibes-image", 180, ())
    assert (priced().price, priced().account_cost) == (1, 1)
    v = plan("vibes-video", "a paper lantern over a lake")
    assert (v.operation, v.media_type, v.pool, v.submit_timeout_s) == ("text_to_video", "video", "video", 120)
    assert v.completion == Completion.DETACH, "a video is polled after the session"
    assert dict(v.params) == {"kind": "video", "model": "midjen-short", "ratio": None, "resolution": "480p"}


@mutation(SPEC_FILE, "            if len(req.images) > 1:", "            if len(req.images) > 2:",
          expect="video with two images")
def test_a_video_from_one_image_is_image_to_video_and_two_images_are_refused():
    p = plan("vibes-video", "slow pan to the left", images=(IMG,), ratio="9:16")
    assert (p.operation, p.params["ratio"]) == ("image_to_video", "9:16")
    assert refusal("vibes-video", images=(IMG, IMG)) == ("input.too_many_images", "images"), "video with two images"


BLANK_RULE = '        if operation.startswith("text_to_") and C.is_blank_prompt(req.prompt):'


@mutation(SPEC_FILE, BLANK_RULE, "        if False:", expect="a text request that shows nothing was planned")
@mutation(SPEC_FILE, BLANK_RULE, "        if C.is_blank_prompt(req.prompt):", expect="an image alone is enough")
def test_a_text_request_needs_a_visible_prompt_and_an_image_to_video_does_not():
    for model in ("vibes-image", "vibes-video"):
        for blank in ("", "   ", "​​", " ⁠﻿\n"):
            got = refusal(model, blank)
            assert got == ("input.empty_prompt", "prompt"), "a text request that shows nothing was planned: %s %r" % (
                model, got)
    assert refusal("vibes-video", "", images=(IMG,)) is None, "an image alone is enough"
    assert plan("vibes-video", "", images=(IMG,)).operation == "image_to_video"


@mutation(SPEC_FILE, "            if req.images:\n", "            if False:\n", expect="image with an input image")
def test_an_image_request_with_an_input_image_is_refused():
    assert refusal(images=(IMG,)) == ("input.too_many_images", "images"), "planned an image with an input image"


RATIO_RULE = "        if req.ratio is not None and req.ratio not in C.RATIOS[kind]:"
CATALOG_FILE = "packages/provider-vibes/src/relay_vibes/catalog.py"
VIDEO_RATIOS = '"video": ("9:16",)}'


@mutation(SPEC_FILE, RATIO_RULE, "        if False:", expect="a ratio the model does not take")
@mutation(CATALOG_FILE, VIDEO_RATIOS, '"video": ("16:9", "9:16", "1:1")}', expect="a ratio the model does not take")
@mutation(SPEC_FILE, RATIO_RULE, '        if req.ratio is not None and req.ratio not in C.RATIOS["image"]:',
          expect="a ratio the model does not take")
def test_each_kind_takes_only_its_own_ratios_as_a_field():
    for ratio in ("1:1", "16:9", "9:16", "4:3", "3:4"):
        assert plan(ratio=ratio).params["ratio"] == ratio
    # a video comes out 9:16 whatever is asked (measured S5, S6): no other ratio is promised, for t2v or i2v
    assert plan("vibes-video", ratio="9:16").params["ratio"] == "9:16"
    assert plan("vibes-video", "pan", images=(IMG,), ratio="9:16").params["ratio"] == "9:16"
    for ratio in ("16:9", "1:1", "4:3"):
        for images in ((), (IMG,)):
            try:
                plan("vibes-video", "pan", images=images, ratio=ratio)
            except InputError as e:
                assert (e.code, e.param, e.detail) == (
                    "input.bad_ratio", "ratio", "vibes video comes out 9:16; send no ratio or 9:16"), (ratio, e)
            else:
                raise AssertionError("a ratio the model does not take: vibes-video %s %s" % (ratio, images))
    for model, ratio in (("vibes-video", "3:4"), ("vibes-image", "21:9"),
                         ("vibes-image", "16x9")):
        assert refusal(model, ratio=ratio) == ("input.bad_ratio", "ratio"), "a ratio the model does not take: %s %s" % (
            model, ratio)
    assert plan(prompt="a cat --ar 16:9").params["ratio"] is None, "a prompt flag is text, never the ratio"


@mutation(SPEC_FILE, "            if resolution not in C.RESOLUTIONS:", "            if False:",
          expect="a resolution vibes does not make")
@mutation(SPEC_FILE, "            if resolution is not None:", "            if False:", expect="an image resolution")
def test_a_video_takes_480p_or_720p_and_an_image_takes_no_resolution():
    assert plan("vibes-video", options={"resolution": "720p"}).params["resolution"] == "720p"
    for bad in ("1080p", "720", 720):
        assert refusal("vibes-video", options={"resolution": bad}) == ("input.invalid", "options"), (
            "a resolution vibes does not make: %r" % (bad,))
    assert refusal(options={"resolution": "720p"}) == ("input.invalid", "options"), "an image resolution was ignored"


@mutation(SPEC_FILE, "        if req.duration is not None:", "        if False:", expect="a duration was ignored")
def test_a_duration_is_refused_rather_than_ignored():
    for model in ("vibes-image", "vibes-video"):
        assert refusal(model, duration=5) == ("input.bad_duration", "duration"), "a duration was ignored: %s" % model


@mutation(SPEC_FILE, "        if kind is None:", "        if False:", expect="KeyError: 'vibes-video-hd'")
# without the check an unknown model fails on the upstream name lookup, a crash instead of a refusal
def test_an_unknown_or_foreign_model_name_is_refused():
    for name in ("vibes-video-hd", "midjen-short", "meta-video", "vibes"):
        assert refusal(name) == ("input.bad_model", "model"), "an unknown model: %s" % name


VIDEO_KEY = "price_key=C.price_key(req.model, resolution)"


@mutation(SPEC_FILE, 'renamed={"vibes-video": ("vibes-video@480p", "vibes-video@720p")}', "renamed={}",
          expect="the stored video price")
def test_a_price_table_stored_by_the_earlier_build_prices_both_resolutions():
    """The earlier build keyed the video by its model alone: the stored price moves to both its resolutions."""
    loaded = SPEC.settings()["vibes.prices"].on_load({"vibes-video": 3})
    assert loaded == {"vibes-image": 1, "vibes-video@480p": 3, "vibes-video@720p": 3, "vibes-video-extend": 1,
                      "vibes-video-edit": 1, "vibes-image-edit": 1}, (
        "the stored video price was lost: %s" % loaded)


@mutation(SPEC_FILE, VIDEO_KEY, "price_key=C.price_key(req.model, None)",
          expect="no price for vibes-video@None")
def test_the_price_comes_from_the_live_settings_by_resolution_never_the_options():
    cfg = settings(vibes__prices={"vibes-image": 3, "vibes-video@480p": 5, "vibes-video@720p": 8})
    assert priced(cfg=cfg).price == 3 and priced("vibes-video", cfg=cfg).price == 5
    assert priced("vibes-video", options={"resolution": "720p"}, cfg=cfg).price == 8, (
        "a video is priced by its resolution")
    assert priced(options={"price": 0}).price == 1


def test_a_model_missing_from_the_prices_is_refused_never_charged_a_guess():
    with pytest.raises(pricing.Unpriced):
        priced(cfg=settings(vibes__prices={"vibes-video@480p": 2}))


@mutation(SPEC_FILE, "        if req.n not in (None, 1):", "        if False:", expect="n=4 was planned")
def test_a_count_above_one_is_refused_not_ignored():
    for n in (4, 2):
        assert refusal(n=n) == ("input.invalid", "n"), "n=%d was planned" % n
    assert refusal(n=1) is None


# ---- the modes that work on an earlier vibes job (pinned jobs) -------------------------------------------------------
VIDEO_SRC = SourceIn("11111111-1111-1111-1111-111111111111", "vibes", "vibes-video", "video", "text_to_video")
IMAGE_SRC = SourceIn("22222222-2222-2222-2222-222222222222", "vibes", "vibes-image", "image", "text_to_image")
SOURCE_RULE = "        if req.source.media_type != wanted:"
FAIL_REFUND = "            after_sent=AfterSent.FAIL_REFUND if image_edit else AfterSent.RECONCILE,"


@mutation(SPEC_FILE, "            price_key=C.price_key(req.model), pool=kind, submit_timeout_s=_SUBMIT_TIMEOUT[kind], "
          "uses_source=True)", "            price_key=C.price_key(req.model), pool=kind, "
          "submit_timeout_s=_SUBMIT_TIMEOUT[kind])", expect="runs on the account that made the source")
@mutation(SPEC_FILE, FAIL_REFUND, "            after_sent=AfterSent.RECONCILE,", expect="nothing to read back")
def test_extend_video_edit_and_image_edit_run_on_the_sources_account_and_an_image_edit_cannot_be_reconciled():
    want = {"vibes-video-extend": ("extend_video", "video", "midjen-extend", Completion.DETACH, AfterSent.RECONCILE),
            "vibes-video-edit": ("video_to_video", "video", "midjen-video-edit", Completion.DETACH,
                                 AfterSent.RECONCILE),
            "vibes-image-edit": ("edit_image", "image", "midjen-base", Completion.HOLD, AfterSent.FAIL_REFUND)}
    for model, (operation, kind, upstream, completion, after) in want.items():
        p = plan(model, "make it snow", source=VIDEO_SRC if kind == "video" else IMAGE_SRC)
        assert p.uses_source, "%s runs on the account that made the source" % model
        assert (p.operation, p.media_type, p.pool, dict(p.params)) == (
            operation, kind, kind, {"kind": kind, "model": upstream, "ratio": None}), model
        assert p.completion == completion and p.price_key == model
        assert p.after_sent == after, "%s: an image edit has nothing to read back after an unclear answer" % model
        assert (priced(model, "make it snow", source=VIDEO_SRC if kind == "video" else IMAGE_SRC).price,) == (1,)
    assert not plan("vibes-video").uses_source and not plan("vibes-image").uses_source


@mutation(SPEC_FILE, SOURCE_RULE, "        if False:", expect="the wrong kind of source")
@mutation(SPEC_FILE, "        if req.source is None:", "        if False:", expect="no source")
def test_a_source_mode_needs_a_source_of_its_own_kind_and_takes_no_ratio_image_or_resolution():
    assert refusal("vibes-video-extend", "go on") == ("input.bad_source", "source_job_id"), "no source"
    assert refusal("vibes-video-edit", "sketch", source=IMAGE_SRC) == ("input.bad_source", "source_job_id"), (
        "the wrong kind of source was planned")
    assert refusal("vibes-image-edit", "snow", source=VIDEO_SRC) == ("input.bad_source", "source_job_id"), (
        "the wrong kind of source was planned")
    assert refusal("vibes-video-extend", "go on", source=VIDEO_SRC, ratio="9:16") == ("input.bad_ratio", "ratio")
    assert refusal("vibes-video-edit", "sketch", source=VIDEO_SRC, images=(IMG,)) == (
        "input.too_many_images", "images")
    assert refusal("vibes-video-extend", "go on", source=VIDEO_SRC, options={"resolution": "720p"}) == (
        "input.invalid", "options")
    assert refusal("vibes-image-edit", "​", source=IMAGE_SRC) == ("input.empty_prompt", "prompt")


def test_the_catalog_says_which_kind_of_source_each_mode_works_on():
    limits = {m.id: m.limits.get("source") for m in SPEC.catalog()}
    assert limits == {"vibes-image": None, "vibes-video": None, "vibes-video-extend": "video",
                      "vibes-video-edit": "video", "vibes-image-edit": "image"}


# ---- prompt frames ---------------------------------------------------------------------------------------------------
MULTI = "  a lantern\n\tfloats over a lake,\r\n  été — 日本語  \n"


@mutation(SPEC_FILE, "max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)",
          "max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX)", expect="not byte for byte")
@mutation(SPEC_FILE, "    frames = FRAMES\n", "    frames = None\n", expect="vibes declares no frames")
def test_the_shipped_frames_send_the_customers_words_byte_for_byte_as_before_frames():
    """Before frames vibes got the customer's words as the door took them (trimmed, line breaks kept, no tracking
    code). The goldens are literals."""
    assert SPEC.frames is not None, "vibes declares no frames"
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
        assert len(longest) <= SPEC.frames.max_sent_chars, "the longest prompt vibes takes is refused for length"
