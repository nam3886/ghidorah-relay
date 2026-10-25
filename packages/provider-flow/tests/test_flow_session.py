"""Session keeping and the account check over a fake flow.google.com (no network): the Google session is rotated
when due, written back sealed at once, merged per account in the process, never rotated twice for one due time, and
every call goes through the account's proxy."""

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from flowkit import (
    AT_TOKEN,
    BAD_ARG,
    BLOB,
    CREDITS_50,
    EMAIL,
    FIX,
    NEW_PSIDTS,
    PROJECT,
    PSID,
    PSIDTS,
    Ctx,
    Flow,
    args_of,
    frame,
    settings,
)

from relay_core.providers import Balance, Cooldown, Dead, Proxy, Suspect, Tier
from relay_core.testing.mutation import mutation
from relay_flow.credential import dump, jar_of
from relay_flow.engine import FlowEngine, build_engine
from relay_flow.engine import http as H
from relay_flow.engine.captcha import CaptchaError, Tokens
from relay_flow.engine.check import check_account, month_before
from relay_flow.engine.http import Sessions, Site

HTTP_FILE = "packages/provider-flow/src/relay_flow/engine/http.py"
CHECK_FILE = "packages/provider-flow/src/relay_flow/engine/check.py"
ENGINE_FILE = "packages/provider-flow/src/relay_flow/engine/__init__.py"
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
WALL = 1_792_737_600.0


def cookies(blob: bytes) -> dict:
    found, _ = jar_of(blob)
    return {(c["domain"], c["name"]): c["value"] for c in found}


def check(flow, ctx, sessions=None, wall=WALL, cfg=None):
    return asyncio.run(check_account(ctx, cfg or settings(), sessions or Sessions(), transport=flow.transport,
                                     now=lambda: NOW, clock=lambda: wall))


# ---- rotation -----------------------------------------------------------------------------------------------------
DUE = '                if self.clock() - jar.rotated_at >= int(self.settings.get("flow.rotate_interval_s")):'


@mutation(HTTP_FILE, DUE, "                if False:", expect="the session was not rotated")
@mutation(HTTP_FILE, "        await self._write_back(kept)", "        pass", expect="not written back before the page")
def test_a_due_session_is_rotated_first_and_the_rotated_jar_is_written_back_at_once():
    log = []
    flow, ctx = Flow(log), Ctx(log)
    health = check(flow, ctx)
    assert health.state == "alive"
    assert log[0] == "rotate", "the session was not rotated: %s" % log
    assert log[1] == "renew", "the rotated jar was not written back before the page load: %s" % log
    rotated = cookies(ctx.renewed[0])
    assert rotated[(".google.com", "__Secure-1PSIDTS")] == NEW_PSIDTS and jar_of(ctx.renewed[0])[1] == WALL
    assert rotated[(".google.com", "__Secure-1PSID")] == PSID and rotated[("flow.google.com", "OSID")] == "osid-planted"
    rotate = next(r for r in flow.seen if r.url.host == "accounts.google.com")
    assert rotate.content == b'[000,"-0000000000000000000"]' and "__Secure-1PSID=" in rotate.headers["cookie"]
    assert "OSID" not in rotate.headers["cookie"], "a host-only flow cookie went to accounts.google.com"
    # the page load refreshed SIDCC: written back when the run ends, on top of the rotated jar
    final = cookies(ctx.renewed[-1])
    assert final[(".google.com", "SIDCC")] == "sidcc-from-page" and final[(".google.com", "__Secure-1PSIDTS")] \
        == NEW_PSIDTS


MERGE_ORDER = "    old, new = (a, b) if a.rotated_at < b.rotated_at else (b, a)"


@mutation(HTTP_FILE, MERGE_ORDER, "    old, new = (b, a) if a.rotated_at < b.rotated_at else (a, b)",
          expect="the older rotation won")
@mutation(HTTP_FILE, "            stored = await self.ctx.renew_credential(jar.blob(), merge_blobs)",
          "            stored = await self.ctx.renew_credential(jar.blob())", expect="no merge was given")
