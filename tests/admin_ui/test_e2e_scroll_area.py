"""The page scroll area of the Vue admin in Chrome against the real app (browser tier): from 768 px the routed page
scrolls in one `el-scrollbar` under the top bar and the document does not; on a phone the window scrolls as before.
Also the keyboard (a tab click then PageDown), the Settings anchors, the popups that follow their trigger, and the
proof that the whole-page photos of the pixel tests (the flow layout) show the same page as the scroll area does."""

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_capture import flow, scroll_page_to, settle, sideways

from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

WRAP = ".app-scroll__wrap"
SHELL = "apps/admin-web/src/components/layout/AppShell.vue"
SETTINGS = "apps/admin-web/src/views/SettingsView.vue"
SIDEWAYS = "tests/admin_ui/pixel/pixel_capture.py"
FLOW_ROOT = "    height: auto;\n    overflow: visible;\n  }\n  .app-shell .app-scroll__wrap {"
FLOW_HIDDEN = "    height: auto;\n    overflow: hidden;\n  }\n  .app-shell .app-scroll__wrap {"
FOCUS = "  if (!main.value?.contains(document.activeElement)) focusPage();\n"
HEIGHT = 700
WIDTHS = (1440, 768, 390)
TIMEOUT_MS = 10_000

STATE_JS = """() => {
  const w = document.querySelector('.app-scroll__wrap'), d = document.documentElement;
  return {doc: d.scrollHeight, view: innerHeight, docWidth: d.clientWidth, view_width: innerWidth,
          bar: document.querySelector('header.topbar').getBoundingClientRect().height,
          wrapHeight: w.clientHeight, wrapRoom: w.scrollHeight - w.clientHeight, top: w.scrollTop, windowY: scrollY,
          active: document.activeElement === w, root: getComputedStyle(w.parentElement).overflowY,
          wrapOverflow: getComputedStyle(w).overflowY};
}"""


def state(page) -> dict:
    return page.evaluate(STATE_JS, isolated_context=False)


def loaded(page) -> None:
    """The route drawn and its reads answered, so the height no longer changes."""
    page.wait_for_load_state("networkidle")
    expect(page.locator("[aria-busy=true]")).to_have_count(0, timeout=TIMEOUT_MS)
    settle(page)


def go(app, page, route: str, width: int, height: int = HEIGHT) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(app.url + "#/" + route)
    expect(page.get_by_role("main")).to_be_visible()
    loaded(page)


# ---- the scroller ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", WIDTHS)
@mutation(SHELL, "  height: 100vh;\n  height: 100dvh;\n  overflow: hidden;",
          "  height: 200vh;\n  height: 200dvh;\n  overflow: hidden;", expect="the document scrolls")
def test_the_page_scrolls_in_its_own_area_from_768_and_in_the_window_on_a_phone(app, app_session, width):
    page = app_session
    go(app, page, "settings", width)
    got = state(page)
    if width >= 768:
        assert got["doc"] <= got["view"], "the document scrolls: %r" % got
        assert got["wrapHeight"] == got["view"] - got["bar"], "the area is the window minus the bar: %r" % got
        assert got["wrapRoom"] > 0, "Settings is taller than the area, so it scrolls in it: %r" % got
        assert got["docWidth"] == got["view_width"], "a browser scroll bar sits beside the header: %r" % got
        assert got["root"] == "hidden" and got["wrapOverflow"] in ("auto", "scroll"), got
    else:
        assert got["doc"] > got["view"], "the phone scrolls the window: %r" % got
        assert got["wrapRoom"] <= 0 and got["root"] == "visible" and got["wrapOverflow"] == "visible", got


def test_the_sign_in_page_stays_window_scrolled(app, app_page):
    app_page.set_viewport_size({"width": 1280, "height": 200})
    app_page.goto(app.url + "#/login")
    expect(app_page.get_by_role("button", name="Sign in")).to_be_visible()
    assert app_page.locator(".app-scroll").count() == 0
    assert app_page.evaluate("document.documentElement.clientHeight", isolated_context=False) <= 200


