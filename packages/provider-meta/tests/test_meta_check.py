"""The account check over HTTP: a page read, one EstabStream and no prompt. The page and the gateway are the fakes of
`metakit` (recorded pages, a scripted socket); nothing here opens a network."""

import asyncio
from urllib.parse import quote

import pytest
from curl_cffi import WebSocketClosed
from metakit import (
    CLOSE_4003,
    NOW,
    PROXY,
    TOKEN,
    VIEWER,
    Clock,
    Ctx,
    FakeSocket,
    Net,
    Resp,
    assert_no_secret,
    page,
    settings,
)

from relay_core.providers import Cooldown, Dead, Suspect
from relay_core.testing.mutation import mutation
from relay_meta.constants import GATEWAY_URL, SITE_URL
from relay_meta.credential import viewer_hash
from relay_meta.engine import build_engine
from relay_meta.engine import web as W
from relay_meta.engine.check import check_account
from relay_meta.engine.wire import ESTAB, estab_answer, frame, parse_frame
from relay_meta.spec import SPEC

CHECK_FILE = "packages/provider-meta/src/relay_meta/engine/check.py"
WEB_FILE = "packages/provider-meta/src/relay_meta/engine/web.py"
WIRE_FILE = "packages/provider-meta/src/relay_meta/engine/wire.py"
HOME = ("GET", SITE_URL + "/")
GATEWAY = ("WS", GATEWAY_URL)
ACCEPTED = frame(ESTAB, 0, b'{"code":200}')
REFUSED = frame(ESTAB, 0, b'{"code":401}')


def run_check(net, *, sessions=None, ctx=None, clock=None):
    ctx = ctx or Ctx()
    clock = clock or Clock()
    sessions = sessions or W.Sessions(clock)
    health = asyncio.run(check_account(ctx, settings(), sessions, make_session=net.make_session, now=clock.now))
    return health, sessions, ctx


def signed_in():
    return Resp(200, page("page_signed_in"))


def kinds(sock: FakeSocket) -> list[int]:
    return [f[0] for f in sock.sent]


@mutation(CHECK_FILE, "        identity = viewer_hash(viewer)\n", "        identity = None\n",
          expect="the viewer hash is the identity")
def test_a_signed_in_page_and_an_accepted_estab_is_alive_with_the_hashed_viewer_and_name():
    sock = FakeSocket([ACCEPTED])
    net = Net({HOME: signed_in(), GATEWAY: sock})
    health, sessions, ctx = run_check(net)
    assert health.state == "alive" and health.effects == ()
    assert health.identity == viewer_hash(VIEWER), "the viewer hash is the identity"
    assert health.state_patch == {"viewer": viewer_hash(VIEWER)} and health.display_name
    assert_no_secret(repr(health), health.state_patch, ctx.events)
    assert kinds(sock).count(0x0f) == 1 and kinds(sock).count(0x0d) == 0 and sock.closed
    assert sessions.token("acc-1", 1).token == TOKEN, "the token read is cached for the jobs"
    assert estab_answer(parse_frame(ACCEPTED)[2]) == "ok"


SIGNED_OUT_RETURN = '            return Health("unknown", effects=signed_out_effects(now()))'


@mutation(CHECK_FILE, SIGNED_OUT_RETURN, '            return Health("signed_out")',
          expect="one signed-out read must be a strike, not a verdict")
@mutation(WEB_FILE, "now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))",
          "now + timedelta(seconds=SIGNED_OUT_REST_S)))", expect="the rest did not ask for a recheck")
def test_one_signed_out_read_is_a_spaced_strike_and_a_rest_never_a_dead_account():
    net = Net({HOME: Resp(200, page("page_signed_out"))})
    health, _, _ = run_check(net)
    assert health.state == "unknown", "one signed-out read must be a strike, not a verdict: %s" % health.state
    assert [type(e) for e in health.effects] == [Suspect, Cooldown], health.effects
    strike, rest = health.effects
    assert strike.min_gap_s == 540 and (rest.until - NOW).total_seconds() == 600
    assert rest.recheck, "the rest did not ask for a recheck"
    assert not any(isinstance(e, Dead) for e in health.effects)
    assert net.methods() == ["GET"], "no socket after a signed-out page"


