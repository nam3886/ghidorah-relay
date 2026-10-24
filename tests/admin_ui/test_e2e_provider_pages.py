"""The provider side of API keys, Request logs, Alerts, Prompt frames and Test in Chrome against the real app with the
four real provider specs (browser tier). Keys, Request logs and Alerts have the strip with "All" as their provider
filter; Prompt frames and Test have the strip of the providers alone, which picks the one provider they work on. Rows
carry their provider's tile (a grey "relay" tile for none). At a desktop's 1440 px, a tablet's 820 px and a phone's
390 px no text is cut and the page never scrolls sideways. With RELAY_SCREENSHOTS_DIR set, each state is saved as
<page>-<width>-<state>.png (the top of the page, then the list scrolled into view)."""

import json
import os
import re
from pathlib import Path

import pytest
from page_helpers import click_tab, settle, sideways
from patchright.sync_api import expect
from test_admin_alert_groups import plant_unclear

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")
WIDTHS = [1440, 820, 390]                # desktop, tablet, phone


def size(page, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 1000 if width > 767 else 844})


def setting(page, key: str, value) -> None:
    """Write an admin setting through the admin API, as the Settings page does."""
    status = page.evaluate("""async ([key, value]) => {
        const token = sessionStorage.getItem('relay_admin_token');
        const r = await fetch('/admin/settings/' + key, {method: 'PUT', credentials: 'same-origin',
            headers: {'X-Admin-Session': token, 'Content-Type': 'application/json'},
            body: JSON.stringify({value})});
        return r.status;
    }""", [key, value], isolated_context=False)
    assert status == 200, (key, status)


def open_tab(page, label: str) -> None:
    click_tab(page, label)
    expect(page.get_by_role("heading", name=label, level=1)).to_be_visible()


def strip(page):
    return page.get_by_role("navigation", name="Provider")


def shot(page, name: str, below: str | None = None) -> None:
    """The top of the page, then (`below`) that element scrolled into view: the viewport only, as the sweep does."""
    if not SHOTS:
        return
    settle(page)
    page.screenshot(path=str(Path(SHOTS) / ("%s.png" % name)))
    if below:
        page.locator(below).first.scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        page.screenshot(path=str(Path(SHOTS) / ("%s-list.png" % name)))
        page.locator("h1").first.scroll_into_view_if_needed()


# what may end in an ellipsis (a title, a route, a key label: each with its whole text in a title attribute), what is
# spoken only, and el-table's hidden measuring copy of the columns
ELLIPSIS_OK = (".sr-only, .l-route, .l-key, .l-job-link, .p-req__route, .p-req__title, .hidden-columns, .asc__row, "
               ".row-open, .job-link, .t-job, .t-sub, .t-req, .p-row__route, .p-row__sub")


def no_cut_text(page, sel: str) -> None:
    """Nothing in `sel` or the strip is cut (bar ELLIPSIS_OK), and no sideways scroll."""
    cut = page.evaluate("""([sel, ok]) => [...document.querySelectorAll(sel + ', .ptab, .ptab *')]
        .filter((el) => el.offsetParent && !el.closest(ok))
        .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible')
        .map((el) => el.className + ': ' + el.textContent.trim().slice(0, 40))""", [sel, ELLIPSIS_OK],
                        isolated_context=False)
    assert cut == [], cut
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


def label_key(db, label: str, models: list[str], wallets):
    k = db.key(*wallets)
    db.run("UPDATE api_keys SET label = %s, models = %s WHERE id = %s", label, models, k)
    return k


def log(db, key, provider, route: str, status: int, error=None) -> None:
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms, error_code, body_redacted, "
           "at) VALUES (%s, %s, %s, %s, 42, %s, %s, clock_timestamp())", key, provider, route, status, error,
           json.dumps({}))