THUMB_JS = """() => {
  const t = document.querySelector('.app-scroll > .el-scrollbar__bar.is-vertical .el-scrollbar__thumb');
  const r = t.getBoundingClientRect(), s = getComputedStyle(t), root = getComputedStyle(document.documentElement);
  return {right: innerWidth - r.right, width: r.width, color: s.backgroundColor, opacity: s.opacity,
          shown: r.height > 0 && getComputedStyle(t.parentElement).display !== 'none',
          radius: parseFloat(s.borderTopLeftRadius), line: root.getPropertyValue('--rl-line-strong').trim()};
}"""


@pytest.mark.parametrize("color_scheme", ("light", "dark"))
@mutation(SHELL, "  right: 3px;\n  bottom: 0;\n  width: 9px;", "  right: 2px;\n  bottom: 0;\n  width: 9px;",
          expect="the pill is 9 px wide")
@mutation(SHELL, "  --el-scrollbar-bg-color: var(--rl-line-strong);", "  --el-scrollbar-bg-color: var(--rl-muted);",
          expect="the pill is the strong line colour")
def test_the_page_bar_is_the_signed_off_pill(app, app_session, color_scheme):
    """9 px wide, the strong line colour at full strength, 3 px from the edge, rounded, drawn without hovering."""
    page = app_session
    go(app, page, "settings", 1440)
    got = page.evaluate(THUMB_JS, isolated_context=False)
    h = got["line"].lstrip("#")
    line = "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    assert got["shown"] and got["width"] == 9 and got["right"] == 3, "the pill is 9 px wide and 3 px in: %r" % got
    assert got["color"] == line and got["opacity"] == "1" and got["radius"] >= 4.5, (
        "the pill is the strong line colour at full strength, rounded: %r" % got)


# ---- the keyboard and the top of the page --------------------------------------------------------------------------

@mutation(SHELL, "  scroller.value?.setScrollTop(0);\n", "", expect="a tab change starts at the top")
@mutation(SHELL, FOCUS, "", expect="focus moved to the page scroller")
def test_a_tab_change_returns_to_the_top_and_the_page_scrolls_by_key_at_once(app, app_session):
    page = app_session
    go(app, page, "settings", 1440, 400)
    scroll_page_to(page, 250)
    assert state(page)["top"] > 0
    page.get_by_role("tab", name="Jobs", exact=True).click()
    expect(page.get_by_role("tab", name="Jobs", exact=True)).to_have_attribute("aria-selected", "true")
    loaded(page)
    got = state(page)
    assert got["top"] == 0, "a tab change starts at the top: %r" % got
    assert got["active"], "focus moved to the page scroller, so the keys scroll it: %r" % got
    page.get_by_role("tab", name="Settings", exact=True).click()
    expect(page.get_by_role("tab", name="Settings", exact=True)).to_have_attribute("aria-selected", "true")
    loaded(page)
    page.keyboard.press("PageDown")
    page.wait_for_function("() => document.querySelector('.app-scroll__wrap').scrollTop > 0", timeout=TIMEOUT_MS)


def test_the_page_scrolls_by_key_straight_after_the_page_has_loaded(app, app_session):
    page = app_session
    go(app, page, "settings", 1440, 400)
    page.keyboard.press("Space")
    page.wait_for_function("() => document.querySelector('.app-scroll__wrap').scrollTop > 0", timeout=TIMEOUT_MS)
    page.keyboard.press("Home")
    page.wait_for_function("() => document.querySelector('.app-scroll__wrap').scrollTop === 0", timeout=TIMEOUT_MS)
    page.keyboard.press("End")
    page.wait_for_function("() => document.querySelector('.app-scroll__wrap').scrollTop > 0", timeout=TIMEOUT_MS)


@mutation(SHELL, "watch(() => route.name, showTop, { flush: \"post\" });",
          "watch(() => route.fullPath, showTop, { flush: \"post\" });", expect="a query change keeps the position")
