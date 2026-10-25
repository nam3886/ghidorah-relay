"""The arrow scroller's rows in Chrome against the real app (browser tier). A vertical wheel over a sideways row
scrolls it sideways (the dola video price table of Settings at a tablet width, the Settings areas chip row): the page
holds still while the row moves, and once the row is at its end the wheel scrolls the page on (wheel-scroll.ts,
v-wheel-x). Every row's first and last item sit inside its padding box at the two ends, no row shows a scrollbar, and
the chosen segment of the Test page's Ratio control is inset on its track."""

import dataclasses
import os
from pathlib import Path

import pytest
from page_helpers import WRAP, click_tab, settle
from patchright.sync_api import expect
from test_e2e_settings_frames_test import open_test_tab

from relay_core.providers import check_catalog
from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")
SCROLLER = "apps/admin-web/src/components/shared/ArrowScroller.vue"
TIMEOUT_MS = 10_000

# the row's sideways position and room, and the page's position (the scroll area's wrap, or the window on a phone)
STATE_JS = """e => { const w = document.querySelector('%s');
  return {left: e.scrollLeft, room: e.scrollWidth - e.clientWidth, page: (w ? w.scrollTop : 0) + scrollY}; }""" % WRAP

# Every visible scroller row that scrolls: how far its first item is inside the padding box at the start and its last
# item at the end (negative: cut), whether it draws a scrollbar, and its arrows at each end once they have settled.
ENDS_JS = """async () => {
  const frames = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 200))));
  const out = [];
  for (const e of document.querySelectorAll('.asc:not(.asc--wrap) > .asc__row')) {
    const kids = [...e.children].filter(k => !k.classList.contains('asc__sentinel'));
    if (!kids.length || !e.offsetParent || e.getBoundingClientRect().width === 0) continue;
    const s = getComputedStyle(e), pl = parseFloat(s.paddingLeft), pr = parseFloat(s.paddingRight);
    const inner = () => { const r = e.getBoundingClientRect();
      return {l: r.left + e.clientLeft + pl, r: r.left + e.clientLeft + e.clientWidth - pr}; };
    const was = e.scrollLeft;
    e.scrollLeft = 0; await frames();
    const first = kids[0].getBoundingClientRect().left - inner().l;
    const startArrow = e.parentElement.classList.contains('asc--l');
    e.scrollLeft = 1e7; await frames();
    const last = inner().r - kids[kids.length - 1].getBoundingClientRect().right;
    const endArrow = e.parentElement.classList.contains('asc--r');
    e.scrollLeft = was;
    out.push({label: e.getAttribute('aria-label'), first, last, startArrow, endArrow,
              bar: e.offsetHeight - e.clientHeight - parseFloat(s.borderTopWidth) - parseFloat(s.borderBottomWidth)});
  }
  return out;
}"""


