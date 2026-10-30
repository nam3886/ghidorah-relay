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
from relay_flow.constants import CAPTCHA_KEY_SETTINGS, USER_AGENT
from relay_flow.credential import dump, jar_of
from relay_flow.engine import FlowEngine, build_engine
from relay_flow.engine import http as H
from relay_flow.engine.captcha import CaptchaError, Tokens
from relay_flow.engine.check import check_account, month_before
from relay_flow.engine.http import Sessions, Site

HTTP_FILE = "packages/provider-flow/src/relay_flow/engine/http.py"
CHECK_FILE = "packages/provider-flow/src/relay_flow/engine/check.py"
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
CAPTCHA_FILE = "packages/provider-flow/src/relay_flow/engine/captcha.py"
YES_KEY = "yes-PLANTED-client-key-0123"
CAP_KEY = "CAP-PLANTED-client-key-4567"
ANTI_KEY = "anti-PLANTED-client-key-89ab"
KEYS = {"yescaptcha": YES_KEY, "capsolver": CAP_KEY, "anticaptcha": ANTI_KEY}
PAGE = "https://flow.google.com/project/" + PROJECT
SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
ACCOUNT_PROXY = Proxy("10.0.0.1", 8080, "us er:p@ss")
READY = {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "tok-abc"}}
CREATED = {"errorId": 0, "taskId": "t"}
WORKING = {"errorId": 0, "status": "processing"}


def solver(answers):
    """A fake solver API: each answer is a JSON body, an `httpx.Response` or an exception, taken in order."""
    seen = []

    def handle(request):
        seen.append((request.url.host, request.url.path, json.loads(request.content)))
        out = answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)
    transport = httpx.MockTransport(handle)
    transport.seen = seen
    return transport


async def no_sleep(s):
    pass


class FakeTime:
    """A clock that only the token source's own waits move (and a request, by `advance`)."""

    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self) -> float:
        return self.t

    async def sleep(self, s):
        self.sleeps.append((self.t, s))
        self.t += s


def keyed(cfg, keys=None):
    """The settings view with the solver keys as the worker's opened secret settings ("" = not set)."""
    keys = KEYS if keys is None else keys
    for source, key in CAPTCHA_KEY_SETTINGS.items():
        cfg.set(key, keys.get(source) or "")
    return cfg


def tokens(transport, keys=None, cfg=None, proxy=None, time=None, source="yescaptcha"):
    cfg = keyed(cfg or settings(flow__captcha_provider=source), keys)
    timing = {"sleep": time.sleep, "clock": time.now} if time else {"sleep": no_sleep}
    t = Tokens(cfg, transport=transport, **timing)
    return asyncio.run(t("IMAGE_GENERATION", PAGE, proxy))


def task_of(transport) -> dict:
    host, path, body = transport.seen[0]
    assert path == "/createTask"
    return body["task"]


NEVER_READY = [CREATED] + [WORKING] * 40      # more reads than the token's 120 s allow, 3 s apart


@mutation(CAPTCHA_FILE, '"type": YESCAPTCHA_TASK, **base, "isEnterprise": True, "userAgent": USER_AGENT}',
          '"type": YESCAPTCHA_TASK, **base, "userAgent": USER_AGENT}', expect="enterprise")
@mutation(CAPTCHA_FILE, '"type": YESCAPTCHA_TASK, **base, "isEnterprise": True, "userAgent": USER_AGENT}',
          '"type": YESCAPTCHA_TASK, **base, "isEnterprise": True}', expect="enterprise")
def test_yescaptcha_mints_an_enterprise_token_for_the_generate_action_and_our_browser():
    transport = solver([{"errorId": 0, "taskId": "t-1"}, WORKING, READY])
    assert tokens(transport, proxy=ACCOUNT_PROXY) == "tok-abc"
    assert task_of(transport) == {"type": "RecaptchaV3TaskProxylessM1", "isEnterprise": True, "websiteURL": PAGE,
                                  "websiteKey": SITE_KEY, "pageAction": "IMAGE_GENERATION",
                                  "userAgent": USER_AGENT}, "enterprise"
    assert [h for h, _, _ in transport.seen] == ["api.yescaptcha.com"] * 3
    assert transport.seen[0][2]["clientKey"] == YES_KEY
    assert transport.seen[2][1:] == ("/getTaskResult", {"clientKey": YES_KEY, "taskId": "t-1"})


