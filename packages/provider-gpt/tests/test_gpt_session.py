"""The gpt session: the token from `/api/auth/session` only, cached to `exp - margin`, every call through the account's
proxy with no redirect, the re-set session cookie written back whole, and the account check (identity, plan -> tier,
the image balance, a signed-out read as a spaced strike)."""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from gptkit import (
    ACCESS,
    ACCOUNT_ID,
    NOW,
    PROXY,
    USER_ID,
    Ctx,
    FakeSession,
    Resp,
    access_token,
    assert_no_secret,
    export,
    fixture,
    settings,
)

from relay_core.providers import Balance, Cooldown, Suspect, Tier
from relay_core.testing.mutation import mutation
from relay_gpt.constants import SESSION_COOKIE
from relay_gpt.credential import jar_of, parse_credential, session_parts
from relay_gpt.engine.check import check_account, image_balance, tier_of
from relay_gpt.engine.web import BadAnswer, NetError, Page, Sessions, SignedOut, Web, claims, proxy_url

WEB_FILE = "packages/provider-gpt/src/relay_gpt/engine/web.py"
CHECK_FILE = "packages/provider-gpt/src/relay_gpt/engine/check.py"
AUTH_SESSION_PATH = 'AUTH_SESSION_PATH = "/api/auth/session"'
OAUTH_PATH = 'AUTH_SESSION_PATH = "/oauth/token"'
PROXIES_LINE = '        self.proxies = {"http": url, "https": url} if url else None'
NO_PROXIES_LINE = "        self.proxies = None"
NO_REDIRECTS = "allow_redirects=False"
REDIRECTS = "allow_redirects=True"
WRITE_BACK = "with_session(ctx.account.credential, parts, "
NO_WRITE_BACK = "with_session(ctx.account.credential, web.start_parts, "
PLUS_PRO = '"plus": "pro"'
PLUS_FREE = '"plus": "free"'
ONLY_ZERO = "remaining != 0"
ANY_REMAINING = "remaining < 0"

S0, S1 = SESSION_COOKIE + ".0", SESSION_COOKIE + ".1"
NEW0 = "eyJ-PLANTED-session-new-0"
HOME = '<html data-build="ccbd58c87c3a77dcbef035020b701a5bc7c54b87"><script src="https://chatgpt.com/sentinel/' \
       '20260810913b/sdk.js"></script></html>'


def run(coro):
    return asyncio.run(coro)


def session_body(token: str = ACCESS) -> dict:
    body = fixture("auth_session")["body"]
    body["accessToken"] = token
    return body


def routes(**over) -> dict:
    base = {("GET", "/api/auth/session"): Resp(json_body=session_body()),
            ("POST", "/backend-api/conversation/init"): Resp(json_body=fixture("init_limits")["body"]),
            ("GET", "/"): Resp(text=HOME)}
    base.update({(k.split(" ", 1)[0], k.split(" ", 1)[1]): v for k, v in over.items()})
    return base


class Maker:
    """`make_session`: one FakeSession per call over the same routes and log; records the proxies it was given."""

    def __init__(self, table: dict | None = None, log: list | None = None, session_cls=FakeSession):
        self.table = table if table is not None else routes()
        self.log = log if log is not None else []
        self.proxies: list = []
        self.sessions: list[FakeSession] = []
        self.cls = session_cls

    def __call__(self, proxies):
        self.proxies.append(proxies)
        s = self.cls(self.table, self.log, proxies=proxies)
        self.sessions.append(s)
        return s

    def paths(self) -> list[str]:
        return [urlsplit(url).path for _, url, _ in self.log if isinstance(url, str)]


class Clock:
    def __init__(self, t: float = NOW):
        self.t = t

    def __call__(self) -> float:
        return self.t


async def token_of(ctx, sessions, maker) -> str:
    async with Web(ctx.account, ctx.settings, sessions, ctx, make_session=maker) as web:
        return await web.access_token()


