"""The Accounts tab of the Vue admin in Chrome against the real app (browser tier): the list, import, edit, the enable
switch, the commands the worker would run, the screenshot blob, auto-refresh and server strings as text. Every test runs
under the CSP watch of `conftest.CspWatch`. The app is opened by `app.url` (`/manage`)."""

import json

import pytest
from accountkit import new_account
from patchright.sync_api import expect

from relay_core import crypto

pytestmark = pytest.mark.browser

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001"
    "e221bc330000000049454e44ae426082")


def open_accounts(page):
    page.get_by_role("tab", name="Accounts", exact=True).click()
    expect(page.get_by_role("heading", name="Accounts")).to_be_visible()


def rows(page):
    return page.locator(".el-table__body tbody tr")


def more(row, name):
    """A row action: an icon button named by its tooltip (Shot, Wake, Edit, Delete)."""
    row.get_by_role("button", name=name, exact=True).click()


def flip(switch):
    """Element Plus hides the checkbox of a switch behind its track: click the checkbox itself."""
    switch.evaluate("el => el.click()")


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


def test_the_list_hides_the_switch_of_an_account_in_use_and_shows_server_strings_as_text(app, app_session, db):
    page = app_session
    busy = new_account(db, label="busy one", checked=True)
    lease(db, busy)
    bad = new_account(db, label=app.planted, checked=True)
    db.run("UPDATE accounts SET display_name = %s, state_reason = %s, last_error = %s WHERE id = %s",
           app.planted, app.planted, app.planted, bad)
    open_accounts(page)
    expect(rows(page)).to_have_count(2)
    expect(rows(page).filter(has_text="busy one").get_by_role("switch")).to_have_count(0)
    expect(rows(page).filter(has_text="busy one")).to_contain_text("in use")
    expect(rows(page).filter(has_text="<b>planted</b>").get_by_role("switch")).to_have_count(1)
    page.wait_for_timeout(300)                            # a script in the planted markup would have run by now
    app.assert_planted_as_text(page)


def test_import_shows_the_accepted_and_the_rejected_lines_and_sends_the_proxy_with_every_one(
        app, app_session, db, pair):
    page = app_session
    open_accounts(page)
    page.get_by_role("button", name="Import", exact=True).click()
    box = page.get_by_role("dialog")
    box.get_by_label("Credentials (one per line)").fill("cookie-one\ncookie-two")
    box.get_by_label("Proxy for all").fill("not a proxy")
    box.get_by_role("button", name="Import", exact=True).click()
    expect(box.locator(".import-result")).to_have_text(
        "Imported 0, rejected 2 (line 1: proxy_invalid; line 2: proxy_invalid)")
    assert db.one("SELECT count(*) FROM accounts")[0] == 0
    box.get_by_label("Credentials (one per line)").fill("cookie-one\n\ncookie-two")
    box.get_by_label("Proxy for all").fill("10.1.2.3:8080:user:pw-secret")
    box.get_by_role("button", name="Import", exact=True).click()
    expect(box.locator(".import-result")).to_have_text("Imported 2, rejected 0")
    assert db.one("SELECT count(*) FROM accounts WHERE proxy_host = '10.1.2.3' AND proxy_auth_enc IS NOT NULL")[0] == 2
    box.get_by_role("button", name="Close", exact=True).click()
    expect(rows(page)).to_have_count(2)


def test_an_empty_paste_says_so_and_sends_nothing(app, app_session, db):
    page = app_session
    open_accounts(page)
    page.get_by_role("button", name="Import", exact=True).click()
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
    expect(box.get_by_label("Proxy")).to_have_value("10.0.0.1:8080")
    box.get_by_label("Label").fill("renamed")
    box.get_by_role("button", name="Save").click()
    expect(rows(page).filter(has_text="renamed")).to_have_count(1)
    assert sent == [{"label": "renamed"}]
    assert db.one("SELECT label FROM accounts WHERE id = %s", acct)[0] == "renamed"
    more(rows(page).filter(has_text="renamed"), "Edit")
    box = page.get_by_role("dialog")
    box.get_by_label("Proxy").fill("")
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved")).to_be_visible()
    assert sent == [{"label": "renamed"}, {"proxy": None}]
    assert db.one("SELECT proxy_host FROM accounts WHERE id = %s", acct)[0] is None
    # nothing changed: Save only closes
    more(rows(page).filter(has_text="renamed"), "Edit")
    page.get_by_role("dialog").get_by_role("button", name="Save").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert len(sent) == 2


def test_escape_closes_the_edit_account_dialog_from_a_focused_field_and_changes_nothing(app, app_session, db):
    page = app_session
    acct = new_account(db, label="free one", checked=True)
    open_accounts(page)
    more(rows(page).filter(has_text="free one"), "Edit")
    label = page.get_by_role("dialog").get_by_label("Label")
    label.fill("typed, never saved")
    label.focus()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert db.one("SELECT label FROM accounts WHERE id = %s", acct)[0] == "free one"


def test_re_check_proxy_in_the_edit_dialog_queues_the_command_and_polls_it_to_the_end(app, app_session, db):
    page = app_session
    acct = new_account(db, label="proxied", checked=True, proxy=("10.0.0.1", 8080))
    open_accounts(page)
    more(rows(page).filter(has_text="proxied"), "Edit")
    page.get_by_role("dialog").get_by_role("button", name="Re-check proxy").click()
    expect(page.get_by_text("Proxy check: queued")).to_be_visible()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'proxy_recheck' AND account_id = %s", acct)[0] == 1
    db.run("UPDATE commands SET status = 'done', result = '{}'::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'proxy_recheck' AND account_id = %s", acct)
    expect(page.get_by_text("Proxy check: done")).to_be_visible(timeout=10000)


