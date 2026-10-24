"""The provider tabs of the Settings page in Chrome against the real app with the four real provider specs (browser
tier): a row of tabs offers General and each provider (no provider strip); a provider's tab has its line, Taking jobs
with the claims switch, Models and prices (a row per model with its switch and its sale and cost inputs; below 968 px a
read-only table on a tablet and a row per model on a phone, each model edited in a bottom sheet), Pools and budgets
and the rest under Advanced; the page's bar saves every change. At a desktop width, a
tablet's 820 px and a phone's 390 px, in both themes, where no text may be cut. With RELAY_SCREENSHOTS_DIR set, the
page, a form open and the states are saved as settings-<width>-<theme>-<what>.png."""

import json
import os
import re
from pathlib import Path

import pytest
from page_helpers import flow, settle, sideways
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")
SIZES = ((1440, 1000), (820, 1100), (390, 844))

# Text the page cuts off: an element narrower than its content that does not let it show. A row of the arrow scroller
# scrolls by design (its arrows say so), the 6 px its arrows reach past its sides is no text, and a hint's own
# ellipsis has its whole text in the hint.
CUT_JS = """() => [...document.querySelectorAll('#settings *, .stab, .sbar *, .rl-sheet *')]
    .filter((el) => el.offsetParent && !el.closest('.hint-tip, .sr-only') && !el.matches('.asc__row'))
    .filter((el) => !(el.querySelector('.asc') && el.scrollWidth - el.clientWidth <= 6))
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible')
    .map((el) => el.className + ': ' + (el.value || el.textContent).trim().slice(0, 40))"""


def shot(page, name: str, whole: bool = True) -> None:
    if not SHOTS:
        return
    settle(page)
    path = str(Path(SHOTS) / ("settings-%s.png" % name))
    if not whole:
        page.screenshot(path=path)
        return
    # the whole page: a window as tall as the page laid out as one block
    size = page.viewport_size
    with flow(page):
        settle(page)
        tall = page.evaluate("document.documentElement.scrollHeight", isolated_context=False)
    page.set_viewport_size({"width": size["width"], "height": max(size["height"], tall)})
    settle(page)
    page.screenshot(path=path)
    page.set_viewport_size(size)
    settle(page)


def no_cut_text(page) -> None:
    cut = page.evaluate(CUT_JS, isolated_context=False)
    assert cut == [], cut
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


def stored(db, key: str):
    row = db.one("SELECT value FROM settings_kv WHERE key = %s", key)
    return None if row is None else (json.loads(row[0]) if isinstance(row[0], str) else row[0])