@mutation(CAPTCHA_FILE, 'if proxy is not None and self.settings.get("flow.capsolver_proxy_task"):',
          "if False:", expect="the proxy task")
def test_capsolver_solves_the_enterprise_task_through_the_accounts_proxy_and_proxyless_only_by_choice():
    """docs.capsolver.com ReCaptchaV3: `ReCaptchaV3EnterpriseTask` with `proxy` "http:ip:port:user:pass"."""
    transport = solver([{"errorId": 0, "taskId": "c-1"}, READY])
    assert tokens(transport, proxy=ACCOUNT_PROXY, source="capsolver") == "tok-abc"
    assert task_of(transport) == {"type": "ReCaptchaV3EnterpriseTask", "websiteURL": PAGE, "websiteKey": SITE_KEY,
                                  "pageAction": "IMAGE_GENERATION", "proxy": "http:10.0.0.1:8080:us er:p@ss"}, \
        "the proxy task was not used: %s" % task_of(transport)["type"]
    assert [h for h, _, _ in transport.seen] == ["api.capsolver.com"] * 2
    assert transport.seen[0][2]["clientKey"] == CAP_KEY
    assert transport.seen[1][1:] == ("/getTaskResult", {"clientKey": CAP_KEY, "taskId": "c-1"})
    transport = solver([CREATED, READY])
    tokens(transport, proxy=Proxy("10.0.0.2", 3128), source="capsolver")
    assert task_of(transport)["proxy"] == "http:10.0.0.2:3128", "a proxy with no login is host and port only"
    proxyless = {"type": "ReCaptchaV3EnterpriseTaskProxyLess", "websiteURL": PAGE, "websiteKey": SITE_KEY,
                 "pageAction": "IMAGE_GENERATION"}
    off = settings(flow__captcha_provider="capsolver", flow__capsolver_proxy_task=False)
    for proxy, cfg in ((ACCOUNT_PROXY, off), (None, None)):
        transport = solver([CREATED, READY])
        assert tokens(transport, proxy=proxy, cfg=cfg, source="capsolver") == "tok-abc"
        assert task_of(transport) == proxyless


@mutation(CAPTCHA_FILE, '"minScore": self.settings.get("flow.anticaptcha_min_score")}', "}", expect="minScore")
def test_anticaptcha_solves_the_v3_task_with_the_enterprise_flag_and_the_score_queue():
    """anti-captcha.com/apidoc RecaptchaV3Enterprise: `RecaptchaV3TaskProxyless` + isEnterprise, minScore required."""
    transport = solver([{"errorId": 0, "taskId": 77}, WORKING, READY])
    assert tokens(transport, proxy=ACCOUNT_PROXY, source="anticaptcha") == "tok-abc"
    assert task_of(transport) == {"type": "RecaptchaV3TaskProxyless", "isEnterprise": True, "minScore": 0.9,
                                  "websiteURL": PAGE, "websiteKey": SITE_KEY, "pageAction": "IMAGE_GENERATION"}, \
        "minScore: %s" % task_of(transport)
    assert [h for h, _, _ in transport.seen] == ["api.anti-captcha.com"] * 3
    assert transport.seen[2][1:] == ("/getTaskResult", {"clientKey": ANTI_KEY, "taskId": 77})
    transport = solver([CREATED, READY])
    tokens(transport, cfg=settings(flow__captcha_provider="anticaptcha", flow__anticaptcha_min_score=0.3))
    assert task_of(transport)["minScore"] == 0.3