def test_a_renewal_that_lost_to_another_run_merges_cookie_by_cookie_the_later_rotation_winning():
    log = []
    ctx = Ctx(log)
    check(Flow(log), ctx)
    assert ctx.merges and all(m is H.merge_blobs for m in ctx.merges), "no merge was given: %s" % ctx.merges

    def blob(at, **values):
        return dump([{"name": n, "value": v, "domain": ".google.com", "path": "/"} for n, v in values.items()], at)

    stored = blob(200.0, PSIDTS="newer", SIDCC="stored")            # another run rotated later
    mine = blob(100.0, PSIDTS="older", SIDCC="mine", NID="only-mine")
    got = cookies(H.merge_blobs(stored, mine))
    assert got[(".google.com", "PSIDTS")] == "newer", "the older rotation won: %s" % got
    assert got[(".google.com", "SIDCC")] == "stored" and got[(".google.com", "NID")] == "only-mine"
    assert jar_of(H.merge_blobs(stored, mine))[1] == 200.0
    assert cookies(H.merge_blobs(mine, stored))[(".google.com", "PSIDTS")] == "newer", "the older rotation won"


@mutation(HTTP_FILE, "                self.sessions.keep(self.account, Jar.of(stored))", "                pass",
          expect="the run went on with its pre-merge jar")
def test_after_a_merge_the_process_holds_exactly_what_was_stored_and_a_later_write_keeps_the_newer_rotation():
    """Run B rotates while run A (another process) rotated later: the core stores merge(A's, B's) = A's PSIDTS. B's
    page load then refreshes SIDCC and B writes again: it must build on what was stored, never on its own copy."""
    log, sessions = [], Sessions()
    a_jar = dump([{"name": "__Secure-1PSIDTS", "value": "sidts-FROM-RUN-A", "domain": ".google.com", "path": "/"}],
                 WALL + 30)
    ctx = Ctx(log)
    ctx.stored_as = lambda mine: H.merge_blobs(a_jar, mine)
    check(Flow(log), ctx, sessions)
    assert len(ctx.renewed) == 2, "a rotation write and the page-load write: %s" % log
    last = cookies(ctx.renewed[-1])
    assert last[(".google.com", "__Secure-1PSIDTS")] == "sidts-FROM-RUN-A", \
        "the run went on with its pre-merge jar: its later write brought back %s" % last[
            (".google.com", "__Secure-1PSIDTS")]
    held = sessions.current(ctx.account)
    assert cookies(held.blob())[(".google.com", "__Secure-1PSIDTS")] == "sidts-FROM-RUN-A"
    assert held.rotated_at == WALL + 30


def test_a_session_rotated_recently_is_not_rotated_again():
    log = []
    fresh = json.dumps({**json.loads(BLOB), "rotated_at": WALL - 300}).encode()
    check(Flow(log), Ctx(log, blob=fresh))
    assert "rotate" not in log


HELD = "        if held is not None and held[0] == account.credential_gen and held[1].rotated_at >= loaded.rotated_at:"


@mutation(HTTP_FILE, HELD, "        if False:", expect="rotated twice")
def test_the_process_keeps_the_rotated_jar_so_the_next_run_of_the_account_does_not_rotate_it_again():
    log, sessions = [], Sessions()
    check(Flow(log), Ctx(log), sessions)
    log.clear()
    flow, ctx = Flow(log), Ctx(log)          # loaded from the database before the write landed: the old jar
    check(flow, ctx, sessions, wall=WALL + 60)
    assert "rotate" not in log, "rotated twice for one due time: %s" % log
    page = next(r for r in flow.seen if r.method == "GET")
    assert "__Secure-1PSIDTS=%s" % NEW_PSIDTS in page.headers["cookie"], "the next run used the old session"
    log.clear()
    check(Flow(log), Ctx(log, gen=2), sessions, wall=WALL + 120)    # re-imported: its own jar, rotated anew
    assert log[0] == "rotate"


def test_a_renewal_superseded_by_a_reimport_drops_the_held_jar_and_a_failed_rotation_changes_nothing():
    log, sessions = [], Sessions()
    check(Flow(log), Ctx(log, renew_ok=False), sessions)
    assert "acc-1" not in sessions._jars
    log = []
    ctx = Ctx(log)
    health = check(Flow(log, rotate=401), ctx)
    assert log[0] == "rotate" and health.state == "alive"
    assert all((".google.com", "__Secure-1PSIDTS") not in cookies(b) or cookies(b)[(".google.com", "__Secure-1PSIDTS")]
               == PSIDTS for b in ctx.renewed)


def test_concurrent_runs_of_one_account_rotate_once():
    log, sessions = [], Sessions()

    async def both():
        ctxs = [Ctx(log), Ctx(log)]
        return await asyncio.gather(*(check_account(c, settings(), sessions, transport=Flow(log).transport,
                                                    now=lambda: NOW, clock=lambda: WALL) for c in ctxs))
    asyncio.run(both())
    assert log.count("rotate") == 1


