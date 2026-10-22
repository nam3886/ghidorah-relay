"""`plan()` of the dola spec (coverage MODELS01-09, GEN02-05, WORKER16, PRICE00)."""

import logging

import pytest
from kit_a import IMG, TEMPLATES, plan, refused, request, settings

from relay_core.providers import AfterSent, Completion, CreateRequest, InputError, MediaIn
from relay_core.testing.mutation import mutation
from relay_dola import catalog as C
from relay_dola.spec import SPEC

CATALOG_FILE = "packages/provider-dola/src/relay_dola/catalog.py"
CONSTANTS_FILE = "packages/provider-dola/src/relay_dola/constants.py"
SPEC_FILE = "packages/provider-dola/src/relay_dola/spec.py"


# ---- a prompt that shows nothing ---------------------------------------------------------------------------------
@mutation(SPEC_FILE, "        if C.is_blank_prompt(req.prompt):", "        if False:",
          expect="a prompt that shows nothing was planned")
def test_a_prompt_that_shows_nothing_is_refused_at_plan_time():
    """Sent as the bare marker, it could be taken for any conversation that carries the same four digits."""
    for blank in ("", "   ", "\u200b\u200b", " \u2060\ufeff\n", "\u3164\u034f\ufe0f", "\U000e0041"):
        for model in ("seedance-2.5", "gemini-image"):
            try:
                planned = plan(model, blank)
            except InputError as e:
                assert (e.code, e.param) == ("input.empty_prompt", "prompt"), (e.code, e.param, blank)
            else:
                pytest.fail("a prompt that shows nothing was planned: %r -> %r" % (blank, planned))
    assert plan(prompt="a\u200bb").params["kind"] == "video"      # a visible character keeps it a real prompt


# ---- durations (MODELS01-03, 06) -------------------------------------------------------------------------------------
def test_every_measured_duration_of_the_default_model_is_accepted():
    for d in C.DURATIONS_BY_MODEL["seedance_v2.5"]:
        assert plan(duration=d).params["duration"] == d


def test_twenty_and_twenty_five_seconds_are_real_levels_priced_like_thirty():
    # ffprobe 20/09: 20 s came out 20.06 s and 25 s 25.06 s, 2 credits each. Red if someone drops them as labels.
    for real in (20, 25):
        assert real in C.DURATIONS_BY_MODEL["seedance_v2.5"], "%ds is proven delivered, yet blocked" % real
        assert plan(duration=real).price == 2


@mutation(CATALOG_FILE, '"seedance_v2.5": (5, 10, 20, 25, 30),', '"seedance_v2.5": (5, 10, 15, 20, 25, 30),',
          expect="DID NOT RAISE")
def test_unknown_durations_are_refused_not_rounded():
    for bad in (7, 15, 0, -1, 60):
        e = refused(duration=bad)
        assert e.code == "input.bad_duration" and e.param == "duration", bad


def test_the_durations_per_model_are_only_the_measured_levels():
    assert plan("seedance-2.0", duration=15).params["duration"] == 15
    assert plan("seedance-1.0", duration=10).params["duration"] == 10
    assert plan("seedance-2.0", duration=5).params["duration"] == 5
    for model, d in (("seedance-2.0", 30), ("seedance-1.0", 30), ("seedance-1.0", 15), ("seedance-2.5", 15)):
        assert refused(model, duration=d).code == "input.bad_duration", (model, d)


def test_a_duration_without_one_is_the_longest_of_the_model():
    assert plan("seedance-2.5").params["duration"] == 30
    assert plan("seedance-2.0").params["duration"] == 15
    assert plan("seedance-1.0").params["duration"] == 10


# ---- ratios (MODELS05) -----------------------------------------------------------------------------------------------
def test_only_the_six_video_ratios_are_accepted_and_16_9_is_the_default():
    for r in ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9"):
        assert plan(ratio=r).params["ratio"] == r
    assert plan().params["ratio"] == "16:9"
    for bad in ("2:1", "9x16", "16:10", "2:3"):
        e = refused(ratio=bad)
        assert e.code == "input.bad_ratio" and e.param == "ratio", bad


