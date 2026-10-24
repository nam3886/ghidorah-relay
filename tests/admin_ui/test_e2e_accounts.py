"""The Accounts tab of the Vue admin in Chrome against the real app (browser tier): the list, the row More menu, the
detail drawer, import, edit and its Taking jobs switch, the commands the worker would run, the screenshot blob,
auto-refresh, the list at every width and server strings as text. Every test runs under the CSP watch of
`conftest.CspWatch`. The app is opened by `app.url` (`/manage`)."""

import json
import re

import pytest
from accountkit import new_account
from page_helpers import sideways
from patchright.sync_api import expect

from relay_core import crypto

pytestmark = pytest.mark.browser

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001"
    "e221bc330000000049454e44ae426082")
RECHECK = "Re-check account · R"
CLOSE = "Close · Esc"


def open_accounts(page):
    page.get_by_role("tab", name="Accounts", exact=True).click()
    expect(page.get_by_role("heading", name="Accounts", exact=True)).to_be_attached()


def rows(page):
    return page.locator(".acct-row")


def header_import(page):
    """The page header's Import (a text button on a desktop and a tablet, an icon button on a phone)."""
    return page.get_by_role("button", name=re.compile(r"^Import( accounts)?$")).first


def more(row, name):
    """A row action from the row's More menu (Shot, Wake, Edit, Delete); the menu is drawn in the page body."""
    row.get_by_role("button", name=re.compile(r"^More actions for ")).click()
    row.page.get_by_role("menuitem", name=name, exact=True).click()


def show_all(page):
    """The Show chip All (the list opens on Needs a look while something needs one)."""
    page.get_by_role("button", name=re.compile(r"^All (· )?\d+$")).click()


def detail(page, label):
    """The account's drawer, opened by its name in the list."""
    rows(page).filter(has_text=label).get_by_role("button", name=label, exact=True).click()
    box = page.get_by_test_id("account-drawer")
    expect(box).to_be_visible()
    return box


@pytest.fixture
def pair(monkeypatch):
    """An import seals each credential with the public key, so the api needs one."""
    keys = crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)
    return keys


def lease(db, account):
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'check', 'w1', "
           "clock_timestamp() + interval '1 hour', clock_timestamp() + interval '2 hours')", account)


def patches(page):
    """The JSON bodies of the PATCH /admin/accounts calls the page makes, in order."""
    sent = []
    page.on("request", lambda r: sent.append(r.post_data_json) if r.method == "PATCH" and "/admin/accounts/" in r.url
            else None)
    return sent


def test_the_list_says_in_use_and_shows_server_strings_as_text(app, app_session, db):
    page = app_session
    busy = new_account(db, label="busy one", checked=True)
    lease(db, busy)
    bad = new_account(db, label=app.planted, checked=True)
    db.run("UPDATE accounts SET display_name = %s, state_reason = %s, last_error = %s WHERE id = %s",
           app.planted, app.planted, app.planted, bad)
    open_accounts(page)
    expect(rows(page)).to_have_count(2)
    expect(rows(page).filter(has_text="busy one").locator(".acct-pill")).to_have_text("In use")
    expect(page.get_by_role("switch")).to_have_count(0)                 # Taking jobs is in Edit, not in the list
    page.wait_for_timeout(300)                            # a script in the planted markup would have run by now
    app.assert_planted_as_text(page)


def test_import_shows_the_accepted_and_the_rejected_lines_and_sends_the_proxy_with_every_one(
        app, app_session, db, pair):
    page = app_session
    open_accounts(page)
    header_import(page).click()
    box = page.get_by_role("dialog")
    result = box.get_by_role("status", name="Result of the previous import")
    box.get_by_label("Credentials").fill("cookie-one\ncookie-two")
    expect(box).to_contain_text("2 lines")
    box.get_by_label("Proxy for all").fill("not a proxy")
    box.get_by_role("button", name="Import", exact=True).click()
    expect(result).to_contain_text("Imported 0, rejected 2")
    expect(result).to_contain_text("line 1")
    expect(result).to_contain_text("line 2")
    expect(result).to_contain_text("The proxy is not host:port or host:port:user:pass.")
    expect(result).not_to_contain_text("cookie-one")                  # a rejected credential is never echoed back
    assert db.one("SELECT count(*) FROM accounts")[0] == 0
    box.get_by_label("Credentials").fill("cookie-one\n\ncookie-two")
    box.get_by_label("Proxy for all").fill("10.1.2.3:8080:user:pw-secret")
    box.get_by_role("button", name="Import", exact=True).click()
    expect(result).to_contain_text("Imported 2, rejected 0")
    expect(result).to_contain_text("added and are being checked now")
    assert db.one("SELECT count(*) FROM accounts WHERE proxy_host = '10.1.2.3' AND proxy_auth_enc IS NOT NULL")[0] == 2
    box.get_by_role("button", name=CLOSE, exact=True).click()
    expect(rows(page)).to_have_count(2)