PROXY_LINE = "        inner = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(self.account.proxy), " \
             "trust_env=False)"


@mutation(HTTP_FILE, PROXY_LINE, "        inner = self.transport or httpx.AsyncHTTPTransport(trust_env=False)",
          expect="without the account proxy")
def test_every_call_of_a_run_goes_through_the_account_proxy(monkeypatch):
    made = []

    class Spy(httpx.AsyncHTTPTransport):
        def __init__(self, **kw):
            made.append(kw)
            super().__init__(**kw)

    monkeypatch.setattr(H.httpx, "AsyncHTTPTransport", Spy)
    ctx = Ctx([], proxy=Proxy("10.0.0.1", 8080, "us er:p@ss"))
    site = Site(ctx, Sessions(), settings(), clock=lambda: 0.0)

    async def open_and_close():
        async with site:
            assert site.client._transport is not None
    asyncio.run(open_and_close())
    assert made == [{"proxy": "http://us%20er:p%40ss@10.0.0.1:8080", "trust_env": False}], \
        "the run was built without the account proxy: %s" % made
    assert site.client.follow_redirects is False and site.client._trust_env is False


# ---- the account check ---------------------------------------------------------------------------------------------
@mutation(CHECK_FILE, '    return Health("alive", identity=page.email,', '    return Health("alive", identity=None,',
          expect="the identity is the email")
def test_a_signed_in_page_is_alive_with_the_email_and_an_account_without_a_project_gets_one():
    log = []
    flow = Flow(log, rpc={"jHPbke": frame("jHPbke", [PROJECT, ["relay"]])})
    health = check(flow, Ctx(log, state={}))
    assert health.identity == EMAIL, "the identity is the email"
    assert (health.state, health.display_name, health.state_patch) == ("alive", EMAIL, {"project": PROJECT})
    assert args_of(flow.calls("jHPbke")[0]) == ["projects/*", [None, ["relay"]], [None, 22]]
    log = []
    flow = Flow(log)
    assert check(flow, Ctx(log)).state_patch == {} and not flow.calls("jHPbke")


STRIKE = '                return Health("unknown", effects=doubt_effects(signed_out_why(site), now()))'


@mutation(CHECK_FILE, STRIKE, '                return Health("signed_out", effects=doubt_effects(signed_out_why(site), '
          'now()))', expect="one signed-out read must be a strike")
@mutation(CHECK_FILE, "            if not page.signed_in:", "            if False:", expect="a strike")
def test_a_signed_out_page_is_a_spaced_strike_and_a_rest_never_a_dead_account():
    log = []
    health = check(Flow(log, signed_in=False, rotate=401), Ctx(log))
    assert health.state == "unknown", "one signed-out read must be a strike, not a verdict: %s" % health.state
    assert [type(e) for e in health.effects] == [Suspect, Cooldown], "a strike: %s" % (health.effects,)
    strike, rest = health.effects
    assert strike.min_gap_s == 540 and "RotateCookies answered 401" in strike.reason
    assert rest.until == NOW + timedelta(seconds=600) and rest.recheck
    assert not any(isinstance(e, Dead) for e in health.effects)


def test_another_answer_is_unknown_with_no_strike_and_a_network_failure_fails_the_check_quietly():
    log = []
    for status in (500, 302, 429):
        health = check(Flow(log, page_status=status), Ctx(log))
        assert (health.state, health.effects) == ("unknown", ()), status
    flow = Flow(log)
    flow.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(
        httpx.ConnectError("connect to http://user:pw-123@10.0.0.1:8080 failed")))
    with pytest.raises(RuntimeError) as caught:
        check(flow, Ctx(log))
    assert str(caught.value) == "flow could not be reached (ConnectError)"


def test_the_session_and_the_page_token_never_reach_a_log(caplog):
    log = []
    with caplog.at_level(logging.DEBUG):
        check(Flow(log), Ctx(log, renew_ok=False))
        flow = Flow(log)
        ctx = Ctx(log)

        async def failing(blob, merge=None):
            raise RuntimeError("database down")
        ctx.renew_credential = failing
        check(flow, ctx)
    assert "was not written back (RuntimeError)" in caplog.text
    for secret in (PSID, PSIDTS, NEW_PSIDTS, AT_TOKEN, "osid-planted"):
        assert secret not in caplog.text


# ---- the token source ----------------------------------------------------------------------------------------------
YES_KEY = "yes-PLANTED-client-key-0123"


