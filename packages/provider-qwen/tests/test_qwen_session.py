"""The session (access token, proxy), the minted pair (when it is minted, how it is kept and dropped) and the account
check, over a fake chat.qwen.ai."""

import asyncio
import logging

import httpx
import pytest
from qwenkit import (
    ACCESS,
    BLOB,
    BX_UA,
    EMAIL,
    NOW,
    PAIR,
    REFRESH,
    UMID,
    Ctx,
    FakeMint,
    Qwen,
    minted_blob,
    minter,
    refresh_ok,
    settings,
)

from relay_core.providers import Balance, Cooldown, Health, Proxy, Suspect
from relay_core.testing.mutation import mutation
from relay_qwen.constants import TOKEN_REUSE_S
from relay_qwen.credential import cred_of, merge_blobs
from relay_qwen.engine import http as H
from relay_qwen.engine.check import check_account
from relay_qwen.engine.mint import Minter, MintError, browser_cookies, drop_pair, kept_headers, paused_action
from relay_qwen.engine.upload import oss_v4_headers

HTTP_FILE = "packages/provider-qwen/src/relay_qwen/engine/http.py"
MINT_FILE = "packages/provider-qwen/src/relay_qwen/engine/mint.py"
CHECK_FILE = "packages/provider-qwen/src/relay_qwen/engine/check.py"


async def tokens_of(sessions, ctx, q, clock):
    async with H.Site(ctx, sessions, transport=q.transport, clock=clock) as site:
        return await site.token()


# ---- the access token ----------------------------------------------------------------------------------------------
@mutation(HTTP_FILE, "or now - held[1].at >= TOKEN_REUSE_S:", "or False:", expect="an access token older than")
def test_an_access_token_is_reused_while_young_and_refreshed_after():
    log = []
    q, ctx, sessions = Qwen(log), Ctx(log), H.Sessions()
    assert asyncio.run(tokens_of(sessions, ctx, q, lambda: NOW)) == ACCESS
    asyncio.run(tokens_of(sessions, ctx, q, lambda: NOW + TOKEN_REUSE_S - 1))
    assert log.count("refresh") == 1, "a young token was refreshed again"
    asyncio.run(tokens_of(sessions, ctx, q, lambda: NOW + TOKEN_REUSE_S))
    assert log.count("refresh") == 2, "an access token older than TOKEN_REUSE_S was reused"
    other = Ctx(log, gen=2)              # a re-imported credential never reuses the old session's token
    asyncio.run(tokens_of(sessions, other, q, lambda: NOW + TOKEN_REUSE_S))
    assert log.count("refresh") == 3


def test_the_refresh_sends_the_jar_and_reads_signed_out_only_from_qwens_own_answer():
    log = []
    q = Qwen(log)
    asyncio.run(tokens_of(H.Sessions(), Ctx(log, blob=BLOB), q, lambda: NOW))
    (call,) = q.calls("refresh")
    assert "refresh_token=" + REFRESH in call.headers["cookie"] and call.headers["x-request-origin"]
    for answer, kind in ((httpx.Response(401), H.SignedOut), (httpx.Response(200, json={"success": False}),
                                                               H.SignedOut), (httpx.Response(502), H.BadAnswer),
                         (httpx.Response(200, json={"success": True, "data": {}}), H.BadAnswer)):
        with pytest.raises(kind):
            asyncio.run(tokens_of(H.Sessions(), Ctx([]), Qwen([], refresh=answer), lambda: NOW))


def rotated(value: str) -> dict:
    body = refresh_ok()
    body["data"]["refresh_token"] = value
    return body


def sent_refresh_token(request) -> str:
    return dict(p.strip().split("=", 1) for p in request.headers["cookie"].split(";"))["refresh_token"]


@mutation(HTTP_FILE, '            await self._keep_rotated(data.get("refresh_token"))\n', "",
          expect="the next refresh sent the old token")
