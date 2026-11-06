"""The grok spec (caps, settings, pools, catalog, prices, error rules, `plan()`) and the test kit itself."""

import asyncio

import pytest
from grokkit import (
    PLANTED,
    SSO,
    Clock,
    Ctx,
    Cut,
    FakeSession,
    FakeSocket,
    Resp,
    assert_no_secret,
    fixture,
    job,
    names,
    settings,
)

from relay_core import errors, pricing
from relay_core.providers import (
    AfterSent,
    Completion,
    CreateRequest,
    Dead,
    Done,
    Found,
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
from relay_gpt.spec import SPEC as GPT
from relay_grok import errors as codes
from relay_grok.catalog import DEFAULT_COSTS, DEFAULT_PRICES, MAX_N, PRICE_KEYS, image_key
from relay_grok.constants import LIMITS, PROMPT_MAX_CHARS, RESPONSES_PATH, STATE_CONVERSATION, STATE_REQUEST
from relay_grok.engine import GrokEngine
from relay_grok.engine import submit as submit_module
from relay_grok.spec import AFTER_SENT, POOL_DEFAULTS, SPEC
from relay_meta.spec import SPEC as META
from relay_muse.spec import SPEC as MUSE
from relay_qwen.spec import SPEC as QWEN
from relay_vibes.spec import SPEC as VIBES

SPEC_FILE = "packages/provider-grok/src/relay_grok/spec.py"
CATALOG_FILE = "packages/provider-grok/src/relay_grok/catalog.py"
ERRORS_FILE = "packages/provider-grok/src/relay_grok/errors.py"
IMG = MediaIn("0" * 64, "image/png", 9)


def plan(prompt="a red paper boat on a lake at dawn", *, model="grok-image", **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), settings(), {})


# ---- caps, settings, pools, catalog ----------------------------------------------------------------------------------
@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="grok must ship drained")
def test_grok_ships_drained():
    assert SPEC.settings()["grok.claims_enabled"].default is False, "grok must ship drained"


def test_the_caps_say_an_http_provider_with_two_pools_held_and_reconciled_by_the_table():
    c = SPEC.caps
    assert SPEC.name == "grok" and c.execution == "http"
    assert [(p.name, p.unit) for p in c.pools] == [("image", "uses"), ("video", "uses")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold, c.strike_effect) == (1, 3, 3, Dead("signed_out"))
    assert (c.has_poll, c.has_probe_proxy) == (True, False)
    assert c.has_reconcile == (AfterSent.RECONCILE in AFTER_SENT.values())
    assert (c.proxy_rest_s, c.check_interval_s) == (300, 3600)
    assert SPEC.max_prompt_chars == PROMPT_MAX_CHARS == 4000
    assert SPEC.frames.verbatim is True


def test_the_registry_accepts_the_spec():
    registry = ProviderRegistry()
    registry.register(SPEC)
    check_catalog([DOLA, META, VIBES, FLOW, GEMINI, QWEN, MUSE, GPT, SPEC])
    image, video = SPEC.catalog()
    assert (image.id, image.operations) == ("grok-image", ("text_to_image", "image_to_image"))
    assert (video.id, video.operations) == ("grok-video", ("text_to_video",)), "no image-to-video in this slice"
    assert image.limits["ratios"] == ["1:1", "2:3", "3:2", "9:16", "16:9"] and image.limits["max_images"] == 3
    assert video.limits["ratios"] == ["16:9", "9:16", "1:1"] and video.limits["max_images"] == 0
    assert image.price_keys == {"n=1": "grok-image", "n=2": "grok-image@n2", "n=3": "grok-image@n3",
                                "n=4": "grok-image@n4"}
    assert video.price_keys == {"default": "grok-video"}


def _rolling(hours, free):
    return {"combine": "sum", "windows": [{"name": "quota", "kind": "rolling", "hours": hours, "anchor": "reset",
                                           "budget": {"free": free}}]}