# ---- the token ------------------------------------------------------------------------------------------------
@mutation(WEB_FILE, AUTH_SESSION_PATH, OAUTH_PATH, expect="only /api/auth/session may give a token")
def test_the_token_comes_only_from_api_auth_session():
    def any_path(req):
        return Resp(json_body=session_body())
    maker = Maker({("GET", "/*"): any_path, ("POST", "/*"): any_path})
    ctx = Ctx(maker.log)
    assert run(token_of(ctx, Sessions(clock=Clock()), maker)) == ACCESS
    hosts = {urlsplit(url).hostname for _, url, _ in maker.log}
    assert hosts == {"chatgpt.com"}
    assert maker.paths() == ["/api/auth/session"], "only /api/auth/session may give a token"
    assert not any("oauth" in p or "auth.openai.com" in p for p in maker.paths())


def test_the_token_is_reused_until_exp_minus_margin():
    clock = Clock()
    sessions = Sessions(clock=clock)
    maker = Maker()
    ctx = Ctx(maker.log)
    assert run(token_of(ctx, sessions, maker)) == ACCESS
    assert run(token_of(ctx, sessions, maker)) == ACCESS
    assert maker.paths().count("/api/auth/session") == 1, "a valid token is read once"
    clock.t = claims(ACCESS)["exp"] - 500          # inside the 600 s margin: read again
    assert run(token_of(ctx, sessions, maker)) == ACCESS
    assert maker.paths().count("/api/auth/session") == 2
    # a new credential generation (a re-import) never takes the old token
    clock.t = NOW
    other = Ctx(maker.log)
    other.account = replace(other.account, credential_gen=2)
    run(token_of(other, sessions, maker))
    assert maker.paths().count("/api/auth/session") == 3


def test_the_token_sets_the_bearer_and_the_workspace_header():
    maker = Maker()
    ctx = Ctx(maker.log)

    async def go():
        async with Web(ctx.account, ctx.settings, Sessions(clock=Clock()), ctx, make_session=maker) as web:
            assert "Authorization" not in web.headers("/x")
            await web.access_token()
            await web.post_json("/backend-api/conversation/init", {})
            return web.headers("/backend-api/x")
    h = run(go())
    assert h["Authorization"] == "Bearer " + ACCESS and h["ChatGPT-Account-Id"] == ACCOUNT_ID
    assert h["OAI-Device-Id"] == "did-0000-0000-0001" and h["X-OpenAI-Target-Path"] == "/backend-api/x"
    assert "OAI-Client-Version" not in h and "OAI-Client-Build-Number" not in h
    init = maker.log[-1]
    assert init[0] == "POST" and init[2]["Authorization"] == "Bearer " + ACCESS


def test_claims_flattens_the_auth_namespace_and_never_raises():
    c = claims(access_token(plan="pro"))
    assert c["chatgpt_plan_type"] == "pro" and c["chatgpt_user_id"] == USER_ID and isinstance(c["exp"], int)
    assert claims("garbage") == {} and claims("a.!!!.c") == {}


def test_a_session_without_access_token_is_signed_out_and_a_401_forgets_the_token():
    sessions = Sessions(clock=Clock())
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body=fixture("auth_session_signed_out")["body"])}))
    ctx = Ctx(maker.log)
    with pytest.raises(SignedOut):
        run(token_of(ctx, sessions, maker))
    sessions.keep_token(ctx.account.id, ctx.account.credential_gen, ACCESS)
    maker.table[("GET", "/backend-api/me")] = Resp(401, text="{}")

    async def me():
        async with Web(ctx.account, ctx.settings, sessions, ctx, make_session=maker) as web:
            await web.access_token()
            await web.get_json("/backend-api/me")
    with pytest.raises(SignedOut):
        run(me())
    assert sessions.token(ctx.account.id, ctx.account.credential_gen) is None


# ---- proxy and redirects ----------------------------------------------------------------------------------------
@mutation(WEB_FILE, PROXIES_LINE, NO_PROXIES_LINE, expect="the session must use the account proxy")
def test_every_call_goes_through_the_account_proxy():
    maker = Maker()
    ctx = Ctx(maker.log)

    async def go():
        async with Web(ctx.account, ctx.settings, Sessions(clock=Clock()), ctx, make_session=maker) as web:
            await web.home()
            await web.access_token()
            await web.get_media("/backend-api/estuary/content?id=1", 100)
    run(go())
    url = proxy_url(PROXY)
    assert url.startswith("http://user:PLANTED-pw@10.0.0.9")
    assert len(maker.proxies) == 2, "the run's session and the media session"
    assert all(p == {"http": url, "https": url} for p in maker.proxies), "the session must use the account proxy"


