"""The gpt spec (caps, settings, pools, catalog, prices, error rules, `plan()`) and the test kit itself."""

import asyncio
import json

import pytest
from gptkit import ACCESS, ACCOUNT_ID, PLANTED, USER_ID, Ctx, Cut, FakeSession, Resp, assert_no_secret, job, settings

from relay_core import errors, pricing
from relay_core.pool import day_pool
from relay_core.providers import (
    AfterSent,
    Completion,
    CreateRequest,
    Dead,
    InputError,
    MediaIn,
    ProviderRegistry,
    RateAlert,
    check_catalog,
)
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA
from relay_flow.spec import SPEC as FLOW
from relay_gemini.spec import SPEC as GEMINI
from relay_gpt import errors as codes
from relay_gpt.catalog import DEFAULT_COSTS, DEFAULT_PRICES, PRICE_KEYS, RATIOS
from relay_gpt.constants import LIMITS, PROMPT_MAX_CHARS, SUBMIT_TIMEOUT_S
from relay_gpt.spec import POOL_DEFAULTS, SPEC
from relay_meta.spec import SPEC as META
from relay_muse.spec import SPEC as MUSE
from relay_qwen.spec import SPEC as QWEN
from relay_vibes.spec import SPEC as VIBES

SPEC_FILE = "packages/provider-gpt/src/relay_gpt/spec.py"
CATALOG_FILE = "packages/provider-gpt/src/relay_gpt/catalog.py"
ERRORS_FILE = "packages/provider-gpt/src/relay_gpt/errors.py"
CLAIMS_OFF = '    _setting("claims_enabled", bool, False,'
CLAIMS_ON = '    _setting("claims_enabled", bool, True,'
REFUSED_ALERT = '                   RateAlert("gpt.alert_refused_rate", codes.REFUSED, "error_code"))'
NO_REFUSED_ALERT = '                   )'
COST_ONE = "DEFAULT_COSTS = {IMAGE_MODEL: 1}"
COST_TWO = "DEFAULT_COSTS = {IMAGE_MODEL: 2}"
REFUSED_PUBLIC = "public_code=core_errors.CONTENT_REFUSED"
REFUSED_AS_FAILED = "public_code=core_errors.GENERATION_FAILED"
KIT_FILE = "packages/provider-gpt/tests/gptkit.py"
KIT_FIXED = '"planted %s leaked in text #%d" % (name, at)'
KIT_ECHO = '"planted %s leaked: %r" % (name, text)'
IMG = MediaIn("0" * 64, "image/png", 9)


def plan(prompt="a red paper boat", *, model="gpt-image", **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), settings(), {})


# ---- caps, settings, pools, catalog --------------------------------------------------------------------------------
@mutation(SPEC_FILE, CLAIMS_OFF, CLAIMS_ON, expect="gpt must ship drained")
def test_gpt_ships_drained():
    assert SPEC.settings()["gpt.claims_enabled"].default is False, "gpt must ship drained"


def test_the_caps_say_an_http_provider_with_one_image_pool_polled_and_reconciled():
    c = SPEC.caps
    assert SPEC.name == "gpt" and c.execution == "http" and SPEC.ref_is_conversation is True
    assert [(p.name, p.unit) for p in c.pools] == [("image", "jobs")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold, c.strike_effect) == (1, 3, 3, Dead("signed_out"))
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy) == (True, True, False)
    assert (c.proxy_rest_s, c.check_interval_s) == (1800, 3600)
    assert SPEC.max_prompt_chars == PROMPT_MAX_CHARS == 4000


def test_the_registry_accepts_the_spec():
    registry = ProviderRegistry()
    registry.register(SPEC)
    check_catalog([DOLA, META, VIBES, FLOW, GEMINI, QWEN, MUSE, SPEC])
    (model,) = SPEC.catalog()
    assert (model.id, model.media_type, model.operations, model.display_name) == (
        "gpt-image", "image", ("text_to_image", "image_to_image"), "GPT Image")
    assert model.limits["max_images"] == 1 and model.limits["ratios"] == list(RATIOS) == ["1:1", "16:9", "9:16", "3:2"]
    assert model.price_keys == {"default": "gpt-image"}


