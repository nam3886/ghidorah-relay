"""The shell of the Vue admin in Chrome against the real app (browser tier): the CSP watch and its proof, login, sign
out, a stale token, the theme, the redirects, the stats strip and every route. Every test here runs under the watch of
`conftest.CspWatch`: a Content Security Policy violation on either of its two channels fails the test, and no test can
clear or filter one. The app is opened by `app.url` (`/manage`)."""

import re

import pytest
from page_helpers import settle, sideways
from patchright.sync_api import TimeoutError as PlaywrightTimeout
from patchright.sync_api import expect

from relay_core.admin.auth import LOGIN_MAX_FAILS
from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

ROUTES = ("jobs", "accounts", "keys", "logs", "alerts", "frames", "settings", "test")
WRONG = "Wrong username or password"


def without_length(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() != "content-length"}


# ---- the CSP watch and its negative proof ---------------------------------------------------------------------------

def test_the_csp_watch_fails_on_an_inline_style_and_an_inline_script_planted_before_startup(app):
    context, page = app.new_page()
    try:
        def plant(route):
            response = route.fetch()
            assert response.headers.get("content-security-policy"), "the real CSP header is on the planted document"
            planted = ('<style>body { color: red }</style><div style="color: red">x</div>'
                       "<script>window.__ran = 1</script></head>")
            route.fulfill(response=response, body=response.text().replace("</head>", planted, 1),
                          headers=without_length(response.headers))

        page.route("**/manage", plant)
        with pytest.raises(AssertionError, match="Content Security Policy violations"):
            page.goto(app.url)
        got = page.csp.entries()
        # each channel saw it on its own: the console lines and the in-page events are independent records
        assert any("inline style" in line.lower() for line in got["console"]), got["console"]
        assert any("inline script" in line.lower() for line in got["console"]), got["console"]
        directives = " ".join(e["directive"] for e in got["page"])
        assert "style-src" in directives and "script-src" in directives, got["page"]
        assert page.evaluate("window.__ran", isolated_context=False) is None, "the planted script must not run"
        with pytest.raises(AssertionError, match="Content Security Policy violations"):
            page.csp.check()
    finally:
        context.close()


def test_the_csp_watch_reads_a_violation_that_comes_after_the_last_navigation(app):
    context, page = app.new_page()
    try:
        page.goto(app.url)
        page.csp.check()                                        # a clean page passes the check
        page.evaluate("document.body.setAttribute('style', 'color: red')", isolated_context=False)
        got = page.csp.entries()
        assert got["console"] and got["page"], got
        with pytest.raises(AssertionError, match="Content Security Policy violations"):
            page.csp.check()                                    # what the teardown of every test runs
    finally:
        context.close()


def test_the_csp_watch_has_no_way_to_clear_or_filter_what_it_saw(app):
    context, page = app.new_page()
    try:
        public = [n for n in dir(page.csp) if not n.startswith("_")]
        assert sorted(public) == ["check", "entries", "watch"], public
        page.goto(app.url)
        page.evaluate("document.body.setAttribute('style', 'color: red')", isolated_context=False)
        page.problems.clear()                                   # the allowance a test has for other console noise
        with pytest.raises(AssertionError):
            page.csp.check()
        page.csp.entries()["console"].clear()                   # a copy: clearing it changes nothing
        with pytest.raises(AssertionError):
            page.csp.check()
    finally:
        context.close()


# ---- login ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_login_wrong_password_says_so_and_the_right_one_lands_on_jobs(app, app_page, live, db, color_scheme):
    page = app_page
    page.goto(app.url + "#/login")
    page.get_by_label("Username").fill(live.user)
    page.get_by_label("Password", exact=True).fill("wrong password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text(WRONG)).to_be_visible(timeout=app.sign_in_timeout_ms)
    page.problems.clear()                                       # the 401 of a wrong password is a console line
    app.sign_in(page)
    assert page.evaluate("sessionStorage.getItem('relay_admin_token')"), "the token is in sessionStorage"
    assert page.evaluate("sessionStorage.getItem('relay_admin_user')") == live.user
    assert db.one("SELECT count(*) FROM admin_sessions")[0] == 1


def test_login_shows_the_server_text_of_a_429(app, app_page, live, db):
    db.run("INSERT INTO admin_login_attempts (ip) SELECT '127.0.0.1'::inet FROM generate_series(1, %s)",
           LOGIN_MAX_FAILS)
    page = app_page
    page.goto(app.url + "#/login")
    page.get_by_label("Username").fill(live.user)
    page.get_by_label("Password", exact=True).fill(live.password)              # even the right password is refused
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_text("too many failed attempts; try again later")).to_be_visible(
        timeout=app.sign_in_timeout_ms)
    expect(page).to_have_url(re.compile(r"#/login$"))
    assert page.evaluate("sessionStorage.getItem('relay_admin_token')") is None
    page.problems.clear()                                       # the 429 is a console line