SOLVER_FAULTS = [   # (source, answers, our code, a part of the reason, the clock)
    ("yescaptcha", [{"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST"}], "flow.captcha_key_invalid",
     "refused the task (ERROR_KEY_DOES_NOT_EXIST)", None),
    ("anticaptcha", [{"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST"}], "flow.captcha_key_invalid",
     "ERROR_KEY_DOES_NOT_EXIST", None),
    ("capsolver", [{"errorId": 1, "errorCode": "ERROR_KEY_DENIED_ACCESS"}], "flow.captcha_key_invalid",
     "ERROR_KEY_DENIED_ACCESS", None),
    ("capsolver", [httpx.Response(401, text="Unauthorized")], "flow.captcha_key_invalid", "HTTP 401", None),
    ("yescaptcha", [{"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"}], "flow.captcha_no_balance",
     "ERROR_ZERO_BALANCE", None),
    ("anticaptcha", [{"errorId": 10, "errorCode": "ERROR_ZERO_BALANCE"}], "flow.captcha_no_balance",
     "ERROR_ZERO_BALANCE", None),
    ("capsolver", [httpx.Response(400, json={"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"})],
     "flow.captcha_no_balance", "ERROR_ZERO_BALANCE", None),
    ("capsolver", [CREATED, {"errorId": 1, "errorCode": "ERROR_TASK_TIMEOUT"}], "flow.captcha_timeout",
     "ERROR_TASK_TIMEOUT", None),
    ("anticaptcha", [CREATED, {"errorId": 30, "errorCode": "ERROR_RECAPTCHA_TIMEOUT"}], "flow.captcha_timeout",
     "ERROR_RECAPTCHA_TIMEOUT", None),
    ("capsolver", list(NEVER_READY), "flow.captcha_timeout", "no token in 120 s", FakeTime),
    ("yescaptcha", [httpx.ReadTimeout("read timed out")], "flow.captcha_timeout", "did not answer in time", None),
    ("yescaptcha", [CREATED, {"errorId": 1, "errorCode": "ERROR_CAPTCHA_UNSOLVABLE"}], "flow.captcha_failed",
     "failed the task (ERROR_CAPTCHA_UNSOLVABLE)", None),
    ("capsolver", [CREATED, {"errorId": 0, "status": "failed"}], "flow.captcha_failed", "failed the task", None),
    ("capsolver", [CREATED, {"errorId": 1, "errorCode": "ERROR_PROXY_BANNED"}], "flow.captcha_failed",
     "ERROR_PROXY_BANNED", None),
    ("yescaptcha", [CREATED, {"errorId": 0, "status": "ready", "solution": {}}], "flow.captcha_failed",
     "ready with no token", None),
    ("anticaptcha", [CREATED, {"errorId": 0, "status": "ready", "solution": "tok-abc"}], "flow.captcha_failed",
     "ready with no token", None),
    ("anticaptcha", [httpx.Response(502, text="<html>bad gateway</html>")], "flow.captcha_failed",
     "not a JSON object (HTTP 502)", None),
    ("capsolver", [httpx.ConnectError("connect http://us er:p@ss@10.0.0.1 %s %s" % (CAP_KEY, YES_KEY))],
     "flow.captcha_failed", "could not be reached (ConnectError)", None),
]


@mutation(CAPTCHA_FILE, '    "ERROR_ZERO_BALANCE": errors.CAPTCHA_NO_BALANCE,\n', "", expect="ERROR_ZERO_BALANCE")
@mutation(CAPTCHA_FILE, '    "ERROR_KEY_DENIED_ACCESS": errors.CAPTCHA_KEY_INVALID,\n', "",
          expect="ERROR_KEY_DENIED_ACCESS")
@mutation(CAPTCHA_FILE, 'raise CaptchaError("no token in %d s" % timeout, errors.CAPTCHA_TIMEOUT)',
          'raise CaptchaError("no token in %d s" % timeout)', expect="no token in 120 s")