def test_a_rotated_refresh_token_is_written_back_at_once_and_the_next_refresh_sends_it():
    log = []
    q, ctx, sessions = Qwen(log, refresh=rotated("rt-rotated-1")), Ctx(log), H.Sessions()
    asyncio.run(tokens_of(sessions, ctx, q, lambda: NOW))
    asyncio.run(tokens_of(sessions, ctx, q, lambda: NOW + TOKEN_REUSE_S))
    first, second = q.calls("refresh")
    assert sent_refresh_token(first) == REFRESH
    assert sent_refresh_token(second) == "rt-rotated-1", "the next refresh sent the old token"
    assert log == ["refresh", "renew", "refresh"], "the rotation was not written back before anything else: %s" % log
    assert cred_of(ctx.renewed[0]).cookies[(".qwen.ai", "/", "refresh_token")] == "rt-rotated-1"
    assert cred_of(ctx.renewed[0]).bx == PAIR, "the rotation lost the pair"


REUSE_HELD = ("            held = self.sessions.token(self.account, self.clock())\n"
              "            if held is not None:\n"
              "                self._token, self.user = held.value, held.user\n"
              "                return held.value\n"
              "            await self._refresh()\n")


@mutation(HTTP_FILE, REUSE_HELD, "            await self._refresh()\n", expect="one upstream refresh")
def test_runs_that_need_a_token_together_make_one_upstream_refresh():
    log = []
    q, sessions = Qwen(log), H.Sessions()

    async def both():
        return await asyncio.gather(tokens_of(sessions, Ctx(log), q, lambda: NOW),
                                    tokens_of(sessions, Ctx(log), q, lambda: NOW))
    assert asyncio.run(both()) == [ACCESS, ACCESS]
    assert log.count("refresh") == 1, "not one upstream refresh: %s" % log


@mutation(HTTP_FILE, "        sessions.keep(account, cred_of(stored))\n", "        sessions.keep(account, cred)\n",
          expect="a stale session token")
def test_a_rotation_that_lost_its_write_refreshes_next_with_what_was_stored():
    log = []
    q, ctx, sessions = Qwen(log, refresh=rotated("rt-mine")), Ctx(log), H.Sessions()
    other = cred_of(minted_blob(NOW - 30))           # another run wrote first: a newer mint and a later refresh
    other.cookies[(".qwen.ai", "/", "refresh_token")], other.refreshed_at = "rt-stored", NOW + 1
    ctx.stored_as = lambda mine: merge_blobs(other.blob(), mine)
    asyncio.run(tokens_of(sessions, ctx, q, lambda: NOW))
    held = sessions.current(ctx.account)
    assert (held.cookies[(".qwen.ai", "/", "refresh_token")], held.minted_at) == ("rt-stored", NOW - 30), \
        "the process kept a stale session token"
    later = Qwen(log, refresh=rotated("rt-stored"))
    asyncio.run(tokens_of(sessions, ctx, later, lambda: NOW + TOKEN_REUSE_S))
    assert sent_refresh_token(later.calls("refresh")[0]) == "rt-stored", "a stale session token was sent"


PROXY_LINE ="        inner = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(self.account.proxy), " \
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
    site = H.Site(Ctx([], proxy=Proxy("10.0.0.1", 8080, "us er:p@ss")), H.Sessions(), clock=lambda: NOW)

    async def open_and_close():
        async with site:
            assert site.client is not None
    asyncio.run(open_and_close())
    assert made == [{"proxy": "http://us%20er:p%40ss@10.0.0.1:8080", "trust_env": False}], \
        "the run was built without the account proxy: %s" % made
    assert site.client.follow_redirects is False and site.client._trust_env is False


# ---- the pair ------------------------------------------------------------------------------------------------------
@mutation(MINT_FILE, "    return bool(cred.bx) and now - cred.minted_at < max_age_s\n",
          "    return bool(cred.bx)\n", expect="a pair older than its age limit was reused")
def test_a_young_pair_is_reused_and_an_old_one_minted_again_and_written_back():
    log = []
    m = minter(log)
    young = Ctx(log, blob=minted_blob(NOW - 100))
    assert asyncio.run(m.fresh(young, H.Sessions(), 1500)).bx == PAIR and "mint" not in log
    old = Ctx(log, blob=minted_blob(NOW - 1500))
    sessions = H.Sessions()
    got = asyncio.run(m.fresh(old, sessions, 1500))
    assert log.count("mint") == 1, "a pair older than its age limit was reused"
    assert got.minted_at == NOW and old.merges == [merge_blobs] and cred_of(old.renewed[0]).minted_at == NOW
    assert sessions.current(old.account).minted_at == NOW, "the process does not hold the new pair"