def test_the_image_ratios_have_two_to_three_and_no_twenty_one_to_nine():
    for r in ("1:1", "2:3", "3:4", "4:3", "9:16", "16:9"):
        assert plan("seedream-4.5", ratio=r).params["ratio"] == r
    assert plan("seedream-4.5").params["ratio"] is None             # None: dola picks
    assert refused("seedream-4.5", ratio="21:9").code == "input.bad_ratio"


# ---- names: alias vs canonical, unknown, native door (MODELS07, MODELS09) --------------------------------------------
def test_alias_and_canonical_names_behave_the_same():
    for alias, canon in (("seedance-2.0", "seedance_v2.0"), ("seedance-1.0", "seedance_v1.0"),
                         ("seedance-2.5", "seedance_v2.5")):
        a, c = plan(alias), plan(canon)
        assert a == c and a.params["duration"] == c.params["duration"]
    for alias, canon in (("seedance-2.0", "seedance_v2.0"), ("seedance-1.0", "seedance_v1.0")):
        # a level the model cannot deliver must be refused under BOTH spellings
        assert refused(alias, duration=30).code == refused(canon, duration=30).code == "input.bad_duration"
        assert refused(alias + "-30s", door="facade").code == "input.bad_duration"


def test_an_unknown_model_is_refused():
    for bad in ("veo3", "", "seedance-9.0", "seedance"):
        assert refused(bad).code == "input.bad_model"


@mutation(SPEC_FILE, 'if req.door == "native" and C.carries_parameters(req.model):',
          'if False and C.carries_parameters(req.model):', expect="DID NOT RAISE")
def test_the_native_door_refuses_suffix_parameters_and_says_how_to_send_them():
    for name in ("seedance-2.5-30s", "seedance-2.5-1:1", "seedance_v2.0-5s", "seedream-4.5-anime", "seedream-4.5-t3"):
        e = refused(name, door="native")
        assert e.code == "input.bad_model" and e.param == "model", name
        assert "duration" in e.detail and "options.style" in e.detail
    # the facade doors carry parameters in the name
    assert plan("seedance-2.5-10s-1:1", door="facade").params["duration"] == 10
    assert plan("seedream-4.5-anime", door="facade").params["style"] == "anime"
    # and a bare name is fine on both
    assert plan("seedance-2.5", door="native").params["duration"] == 30


def test_explicit_fields_override_what_a_name_states_and_are_judged_on_the_model():
    assert plan("seedance-2.5-10s", duration=20, door="facade").params["duration"] == 20
    assert refused("seedance-2.0-10s", duration=30, door="facade").code == "input.bad_duration"


# ---- warnings (MODELS08) ---------------------------------------------------------------------------------------------
def test_two_oh_at_fifteen_seconds_carries_a_warning_without_blocking():
    p = plan("seedance-2.0", duration=15)
    assert len(p.warnings) == 1 and p.warnings[0].code == "dola.duration_capped"
    assert "15" in p.warnings[0].text and "2.0" in p.warnings[0].text
    assert not plan("seedance-2.0", duration=10).warnings
    assert not plan("seedance-2.5").warnings                    # 2.5 at its default: no warning
    assert not plan("seedance-1.0").warnings


# ---- prices (GEN02-05, PRICE00) --------------------------------------------------------------------------------------
def test_prices_come_from_the_price_setting():
    for model, secs, price in (("seedance-2.5", 30, 2), ("seedance-2.5", 5, 2), ("seedance-2.5", 10, 4),
                               ("seedance-2.5", 20, 2), ("seedance-2.5", 25, 2), ("seedance-2.0", 5, 1),
                               ("seedance-2.0", 10, 2), ("seedance-2.0", 15, 3), ("seedance-1.0", 5, 1),
                               ("seedance-1.0", 10, 2)):
        p = plan(model, duration=secs)
        assert p.price == price and p.account_cost == price and p.wallet_kind == "video" and p.pool == "video", (
            model, secs)
    # 10 s costs twice 30 s: measured, not a typo
    assert plan(duration=10).price == 2 * plan(duration=30).price