@mutation(CAPTCHA_FILE, 'raise CaptchaError("the solver refused its key (HTTP 401)", errors.CAPTCHA_KEY_INVALID)',
          'raise CaptchaError("the solver refused its key (HTTP 401)")', expect="HTTP 401")
@mutation(CAPTCHA_FILE, "        except httpx.TimeoutException as e:", "        except ZeroDivisionError as e:",
          expect="did not answer in time")
def test_each_solver_fault_has_its_own_code_and_its_reason_never_holds_a_secret(caplog):
    """A bad key, no balance and no token in time each have a code; none of them blames the flow account (the submit
    turns every `CaptchaError` into a Retry of its code)."""
    caplog.set_level(logging.DEBUG)
    for source, answers, code, reason, clock in SOLVER_FAULTS:
        with pytest.raises(CaptchaError) as caught:
            tokens(solver(answers), source=source, proxy=ACCOUNT_PROXY, time=clock and clock())
        text = str(caught.value)
        assert caught.value.code == code, "%s: %s, not %s (%s)" % (source, caught.value.code, code, reason)
        assert reason in text, text
        for secret in (YES_KEY, CAP_KEY, ANTI_KEY, "p@ss", "tok-abc"):
            assert secret not in text, text
    with pytest.raises(CaptchaError, match=r"flow.capsolver_key is not set \(Settings, flow\)") as caught:
        tokens(solver([]), keys={"yescaptcha": YES_KEY}, source="capsolver")
    assert caught.value.code == "flow.captcha_key_invalid"
    with pytest.raises(CaptchaError, match="no token source"):
        tokens(solver([]), source="other")
    assert repr(Tokens(keyed(settings()))) == "Tokens()"
    for secret in (YES_KEY, CAP_KEY, ANTI_KEY, "p@ss"):
        assert secret not in caplog.text


@mutation(CAPTCHA_FILE, "        for source in order:\n", "        for source in order[:1]:\n", expect="no fallback")
@mutation(CAPTCHA_FILE, 'token = token_of(solution) if isinstance(solution, dict) else None',
          'token = token_of(solution or {})', expect="has no attribute 'get'")
def test_an_order_of_sources_falls_back_to_the_next_and_names_every_reason_when_all_fail():
    cfg = settings(flow__captcha_provider="capsolver,yescaptcha")
    transport = solver([{"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"}, CREATED, READY])
    try:
        assert tokens(transport, cfg=cfg) == "tok-abc"
    except CaptchaError as e:
        pytest.fail("no fallback: %s" % e)
    assert [h for h, _, _ in transport.seen] == ["api.capsolver.com", "api.yescaptcha.com", "api.yescaptcha.com"]
    with pytest.raises(CaptchaError) as caught:
        tokens(solver([{"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST"}]), cfg=cfg,
               keys={"yescaptcha": YES_KEY})
    assert str(caught.value) == ("capsolver: flow.capsolver_key is not set (Settings, flow); "
                                 "yescaptcha: the solver refused the task (ERROR_KEY_DOES_NOT_EXIST)")
    assert caught.value.code == "flow.captcha_key_invalid"
    # a ready answer whose solution is not an object is a failed source like any other: the next one is tried
    transport = solver([CREATED, {"errorId": 0, "status": "ready", "solution": ["x"]}, CREATED, READY])
    assert tokens(transport, cfg=cfg) == "tok-abc"
    # the token's time is shared by the order: once spent, the next source is not started
    transport = solver(list(NEVER_READY))
    with pytest.raises(CaptchaError) as caught:
        tokens(transport, cfg=cfg, time=FakeTime())
    assert caught.value.code == "flow.captcha_timeout"
    assert {h for h, _, _ in transport.seen} == {"api.capsolver.com"}, "the next source started with no time left"


def timed_solver(time: FakeTime, answers):
    """A fake solver API whose answers are (seconds the call takes, JSON body); it records each call's host, the time
    it was sent and the read timeout it was given."""
    seen = []

    def handle(request):
        took, body = answers.pop(0)
        seen.append((request.url.host, time.t, request.extensions["timeout"]["read"]))
        time.t += took
        return httpx.Response(200, json=body)
    transport = httpx.MockTransport(handle)
    transport.seen = seen
    return transport