# a press on the list's padding (the 6 px around its options) must keep the focus on the select: in Chrome a press on
# anything that cannot take the focus moves it off, and the next Escape then reaches the dialog, which closes with the
# pasted lines (a synthetic event in a unit test cannot show where the browser puts the focus)
def test_a_press_on_the_open_provider_list_and_escape_keep_the_import_dialog_and_its_paste(app, app_session, db):
    page = app_session
    open_accounts(page)
    header_import(page).click()
    box = page.get_by_role("dialog", name="Import accounts")
    box.get_by_label("Credentials").fill("cookie-one\ncookie-two")
    provider = box.get_by_role("combobox", name="Provider")
    provider.click()
    listbox = page.get_by_role("listbox", name="Provider")
    expect(listbox).to_be_visible()
    edge = listbox.bounding_box()
    # the left padding strip at mid-height (its corners are rounded off: a press there would land on the dialog below)
    page.mouse.click(edge["x"] + 3, edge["y"] + edge["height"] / 2)
    expect(provider, "the focus stays on the select").to_be_focused()
    page.keyboard.press("Escape")
    expect(listbox).to_have_count(0)
    expect(box, "the Import dialog stays open").to_be_visible()
    expect(box.get_by_label("Credentials")).to_have_value("cookie-one\ncookie-two")
    provider.click()
    options = page.get_by_role("option")
    pick = options.last
    name = pick.inner_text().strip()
    pick.click()
    expect(listbox).to_have_count(0)
    expect(provider).to_contain_text(name)
    provider.click()
    expect(page.get_by_role("option", name=name, exact=True)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Escape")
    expect(box).to_be_visible()


def test_an_empty_paste_says_so_and_sends_nothing(app, app_session, db):
    page = app_session
    open_accounts(page)
    header_import(page).click()
    page.get_by_role("dialog").get_by_role("button", name="Import", exact=True).click()
    expect(page.get_by_text("Paste at least one credential")).to_be_visible()
    assert db.one("SELECT count(*) FROM accounts")[0] == 0


def test_edit_sends_the_changed_fields_only_and_an_emptied_proxy_as_null(app, app_session, db):
    page = app_session
    acct = new_account(db, label="free one", checked=True, proxy=("10.0.0.1", 8080))
    open_accounts(page)
    sent = patches(page)
    row = rows(page).filter(has_text="free one")
    more(row, "Edit")
    box = page.get_by_role("dialog")
    expect(box.get_by_label("Proxy", exact=True)).to_have_value("10.0.0.1:8080")
    box.get_by_label("Label").fill("renamed")
    expect(box).to_contain_text("Was free one.")                      # the edited label says what it was
    expect(box).to_contain_text("1 unsaved change")
    box.get_by_role("button", name="Save").click()
    expect(rows(page).filter(has_text="renamed")).to_have_count(1)
    assert sent == [{"label": "renamed"}]
    assert db.one("SELECT label FROM accounts WHERE id = %s", acct)[0] == "renamed"
    more(rows(page).filter(has_text="renamed"), "Edit")
    box = page.get_by_role("dialog")
    box.get_by_label("Proxy", exact=True).fill("")
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved", exact=True)).to_be_visible()
    assert sent == [{"label": "renamed"}, {"proxy": None}]
    assert db.one("SELECT proxy_host FROM accounts WHERE id = %s", acct)[0] is None
    # nothing changed: Save only closes
    more(rows(page).filter(has_text="renamed"), "Edit")
    page.get_by_role("dialog").get_by_role("button", name="Save").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert len(sent) == 2


def test_escape_from_a_focused_field_of_the_edit_account_drawer_asks_and_discarding_changes_nothing(
        app, app_session, db):
    page = app_session
    acct = new_account(db, label="free one", checked=True)
    open_accounts(page)
    more(rows(page).filter(has_text="free one"), "Edit")
    label = page.get_by_role("dialog").get_by_label("Label")
    label.fill("typed, never saved")
    label.focus()
    page.keyboard.press("Escape")
    question = page.locator(".el-message-box")
    expect(question).to_contain_text("Discard your changes to this account?")
    question.get_by_role("button", name="Discard").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert db.one("SELECT label FROM accounts WHERE id = %s", acct)[0] == "free one"


def test_check_proxy_in_the_edit_dialog_queues_the_command_and_polls_it_to_the_end(app, app_session, db):
    page = app_session
    acct = new_account(db, label="proxied", checked=True, proxy=("10.0.0.1", 8080))
    open_accounts(page)
    more(rows(page).filter(has_text="proxied"), "Edit")
    page.get_by_role("dialog").get_by_role("button", name="Check proxy").click()
    # the dialog stays open: the check's chip runs under the proxy row; with a chip there is no "queued" toast
    expect(page.get_by_role("dialog").locator("[data-state=running]")).to_contain_text(
        re.compile(r"^Checking · started \d\d:\d\d:\d\d$"))
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'proxy_recheck' AND account_id = %s", acct)[0] == 1
    db.run("UPDATE commands SET status = 'done', result = '{}'::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'proxy_recheck' AND account_id = %s", acct)
    expect(page.get_by_text("Proxy check done")).to_be_visible(timeout=10000)
    expect(page.get_by_text("Proxy check: queued")).to_have_count(0)
    expect(page.get_by_role("dialog").locator("[data-state=ok]")).to_contain_text("Check passed ·")


def test_saving_a_changed_proxy_polls_the_proxy_check_the_server_queued(app, app_session, db):
    page = app_session
    acct = new_account(db, label="proxied", checked=True, proxy=("10.0.0.1", 8080))
    open_accounts(page)
    more(rows(page).filter(has_text="proxied"), "Edit")
    box = page.get_by_role("dialog")
    box.get_by_label("Proxy", exact=True).fill("10.9.9.9:3128")
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved", exact=True)).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'proxy_recheck' AND account_id = %s", acct)[0] == 1
    db.run("UPDATE commands SET status = 'done', result = '{}'::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'proxy_recheck' AND account_id = %s", acct)
    expect(page.get_by_text("Proxy check done")).to_be_visible(timeout=10000)


