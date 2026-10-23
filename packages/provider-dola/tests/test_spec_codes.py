"""The dola spec's caps, its error codes and the deadline cause (phase mutation 7)."""

import importlib

from kit_a import refused

from relay_core import errors
from relay_core.providers import Dead, check_catalog
from relay_core.testing.mutation import mutation
from relay_dola import errors as dola_errors
from relay_dola.spec import SPEC

SPEC_FILE = "packages/provider-dola/src/relay_dola/spec.py"

LIKENESS = ("For likeness protection, Dreamina Seedance 2.5 only supports generating videos featuring yourself. "
            "Use another image, or create a video from text.")
SAFETY = "Take care. [Resources](https://www.dola.com/help_resources/en)"
UPLOAD = "To proceed, please upload the 3 reference images of the 3 people and the stage reference image."


def test_the_caps_are_the_contract_values():
    c = SPEC.caps
    assert SPEC.name == "dola" and c.execution == "browser" and c.wallet_kinds == ("video", "image")
    assert [(p.name, p.tz, p.budget_setting) for p in c.pools] == [
        ("video", "UTC", "dola.video_budget"), ("image", "UTC", "dola.image_budget")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold) == (1, 3, 3)
    assert c.strike_effect == Dead("signed_out")
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (True, True, True, True)
    assert (c.catalog_refresh_s, c.proxy_rest_s, c.check_interval_s) == (3600, 1800, 21600)


def test_the_catalog_is_unique_across_names():
    names = check_catalog([SPEC])
    assert "seedance-2.5" in names and "seedance_v2.5" in names and "seedream-5.0-pro" in names


# ---- codes -----------------------------------------------------------------------------------------------------------
DOLA_CODES = {
    "dola.content_refused", "dola.refused_710022002", "dola.likeness_refused", "dola.needs_confirm", "dola.no_video",
    "dola.no_image",
    "dola.temporary_error", "dola.unknown_status", "dola.http_error", "dola.bad_response",
    "dola.ui_mode_not_ready", "dola.ui_model_select_failed", "dola.ui_ratio_select_failed",
    "dola.ui_duration_patch_failed", "dola.ui_attach_failed", "dola.ui_style_select_failed", "dola.ui_send_failed",
    "dola.ui_no_conversation", "dola.browser_cookies_lost",
    "dola.page_reloaded"}


def test_every_dola_code_of_the_contract_is_registered_and_the_registry_is_sound():
    assert set(SPEC.codes()) == DOLA_CODES
    assert DOLA_CODES <= set(errors.RULES), "the codes are registered when the package loads"
    assert errors.violations(errors.RULES) == []
    assert all(errors.RULES[c] == r for c, r in SPEC.codes().items())


def test_refusals_end_the_job_at_once_and_refund_and_transient_codes_count_an_attempt():
    for code in ("dola.content_refused", "dola.refused_710022002", "dola.likeness_refused", "dola.needs_confirm",
                 "dola.no_video", "dola.no_image"):
        r = errors.rule(code)
        assert (r.terminal, r.refund, r.counts_attempt, r.public, r.job_scoped) == (True, True, False, True, True), code
        assert r.http_status == 422, code
    for code in ("dola.temporary_error", "dola.unknown_status", "dola.http_error", "dola.bad_response"):
        r = errors.rule(code)
        assert (r.terminal, r.refund, r.counts_attempt, r.public) == (False, True, True, True), code
        assert r.http_status == 502, code


def test_the_ui_codes_are_ours_to_fix_so_the_client_sees_a_generic_error():
    ui = [c for c in DOLA_CODES if c.startswith("dola.ui_")]
    assert len(ui) == 8
    for code in ui:
        r = errors.rule(code)
        assert (r.public, r.counts_attempt, r.terminal, r.refund) == (False, True, False, True), code
        body = errors.envelope(code)["error"]
        assert body["code"] == "internal.unexpected" and "dola" in body["message"], body
        assert "selector" not in str(body) and code not in str(body), "the hidden code leaked: %s" % body
        assert errors.http_status(code) == 500


def test_loading_the_codes_twice_registers_them_once():
    before = dict(errors.RULES)
    importlib.reload(dola_errors)           # a test or a reloader must not turn into a duplicate-code error
    assert errors.RULES.keys() == before.keys() and errors.violations(errors.RULES) == []