@mutation(SPEC_FILE, '    "image": _rolling(24, 8),', '    "image": _rolling(24, 9),',
          expect="the free image budget is 8 uses a rolling day")
@mutation(SPEC_FILE, '    "video": _rolling(25, 1),', '    "video": _rolling(25, 2),',
          expect="the free video budget is 1 use per 25 h")
def test_two_rolling_pools_anchored_at_reset_with_free_budgets():
    assert POOL_DEFAULTS["image"] == _rolling(24, 8), "the free image budget is 8 uses a rolling day"
    assert POOL_DEFAULTS["video"] == _rolling(25, 1), "the free video budget is 1 use per 25 h"
    pools = SPEC.settings()["grok.pools"]
    assert pools.validate(pools.default) == pools.default, "the shipped pools pass their own rule"


@mutation(CATALOG_FILE, '"grok-image@n3": 3', '"grok-image@n3": 2', expect="price is not 1:1 with cost")
def test_price_is_cost_one_to_one():
    assert PRICE_KEYS == ("grok-image", "grok-image@n2", "grok-image@n3", "grok-image@n4", "grok-video")
    for n in range(1, MAX_N + 1):
        assert DEFAULT_PRICES[image_key(n)] == DEFAULT_COSTS[image_key(n)] == n, "price is not 1:1 with cost"
    assert DEFAULT_PRICES["grok-video"] == DEFAULT_COSTS["grok-video"] == 1, "price is not 1:1 with cost"
    priced = pricing.price_plan(SPEC, plan(n=3), settings())
    assert (priced.price, priced.account_cost) == (3, 3)


SETTINGS = [
    ("claims_enabled", bool, False, None, None), ("render_deadline_s", int, 900, 60, None),
    ("image_submit_timeout_s", int, 90, 30, 600), ("edit_submit_timeout_s", int, 120, 30, 600),
    ("video_submit_timeout_s", int, 240, 60, 720), ("send_gap_s", int, 30, 0, 3600),
    ("limit_rest_s", int, 86400, 600, 604800), ("refusal_max_accounts", int, 2, 1, 9),
    ("decoy_min_s", int, 15, 0, 120), ("decoy_rest_s", int, 86400, 0, 604800),
    ("cf_rest_s", int, 600, 60, 86400), ("antibot_rest_s", int, 1800, 60, 86400),
    ("statsig_mode", str, "off", None, None),
    ("alert_antibot_rate", float, 0.1, 0.0, 1.0), ("alert_decoy_rate", float, 0.2, 0.0, 1.0)]


@pytest.mark.parametrize("key, kind, default, low, high", SETTINGS)
def test_settings_have_the_design_defaults_and_bounds(key, kind, default, low, high):
    spec = SPEC.settings()["grok." + key]
    assert (spec.type, spec.default, spec.min, spec.max) == (kind, default, low, high), key


def test_the_settings_are_namespaced_and_statsig_takes_off_or_generate():
    s = SPEC.settings()
    assert {"grok.prices", "grok.costs", "grok.pools", "grok.disabled_models"} <= set(s)
    assert set(s) == {"grok." + row[0] for row in SETTINGS} | {"grok.prices", "grok.costs", "grok.pools",
                                                                "grok.disabled_models"}
    assert {k for k, v in s.items() if v.basic} == {"grok.claims_enabled", "grok.prices", "grok.disabled_models"}
    mode = s["grok.statsig_mode"]
    assert mode.rule.choices == ("off", "generate")
    for good in ("off", "generate"):
        assert mode.validate(good) == good
    with pytest.raises(ValueError):
        mode.validate("always")
    assert SPEC.rate_alerts == (RateAlert("grok.alert_antibot_rate", ("grok.antibot", "grok.cloudflare"),
                                          "error_code"),
                                RateAlert("grok.alert_decoy_rate", "grok.decoy_video", "error_code"))