def test_a_query_only_change_keeps_the_scroll_position(app, app_session):
    """Only a tab change returns to the top: a change of the query on the same route leaves the page where it is."""
    page = app_session
    go(app, page, "settings", 1440, 400)
    scroll_page_to(page, 250)
    page.evaluate("location.hash = '#/settings?x=1'", isolated_context=False)
    page.wait_for_function("() => location.hash === '#/settings?x=1'", timeout=TIMEOUT_MS)
    loaded(page)
    assert state(page)["top"] == 250, "a query change keeps the position"


@mutation(SHELL, 'wrap?.setAttribute("role", "region");', "", expect="Page content")
def test_the_focusable_wrap_is_a_named_region(app, app_session):
    page = app_session
    go(app, page, "settings", 1440)
    region = page.get_by_role("region", name="Page content")
    expect(region).to_have_count(1)
    assert region.evaluate("e => e.classList.contains('app-scroll__wrap') && e.getAttribute('tabindex') === '-1'"), \
        "the focusable wrap is a named region, not Element Plus's root"


RING_JS = """() => { const w = document.querySelector('.app-scroll__wrap'), s = getComputedStyle(w);
  return {focused: document.activeElement === w, visible: w.matches(':focus-visible'), style: s.outlineStyle,
          width: s.outlineWidth, offset: s.outlineOffset}; }"""


def no_ring(page, how: str) -> None:
    page.wait_for_function("() => document.activeElement.classList.contains('app-scroll__wrap')", timeout=TIMEOUT_MS)
    got = page.evaluate(RING_JS, isolated_context=False)
    assert got["focused"] and (got["style"] == "none" or got["width"] == "0px"), (
        "the page area never draws a focus ring, %s: %r" % (how, got))


@mutation(SHELL, ".app-shell .app-scroll__wrap:focus {\n  outline: none;",
          ".app-shell .app-scroll__wrap:focus:not(:focus-visible) {\n  outline: none;",
          expect="the page area never draws a focus ring")
def test_the_page_area_never_draws_a_focus_ring(app, app_session):
    """Focus is moved to the area by script on load and on a tab change, and Chrome counts that as keyboard focus."""
    page = app_session
    go(app, page, "settings", 1440)
    no_ring(page, "after load")
    page.get_by_role("tab", name="Accounts", exact=True).click()
    expect(page.get_by_role("heading", name="Accounts", exact=True)).to_be_visible()
    no_ring(page, "after a mouse click on a tab")
    page.get_by_role("tab", name="Settings", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("tab", name="Settings", exact=True)).to_have_attribute("aria-selected", "true")
    no_ring(page, "after Enter on a tab")


# ---- Settings anchors and the sticky navigation ------------------------------------------------------------------

@pytest.mark.parametrize(("width", "under"), ((1440, 16), (768, 68)))
@mutation(SETTINGS, "  --settings-bar: 0px;\n  --settings-anchor: calc(var(--settings-bar) + 16px);",
          "  --settings-bar: 65px;\n  --settings-anchor: calc(var(--settings-bar) + 16px);",
          expect="px under the top of the area")
def test_a_settings_nav_click_lands_the_card_under_the_navigation(app, app_session, width, under):
    page = app_session
    go(app, page, "settings", width, 330)
    page.locator(".snav").get_by_role("link", name="Boot-only values").click()
    page.wait_for_function("() => document.querySelector('.app-scroll__wrap').scrollTop > 0", timeout=TIMEOUT_MS)
    settle(page)
    top = page.evaluate("""() => document.getElementById('boot').getBoundingClientRect().top
        - document.querySelector('.app-scroll__wrap').getBoundingClientRect().top""", isolated_context=False)
    assert abs(top - under) <= 1, "the card is %s px under the top of the area, not %d" % (top, under)