def test_an_already_signed_in_visit_to_login_skips_to_jobs(app, app_session):
    page = app_session
    page.goto(app.url + "#/login")
    expect(page).to_have_url(re.compile(r"#/jobs$"))


def test_a_stale_token_at_login_is_dropped_and_the_form_stays(app, app_page, live):
    page = app_page
    page.goto(app.url + "#/login")
    page.evaluate("sessionStorage.setItem('relay_admin_token', 'not-a-session')")
    page.reload()
    expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    expect(page).to_have_url(re.compile(r"#/login$"))
    # the check's 401 clears the token once it answers, which may be after the form is drawn: wait for it
    page.wait_for_function("() => sessionStorage.getItem('relay_admin_token') === null", timeout=5000)
    page.problems.clear()                                       # the 401 of the check is a console line


# ---- sign out and a stale token -------------------------------------------------------------------------------------

def test_sign_out_ends_the_session_and_the_dashboard_then_needs_a_login(app, app_session, db):
    page = app_session
    page.evaluate("sessionStorage.setItem('relay_test_key', 'typed-key')")
    app.sign_out(page)
    expect(page).to_have_url(re.compile(r"#/login$"))
    assert page.evaluate("sessionStorage.getItem('relay_admin_token')") is None
    assert page.evaluate("sessionStorage.getItem('relay_admin_user')") is None
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") is None
    assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0
    page.goto(app.url + "#/jobs")
    expect(page).to_have_url(re.compile(r"#/login$"))


def test_a_401_from_a_stale_token_clears_the_session_and_goes_to_login(app_session, db):
    page = app_session
    page.evaluate("sessionStorage.setItem('relay_test_key', 'typed-key')")
    db.run("DELETE FROM admin_sessions")                         # the server forgets the session
    try:
        page.get_by_role("tab", name=re.compile("Accounts")).click(timeout=3000)   # any admin call now answers 401
    except PlaywrightTimeout:
        # the stats strip's own call may answer 401 first and send the page to login under the click's feet
        assert re.search(r"#/login$", page.url), page.url
    expect(page).to_have_url(re.compile(r"#/login$"))
    for key in ("relay_admin_token", "relay_admin_user", "relay_test_key"):
        assert page.evaluate("sessionStorage.getItem(%r)" % key) is None, key
    page.problems.clear()                                       # the 401 is a console line


# ---- the theme ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_theme_follows_the_os_before_the_bundle_runs_so_nothing_flashes(app, color_scheme):
    context, page = app.new_page(color_scheme)
    try:
        page.route("**/ui/app/assets/**", lambda route: route.abort())      # no bundle: only theme-init.js can have run
        page.goto(app.url)
        is_dark = page.evaluate("document.documentElement.classList.contains('dark')")
        assert is_dark is (color_scheme == "dark")
        page.problems.clear()                                   # the aborted bundle is a console error
    finally:
        context.close()