def test_every_number_of_the_pure_modules_is_on_the_fixed_limits_card():
    names_ = {f.name for f in SPEC.limits}
    assert set(LIMITS) <= set(SPEC.limits)
    assert {"relay_grok.catalog.MAX_N", "relay_grok.catalog.MAX_REFS"} <= names_
    import relay_grok.constants as K
    for f in LIMITS:
        assert getattr(K, f.name.rsplit(".", 1)[1]) == f.value, f.name


# ---- plan ------------------------------------------------------------------------------------------------------------
@mutation(SPEC_FILE, "        if _MODE_FLAG.search(prompt):", "        if False:",
          expect="a mode flag reached the prompt")
@mutation(SPEC_FILE,
          '        if req.duration is not None and (kind != "video" or req.duration != VIDEO_DURATION_S):',
          "        if False:", expect="a duration other than 6 s was planned")
@pytest.mark.parametrize("req, code, param, why", [
    (dict(n=5), "input.invalid", "n", "n above 4 was planned"),
    (dict(n=2, images=(IMG,)), "input.invalid", "n", "an edit of 2 images was planned"),
    (dict(images=(IMG,) * 4), "input.too_many_images", "images", "4 inputs were planned"),
    (dict(ratio="4:3"), "input.bad_ratio", "ratio", "an unknown image ratio was planned"),
    (dict(ratio="1:1", images=(IMG,)), "input.bad_ratio", "ratio", "an edit took a ratio"),
    (dict(model="grok-video", ratio="2:3"), "input.bad_ratio", "ratio", "an unknown video ratio was planned"),
    (dict(model="grok-video", duration=10), "input.bad_duration", "duration", "a duration other than 6 s was planned"),
    (dict(duration=6), "input.bad_duration", "duration", "an image took a duration"),
    (dict(prompt="a boat --mode=extremely-spicy"), "input.invalid", "prompt", "a mode flag reached the prompt"),
    (dict(model="grok-video", prompt="a boat --MODE = spicy"), "input.invalid", "prompt",
     "a mode flag reached the prompt"),
    (dict(model="grok-video", images=(IMG,)), "input.too_many_images", "images", "a video took an input image"),
    (dict(prompt="x" * (PROMPT_MAX_CHARS + 1)), "input.prompt_too_long", "prompt", "a prompt over 4000 was planned"),
    (dict(prompt="  ​ "), "input.empty_prompt", "prompt", "a blank prompt was planned"),
    (dict(model="grok-music"), "input.bad_model", "model", "an unknown model was planned")])
def test_plan_refuses(req, code, param, why):
    try:
        plan(**req)
        got = None
    except InputError as e:
        got = (e.code, e.param)
    assert got == (code, param), why
    assert code in errors.RULES


@mutation(SPEC_FILE, '    "text_to_image": AfterSent.RECONCILE,', '    "text_to_image": AfterSent.FAIL_REFUND,',
          expect="t2i must be reconciled by its conversation")
def test_plan_ops():
    t2i = plan(n=3)
    assert (t2i.operation, t2i.media_type, t2i.model, t2i.price_key, t2i.pool) == (
        "text_to_image", "image", "grok-image", "grok-image@n3", "image")
    assert dict(t2i.params) == {"kind": "image", "n": 3, "ratio": "1:1"}
    assert plan(ratio="9:16").params["ratio"] == "9:16" and plan().price_key == "grok-image"
    edit = plan(images=(IMG, IMG, IMG))
    assert (edit.operation, edit.price_key, edit.pool, dict(edit.params)) == (
        "image_to_image", "grok-image", "image", {"kind": "image", "n": 1})
    video = plan(model="grok-video", duration=6)
    assert (video.operation, video.media_type, video.price_key, video.pool, dict(video.params)) == (
        "text_to_video", "video", "grok-video", "video", {"kind": "video", "ratio": "16:9", "duration": 6})
    assert plan(model="grok-video", ratio="9:16").params["ratio"] == "9:16"
    for p in (t2i, edit, video):
        assert p.completion == Completion.HOLD and p.after_sent == AFTER_SENT[p.operation], p.operation
    assert (t2i.submit_timeout_s, edit.submit_timeout_s, video.submit_timeout_s) == (90, 120, 240)
    # timeouts come from the settings, never from the request
    assert plan(options={"submit_timeout_s": 5}).submit_timeout_s == 90
    assert SPEC.plan(CreateRequest(model="grok-image", prompt="a boat"), settings(image_submit_timeout_s=33),
                     {}).submit_timeout_s == 33
    assert AFTER_SENT["image_to_image"] == AFTER_SENT["text_to_video"] == AfterSent.RECONCILE
    assert t2i.after_sent == AfterSent.RECONCILE, "t2i must be reconciled by its conversation"
    assert SPEC.caps.has_reconcile is True