def test_check_wake_delete_and_the_provider_commands_queue_a_command_and_say_so(app, app_session, db):
    page = app_session
    free = new_account(db, label="free one", checked=True)
    open_accounts(page)
    row = rows(page).filter(has_text="free one")
    page.get_by_role("button", name="Refresh catalog", exact=True).click()
    expect(page.locator(".head [data-state=running]")).to_have_text("running…")   # its chip, no toast
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'refresh_catalog'")[0] == 1
    page.get_by_role("button", name="Re-check all accounts", exact=True).click()
    expect(page.locator(".head [data-state=running]")).to_have_count(2)
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'check_all'")[0] == 1
    # a healthy row has no Re-check of its own: its drawer has one
    expect(row.get_by_role("button", name=RECHECK)).to_have_count(0)
    box = detail(page, "free one")
    box.get_by_role("button", name=RECHECK).click()
    expect(box.locator("[data-state=running]")).to_have_text("running…")
    expect(page.get_by_text("Check: queued")).to_have_count(0)
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'check' AND account_id = %s", free)[0] == 1
    box.get_by_role("button", name=CLOSE).click()
    expect(box).to_be_hidden()
    more(row, "Wake")
    expect(page.get_by_text("Wake: clear cooldown and strikes: queued")).to_be_visible()
    more(row, "Delete")
    dialog = page.locator(".el-message-box")
    expect(dialog).to_contain_text("Delete account free one?")
    expect(dialog).to_contain_text("A used account cannot be deleted: disable it instead.")
    dialog.get_by_role("button", name="Cancel").click()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'delete_account'")[0] == 0
    # the toast stack holds four: a fifth is dropped, so let the earlier ones go first
    expect(page.locator(".el-message")).to_have_count(0)
    more(row, "Delete")
    page.locator(".el-message-box").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Delete: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'delete_account'")[0] == 1


def test_a_dead_row_rechecks_from_the_list_and_shows_what_it_found(app, app_session, db):
    page = app_session
    gone = new_account(db, label="gone one", checked=True)
    db.run("UPDATE accounts SET state = 'dead', state_reason = 'signed out' WHERE id = %s", gone)
    open_accounts(page)
    row = rows(page).filter(has_text="gone one")
    expect(row.locator(".acct-pill")).to_have_text("Dead")
    expect(row).to_contain_text("signed out")
    row.get_by_role("button", name=RECHECK).click()
    expect(row.locator("[data-state=running]")).to_have_text("running…")
    db.run("UPDATE commands SET status = 'done', result = %s::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'check' AND account_id = %s", json.dumps({"outcome": "checked"}), gone)
    expect(page.get_by_text("Check done: checked, account dead (signed out)")).to_be_visible(timeout=10000)


