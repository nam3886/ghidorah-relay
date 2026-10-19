"""Import accounts and Edit account as right drawers, in Chrome against the real app (browser tier): both open in the
shared `DetailDrawer` (620 px on a tablet and a desktop, a full-screen sheet with Back on a phone), carry the close
guard (what was pasted or typed is never lost to a stray Escape, X, Cancel, scrim click or browser Back), keep focus
inside and give it back to a stable control, and leave no credential or proxy in a hidden field after any way out.
Every test runs under the CSP watch of `conftest.CspWatch`."""

import re

import pytest
from accountkit import new_account
from patchright.sync_api import expect
from test_e2e_accounts import header_import, more, open_accounts, pair, rows  # noqa: F401  (pair is a fixture)

pytestmark = pytest.mark.browser

WIDTHS = ((1440, 900), (768, 1024))
PASTE, PROXY = "cookie-one\ncookie-two", "10.1.2.3:8080:user:pw-secret"
BOXES_JS = """() => {
  window.__most_boxes = 0;
  new MutationObserver(() => {
    window.__most_boxes = Math.max(window.__most_boxes, document.querySelectorAll('.el-message-box').length);
  }).observe(document.body, {childList: true, subtree: true});
}"""


def goto_accounts(app, page, width=1440, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(app.url + "#/accounts")
    expect(page.get_by_role("heading", name="Accounts", exact=True)).to_be_visible()


def drawer(page):
    """The one dialog of the page (a discard question is an alertdialog)."""
    return page.get_by_role("dialog")


def question(page):
    return page.locator(".el-message-box")


def open_import(page):
    header_import(page).click()
    expect(drawer(page)).to_be_visible()
    return drawer(page)


def pasted_import(page):
    box = open_import(page)
    box.get_by_label("Credentials").fill(PASTE)
    box.get_by_label("Proxy for all").fill(PROXY)
    return box


def open_edit(page, label):
    more(rows(page).filter(has_text=label), "Edit")
    expect(drawer(page)).to_be_visible()
    return drawer(page)


def active_in_drawer(page) -> bool:
    return page.evaluate("Boolean(document.activeElement && document.activeElement.closest('.el-drawer'))",
                         isolated_context=False)


def box_of(locator):
    """The box once it stops moving (a drawer slides in for a moment)."""
    box = locator.bounding_box()
    for _ in range(30):
        locator.page.wait_for_timeout(100)
        again = locator.bounding_box()
        if again == box:
            break
        box = again
    assert box is not None
    return box


def reopened_import_is_empty(page):
    box = open_import(page)
    expect(box.get_by_label("Credentials")).to_have_value("")
    expect(box.get_by_label("Proxy for all")).to_have_value("")


# ---- the frame ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width, height", WIDTHS)
def test_import_and_edit_are_right_drawers_named_by_their_headings(app, app_session, db, width, height):
    page = app_session
    new_account(db, label="free one", checked=True)
    goto_accounts(app, page, width, height)
    box = open_import(page)
    expect(page.get_by_role("dialog", name="Import accounts")).to_be_visible()
    expect(box.get_by_role("heading", name="Import accounts")).to_be_visible()
    got = box_of(page.get_by_test_id("import-drawer"))
    assert abs(got["width"] - 620) < 1 and abs(got["x"] + got["width"] - width) < 1, "a right drawer 620 px: %r" % got
    assert page.locator(".el-dialog").count() == 0
    expect(box.get_by_role("button", name="Close")).to_be_visible()
    assert box.get_by_role("button", name="Back to Accounts").count() == 0, "Back is the phone's"
    box.get_by_role("button", name="Close").click()
    expect(drawer(page)).to_have_count(0)
    box = open_edit(page, "free one")
    expect(page.get_by_role("dialog", name="Edit account — free one")).to_be_visible()
    expect(box.get_by_role("heading", name="Edit account — free one")).to_be_visible()
    got = box_of(page.get_by_test_id("edit-account-drawer"))
    assert abs(got["width"] - 620) < 1 and abs(got["x"] + got["width"] - width) < 1, "a right drawer 620 px: %r" % got


def test_on_a_phone_both_are_full_screen_sheets_with_back_and_44_px_footer_buttons(app, app_session, db):
    page = app_session
    new_account(db, label="free one", checked=True)
    goto_accounts(app, page, 390, 844)
    box = open_import(page)
    got = box_of(page.get_by_test_id("import-drawer"))
    assert abs(got["x"]) < 1 and abs(got["width"] - 390) < 1, "the whole screen: %r" % got
    expect(box.get_by_role("button", name="Back to Accounts")).to_be_visible()
    assert box.get_by_role("button", name="Close").count() == 0
    for name in ("Cancel", "Import"):
        height = box_of(box.get_by_role("button", name=name, exact=True))["height"]
        assert height == 44, "%s is %s px high" % (name, height)
    box.get_by_role("button", name="Back to Accounts").click()
    expect(drawer(page)).to_have_count(0)
    page.get_by_role("button", name=re.compile(r"^More actions for free one")).click()
    page.get_by_role("menuitem", name="Edit", exact=True).click()
    box = drawer(page)
    expect(box.get_by_role("button", name="Back to Accounts")).to_be_visible()
    expect(box.get_by_role("heading", name="Edit account — free one", level=1)).to_be_visible()
    for name in ("Cancel", "Save"):
        height = box_of(box.get_by_role("button", name=name, exact=True))["height"]
        assert height == 44, "%s is %s px high" % (name, height)


# ---- focus -------------------------------------------------------------------------------------------------------

def test_focus_stays_in_the_import_drawer_and_comes_back_to_the_button_that_opened_it(app, app_session, db):
    page = app_session
    goto_accounts(app, page)
    header_import(page).focus()
    header_import(page).press("Enter")
    expect(drawer(page)).to_be_visible()
    for key in ("Tab", "Shift+Tab"):
        for _ in range(40):
            page.keyboard.press(key)
            assert active_in_drawer(page), "focus left the drawer"
    page.keyboard.press("Escape")
    expect(drawer(page)).to_have_count(0)
    expect(header_import(page)).to_be_focused()


def test_focus_comes_back_to_the_row_button_that_opened_the_edit_menu(app, app_session, db):
    page = app_session
    new_account(db, label="free one", checked=True)
    goto_accounts(app, page)
    open_edit(page, "free one")
    for _ in range(20):
        page.keyboard.press("Tab")
        assert active_in_drawer(page), "focus left the drawer"
    page.keyboard.press("Escape")
    expect(drawer(page)).to_have_count(0)
    expect(rows(page).filter(has_text="free one").get_by_role("button", name=re.compile(r"^More actions for "))
           ).to_be_focused()


def test_focus_goes_to_the_import_button_when_the_row_that_opened_edit_is_gone(app, app_session, db):
    page = app_session
    new_account(db, label="free one", checked=True)
    goto_accounts(app, page)
    box = open_edit(page, "free one")
    # the list the save reads again holds no account, so the button that opened the drawer is gone
    page.route(re.compile(r"/admin/accounts\?"), lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"items": [], "total": 0}')
        if r.request.method == "GET" else r.continue_())
    box.get_by_label("Label", exact=True).fill("renamed")
    box.get_by_role("button", name="Save", exact=True).click()
    expect(drawer(page)).to_have_count(0)
    expect(page.locator("tbody tr")).to_have_count(0)
    expect(header_import(page)).to_be_focused()