def test_runs_of_one_account_that_need_a_pair_together_mint_once():
    log = []
    m, sessions, ctx = minter(log), H.Sessions(), Ctx(log, blob=BLOB)

    async def both():
        return await asyncio.gather(m.fresh(ctx, sessions, 1500), m.fresh(ctx, sessions, 1500))
    a, b = asyncio.run(both())
    assert log.count("mint") == 1 and a.bx == b.bx == PAIR


def test_a_mint_that_fails_raises_mint_error_with_no_value_and_releases_its_browser_token():
    log = []
    m = Minter(FakeMint(log, fail=RuntimeError("chrome died at http://user:pw@proxy " + REFRESH)), clock=lambda: NOW)
    for _ in range(2):          # a held token would make the second wait forever
        with pytest.raises(MintError) as e:
            asyncio.run(asyncio.wait_for(m.fresh(Ctx(log, blob=BLOB), H.Sessions(), 1500), 5))
        assert REFRESH not in str(e.value) and "pw" not in str(e.value)
    assert m._budget.busy == 0


@mutation(MINT_FILE, '    if any(fnmatch.fnmatchcase(url, p) for p in SESSION_ENDERS):\n        return "refuse"\n', "",
          expect="a mint let the page end the session")
def test_a_mint_captures_the_generate_and_never_lets_the_page_end_the_session():
    assert paused_action("https://chat.qwen.ai/api/v2/chat/completions?chat_id=x") == "capture"
    for url in ("https://auth.qwen.ai/api/v2/auths/signout", "https://auth.qwen.ai/api/v2/users/logout/u1"):
        assert paused_action(url) == "refuse", "a mint let the page end the session: %s" % url
    assert paused_action("https://auth.qwen.ai/api/v2/auths/refresh") == "continue"


KEEP_RENEWED = ("                    await write_back(ctx, sessions, e.cred)      # the page renewed the session: "
                "keep it\n")


@mutation(MINT_FILE, KEEP_RENEWED, "                    pass\n", expect="the renewed session of a failed mint was lost")
def test_a_failed_mint_still_writes_back_the_session_the_page_renewed_and_keeps_the_old_pair():
    log = []
    renewed = cred_of(minted_blob(NOW - 5000))
    renewed.cookies[(".qwen.ai", "/", "refresh_token")] = "rt-renewed-by-the-page"

    async def mint(account, cred, token):
        raise MintError("the page sent no generate in 30 s", renewed)
    ctx = Ctx(log, blob=minted_blob(NOW - 5000))
    with pytest.raises(MintError):
        asyncio.run(Minter(mint, clock=lambda: NOW).fresh(ctx, H.Sessions(), 1500))
    assert ctx.renewed, "the renewed session of a failed mint was lost"
    kept = cred_of(ctx.renewed[-1])
    assert kept.cookies[(".qwen.ai", "/", "refresh_token")] == "rt-renewed-by-the-page" and kept.bx == PAIR


def test_a_drop_forgets_only_the_pair_the_waf_refused():
    log = []
    ctx, sessions = Ctx(log, blob=minted_blob(NOW - 10)), H.Sessions()
    asyncio.run(drop_pair(ctx, sessions, NOW - 999))         # an older pair: the current one stays
    assert "renew" not in log and sessions.current(ctx.account).bx == PAIR
    asyncio.run(drop_pair(ctx, sessions, NOW - 10))
    assert cred_of(ctx.renewed[-1]).bx is None and sessions.current(ctx.account).bx is None


def test_the_kept_headers_need_the_pair_and_take_the_browsers_agent_where_the_request_lacks_it():
    raw = {"bx-ua": BX_UA, "BX-UMIDTOKEN": UMID, "bx-v": "2.5.37", "Version": "0.3.22", "authorization": "Bearer x",
           "cookie": "a=b"}
    agent = {"user-agent": "Mozilla/5.0 Chrome/154", "sec-ch-ua": '"Chromium";v="154"', "sec-ch-ua-mobile": "?0",
             "sec-ch-ua-platform": '"Linux"'}
    kept = kept_headers(raw, agent)
    assert kept == {"bx-ua": BX_UA, "bx-umidtoken": UMID, "bx-v": "2.5.37", "version": "0.3.22", **agent}
    with pytest.raises(MintError):
        kept_headers({"bx-ua": BX_UA}, agent)
    found = [{"name": "cna", "value": "1", "domain": ".qwen.ai", "path": "/"},
             {"name": "x", "value": "2", "domain": ".alicdn.com", "path": "/"}]
    assert browser_cookies(found) == {(".qwen.ai", "/", "cna"): "1"}


