"""CDP response patches and telemetry blocking (contracts §6.1; coverage BSES06, E03): the page's request must never
hang on us, a patch is counted only when it really changed something, and none of it goes through a route (which would
switch Chrome's HTTP cache off). Pure tests use a recording stand-in for the CDP session; browser tests use a real
one."""

import base64
import gzip
import json

import pytest
from browserkit import Account, redact, site_for, sync
from fixtures_server import SiteServer

from relay_browser import session as sess
from relay_browser.evidence import EvidenceWriter
from relay_browser.net import NetRecorder
from relay_browser.session import ResponsePatch, open_session
from relay_core.testing.mutation import mutation

S = "packages/relay-browser/src/relay_browser/session.py"
URL = "https://site.test/api/action_bar_v3/get_item_conf?x=1"
CONTINUE_ON_ERROR = ('            with contextlib.suppress(Exception):\n'
                     '                await cdp.send("Fetch.continueRequest", {"requestId": rid})')
BLOCK_CALL = ('                await cdp.send("Network.setBlockedURLs", '
              '{"urls": list(self.site.telemetry_url_patterns)})')


def add_30(body: str) -> tuple[str, bool]:
    """A stand-in patch: adds a 30 option to `options` when it is missing; anything else is left alone."""
    try:
        data = json.loads(body)
    except ValueError:
        return body, False
    options = data.get("options") if isinstance(data, dict) else None
    if not isinstance(options, list) or 30 in options:
        return body, False
    return json.dumps({**data, "options": [*options, 30]}), True


PATCH = ResponsePatch("*get_item_conf*", add_30)


class FakeCdp:
    def __init__(self, body: str = '{"options": [5, 10]}', fail=()):
        self.body, self.sent, self.handlers, self.fail = body, [], {}, set(fail)

    def on(self, event, handler):
        self.handlers[event] = handler

    async def send(self, method, params=None):
        self.sent.append((method, params))
        if method in self.fail:
            raise RuntimeError("%s not allowed" % method)
        if method == "Fetch.getResponseBody":
            return {"body": base64.b64encode(self.body.encode()).decode(), "base64Encoded": True}
        return {}

    def methods(self):
        return [m for m, _ in self.sent]


def paused(url=URL, **extra) -> dict:
    return {"requestId": "1", "request": {"url": url}, "responseStatusCode": 200,
            "responseHeaders": [{"name": "Content-Type", "value": "application/json"},
                                {"name": "Content-Encoding", "value": "br"}, {"name": "Content-Length", "value": "9"}],
            **extra}


# ---- the counter and the fulfilled response --------------------------------------------------------------------------
@mutation(S, "                self.count += 1", "                pass", expect="was not added")
@mutation(S, 'if h["name"].lower() not in ("content-length", "content-encoding")]', "if True]",
          expect="still says it is compressed")
@sync
async def test_a_changed_response_is_counted_and_fulfilled_as_plain_text():
    cdp, patcher = FakeCdp(), sess.ResponsePatcher([PATCH])
    await patcher._handle(cdp, paused())
    method, params = cdp.sent[-1]
    assert method == "Fetch.fulfillRequest" and patcher.count == 1, "the 30 option was not added to the config"
    assert json.loads(base64.b64decode(params["body"]))["options"] == [5, 10, 30]
    assert params["responseCode"] == 200
    names = [h["name"].lower() for h in params["responseHeaders"]]
    assert names == ["content-type"], "the patched body is plain but the response still says it is compressed: %r" % (
        names,)


@mutation(S, "            if changed:      # only now", "            if True:      # only now",
          expect="counted as patched")