@mutation(CAPTCHA_FILE, "await self.sleep(min(poll, wait))", "await self.sleep(poll)",
          expect="a wait past the deadline")
@mutation(CAPTCHA_FILE, "timeout=max(0.5, min(API_TIMEOUT_S, left))", "timeout=API_TIMEOUT_S",
          expect="a call past the deadline")
def test_every_wait_and_every_call_ends_by_the_tokens_deadline():
    """Codex review 25/10: a poll's sleep and a call's timeout are cut to the time left, so one token never takes longer
    than `flow.captcha_timeout_s` (the submit bound counts on it)."""
    time = FakeTime()
    cfg = settings(flow__captcha_provider="yescaptcha", flow__captcha_timeout_s=30, flow__captcha_poll_s=7)
    transport = timed_solver(time, [(1, CREATED)] + [(1, WORKING)] * 3)
    with pytest.raises(CaptchaError) as caught:
        tokens(transport, cfg=cfg, time=time)
    assert caught.value.code == "flow.captcha_timeout"
    assert [s for _, s in time.sleeps] == [7, 7, 7, 5], "a wait past the deadline: %s" % time.sleeps
    assert [round(read) for _, _, read in transport.seen] == [30, 22, 14, 6], \
        "a call past the deadline: %s" % transport.seen
    assert time.t == 30


@mutation(CAPTCHA_FILE, "if failed and end - self.clock() < CAPTCHA_MIN_LEFT_S:",
          "if failed and end - self.clock() < 0:", expect="started with too little time")
def test_the_next_source_is_not_started_with_less_than_five_seconds_left():
    cfg = settings(flow__captcha_provider="capsolver,yescaptcha", flow__captcha_timeout_s=30)
    time = FakeTime()
    transport = timed_solver(time, [(26, {"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"}), (0, CREATED), (0, READY)])
    try:
        code = tokens(transport, cfg=cfg, time=time)
    except CaptchaError as e:
        code = e.code
    assert [h for h, _, _ in transport.seen] == ["api.capsolver.com"], "started with too little time: %s" % (
        transport.seen,)
    assert code == "flow.captcha_no_balance"
    time = FakeTime()
    transport = timed_solver(time, [(24, {"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE"}), (0, CREATED), (0, READY)])
    assert tokens(transport, cfg=cfg, time=time) == "tok-abc"
    assert [h for h, _, _ in transport.seen] == ["api.capsolver.com", "api.yescaptcha.com", "api.yescaptcha.com"]
    assert time.sleeps == [(24, 3)]      # 6 s were left: one read, 3 s on


CAPTCHA_PROXY = ("        transport = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(proxy), "
                 "trust_env=False)")


@mutation(CAPTCHA_FILE, CAPTCHA_PROXY,
          "        transport = self.transport or httpx.AsyncHTTPTransport(trust_env=False)",
          expect="the solver was called without the account proxy")