def test_an_oss_v4_signature_is_stable_and_scoped_to_the_region():
    from datetime import UTC, datetime
    kw = dict(method="PUT", bucket="b", key="u/f 1.jpg", region="ap-southeast-1", access_key_id="AK",
              access_key_secret="SK", security_token="T", content_type="image/jpeg",
              now=datetime(2026, 10, 23, 7, 0, tzinfo=UTC))
    one, two = oss_v4_headers(**kw), oss_v4_headers(**kw)
    assert one == two and one["x-oss-date"] == "20261023T070000Z" and one["x-oss-security-token"] == "T"
    assert one["Authorization"].startswith("OSS4-HMAC-SHA256 Credential=AK/20261023/ap-southeast-1/oss/"
                                           "aliyun_v4_request,Signature=")
    assert one != oss_v4_headers(**{**kw, "access_key_secret": "SK2"})


# ---- the account check ---------------------------------------------------------------------------------------------
def run_check(q, ctx, m=None, **cfg):
    return asyncio.run(check_account(ctx, settings(**cfg), H.Sessions(), m or minter(ctx.log),
                                     transport=q.transport, clock=lambda: NOW))


def test_a_signed_in_account_is_alive_by_its_email_with_its_credits_as_the_video_balance():
    log = []
    health = run_check(Qwen(log), Ctx(log))
    assert health == Health("alive", identity=EMAIL, display_name=EMAIL, effects=(Balance("video", 10),))
    assert "mint" not in log and "send" not in log


@mutation(CHECK_FILE, '            if settings.get("qwen.claims_enabled"):\n', "            if True:\n",
          expect="a drained provider started a browser")
def test_the_check_mints_at_half_the_pair_age_only_while_qwen_claims():
    half_old = minted_blob(NOW - 800)          # bx_max_age_s 1500: older than half of it
    log = []
    run_check(Qwen(log), Ctx(log, blob=half_old))
    assert "mint" not in log, "a drained provider started a browser: %s" % log
    log = []
    health = run_check(Qwen(log), Ctx(log, blob=half_old), qwen__claims_enabled=True)
    assert log.count("mint") == 1 and health.state == "alive"
    log = []
    health = run_check(Qwen(log), Ctx(log, blob=BLOB), Minter(FakeMint(log, fail=RuntimeError("x"))),
                       qwen__claims_enabled=True)
    assert health.state == "alive", "a failed mint is no reason to doubt the account"


def test_a_signed_out_refresh_is_a_spaced_strike_another_answer_unknown_and_no_network_a_failed_check():
    health = run_check(Qwen([], refresh=httpx.Response(401)), Ctx([]))
    assert health.state == "unknown" and any(isinstance(e, Suspect) and e.min_gap_s > 0 for e in health.effects)
    assert any(isinstance(e, Cooldown) and e.recheck for e in health.effects)
    assert run_check(Qwen([], refresh=httpx.Response(502)), Ctx([])) == Health("unknown")
    no_credits = run_check(Qwen([], credits=httpx.Response(500)), Ctx([]))
    assert no_credits.state == "alive" and no_credits.effects == ()
    with pytest.raises(RuntimeError) as e:
        run_check(Qwen([], refresh=httpx.ConnectError("http://user:pw-7@10.0.0.1:1 refused")), Ctx([]))
    assert "pw-7" not in str(e.value)


def test_the_check_never_logs_a_session_value(caplog):
    caplog.set_level(logging.DEBUG)
    log = []
    run_check(Qwen(log), Ctx(log, blob=BLOB), qwen__claims_enabled=True)
    for secret in (REFRESH, ACCESS, BX_UA, UMID):
        assert secret not in caplog.text