@pytest.mark.parametrize("width", WIDTHS)
def test_keys_logs_and_alerts_filter_by_the_strip_and_draw_each_provider(app, app_page, live, db, real_providers,
                                                                        width):
    page = app_page
    size(page, width)
    studio = label_key(db, "studio-prod", ["*"], [("dola", "video", 40, None), ("vibes", "video", 100, None)])
    label_key(db, "agency-a", ["seedream-4.5", "meta-image", "meta-video", "flow-nano-banana-pro"],
              [("meta", "image", 50, None), ("flow", "image", 20, None)])
    label_key(db, "trial-oct", ["seedance-2.0", "seedance-1.0"], [("dola", "video", None, 5)])
    log(db, studio, "dola", "POST /v1/video/generations", 202)
    log(db, studio, "vibes", "POST /v1/video/generations", 202)
    log(db, studio, "meta", "POST /v1/chat/completions", 429, "quota.too_many_queued")
    log(db, studio, None, "GET /v1/models", 200)
    plant_unclear(db, studio, "Which style do you want?", provider="dola")
    plant_unclear(db, studio, "I can't make that video.", provider="meta")
    app.sign_in(page)
    setting(page, "dola.disabled_models", ["seedance-1.0"])
    page.reload()                    # the model catalog (paused models too) is read once per page load

    # API keys: a provider keeps the keys that may use its models, and only its models and
    # wallets; a paused model is greyed with "(paused)"
    open_tab(page, "API keys")
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)
    expect(page.locator(".keys-table tbody tr, .keys-table li.data-table__item")).to_have_count(3)
    paused = page.locator(".k-mg__paused").first
    expect(paused).to_have_text("seedance-1.0 (paused)")
    no_cut_text(page, ".keys-card *")
    shot(page, "keys-%d-all" % width, ".keys-card")
    strip(page).get_by_test_id("provider-tab-dola").click()
    expect(page.get_by_text("agency-a")).to_be_visible()
    expect(page.locator(".k-mg .ptile.p-meta")).to_have_count(0)
    no_cut_text(page, ".keys-card *")
    shot(page, "keys-%d-dola" % width, ".keys-card")

    # Request logs: its own provider chips (no strip) are the server's filter; a row carries its provider
    strip(page).get_by_test_id("provider-tab-all").click()
    open_tab(page, "Request logs")
    expect(page.locator("nav.pstrip")).to_have_count(0)
    page.get_by_test_id("logs-provider-all").click()
    table = page.get_by_test_id("logs-table")
    rows = table.locator("tbody tr, .p-row")
    expect(rows).to_have_count(4)
    expect(table.locator(".pchip")).to_have_count(3)             # the request no provider handled has no chip
    no_cut_text(page, ".lv-card *")
    shot(page, "logs-%d-all" % width, ".lv-card")
    page.get_by_test_id("logs-provider-meta").click()
    expect(rows).to_have_count(1)
    expect(table.locator(".pchip.p-meta")).to_have_count(1)
    shot(page, "logs-%d-meta" % width, ".lv-card")

    # Alerts: the groups and the live conditions carry their tiles; a provider keeps its own
    page.get_by_test_id("logs-provider-all").click()
    open_tab(page, "Alerts")
    strip(page).get_by_test_id("provider-tab-all").click()
    groups = page.locator('section[aria-label="Alert groups"] tr.g-row, section[aria-label="Alert groups"] .p-group')
    expect(groups).to_have_count(2)
    expect(page.get_by_test_id("alerts-live").locator(".ptile").first).to_be_visible()
    no_cut_text(page, ".groups *, .live *")
    shot(page, "alerts-%d-all" % width, 'section[aria-label="Alert groups"]')
    strip(page).get_by_test_id("provider-tab-dola").click()
    expect(groups).to_have_count(1)
    expect(groups.first).to_have_class(re.compile(r"\bp-dola\b"))
    shot(page, "alerts-%d-dola" % width, 'section[aria-label="Alert groups"]')


@pytest.mark.parametrize("width", WIDTHS)
def test_frames_and_test_work_on_the_one_provider_their_strip_picks(app, app_page, live, db, real_providers, width):
    page = app_page
    size(page, width)
    app.sign_in(page)
    setting(page, "dola.disabled_models", ["seedance-1.0"])
    page.reload()

    # Prompt frames: the providers alone, no All; each kind writes out its default with the markers picked out
    open_tab(page, "Prompt frames")
    expect(strip(page).get_by_test_id("provider-tab-all")).to_have_count(0)
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)
    strip(page).get_by_test_id("provider-tab-dola").click()
    expect(strip(page).get_by_test_id("provider-tab-dola")).to_have_attribute("aria-pressed", "true")
    video = page.locator("[data-frame-kind=video]")
    expect(video.get_by_test_id("frame-default-text").locator("em")).to_have_count(2)
    expect(video.locator(".kind__paused")).to_have_count(1)
    no_cut_text(page, ".frames *")
    shot(page, "frames-%d-dola" % width, "[data-frame-kind=video] .kind__table, [data-frame-kind=video] .kind__list")
    strip(page).get_by_test_id("provider-tab-vibes").click()
    expect(page.get_by_test_id("frames-verbatim")).to_contain_text("vibes sends the text as written")
    shot(page, "frames-%d-vibes" % width)

    # Test: the strip's provider (shared with Prompt frames) shows its models as chips by kind; a paused one is off
    api_key = live.call(live.env.new_key())
    open_tab(page, "Test")
    page.get_by_role("textbox", name="API key", exact=True).fill(api_key.raw)
    page.get_by_role("button", name="Load models").click()
    expect(page.get_by_test_id("test-model").first).to_be_visible()
    expect(strip(page).get_by_test_id("provider-tab-vibes")).to_have_attribute("aria-pressed", "true")
    strip(page).get_by_test_id("provider-tab-dola").click()
    expect(page.get_by_test_id("test-model").filter(has_text="Paused")).to_be_disabled()
    page.get_by_test_id("test-model").filter(has_text="Seedance 2.5").click()
    expect(page.get_by_test_id("test-chosen")).to_contain_text("2–4 points · 5–30 s · seedance-2.5")
    expect(page.get_by_test_id("test-model").filter(has_text="Seedance 2.5")).to_have_attribute("aria-pressed", "true")
    no_cut_text(page, ".models *")
    shot(page, "test-%d-dola" % width, ".form")
    strip(page).get_by_test_id("provider-tab-vibes").click()
    expect(page.get_by_test_id("test-chosen")).to_contain_text("vibes-")
    shot(page, "test-%d-vibes" % width)