@sync
async def test_a_response_the_patch_does_not_recognise_is_passed_on_unchanged_and_not_counted():
    cdp, patcher = FakeCdp("not json"), sess.ResponsePatcher([PATCH])
    await patcher._handle(cdp, paused())
    method, params = cdp.sent[-1]
    assert method == "Fetch.fulfillRequest" and patcher.count == 0, "an unrecognised config is counted as patched"
    assert base64.b64decode(params["body"]) == b"not json", "the page gets a config it never sent"
    done = FakeCdp('{"options": [5, 10, 30]}')
    await patcher._handle(done, paused())
    assert patcher.count == 0, "a config that already has the option is counted as patched"


@mutation(S, '            await cdp.send("Fetch.fulfillRequest", {',
          '            self.count += int(changed)\n            await cdp.send("Fetch.fulfillRequest", {',
          expect="counted a patch the page never got")
@sync
async def test_a_response_that_could_not_be_fulfilled_is_not_counted_as_patched():
    cdp, patcher = FakeCdp(fail={"Fetch.fulfillRequest"}), sess.ResponsePatcher([PATCH])
    await patcher._handle(cdp, paused())
    assert patcher.count == 0, "counted a patch the page never got"
    assert cdp.sent[-1][0] == "Fetch.continueRequest", "the page's request was left hanging after a failed fulfil"


@mutation(S, CONTINUE_ON_ERROR, "            pass", expect="request hanging")
@sync
async def test_a_body_that_cannot_be_read_or_patched_lets_the_request_go_on():
    unreadable = FakeCdp(fail={"Fetch.getResponseBody"})
    await sess.ResponsePatcher([PATCH])._handle(unreadable, paused())
    assert unreadable.sent[-1][0] == "Fetch.continueRequest", \
        "a config the patch cannot read leaves the page's request hanging"

    def broken(body):
        raise ValueError("patch bug")
    crashing, patcher = FakeCdp(), sess.ResponsePatcher([ResponsePatch("*", broken)])
    await patcher._handle(crashing, paused())
    assert crashing.sent[-1][0] == "Fetch.continueRequest" and patcher.count == 0, \
        "a patch that raises leaves the page's request hanging"


@mutation(S, 'if patch is None or ev.get("responseErrorReason"):', "if False:", expect="read a body it must not touch")
@sync
async def test_an_error_response_or_an_unmatched_url_is_never_read():
    cdp = FakeCdp()
    patcher = sess.ResponsePatcher([PATCH])
    await patcher._handle(cdp, paused(url="https://site.test/other"))
    await patcher._handle(cdp, paused(responseErrorReason="Failed"))
    assert "Fetch.getResponseBody" not in cdp.methods(), "the patcher read a body it must not touch"
    assert cdp.methods() == ["Fetch.continueRequest"] * 2


# ---- installing it ---------------------------------------------------------------------------------------------------
@mutation(S, '"requestStage": "Response"', '"requestStage": "Request"', expect="Response stage")
@sync
async def test_install_pauses_only_the_declared_urls_at_the_response_stage():
    cdp = FakeCdp()
    await sess.ResponsePatcher([PATCH, ResponsePatch("*second*", add_30)]).install(cdp)
    assert cdp.sent == [("Fetch.enable", {"patterns": [{"urlPattern": "*get_item_conf*", "requestStage": "Response"},
                                                       {"urlPattern": "*second*", "requestStage": "Response"}]})], \
        "the patterns are not limited to the declared URLs at the Response stage: %r" % (cdp.sent,)
    assert "Fetch.requestPaused" in cdp.handlers
    quiet = FakeCdp()
    await sess.ResponsePatcher([]).install(quiet)
    assert quiet.sent == [] and not quiet.handlers, "Fetch was enabled with nothing to patch"


@sync
async def test_a_cdp_that_cannot_enable_fetch_does_not_fail_the_run():
    cdp, patcher = FakeCdp(fail={"Fetch.enable"}), sess.ResponsePatcher([PATCH])
    await patcher.install(cdp)
    assert patcher.count == 0


class FakeCtx:
    def __init__(self, cdp):
        self.cdp, self.pages = cdp, []

    async def new_cdp_session(self, page):
        if self.cdp is None:
            raise RuntimeError("target closed")
        return self.cdp


