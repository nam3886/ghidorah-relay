"""The on-demand page check: what it reports on the saved page, what it prints, how it reads the live page. Fixtures
and fakes only (fixtures/grok/spike-261106); nothing here touches the network or a database."""

import ast
import asyncio
from pathlib import Path

import pytest
from grokkit import FIX, PROXY, SSO, Ctx, FakeSession, Resp, assert_no_secret, fixture, settings

from relay_core.testing.mutation import mutation
from relay_grok.constants import SITE_URL
from relay_grok.engine import drift
from relay_grok.engine.statsig import SALT
from relay_grok.engine.web import proxy_url

DRIFT_FILE = "packages/provider-grok/src/relay_grok/engine/drift.py"
PINNED_CHECK = "                    for where, names in PINNED.items() for name in names if name not in js]"
EXIT_CHECK = "    return 1 if any(not line.startswith(STATSIG) for line in lines) else 0"
BARE_COOKIES = "                status, text = await bare.get_text(SITE_URL + name, cookies=False)"
SALT_CHECK = "    if SALT not in js:"
PINNED_GONE = "                    for where, names in PINNED.items() for name in names if False]"
BARE_WITH_COOKIES = "                status, text = await bare.get_text(SITE_URL + name)"
ALL = [n for names in drift.PINNED.values() for n in names]
JS = "var a={" + ",".join('"%s":1' % n for n in ALL) + "};"
STATSIG_NOTES = ["statsig grok-site-verification: not on the page", "statsig curves: not on the page or its scripts",
                 "statsig salt: not in the scripts"]


def test_every_pinned_name_is_in_the_captures():
    """A pin is a name grok really used on 06/11, not a typo that would read as drift forever."""
    text = "".join(p.read_text(encoding="utf-8") for p in FIX.glob("*.json"))
    assert [n for n in ALL if n not in text] == []


@mutation(DRIFT_FILE, EXIT_CHECK, "    return 1 if lines else 0", expect="the statsig notes are not drift")
@mutation(DRIFT_FILE, SALT_CHECK, "    if False:", expect="the missing salt is noted")
def test_the_saved_home_page_reports_no_seed_today():
    page = fixture("page_home.html")
    lines = drift.check_page(page, [JS])
    assert lines == STATSIG_NOTES, "the missing salt is noted"
    assert drift.exit_code(lines) == 0, "the statsig notes are not drift"
    lines = drift.check_page(page, [])
    assert lines[:3] == STATSIG_NOTES and len(lines) == 3 + len(ALL)
    assert drift.exit_code(lines) == 1


def test_a_page_with_a_seed_curves_and_the_salt_has_no_note():
    page = '<meta name="grok-site-verification" content="c2VlZA"/>'
    assert drift.check_page(page, [JS + 'x={\\"curves\\":[]};' + SALT]) == []


@mutation(DRIFT_FILE, PINNED_CHECK, PINNED_GONE,
          expect="a missing frame key is named")
def test_a_missing_frame_key_is_named():
    js = JS.replace('"percentage_complete"', '"pc"').replace('"videoPostId"', '"vp"')
    lines = drift.check_page("", [js])[3:]
    assert lines == ["imagine frame percentage_complete: not in the scripts",
                     "media stream videoPostId: not in the scripts"], "a missing frame key is named"
    assert drift.exit_code(lines) == 1


def test_the_output_holds_no_value_from_the_page():
    page = fixture("page_home.html").replace("<title>", '<meta name="grok-site-verification" content="%s"/><title>'
                                             % SSO)
    js = JS.replace('"assetId"', '"%s"' % SSO) + 'curves="PLANTED-curve"'
    out = "\n".join(drift.check_page(page, [js]))
    assert out == "statsig curves: not on the page or its scripts\nstatsig salt: not in the scripts\n" \
        "media stream assetId: not in the scripts"
    assert_no_secret(out)
    assert "d0000000" not in out       # the page's user id


def test_the_page_names_its_scripts_once_in_order():
    page = fixture("page_home.html")
    assert drift.scripts_of(page) == ["/_next/static/chunks/webpack-0000000000000000.js"]
    assert drift.scripts_of(page + page) == drift.scripts_of(page)


def test_main_needs_an_account_label():
    with pytest.raises(SystemExit):
        drift.main([])


def run_fetch(page_resp, script_resp=None):
    """The live read on two fake sessions: the page on the first, the scripts on the second."""
    sessions = [FakeSession({("GET", "/imagine"): page_resp}),
                FakeSession({("GET", "/_next/static/"): script_resp or Resp(200, text=JS)})]
    made = iter(sessions)
    result = asyncio.run(drift.fetch_page(Ctx().account, settings(), make_session=lambda p: next(made)(p)))
    return sessions, result


@mutation(DRIFT_FILE, BARE_COOKIES, BARE_WITH_COOKIES, expect="a script got the cookie")
def test_the_page_carries_the_cookie_and_the_scripts_none_both_through_the_proxy():
    (page_s, bare_s), (html, chunks) = run_fetch(Resp(200, text=fixture("page_home.html"), cookies={"sso": "rotated"}))
    assert html == fixture("page_home.html") and chunks == [JS]
    (page_call,) = page_s.requests
    (script_call,) = bare_s.requests
    assert page_call.url == SITE_URL + "/imagine" and page_call.has_cookie
    assert script_call.url == SITE_URL + "/_next/static/chunks/webpack-0000000000000000.js"
    assert not script_call.has_cookie, "a script got the cookie"
    url = proxy_url(PROXY)
    assert all(c.proxies == {"http": url, "https": url} and c.allow_redirects is False
               for c in (page_call, script_call))
    assert page_s.closed and bare_s.closed


def test_drift_sends_nothing():
    """Reads only: the module names no send step, and a live read is two GETs."""
    tree = ast.parse(Path(drift.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
        {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"send_new", "socket", "upload", "NEW_CONVERSATION_PATH", "item_create", "send_json"}
    (page_s, bare_s), _ = run_fetch(Resp(200, text=fixture("page_home.html")))
    assert [c.method for c in page_s.requests + bare_s.requests] == ["GET", "GET"]


def test_an_unreadable_page_says_why_without_a_value():
    page = fixture("page_home.html")
    for page_resp, script_resp, why in (
            (Resp(302, text=page), None, "the page answered 302"),
            (Resp(200, text="<html>%s</html>" % SSO), None, "the page names no scripts"),
            (ConnectionError("proxy user:PLANTED-pw failed"), None, "could not read the page (NetError)"),
            (Resp(200, text=page), Resp(404, text=SSO), "no script of the page answered 200")):
        with pytest.raises(drift.Unreadable) as e:
            run_fetch(page_resp, script_resp)
        assert str(e.value) == why
        assert_no_secret(e.value)
