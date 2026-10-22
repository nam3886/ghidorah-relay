"""The account check over a fake HTTP transport (no network), fed vibes' recorded answers from
fixtures/vibes/s4-capture; and the engine's unbuilt parts."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from relay_core.providers import Balance, Cooldown, Dead, Proxy, Suspect
from relay_core.testing.mutation import mutation
from relay_vibes.engine import build_engine
from relay_vibes.engine.check import check_account
from relay_vibes.engine.http import proxy_url
from relay_vibes.spec import SPEC

CHECK_FILE = "packages/provider-vibes/src/relay_vibes/engine/check.py"
CAPTURE = Path(__file__).resolve().parents[3] / "fixtures" / "vibes" / "s4-capture" / "capture.json"
RECORDED = {step["step"]: step for step in json.loads(CAPTURE.read_text(encoding="utf-8"))}
ME, QUOTA_LEFT = RECORDED["auth_me"]["body"], RECORDED["quota_upsell"]["body"]
USER_ID = ME["user"]["id"]
SESSION = "sess-value-1"
QUOTA_USED = {"upsell": {"bannerStatus": "Enabled", "title": "You've reached your limit", "body": "Try tomorrow",
                         "buttonLabel": "Learn more", "deeplink": "https://accountscenter.meta.com/"}}
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)


def answers(me=(200, ME), quota=(200, QUOTA_LEFT)):
    """A fake vibes.ai: each path answers its (status, body); a body that is an exception is raised instead. The
    requests it saw are kept on `.seen`."""
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = {"/api/auth/me": me, "/api/quota/upsell": quota}[request.url.path]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body) if not isinstance(body, str) else httpx.Response(status, text=body)

    transport = httpx.MockTransport(handle)
    transport.seen = seen
    return transport


def check(transport):
    blob = json.dumps({"meta_session": SESSION, "cookies": []}).encode()
    ctx = SimpleNamespace(account=SimpleNamespace(credential=blob, proxy=None))
    return asyncio.run(check_account(ctx, None, transport=transport, now=lambda: NOW))


@mutation(CHECK_FILE, '    return Health("alive", identity=user["id"],', '    return Health("alive", identity=None,',
          expect="the identity is the user id")
def test_the_recorded_signed_in_answers_are_alive_with_the_user_id_and_the_username():
    transport = answers()
    health = check(transport)
    assert health.identity == USER_ID, "the identity is the user id"
    assert (health.state, health.display_name, health.effects) == ("alive", ME["user"]["username"], ())
    me = transport.seen[0]
    assert [r.url.path for r in transport.seen] == ["/api/auth/me", "/api/quota/upsell"]
    assert str(me.url).startswith("https://vibes.ai/api/auth/me")
    assert me.headers["cookie"] == "cookie_ack=true; meta_session=%s" % SESSION
    assert me.headers["origin"] == "https://vibes.ai" and me.headers["referer"].startswith("https://vibes.ai")
    assert "Chrome/" in me.headers["user-agent"]


SIGNED_OUT_RETURN = "            if me.status_code in SIGNED_OUT_STATUSES:"
STRIKE_READ = '                return Health("unknown", effects=doubt_effects(\n                    "not signed in'
VERDICT_READ = '                return Health("signed_out", effects=doubt_effects(\n                    "not signed in'


@mutation(CHECK_FILE, SIGNED_OUT_RETURN, "            if False:", expect="a 401 is a strike")
@mutation(CHECK_FILE, STRIKE_READ, VERDICT_READ, expect="one signed-out read must be a strike, not a verdict")
@mutation(CHECK_FILE, "now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))",
          "now + timedelta(seconds=SIGNED_OUT_REST_S)))", expect="the rest did not ask for a recheck")
def test_a_401_or_403_is_a_spaced_strike_and_a_rest_never_a_dead_account():
    for status in (401, 403):
        transport = answers(me=(status, {"error": "Unauthorized"}))
        health = check(transport)
        assert health.state == "unknown", "one signed-out read must be a strike, not a verdict: %s" % health.state
        assert [type(e) for e in health.effects] == [Suspect, Cooldown], "a 401 is a strike: %s" % (health.effects,)
        strike, rest = health.effects
        assert strike.min_gap_s == 540 and str(status) in strike.reason
        assert rest.until == NOW + timedelta(seconds=600)
        assert rest.recheck, "the rest did not ask for a recheck"
        assert not any(isinstance(e, Dead) for e in health.effects) and health.identity is None
        assert [r.url.path for r in transport.seen] == ["/api/auth/me"], "no quota read for a signed-out session"


@mutation(CHECK_FILE, "            if status != ACTIVE_STATUS:", "            if False:", expect="a suspended account")
def test_an_account_that_is_not_active_is_a_strike_too():
    suspended = {"user": {**ME["user"], "accountStatus": "SUSPENDED"}}
    health = check(answers(me=(200, suspended)))
    assert health.state == "unknown" and [type(e) for e in health.effects] == [Suspect, Cooldown], (
        "a suspended account read as alive: %s" % (health,))
    assert "SUSPENDED" in health.effects[0].reason


def test_a_server_error_or_an_answer_that_is_not_a_user_is_unknown_with_no_strike():
    for me in ((500, {"error": "Internal"}), (302, {}), (200, {"user": None}), (200, {"user": {"id": ""}}),
               (200, "<html>not json</html>"), (200, ["a", "list"])):
        health = check(answers(me=me))
        assert (health.state, health.effects) == ("unknown", ()), (me, health)


QUOTA_RULE = ('    return isinstance(upsell, dict) and str(upsell.get("bannerStatus") or "").lower() '
              "== QUOTA_USED_BANNER")


@mutation(CHECK_FILE, QUOTA_RULE,
          '    return isinstance(upsell, dict) and str(upsell.get("bannerStatus") or "") == QUOTA_USED_BANNER',
          expect="the used-up banner")
@mutation(CHECK_FILE, QUOTA_RULE, "    return False", expect="the used-up banner")
def test_the_used_up_banner_reports_no_quota_left_on_both_pools():
    health = check(answers(quota=(200, QUOTA_USED)))
    assert health.state == "alive" and health.identity == USER_ID
    assert health.effects == (Balance("video", 0), Balance("image", 0)), "the used-up banner: %s" % (health.effects,)
    lower = {"upsell": {**QUOTA_USED["upsell"], "bannerStatus": "enabled"}}
    assert check(answers(quota=(200, lower))).effects == (Balance("video", 0), Balance("image", 0))


def test_quota_left_a_disabled_banner_or_a_failed_quota_read_changes_nothing():
    disabled = {"upsell": {**QUOTA_USED["upsell"], "bannerStatus": "Disabled"}}
    for quota in ((200, QUOTA_LEFT), (200, disabled), (500, QUOTA_USED), (200, "oops"),
                  (200, httpx.ReadTimeout("slow"))):
        health = check(answers(quota=quota))
        assert (health.state, health.identity, health.effects) == ("alive", USER_ID, ()), quota


def test_a_network_failure_fails_the_check_without_a_strike_or_the_proxy_login():
    transport = answers(me=(200, httpx.ConnectError("connect to http://user:pw-123@10.0.0.1:8080 failed")))
    with pytest.raises(RuntimeError) as caught:
        check(transport)
    assert str(caught.value) == "vibes.ai could not be reached (ConnectError)"


def test_the_proxy_url_quotes_the_login():
    assert proxy_url(None) is None
    assert proxy_url(Proxy("10.0.0.1", 8080)) == "http://10.0.0.1:8080"
    assert proxy_url(Proxy("10.0.0.1", 8080, "us er:p@ss:w")) == "http://us%20er:p%40ss%3Aw@10.0.0.1:8080"


def test_submit_poll_and_fetch_are_not_built_and_no_command_runs():
    engine = build_engine(SPEC, None)
    with pytest.raises(NotImplementedError, match="vibes submit is not built yet"):
        asyncio.run(engine.submit(None, None))
    with pytest.raises(NotImplementedError, match="vibes poll is not built yet"):
        asyncio.run(engine.poll(None, None))
    with pytest.raises(NotImplementedError, match="vibes fetch is not built yet"):
        engine.fetch(None, None)
    for kind in ("titles", "recover", "refresh_catalog"):
        with pytest.raises(ValueError, match="vibes runs no command"):
            asyncio.run(engine.run_command(None, kind, {}))