def test_images_are_priced_per_job_from_the_same_table_by_a_key_without_a_length():
    assert plan("seedream-4.5").price == 1 and plan("seedream-5.0-pro").price == 4
    cfg = settings(dola__prices={"seedream-4.5": 1, "seedream-4.5@30s": 99})
    assert plan("seedream-4.5", cfg=cfg).price == 1, "an image looked up a key with a length"
    assert C.price_key("seedream-4.5", "image", 30) == "seedream-4.5"
    assert C.price_key("seedance_v2.5", "video", 30) == "seedance_v2.5@30s"


@mutation(CONSTANTS_FILE, "FALLBACK_VIDEO_PRICE = 3", "FALLBACK_VIDEO_PRICE = 1", expect="== 3")
def test_a_tier_missing_from_the_table_uses_the_fallback_logs_it_and_warns(caplog):
    cfg = settings(dola__prices={})
    with caplog.at_level(logging.WARNING):
        p = plan("seedance-2.5", duration=5, cfg=cfg)
    assert p.price == 3 and p.account_cost == 3, "the video fallback is 3 points"
    assert [w.code for w in p.warnings] == ["dola.price_fallback"] and "seedance_v2.5@5s" in p.warnings[0].text
    assert any("seedance_v2.5@5s" in r.getMessage() for r in caplog.records), "a fallback must be logged"
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        img = plan("seedream-4.5", cfg=cfg)
    assert img.price == 1 and [w.code for w in img.warnings] == ["dola.price_fallback"]
    assert caplog.records, "an image fallback is logged too"
    # a table that has the tier: no fallback, no warning
    assert not plan("seedance-2.5", duration=5).warnings


def test_a_price_change_in_settings_applies_to_the_next_plan_only():
    before = plan(duration=30)
    cfg = settings(dola__prices={**C.DEFAULT_PRICES, "seedance_v2.5@30s": 9})
    after = plan(duration=30, cfg=cfg)
    assert after.price == 9 and after.account_cost == 9
    assert before.price == 2, "a plan already made keeps its price (an old invoice never changes)"
    assert C.DEFAULT_PRICES["seedance_v2.5@30s"] == 2, "the default table was mutated"


def test_the_default_price_table_is_the_measured_one():
    t = C.DEFAULT_PRICES
    assert t["seedance_v2.5@30s"] == 2 and t["seedance_v2.5@10s"] == 4, "10 s is dearer than 30 s: measured"
    assert t["seedream-4.5"] == 1 and t["seedream-5.0-pro"] == 4
    assert "seedance_v1.0@15s" not in t and "seedance_v2.5@60s" not in t, "tiers dola caps are not sold"
    assert settings().get("dola.prices") == t


# ---- account cost, completion, timeouts ------------------------------------------------------------------------------
@mutation(SPEC_FILE, 'pool="video", account_cost=price,', 'pool="video", account_cost=1,', expect="account_cost")
def test_a_video_costs_the_account_its_price_and_an_image_costs_one():
    v = plan(duration=10)
    assert (v.price, v.account_cost) == (4, 4), "account_cost of a video is its price"
    for model in ("seedream-4.5", "seedream-5.0-pro"):
        i = plan(model)
        assert i.account_cost == 1, "dola counts the image ceiling per JOB, whatever the model (R20)"
    assert plan("seedream-5.0-pro").price == 4


@mutation(CONSTANTS_FILE, "IMAGE_ACCOUNT_COST = 1", "IMAGE_ACCOUNT_COST = 4", expect="account_cost == 1")
def test_an_image_with_a_dear_model_still_costs_the_account_one_job():
    assert plan("seedream-5.0-pro").account_cost == 1


