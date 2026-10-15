"""The `/reference` page (Scalar over `/openapi.json`) stays vanilla and outside the Vue build: it loads and renders
under its own looser CSP, the vendored renderer is the pinned build with its licence, the page links back to the
dashboard and follows the stored theme. Browser tests run under the CSP watch of `conftest.CspWatch`."""

import base64
import hashlib
import re
from pathlib import Path

import pytest
from patchright.sync_api import expect

from relay_core.testing.mutation import mutation

STATIC = Path(__file__).resolve().parents[2] / "packages/relay-core/src/relay_core/admin/static"
REFERENCE_HTML = "packages/relay-core/src/relay_core/admin/static/reference.html"
REFERENCE_JS = "packages/relay-core/src/relay_core/admin/static/reference.js"
PAGES_PY = "packages/relay-core/src/relay_core/admin/pages.py"
SCALAR_URL = "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.122/dist/browser/standalone.min.js"
SCALAR_SHA384 = "GSEN1x/V+skrQFYZQ1d4OxNM7t1xg7f8WND43lCquTJEO+mRo9l0AAa3HzrNOLqt"


def wait_rendered(page, seconds: float = 20) -> None:
    """Poll from here: wait_for_function would evaluate a string in the page itself, which its CSP refuses."""
    for _ in range(int(seconds * 10)):
        if page.evaluate("document.body.innerText.length") > 200:
            return
        page.wait_for_timeout(100)
    raise AssertionError("the renderer drew nothing in %s s" % seconds)


def sha384(path: Path) -> str:
    return base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


@mutation(REFERENCE_HTML, "sha384-GSEN1x", "sha384-XSEN1x", expect="does not pin the renderer")
def test_the_renderer_is_the_pinned_vendored_build_with_its_licence_and_the_page_pins_it_by_hash():
    assert sha384(STATIC / "vendor" / "scalar.min.js") == SCALAR_SHA384, \
        "the vendored renderer changed: pinned %s from %s" % (SCALAR_SHA384, SCALAR_URL)
    assert "MIT License" in (STATIC / "vendor" / "scalar.LICENSE.txt").read_text()
    page = (STATIC / "reference.html").read_text()
    assert 'data-integrity="sha384-%s"' % SCALAR_SHA384 in page, "reference.html does not pin the renderer"
    assert 'data-renderer="/ui/vendor/scalar.min.js"' in page


@pytest.mark.browser
@mutation(PAGES_PY, "CSP.replace(\"script-src 'self';\",", "CSP.replace(\"zz\",",
          expect="Content Security Policy violations")
def test_the_page_renders_the_api_from_the_schema_and_the_browser_reports_no_csp_violation(app):
    context, page = app.new_page()
    try:
        page.goto("/reference")
        wait_rendered(page)
        title = page.evaluate("fetch('/openapi.json').then(r => r.json()).then(j => j.info.title)")
        expect(page.locator("body")).to_contain_text(title)
        assert page.evaluate("document.getElementById('fallback').hidden") is True, "the renderer loaded"
        page.wait_for_timeout(1500)                    # styles the renderer writes into the page come after the render
        page.csp.check()
        assert not page.problems, page.problems
    finally:
        context.close()


@pytest.mark.browser
@mutation(REFERENCE_HTML, 'href="/manage"', 'href="/manage-x"', expect="the back link")
def test_the_bar_links_back_to_the_dashboard(app):
    context, page = app.new_page()
    try:
        page.goto("/reference")
        link = page.get_by_role("link", name=re.compile("Back to the dashboard"))
        expect(link).to_be_visible()
        assert link.get_attribute("href") == "/manage", "the back link goes to %s" % link.get_attribute("href")
        with page.expect_response(lambda r: urlpath(r.url) == "/manage") as served:
            link.click()
        assert served.value.status == 200 and "/reference" not in page.url, (served.value.status, page.url)
    finally:
        context.close()


def urlpath(url: str) -> str:
    return re.sub(r"^https?://[^/]+", "", url).split("?")[0].split("#")[0]


@pytest.mark.browser
@mutation(REFERENCE_JS, "forceDarkModeState: dark ? 'dark' : 'light',", "forceDarkModeState: 'dark',",
          expect="the renderer is in dark mode, not light")
@mutation(REFERENCE_JS, "localStorage.getItem('relay_admin_theme')", "localStorage.getItem('relay_admin_theme_x')",
          expect="the page ignores the theme")
@pytest.mark.parametrize(("stored", "os_scheme", "dark"), [
    ("dark", "light", True),       # a stored choice wins over the OS setting, both ways
    ("light", "dark", False),
    (None, "dark", True),          # nothing stored: the OS setting
    (None, "light", False),
])
def test_the_page_and_the_renderer_follow_the_stored_theme_else_the_os_setting(app, stored, os_scheme, dark):
    context, page = app.new_page(os_scheme)
    try:
        page.goto("/reference")                        # a first visit on this origin: storage is empty
        if stored:
            page.evaluate("t => localStorage.setItem('relay_admin_theme', t)", stored)
            page.reload()
        wait_rendered(page)
        word = "dark" if dark else "light"
        page_dark = page.evaluate("document.documentElement.classList.contains('dark')")
        assert page_dark is dark, "the page ignores the theme"
        assert page.evaluate("document.body.classList.contains('dark-mode')") is dark, \
            "the renderer is in %s mode, not %s (stored=%s, os=%s)" % (
                "light" if dark else "dark", word, stored, os_scheme)
        assert page.evaluate("document.body.classList.contains('light-mode')") is (not dark)
        # the bar uses the token of that theme
        bar = page.evaluate("getComputedStyle(document.getElementById('bar')).backgroundColor")
        assert bar == ("rgb(24, 24, 27)" if dark else "rgb(255, 255, 255)"), bar
        page.csp.check()
    finally:
        context.close()


@pytest.mark.browser
@mutation(REFERENCE_JS, "renderer.integrity = self_.dataset.integrity;", "", expect="the tampered renderer ran")
def test_a_renderer_that_fails_to_load_or_does_not_match_its_pin_shows_the_fallback_card(app):
    for name, handler in {
        "missing": lambda route: route.abort(),
        "tampered": lambda route: route.fulfill(status=200, content_type="text/javascript",
                                                body="window.__tampered = 1;"),
    }.items():
        context, page = app.new_page()
        try:
            page.route("**/ui/vendor/scalar.min.js", handler)
            page.goto("/reference")                    # the load event waits for the renderer, run or refused
            assert page.evaluate("window.__tampered", isolated_context=False) is None, \
                "the tampered renderer ran (%s)" % name
            card = page.locator("#fallback")
            expect(card).to_be_visible()
            expect(card).to_contain_text("API reference could not load")
            assert card.get_by_role("link", name="/openapi.json").get_attribute("href") == "/openapi.json"
            expect(page.get_by_role("link", name=re.compile("Back to the dashboard"))).to_be_visible()
        finally:
            context.close()