@mutation(CHECK_FILE, 'Vercel page, to be checked again", end, recheck=True),))',
          'Vercel page, to be checked again", end),))', expect="the rest must ask for a recheck")
def test_a_vercel_page_rests_the_account_only_without_a_token_and_never_strikes():
    # no token: the IP rests, so does the account, until the gate opens, with a recheck and no strike
    net = Net({HOME: Resp(429, page("page_vercel"))})
    health, sessions, _ = run_check(net)
    assert health.state == "unknown" and len(health.effects) == 1
    rest = health.effects[0]
    assert isinstance(rest, Cooldown) and rest.recheck, "the rest must ask for a recheck"
    assert (rest.until - NOW).total_seconds() == 600 == sessions.gate.until(W.ip_key(PROXY)) - sessions.clock()
    assert not any(isinstance(e, Suspect | Dead) for e in health.effects)
    # a held token: the Vercel page is read, the estab goes on with the held token
    for marked_first in (False, True):
        clock = Clock()
        sessions = W.Sessions(clock)
        sessions.keep("acc-1", 1, W.read_page(200, page("page_signed_in")))
        if marked_first:
            sessions.gate.mark(W.ip_key(PROXY), 600, 600)
        sock = FakeSocket([ACCEPTED])
        net = Net({HOME: Resp(429, page("page_vercel")), GATEWAY: sock})
        health, _, _ = run_check(net, sessions=sessions, clock=clock)
        assert (health.state, health.effects) == ("alive", ()), marked_first
        assert health.identity == viewer_hash(VIEWER) and health.display_name is None
        assert net.methods() == (["WS"] if marked_first else ["GET", "WS"]), "a marked IP loads no page"
        assert sock.url and quote(TOKEN) in sock.url and kinds(sock) == [0x0f]


def test_a_closed_gate_is_unknown_with_no_effect_and_no_request():
    clock = Clock()
    sessions = W.Sessions(clock)
    sessions.gate.take(W.ip_key(PROXY), 60)
    net = Net({HOME: signed_in(), GATEWAY: FakeSocket([ACCEPTED])})
    health, _, _ = run_check(net, sessions=sessions, clock=clock)
    assert (health.state, health.effects) == ("unknown", ()) and net.calls == []


def refused_with(caplog, sock, cause):
    """A signed-in page whose estab is not accepted: unknown, one recheck rest naming `cause`, no strike, a warning
    naming the account and the cause."""
    caplog.set_level("WARNING", logger="relay_meta.check")
    health, sessions, _ = run_check(Net({HOME: signed_in(), GATEWAY: sock}))
    assert (health.state, health.identity) == ("unknown", None)
    assert [type(e) for e in health.effects] == [Cooldown], "a refused estab must rest the account: %r" % (
        health.effects,)
    rest = health.effects[0]
    assert rest.recheck and (rest.until - NOW).total_seconds() == 600
    assert cause in rest.reason, "the rest must name the cause: %r" % rest.reason
    warned = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("acc-1" in m and cause in m for m in warned), "the refusal must be logged: %r" % warned
    return sessions


REFUSAL_RETURN = '''            return Health("unknown", effects=(Cooldown(
                "gateway refused the session (%s), to be checked again" % answer,
                now() + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True),))'''


@mutation(CHECK_FILE, REFUSAL_RETURN, "            return UNKNOWN", expect="a refused estab must rest the account")
@mutation(CHECK_FILE, '(%s), to be checked again" % answer,', '(?), to be checked again",',
          expect="the rest must name the cause")
@mutation(CHECK_FILE, '            log.warning("meta check of %s: the gateway refused the session (%s)", account.id, '
          'answer)\n', "", expect="the refusal must be logged")
def test_an_estab_refused_with_another_code_rests_the_account_and_names_the_code(caplog):
    sessions = refused_with(caplog, FakeSocket([frame(ESTAB, 0, b'{"code":403,"msg":"x"}')]), "code:403")
    assert sessions.token("acc-1", 1) is not None, "only a 401 forgets the token"