def test_a_stored_choice_beats_the_os_setting_before_the_bundle_runs(app):
    context, page = app.new_page("dark")
    try:
        page.goto(app.url)
        page.evaluate("localStorage.setItem('relay_admin_theme', 'light')")
        page.route("**/ui/app/assets/**", lambda route: route.abort())
        page.reload()
        assert page.evaluate("document.documentElement.classList.contains('dark')") is False
        page.problems.clear()
    finally:
        context.close()


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_toggle_flips_the_theme_remembers_it_across_a_reload_and_both_themes_render(app_session, color_scheme):
    page = app_session
    html = page.locator("html")
    start_dark = color_scheme == "dark"
    if start_dark:
        expect(html).to_have_class(re.compile("dark"))
    else:
        expect(html).not_to_have_class(re.compile("dark"))
    page.get_by_role("button", name=re.compile(r"^Account menu")).click()
    page.get_by_role("menuitem", name=re.compile("theme", re.I)).click()
    if start_dark:
        expect(html).not_to_have_class(re.compile("dark"))
    else:
        expect(html).to_have_class(re.compile("dark"))
    assert page.evaluate("localStorage.getItem('relay_admin_theme')") == ("light" if start_dark else "dark")
    page.reload()
    assert page.evaluate("document.documentElement.classList.contains('dark')") is (not start_dark)
    expect(page.get_by_role("button", name=re.compile(r"^Account menu"))).to_be_visible()


# ---- the redirects --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, target", [("/", "/manage"), ("/login", "/manage#/login"), ("/test", "/manage#/test")])
def test_the_root_the_login_and_the_test_addresses_redirect_into_the_dashboard(live, app_page, path, target):
    r = app_page.request.get(live.url + path, max_redirects=0)
    assert r.status == 307 and r.headers["location"] == target, (path, r.status, r.headers.get("location"))


@pytest.mark.parametrize("route", ROUTES)
def test_a_bare_tab_hash_of_an_old_bookmark_redirects_to_its_route(app, app_session, route):
    page = app_session
    page.goto(app.url + "#" + route)
    page.reload()                                               # an old bookmark is a fresh load: the first navigation
    expect(page).to_have_url(re.compile(r"#/%s$" % route))
    expect(page.get_by_role("tab", selected=True)).to_have_count(1)


def test_an_unknown_hash_lands_on_jobs(app, app_session):
    page = app_session
    page.goto(app.url + "#/no-such-tab")
    page.reload()
    expect(page).to_have_url(re.compile(r"#/jobs$"))


# ---- the stats strip is gone ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("route", ["jobs", "accounts"])
def test_no_stats_strip_is_drawn_on_the_tabs_that_used_to_have_one(seeded, app, app_session, route):
    page = app_session
    page.goto(app.url + "#/" + route)
    expect(page.get_by_role("main")).to_be_visible()
    expect(page.get_by_role("region", name="Totals")).to_have_count(0)


# ---- the banner -----------------------------------------------------------------------------------------------------

def test_the_shell_draws_no_banner_even_with_a_live_condition(app, app_session, db):
    from accountkit import new_account
    new_account(db, label="only", checked=True)                 # one usable account is below the limit of 3
    page = app_session
    page.goto(app.url + "#/accounts")                           # Jobs draws the banner itself, after its band
    page.reload()
    expect(page.get_by_role("main")).to_be_visible()
    expect(page.get_by_role("status").filter(has_text="accounts_low")).to_have_count(0)


# ---- the console-error rule of the harness ---------------------------------------------------------------------------

class Message:
    """What the harness reads of a console message."""

    def __init__(self, text, url):
        self.text, self.location = text, {"url": url}


FAILED_LOAD = "Failed to load resource: the server responded with a status of 404 (Not Found)"


@mutation("tests/admin_ui/conftest.py", 'path.startswith(("/admin/", "/v1/"))', "True",
          expect="a missing file under /ui/ stays a console error")
@mutation("tests/admin_ui/conftest.py", 'path.startswith(("/admin/", "/v1/"))', "False",
          expect="a 404 answer of an API path is not a console error")
