"""The provider side of the Accounts page in Chrome against the real app with the four real provider specs (browser
tier): the provider strip is the page's provider filter (no Provider list in the top bar), the "Jobs today" table sits
on top, and the accounts are drawn in one lane per provider with its coloured header, each row with its status pill,
in use, the switch, pool meters, the proxy check, the sleep and the last error. At a desktop width, a tablet's 820 px
and a phone's 390 px, where no text may be cut. With RELAY_SCREENSHOTS_DIR set, each state is saved as
accounts-<width>-<pick>.png."""

import os
import re
from pathlib import Path

import pytest
from accountkit import new_account
from page_helpers import settle, sideways
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")

CUT_JS = """() => [...document.querySelectorAll('.lane *, .ptab, [data-testid=provider-table] *')]
    .filter((el) => el.offsetParent && !el.closest('.spark, .mini__bar, .meter__track'))
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible')
    .map((el) => el.className + ': ' + el.textContent.trim().slice(0, 40))"""


def pool(db, account, name: str, left: int) -> None:
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at) "
           "VALUES (%s, %s, %s, clock_timestamp())", account, name, left)


def plant(db) -> None:
    a = new_account(db, provider="dola", label="dola-07", identity="ng@example.com", checked=True,
                    proxy=("203.0.113.11", 8031))
    pool(db, a, "video", 2)
    pool(db, a, "image", 31)
    db.run("UPDATE accounts SET proxy_ok = true, proxy_checked_at = clock_timestamp() WHERE id = %s", a)
    b = new_account(db, provider="dola", label="dola-05", identity="tr@example.com", checked=True)
    pool(db, b, "video", 0)
    db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '2 hours', cooldown_reason = "
           "'dola.likeness_refused', strikes = 1, last_error = 'render.watermarked_only' WHERE id = %s", b)
    c = new_account(db, provider="dola", label="dola-02", identity="ph@example.com", checked=True, state="dead",
                    proxy=("203.0.113.15", 8031))
    db.run("UPDATE accounts SET state_reason = 'account.dead_session', proxy_ok = false, "
           "proxy_checked_at = clock_timestamp() WHERE id = %s", c)
    m = new_account(db, provider="meta", label="meta-02", identity="vu@example.com", checked=True)
    pool(db, m, "image", 33)
    new_account(db, provider="vibes", label="vibes-03", identity="hi@example.com", checked=True)
    new_account(db, provider="vibes", label="vibes-01", identity="an@example.com", checked=True, state="disabled")
    new_account(db, provider="flow", label="flow-g2", identity="ga@example.com", checked=True)


def claims(page, provider: str, on: bool) -> None:
    status = page.evaluate("""async ([key, on]) => {
        const token = sessionStorage.getItem('relay_admin_token');
        const r = await fetch('/admin/settings/' + key, {method: 'PUT', credentials: 'same-origin',
            headers: {'X-Admin-Session': token, 'Content-Type': 'application/json'},
            body: JSON.stringify({value: on})});
        return r.status;
    }""", [provider + ".claims_enabled", on], isolated_context=False)
    assert status == 200, status


def shot(page, name: str) -> None:
    """The top of the page, then the lanes scrolled into view (the viewport only, as the sweep takes them)."""
    if SHOTS:
        settle(page)
        page.screenshot(path=str(Path(SHOTS) / ("accounts-%s.png" % name)))
        page.locator(".board").scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        page.screenshot(path=str(Path(SHOTS) / ("accounts-%s-lanes.png" % name)))
        page.locator("h1").first.scroll_into_view_if_needed()


def no_cut_text(page) -> None:
    assert page.evaluate(CUT_JS, isolated_context=False) == []
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


@pytest.mark.parametrize("width", [1440, 820, 390])
def test_the_accounts_are_drawn_in_provider_lanes_filtered_by_the_strip(app, app_page, live, db, real_providers,
                                                                       width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 1000 if width > 767 else 844})
    plant(db)
    app.sign_in(page)
    for provider in ("meta", "vibes"):
        claims(page, provider, True)
    page.goto(app.url + "#/accounts")
    expect(page.locator(".acct-row")).to_have_count(7)

    strip = page.get_by_role("navigation", name="Provider")
    expect(strip.get_by_test_id("provider-tab-all")).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)
    expect(page.get_by_test_id("provider-table")).to_be_visible()

    lanes = page.locator(".lane")
    expect(lanes.locator(".lane__name")).to_have_text(["dola", "meta", "vibes", "flow"])
    dola = page.get_by_test_id("lane-dola")
    expect(dola.locator(".lane__meta")).to_contain_text(re.compile(r"\d/3 usable"))
    expect(page.get_by_test_id("lane-flow").locator(".lane__meta")).to_contain_text("paused")
    row = dola.locator(".acct-row").filter(has_text="dola-07")
    expect(row.locator(".meter")).to_have_count(2)
    expect(row.locator(".meter__figure").first).to_contain_text("2 / ")
    expect(row).to_contain_text("203.0.113.11:8031")
    expect(row.locator(".mini-chip")).to_contain_text("ok")
    expect(row.get_by_role("switch")).to_have_attribute("aria-checked", "true")
    asleep = dola.locator(".acct-row").filter(has_text="dola-05")
    expect(asleep).to_contain_text("asleep until")
    expect(asleep).to_contain_text("render.watermarked_only")
    dead = dola.locator(".acct-row").filter(has_text="dola-02")
    expect(dead).to_have_class(re.compile(r"\bis-dead\b"))
    expect(dead.locator(".mini-chip")).to_contain_text("failed")
    expect(page.get_by_test_id("lane-vibes").locator(".acct-row").filter(has_text="vibes-01")
           .get_by_role("switch")).to_have_attribute("aria-checked", "false")
    no_cut_text(page)
    # the checked stamp and the made counts are one line of their own, at every width
    heights = page.locator(".foot-note").evaluate_all("els => els.map((e) => e.getBoundingClientRect().height)")
    assert heights and max(heights) < 22, heights
    shot(page, "%d-all" % width)

    # the strip filters the page; a lane's footer picks its provider as well
    strip.get_by_test_id("provider-tab-dola").click()
    expect(page.locator(".acct-row")).to_have_count(3)
    expect(lanes).to_have_count(1)
    no_cut_text(page)
    shot(page, "%d-dola" % width)
    strip.get_by_test_id("provider-tab-all").click()
    expect(page.locator(".acct-row")).to_have_count(7)
    page.get_by_test_id("lane-meta").get_by_role("button", name="All 1 meta accounts →").click()
    expect(strip.get_by_test_id("provider-tab-meta")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".acct-row")).to_have_count(1)