def session_for(tmp_path, ctx, **site_fields) -> sess.BrowserSession:
    recorder = NetRecorder()
    return sess.BrowserSession(site_for(tmp_path, **site_fields), ctx, recorder, EvidenceWriter(redact, recorder),
                               sess.ResponsePatcher([PATCH]), sess.ProfileClaim(str(tmp_path), None, True))


@mutation(S, BLOCK_CALL, "                pass", expect="telemetry patterns were not sent")
@sync
async def test_telemetry_is_blocked_through_cdp_and_a_failing_block_does_not_stop_the_patch(tmp_path):
    cdp = FakeCdp(fail={"Network.setBlockedURLs"})
    s = session_for(tmp_path, FakeCtx(cdp), telemetry_url_patterns=("*telemetry*",))
    await s._prepare(object())
    assert ("Network.setBlockedURLs", {"urls": ["*telemetry*"]}) in cdp.sent, "telemetry patterns were not sent"
    assert "Fetch.enable" in cdp.methods(), "a failing telemetry block stopped the patch from being installed"


@sync
async def test_without_a_devtools_session_the_run_goes_on_with_a_zero_patch_count(tmp_path):
    s = session_for(tmp_path, FakeCtx(None))
    await s._prepare(object())
    assert s.patched == 0


# ---- a real Chrome ---------------------------------------------------------------------------------------------------
def conf_server() -> SiteServer:
    server = SiteServer()
    server.add("GET", "/", "<title>app</title>")
    body = gzip.compress(b'{"options": [5, 10]}')
    server.add("GET", "/api/get_item_conf", lambda r: (200, {"Content-Encoding": "gzip"}, body), "application/json")
    server.add("GET", "/api/other", lambda r: (200, {}, '{"options": [5, 10]}'), "application/json")
    server.add("GET", "/telemetry/ping", "x")
    server.add("GET", "/ok", "x")
    return server


FETCH = "path => fetch(path).then(r => r.text())"


@pytest.mark.browser
@sync
async def test_chrome_gets_the_patched_config_and_only_for_the_declared_url(tmp_path, token):
    async with conf_server() as server:
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact,
                                response_patches=[PATCH]) as s:
            await s.page.goto(server.url())
            patched = json.loads(await s.page.evaluate(FETCH, "/api/get_item_conf"))
            other = json.loads(await s.page.evaluate(FETCH, "/api/other"))
            assert patched["options"] == [5, 10, 30], "the page received the config unchanged"
            assert other["options"] == [5, 10], "a URL outside the declared pattern was patched"
            assert s.patched == 1


@pytest.mark.browser
@sync
async def test_when_the_patch_raises_chrome_still_gets_the_original_response(tmp_path, token):
    def broken(body):
        raise ValueError("patch bug")
    async with conf_server() as server:
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact,
                                response_patches=[ResponsePatch("*get_item_conf*", broken)]) as s:
            await s.page.goto(server.url())
            got = json.loads(await s.page.evaluate(FETCH, "/api/get_item_conf"))
            assert got["options"] == [5, 10], "the page did not get the original response"
            assert s.patched == 0


@pytest.mark.browser
@sync
async def test_telemetry_urls_are_blocked_and_the_rest_loads(tmp_path, token):
    site = site_for(tmp_path, telemetry_url_patterns=("*/telemetry/*",))
    async with conf_server() as server:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            blocked = await s.page.evaluate("fetch('/telemetry/ping').then(() => 'loaded', () => 'blocked')")
            ok = await s.page.evaluate("fetch('/ok').then(() => 'loaded', () => 'blocked')")
    assert (blocked, ok) == ("blocked", "loaded"), "telemetry was not blocked, or something else was: %r" % (
        (blocked, ok),)
    assert "/telemetry/ping" not in [r.path for r in server.requests], "the blocked request reached the server"
