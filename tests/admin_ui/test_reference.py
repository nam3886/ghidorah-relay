"""The `/reference` page (Scalar over `/openapi.json`) stays vanilla and outside the Vue build: it loads and renders
under its own looser CSP, the vendored renderer is the pinned build with its licence, the page is public (its bar
names the API and links to nothing locked) and follows the stored theme. Browser tests run under the CSP watch of
`conftest.CspWatch`."""

import base64
import hashlib
import os
import re
import urllib.parse
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


@mutation(REFERENCE_HTML, '<span id="logo">', '<span id="logo" onclick="x()">',
          expect="inline handler, style or script")
@mutation(REFERENCE_HTML, '<span id="bar-title">', '<span style="color:red" id="bar-title">',
          expect="inline handler, style or script")
@mutation(REFERENCE_HTML, '<link rel="stylesheet" href="/ui/reference.css">',
          '<link rel="stylesheet" href="https://cdn.example.com/x.css">', expect="external or missing asset")
@mutation(REFERENCE_JS, "localStorage.getItem('relay_admin_theme')",
          "localStorage.getItem('relay_admin_theme') || document.cookie", expect="a store the admin token lives in")
def test_the_reference_files_hold_no_inline_handler_style_script_or_foreign_host_and_read_only_the_stored_theme():
    page = (STATIC / "reference.html").read_text()
    script = (STATIC / "reference.js").read_text()
    assert re.search(r"<html lang=\"en\"", page) and "<title>" in page and 'name="viewport"' in page
    forbidden = re.compile(r"""[\s"']on[a-z]+\s*=\s*["']|\sstyle\s*=\s*["']|<style\b|javascript:""", re.I)
    problems = [m.group(0) for text in (page, script) for m in forbidden.finditer(text)]
    problems += ["inline <script> body" for _ in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>\s*[^<\s]", page)]
    assert not problems, "inline handler, style or script: %s" % problems
    external = []
    for url in re.findall(r"""\b(?:src|href|data-renderer)\s*=\s*["']([^"']+)["']""", page):
        if re.match(r"(?:https?:)?//", url) or url.startswith("data:"):
            external.append(url)
        elif url.startswith("/ui/") and not (STATIC / url.removeprefix("/ui/")).is_file():
            external.append(url)
    assert not external, "a page names an external or missing asset: %s" % external
    assert not re.search(r"document\.cookie|sessionStorage|indexedDB", script), "a store the admin token lives in"
    assert re.findall(r"localStorage\.(\w+)\(([^)]*)\)", script) == [("getItem", "'relay_admin_theme'")], \
        "a store the admin token lives in: reference.js may only read the stored theme"


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


@mutation(REFERENCE_HTML, '<span id="bar-title">API reference</span>',
          '<a id="back" href="/manage">Dashboard</a><span id="bar-title">API reference</span>',
          expect="links to the locked dashboard")
def test_the_public_bar_shows_the_logo_and_the_title_and_links_to_nothing_locked():
    """The page is public; /manage is locked behind the operator's access gate, so a link there only fails."""
    page = (STATIC / "reference.html").read_text()
    bar = re.search(r'<header id="bar">(.*?)</header>', page, re.S).group(1)
    assert 'id="logo"' in bar and '<span id="bar-title">API reference</span>' in bar, bar
    assert "/manage" not in page, "the public page links to the locked dashboard"
    assert "#back" not in (STATIC / "reference.js").read_text() + (STATIC / "reference.css").read_text()


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
        assert bar == ("rgb(23, 18, 34)" if dark else "rgb(255, 255, 255)"), bar
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
            expect(page.locator("#bar-title")).to_have_text("API reference")
        finally:
            context.close()


@mutation(REFERENCE_HTML, 'href="/llms.txt"', 'href="/llm.txt"', expect="no-JS text")
@mutation(REFERENCE_HTML, '<a href="/openapi.json">/openapi.json</a>.</p>\n  </noscript>',
          '/openapi.json.</p>\n  </noscript>', expect="no-JS text")
def test_the_page_tells_a_reader_without_javascript_where_the_machine_readable_docs_are():
    page = (STATIC / "reference.html").read_text()
    noscript = re.search(r"<noscript>(.*?)</noscript>", page, re.S).group(1)
    for path in ("/llms.txt", "/llms-full.txt", "/openapi.json"):
        assert 'href="%s"' % path in noscript, "no-JS text: " + path


MENU = ["Copy Markdown link", "View as Markdown", "Open in ChatGPT", "Open in Claude"]
CLIPBOARD_STUB = """() => { window.__copied = [];
  const writeText = (t) => { window.__copied.push(t); return Promise.resolve(); };
  Object.defineProperty(navigator, 'clipboard', {value: {writeText}}); }"""


@pytest.mark.browser
@mutation(REFERENCE_JS, "'https://claude.ai/new?q=' + prompt", "'https://claude.ai/new?q=x'", expect="Claude prompt")
@mutation(REFERENCE_JS, "origin + '/llms-full.txt'", "origin + '/llms.txt'", expect="Markdown link")
@mutation(REFERENCE_JS, "if (e.key === 'Escape')", "if (e.key === 'Esc')", expect="Escape")
@mutation(REFERENCE_JS, "if (!document.getElementById('ai').contains(e.target)) setMenu(false);", "", expect="outside")
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_copy_page_split_button_copies_the_markdown_and_its_menu_has_the_four_targets(app, scheme):
    context, page = app.new_page(scheme)
    try:
        page.goto("/reference")
        page.evaluate(CLIPBOARD_STUB, isolated_context=False)
        wait_rendered(page)
        origin = page.evaluate("location.origin")
        full = page.evaluate("fetch('/llms-full.txt').then(r => r.text())")
        main = page.locator("#ai-copy")
        expect(main).to_have_text("Copy page")
        main.click()
        expect(main).to_have_text("Copied")
        copied = page.evaluate("window.__copied", isolated_context=False)
        assert copied == [full], "the main button copies /llms-full.txt"
        toggle = page.locator("#ai-toggle")
        menu = page.locator("#ai-list")
        expect(menu).to_be_hidden()
        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.click()
        expect(menu).to_be_visible()
        assert toggle.get_attribute("aria-expanded") == "true" and menu.get_attribute("role") == "menu"
        items = menu.get_by_role("menuitem")
        assert [t.strip() for t in items.all_inner_texts()] == MENU
        shots = os.environ.get("RELAY_SCREENSHOTS_DIR")
        if shots:
            page.screenshot(path=os.path.join(shots, "reference-copy-menu-%s.png" % scheme))
        prompt = urllib.parse.quote("Read " + origin + "/llms.txt so I can ask questions about the Ghidorah API.",
                                    safe="")
        assert page.locator("#ai-view").get_attribute("href") == "/llms-full.txt"
        assert page.locator("#ai-chatgpt").get_attribute("href") == "https://chatgpt.com/?q=" + prompt
        claude = page.locator("#ai-claude").get_attribute("href")
        assert claude == "https://claude.ai/new?q=" + prompt, "Claude prompt"
        for link in ("view", "chatgpt", "claude"):
            assert page.locator("#ai-" + link).get_attribute("target") == "_blank"
            assert "noopener" in page.locator("#ai-" + link).get_attribute("rel")
        page.locator("#ai-link").click()
        expect(page.locator("#ai-link-label")).to_have_text("Copied")
        link = page.evaluate("window.__copied", isolated_context=False)[-1]
        assert link == origin + "/llms-full.txt", "Markdown link"
        assert menu.is_hidden(), "a chosen item closes the menu"
        # Escape closes and returns focus to the chevron; an outside click closes too
        toggle.click()
        page.keyboard.press("Escape")
        assert menu.is_hidden(), "Escape closes the menu"
        assert page.evaluate("document.activeElement.id") == "ai-toggle", "Escape"
        toggle.click()
        assert page.evaluate("document.activeElement.id") == "ai-link", "opening focuses the first item"
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement.id") == "ai-view", "ArrowDown moves to the next item"
        page.mouse.click(300, 400)
        assert menu.is_hidden(), "outside click closes the menu"
        assert toggle.get_attribute("aria-expanded") == "false"
        page.csp.check()
        assert not page.problems, page.problems
    finally:
        context.close()


WRITE_STUB = """() => { window.__copied = [];
  const write = (items) => items[0].getType('text/plain').then((b) => b.text())
    .then((t) => { window.__copied.push(t); });
  const writeText = () => Promise.reject(new Error('gesture'));
  Object.defineProperty(navigator, 'clipboard', {value: {write, writeText}}); }"""


@pytest.mark.browser
@mutation(REFERENCE_JS, "label.textContent = LABELS[label.id];", "label.textContent = 'Copied';", expect="Copy page")
@mutation(REFERENCE_JS, "if (!r.ok) throw new Error('llms-full.txt ' + r.status);", "", expect="error body")
@mutation(REFERENCE_JS, "navigator.clipboard.write([new ClipboardItem(",
          "navigator.clipboard.writeText([new ClipboardItem(", expect="Copied")
@pytest.mark.parametrize("stub", [CLIPBOARD_STUB, WRITE_STUB], ids=["writeText", "ClipboardItem"])
def test_the_copy_button_restores_its_label_after_a_double_click_and_never_copies_an_error_body(app, stub):
    context, page = app.new_page()
    try:
        page.goto("/reference")
        page.evaluate(stub, isolated_context=False)
        main = page.locator("#ai-copy")
        full = page.evaluate("fetch('/llms-full.txt').then(r => r.text())")
        main.click()
        main.click()                                   # the second click must not save "Copied" as the label
        expect(main).to_have_text("Copied")
        expect(main).to_have_text("Copy page", timeout=4000)
        assert page.evaluate("window.__copied", isolated_context=False) == [full, full], "ClipboardItem"
        page.route("**/llms-full.txt", lambda route: route.fulfill(status=500, body="boom"))
        page.evaluate("window.__copied.length = 0", isolated_context=False)
        main.click()
        expect(main).to_have_text("Copy failed")
        assert page.evaluate("window.__copied", isolated_context=False) == [], "error body copied"
        expect(main).to_have_text("Copy page", timeout=4000)
    finally:
        context.close()


@pytest.mark.browser
@mutation(REFERENCE_JS, "} else if (e.key === 'Tab') { setMenu(false); }", "}", expect="Tab")
@mutation(REFERENCE_JS, "e.key === 'Home'", "e.key === 'Hom'", expect="Home")
@mutation(REFERENCE_JS, "e.key === 'End'", "e.key === 'En'", expect="End")
@mutation(REFERENCE_JS,
          "ai.list.addEventListener('click', (e) => { if (e.target.closest('[role=menuitem]')) setMenu(false); });",
          "", expect="chosen item")
def test_the_menu_jumps_with_home_and_end_closes_on_tab_and_after_any_item_is_chosen(app):
    context, page = app.new_page()
    try:
        page.goto("/reference")
        page.evaluate(CLIPBOARD_STUB, isolated_context=False)
        menu, toggle = page.locator("#ai-list"), page.locator("#ai-toggle")
        toggle.click()
        page.keyboard.press("End")
        assert page.evaluate("document.activeElement.id") == "ai-claude", "End"
        page.keyboard.press("Home")
        assert page.evaluate("document.activeElement.id") == "ai-link", "Home"
        page.keyboard.press("Tab")
        assert menu.is_hidden(), "Tab"
        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.click()
        page.locator("#ai-link").click()
        assert menu.is_hidden(), "chosen item: copy link"
        toggle.click()
        with page.expect_popup():
            page.locator("#ai-view").click()
        assert menu.is_hidden(), "chosen item: link followed"
    finally:
        context.close()