@mutation(SPEC_FILE, "completion=Completion.HOLD", "completion=Completion.DETACH", expect="Completion.HOLD")
def test_completion_after_sent_and_timeouts_are_the_spike_findings():
    v, i = plan(), plan("seedream-4.5")
    assert v.completion is Completion.DETACH and i.completion is Completion.HOLD
    assert v.after_sent is AfterSent.RECONCILE and i.after_sent is AfterSent.RECONCILE
    assert (v.submit_timeout_s, i.submit_timeout_s) == (720, 300)
    assert (v.media_type, i.media_type, v.pool, i.pool, v.wallet_kind, i.wallet_kind) == (
        "video", "image", "video", "image", "video", "image")


def test_operations_follow_the_inputs():
    assert plan().operation == "text_to_video" and plan(images=(IMG,)).operation == "image_to_video"
    assert plan("seedream-4.5").operation == "text_to_image"
    assert plan("seedream-4.5", images=(IMG,)).operation == "image_to_image"
    assert all(plan(m).operation in {o for md in SPEC.catalog() if md.id == m for o in md.operations}
               for m in ("seedance-2.5", "seedream-4.5"))


def test_more_than_ten_reference_images_is_refused():
    assert plan(images=(IMG,) * 10).operation == "image_to_video"
    e = refused(images=(IMG,) * 11)
    assert e.code == "input.too_many_images"


# ---- the sentence and the marker are the frame's, not the plan's (WORKER16) ------------------------------------
def test_a_plan_holds_neither_the_hint_nor_the_marker_nor_the_composed_text():
    for model in ("seedance-2.5", "seedream-4.5"):
        p = plan(model, "a calm  sea\nat dusk")
        assert "prompt_hint" not in p.params, "the video sentence is part of the shipped frame now"
        assert "4321" not in str(p.params) and "a calm" not in str(p.params)


# ---- prompt conflicts (R103) -----------------------------------------------------------------------------------------
@mutation(SPEC_FILE, "found = find_conflict(prompt, kind, ratio, duration)", "found = None",
          expect="DID NOT RAISE")
def test_a_prompt_that_contradicts_the_parameters_is_refused_before_any_charge():
    e = refused(prompt="Create a 30-second, 16:9 news video about a theft.", ratio="21:9")
    assert e.code == "input.prompt_ratio_conflict" and "16:9" in e.detail
    e = refused(prompt="Create a 30-second, 16:9 news video about a theft.", ratio="16:9", duration=10)
    assert e.code == "input.prompt_duration_conflict" and "30" in e.detail
    assert plan(prompt="Create a 30-second, 16:9 news video about a theft.", ratio="16:9", duration=30)
    # images: ratio only, and an unset ratio contradicts nothing
    assert refused("seedream-4.5", "A 1:1 square poster of a lighthouse", ratio="16:9").code == (
        "input.prompt_ratio_conflict")
    assert plan("seedream-4.5", "A 9:16 phone wallpaper").params["ratio"] is None
    assert plan("seedream-4.5", "A 1:1 square poster, 10 seconds long", ratio="1:1")


def test_the_conflict_is_judged_on_the_ratio_and_length_that_will_run():
    # a default (no ratio sent) is 16:9: a prompt asking 9:16 conflicts with it
    assert refused(prompt="vertical 9:16 clip of a cat").code == "input.prompt_ratio_conflict"
    assert refused("seedance-2.5-5s", "a clip of 10 seconds long", door="facade").code == (
        "input.prompt_duration_conflict")


# ---- options never steer money or time --------------------------------------------------------
@mutation(SPEC_FILE, 'price, warnings = self._price(settings, gen.model, "video", duration)',
          'price, warnings = req.options.get("price", 2), []', expect="999")