def yescaptcha(answers):
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        out = answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return httpx.Response(200, json=out)
    transport = httpx.MockTransport(handle)
    transport.seen = seen
    return transport


async def no_sleep(s):
    pass


def tokens(transport, key=YES_KEY, cfg=None):
    t = Tokens(cfg or settings(), key, transport=transport, sleep=no_sleep)
    return asyncio.run(t("IMAGE_GENERATION", "https://flow.google.com/project/" + PROJECT))


@mutation("packages/provider-flow/src/relay_flow/engine/captcha.py", '"pageAction": action, "isEnterprise": True}',
          '"pageAction": action}', expect="enterprise")
def test_yescaptcha_mints_an_enterprise_token_for_the_generate_action():
    transport = yescaptcha([{"errorId": 0, "taskId": "t-1"}, {"errorId": 0, "status": "processing"},
                            {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "tok-abc"}}])
    assert tokens(transport) == "tok-abc"
    created = transport.seen[0]
    assert created["task"] == {"type": "RecaptchaV3TaskProxylessM1", "isEnterprise": True,
                               "websiteURL": "https://flow.google.com/project/" + PROJECT,
                               "websiteKey": "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV",
                               "pageAction": "IMAGE_GENERATION"}, "enterprise"
    assert created["clientKey"] == YES_KEY and transport.seen[2] == {"clientKey": YES_KEY, "taskId": "t-1"}


def test_the_token_source_fails_with_a_reason_that_never_holds_the_key():
    cases = [
        ([{"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST"}], "refused the task (ERROR_KEY_DOES_NOT_EXIST)"),
        ([{"errorId": 0, "taskId": "t"}, {"errorId": 1, "errorCode": "ERROR_CAPTCHA_UNSOLVABLE"}], "failed the task"),
        ([{"errorId": 0, "taskId": "t"}, {"errorId": 0, "status": "ready", "solution": {}}], "no token"),
        ([httpx.ConnectError("connect https://api.yescaptcha.com %s" % YES_KEY)], "could not be reached"),
    ]
    for answers, reason in cases:
        with pytest.raises(CaptchaError) as caught:
            tokens(yescaptcha(answers))
        assert reason in str(caught.value) and YES_KEY not in str(caught.value), caught.value
    with pytest.raises(CaptchaError, match="FLOW_YESCAPTCHA_KEY is not set"):
        tokens(yescaptcha([]), key=None)
    with pytest.raises(CaptchaError, match="no token source"):
        tokens(yescaptcha([]), cfg=settings(flow__captcha_provider="other"))
    assert YES_KEY not in repr(Tokens(settings(), YES_KEY))


CAPTCHA_PROXY = ("        transport = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(proxy), "
                 "trust_env=False)")


@mutation("packages/provider-flow/src/relay_flow/engine/captcha.py", CAPTCHA_PROXY,
          "        transport = self.transport or httpx.AsyncHTTPTransport(trust_env=False)",
          expect="the solver was called without the account proxy")