# ---- error codes -----------------------------------------------------------------------------------------------------
def test_every_code_is_namespaced_and_end_codes_refund():
    table = SPEC.codes()
    assert set(table) == {"grok.http_error", "grok.cloudflare", "grok.antibot", "grok.input_unusable",
                          "grok.content_refused", "grok.no_image", "grok.no_video", "grok.decoy_video", "grok.limit"}
    for code, rule in table.items():
        assert code.startswith("grok.") and errors.RULES[code] == rule, code
        assert rule.refund == (rule.terminal or rule.counts_attempt), code
    retry = table["grok.http_error"]
    assert (retry.terminal, retry.counts_attempt, retry.public_code) == (False, True, errors.INTERNAL_ERROR)
    for code in ("grok.cloudflare", "grok.antibot"):  # nothing sent: requeued, no attempt
        r = table[code]
        assert (r.terminal, r.counts_attempt, r.refund, r.free_retries) == (False, False, False, 2), code
        assert r.public_code == errors.TEMPORARILY_UNAVAILABLE, code
    for code, public in (("grok.input_unusable", errors.INVALID_INPUT), ("grok.no_image", errors.GENERATION_FAILED),
                         ("grok.no_video", errors.GENERATION_FAILED), ("grok.decoy_video", errors.GENERATION_FAILED),
                         ("grok.limit", errors.TEMPORARILY_UNAVAILABLE)):
        r = table[code]
        assert (r.terminal, r.refund, r.public_code) == (True, True, public), code


@mutation(ERRORS_FILE, "public_code=core_errors.CONTENT_REFUSED", "public_code=core_errors.GENERATION_FAILED",
          expect="a refusal must read content_refused")
def test_content_refused_reads_content_refused():
    r = errors.RULES[codes.CONTENT_REFUSED]
    assert r.terminal and r.refund, "a refusal ends the job with its charge returned"
    assert r.public_code == errors.CONTENT_REFUSED, "a refusal must read content_refused"


# ---- the kit ---------------------------------------------------------------------------------------------------------
def test_the_secret_helper_names_the_leak_but_never_prints_it():
    assert all("PLANTED" in p for p in PLANTED)
    for planted in PLANTED:
        with pytest.raises(AssertionError) as e:
            assert_no_secret("fine", "before %s after" % planted)
        assert "PLANTED" not in str(e.value) and planted not in str(e.value), "the helper echoed the leak"
        assert "text #1" in str(e.value)