def test_plan_ignores_price_account_cost_and_timeout_in_options():
    # FakeSpec takes these from options; the native door passes options as the client sent them.
    steer = {"price": 999, "account_cost": 50, "submit_timeout_s": 1, "completion": "hold", "pool": "image",
             "wallet_kind": "image", "after_sent": "fail_refund", "marker": "0001"}
    for model in ("seedance-2.5", "seedream-4.5"):
        plain, steered = plan(model), plan(model, options=steer)
        assert steered == plain, "%s: options changed the plan: %s" % (model, steered)
    assert plan(options=steer).price == 2 and plan(options=steer).submit_timeout_s == 720
    assert plan("seedream-4.5", options=steer).account_cost == 1


def test_options_that_are_not_style_are_ignored_on_video():
    assert plan(options={"style": "anime"}) == plan()


@mutation(SPEC_FILE, '        if req.options.get("template") is not None or req.options.get',
          '        if False or req.options.get', expect="a template option was accepted")
def test_template_options_are_refused_never_ignored_and_never_charged():
    """Templates are a UI concept: the API takes no template. A request still sending one gets a 400 naming the
    change, for images and video alike."""
    for opts in ({"template": 3}, {"template_category": 0}, {"style": "anime", "template": 0, "template_category": 1}):
        for model in ("seedream-4.5", "seedance-2.5"):
            try:
                planned = plan(model, options=opts)
            except InputError as e:
                assert (e.code, e.param) == ("input.invalid", "options"), (e.code, e.param, opts)
                assert "template" in e.detail and "prompt" in e.detail
            else:
                pytest.fail("a template option was accepted: %r on %s -> %r" % (opts, model, planned))
    assert plan("seedream-4.5", options={"template": None, "style": "anime"}).params["style"] == "anime"


# ---- images: style, options ------------------------------------------------------------------------------------------
def test_the_style_rides_in_options_on_the_native_door():
    assert plan("seedream-4.5", options={"style": "anime"}).params["style"] == "anime"
    e = refused("seedream-4.5", options={"style": "vaporwave"})
    assert e.code == "input.bad_style" and e.param == "style"


def test_an_image_is_judged_for_conflicts_with_the_customers_words_only():
    e = refused("seedream-4.5", "a 9:16 phone wallpaper of a lake", ratio="1:1")
    assert e.code == "input.prompt_ratio_conflict"


# ---- a stored request replans the same way ---------------------------------------------------------------------
def rebuilt(sent: dict) -> CreateRequest:
    """What the core stores in jobs.request, turned back into a request, as Regenerate does."""
    return CreateRequest(
        model=sent["model"], prompt=sent["prompt"], duration=sent["duration"], ratio=sent["ratio"], n=sent["n"],
        images=tuple(MediaIn(i["sha256"], i["mime"], i["size"]) for i in sent["images"]), options=dict(sent["options"]),
        title=sent["title"], callback_url=sent["callback_url"], door="native")


@pytest.mark.parametrize("sent", [
    {"model": "seedance-2.5", "prompt": "a calm sea", "duration": 10, "ratio": "9:16", "n": None, "options": {},
     "title": "t", "callback_url": None, "images": [{"sha256": "0" * 64, "mime": "image/png", "size": 10}]},
    {"model": "seedance-2.0", "prompt": "a calm sea", "duration": None, "ratio": None, "n": None, "options": {},
     "title": None, "callback_url": None, "images": []},
    {"model": "seedream-4.5", "prompt": "a lighthouse", "duration": None, "ratio": "1:1", "n": None,
     "options": {"style": "anime"}, "title": None, "callback_url": None, "images": []},
])
def test_a_request_rebuilt_from_a_stored_one_gives_the_same_plan(sent):
    first = SPEC.plan(request(**{k: v for k, v in sent.items() if k in ("model", "prompt")},
                              duration=sent["duration"], ratio=sent["ratio"],
                              images=tuple(MediaIn(i["sha256"], i["mime"], i["size"]) for i in sent["images"]),
                              options=dict(sent["options"])), settings(), {"templates": TEMPLATES})
    again = SPEC.plan(rebuilt(sent), settings(), {"templates": TEMPLATES})
    assert again == first
    assert again == SPEC.plan(rebuilt(sent), settings(), {"templates": TEMPLATES}), "a plan is deterministic"