def open_settings(app, page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    app.sign_in(page)
    page.goto(app.url + "#/settings")
    expect(page.locator("#settings [data-state=ready]")).to_be_visible()
    page.wait_for_load_state("networkidle")
    settle(page)


def models_scope(page, card, model: str, sheet: bool):
    """Where a model's inputs are: its table row, or below 968 px the bottom sheet its Edit opens."""
    if not sheet:
        return card
    card.get_by_role("button", name="Edit %s prices" % model).click()
    sheet = page.get_by_role("dialog", name=model)
    expect(sheet).to_be_visible()
    return sheet


def close_sheet(page, sheet: bool) -> None:
    if sheet:
        done = page.get_by_role("dialog").get_by_role("button", name="Done")
        done.click()
        expect(done).to_be_hidden()
        settle(page)


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_provider_tabs_edit_models_prices_claims_and_pools_and_the_bar_saves_them(
        app, app_page, live, db, real_providers, width, height, color_scheme):
    page = app_page
    phone = width < 768
    sheet = width < 968                 # stacked: the models are read in a table or a list and edited in a sheet
    theme = "%d-%s" % (width, color_scheme)
    open_settings(app, page, width, height)
    tabs = page.get_by_role("navigation", name="Settings areas")
    expect(page.locator("nav.pstrip")).to_have_count(0)
    expect(tabs.locator(".stab")).to_have_text(["General", "dola", "meta", "vibes", "flow"])
    expect(tabs.get_by_test_id("settings-tab-general")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#password")).to_be_visible()
    no_cut_text(page)
    shot(page, "%s-general" % theme)

    tabs.get_by_test_id("settings-tab-meta").click()
    meta = page.get_by_test_id("settings-card-meta")
    expect(meta.locator(".banner")).to_contain_text("Taking jobs is off for meta.")
    expect(meta.locator(".claims")).to_contain_text("Paused")

    tabs.get_by_test_id("settings-tab-dola").click()
    dola = page.get_by_test_id("settings-card-dola")
    if not phone:                       # SettingsPhone draws no provider line
        expect(dola.locator(".pline")).to_contain_text("accounts")
    expect(page.locator("#password")).to_have_count(0)
    if not sheet:
        expect(dola.locator("tr[data-model='seedance-2.5'] .price")).to_have_count(5)
        expect(dola.locator("tr[data-model='seedance-2.5'] .cost")).to_have_count(5)
    elif not phone:                     # SettingsTablet: read-only, On/Off chips, the sale points as figures
        expect(dola.locator("tr[data-model='seedance-2.5'] .pill")).to_have_text("On")
        expect(dola.locator("tr[data-model='seedance-2.5'] input")).to_have_count(0)
    advanced = dola.locator("button.disclosure, button.adv-btn")
    expect(advanced).to_have_attribute("aria-expanded", "false")
    no_cut_text(page)
    shot(page, "%s-dola" % theme)

    scope = models_scope(page, dola, "seedance-1.0", sheet)
    scope.get_by_role("switch", name="Offer seedance-1.0").click()
    close_sheet(page, sheet)
    scope = models_scope(page, dola, "seedream-4.5", sheet)
    scope.get_by_label("seedream-4.5 points price").fill("2")
    if phone:
        no_cut_text(page)
        shot(page, "%s-form-sheet" % theme, whole=False)
    close_sheet(page, sheet)
    dola.get_by_label(re.compile(r"^Video \w+ per day$")).fill("5")
    bar = page.get_by_role("region", name="Unsaved changes")
    expect(bar).to_contain_text("3 unsaved changes")
    if not phone:
        expect(bar).to_contain_text("seedance-1.0 offered: on → off")
    if phone:
        expect(dola.locator("li[data-model='seedance-1.0']")).to_have_class(re.compile(r"\bis-off\b"))
    else:
        expect(dola.locator("tr[data-model='seedance-1.0']")).to_have_class(re.compile(r"\bis-off\b"))
    advanced.click()
    expect(dola.locator(".adv-fields")).to_be_visible()
    no_cut_text(page)
    shot(page, "%s-form-edited" % theme)
    if phone:
        box = bar.get_by_role("button", name="Save changes").bounding_box()
        assert box["height"] >= 43.5, box
    bar.get_by_role("button", name="Save changes").click()
    expect(page.locator(".el-message--success")).to_contain_text("Saved")
    expect(bar).to_have_count(0)
    assert stored(db, "dola.disabled_models") == ["seedance-1.0"]
    assert stored(db, "dola.prices")["seedream-4.5"] == 2
    assert stored(db, "dola.pools")["video"]["windows"][0]["budget"]["free"] == 5

    # a price that is not a whole number is refused under the Models block, and nothing is written
    scope = models_scope(page, dola, "seedream-4.5", sheet)
    scope.get_by_label("seedream-4.5 points price").fill("1.5")
    close_sheet(page, sheet)
    bar.get_by_role("button", name="Save changes").click()
    expect(dola.locator(".mp__foot [role=alert]")).to_contain_text("(value)")
    assert stored(db, "dola.prices")["seedream-4.5"] == 2
    page.problems.clear()                                    # the 400 is a console line
    no_cut_text(page)
    shot(page, "%s-refused" % theme)
    bar.get_by_role("button", name="Discard changes").click()
    expect(bar).to_have_count(0)
    scope = models_scope(page, dola, "seedream-4.5", sheet)
    expect(scope.get_by_label("seedream-4.5 points price")).to_have_value("2")
    close_sheet(page, sheet)


STATES = (
    ("error", 500, {"error": {"code": "internal.unexpected", "message": "the settings are not there"}}),
    ("empty", 200, {"items": [], "env_only": [], "fixed": [], "containers": []}),
)


def answer(status: int, body: dict):
    """A route handler that answers the settings read with this status and body (one argument: the route)."""
    return lambda route: route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_states_fit_each_width_in_both_themes(app, app_page, width, height, color_scheme):
    """A failed read (with Retry, a finger target on a phone), an empty one and the first read's bars."""
    page = app_page
    theme = "%d-%s" % (width, color_scheme)
    page.set_viewport_size({"width": width, "height": height})
    app.sign_in(page)
    for name, status, body in STATES:
        route = re.compile(r"/admin/settings$")
        page.route(route, answer(status, body))
        page.goto(app.url + "#/settings")
        page.reload()
        box = page.locator("#settings [data-state=%s]" % name)
        expect(box).to_be_visible()
        if name == "error":
            expect(box).to_contain_text("Could not load the settings")
            retry = box.get_by_role("button", name="Retry")
            if width < 768:
                b = retry.bounding_box()
                assert b["width"] >= 43.5 and b["height"] >= 43.5, b
            page.problems.clear()                            # the 500 is a console line
        else:
            expect(box).to_contain_text("No settings")
        no_cut_text(page)
        shot(page, "%s-state-%s" % (theme, name))
        page.unroute(route)
    # the first read: the settings answer late, the sections wait as flat bars
    page.route(re.compile(r"/admin/settings$"), lambda r: None)
    page.goto(app.url + "#/settings")
    page.reload()
    expect(page.locator("#settings [data-state=loading]")).to_be_visible()
    shot(page, "%s-state-loading" % theme)
