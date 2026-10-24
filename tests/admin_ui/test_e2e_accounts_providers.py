"""The provider side of the Accounts page in Chrome against the real app with the four real provider specs (browser
tier): the Provider chips of the list are the page's provider filter (no provider strip under the top bar), the Show
chips open on Needs a look, and each row carries its health pill and reason, its provider chip, its pools as meters,
its proxy and the last check. At a desktop width, a tablet's 820 px and a phone's 390 px, where no text may be cut.
With RELAY_SCREENSHOTS_DIR set, each state is saved as accounts-<width>-<pick>.png."""

import os
import re
from pathlib import Path

import pytest
from accountkit import new_account
from page_helpers import settle, sideways
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")

# a text the page cuts off has more width than its box shows; a `.clip` text or a name button ends in "…" by design and
# carries the whole text as its title
CUT_JS = """() => [...document.querySelectorAll('[data-testid=accounts-table] *, .head *, .av-toolbar *')]
    .filter((el) => el.offsetParent && !el.closest('.track, .asc__row, .sr-only'))
    .filter((el) => !el.matches('.clip, .at__open, .t-sub') || !el.title)
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
    if SHOTS:
        settle(page)
        page.screenshot(path=str(Path(SHOTS) / ("accounts-%s.png" % name)))


def no_cut_text(page) -> None:
    assert page.evaluate(CUT_JS, isolated_context=False) == []
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


def row(page, label):
    return page.locator(".acct-row").filter(has=page.get_by_role("button", name=label, exact=True))


@pytest.mark.parametrize("width", [1440, 820, 390])
def test_the_accounts_list_is_filtered_by_its_show_and_provider_chips(app, app_page, live, db, real_providers, width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 1000 if width > 767 else 844})
    plant(db)
    app.sign_in(page)
    for provider in ("meta", "vibes"):
        claims(page, provider, True)
    page.goto(app.url + "#/accounts")
    expect(page.get_by_role("navigation", name="Provider")).to_have_count(0)        # no strip on this page

    # Needs a look first: the dead one, then the asleep one
    expect(page.locator(".acct-row")).to_have_count(2)
    expect(page.locator(".acct-row .acct-pill")).to_have_text(["Dead", re.compile(r"^Asleep (until|·) \d\d:\d\d$")])
    no_cut_text(page)
    shot(page, "%d-look" % width)

    page.get_by_role("button", name=re.compile(r"^All (· )?7$")).click()
    expect(page.locator(".acct-row")).to_have_count(7)
    expect(row(page, "flow-g2").locator(".acct-pill")).to_have_text("Paused")
    expect(row(page, "vibes-01").locator(".acct-pill")).to_have_text("Switched off")
    expect(row(page, "dola-02")).to_contain_text("account.dead_session")
    expect(row(page, "dola-05")).to_contain_text("dola.likeness_refused")
    if width == 1440:
        dola = row(page, "dola-07")
        expect(dola.locator(".d-meter")).to_have_count(2)
        expect(dola.locator(".d-meter .small").nth(1)).to_have_text(re.compile(r"^2 / "))
        expect(dola).to_contain_text("203.0.113.11:8031")
        expect(dola.locator(".check-line")).to_contain_text("ok")
        expect(row(page, "dola-02").locator(".check-line")).to_contain_text("failed")
    no_cut_text(page)
    shot(page, "%d-all" % width)

    # the provider chips filter the page (a phone picks it in a sheet); a second pick clears it
    if width == 390:
        page.get_by_role("button", name="Filter by provider").click()
        page.get_by_test_id("accounts-filter-sheet").get_by_role("button", name="dola", exact=True).click()
        page.get_by_role("button", name="Done").click()
    else:
        page.get_by_role("group", name="Provider").get_by_role("button", name="dola", exact=True).click()
    expect(page.locator(".acct-row")).to_have_count(3)
    no_cut_text(page)
    shot(page, "%d-dola" % width)
    if width != 390:
        page.get_by_role("group", name="Provider").get_by_role("button", name="flow", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="flow is paused in Settings.")).to_be_visible()
        page.get_by_role("group", name="Provider").get_by_role("button", name="flow", exact=True).click()
        expect(page.locator(".acct-row")).to_have_count(7)