def test_every_solver_is_called_through_the_accounts_proxy_and_direct_only_without_one(monkeypatch):
    """The owner's ruling (23/10): every solver's API too goes through the account proxy, never direct beside it."""
    made, seen = [], []

    class Spy(httpx.AsyncBaseTransport):
        def __init__(self, **kw):
            made.append(kw)

        async def handle_async_request(self, request):
            seen.append(request.url.host)
            return httpx.Response(200, json=CREATED if request.url.path == "/createTask" else READY)

    monkeypatch.setattr("relay_flow.engine.captcha.httpx.AsyncHTTPTransport", Spy)
    hosts = {"yescaptcha": "api.yescaptcha.com", "capsolver": "api.capsolver.com",
             "anticaptcha": "api.anti-captcha.com"}
    for source, host in hosts.items():
        made.clear()
        seen.clear()
        t = Tokens(keyed(settings(flow__captcha_provider=source)), sleep=no_sleep)
        assert asyncio.run(t("IMAGE_GENERATION", PAGE, ACCOUNT_PROXY)) == "tok-abc"
        assert made == [{"proxy": "http://us%20er:p%40ss@10.0.0.1:8080", "trust_env": False}], \
            "the solver was called without the account proxy: %s %s" % (source, made)
        assert seen == [host, host]
        made.clear()
        assert asyncio.run(t("IMAGE_GENERATION", PAGE, None)) == "tok-abc"
        assert made == [{"proxy": None, "trust_env": False}]
    made.clear()
    t = Tokens(keyed(settings(flow__captcha_provider=["capsolver", "anticaptcha"]), {"anticaptcha": ANTI_KEY}),
               sleep=no_sleep)
    assert asyncio.run(t("IMAGE_GENERATION", PAGE, ACCOUNT_PROXY)) == "tok-abc"
    assert made and all(m["proxy"] for m in made), "the solver was called without the account proxy: %s" % made


CAPTCHA_KEY_READ = "        key = self.settings.get(CAPTCHA_KEY_SETTINGS[source])"


@mutation(CAPTCHA_FILE, CAPTCHA_KEY_READ, '        key = self.settings.get("flow.yescaptcha_key")',
          expect="the capsolver key was not the one sent")
def test_each_solver_takes_its_own_key_from_the_settings_at_each_solve():
    """The keys are secret settings the worker's settings view opens at each reload: a key replaced in the admin is
    the one the next solve sends, with no restart."""
    cfg = keyed(settings(flow__captcha_provider="capsolver"))
    transport = solver([CREATED, READY, CREATED, READY])
    t = Tokens(cfg, transport=transport, sleep=no_sleep)
    assert asyncio.run(t("IMAGE_GENERATION", PAGE)) == "tok-abc"
    assert transport.seen[0][2]["clientKey"] == CAP_KEY, "the capsolver key was not the one sent"
    cfg.set("flow.capsolver_key", "CAP-REPLACED-in-the-admin-0000")
    assert asyncio.run(t("IMAGE_GENERATION", PAGE)) == "tok-abc"
    assert transport.seen[2][2]["clientKey"] == "CAP-REPLACED-in-the-admin-0000", "a replaced key was not read"


def test_no_env_file_or_worker_reads_the_solver_keys_any_more():
    """Owner 25/10: the keys live only in the sealed settings; `relay seed-secrets` stored the old env values once.
    No env template names them and the flow package never reads them."""
    old = {"FLOW_YESCAPTCHA_KEY", "FLOW_CAPSOLVER_KEY", "FLOW_ANTICAPTCHA_KEY"}
    deploy = FIX.parents[1] / "deploy"
    for path in deploy.glob("*.env.example"):
        assert not any(name in path.read_text(encoding="utf-8") for name in old), path.name
    src = FIX.parents[1] / "packages" / "provider-flow" / "src"
    for path in src.rglob("*.py"):
        assert not any(name in path.read_text(encoding="utf-8") for name in old), path
    assert isinstance(build_engine(None, keyed(settings())), FlowEngine)


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


# ---- the browser of every call ------------------------------------------------------------------------------------
CHROME_141 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
NAVIGATE_LINE = ("                                                                "
                 "**NAVIGATE, **browser_headers(USER_AGENT)})")
NAVIGATE_ONLY = "                                                                **NAVIGATE})"
SOLVER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/153.0.0.0 Safari/537.36")


@mutation(HTTP_FILE, "_BRAND_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))",
          "_BRAND_ORDERS = ((0, 1, 2),) * 6", expect="as Chrome sends it")