def test_the_solver_is_called_through_the_accounts_proxy_and_direct_only_without_one(monkeypatch):
    """The owner's ruling (23/10): YesCaptcha's API too goes through the account proxy."""
    made, seen = [], []

    class Spy(httpx.AsyncBaseTransport):
        def __init__(self, **kw):
            made.append(kw)

        async def handle_async_request(self, request):
            seen.append(request.url.host)
            body = {"errorId": 0, "taskId": "t"} if request.url.path == "/createTask" else {
                "errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "tok"}}
            return httpx.Response(200, json=body)

    monkeypatch.setattr("relay_flow.engine.captcha.httpx.AsyncHTTPTransport", Spy)
    t = Tokens(settings(), YES_KEY, sleep=no_sleep)
    page = "https://flow.google.com/project/" + PROJECT
    assert asyncio.run(t("IMAGE_GENERATION", page, Proxy("10.0.0.1", 8080, "us er:p@ss"))) == "tok"
    assert made == [{"proxy": "http://us%20er:p%40ss@10.0.0.1:8080", "trust_env": False}], \
        "the solver was called without the account proxy: %s" % made
    assert seen == ["api.yescaptcha.com", "api.yescaptcha.com"]
    made.clear()
    assert asyncio.run(t("IMAGE_GENERATION", page, None)) == "tok"
    assert made == [{"proxy": None, "trust_env": False}]


@mutation(ENGINE_FILE, "(env.pop(YESCAPTCHA_KEY_VAR, None)", "(env.get(YESCAPTCHA_KEY_VAR, None)",
          expect="the key stayed in the environment")
def test_the_engine_takes_the_key_out_of_the_environment():
    env = {"FLOW_YESCAPTCHA_KEY": " %s \n" % YES_KEY, "OTHER": "1"}
    engine = build_engine(None, settings(), env)
    assert isinstance(engine, FlowEngine) and engine.tokens._key == YES_KEY
    assert env == {"OTHER": "1"}, "the key stayed in the environment"
    assert build_engine(None, settings(), {}).tokens._key is None


# ---- the account's credits and plan ---------------------------------------------------------------------------------
PRO_CREDITS = json.loads((FIX / "live-261025" / "credits_pro.json").read_text(encoding="utf-8"))["payload"]
NOV_8 = datetime(2026, 11, 8, 6, 42, 6, tzinfo=UTC)          # the AI Pro account's refill stamp (1794120126)


def read_by_check(answer):
    log = []
    health = check(Flow(log, rpc={"nzlxg": answer}), Ctx(log))
    assert health.state == "alive" and "nzlxg" in log, "the check did not read the credits"
    return health.effects


@mutation(CHECK_FILE, 'PAYGATE_TIERS = {1: "pro", 2: "ultra", 3: "free"}', 'PAYGATE_TIERS = {2: "ultra", 3: "free"}',
          expect="AI Pro")
@mutation(CHECK_FILE, "                          effects=await read_account(site, now()))",
          "                          )",
          expect="did not read the credits")
def test_an_ai_pro_account_reports_its_credits_its_monthly_refill_and_the_pro_tier():
    effects = read_by_check(frame("nzlxg", PRO_CREDITS))
    assert Tier("pro", date(2026, 10, 8)) in effects, "the AI Pro account was not read as AI Pro: %s" % (effects,)
    assert Balance("video", 1050, NOV_8) in effects and len(effects) == 2


def test_a_free_account_reports_its_daily_credits_and_the_free_tier_with_no_cycle():
    effects = read_by_check(frame("nzlxg", CREDITS_50["payload"]))
    refill = datetime.fromtimestamp(CREDITS_50["payload"][6][0], UTC)
    assert effects == (Balance("video", 50, refill), Tier("free"))


def test_an_ultra_account_is_read_as_ultra_from_flows_tier_enum():
    ultra = [12500, 2, 2, 2, None, 12500, [1795000000, 0]]          # 2 = PAYGATE_TIER_TWO (no Ultra account recorded)
    refill = datetime.fromtimestamp(1795000000, UTC)
    assert read_by_check(frame("nzlxg", ultra)) == (Balance("video", 12500, refill), Tier("ultra", date(2026, 10, 18)))


@mutation(CHECK_FILE, "    return account_read(reply.payload, now) if reply.status == 200 else ()",
          "    return account_read(reply.payload, now)", expect="a failed read")
def test_a_credit_read_that_fails_keeps_the_account_alive_and_changes_neither_tier_nor_balance():
    for answer in (httpx.Response(500, text=frame("nzlxg", PRO_CREDITS)), frame("nzlxg", None),
                   frame("nzlxg", ["x"]), httpx.ReadTimeout("slow"), BAD_ARG):
        assert read_by_check(answer) == (), "a failed read changed the account: %r" % (answer,)
    # a plan code that flow's enum does not name as free, pro or ultra: the balance counts, the stored tier stays
    assert read_by_check(frame("nzlxg", [80, 4, 8, 1, None, 80, None])) == (Balance("video", 80),)


@mutation(CHECK_FILE, '        renewal = believed_refill(refill, now, 0) if tier != "free" else None',
          '        renewal = refill if tier != "free" else None', expect="a garbled stamp named a cycle")
def test_a_garbled_refill_stamp_never_fails_the_check_nor_names_a_cycle():
    for stamp, refill in (([10**20, 0], None), ([-5, 0], None), (["soon"], None),
                          ([1893456000, 0], datetime(2030, 1, 1, tzinfo=UTC)),       # years ahead: the core drops it
                          ([1792000000, 0], datetime.fromtimestamp(1792000000, UTC))):  # days in the past
        effects = read_by_check(frame("nzlxg", [1050, 1, 2, 2, None, 1050, stamp]))
        assert effects == (Balance("video", 1050, refill), Tier("pro")), "a garbled stamp named a cycle: %s" % (
            effects,)


def test_a_cycle_start_is_a_month_before_the_refill_clamped_to_the_months_length():
    assert month_before(date(2026, 3, 31)) == date(2026, 2, 28)
    assert month_before(date(2026, 1, 8)) == date(2025, 12, 8)