def test_a_failed_load_is_a_console_error_unless_it_is_an_answer_of_an_api_path(app):
    is_handled_api_failure = app.is_handled_api_failure
    for path in ("/admin/frames?provider=x", "/v1/models"):
        assert is_handled_api_failure(Message(FAILED_LOAD, "http://127.0.0.1:1" + path)), \
            "a 404 answer of an API path is not a console error: " + path
    for path in ("/ui/app/assets/gone.js", "/ui/app/assets/gone.css", "/m/x.png", "/administrator/x"):
        assert not is_handled_api_failure(Message(FAILED_LOAD, "http://127.0.0.1:1" + path)), \
            "a missing file under /ui/ stays a console error: " + path
    assert not is_handled_api_failure(Message("Uncaught boom", "http://127.0.0.1:1/admin/x"))
    # the text must be the browser's load failure, whatever the path
    assert not is_handled_api_failure(Message("Failed to fetch", "http://127.0.0.1:1/v1/x"))

    context, page = app.new_page()
    try:
        page.goto(app.url)
        for path in ("/admin/no-such-route", "/v1/no-such-route"):
            page.evaluate("p => fetch(p)", path)
        page.wait_for_timeout(300)
        assert page.problems == [], "a 404 answer of an API path is not a console error"
        page.evaluate("p => fetch(p)", "/ui/app/assets/gone.js")
        for _ in range(40):
            if page.problems:
                break
            page.wait_for_timeout(50)
        assert len(page.problems) == 1 and "Failed to load resource" in page.problems[0], \
            "a missing file under /ui/ stays a console error: %r" % page.problems
    finally:
        context.close()


# ---- responsive -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", [390, 768, 1280])
def test_every_route_fits_the_screen_without_the_page_scrolling_sideways(app, app_session, width):
    page = app_session
    page.set_viewport_size({"width": width, "height": 800})
    for route in ROUTES:
        page.goto(app.url + "#/" + route)
        expect(page.get_by_role("main")).to_be_visible()
        expect(page.get_by_role("button", name=re.compile(r"^Account menu"))).to_be_visible()   # the bar never hides it
        expect(page.get_by_role("tab", selected=True)).to_have_count(1)
        page.wait_for_load_state("networkidle")
        expect(page.locator("[aria-busy=true]")).to_have_count(0)    # measure the loaded page, not the skeleton
        overflow = sideways(page)
        wide = page.evaluate(
            "(() => { const w = document.documentElement.clientWidth;"
            " const name = e => e.tagName + '.' + e.className + '@' + Math.round(e.getBoundingClientRect().right);"
            " return [...document.querySelectorAll('body, body *')]"
            ".filter(e => e.getBoundingClientRect().right > w + 0.5"
            " || (e.scrollWidth > e.clientWidth + 0.5 && getComputedStyle(e).overflowX === 'visible'))"
            ".slice(0, 8).map(name); })()"
        ) if overflow > 0 else []
        assert overflow <= 0, "%s at %d px: the page is %d px too wide (a table or toolbar must scroll): %s" % (
            route, width, overflow, wide)
    page.csp.check()


# ---- every route ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_every_route_is_reachable_by_its_tab_and_by_its_address(app, app_session, color_scheme):
    page = app_session
    tabs = page.get_by_role("tab")
    expect(tabs).to_have_count(len(ROUTES))
    for route in ROUTES:
        page.goto(app.url + "#/" + route)
        expect(page).to_have_url(re.compile(r"#/%s$" % route))
        expect(page.get_by_role("tab", selected=True)).to_have_count(1)
        expect(page.get_by_role("main")).to_be_visible()
    for index in range(len(ROUTES)):                             # and by a click on each tab
        tabs.nth(index).click()
        expect(page).to_have_url(re.compile(r"#/%s$" % ROUTES[index]))
    page.csp.check()


# ---- the top bar, the bottom bar and the frame, drawn at the three widths (Ledger, JobsTablet) ---------------------

TOPBAR_BG = {"light": "rgb(255, 255, 255)", "dark": "rgb(21, 22, 31)"}
BRAND = {"light": "rgb(106, 53, 232)", "dark": "rgb(139, 92, 246)"}