def test_the_taking_jobs_switch_in_edit_switches_the_account_off_and_a_refused_change_says_why(app, app_session, db):
    page = app_session
    acct = new_account(db, label="free one", checked=True)
    open_accounts(page)
    more(rows(page).filter(has_text="free one"), "Edit")
    box = page.get_by_role("dialog")
    switch = box.get_by_role("switch", name="Enable free one")
    expect(switch).to_be_checked()
    switch.click()
    expect(switch).not_to_be_checked()
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved", exact=True)).to_be_visible()
    assert db.one("SELECT state FROM accounts WHERE id = %s", acct)[0] == "disabled"
    expect(rows(page).filter(has_text="free one").locator(".acct-pill")).to_have_text("Switched off")
    # a refused change: the drawer stays with what was set, and the toast says why
    refusal = {"error": {"code": "pool.account_busy", "message": "account is busy", "param": None}}
    page.route("**/admin/accounts/*", lambda r: r.fulfill(status=409, content_type="application/json",
                                                           body=json.dumps(refusal))
               if r.request.method == "PATCH" else r.continue_())
    more(rows(page).filter(has_text="free one"), "Edit")
    box = page.get_by_role("dialog")
    box.get_by_role("switch").click()
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_text("account is busy")).to_be_visible()
    assert db.one("SELECT state FROM accounts WHERE id = %s", acct)[0] == "disabled"
    page.problems.clear()                                       # the 409 is a console line


def test_shot_opens_the_screenshot_as_a_blob_in_a_new_tab_without_an_opener(app, app_session, db):
    page = app_session
    acct = new_account(db, label="has shot", checked=True)
    db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, 'shots/x.png')", acct)
    asked = []

    def screenshot(route):
        asked.append(route.request.url)
        route.fulfill(status=200, content_type="image/png", body=PNG)

    page.route("**/admin/accounts/*/screenshot", screenshot)
    open_accounts(page)
    # The new tab shows a blob: image document, which inherits the admin CSP, and Chrome's own image viewer styles it
    # inline: that is the browser's document, not ours, so the call is recorded instead of followed.
    # The expression ends in `true`: an expression whose value is a function is CALLED by evaluate, and that stray
    # call (window.open(undefined)) landed in the record, so the test read it whenever it looked before the real one.
    page.evaluate("window.__opened = []; window.open = (...args) => "
                  "{ window.__opened.push({args, stack: new Error().stack}); return null; }; true",
                  isolated_context=False)
    more(rows(page).filter(has_text="has shot"), "Shot")
    for _ in range(100):
        if page.evaluate("window.__opened.length > 0", isolated_context=False):
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(300)                                  # a second, stray call would land by now
    calls = json.loads(page.evaluate("JSON.stringify(window.__opened)", isolated_context=False))
    assert len(calls) == 1, "window.open was called %d times: %r" % (len(calls), calls)
    url, target, features = calls[0]["args"]
    assert url.startswith("blob:http://127.0.0.1:") and target == "_blank" and features == "noopener"
    assert len(asked) == 1 and asked[0].endswith("/screenshot")


def test_auto_refresh_picks_up_new_rows_and_waits_while_a_dialog_is_open(app, app_session, db):
    page = app_session
    new_account(db, label="first", checked=True)
    open_accounts(page)
    expect(rows(page)).to_have_count(1)
    header_import(page).click()
    new_account(db, label="late arrival", checked=True)
    page.wait_for_timeout(12000)                                # more than one period, with the dialog open
    expect(rows(page)).to_have_count(1)
    page.get_by_role("dialog").get_by_role("button", name=CLOSE, exact=True).click()
    expect(rows(page)).to_have_count(2, timeout=15000)


def test_the_more_menu_opens_under_its_button_and_closes_on_escape(app, app_session, db):
    page = app_session
    new_account(db, label="free one", checked=True)
    page.set_viewport_size({"width": 1440, "height": 1400})          # room under the row
    open_accounts(page)
    row = rows(page).filter(has_text="free one")
    trigger = row.get_by_role("button", name="More actions for free one")
    trigger.click()
    menu = page.get_by_role("menu", name="Account actions")
    expect(menu).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    names = [" ".join(t.split()) for t in menu.get_by_role("menuitem").all_inner_texts()]
    assert names == ["Shot none yet", "Wake", "Edit", "Delete"], names
    assert menu.bounding_box()["y"] >= trigger.bounding_box()["y"] + trigger.bounding_box()["height"]
    page.keyboard.press("Escape")
    expect(menu).to_have_count(0)
    expect(trigger).to_have_attribute("aria-expanded", "false")