def test_every_code_a_plan_refuses_with_is_a_registered_platform_code():
    codes_seen = {refused(duration=7).code, refused("nope").code, refused(ratio="2:1").code,
                  refused("seedream-4.5", options={"style": "x"}).code, refused("seedance-2.5-30s").code,
                  refused("seedream-4.5-t0", door="facade").code, refused(prompt="a 16:9 clip", ratio="1:1").code,
                  refused(prompt="a 10-second video", duration=30).code, refused(images=(object(),) * 11).code}
    assert codes_seen == {"input.bad_duration", "input.bad_model", "input.bad_ratio", "input.bad_style",
                          "input.prompt_ratio_conflict", "input.prompt_duration_conflict",
                          "input.too_many_images"}
    assert codes_seen <= set(errors.RULES)
    assert refused("seedream-4.5", options={"template": 0}).code == "input.invalid"


def test_the_warning_codes_are_dola_codes_and_are_not_error_rules():
    warnings = {dola_errors.WARN_DURATION_DRIFT, dola_errors.WARN_MODEL_SUBSTITUTED,
                dola_errors.WARN_PARTLY_WATERMARKED, dola_errors.WARN_PRICE_FALLBACK,
                dola_errors.WARN_DURATION_CAPPED}
    assert warnings == {"dola.duration_drift", "dola.model_substituted", "dola.partly_watermarked",
                        "dola.price_fallback", "dola.duration_capped"}
    assert not (warnings & set(errors.RULES)), "a warning never ends a job, so it is not an error rule"


# ---- deadline_cause (R81) ----------------------------------------------------------------------------------
@mutation(SPEC_FILE, 'if provider_state.get("signed_out"):', "if False:", expect="account.dead_session")
def test_a_deadline_after_a_signed_out_poll_is_a_dead_session_whatever_dola_last_said():
    # the poll sets signed_out on error 712012001; the account, not dola's words, is why nothing came
    state = {"signed_out": True}
    assert SPEC.deadline_cause("", state) == "account.dead_session"
    assert SPEC.deadline_cause(LIKENESS, state) == "account.dead_session"
    assert SPEC.deadline_cause(UPLOAD, state) == "account.dead_session"
    assert SPEC.deadline_cause("The video will be ready in 15 minutes", state) == "account.dead_session"
    assert errors.rule("account.dead_session").terminal and errors.rule("account.dead_session").refund, (
        "I26: a deadline ends the job refunded")


@mutation(SPEC_FILE, "return cause[0]", 'return "render.deadline"',
          expect="dola.content_refused")
def test_otherwise_the_deadline_is_dolas_last_words_then_render_deadline():
    for state in ({}, {"signed_out": False}, {"signed_out": None}, {"no_recheck": True}):
        assert SPEC.deadline_cause("I can't generate the requested images. Try something else.", state) == (
            "dola.content_refused")
        assert SPEC.deadline_cause(SAFETY, state) == "dola.content_refused"
        assert SPEC.deadline_cause(LIKENESS, state) == "dola.likeness_refused"
        assert SPEC.deadline_cause(UPLOAD, state) == "dola.needs_confirm"
        assert SPEC.deadline_cause("", state) == "render.deadline"
        assert SPEC.deadline_cause("The video will be generated using the Dreamina Seedance 2.5 model. It will use 2 "
                                   "credits and be ready in 15 minutes.", state) == "render.deadline", (
            "an accepted order that never came is a real render timeout")
        assert SPEC.deadline_cause("Generating a cinematic video...", state) == "render.deadline"


@mutation(SPEC_FILE, 'return "render.watermarked_only" if provider_state.get("watermarked_only") else',
          'return "render.deadline" if True else', expect="render.watermarked_only")
def test_a_deadline_after_only_watermarked_links_is_render_watermarked_only():
    assert SPEC.deadline_cause("", {"watermarked_only": True}) == "render.watermarked_only"
    assert SPEC.deadline_cause("", {"watermarked_only": False}) == "render.deadline"
    assert SPEC.deadline_cause("", {"signed_out": True, "watermarked_only": True}) == "account.dead_session"
    assert SPEC.deadline_cause(LIKENESS, {"watermarked_only": True}) == "dola.likeness_refused"


def test_every_deadline_cause_is_a_job_ending_refunded_code():
    for code in ("account.dead_session", "dola.content_refused", "dola.likeness_refused", "dola.needs_confirm",
                 "render.deadline", "render.watermarked_only"):
        r = errors.rule(code)
        assert r.code == code and r.terminal and r.refund and not r.counts_attempt, code