def test_no_proxy_means_no_proxies_and_never_a_fallback():
    maker = Maker()
    ctx = Ctx(maker.log, proxy=None)
    run(token_of(ctx, Sessions(clock=Clock()), maker))
    assert maker.proxies == [None]
    # a proxy that fails is a failed call, never a direct retry
    maker = Maker(routes(**{"GET /api/auth/session": ProxyError("proxy refused")}))
    ctx = Ctx(maker.log)
    with pytest.raises(NetError) as e:
        run(token_of(ctx, Sessions(clock=Clock()), maker))
    assert e.value.proxy is True and len(maker.log) == 1 and len(maker.proxies) == 1


class Recording(FakeSession):
    """Records what each request asked of redirects instead of failing inside the call."""
    asked: list = []

    async def request(self, method, url, *, allow_redirects=True, **kw):
        Recording.asked.append(allow_redirects)
        return await super().request(method, url, allow_redirects=False, **kw)


@mutation(WEB_FILE, NO_REDIRECTS, REDIRECTS, expect="redirects must not be followed")
def test_no_call_follows_a_redirect():
    Recording.asked = []
    maker = Maker(session_cls=Recording)
    maker.table[("PUT", "up.oaiusercontent.com/blob")] = Resp(201)
    ctx = Ctx(maker.log)

    async def go():
        async with Web(ctx.account, ctx.settings, Sessions(clock=Clock()), ctx, make_session=maker) as web:
            await web.home()
            await web.access_token()
            await web.get_json("/backend-api/me")
            await web.post_json("/backend-api/conversation/init", {})
            async for _ in web.stream("/backend-api/conversation/init", {}, {}, 5):
                pass
            await web.put_blob("https://up.oaiusercontent.com/blob", b"x", "image/png")
            await web.get_media("https://chatgpt.com/backend-api/estuary/content?id=1", 100)
    run(go())
    assert len(Recording.asked) == 7
    assert Recording.asked == [False] * 7, "redirects must not be followed"


# ---- the page --------------------------------------------------------------------------------------------------
def test_home_reads_the_build_and_the_sdk_url_and_is_cached():
    clock = Clock()
    sessions = Sessions(clock=clock)
    maker = Maker()
    ctx = Ctx(maker.log)

    async def home():
        async with Web(ctx.account, ctx.settings, sessions, ctx, make_session=maker) as web:
            return await web.home()
    assert run(home()) == Page("ccbd58c87c3a77dcbef035020b701a5bc7c54b87",
                               "https://chatgpt.com/sentinel/20260810913b/sdk.js")
    run(home())
    assert maker.paths().count("/") == 1
    clock.t += settings().get("gpt.page_max_age_s") + 1
    run(home())
    assert maker.paths().count("/") == 2
    sessions = Sessions(clock=clock)
    maker.table[("GET", "/")] = Resp(text="<html>no build</html>")
    with pytest.raises(BadAnswer):
        run(home())


def test_uploads_and_media_go_only_to_their_hosts():
    maker = Maker()
    ctx = Ctx(maker.log)

    async def go(call):
        async with Web(ctx.account, ctx.settings, Sessions(clock=Clock()), ctx, make_session=maker) as web:
            await web.access_token()
            return await call(web)
    for url in ("http://up.oaiusercontent.com/b", "https://evil.com/b?x=.oaiusercontent.com",
                "https://oaiusercontent.com.evil.com/b"):
        with pytest.raises(BadAnswer):
            run(go(lambda w, u=url: w.put_blob(u, b"x", "image/png")))
    for url in ("https://evil.com/backend-api/estuary/content?id=1", "http://chatgpt.com/backend-api/estuary/content",
                "https://chatgpt.com/backend-api/files/1", "https://chatgpt.com.evil.com/backend-api/estuary/content"):
        with pytest.raises(BadAnswer):
            run(go(lambda w, u=url: w.get_media(u, 100)))
    assert maker.paths().count("/api/auth/session") == 7 and len(maker.log) == 7, "nothing went to a refused host"
    # media: the bearer, no cookie, at most max_bytes
    maker.table[("GET", "/backend-api/estuary/content")] = Resp(200, {"content-type": "image/png"},
                                                                content=b"P" * 50)
    status, mime, data = run(go(lambda w: w.get_media("/backend-api/estuary/content?id=1", 100)))
    assert (status, mime, data) == (200, "image/png", b"P" * 50)
    media = maker.sessions[-1].requests[-1]
    assert media.headers["Authorization"] == "Bearer " + ACCESS and media.cookies == {}
    with pytest.raises(BadAnswer):
        run(go(lambda w: w.get_media("/backend-api/estuary/content?id=1", 10)))