@mutation(SPEC_FILE, REFUSED_ALERT, NO_REFUSED_ALERT, expect="a refusal rate must be alerted on")
def test_the_settings_are_the_tunables_with_their_defaults_and_bounds():
    s = SPEC.settings()
    want = {"render_deadline_s": (int, 900, 300, None), "poll_interval_s": (int, 15, 5, 120),
            "account_min_gap_s": (int, 30, 0, 3600), "image_lost_after_s": (int, 600, 120, 3600),
            "page_max_age_s": (int, 3600, 300, 86400), "signed_out_rest_s": (int, 600, 60, 86400),
            "signed_out_gap_s": (int, 540, 0, 86400), "network_rest_s": (int, 300, 30, 3600),
            "challenge_rest_s": (int, 3600, 300, 86400), "alert_turnstile_rate": (float, 0.2, 0.0, 1.0),
            "refusal_max_accounts": (int, 2, 1, 9), "alert_refused_rate": (float, 0.5, 0.0, 1.0)}
    for key, (kind, default, low, high) in want.items():
        spec = s["gpt." + key]
        assert (spec.type, spec.default, spec.min, spec.max) == (kind, default, low, high), key
    assert {"gpt.prices", "gpt.costs", "gpt.pools", "gpt.disabled_models"} <= set(s)
    assert all(k.startswith("gpt.") for k in s)
    assert {k for k, v in s.items() if v.basic} == {"gpt.claims_enabled", "gpt.prices", "gpt.disabled_models"}
    assert SPEC.rate_alerts == (RateAlert("gpt.alert_turnstile_rate", codes.WARN_TURNSTILE, "warning"),
                                RateAlert("gpt.alert_refused_rate", codes.REFUSED, "error_code")), \
        "a refusal rate must be alerted on"
    assert codes.WARN_TURNSTILE == "gpt.turnstile_unsolved"


def test_one_image_pool_counted_in_jobs_with_tier_budgets():
    assert POOL_DEFAULTS["image"] == day_pool({"free": 3, "pro": 40, "ultra": 100})
    pools = SPEC.settings()["gpt.pools"]
    assert pools.validate(pools.default) == pools.default, "the shipped pools pass their own rule"


@mutation(CATALOG_FILE, COST_ONE, COST_TWO, expect="price is not 1:1 with cost")
def test_price_is_cost_one_job_one_point():
    assert DEFAULT_COSTS == {"gpt-image": 1} and DEFAULT_PRICES == {"gpt-image": 1}, "price is not 1:1 with cost"
    assert PRICE_KEYS == ("gpt-image",)
    priced = pricing.price_plan(SPEC, plan(), settings())
    assert (priced.price, priced.account_cost) == (1, 1)


def test_every_number_of_the_pure_modules_is_on_the_fixed_limits_card():
    names = {f.name for f in SPEC.limits}
    assert set(LIMITS) <= set(SPEC.limits) and "relay_gpt.credential.MAX_COOKIES" in names
    import relay_gpt.constants as K
    for f in LIMITS:
        assert getattr(K, f.name.rsplit(".", 1)[1]) == f.value, f.name


# ---- plan ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("req, code, param", [
    (dict(n=2), "input.invalid", "n"), (dict(images=(IMG, IMG)), "input.too_many_images", "images"),
    (dict(ratio="4:3"), "input.bad_ratio", "ratio"), (dict(prompt="  ​ "), "input.empty_prompt", "prompt"),
    (dict(duration=5), "input.bad_duration", "duration"), (dict(model="gpt-video"), "input.bad_model", "model")])
def test_plan_refuses(req, code, param):
    with pytest.raises(InputError) as e:
        plan(**req)
    assert (e.value.code, e.value.param) == (code, param)
    assert code in errors.RULES


def test_plan_t2i_and_edit():
    p = plan()
    assert (p.operation, p.media_type, p.model, p.completion, p.after_sent, p.price_key, p.pool,
            p.submit_timeout_s) == ("text_to_image", "image", "gpt-image", Completion.HOLD, AfterSent.RECONCILE,
                                    "gpt-image", "image", SUBMIT_TIMEOUT_S)
    assert dict(p.params) == {"kind": "image", "ratio": None}, "no ratio stays None (upstream draws 3:2)"
    assert plan(n=1, ratio="16:9").params["ratio"] == "16:9"
    edit = plan(images=(IMG,))
    assert (edit.operation, edit.completion, edit.pool) == ("image_to_image", Completion.HOLD, "image")
    # timeouts come from the constants, never from the request
    assert plan(options={"timeout": 5, "submit_timeout_s": 5}).submit_timeout_s == SUBMIT_TIMEOUT_S


# ---- error codes ---------------------------------------------------------------------------------------------------
@mutation(ERRORS_FILE, REFUSED_PUBLIC, REFUSED_AS_FAILED, expect="a refusal must read content_refused")
def test_refused_is_terminal_refunded_content_refused():
    r = errors.RULES["gpt.refused"]
    assert r.terminal and r.refund, "a refusal ends the job with its charge returned"
    assert r.public_code == errors.CONTENT_REFUSED, "a refusal must read content_refused"