def box(locator):
    value = locator.bounding_box()
    assert value, "not visible"
    return value


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize("width, bar_height, bottom_tabs", [(1280, 61, 0), (820, 61, 8), (390, 57, 5)])
def test_the_tabs_are_in_the_top_bar_on_a_desktop_and_in_a_bottom_bar_below_it(
        app, app_session, color_scheme, width, bar_height, bottom_tabs):
    page = app_session
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(app.url + "#/logs")
    bar = page.get_by_role("banner")
    logo = bar.get_by_role("link", name="Ghidorah admin home")
    menu = bar.get_by_role("button", name=re.compile(r"^Account menu"))
    for part in (logo, menu):
        expect(part).to_be_visible()
    # the bar has no Provider list (Jobs filters by its cards or strip, other pages by their own chips)
    expect(bar.get_by_test_id("provider-filter")).to_have_count(0)
    assert abs(box(bar)["height"] - bar_height) <= 1, box(bar)
    assert box(bar)["y"] == 0
    bottom = page.locator("nav.bnav")
    if bottom_tabs:
        expect(bar.get_by_role("tab")).to_have_count(0)
        expect(bar.get_by_test_id("page-title")).to_have_text("Request logs")
        expect(bottom.get_by_role("tab")).to_have_count(bottom_tabs)
        nav = box(bottom)
        assert nav["height"] >= 64 and abs(nav["y"] + nav["height"] - 800) <= 1, "the bar sits at the foot: %r" % nav
        for tab in bottom.get_by_role("tab").all():
            assert box(tab)["height"] >= 44, "a tab is a 44 px target at least"
        current = bottom.get_by_role("tab", selected=True)
        expect(current).to_have_count(1)
        line = current.evaluate("el => getComputedStyle(el).boxShadow")
        assert line == "%s 0px 2px 0px 0px inset" % BRAND[color_scheme], line
        assert box(menu)["height"] >= 44 and box(menu)["width"] >= 44
    else:
        expect(bottom).to_have_count(0)
        nav = bar.get_by_role("navigation", name="Main")
        expect(nav.get_by_role("tab")).to_have_count(8)
        assert box(nav)["x"] > box(logo)["x"] + box(logo)["width"], "the tabs follow the logo on one row"
        assert box(menu)["x"] > box(nav)["x"]
        expect(bar.get_by_test_id("live")).to_have_text("Live")
    bg = bar.evaluate("el => getComputedStyle(el).backgroundColor")
    assert bg == TOPBAR_BG[color_scheme], bg
    assert sideways(page) <= 0
    page.csp.check()


def test_the_phone_more_tab_opens_the_other_pages_in_a_bottom_sheet(app, app_session):
    page = app_session
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(app.url + "#/jobs")
    more = page.get_by_role("tab", name="More")
    more.click()
    sheet = page.get_by_role("dialog", name="More")
    expect(sheet).to_be_visible()
    links = sheet.get_by_role("link")
    expect(links).to_have_text(["API keys", "Prompt frames", "Settings", "Test"])
    radius = sheet.evaluate("el => getComputedStyle(el.closest('.rl-sheet') || el).borderTopLeftRadius")
    assert radius == "20px", radius
    sheet.get_by_role("link", name="Settings").click()
    expect(page).to_have_url(re.compile(r"#/settings$"))
    expect(sheet).to_be_hidden()
    # a page under More makes More the current tab
    expect(page.get_by_role("tab", selected=True)).to_have_attribute("aria-label", "More")
    page.csp.check()


@pytest.mark.parametrize("width, sides", [(1280, (28, 32, 48)), (820, (24, 24, 24)), (390, (14, 16, 20))])
def test_the_page_frame_is_centred_with_the_padding_of_its_width(app, app_session, width, sides):
    page = app_session
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(app.url + "#/settings")
    main = page.get_by_role("main")
    expect(main).to_be_visible()
    padding = main.evaluate(
        "el => { const s = getComputedStyle(el); return [s.paddingTop, s.paddingLeft, s.paddingBottom, s.maxWidth]; }")
    top, side, bottom = sides
    assert padding == ["%dpx" % top, "%dpx" % side, "%dpx" % bottom, "1360px" if width >= 1280 else "none"], padding
    page.set_viewport_size({"width": 1800, "height": 800})
    wide = box(main)
    assert abs(wide["width"] - 1360) < 1 and abs(wide["x"] - (1800 - 1360) / 2) < 2, wide


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_current_tab_is_underlined_in_the_brand_and_the_badges_are_drawn(app, app_session, color_scheme):
    page = app_session
    page.goto(app.url + "#/logs")
    current = page.get_by_role("tab", selected=True)
    expect(current).to_have_text("Request logs", use_inner_text=True)
    settle(page)
    underline = current.evaluate(
        "el => [getComputedStyle(el).borderBottomWidth, getComputedStyle(el).borderBottomColor]")
    assert underline == ["2px", BRAND[color_scheme]], underline
    assert box(current)["height"] == 60