def open_settings(app, page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    app.sign_in(page)
    page.goto(app.url + "#/settings")
    expect(page.locator("#settings [data-state=ready]")).to_be_visible()
    page.wait_for_load_state("networkidle")
    settle(page)


def dola_tab(page) -> None:
    page.get_by_role("navigation", name="Settings areas").get_by_test_id("settings-tab-dola").click()
    expect(page.get_by_test_id("model-prices-dola")).to_be_visible()
    page.wait_for_load_state("networkidle")
    settle(page)


def shot(page, locator, name: str) -> None:
    if SHOTS:
        settle(page)
        locator.screenshot(path=str(Path(SHOTS) / ("scroller-%s.png" % name)))


def ends_inside(page, where: str) -> list:
    rows = page.evaluate(ENDS_JS, isolated_context=False)
    for r in rows:
        assert r["first"] >= -0.5 and r["last"] >= -0.5, "%s: an end item is cut by its row: %r" % (where, r)
        assert not r["startArrow"] and not r["endArrow"], "%s: an arrow stays at its end: %r" % (where, r)
        assert r["bar"] <= 0, "%s: the row draws a scrollbar: %r" % (where, r)
    return rows


# ---- the wheel ----------------------------------------------------------------------------------------------------

def state(row) -> dict:
    return row.evaluate(STATE_JS)


def wheel_over(page, row, dy: int) -> None:
    box, view = row.bounding_box(), page.viewport_size
    top, bottom = max(box["y"], 0), min(box["y"] + box["height"], view["height"])
    assert bottom - top > 20, "the row is on screen: %r" % box
    page.mouse.move(box["x"] + box["width"] / 2, (top + bottom) / 2)
    page.mouse.wheel(0, dy)
    page.wait_for_timeout(150)
    settle(page)


def sideways_then_on(page, row) -> None:
    """Down from the start: the row moves, the page does not. Down at the end: the page moves, the row does not."""
    before = state(row)
    assert before["room"] > 0 and before["left"] == 0, "the row overflows sideways and starts at 0: %r" % before
    wheel_over(page, row, 100)
    moved = state(row)
    assert moved["left"] > 0, "the wheel scrolled the row sideways: %r" % moved
    assert moved["page"] == before["page"], "the page held still while the row moved: %r -> %r" % (before, moved)
    row.evaluate("e => { e.scrollLeft = 1e7; }")
    at_end = state(row)
    wheel_over(page, row, 100)
    after = state(row)
    assert after["left"] == at_end["left"], "the row stayed at its end: %r -> %r" % (at_end, after)
    assert after["page"] > at_end["page"], "at the end the wheel scrolled the page on: %r -> %r" % (at_end, after)


@mutation(SCROLLER, '<div ref="row" v-wheel-x class="asc__row"', '<div ref="row" class="asc__row"',
          expect="the wheel scrolled the row sideways")
def test_the_wheel_scrolls_the_video_price_table_sideways_at_a_tablet_width(app, app_page, live, real_providers):
    page = app_page
    open_settings(app, page, 1024, 500)
    dola_tab(page)
    table = page.get_by_test_id("model-prices-dola").get_by_role("group", name="Video prices")
    table.scroll_into_view_if_needed()
    page.evaluate("() => { const w = document.querySelector('%s'); if (w) w.scrollTop -= 20; }" % WRAP,
                  isolated_context=False)
    settle(page)
    sideways_then_on(page, table)


def test_the_wheel_scrolls_a_chip_row_sideways(app, app_page, live, real_providers):
    page = app_page
    open_settings(app, page, 820, 500)
    nav = page.get_by_role("navigation", name="Settings areas")
    nav.locator(".asc").evaluate("e => { e.style.maxWidth = '160px'; }")   # five chips in 160 px: the row overflows
    settle(page)
    sideways_then_on(page, nav.get_by_role("group", name="Settings areas"))


# ---- the two ends of every row --------------------------------------------------------------------------------------

@pytest.mark.parametrize("width", (1440, 1024, 390))
def test_every_settings_row_shows_its_end_items_whole_and_no_scrollbar(app, app_page, live, real_providers, width):
    page = app_page
    open_settings(app, page, width, 900)
    ends_inside(page, "settings general %d" % width)
    dola_tab(page)
    rows = ends_inside(page, "settings dola %d" % width)
    if width == 1024:
        assert any(r["label"] == "Video prices" for r in rows), rows
    prices = page.get_by_test_id("model-prices-dola")
    prices.scroll_into_view_if_needed()
    if prices.locator(".asc__row").count():          # a phone lists the models instead
        prices.locator(".asc__row").first.evaluate("e => { e.scrollLeft = 1e7; }")
    shot(page, prices, "settings-dola-prices-end-%d" % width)


def video_ratios(live) -> None:
    """Each video model of the fake provider names six ratios, so the Ratio control has seven segments."""
    video = ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9")
    registry = live.env.registry
    for name in registry.names():
        spec = registry.spec(name)
        listed = spec.catalog()
        spec.catalog = lambda listed=listed: [
            dataclasses.replace(m, limits={**m.limits, "ratios": list(video), "default_ratio": "16:9"})
            if m.media_type == "video" else m for m in listed]
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])


SEGMENT_JS = """e => { const s = getComputedStyle(e), r = e.getBoundingClientRect();
  const on = e.querySelector('[aria-pressed="true"]').getBoundingClientRect();
  const l = r.left + e.clientLeft + parseFloat(s.paddingLeft), t = r.top + e.clientTop + parseFloat(s.paddingTop);
  const ri = r.left + e.clientLeft + e.clientWidth - parseFloat(s.paddingRight);
  const b = r.top + e.clientTop + e.clientHeight - parseFloat(s.paddingBottom);
  return {left: on.left - l, top: on.top - t, right: ri - on.right, bottom: b - on.bottom}; }"""


@mutation(SCROLLER, "  margin-right: calc(-1px - var(--asc-gap));", "  margin-right: -9px;",
          expect="the chosen segment leaves")
@pytest.mark.parametrize("width", (1440, 390))
def test_the_chosen_ratio_segment_is_inset_on_its_track(app_session, live, db, width):
    page = app_session
    page.set_viewport_size({"width": width, "height": 900})
    video_ratios(live)
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    group = page.get_by_role("group", name="Ratio")
    group.scroll_into_view_if_needed()
    settle(page)
    for option in ("Default", "21:9", "Default"):
        group.locator("button[data-value]").filter(has_text=option).first.click()
        settle(page)
        inset = group.evaluate(SEGMENT_JS)
        assert min(inset.values()) >= -0.5, (
            "%s: the chosen segment leaves the track's padding box: %r" % (option, inset))
        if option == "21:9":
            shot(page, group.locator("xpath=../.."), "test-ratio-last-%d" % width)
    shot(page, group.locator("xpath=../.."), "test-ratio-%d" % width)
    ends_inside(page, "test %d" % width)


@pytest.mark.parametrize("width", (1440, 390))
def test_every_jobs_row_shows_its_end_items_whole(app_session, width):
    page = app_session
    page.set_viewport_size({"width": width, "height": 900})
    click_tab(page, "Jobs")
    page.wait_for_load_state("networkidle")
    settle(page)
    ends_inside(page, "jobs %d" % width)
