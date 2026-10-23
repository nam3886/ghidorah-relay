"""The Settings links of the General card in Chrome against the real app (browser tier). A click scrolls the page's
own scroll area (desktop, tablet) or the window (phone) and nothing else: no ancestor of the card, the shell included,
may be scrolled by script even though its overflow is hidden, and the top bar keeps its place. The scrollbar root,
which clips the page area, has no scroll range at all, so nothing can scroll it."""

import pytest
from page_helpers import settle
from patchright.sync_api import expect

from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

SETTINGS = "apps/admin-web/src/views/SettingsView.vue"
TIMEOUT_MS = 10_000
SECTIONS = (("Change password", "password"), ("Boot-only values", "boot"), ("Measured prices", "prices"),
            ("Fixed limits", "limits"), ("Containers", "containers"))
LINKS = '[data-testid="settings-card-core"] .scard__links'

# What every ancestor of the card except the page scroll area (and the window) has scrolled.
MOVED_JS = """(id) => {
  const wrap = document.querySelector('.app-scroll__wrap'), out = [];
  for (let e = document.getElementById(id).parentElement; e; e = e.parentElement) {
    if (e === wrap || e === document.documentElement) continue;
    if (e.scrollTop || e.scrollLeft) out.push(e.tagName + '.' + e.className + ' ' + e.scrollLeft + ',' + e.scrollTop);
  }
  if (scrollX) out.push('window x ' + scrollX);
  if (wrap.scrollLeft) out.push('wrap x ' + wrap.scrollLeft);
  return out;
}"""
# The scrollbar root clips the page area: it must have nothing to scroll, whatever the page holds.
RANGE_JS = """() => {
  const root = document.querySelector('.app-scroll'), sr = document.getElementById('password-ends');
  return {x: root.scrollWidth - root.clientWidth, y: root.scrollHeight - root.clientHeight,
          offsetParent: sr && sr.offsetParent && sr.offsetParent.className};
}"""
GEOMETRY_JS = """(id) => {
  const wrap = document.querySelector('.app-scroll__wrap');
  const r = (s) => document.querySelector(s).getBoundingClientRect();
  const card = document.getElementById(id);
  return {bar: r('header.topbar').left, barTop: r('header.topbar').top,
          cardTop: card.getBoundingClientRect().top, wrapTop: wrap.getBoundingClientRect().top,
          margin: parseFloat(getComputedStyle(card).scrollMarginTop),
          room: wrap.scrollHeight - wrap.clientHeight - wrap.scrollTop};
}"""
# A page wider than the window, so a sideways scroll of any ancestor has room to happen and the checks can fail.
WIDE_JS = """() => {
  const wide = document.createElement('div');
  wide.style.width = '3000px';
  wide.style.height = '1px';
  document.querySelector('.app-main').appendChild(wide);
}"""


def open_settings(app, page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(app.url + "#/settings")
    expect(page.locator("h1.settings__title")).to_be_visible()
    page.wait_for_load_state("networkidle")
    expect(page.locator("[aria-busy=true]")).to_have_count(0, timeout=TIMEOUT_MS)
    settle(page)


@pytest.mark.parametrize(("width", "height"), ((1440, 900), (1024, 768)))
def test_the_scrollbar_root_has_no_scroll_range(app, app_session, width, height):
    page = app_session
    for route in ("settings", "jobs", "accounts", "keys", "alerts", "test"):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(app.url + "#/" + route)
        page.wait_for_load_state("networkidle")
        expect(page.locator("[aria-busy=true]")).to_have_count(0, timeout=TIMEOUT_MS)
        settle(page)
        got = page.evaluate(RANGE_JS, isolated_context=False)
        assert got["x"] == 0 and got["y"] == 0, "%s: the scrollbar root has a scroll range: %r" % (route, got)
        if route == "settings":
            # Change password is first, so its screen-reader line is in view and no longer stretches the root: the
            # containing block is checked itself
            assert "app-scroll__view" in got["offsetParent"].split(), \
                "settings: the scrollbar root has a scroll range waiting: %r" % got


@pytest.mark.parametrize(("width", "height"), ((1440, 900), (1024, 768)))
@mutation(SETTINGS, "- (area ? area.getBoundingClientRect().top : 0) - anchor;",
          "- (area ? area.getBoundingClientRect().top : 0);", expect="px under the area top")
def test_a_settings_link_click_scrolls_only_the_page_area(app, app_session, width, height):
    page = app_session
    open_settings(app, page, width, height)
    page.evaluate(WIDE_JS, isolated_context=False)
    before = page.evaluate(GEOMETRY_JS, "settings", isolated_context=False)
    for name, card in SECTIONS:
        page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop = 0", isolated_context=False)
        settle(page)
        page.locator(LINKS).get_by_role("link", name=name).click()
        settle(page)
        got = page.evaluate(GEOMETRY_JS, card, isolated_context=False)
        assert page.evaluate(MOVED_JS, card, isolated_context=False) == [], "%s scrolled something else" % name
        assert (got["bar"], got["barTop"]) == (before["bar"], before["barTop"]), "%s shifted the layout: %r" % (
            name, got)
        if got["room"] > 1:
            want = got["wrapTop"] + got["margin"]
            assert abs(got["cardTop"] - want) <= 1, "%s is not %s px under the area top: %r" % (
                name, got["margin"], got)
        expect(page.locator(LINKS + " a[aria-current=true]")).to_have_text(name)


def test_on_a_phone_a_settings_link_click_scrolls_only_the_window(app, app_session):
    page = app_session
    open_settings(app, page, 390, 700)
    page.evaluate(WIDE_JS, isolated_context=False)
    for name, card in SECTIONS:
        page.evaluate("scrollTo(0, 0)", isolated_context=False)
        settle(page)
        page.locator(LINKS).get_by_role("link", name=name).click()
        settle(page)
        assert page.evaluate(MOVED_JS, card, isolated_context=False) == [], "%s scrolled something else" % name
        expect(page.locator(LINKS + " a[aria-current=true]")).to_have_text(name)