def test_the_account_menu_offers_api_docs_and_sign_out_and_closes_on_escape(app_session):
    page = app_session
    button = page.get_by_role("button", name=re.compile(r"^Account menu"))
    button.click()
    expect(page.get_by_role("menuitem", name="API docs")).to_be_visible()
    expect(page.get_by_role("menuitem", name="Sign out")).to_be_visible()
    page.get_by_role("menuitem", name="API docs").focus()       # the keyboard is in the menu, as after an arrow key
    page.keyboard.press("Escape")
    expect(page.get_by_role("menuitem", name="Sign out")).to_be_hidden()
    expect(page.get_by_role("tab", selected=True)).to_have_count(1)


def test_the_api_docs_item_opens_the_reference_in_a_new_tab(app_session):
    page = app_session
    page.get_by_role("button", name=re.compile(r"^Account menu")).click()
    with page.context.expect_page() as opened:
        page.get_by_role("menuitem", name="API docs").click()
    reference = opened.value
    reference.wait_for_load_state()
    assert reference.url.endswith("/reference"), reference.url
    reference.close()


def test_the_fonts_load_from_this_origin_as_files_and_the_body_uses_them(app_session):
    page = app_session
    # A function, not a bare expression: patchright evals a bare one on every poll, and the page's CSP refuses eval
    page.wait_for_function("() => document.fonts.check('600 14px Manrope')")
    assert page.evaluate("getComputedStyle(document.body).fontFamily").startswith("Manrope")
    sources = page.evaluate("performance.getEntriesByType('resource').map(e => e.name).filter(n => /woff2?$/.test(n))")
    assert sources, "no font file was requested"
    origin = page.evaluate("location.origin")
    assert all(s.startswith(origin + "/ui/app/assets/") for s in sources), sources
    page.csp.check()


# ---- the drawn sizes of the top bar, its popovers and the login page (specs/shell.md 1, 2, 7; specs/login.md) --------

def sizes(locator):
    return locator.evaluate(
        "el => { const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }")


# The account trigger: the 30 px avatar letter alone (Ledger), in a 36 px target on a desktop and a 44 px one below it
# (JobsTablet); the menu is 220 px with 36 px items (44 below the desktop) and the menu lift.
@pytest.mark.parametrize("width, account, avatar, item_h", [
    (1440, (36, 36), (30, 30), 36),
    (820, (44, 44), (30, 30), 44),
    (390, (44, 44), (30, 30), 44)])
def test_the_top_bar_and_its_popovers_have_the_drawn_sizes(app, app_session, width, account, avatar, item_h):
    page = app_session
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(app.url + "#/logs")
    bar = page.get_by_role("banner")
    button = bar.get_by_test_id("account-menu")
    got = sizes(button)
    assert got[1] == account[1] and (account[0] is None or got[0] == account[0]), got
    assert sizes(button.locator(".account-avatar")) == list(avatar)
    button.click()
    menu = page.get_by_role("menu", name="Account")
    assert sizes(menu)[0] == 220
    assert sizes(menu.get_by_role("menuitem").first)[1] == item_h
    assert menu.evaluate("el => getComputedStyle(el).boxShadow") == (
        "rgba(16, 24, 40, 0.08) 0px 0px 0px 1px, rgba(16, 24, 40, 0.14) 0px 16px 40px 0px")
    expect(menu.get_by_text(re.compile(r"^Signed in as"))).to_be_visible()
    page.csp.check()


@pytest.mark.parametrize("width, card, field, button", [(1440, 400, 44, 44), (820, 400, 44, 44), (390, 358, 44, 44)])
def test_the_login_page_has_the_drawn_card_and_fields(app, app_page, width, card, field, button):
    page = app_page
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(app.url + "#/login")
    expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    assert sizes(page.get_by_role("form", name="Sign in to Ghidorah"))[0] == card
    assert sizes(page.get_by_label("Username"))[1] == field
    assert sizes(page.get_by_role("button", name="Sign in"))[1] == button
    assert page.locator("#loginError").count() == 0                # absent, not hidden, until a sign-in fails
    size = page.get_by_label("Username").evaluate("el => getComputedStyle(el).fontSize")
    assert size == ("16px" if width < 768 else "14px")
