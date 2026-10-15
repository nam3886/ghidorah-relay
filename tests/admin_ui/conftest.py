"""Fixtures of the admin UI tests: the relay-core database fixtures, and a live server on a real port for the browser
tests (`live`). The page tests need a real HTTP server because Chrome loads real URLs; the data is planted through the
same `db` handle the core tests use, and read back through the app."""

import asyncio
import importlib.util
import json
import re
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import uvicorn

CORE_TESTS = Path(__file__).resolve().parents[2] / "packages" / "relay-core" / "tests"
sys.path.insert(0, str(CORE_TESTS))
sys.path.insert(0, str(CORE_TESTS / "api"))

# The core fixtures live in a conftest of another directory; load that file once under its own name.
_spec = importlib.util.spec_from_file_location("relay_core_tests_conftest", CORE_TESTS / "conftest.py")
_core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _core
_spec.loader.exec_module(_core)
_migrated = _core._migrated
db = _core.db
_after_each_db_test = _core._after_each_db_test
pytest_configure = _core.pytest_configure      # registers the `fabricated_rows` marker the invariant checker reads

from apikit import ADMIN_PASSWORD, ADMIN_USER, serving  # noqa: E402  (needs the path set above)

from relay_core.admin import users  # noqa: E402
from relay_core.testing.fake_provider import FAKE_FRAMES, FakeSpec  # noqa: E402


class Live:
    """The app on 127.0.0.1:<port>, running on its own event loop in a thread."""

    def __init__(self, url: str, loop: asyncio.AbstractEventLoop, env):
        self.url, self.loop, self.env = url, loop, env
        self.user, self.password = ADMIN_USER, ADMIN_PASSWORD

    def call(self, coro):
        """Run a coroutine on the server's loop (it owns the pool) and wait for it."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(30)


@pytest.fixture
def framed():
    """Asking for this fixture makes `live` serve a provider that has prompt frames."""
    return True


@pytest.fixture
def live(db, request):
    spec = FakeSpec(frames=FAKE_FRAMES) if "framed" in request.fixturenames else None
    ready = threading.Event()
    box: dict = {}

    async def main():
        async with serving(db, raise_app_exceptions=False, spec=spec) as env:
            await users.create_user(env.pool, ADMIN_USER, ADMIN_PASSWORD)
            server = uvicorn.Server(uvicorn.Config(env.app, host="127.0.0.1", port=0, log_level="warning"))
            task = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.05)
            port = server.servers[0].sockets[0].getsockname()[1]
            box.update(live=Live("http://127.0.0.1:%d" % port, asyncio.get_running_loop(), env), server=server)
            ready.set()
            await task

    def run():
        try:
            asyncio.run(main())
        except BaseException as exc:      # a start-up failure must reach the test now, not after the wait below
            box["error"] = exc
            ready.set()
            raise

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(60), "the live server did not start"
    if "error" in box:
        raise RuntimeError("the live server died while starting: %r" % (box["error"],)) from box["error"]
    try:
        yield box["live"]
    finally:
        box["server"].should_exit = True
        thread.join(30)


# ---- the harness of the Vue admin (browser tier) ------------------------------------------------------------------
# The new app is served from the same static folder, at the entry the build writes. Until the switch it is NOT what
# /manage serves, so the tests open it by this one constant; the switch changes this line and nothing else.
APP_URL = "/ui/app/index.html"
ROUTES = ("jobs", "accounts", "keys", "logs", "alerts", "frames", "settings", "test")

# Markup a hostile server string could carry: if it ever reached the DOM as HTML, the image would run its handler.
PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>"

# An in-page recorder of the CSP violations of every document, injected before any page script runs. Each event is
# kept in the page and also handed to Python at once through a binding, so a document that is replaced by a
# navigation cannot take its violations with it.
CSP_RECORDER_JS = """
(() => {
  const seen = (window.__cspViolations = window.__cspViolations || []);
  document.addEventListener("securitypolicyviolation", (e) => {
    const v = {directive: e.violatedDirective, blocked: e.blockedURI, source: e.sourceFile, line: e.lineNumber,
               sample: e.sample, disposition: e.disposition, page: location.href};
    seen.push(v);
    try { window.__cspReport(JSON.stringify(v)); } catch (_) {}
  }, true);
})();
"""


class CspWatch:
    """Watches a browser context for Content Security Policy violations on two independent channels, both registered
    before any page exists, so the very first document is covered:
      console: any console message that mentions the policy (what Chrome prints for a refused style or script);
      page:    the `securitypolicyviolation` events, recorded in the page by an init script.
    `check()` fails when either channel holds an entry. There is no way to clear or filter the entries: a test that
    allows other console noise still fails on a CSP line."""

    def __init__(self, context):
        self._console: list[str] = []
        self._reported: list[dict] = []
        self._context = context
        context.expose_function("__cspReport", lambda raw: self._reported.append(json.loads(raw)))
        context.add_init_script(CSP_RECORDER_JS)
        context.on("console", self._on_console)

    def _on_console(self, message):
        if "content security policy" in message.text.lower():
            self._console.append("%s: %s" % (message.type, message.text))

    def _in_page(self) -> list[dict]:
        found = list(self._reported)
        for page in self._context.pages:
            try:
                for entry in page.evaluate("window.__cspViolations || []", isolated_context=False):
                    if entry not in found:
                        found.append(entry)
            except Exception:           # a page that is closing or mid-navigation: its events came through the binding
                pass
        return found

    def entries(self) -> dict:
        """A copy of what both channels hold now."""
        return {"console": list(self._console), "page": self._in_page()}

    def check(self) -> None:
        got = self.entries()
        assert not got["console"] and not got["page"], "Content Security Policy violations: %r" % (got,)

    def watch(self, page):
        """Make every navigation of `page` read both channels as soon as it settles."""
        for name in ("goto", "reload", "go_back", "go_forward"):
            original = getattr(page, name)

            def checked(*args, _original=original, **kwargs):
                result = _original(*args, **kwargs)
                self.check()
                return result

            setattr(page, name, checked)
        page.csp = self
        return page


def is_handled_api_failure(message) -> bool:
    """Chrome's own "Failed to load resource" line for an API path (`/admin/...`, `/v1/...`). An API answer of 4xx is
    something the app handles and shows (a frames route asking for a provider with none: 404 and a message), so it is
    not an error of the page. The same line for anything else, above all a missing file under `/ui/`, stays one."""
    if not message.text.startswith("Failed to load resource"):
        return False
    path = urlsplit((message.location or {}).get("url", "")).path
    return path.startswith(("/admin/", "/v1/"))


def new_app_page(chrome, live, color_scheme: str = "light"):
    """A fresh context and page for the Vue admin: CSP watch registered first, then the usual console and script error
    list (`page.problems`, which a test may allow to hold a line it expects; the CSP entries are not in it)."""
    context = chrome.new_context(base_url=live.url, viewport={"width": 1500, "height": 1000}, color_scheme=color_scheme)
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live.url)
    watch = CspWatch(context)
    page = watch.watch(context.new_page())
    page.problems = []
    page.on("pageerror", lambda e: page.problems.append("script error: %s" % e))
    page.on("console", lambda m: page.problems.append("console %s: %s" % (m.type, m.text))
            if m.type == "error" and "content security policy" not in m.text.lower()
            and not is_handled_api_failure(m) else None)
    return context, page


# Module scope: a session-wide driver keeps its event loop running and breaks every later `asyncio.run` in the session.
@pytest.fixture(scope="module")
def chrome():
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


@pytest.fixture
def color_scheme():
    """The OS colour scheme of the context; a test parametrizes it with "light" and "dark"."""
    return "light"


@pytest.fixture
def app_page(chrome, live, color_scheme):
    """A page for the Vue admin under the CSP watch. At the end the watch is read once more, and a script error or an
    unexpected console error fails the test."""
    context, page = new_app_page(chrome, live, color_scheme)
    try:
        yield page
        page.csp.check()
    finally:
        context.close()
    assert not page.problems, page.problems


def sign_in_app(page, live, username=None, password=None) -> None:
    """Sign in through the new app's own login view and wait for the dashboard."""
    from patchright.sync_api import expect
    page.goto(APP_URL + "#/login")
    page.get_by_label("Username").fill(username or live.user)
    page.get_by_label("Password").fill(password or live.password)
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(re.compile(r"#/jobs$"))