def test_on_a_phone_a_settings_nav_click_lands_the_card_under_the_bar(app, app_session):
    """The phone's sub-navigation scrolls away with the page (it is not sticky); the window scrolls and a card stops
    below the sticky bar and 16 px more."""
    page = app_session
    go(app, page, "settings", 390, 500)
    assert page.locator(".snav").evaluate("e => getComputedStyle(e).position") == "static"
    page.locator(".snav").get_by_role("link", name="Boot-only values").click()
    page.wait_for_function("() => scrollY > 0", timeout=TIMEOUT_MS)
    settle(page)
    top = page.evaluate("document.getElementById('boot').getBoundingClientRect().top", isolated_context=False)
    assert abs(top - 118) <= 1, "the card is %s px from the top, under the bar and 16 px more" % top


@pytest.mark.parametrize(("width", "under"), ((1440, 16), (768, 0)))
def test_the_settings_navigation_sticks_under_the_top_of_the_area(app, app_session, width, under):
    page = app_session
    go(app, page, "settings", width, 400)
    scroll_page_to(page, 300)
    settle(page)
    assert state(page)["top"] > 150
    top = page.evaluate("""() => document.querySelector('.snav').getBoundingClientRect().top
        - document.querySelector('.app-scroll__wrap').getBoundingClientRect().top""", isolated_context=False)
    assert abs(top - under) <= 1, "the navigation is %s px under the top of the area, not %d" % (top, under)


# ---- the popups that follow their trigger -------------------------------------------------------------------------

def gap(page, trigger, popup) -> float:
    """The space between the trigger and its popup, whether the popup hangs under it or (no room below) over it."""
    t, p = trigger.bounding_box(), popup.bounding_box()
    if p["y"] >= t["y"]:
        return round(p["y"] - (t["y"] + t["height"]), 1)
    return round(t["y"] - (p["y"] + p["height"]), 1)


def test_an_open_filter_list_follows_its_trigger_while_the_area_scrolls(app, app_session, db):
    page = app_session
    key = new_key(db)
    for n in range(3):
        done_job(db, key, new_account(db, label="a%d" % n, checked=True), ref="r%d" % n)
    go(app, page, "jobs", 1440, 420)
    trigger = page.get_by_role("combobox", name="Status", exact=True)
    trigger.click()
    popup = page.get_by_role("listbox")
    expect(popup).to_be_visible()
    before = gap(page, trigger, popup)
    assert before == 6, before
    assert state(page)["wrapRoom"] > 40, "the Jobs page must scroll for this to prove anything"
    scroll_page_to(page, 40)
    settle(page)
    assert state(page)["top"] == 40
    assert gap(page, trigger, popup) == 6, "the list stayed where it was while its trigger moved"


def test_an_open_row_menu_follows_its_trigger_while_the_area_scrolls(app, app_session, db):
    page = app_session
    for n in range(3):
        new_account(db, label="acct %d" % n, checked=True)
    go(app, page, "accounts", 1440, 420)
    trigger = page.get_by_role("button", name="More actions for acct 0")
    trigger.click()
    popup = page.get_by_role("menu", name="Account actions")
    expect(popup).to_be_visible()
    before = gap(page, trigger, popup)
    assert state(page)["wrapRoom"] > 40, "the Accounts page must scroll for this to prove anything"
    scroll_page_to(page, 40)
    settle(page)
    assert state(page)["top"] == 40
    assert gap(page, trigger, popup) == before, "the menu stayed where it was while its trigger moved"


# ---- the flow layout shows the whole page, and the same page ---------------------------------------------------------

