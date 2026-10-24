"""Session keeping and the account check over a fake gemini.google.com (no network): the Google session is rotated
when due, written back sealed at once, merged per account in the process, never rotated twice for one due time, and
every call goes through the account's proxy."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from geminikit import (
    AT_TOKEN,
    BLOB,
    EMAIL,
    MODE_FLASH_LITE,
    NEW_PSIDTS,
    PRO_MODES,
    PSID,
    PSIDTS,
    Ctx,
    Gemini,
    blob_for,
    settings,
)

from relay_core.providers import Cooldown, Dead, Proxy, Suspect
from relay_core.testing.mutation import mutation
from relay_gemini.credential import dump, email_of, jar_of
from relay_gemini.engine import GeminiEngine, build_engine
from relay_gemini.engine import http as H
from relay_gemini.engine.check import check_account
from relay_gemini.engine.http import Sessions, Site

HTTP_FILE = "packages/provider-gemini/src/relay_gemini/engine/http.py"
CHECK_FILE = "packages/provider-gemini/src/relay_gemini/engine/check.py"
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
WALL = 1_792_737_600.0


def cookies(blob: bytes) -> dict:
    found, _ = jar_of(blob)
    return {(c["domain"], c["name"]): c["value"] for c in found}


def check(fake, ctx, sessions=None, wall=WALL, cfg=None):
    return asyncio.run(check_account(ctx, cfg or settings(), sessions or Sessions(), transport=fake.transport,
                                     now=lambda: NOW, clock=lambda: wall))


# ---- rotation -----------------------------------------------------------------------------------------------------
DUE = '                if self.clock() - jar.rotated_at >= int(self.settings.get("gemini.rotate_interval_s")):'


@mutation(HTTP_FILE, DUE, "                if False:", expect="the session was not rotated")
@mutation(HTTP_FILE, "        await self._write_back(kept)", "        pass", expect="not written back before the page")
def test_a_due_session_is_rotated_first_and_the_rotated_jar_is_written_back_at_once():
    log = []
    fake, ctx = Gemini(log), Ctx(log)
    health = check(fake, ctx)
    assert health.state == "alive"
    assert log[0] == "rotate", "the session was not rotated: %s" % log
    assert log[1] == "renew", "the rotated jar was not written back before the page load: %s" % log
    rotated = cookies(ctx.renewed[0])
    assert rotated[(".google.com", "__Secure-1PSIDTS")] == NEW_PSIDTS and jar_of(ctx.renewed[0])[1] == WALL
    assert rotated[(".google.com", "__Secure-1PSID")] == PSID
    assert rotated[(".gemini.google.com", "COMPASS")] == "compass-planted"
    rotate = next(r for r in fake.seen if r.url.host == "accounts.google.com")
    assert rotate.content == b'[000,"-0000000000000000000"]' and "__Secure-1PSID=" in rotate.headers["cookie"]
    assert "COMPASS" not in rotate.headers["cookie"], "a .gemini.google.com cookie went to accounts.google.com"
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
    check(Gemini(log), ctx)
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
    check(Gemini(log), ctx, sessions)
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
    check(Gemini(log), Ctx(log, blob=fresh))
    assert "rotate" not in log


HELD = "        if held is not None and held[0] == account.credential_gen and held[1].rotated_at >= loaded.rotated_at:"


@mutation(HTTP_FILE, HELD, "        if False:", expect="rotated twice")
def test_the_process_keeps_the_rotated_jar_so_the_next_run_of_the_account_does_not_rotate_it_again():
    log, sessions = [], Sessions()
    check(Gemini(log), Ctx(log), sessions)
    log.clear()
    fake, ctx = Gemini(log), Ctx(log)          # loaded from the database before the write landed: the old jar
    check(fake, ctx, sessions, wall=WALL + 60)
    assert "rotate" not in log, "rotated twice for one due time: %s" % log
    page = next(r for r in fake.seen if r.method == "GET")
    assert "__Secure-1PSIDTS=%s" % NEW_PSIDTS in page.headers["cookie"], "the next run used the old session"
    log.clear()
    check(Gemini(log), Ctx(log, gen=2), sessions, wall=WALL + 120)    # re-imported: its own jar, rotated anew
    assert log[0] == "rotate"


def test_a_renewal_superseded_by_a_reimport_drops_the_held_jar_and_a_failed_rotation_changes_nothing():
    log, sessions = [], Sessions()
    check(Gemini(log), Ctx(log, renew_ok=False), sessions)
    assert "acc-1" not in sessions._jars
    log = []
    ctx = Ctx(log)
    health = check(Gemini(log, rotate=401), ctx)
    assert log[0] == "rotate" and health.state == "alive"
    assert all((".google.com", "__Secure-1PSIDTS") not in cookies(b) or cookies(b)[(".google.com", "__Secure-1PSIDTS")]
               == PSIDTS for b in ctx.renewed)


def test_concurrent_runs_of_one_account_rotate_once():
    log, sessions = [], Sessions()

    async def both():
        ctxs = [Ctx(log), Ctx(log)]
        return await asyncio.gather(*(check_account(c, settings(), sessions, transport=Gemini(log).transport,
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
def test_a_signed_in_page_is_alive_with_the_email_and_the_check_sends_nothing():
    log = []
    health = check(Gemini(log), Ctx(log))
    assert health.identity == EMAIL, "the identity is the email"
    assert (health.state, health.display_name) == ("alive", EMAIL)
    assert health.state_patch == {"authuser": 0, "mode": "cf41b0e0dd7d53e5"}     # no email named: /u/0, its mode
    assert "SEND" not in log and not any(s.startswith("upload") for s in log)


FIND = "            if (page.email or \"\").lower() == email:\n                return n\n"
LIST_END = "            if not page.signed_in:\n                return None\n"
MISMATCH = '            if site.email and (page.email or "").lower() != site.email:'


@mutation(CHECK_FILE, MISMATCH, "            if False:", expect="the account named by email was not looked for")
@mutation(HTTP_FILE, FIND, "", expect="the account named by email was not looked for")
@mutation(CHECK_FILE, '            patch = {"authuser": site.authuser,', '            patch = {"authuser": 0,',
          expect="the index found was not kept")
@mutation(CHECK_FILE, "                page = site.page\n", "", expect="the identity is the account found")
def test_the_check_finds_the_index_of_the_account_the_credential_names_and_keeps_it_with_its_mode():
    log = []
    fake = Gemini(log, accounts=["a@example.com", "b@example.com", "Owner.Pro@example.com", "c@example.com"],
                  rpc={"otAQ7b": PRO_MODES})
    health = check(fake, Ctx(log, blob=blob_for("owner.pro@example.com")))
    assert health.state == "alive", "the account named by email was not looked for: %s" % (health,)
    assert health.state_patch.get("authuser") == 2, \
        "the account named by email was not looked for, or the index found was not kept: %s" % (health.state_patch,)
    assert health.state_patch["mode"] == MODE_FLASH_LITE
    assert health.identity == "Owner.Pro@example.com", "the identity is the account found: %s" % health.identity
    pages = [r.url.path for r in fake.seen if r.method == "GET" and r.url.host == "gemini.google.com"]
    assert pages == ["/app", "/app", "/u/1/app", "/u/2/app"], pages
    mode_read = next(r for r in fake.seen if "otAQ7b" in str(r.url))
    assert mode_read.url.path.startswith("/u/2/"), "the mode was read on another account"
    # resolved already: one page load at that index, nothing looked through
    log = []
    fake = Gemini(log, accounts=["a@example.com", "b@example.com", "owner.pro@example.com"], rpc={"otAQ7b": PRO_MODES})
    health = check(fake, Ctx(log, blob=blob_for("owner.pro@example.com"), state={"authuser": 2}))
    assert health.state_patch["authuser"] == 2 and log.count("page") == 1


@mutation(HTTP_FILE, LIST_END, "", expect="past the last account")
def test_an_email_no_account_of_the_session_has_is_a_rest_and_a_recheck_never_a_strike():
    log = []
    fake = Gemini(log, accounts=["a@example.com", "b@example.com"])
    health = check(fake, Ctx(log, blob=blob_for("owner.pro@example.com")))
    assert health.state == "unknown" and [type(e) for e in health.effects] == [Cooldown], health
    assert health.effects[0].recheck and health.effects[0].until == NOW + timedelta(seconds=600)
    assert "owner.pro" not in health.effects[0].reason
    pages = [r.url.path for r in fake.seen if r.method == "GET"]
    assert pages == ["/app", "/app", "/u/1/app", "/u/2/app"], "looked past the last account: %s" % pages
    assert "SEND" not in log


@mutation(HTTP_FILE, "            if not jar.email:\n                self.authuser = 0",
          "            if False:\n                self.authuser = 0", expect="not /u/0")
def test_a_credential_with_no_email_goes_to_u0_whatever_an_older_check_kept():
    log = []
    fake = Gemini(log, accounts=["a@example.com", "b@example.com"])
    health = check(fake, Ctx(log, state={"authuser": 1, "mode": "cf41b0e0dd7d53e5"}))
    assert health.state_patch["authuser"] == 0 and health.identity == "a@example.com", "not /u/0: %s" % (health,)
    assert [r.url.path for r in fake.seen if r.method == "GET"] == ["/app"]


@mutation(HTTP_FILE, "            self._before, self.email = jar, jar.email\n",
          "            self._before, self.email = jar, None\n", expect="the email was dropped")
@mutation(HTTP_FILE, "    return Jar({**old.cookies, **new.cookies}, new.rotated_at, a.email).blob()",
          "    return Jar({**old.cookies, **new.cookies}, new.rotated_at).blob()", expect="the email was dropped")
def test_the_named_account_survives_every_rotation_and_merge_of_the_session():
    log = []
    ctx = Ctx(log, blob=blob_for("owner.pro@example.com"))
    ctx.stored_as = lambda mine: H.merge_blobs(blob_for("owner.pro@example.com"), mine)
    check(Gemini(log, accounts=["owner.pro@example.com"]), ctx)
    assert ctx.renewed and all(email_of(b) == "owner.pro@example.com" for b in ctx.renewed), \
        "the email was dropped from a written-back jar"
    assert email_of(H.merge_blobs(blob_for("owner.pro@example.com"), BLOB)) == "owner.pro@example.com", \
        "the email was dropped by a merge"
    site = Site(Ctx([], blob=blob_for("owner.pro@example.com"), state={"authuser": 3}), Sessions(), settings(),
                clock=lambda: 0.0)

    async def enter():
        async with site:
            return site.email, site.authuser
    assert asyncio.run(enter()) == ("owner.pro@example.com", 3), "the email was dropped on entering a run"


def test_the_engine_is_built_with_the_settings_and_one_session_store():
    engine = build_engine(None, settings())
    assert isinstance(engine, GeminiEngine) and isinstance(engine.sessions, Sessions)


STRIKE = '                return Health("unknown", effects=doubt_effects(signed_out_why(site), now()))'


@mutation(CHECK_FILE, STRIKE, '                return Health("signed_out", effects=doubt_effects(signed_out_why(site), '
          'now()))', expect="one signed-out read must be a strike")
@mutation(CHECK_FILE, "            if not page.signed_in:", "            if False:", expect="a strike")
def test_a_signed_out_page_is_a_spaced_strike_and_a_rest_never_a_dead_account():
    log = []
    health = check(Gemini(log, signed_in=False, rotate=401), Ctx(log))
    assert health.state == "unknown", "one signed-out read must be a strike, not a verdict: %s" % health.state
    assert [type(e) for e in health.effects] == [Suspect, Cooldown], "a strike: %s" % (health.effects,)
    strike, rest = health.effects
    assert strike.min_gap_s == 540 and "RotateCookies answered 401" in strike.reason
    assert rest.until == NOW + timedelta(seconds=600) and rest.recheck
    assert not any(isinstance(e, Dead) for e in health.effects)


def test_another_answer_is_unknown_with_no_strike_and_a_network_failure_fails_the_check_quietly():
    log = []
    for status in (500, 302, 429):
        health = check(Gemini(log, page_status=status), Ctx(log))
        assert (health.state, health.effects) == ("unknown", ()), status
    fake = Gemini(log)
    fake.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(
        httpx.ConnectError("connect to http://user:pw-123@10.0.0.1:8080 failed")))
    with pytest.raises(RuntimeError) as caught:
        check(fake, Ctx(log))
    assert str(caught.value) == "gemini could not be reached (ConnectError)"


def test_the_session_and_the_page_token_never_reach_a_log(caplog):
    log = []
    with caplog.at_level(logging.DEBUG):
        check(Gemini(log), Ctx(log, renew_ok=False))
        fake = Gemini(log)
        ctx = Ctx(log)

        async def failing(blob, merge=None):
            raise RuntimeError("database down")
        ctx.renew_credential = failing
        check(fake, ctx)
    assert "was not written back (RuntimeError)" in caplog.text
    for secret in (PSID, PSIDTS, NEW_PSIDTS, AT_TOKEN, "compass-planted"):
        assert secret not in caplog.text