@pytest.fixture
def app_session(app_page, live):
    """`app_page`, signed in and on the Jobs route."""
    sign_in_app(app_page, live)
    return app_page


def planted(db, scenario):
    """jobkit's helpers run their own event loop; the browser driver already owns this thread's, so run them aside."""
    from concurrent.futures import ThreadPoolExecutor

    from jobkit import run
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(run, db, scenario).result()


@pytest.fixture
def seeded(db):
    """Rows for the shell tests, through the same helpers the API tests use (the fake provider): one done job, one
    failed job, one dead and one disabled account beside an available one. Returns the counts."""
    from accountkit import done_job, new_account
    from jobkit import key as new_key
    from test_admin_jobs import failed_job
    k = new_key(db)
    done_job(db, k, new_account(db, label="alive", checked=True))
    planted(db, lambda p: failed_job(p, k, "one"))
    new_account(db, label="gone", state="dead", checked=True)
    new_account(db, label="off", state="disabled", checked=True)
    return {"done": 1, "failed": 1, "dead": 1, "disabled": 1}


def assert_planted_as_text(page, text: str = PLANTED) -> None:
    """The planted markup is on the page as literal text and made no element and ran no handler. (`window` is the
    page's own: patchright evaluates in an isolated world unless told otherwise.)"""
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "the planted handler ran"
    assert page.locator("img[src='x']").count() == 0, "the planted markup became an element"
    assert text in page.locator("body").inner_text(), "the planted text is not shown"


class App:
    """What a test of the Vue admin needs, as one fixture (`app`): test modules cannot import this conftest by name, so
    the constants and helpers travel on the fixture. The module-scope `chrome` and the `live` server are its inputs."""

    url = APP_URL
    routes = ROUTES
    planted = PLANTED

    def __init__(self, chrome, live):
        self._chrome, self._live = chrome, live

    def new_page(self, color_scheme: str = "light"):
        """(context, page) under a fresh CspWatch; the caller closes the context and reads `page.csp.check()`."""
        return new_app_page(self._chrome, self._live, color_scheme)

    def sign_in(self, page, username=None, password=None) -> None:
        sign_in_app(page, self._live, username, password)

    def run(self, db, scenario):
        """Plant rows with a jobkit scenario (`failed_job(p, key, "x")` and the like) from inside a browser test."""
        return planted(db, scenario)

    assert_planted_as_text = staticmethod(assert_planted_as_text)
    is_handled_api_failure = staticmethod(is_handled_api_failure)


@pytest.fixture
def app(chrome, live):
    return App(chrome, live)