@mutation(CHECK_FILE, '        if answer == "timeout":', '        if False:',
          expect="a slow proxy must not rest the account")
@mutation(CHECK_FILE, '            log.warning("meta check of %s: the gateway did not answer the estab in time", '
          'account.id)\n', "", expect="the timeout must be logged")
def test_a_silent_gateway_is_a_slow_proxy_logged_with_no_effect(caplog, monkeypatch):
    monkeypatch.setattr("relay_meta.engine.check.ESTAB_WAIT_S", 0.05)
    caplog.set_level("WARNING", logger="relay_meta.check")
    health, sessions, _ = run_check(Net({HOME: signed_in(), GATEWAY: FakeSocket([])}))
    assert (health.state, health.effects) == ("unknown", ()), "a slow proxy must not rest the account: %r" % (
        health.effects,)
    warned = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("acc-1" in m and "in time" in m for m in warned), "the timeout must be logged: %r" % warned
    assert sessions.token("acc-1", 1) is not None


@mutation(CHECK_FILE, '"none" if code is None else code', "code", expect="the rest must name the cause")
def test_an_estab_answer_with_no_integer_code_is_code_none_and_never_its_text(caplog):
    for payload in (b'{"msg":"secret-ish text"}', b'{"code":"403 secret-ish"}', b"[]"):
        caplog.clear()
        refused_with(caplog, FakeSocket([frame(ESTAB, 0, payload)]), "code:none")
        assert "secret-ish" not in caplog.text


@mutation(CHECK_FILE, '        return "frame_error"', '        return "other"', expect="the rest must name the cause")
def test_a_frame_that_does_not_decode_is_a_frame_error(caplog):
    refused_with(caplog, FakeSocket([b"\x0f\x00\x00\x09\x00\x00{}"]), "frame_error")


@mutation(WEB_FILE, "        if flags & _WS_CLOSE:", "        if False:", expect="the rest must name the cause")
@mutation(CHECK_FILE, "        return closed.cause", '        return "frame_error"',
          expect="the rest must name the cause")
@mutation(WIRE_FILE, "        if self.code == CLOSE_UNAUTHORIZED:", "        if False:",
          expect="the rest must name the cause")
def test_a_socket_the_gateway_closes_is_its_close_code_never_a_frame_error(caplog):
    # 06/11 live: the gateway closed the socket with 4003 right after the estab; read as a frame it was a FrameError
    sessions = refused_with(caplog, FakeSocket([b"\x0a", CLOSE_4003]), "unauthorized")
    assert sessions.token("acc-1", 1) is None, "a 4003 close is a 401: the token must be forgotten"
    assert "frame_error" not in caplog.text and "Unauthorized...." not in caplog.text
    for close, cause in (((1011).to_bytes(2, "big") + b"reason-PLANTED", "closed:1011"), (b"", "closed:none")):
        caplog.clear()
        sessions = refused_with(caplog, FakeSocket([(close, 8)]), cause)
        assert sessions.token("acc-1", 1) is not None and "reason-PLANTED" not in caplog.text


@mutation(WEB_FILE, "            raise GatewayClosed(b\"\") from None", "            raise", expect="WebSocketClosed")
def test_a_socket_curl_already_saw_closed_is_closed_none_never_an_escape(caplog):
    # the close came before curl handed it over as a message: curl's own WebSocketClosed, no code
    sessions = refused_with(caplog, FakeSocket([b"\x0a", WebSocketClosed("gone")]), "closed:none")
    assert sessions.token("acc-1", 1) is not None


@mutation(CHECK_FILE, "                sessions.forget(account.id)", "                pass",
          expect="the token must be forgotten")
def test_a_401_estab_forgets_the_token_and_rests_the_account(caplog):
    sessions = refused_with(caplog, FakeSocket([REFUSED]), "unauthorized")
    assert sessions.token("acc-1", 1) is None, "the token must be forgotten"


def test_no_command_runs():
    engine = build_engine(SPEC, None)
    for kind in ("titles", "recover", "refresh_catalog", "list_conversations"):
        with pytest.raises(ValueError, match="meta runs no command"):
            asyncio.run(engine.run_command(None, kind, {}))