# ---- the session cookie write-back -----------------------------------------------------------------------------
@mutation(WEB_FILE, WRITE_BACK, NO_WRITE_BACK, expect="the re-set session cookie was not kept")
def test_a_re_set_session_cookie_is_written_back_merged():
    # Review Focus 3: the split .0/.1 comes back as a single .0 (the .1 deleted): the stored set is replaced whole
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body=session_body(), cookies={S0: NEW0, S1: ""})}))
    ctx = Ctx(maker.log)
    ctx.stored = parse_credential(json.dumps([*export(), {"domain": "chatgpt.com", "name": "other-run",
                                                          "value": "1"}]).encode()).blob
    run(token_of(ctx, Sessions(clock=Clock()), maker))
    assert len(ctx.renewed) == 1
    jar = jar_of(ctx.stored)
    assert session_parts(jar) == {S0: NEW0}, "the re-set session cookie was not kept"
    assert jar["other-run"] == "1", "the write-back merges into what is stored now (merge_blobs)"
    # nothing re-set: nothing written
    maker = Maker()
    ctx = Ctx(maker.log)
    run(token_of(ctx, Sessions(clock=Clock()), maker))
    assert ctx.renewed == []


def test_a_signed_out_run_writes_nothing_back():
    signed_out = Resp(json_body={}, cookies={S0: NEW0})
    maker = Maker(routes(**{"GET /api/auth/session": signed_out}))
    ctx = Ctx(maker.log)
    with pytest.raises(SignedOut):
        run(token_of(ctx, Sessions(clock=Clock()), maker))
    assert ctx.renewed == [] and "renew" not in ctx.log
    # a 401 later in the run also stops the write-back of a cookie re-set before it
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body=session_body(), cookies={S0: NEW0}),
                            "GET /backend-api/me": Resp(401, text="{}")}))
    ctx = Ctx(maker.log)

    async def go():
        async with Web(ctx.account, ctx.settings, Sessions(clock=Clock()), ctx, make_session=maker) as web:
            await web.access_token()
            await web.get_json("/backend-api/me")
    with pytest.raises(SignedOut):
        run(go())
    assert ctx.renewed == []


def test_a_write_back_that_fails_never_fails_the_run(caplog):
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body=session_body(), cookies={S0: NEW0})}))
    ctx = Ctx(maker.log, fail="renew")
    with caplog.at_level(logging.DEBUG):
        assert run(token_of(ctx, Sessions(clock=Clock()), maker)) == ACCESS
    assert "RuntimeError" in caplog.text
    assert_no_secret(caplog.text)


# ---- the account check ------------------------------------------------------------------------------------------
START = datetime.fromtimestamp(NOW, UTC)


def check(maker, ctx, sessions=None, now=START):
    return run(check_account(ctx, ctx.settings, sessions or Sessions(clock=Clock()), make_session=maker,
                             now=lambda: now))


@mutation(CHECK_FILE, PLUS_PRO, PLUS_FREE, expect="plus must be the pro tier")
@pytest.mark.parametrize("plan, tier", [("free", "free"), ("go", "free"), ("plus", "pro"), ("team", "pro"),
                                        ("enterprise", "pro"), ("edu", "pro"), ("pro", "ultra"), ("weird", "free"),
                                        (None, "free")])
def test_plan_maps_to_tier(plan, tier):
    assert tier_of(plan) == tier, "%s must be the %s tier" % (plan, tier)