@pytest.mark.parametrize("width", [390, 768])
def test_the_list_fits_the_screen_at_tablet_and_phone_widths_and_import_is_reachable(app, app_page, live, db, width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 900})
    new_account(db, label="free one", checked=True, proxy=("10.0.0.1", 8080))
    app.sign_in(page)
    open_accounts(page)
    expect(page.get_by_role("button", name="free one", exact=True)).to_be_visible()
    assert sideways(page) <= 1, "the page scrolls sideways"
    header_import(page).click()
    sheet = page.get_by_role("dialog", name="Import accounts")
    expect(sheet).to_be_visible()
    box = sheet.bounding_box()
    for _ in range(30):                                             # a drawer slides in for a moment
        page.wait_for_timeout(100)
        again = sheet.bounding_box()
        if again == box:
            break
        box = again
    if width == 390:
        assert box["x"] == 0 and box["width"] == width, box             # the whole screen below 768 px
    else:
        assert abs(box["x"] - 48) < 1 and abs(box["width"] - (width - 48)) < 1, box
    sheet.get_by_role("button", name=CLOSE).click()
    expect(sheet).to_have_count(0)
    # a healthy row has no Re-check; on a phone its More menu starts with Check
    expect(page.get_by_role("button", name=RECHECK)).to_have_count(0)
    if width == 390:
        page.get_by_role("button", name="More actions for free one").click()
        expect(page.get_by_role("menuitem").first).to_have_text("Check")


def test_an_asleep_account_shows_until_when_and_why(app, app_session, db):
    """The list carries `cooldown_until_ms`; the page once read `cooldown_ms` and never drew a sleeping account."""
    page = app_session
    acct = new_account(db, label="napping", checked=True)
    db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '35 min', "
           "cooldown_reason = 'dola.likeness_refused', strikes = 1 WHERE id = %s", acct)
    open_accounts(page)
    row = rows(page).filter(has_text="napping")
    expect(row.locator(".acct-pill")).to_have_text(re.compile(r"^Asleep until \d\d:\d\d$"))
    expect(row).to_contain_text("dola.likeness_refused")
    expect(row.get_by_role("button", name="Wake account")).to_be_visible()
    box = detail(page, "napping")
    expect(box.get_by_role("status")).to_contain_text("Resting after 1 strike.")
    expect(box.get_by_role("button", name="Wake now")).to_be_visible()


# a text that the page cuts off has more width than its box shows; `.clip` and the name buttons end in "…" by design
# and carry the whole text as their title
CUT_JS = """() => [...document.querySelectorAll('[data-testid=accounts-table] *, .head *, .av-toolbar *')]
    .filter((el) => el.offsetParent && !el.closest('.track, .asc__row, .sr-only'))
    .filter((el) => !el.matches('.clip, .at__open, .t-sub') || !el.title)
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible')
    .map((el) => el.className + ': ' + el.textContent.trim().slice(0, 40))"""


@pytest.mark.parametrize("width", [1280, 1440])
def test_the_desktop_rows_fit_the_card_with_their_actions_inside(app, app_page, live, db, width):
    """The table is as wide as the card gives it: a row's actions sit inside the card and nothing scrolls sideways."""
    page = app_page
    page.set_viewport_size({"width": width, "height": 800})
    gone = new_account(db, label="gone one", checked=True, proxy=("10.0.0.1", 8080))
    db.run("UPDATE accounts SET state = 'dead', state_reason = 'account.dead_session' WHERE id = %s", gone)
    app.sign_in(page)
    open_accounts(page)
    check = page.get_by_role("button", name=RECHECK)
    expect(check).to_be_visible()
    inside = page.evaluate("""() => {
        const box = document.querySelector(".av-card").getBoundingClientRect();
        const b = document.querySelector("[data-testid=accounts-table] button[aria-label^='More actions']")
            .getBoundingClientRect();
        return b.left >= box.left - 1 && b.right <= box.right + 1;
    }""")
    assert inside, "More lies inside the card"
    assert sideways(page) <= 0, "the page scrolls sideways"
    assert page.evaluate(CUT_JS, isolated_context=False) == []