def test_the_kit_session_answers_routes_records_cookies_and_streams():
    ctx = Ctx()
    s = FakeSession({("GET", "/api/auth/session"): Resp(json_body={"status": "authenticated"}),
                     ("POST", "/rest/app-chat/conversations/new"): Resp(lines=["a", "b", "c"], cut=2),
                     ("GET", "imagine-public.x.ai/"): [Resp(200, content=b"jpg"), Resp(500)]}, ctx.calls)
    make = s
    session = make({"https": "http://user:PLANTED-pw@10.0.0.9:3128"})
    session.cookies.set("sso", SSO, domain=".grok.com")

    async def run():
        out = [(await session.get("https://grok.com/api/auth/session", allow_redirects=False)).json()]
        await ctx.mark_sent()
        r = await session.post("https://grok.com/rest/app-chat/conversations/new", stream=True,
                               allow_redirects=False)
        try:
            async for line in r.aiter_lines():
                out.append(line)
        except Cut:
            out.append("cut")
        for _ in range(3):
            out.append((await session.get("https://imagine-public.x.ai/imagine-public/images/x.jpg",
                                          allow_redirects=False)).status_code)
        out.append((await session.get("https://grok.com/nothing")).status_code)
        return out
    assert asyncio.run(run()) == [{"status": "authenticated"}, b"a", b"b", "cut", 200, 500, 500, 404]
    assert names(ctx.calls) == ["GET /api/auth/session", "mark_sent", "POST /rest/app-chat/conversations/new",
                                "GET /imagine-public/images/x.jpg", "GET /imagine-public/images/x.jpg",
                                "GET /imagine-public/images/x.jpg", "GET /nothing"]
    has = [(c.host, c.has_cookie) for c in session.requests]
    assert has[0] == ("grok.com", True) and has[2] == ("imagine-public.x.ai", False), "the jar follows the domain"
    assert session.requests[0].proxies == {"https": "http://user:PLANTED-pw@10.0.0.9:3128"}
    assert session.requests[-1].allow_redirects is True


def test_the_kit_socket_allows_one_item_create_and_nothing_after_it_and_a_hung_stream_hangs():
    ctx = Ctx()
    sock = FakeSocket([{"type": "session", "conversation_id": "c"}], calls=ctx.calls)
    reset = {"type": "conversation.item.create", "item": {"type": "message", "content": [{"type": "reset"}]}}
    make = {"type": "conversation.item.create",
            "item": {"type": "message", "content": [{"type": "input_text", "text": "x", "requestId": "r"}]}}

    async def run():
        await sock.send_json(reset)
        await sock.send_json(make)
        return await sock.recv_json()
    assert asyncio.run(run()) == {"type": "session", "conversation_id": "c"}
    assert names(ctx.calls) == ["reset", "item.create"]
    with pytest.raises(AssertionError, match="second item.create"):
        asyncio.run(sock.send_json(make))
    with pytest.raises(AssertionError, match="after the item.create"):
        asyncio.run(sock.send_json(reset))

    async def hung():
        r = Resp(lines=["one"], hang=True)
        async with asyncio.timeout(0.05):
            async for _ in r.aiter_lines():
                pass
    with pytest.raises(TimeoutError):
        asyncio.run(hung())


def test_the_kit_plans_each_operation_and_the_clock_moves_only_when_told():
    assert (job("t2i", n=2).operation, job("t2i", n=2).params["n"]) == ("text_to_image", 2)
    edit = job("edit")
    assert edit.operation == "image_to_image" and len(asyncio.run(edit.inputs())) == 1
    assert job("t2v").params == {"kind": "video", "ratio": "16:9", "duration": 6}
    clock = Clock()
    asyncio.run(clock.sleep(5))
    assert clock() == 1005.0 and (clock.now() - Clock().now()).total_seconds() == 5


# ---- the engine (worker side) ----------------------------------------------------------------------------------------
ENGINE_FILE = "packages/provider-grok/src/relay_grok/engine/__init__.py"
T2I_CONVERSATION = "c0000000-0000-4000-8000-000000000008"     # the read-back fixture's text-to-image conversation


def test_build_engine_matches_the_caps():
    from relay_grok.engine import GrokEngine, build_engine
    engine = build_engine(SPEC, settings())
    assert isinstance(engine, GrokEngine)
    assert hasattr(engine, "poll") == SPEC.caps.has_poll is True
    assert hasattr(engine, "reconcile") == SPEC.caps.has_reconcile is True
    assert hasattr(engine, "probe_proxy") == SPEC.caps.has_probe_proxy is False
    with pytest.raises(ValueError, match="grok runs no command"):
        asyncio.run(engine.run_command(Ctx(), "anything", {}))