def test_identity_is_user_slash_workspace():
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body=session_body(access_token(plan="team")))}))
    ctx = Ctx(maker.log)
    health = check(maker, ctx)
    assert health.state == "alive"
    assert health.identity == USER_ID + "/" + ACCOUNT_ID
    assert health.display_name == fixture("auth_session")["body"]["user"]["email"]
    assert Tier("pro") in health.effects and dict(health.state_patch) == {"plan": "team"}
    assert maker.paths() == ["/api/auth/session", "/backend-api/conversation/init"]
    # a cached token is not enough: the check always reads the session (it is what keeps the cookie rolling)
    sessions = Sessions(clock=Clock())
    sessions.keep_token(ctx.account.id, ctx.account.credential_gen, ACCESS)
    check(maker, ctx, sessions)
    assert maker.paths().count("/api/auth/session") == 2


@mutation(CHECK_FILE, ONLY_ZERO, ANY_REMAINING, expect="a non-zero remaining must report no balance")
def test_a_plus_remaining_of_120_reports_no_balance_and_zero_empties_the_pool():
    init = fixture("init_limits")["body"]
    assert image_balance(init) is None, "a non-zero remaining must report no balance"
    for row in init["limits_progress"]:
        if row["feature_name"] == "image_gen":
            row["remaining"] = 0
    assert image_balance(init) == Balance("image", 0, datetime(2026, 11, 5, 16, 14, 21, 130437, UTC))
    assert image_balance({}) is None and image_balance({"limits_progress": "x"}) is None
    maker = Maker(routes(**{"POST /backend-api/conversation/init": Resp(json_body=init)}))
    health = check(maker, Ctx(maker.log))
    assert any(isinstance(e, Balance) and e.left_n == 0 for e in health.effects)
    # an init that fails still checks the account alive, with no balance
    maker = Maker(routes(**{"POST /backend-api/conversation/init": Resp(500, text="oops")}))
    health = check(maker, Ctx(maker.log))
    assert health.state == "alive" and not any(isinstance(e, Balance) for e in health.effects)


def test_signed_out_check_is_a_spaced_strike_not_signed_out_state():
    now = datetime.fromtimestamp(NOW, UTC)
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body={})}))
    health = check(maker, Ctx(maker.log), now=now)
    assert health.state == "unknown" and health.identity is None
    suspect = next(e for e in health.effects if isinstance(e, Suspect))
    cool = next(e for e in health.effects if isinstance(e, Cooldown))
    assert suspect.min_gap_s == settings().get("gpt.signed_out_gap_s")
    assert cool.until == now + timedelta(seconds=settings().get("gpt.signed_out_rest_s")) and cool.recheck
    # a 401 on the init is the same doubt
    maker = Maker(routes(**{"POST /backend-api/conversation/init": Resp(401, text="{}")}))
    health = check(maker, Ctx(maker.log), now=now)
    assert health.state == "unknown" and any(isinstance(e, Suspect) for e in health.effects)


class ProxyError(Exception):
    """Named like curl_cffi's proxy failure; its text holds the proxy's login, as curl's does."""


def test_net_error_text_holds_no_proxy_login():
    leak = ProxyError("CONNECT via http://%s@10.0.0.9:3128 failed" % PROXY.auth)
    maker = Maker(routes(**{"GET /api/auth/session": leak}))
    ctx = Ctx(maker.log)
    with pytest.raises(NetError) as e:
        run(token_of(ctx, Sessions(clock=Clock()), maker))
    assert str(e.value) == "chatgpt.com could not be reached (ProxyError)" and e.value.kind == "ProxyError"
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert_no_secret(str(e.value))
    with pytest.raises(RuntimeError) as e:
        check(maker, Ctx(maker.log))
    assert "ProxyError" in str(e.value)
    assert_no_secret(str(e.value))


def test_nothing_secret_reaches_the_log(caplog):
    maker = Maker(routes(**{"GET /api/auth/session": Resp(json_body=session_body(access_token(plan="weird")),
                                                          cookies={S0: NEW0, S1: ""})}))
    ctx = Ctx(maker.log)
    with caplog.at_level(logging.DEBUG):
        health = check(maker, ctx)
    assert "weird" in caplog.text, "an unknown plan is logged by name"
    assert health.state == "alive" and len(ctx.renewed) == 1
    assert_no_secret(caplog.text, *ctx.events, health)
