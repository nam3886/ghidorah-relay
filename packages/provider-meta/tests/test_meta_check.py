"""The account check over HTTP: a page read, one EstabStream and no prompt. The page and the gateway are the fakes of
`metakit` (recorded pages, a scripted socket); nothing here opens a network."""

import asyncio
from urllib.parse import quote

import pytest
from metakit import NOW, PROXY, TOKEN, VIEWER, Clock, Ctx, FakeSocket, Net, Resp, assert_no_secret, page, settings

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


@mutation(CHECK_FILE, 'recheck=True),))', "),))", expect="the rest must ask for a recheck")
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


@mutation(CHECK_FILE, "            sessions.forget(account.id)", "            pass",
          expect="the token must be forgotten")
def test_a_refused_estab_forgets_the_token_and_is_unknown():
    net = Net({HOME: signed_in(), GATEWAY: FakeSocket([REFUSED])})
    health, sessions, _ = run_check(net)
    assert (health.state, health.effects, health.identity) == ("unknown", (), None)
    assert sessions.token("acc-1", 1) is None, "the token must be forgotten"


def test_no_command_runs():
    engine = build_engine(SPEC, None)
    for kind in ("titles", "recover", "refresh_catalog", "list_conversations"):
        with pytest.raises(ValueError, match="meta runs no command"):
            asyncio.run(engine.run_command(None, kind, {}))
