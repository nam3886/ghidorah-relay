"""The Settings sub-navigation in Chrome against the real app (browser tier). A click scrolls the page's own scroll
area (desktop, tablet) or the window (phone) and nothing else: no ancestor of the card, the shell included, may be
scrolled by script even though its overflow is hidden, and the top bar and the navigation keep their place."""

import pytest
from patchright.sync_api import expect
from pixel_capture import settle

pytestmark = pytest.mark.browser

TIMEOUT_MS = 10_000
SECTIONS = (("Settings", "settings"), ("Boot-only values", "boot"), ("Measured prices", "prices"),
            ("Change password", "password"))

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
GEOMETRY_JS = """(id) => {
  const wrap = document.querySelector('.app-scroll__wrap'), r = (s) => document.querySelector(s).getBoundingClientRect();
  const nav = r('.snav');
  return {bar: r('header.topbar').left, snav: nav.left, snavTop: nav.top,
          snavShown: nav.width > 0 && nav.bottom > 0 && nav.top < innerHeight,
          cardTop: document.getElementById(id).getBoundingClientRect().top,
          room: wrap.scrollHeight - wrap.clientHeight - wrap.scrollTop};
}"""


def open_settings(app, page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(app.url + "#/settings")
    expect(page.locator("h1.settings__title")).to_be_visible()
    page.wait_for_load_state("networkidle")
    expect(page.locator("[aria-busy=true]")).to_have_count(0, timeout=TIMEOUT_MS)
    settle(page)


@pytest.mark.parametrize(("width", "height"), ((1440, 900), (1024, 768)))
def test_a_settings_nav_click_scrolls_only_the_page_area(app, app_session, width, height):
    page = app_session
    open_settings(app, page, width, height)
    before = page.evaluate(GEOMETRY_JS, "settings", isolated_context=False)
    for name, card in SECTIONS:
        page.locator(".snav").get_by_role("link", name=name).click()
        settle(page)
        got = page.evaluate(GEOMETRY_JS, card, isolated_context=False)
        assert page.evaluate(MOVED_JS, card, isolated_context=False) == [], "%s scrolled something else" % name
        assert got["snavShown"], "the navigation left the screen after %s: %r" % (name, got)
        assert (got["bar"], got["snav"]) == (before["bar"], before["snav"]), "%s shifted the layout: %r" % (name, got)
        if got["room"] > 1:
            assert got["cardTop"] >= got["snavTop"] - 1, "%s stopped above the navigation: %r" % (name, got)
        expect(page.locator(".snav a.is-current")).to_have_text(name)


def test_on_a_phone_a_settings_nav_click_scrolls_only_the_window(app, app_session):
    page = app_session
    open_settings(app, page, 390, 700)
    for name, card in SECTIONS:
        page.locator(".snav").get_by_role("link", name=name).click()
        settle(page)
        assert page.evaluate(MOVED_JS, card, isolated_context=False) == [], "%s scrolled something else" % name
        expect(page.locator(".snav a.is-current")).to_have_text(name)