SAMPLE_JS = """() => {
  const els = [document.querySelector('main'), ...document.querySelectorAll('main *')];
  const props = ['color', 'backgroundColor', 'fontFamily', 'fontSize', 'fontWeight', 'paddingTop', 'paddingRight',
                 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth',
                 'borderLeftWidth', 'borderTopColor', 'display', 'width'];
  const step = Math.max(1, Math.floor(els.length / 40)), out = [];
  for (let i = 0; i < els.length && out.length < 40; i += step) {
    const s = getComputedStyle(els[i]);
    out.push([els[i].tagName + '.' + els[i].className, ...props.map((p) => s[p])]);
  }
  return out;
}"""
MEASURE_JS = """() => {
  const w = document.querySelector('.app-scroll__wrap'), m = document.querySelector('main').getBoundingClientRect();
  return {bar: document.querySelector('header.topbar').offsetHeight, wrap: w.scrollHeight,
          doc: document.documentElement.scrollHeight, left: m.left, width: m.width};
}"""
PAGES = ("jobs", "accounts", "settings", "test")


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("route", PAGES)
@mutation(SHELL, "@media (max-width: 767px), print {", "@media (max-width: 767px) {", expect="AssertionError")
@mutation(SHELL, FLOW_ROOT, FLOW_HIDDEN,
          expect="overflow stays visible up the chain")
def test_the_flow_layout_is_the_same_page_in_one_block(app, app_session, db, route, width):
    """What a whole-page photo shows (the flow layout) is the page the scroll area holds: the bar and the area's content
    add up to the document's height (a page shorter than the window clamps to it in both), `main` sits in the same place
    and a sample of 40 elements has the same computed style."""
    page = app_session
    key = new_key(db)
    for n in range(4):
        done_job(db, key, new_account(db, label="b%d" % n, checked=True), ref="q%d" % n)
    go(app, page, route, width)
    inside = page.evaluate(MEASURE_JS, isolated_context=False)
    styles_inside = page.evaluate(SAMPLE_JS, isolated_context=False)
    with flow(page):
        settle(page)
        around = page.evaluate(MEASURE_JS, isolated_context=False)
        styles_around = page.evaluate(SAMPLE_JS, isolated_context=False)
        shape = page.evaluate("""() => { const w = document.querySelector('.app-scroll__wrap');
          return [getComputedStyle(w.parentElement).overflowY, getComputedStyle(w).overflowY, getComputedStyle(
          document.querySelector('.app-shell')).overflowY]; }""", isolated_context=False)
    assert shape == ["visible"] * 3, "overflow stays visible up the chain, or sticky breaks: %r" % shape
    assert inside["bar"] + inside["wrap"] == around["doc"], (inside, around)
    assert abs(inside["left"] - around["left"]) <= 1 and abs(inside["width"] - around["width"]) <= 1, (inside, around)
    assert len(styles_inside) >= 20, "the sample is too small to mean anything: %d" % len(styles_inside)
    assert styles_inside == styles_around, [
        (a, b) for a, b in zip(styles_inside, styles_around, strict=True) if a != b][:3]


def test_a_page_shorter_than_the_window_keeps_the_window_height_in_the_flow_layout(app, app_session):
    page = app_session
    go(app, page, "test", 1440, 1200)
    with flow(page):
        settle(page)
        assert page.evaluate("document.documentElement.scrollHeight", isolated_context=False) == 1200


def test_the_flow_layout_is_not_a_test_hook(app, app_session):
    """The same rule set serves a phone and print: at 390 px the layout is already the flow layout."""
    page = app_session
    go(app, page, "settings", 390)
    before = page.evaluate(MEASURE_JS, isolated_context=False)
    with flow(page):
        settle(page)
        assert page.evaluate(MEASURE_JS, isolated_context=False) == before


@mutation(SIDEWAYS, "- w.clientWidth : 0", "- w.clientWidth * 1e9 : 0", expect="a 3000 px box")
def test_a_sideways_overflow_is_seen_in_the_scroll_area_layout(app, app_session):
    """The sideways reading can fail: a 3000 px box planted in the page overflows the scroll area, not the document."""
    page = app_session
    go(app, page, "settings", 1440)
    assert sideways(page) <= 0
    page.evaluate("""() => { const d = document.createElement('div'); d.style.width = '3000px'; d.style.height = '10px';
      document.querySelector('main').appendChild(d); }""", isolated_context=False)
    assert sideways(page) > 1000, "a 3000 px box in a 1440 px window must show as sideways scroll"