def test_saving_a_changed_proxy_polls_the_proxy_check_the_server_queued(app, app_session, db):
    page = app_session
    acct = new_account(db, label="proxied", checked=True, proxy=("10.0.0.1", 8080))
    open_accounts(page)
    more(rows(page).filter(has_text="proxied"), "Edit")
    box = page.get_by_role("dialog")
    box.get_by_label("Proxy").fill("10.9.9.9:3128")
    box.get_by_role("button", name="Save").click()
    expect(page.get_by_text("Saved")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'proxy_recheck' AND account_id = %s", acct)[0] == 1
    db.run("UPDATE commands SET status = 'done', result = '{}'::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'proxy_recheck' AND account_id = %s", acct)
    expect(page.get_by_text("Proxy check: done")).to_be_visible(timeout=10000)


def test_check_wake_delete_and_the_provider_commands_queue_a_command_and_say_so(app, app_session, db):
    page = app_session
    free = new_account(db, label="free one", checked=True)
    open_accounts(page)
    row = rows(page).filter(has_text="free one")
    page.get_by_role("button", name="Refresh catalog").click()
    expect(page.get_by_text("Refresh catalog fake: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'refresh_catalog'")[0] == 1
    page.get_by_role("button", name="Check all").click()
    expect(page.get_by_text("Check all fake: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'check_all'")[0] == 1
    row.get_by_role("button", name="Check", exact=True).click()
    expect(page.get_by_text("Check: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'check' AND account_id = %s", free)[0] == 1
    more(row, "Wake")
    expect(page.get_by_text("Wake: clear cooldown and strikes: queued")).to_be_visible()
    more(row, "Delete")
    box = page.locator(".el-message-box")
    expect(box).to_contain_text('Delete account "free one"? A used account cannot be deleted: disable it instead.')
    box.get_by_role("button", name="Cancel").click()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'delete_account'")[0] == 0
    # the toast stack holds four: a fifth is dropped, so let the earlier ones go first
    expect(page.locator(".el-message")).to_have_count(0)
    more(row, "Delete")
    page.locator(".el-message-box").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Delete: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'delete_account'")[0] == 1


def test_a_check_shows_what_it_found_and_a_dead_account_shows_its_reason(app, app_session, db):
    page = app_session
    gone = new_account(db, label="gone one", checked=True)
    db.run("UPDATE accounts SET state = 'dead', state_reason = 'signed out' WHERE id = %s", gone)
    open_accounts(page)
    rows(page).filter(has_text="gone one").get_by_role("button", name="Check", exact=True).click()
    expect(page.get_by_text("Check: queued")).to_be_visible()
    db.run("UPDATE commands SET status = 'done', result = %s::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'check' AND account_id = %s", json.dumps({"outcome": "checked"}), gone)
    expect(page.get_by_text("Check: checked - account dead (signed out)")).to_be_visible(timeout=10000)


def test_the_enable_switch_changes_the_account_and_a_refused_change_puts_it_back(app, app_session, db):
    page = app_session
    acct = new_account(db, label="free one", checked=True)
    open_accounts(page)
    switch = rows(page).filter(has_text="free one").get_by_role("switch")
    expect(switch).to_be_checked()
    flip(switch)
    expect(switch).not_to_be_checked()
    assert db.one("SELECT state FROM accounts WHERE id = %s", acct)[0] == "disabled"
    # a refused change: the page reads the list again, so the switch is what the server holds (off)
    refusal = {"error": {"code": "pool.account_busy", "message": "account is busy", "param": None}}
    page.route("**/admin/accounts/*", lambda r: r.fulfill(status=409, content_type="application/json",
                                                           body=json.dumps(refusal))
               if r.request.method == "PATCH" else r.continue_())
    flip(switch)
    expect(page.get_by_text("account is busy")).to_be_visible()
    expect(switch).not_to_be_checked()
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
    page.evaluate("window.open = (...args) => { window.__opened = args; return null; }", isolated_context=False)
    more(rows(page).filter(has_text="has shot"), "Shot")
    for _ in range(100):
        if page.evaluate("window.__opened !== undefined", isolated_context=False):
            break
        page.wait_for_timeout(50)
    url, target, features = page.evaluate("window.__opened", isolated_context=False)
    assert url.startswith("blob:http://127.0.0.1:") and target == "_blank" and features == "noopener"
    assert len(asked) == 1 and asked[0].endswith("/screenshot")


def test_auto_refresh_picks_up_new_rows_and_waits_while_a_dialog_is_open(app, app_session, db):
    page = app_session
    new_account(db, label="first", checked=True)
    open_accounts(page)
    expect(rows(page)).to_have_count(1)
    flip(page.get_by_role("switch", name="Auto-refresh"))
    page.get_by_role("button", name="Import", exact=True).click()
    new_account(db, label="late arrival", checked=True)
    page.wait_for_timeout(12000)                                # more than one period, with the dialog open
    expect(rows(page)).to_have_count(1)
    page.get_by_role("dialog").get_by_role("button", name="Close", exact=True).click()
    expect(rows(page)).to_have_count(2, timeout=15000)
    # the switch outlives a tab switch
    page.get_by_role("tab", name="Jobs", exact=True).click()
    page.get_by_role("tab", name="Accounts", exact=True).click()
    expect(page.get_by_role("switch", name="Auto-refresh")).to_be_checked()