def test_every_code_is_namespaced_and_ends_job_codes_refund():
    table = SPEC.codes()
    assert set(table) == {"gpt.http_error", "gpt.image_limit", "gpt.challenge_required", "gpt.unavailable",
                          "gpt.refused", "gpt.image_lost", "gpt.output_missing"}
    for code, rule in table.items():
        assert code.startswith("gpt.") and errors.RULES[code] == rule, code
        assert rule.refund == errors.ends_job(rule), code
    retry = table["gpt.http_error"]
    assert (retry.terminal, retry.counts_attempt) == (False, True)
    for code in ("gpt.image_limit", "gpt.challenge_required"):        # the account's state: back to the queue, free
        assert (table[code].terminal, table[code].counts_attempt, table[code].refund) == (False, False, False), code
    for code, public in (("gpt.unavailable", errors.TEMPORARILY_UNAVAILABLE), ("gpt.image_lost",
                         errors.GENERATION_FAILED), ("gpt.output_missing", errors.GENERATION_FAILED)):
        assert table[code].terminal and table[code].refund and table[code].public_code == public, code


# ---- the kit -------------------------------------------------------------------------------------------------------
def test_the_kit_plants_a_token_that_names_the_user_the_workspace_and_the_plan():
    import base64
    payload = ACCESS.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    auth = claims["https://api.openai.com/auth"]
    assert (auth["chatgpt_user_id"], auth["chatgpt_account_id"], auth["chatgpt_plan_type"]) == (
        USER_ID, ACCOUNT_ID, "plus")
    assert all("PLANTED" in p for p in PLANTED)
    with pytest.raises(AssertionError):
        assert_no_secret("x " + ACCESS.split(".")[1])


@mutation(KIT_FILE, KIT_FIXED, KIT_ECHO, expect="the helper echoed the leak")
def test_the_secret_helper_names_the_leak_but_never_prints_it():
    for planted in PLANTED:
        with pytest.raises(AssertionError) as e:
            assert_no_secret("fine", "before %s after" % planted)
        assert "PLANTED" not in str(e.value) and planted not in str(e.value), "the helper echoed the leak"
        assert "text #1" in str(e.value)


def test_the_kit_session_answers_from_its_routes_logs_in_order_and_cuts_a_stream():
    log: list = []
    s = FakeSession({("GET", "/api/auth/session"): Resp(json_body={"ok": 1}, cookies={"a": "b"}),
                     ("POST", "/backend-api/f/conversation"): Resp(lines=["data: 1", "data: 2", "data: 3"], cut=2),
                     ("PUT", "files.oaiusercontent.com/*"): [Resp(201), Resp(500)]}, log)
    ctx = Ctx(log)

    async def run():
        out = [(await s.get("https://chatgpt.com/api/auth/session", allow_redirects=False)).json()]
        await ctx.mark_sent()
        r = await s.post("https://chatgpt.com/backend-api/f/conversation", allow_redirects=False, stream=True)
        try:
            async for line in r.aiter_lines():
                out.append(line)
        except Cut:
            out.append("cut")
        for _ in range(3):
            out.append((await s.put("https://files.oaiusercontent.com/f?sig=x", allow_redirects=False)).status_code)
        out.append((await s.get("https://chatgpt.com/nothing", allow_redirects=False)).status_code)
        return out
    assert asyncio.run(run()) == [{"ok": 1}, b"data: 1", b"data: 2", "cut", 201, 500, 500, 404]
    assert [e if isinstance(e, str) else e[0] for e in log] == ["GET", "mark_sent", "POST", "PUT", "PUT", "PUT", "GET"]
    assert s.cookies.get("a") == "b"
    with pytest.raises(AssertionError):
        asyncio.run(s.get("https://chatgpt.com/"))          # a request that may follow a redirect
    j = job(image=True)
    assert (j.operation, j.params["kind"], [r.object_key for r in asyncio.run(j.inputs())]) == (
        "image_to_image", "image", ["inputs/one.png"])


def test_build_engine_has_poll_reconcile_and_no_probe():
    from relay_gpt.engine import GptEngine, build_engine
    engine = build_engine(SPEC, settings(poll_interval_s=42))
    assert isinstance(engine, GptEngine)
    assert hasattr(engine, "poll") == SPEC.caps.has_poll is True
    assert hasattr(engine, "reconcile") == SPEC.caps.has_reconcile is True
    assert hasattr(engine, "probe_proxy") == SPEC.caps.has_probe_proxy is False
    # the delegates bind the worker's settings: a job with no conversation yet is read again after gpt.poll_interval_s
    polled = asyncio.run(engine.poll(Ctx(), job())).outcome
    assert (polled.next_in_s, polled.answered) == (42, False)
    with pytest.raises(ValueError, match="gpt runs no command"):
        asyncio.run(engine.run_command(Ctx(), "anything", {}))
