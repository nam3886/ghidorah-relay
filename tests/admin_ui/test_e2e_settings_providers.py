"""The provider cards of the Settings page in Chrome against the real app with the four real provider specs (browser
tier): the strip offers All providers, General and each provider; a provider's card has its "Taking jobs" switch, a
row per model with its switch and price inputs, its basic settings and the rest folded under Advanced; Save writes
what the card changed. At a desktop width and at a phone's 390 px, where no text may be cut. With
RELAY_SCREENSHOTS_DIR set, each state is saved as settings-<width>-<pick>.png."""

import json
import os
import re
from pathlib import Path

import pytest
from page_helpers import settle, sideways
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")

CUT_JS = """() => [...document.querySelectorAll('.scard *, .ptab')]
    .filter((el) => el.offsetParent && !el.closest('.hint-tip'))
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible')
    .map((el) => el.className + ': ' + el.textContent.trim().slice(0, 40))"""


def shot(page, name: str) -> None:
    if SHOTS:
        settle(page)
        page.screenshot(path=str(Path(SHOTS) / ("settings-%s.png" % name)))
        cards = page.locator(".scard")
        if cards.count() > 1:
            cards.nth(1).scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            page.screenshot(path=str(Path(SHOTS) / ("settings-%s-cards.png" % name)))
        page.locator("h1").first.scroll_into_view_if_needed()


def no_cut_text(page) -> None:
    assert page.evaluate(CUT_JS, isolated_context=False) == []
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


def stored(db, key: str):
    row = db.one("SELECT value FROM settings_kv WHERE key = %s", key)
    return None if row is None else (json.loads(row[0]) if isinstance(row[0], str) else row[0])


@pytest.mark.parametrize("width", [1440, 390])
def test_the_provider_cards_edit_the_models_prices_and_claims_and_save_them(app, app_page, live, db, real_providers,
                                                                          width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 1000 if width > 767 else 844})
    app.sign_in(page)
    page.goto(app.url + "#/settings")
    strip = page.get_by_role("navigation", name="Provider")
    expect(strip.get_by_test_id("provider-tab-all")).to_have_text("All providers")
    expect(strip.get_by_test_id("provider-tab-general")).to_be_visible()
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)
    expect(page.locator(".scard h2")).to_have_text(["General", "dola", "meta", "vibes", "flow"])

    dola = page.get_by_test_id("settings-card-dola")
    expect(dola.locator(".scard__sub")).to_contain_text("models on")
    expect(dola.locator(".mrow[data-model='seedance-2.5'] .price")).to_have_count(5)
    expect(dola.locator("details.adv")).not_to_have_attribute("open", "")
    expect(page.get_by_test_id("settings-card-meta").locator(".scard__claims")).to_contain_text("Paused")
    no_cut_text(page)
    shot(page, "%d-all" % width)

    strip.get_by_test_id("provider-tab-dola").click()
    expect(page.locator(".scard")).to_have_count(1)
    expect(page.locator("#password")).to_have_count(0)
    dola.get_by_role("switch", name="Offer seedance-1.0").click()
    dola.get_by_label("seedream-4.5 points price").fill("2")
    expect(dola.locator(".scard__changed")).to_have_text("2 changes not saved")
    expect(dola.locator(".mrow[data-model='seedance-1.0']")).to_have_class(re.compile(r"\bis-off\b"))
    no_cut_text(page)
    shot(page, "%d-dola" % width)
    dola.get_by_role("button", name="Save dola", exact=True).click()
    expect(page.locator(".el-message--success")).to_contain_text("Saved")
    expect(dola.locator(".scard__changed")).to_have_count(0)
    assert stored(db, "dola.disabled_models") == ["seedance-1.0"]
    assert stored(db, "dola.prices")["seedream-4.5"] == 2

    # a price that is not a whole number is refused under the Models block, and nothing is written
    dola.get_by_label("seedream-4.5 points price").fill("1.5")
    dola.get_by_role("button", name="Save dola", exact=True).click()
    expect(dola.locator(".blk__key [role=alert]")).to_contain_text("(value)")
    assert stored(db, "dola.prices")["seedream-4.5"] == 2
    page.problems.clear()                                    # the 400 is a console line
    dola.get_by_role("button", name="Discard dola changes", exact=True).click()
    expect(dola.get_by_label("seedream-4.5 points price")).to_have_value("2")

    strip.get_by_test_id("provider-tab-general").click()
    expect(page.locator(".scard h2")).to_have_text(["General"])
    expect(page.locator("#password")).to_be_visible()
    no_cut_text(page)
    shot(page, "%d-general" % width)