@mutation(SPEC_FILE, "strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True,",
          "strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=False,", expect="a reconciled job is never delivered")
def test_a_reconciled_job_ends_delivered():
    """The core moves a `Found` job to `waiting`, and only a poll hands its outputs to the fetcher: without the poll a
    job the reconcile found is never delivered (the render deadline refunds what was made)."""
    s = FakeSession({("GET", RESPONSES_PATH % T2I_CONVERSATION): Resp(json_body=fixture("readback_t2i")["body"])})
    engine = GrokEngine(settings(), make_session=s)
    lost = job("t2i", n=2, state={STATE_REQUEST: "r-1", STATE_CONVERSATION: T2I_CONVERSATION})
    found = asyncio.run(engine.reconcile(Ctx(), lost))
    assert isinstance(found, Found) and found.taken
    assert SPEC.caps.has_poll, "a reconciled job is never delivered"
    waiting = job("t2i", n=2, state={**lost.provider_state, **found.provider_state})
    done = asyncio.run(engine.poll(Ctx(), waiting)).outcome
    assert isinstance(done, Done) and len(done.outputs) == 2
    assert all(o.ref.startswith("https://imagine-public.x.ai/") and o.mime == "image/jpeg" for o in done.outputs)
    assert s.requests and all(r.proxies == s.proxies for r in s.requests) and s.proxies is not None


def test_statsig_off_builds_no_generator_and_the_submit_gets_none(monkeypatch):
    s = FakeSession()
    engine = GrokEngine(settings(), make_session=s)
    seen = []

    async def fake_submit(ctx, job_, settings_, *, deps):
        seen.append(deps)
        return "result"
    monkeypatch.setattr(submit_module, "submit", fake_submit)
    assert asyncio.run(engine.submit(Ctx(), job("t2i"))) == "result"
    assert seen[0].statsig is None and seen[0].make_session is s
    assert s.requests == [], "statsig off must send no header and read no page"


@mutation(ENGINE_FILE, "statsig=self.statsig(ctx.account))", "statsig=None)",
          expect="the statsig generator never reaches the submit")
def test_statsig_generate_reads_the_page_once_through_the_account_proxy(monkeypatch):
    s = FakeSession({("GET", "/imagine"): Resp(200, text=fixture("page_home.html")),
                     ("GET", "/_next/static/"): Resp(200, text="var a=1;")})
    engine = GrokEngine(settings(statsig_mode="generate"), make_session=s)
    seen = []

    async def fake_submit(ctx, job_, settings_, *, deps):
        seen.append(await deps.statsig("POST", "/rest/app-chat/conversations/new") if deps.statsig else "none")
        return "result"
    monkeypatch.setattr(submit_module, "submit", fake_submit)
    asyncio.run(engine.submit(Ctx(), job("t2i")))
    asyncio.run(engine.submit(Ctx(), job("t2i")))
    assert seen == [None, None], "the statsig generator never reaches the submit"   # 06/11's page has no seed
    pages = [r for r in s.requests if r.path == "/imagine"]
    assert len(pages) == 1, "the seed page is read once per max age, not once per job"
    assert pages[0].has_cookie and s.proxies is not None and all(r.proxies == s.proxies for r in s.requests)
    assert all(not r.has_cookie for r in s.requests if r.path.startswith("/_next/static/"))


def test_an_unreadable_seed_page_gives_no_header_and_never_fails_the_job():
    engine = GrokEngine(settings(statsig_mode="generate"), make_session=FakeSession())     # every GET is a 404
    sign = engine.statsig(Ctx().account)
    assert asyncio.run(sign("POST", "/rest/media/imagine/quota_info")) is None