def test_escape_with_the_provider_list_open_closes_only_the_list(app, app_session, db):
    page = app_session
    goto_accounts(app, page)
    box = pasted_import(page)
    box.get_by_role("combobox", name="Provider").click()
    expect(page.get_by_role("listbox", name="Provider")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("listbox", name="Provider")).to_have_count(0)
    expect(box).to_be_visible()
    expect(question(page)).to_have_count(0)
    expect(box.get_by_label("Credentials")).to_have_value(PASTE)


# ---- the close guard ---------------------------------------------------------------------------------------------

def test_a_click_beside_either_drawer_acts_as_escape(app, app_session, db):
    page = app_session
    new_account(db, label="free one", checked=True)
    goto_accounts(app, page)
    open_import(page)
    page.mouse.click(40, 400)                       # clean: it closes
    expect(drawer(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    open_import(page).get_by_label("Credentials").fill(PASTE)
    page.mouse.click(40, 400)                       # pasted: it asks, and Keep editing keeps the paste
    expect(question(page)).to_contain_text("Discard the pasted credentials?")
    question(page).get_by_role("button", name="Keep editing").click()
    expect(question(page)).to_have_count(0)
    expect(drawer(page).get_by_label("Credentials")).to_have_value(PASTE)
    page.mouse.click(40, 400)
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Discard").click()
    expect(drawer(page)).to_have_count(0)
    open_edit(page, "free one")
    page.mouse.click(40, 400)
    expect(drawer(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    open_edit(page, "free one").get_by_label("Label", exact=True).fill("typed")
    page.mouse.click(40, 400)
    expect(question(page)).to_contain_text("Discard your changes to this account?")


@pytest.mark.parametrize("way", ("escape", "close", "cancel"))
def test_a_paste_asks_once_keep_editing_keeps_it_and_discard_empties_it_and_closes(app, app_session, db, way):
    page = app_session
    goto_accounts(app, page)
    box = pasted_import(page)

    def leave():
        if way == "escape":
            page.keyboard.press("Escape")
        else:
            box.get_by_role("button", name="Close" if way == "close" else "Cancel", exact=True).click()

    leave()
    expect(question(page)).to_have_count(1)
    expect(question(page)).to_contain_text("Discard the pasted credentials?")
    question(page).get_by_role("button", name="Keep editing").click()
    expect(question(page)).to_have_count(0)
    expect(box).to_be_visible()
    expect(box.get_by_label("Credentials")).to_have_value(PASTE)
    expect(box.get_by_label("Proxy for all")).to_have_value(PROXY)
    leave()
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Discard").click()
    expect(drawer(page)).to_have_count(0)
    assert db.one("SELECT count(*) FROM accounts")[0] == 0
    reopened_import_is_empty(page)


def test_a_proxy_typed_alone_is_worth_a_question_and_is_gone_after_discard(app, app_session, db):
    page = app_session
    goto_accounts(app, page)
    box = open_import(page)
    box.get_by_label("Proxy for all").fill(PROXY)
    page.keyboard.press("Escape")
    expect(question(page)).to_contain_text("Discard the pasted credentials?")
    question(page).get_by_role("button", name="Discard").click()
    expect(drawer(page)).to_have_count(0)
    reopened_import_is_empty(page)


def test_two_ways_out_in_a_row_never_put_up_two_questions(app, app_session, db):
    page = app_session
    goto_accounts(app, page)
    box = pasted_import(page)
    page.evaluate(BOXES_JS, isolated_context=False)
    page.keyboard.press("Escape")
    expect(question(page)).to_have_count(1)
    page.keyboard.press("Escape")             # the question's own Escape: it closes only itself (keep)
    expect(question(page)).to_have_count(0)
    box.get_by_role("button", name="Close", exact=True).click()
    expect(question(page)).to_have_count(1)
    page.keyboard.press("Escape")
    expect(question(page)).to_have_count(0)
    expect(box.get_by_label("Credentials")).to_have_value(PASTE)
    assert page.evaluate("window.__most_boxes", isolated_context=False) == 1, "two questions at once"


def test_a_good_import_leaves_the_result_and_empty_fields_and_closes_without_asking(app, app_session, db, pair):  # noqa: F811
    page = app_session
    goto_accounts(app, page)
    box = pasted_import(page)
    box.get_by_role("button", name="Import", exact=True).click()
    expect(box.get_by_role("status", name="Result of the previous import")).to_contain_text("Imported 2, rejected 0")
    expect(box.get_by_label("Credentials")).to_have_value("")
    expect(box.get_by_label("Proxy for all")).to_have_value("")
    box.get_by_role("button", name="Close", exact=True).click()
    expect(drawer(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    reopened_import_is_empty(page)


def test_a_bad_import_empties_the_fields_too_and_the_drawer_closes_without_asking(app, app_session, db, pair):  # noqa: F811
    page = app_session
    goto_accounts(app, page)
    box = open_import(page)
    box.get_by_label("Credentials").fill(PASTE)
    box.get_by_label("Proxy for all").fill("not a proxy")
    box.get_by_role("button", name="Import", exact=True).click()
    expect(box.get_by_role("status", name="Result of the previous import")).to_contain_text("rejected 2")
    expect(box.get_by_label("Credentials")).to_have_value("")
    expect(box.get_by_label("Proxy for all")).to_have_value("")
    page.keyboard.press("Escape")
    expect(drawer(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)


def test_edit_asks_what_is_lost_and_discard_leaves_the_account_alone(app, app_session, db):
    page = app_session
    acct = new_account(db, label="free one", checked=True, proxy=("10.0.0.1", 8080))
    goto_accounts(app, page)
    for field, text in (("Label", "renamed"), ("Proxy", "10.0.0.1:8080:user:new-password")):
        box = open_edit(page, "free one")
        box.get_by_label(field, exact=True).fill(text)
        page.keyboard.press("Escape")
        expect(question(page)).to_contain_text("Discard your changes to this account?")
        question(page).get_by_role("button", name="Keep editing").click()
        expect(box.get_by_label(field, exact=True)).to_have_value(text)
        box.get_by_role("button", name="Cancel", exact=True).click()
        expect(question(page)).to_have_count(1)
        question(page).get_by_role("button", name="Discard").click()
        expect(drawer(page)).to_have_count(0)
    assert db.one("SELECT label FROM accounts WHERE id = %s", acct)[0] == "free one"
    box = open_edit(page, "free one")
    expect(box.get_by_label("Label", exact=True)).to_have_value("free one")
    expect(box.get_by_label("Proxy", exact=True)).to_have_value("10.0.0.1:8080")


def test_an_edit_put_back_as_it_was_closes_without_asking(app, app_session, db):
    page = app_session
    new_account(db, label="free one", checked=True)
    goto_accounts(app, page)
    box = open_edit(page, "free one")
    box.get_by_label("Label", exact=True).fill("abc")
    box.get_by_label("Label", exact=True).fill("free one")
    page.keyboard.press("Escape")
    expect(drawer(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)


def test_a_list_read_under_an_open_edit_drawer_does_not_change_whether_it_is_dirty(app, app_session, db):
    """Check proxy ends and the page reads the list again: the account comes back under another label. The drawer
    holds what it opened with, so an untouched form stays clean and a typed one stays dirty."""
    page = app_session
    acct = new_account(db, label="proxied", checked=True, proxy=("10.0.0.1", 8080))
    goto_accounts(app, page)
    box = open_edit(page, "proxied")

    def check_proxy_ends(server_label):
        box.get_by_role("button", name="Check proxy").click()
        expect(box.locator("[data-state=running]")).to_be_visible()
        db.run("UPDATE accounts SET label = %s WHERE id = %s", server_label, acct)
        db.run("UPDATE commands SET status = 'done', result = '{}'::jsonb, finished_at = clock_timestamp() "
               "WHERE kind = 'proxy_recheck' AND account_id = %s AND status <> 'done'", acct)
        expect(page.get_by_text("Proxy check done").first).to_be_visible(timeout=10000)
        expect(page.get_by_role("heading", name="Edit account — " + server_label)).to_be_visible(timeout=10000)

    check_proxy_ends("renamed by someone else")
    expect(box.get_by_label("Label", exact=True)).to_have_value("proxied")      # the typed fields are untouched
    box.get_by_role("button", name="Cancel", exact=True).click()                # nothing typed: no question
    expect(drawer(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    box = open_edit(page, "renamed by someone else")
    box.get_by_label("Label", exact=True).fill("typed after the read")
    box.get_by_role("button", name="Check proxy").click()
    expect(box.locator("[data-state=running]")).to_be_visible()
    db.run("UPDATE accounts SET label = 'typed after the read' WHERE id = %s", acct)
    db.run("UPDATE commands SET status = 'done', result = '{}'::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'proxy_recheck' AND account_id = %s AND status <> 'done'", acct)
    expect(page.get_by_role("heading", name="Edit account — typed after the read")).to_be_visible(timeout=10000)
    # the server copy now says what was typed, but the drawer was opened on another label: still a change
    box.get_by_role("button", name="Cancel", exact=True).click()
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Keep editing").click()
    expect(box.get_by_label("Label", exact=True)).to_have_value("typed after the read")


# ---- leaving the page --------------------------------------------------------------------------------------------

def test_browser_back_with_a_paste_offers_the_question_first(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(app.url + "#/jobs")
    open_accounts(page)
    pasted_import(page)
    page.go_back()
    expect(question(page)).to_contain_text("Discard what you typed?")      # the leave guard's own sentence
    question(page).get_by_role("button", name="Keep editing").click()
    expect(question(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(r"#/accounts$"))
    expect(drawer(page).get_by_label("Credentials")).to_have_value(PASTE)
    page.go_back()
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Discard").click()
    expect(page).to_have_url(re.compile(r"#/jobs"))


def test_a_clean_drawer_leaves_with_browser_back_without_asking(app, app_session, db):
    page = app_session
    page.goto(app.url + "#/jobs")
    open_accounts(page)
    open_import(page)
    page.go_back()
    expect(page).to_have_url(re.compile(r"#/jobs"))
    expect(question(page)).to_have_count(0)


def test_a_reload_with_a_paste_is_warned_about_and_a_clean_drawer_is_not(app, app_session, db):
    page = app_session
    goto_accounts(app, page)
    box = open_import(page)
    probe = """() => {
      const e = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(e);
      return e.defaultPrevented;
    }"""
    assert not page.evaluate(probe, isolated_context=False)
    box.get_by_label("Credentials").fill(PASTE)
    assert page.evaluate(probe, isolated_context=False)