@mutation(HTTP_FILE, "_GREASE_CHARS[(major + 1) % 11]", "_GREASE_CHARS[major % 11]", expect="as Chrome sends it")
@mutation(HTTP_FILE, "_GREASE_VERSIONS[major % 3]", '"99"', expect="as Chrome sends it")
def test_the_client_hints_are_the_ones_chrome_sends_with_its_user_agent():
    """sec-ch-ua as real Chrome sent it (131 and 141, captured from those releases), built the same way for ours."""
    assert H.chrome_brands(141) == '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"', \
        "not as Chrome sends it"
    assert H.chrome_brands(131) == '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"', \
        "not as Chrome sends it"
    assert H.browser_headers(CHROME_141) == {
        "User-Agent": CHROME_141, "sec-ch-ua": H.chrome_brands(141), "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'}
    assert H.browser_headers(SOLVER_UA)["sec-ch-ua-platform"] == '"macOS"'
    firefox = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
    assert H.browser_headers(firefox) == {"User-Agent": firefox}, "only Chromium sends client hints"
    assert "Chrome/156.0.0.0" in USER_AGENT


@mutation(HTTP_FILE, '                trust_env=False, headers={"Accept-Language": "en-US,en;q=0.9"})',
          '                trust_env=False, headers={"Accept-Language": "en-US,en;q=0.9", "sec-ch-ua": "x"})',
          expect="other browser")
@mutation(HTTP_FILE, '"Accept": "*/*", **CORS, **browser_headers(user_agent or USER_AGENT)}',
          '"Accept": "*/*", **CORS, **browser_headers(USER_AGENT)}', expect="the solver's browser")
@mutation(HTTP_FILE, NAVIGATE_LINE, NAVIGATE_ONLY, expect="as one Chrome")
def test_every_call_of_an_account_goes_as_one_chrome_and_a_call_given_the_solvers_browser_as_that_one():
    log = []
    flow, ctx = Flow(log), Ctx(log)

    async def run():
        async with Site(ctx, Sessions(), settings(), transport=flow.transport, clock=lambda: WALL) as site:
            await site.load("/")
            await site.rpc("nzlxg", [])
            await site.rpc("nzlxg", [], user_agent=SOLVER_UA)
            await site.rpc("nzlxg", [], user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Firefox/140.0")
    asyncio.run(run())
    rotate, page, rpc, solver_rpc, other = flow.seen
    ours = H.browser_headers(USER_AGENT)
    for request in (rotate, page, rpc):
        assert {k: request.headers.get(k) for k in ours} == ours, "not as one Chrome: %s" % request.url
    assert {k: solver_rpc.headers.get(k) for k in H.browser_headers(SOLVER_UA)} == H.browser_headers(SOLVER_UA), \
        "the solver's browser was not used"
    assert other.headers["User-Agent"].endswith("Firefox/140.0") and "sec-ch-ua" not in other.headers, \
        "Chrome's client hints went with another browser"
    assert page.headers["Sec-Fetch-Mode"] == "navigate" and page.headers["Sec-Fetch-Dest"] == "document"
    for request in (rotate, rpc, solver_rpc):
        assert (request.headers["Sec-Fetch-Mode"], request.headers["Sec-Fetch-Site"]) == ("cors", "same-origin")


@mutation(CAPTCHA_FILE, "    if isinstance(ua, str) and _BROWSER_UA.fullmatch(ua):", "    if isinstance(ua, str):",
          expect="not a browser string")
@mutation(CAPTCHA_FILE, "        out.user_agent = ua", "        pass", expect="the solver's browser")
def test_the_token_carries_the_browser_the_solver_minted_it_in_and_only_a_plain_browser_string():
    ready = {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "tok-abc", "userAgent": SOLVER_UA}}
    token = tokens(solver([CREATED, ready]))
    assert token == "tok-abc" and token.user_agent == SOLVER_UA, "the solver's browser was dropped"
    for bad in ("Mozilla/5.0 (x)\r\nX-Evil: 1", "curl/8.0", "", 42, "Mozilla/5.0 (" + "x" * 500):
        ready["solution"]["userAgent"] = bad
        assert tokens(solver([CREATED, ready])).user_agent is None, "not a browser string: %r" % (bad,)
    assert tokens(solver([CREATED, READY])).user_agent is None
