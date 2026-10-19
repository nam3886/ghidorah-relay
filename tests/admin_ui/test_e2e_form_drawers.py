"""The key forms as right drawers, in Chrome against the real app (browser tier): New key and Edit key open in the
shared `DetailDrawer` (620 px on a tablet and a desktop, a full-screen sheet on a phone), carry the close guard (what
was typed is never lost to a stray Escape, X, Cancel, scrim click or browser Back; a write in flight makes every way out
inert), keep the optional label, and the Keys list names a key without a label by its prefix and draws the relative bars
of unlimited wallets. Every test runs under the CSP watch of `conftest.CspWatch`."""

import re
from datetime import UTC, datetime
from math import floor

import pixel_manifest  # noqa: F401  (before pixel_kla: the two import each other, and the manifest must load first)
import pytest
from accountkit import done_job
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_kla import set_key
from pixel_seed import WALLETS
from test_admin_alert_groups import group_of, plant_unclear
from test_e2e_keys_logs_alerts import log, open_tab, rows

pytestmark = pytest.mark.browser

TIMEOUT_MS = 10_000
WIDTHS = ((1440, 900), (768, 1024))
BOXES_JS = """() => {
  window.__most_boxes = 0;
  new MutationObserver(() => {
    window.__most_boxes = Math.max(window.__most_boxes, document.querySelectorAll('.el-message-box').length);
  }).observe(document.body, {childList: true, subtree: true});
}"""


def goto_keys(app, page, width=1440, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(app.url + "#/keys")
    expect(page.get_by_role("heading", name="API keys", level=1)).to_be_visible()


def new_key_button(page):
    return page.get_by_role("button", name="New key").first


def form(page):
    """The key form drawer (the one dialog of the page; a discard question is an alertdialog)."""
    return page.get_by_role("dialog")


def question(page):
    return page.locator(".el-message-box")


def open_form(page):
    new_key_button(page).click()
    expect(form(page)).to_be_visible()
    return form(page)


def typed_form(page, label="half typed"):
    box = open_form(page)
    box.get_by_label("Label", exact=True).fill(label)
    return box


def create_clicked(page):
    form(page).get_by_role("button", name="Create key").click()


def held_creates(page):
    """Hold a create at the network until the test lets it go; every other call goes through."""
    held = []
    page.route("**/admin/keys", lambda r: held.append(r) if r.request.method == "POST" else r.continue_())
    return held


def label_of(db, key_id):
    return db.one("SELECT label FROM api_keys WHERE id = %s", key_id)[0]


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


# ---- the frame ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("width, height", WIDTHS)
def test_the_new_key_form_is_a_right_drawer_named_by_its_heading(app, app_session, db, width, height):
    page = app_session
    goto_keys(app, page, width, height)
    box = open_form(page)
    expect(page.get_by_role("dialog", name="New API key")).to_be_visible()
    expect(box.get_by_role("heading", name="New API key")).to_be_visible()
    got = box_of(page.get_by_test_id("key-drawer"))
    assert abs(got["width"] - 620) < 1 and abs(got["x"] + got["width"] - width) < 1, (
        "a right drawer 620 px wide: %r" % got)
    assert page.locator(".el-dialog").count() == 0
    expect(box.get_by_role("button", name="Close")).to_be_visible()
    assert box.get_by_role("button", name="Back to API keys").count() == 0, "Back is the phone's"


def test_the_edit_key_form_is_named_by_its_heading_too(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    goto_keys(app, page)
    rows(page).filter(has_text="rk-plain").get_by_role("button", name="Edit").click()
    expect(page.get_by_role("dialog", name="Edit API key — plain")).to_be_visible()
    expect(form(page).get_by_role("heading", name="Edit API key — plain")).to_be_visible()


def test_on_a_phone_the_form_is_a_full_screen_sheet_with_44_px_footer_buttons(app, app_session, db):
    page = app_session
    goto_keys(app, page, 390, 844)
    box = open_form(page)
    got = box_of(page.get_by_test_id("key-drawer"))
    assert abs(got["x"]) < 1 and abs(got["width"] - 390) < 1, "the whole screen: %r" % got
    expect(box.get_by_role("button", name="Back to API keys")).to_be_visible()
    assert box.get_by_role("button", name="Close").count() == 0
    for name in ("Cancel", "Create key"):
        height = box_of(box.get_by_role("button", name=name))["height"]
        assert height == 44, "%s is %s px high" % (name, height)
    expect(box.get_by_role("heading", name="New API key", level=1)).to_be_visible()


@pytest.mark.parametrize("width, height", [(1440, 900), (390, 844)])
def test_the_footer_buttons_sit_at_the_right_of_a_drawer_and_wrap_on_a_sheet(app, app_session, db, width, height):
    page = app_session
    goto_keys(app, page, width, height)
    box = open_form(page)
    cancel, create = (box_of(box.get_by_role("button", name=n)) for n in ("Cancel", "Create key"))
    foot = box_of(page.locator(".detail-drawer__foot"))
    if width >= 768:
        assert abs(create["x"] + create["width"] - (foot["x"] + foot["width"] - 24)) < 1, "flush right"
        assert cancel["x"] < create["x"]


@pytest.mark.parametrize("width, height", [(1440, 900), (768, 1024), (390, 844)])
def test_every_model_checkbox_shares_the_left_edge_of_the_any_model_checkbox(app, app_session, db, width, height):
    page = app_session
    goto_keys(app, page, width, height)
    open_form(page)
    page.get_by_test_id("key-drawer").locator(".kd__any input").wait_for()
    edges = page.evaluate(
        """() => ({
          any: document.querySelector('.kd__any input').getBoundingClientRect().left,
          rows: [...document.querySelectorAll('.kd__model input')].map((i) => i.getBoundingClientRect().left),
        })""", isolated_context=False)
    assert edges["rows"], "the catalog shows models"
    assert all(abs(x - edges["any"]) < 0.5 for x in edges["rows"]), "model checkboxes at %r, Any model at %r" % (
        edges["rows"], edges["any"])


def test_on_a_phone_cancel_and_create_key_are_two_equal_buttons_in_the_footer_row(app, app_session, db):
    page = app_session
    goto_keys(app, page, 390, 844)
    box = open_form(page)
    cancel, create = (box_of(box.get_by_role("button", name=n)) for n in ("Cancel", "Create key"))
    foot = box_of(page.locator(".detail-drawer__foot"))
    assert abs(cancel["y"] - create["y"]) < 0.5, "one row"
    assert cancel["height"] == create["height"] == 44
    assert abs(cancel["width"] - create["width"]) < 1, "equal widths: %r %r" % (cancel, create)
    assert abs(create["x"] - (cancel["x"] + cancel["width"]) - 8) < 0.5, "an 8 px gap"
    assert abs(cancel["x"] - (foot["x"] + 16)) < 0.5, "16 px from the left edge"
    assert abs(create["x"] + create["width"] - (foot["x"] + 390 - 16)) < 0.5, "16 px from the right edge"
    assert abs(cancel["y"] - (foot["y"] + 10 + 1)) < 1.5, "10 px padding under the rule"


# ---- focus -------------------------------------------------------------------------------------------------------

def test_focus_stays_in_the_drawer_and_comes_back_to_the_button_that_opened_it(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    new_key_button(page).focus()
    new_key_button(page).press("Enter")
    expect(form(page)).to_be_visible()
    for _ in range(40):
        page.keyboard.press("Tab")
        assert active_in_drawer(page), "focus left the drawer"
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
        assert active_in_drawer(page), "focus left the drawer"
    page.keyboard.press("Escape")
    expect(form(page)).to_have_count(0)
    expect(new_key_button(page)).to_be_focused()


def test_focus_goes_to_the_new_key_button_when_the_row_that_opened_the_form_is_gone(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    goto_keys(app, page)
    rows(page).filter(has_text="rk-plain").get_by_role("button", name="Edit").click()
    expect(form(page)).to_be_visible()
    # the list the save reads again no longer holds the key, so the button that opened the form is gone
    page.route("**/admin/keys", lambda r: r.fulfill(status=200, content_type="application/json", body='{"items": []}')
               if r.request.method == "GET" else r.continue_())
    form(page).get_by_role("button", name="Save").click()
    expect(form(page)).to_have_count(0)
    expect(page.get_by_text("No API keys yet.")).to_be_visible()
    expect(new_key_button(page)).to_be_focused()


def test_the_drawer_is_named_by_its_heading_for_the_screen_reader(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    open_form(page)
    drawer = page.get_by_test_id("key-drawer")
    assert drawer.get_attribute("aria-label") is None
    target = drawer.get_attribute("aria-labelledby")
    assert page.locator("[id='%s']" % target).inner_text() == "New API key"
    expect(page.get_by_role("dialog", name="New API key", exact=True)).to_be_visible()


# ---- a select inside the drawer ----------------------------------------------------------------------------------

def test_a_mode_list_opens_over_the_drawer_escape_closes_only_the_list_and_a_press_on_its_padding_keeps_focus(
        app, app_session, db):
    page = app_session
    goto_keys(app, page)
    box = open_form(page)
    combo = box.get_by_role("group", name="fake video", exact=True).get_by_role("combobox", name="Mode")
    combo.focus()
    page.keyboard.press("ArrowDown")
    listbox = page.get_by_role("listbox")
    expect(listbox).to_be_visible()
    on_top = page.evaluate("""() => {
      const el = document.querySelector('[role=listbox]'), r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return Boolean(top && el.contains(top));
    }""", isolated_context=False)
    assert on_top, "the list is under the drawer"
    # a press on the list's own padding (6 px) must not move the focus off the select
    at = box_of(listbox)
    page.mouse.move(at["x"] + at["width"] / 2, at["y"] + 2)
    page.mouse.down()
    page.mouse.up()
    expect(listbox).to_be_visible()
    expect(combo).to_be_focused()
    page.keyboard.press("Escape")
    expect(listbox).to_have_count(0)
    expect(form(page)).to_be_visible()
    expect(question(page)).to_have_count(0)
    expect(combo).to_be_focused()


# ---- closing -----------------------------------------------------------------------------------------------------

def test_a_click_beside_the_drawer_is_escape_clean_it_closes_typed_it_asks(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    open_form(page)
    page.mouse.click(40, 400)
    expect(form(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    box = typed_form(page)
    page.mouse.click(40, 400)
    expect(question(page)).to_have_count(1)
    expect(question(page)).to_contain_text("Discard what you typed?")
    question(page).get_by_role("button", name="Keep editing").click()
    expect(question(page)).to_have_count(0)
    expect(box.get_by_label("Label", exact=True)).to_have_value("half typed")
    page.mouse.click(40, 400)
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Discard").click()
    expect(form(page)).to_have_count(0)
    assert db.one("SELECT count(*) FROM api_keys")[0] == 0


@pytest.mark.parametrize("way", ("escape", "close", "cancel"))
def test_escape_the_x_and_cancel_on_a_typed_form_ask_once_keep_editing_keeps_it_and_discard_closes_it(
        app, app_session, db, way):
    page = app_session
    goto_keys(app, page)
    box = typed_form(page)

    def leave():
        if way == "escape":
            page.keyboard.press("Escape")
        else:
            box.get_by_role("button", name="Close" if way == "close" else "Cancel", exact=True).click()

    leave()
    expect(question(page)).to_have_count(1)
    expect(question(page)).to_contain_text("Discard what you typed?")
    question(page).get_by_role("button", name="Keep editing").click()
    expect(question(page)).to_have_count(0)
    expect(form(page)).to_be_visible()
    expect(box.get_by_label("Label", exact=True)).to_have_value("half typed")
    leave()
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Discard").click()
    expect(form(page)).to_have_count(0)
    assert db.one("SELECT count(*) FROM api_keys")[0] == 0
    # the next form starts empty
    box = open_form(page)
    expect(box.get_by_label("Label", exact=True)).to_have_value("")


def test_two_ways_out_in_a_row_never_put_up_two_questions(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    box = typed_form(page)
    page.evaluate(BOXES_JS, isolated_context=False)
    page.keyboard.press("Escape")
    expect(question(page)).to_have_count(1)
    page.keyboard.press("Escape")             # the question's own Escape: it closes only itself (keep)
    expect(question(page)).to_have_count(0)
    expect(form(page)).to_be_visible()
    box.get_by_role("button", name="Close", exact=True).click()
    expect(question(page)).to_have_count(1)
    page.keyboard.press("Escape")
    expect(question(page)).to_have_count(0)
    expect(form(page)).to_be_visible()
    expect(box.get_by_label("Label", exact=True)).to_have_value("half typed")
    assert page.evaluate("window.__most_boxes", isolated_context=False) == 1, "two questions at once"


def test_a_form_that_was_typed_in_and_put_back_as_it_was_closes_without_asking(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    box = typed_form(page, "abc")
    box.get_by_label("Label", exact=True).fill("")
    page.keyboard.press("Escape")
    expect(form(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)


def test_while_a_create_is_in_flight_every_way_out_does_nothing_and_a_good_answer_closes_without_asking(
        app, app_session, db):
    page = app_session
    goto_keys(app, page)
    held = held_creates(page)
    box = typed_form(page, "slow one")
    create_clicked(page)
    expect(box.get_by_role("button", name="Create key")).to_be_disabled()
    page.keyboard.press("Escape")
    for name in ("Close", "Cancel"):
        expect(box.get_by_role("button", name=name, exact=True)).to_be_disabled()
        box.get_by_role("button", name=name, exact=True).click(force=True)
    page.mouse.click(40, 400)
    page.wait_for_timeout(400)
    expect(form(page)).to_be_visible()
    expect(question(page)).to_have_count(0)
    assert len(held) == 1
    held[0].continue_()
    expect(form(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    callout = page.get_by_role("textbox", name="New API key")
    expect(callout).to_be_visible()
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1


def test_a_good_save_brings_the_banner_into_view_and_puts_focus_in_its_field(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 1440, "height": 420})
    for n in range(10):
        key = new_key(db)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", "filler %d" % n, key)
    page.goto(app.url + "#/keys")
    expect(page.get_by_role("heading", name="API keys", level=1)).to_be_visible()
    expect(rows(page)).to_have_count(10)
    wrap = page.locator(".app-scroll__wrap")
    page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop = 400", isolated_context=False)
    open_tab_scroll = wrap.evaluate("el => el.scrollTop")
    assert open_tab_scroll > 0, "the page scrolls, so the banner can start out of sight"
    page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop = 0", isolated_context=False)
    typed_form(page, "banner key")
    create_clicked(page)
    callout = page.get_by_role("textbox", name="New API key")
    expect(callout).to_be_visible()
    expect(callout).to_be_focused()
    expect(callout).to_be_in_viewport()


def test_a_pending_question_closes_as_keep_when_the_form_is_closed_under_it(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    typed_form(page, "closed under the question")
    page.keyboard.press("Escape")
    expect(question(page)).to_have_count(1)
    # the create goes out from under the question (the question blocks the pointer, not a script)
    page.evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Create key').click()",
                  isolated_context=False)
    expect(form(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    expect(page.get_by_role("textbox", name="New API key")).to_be_visible()
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1
    expect(page.locator(".el-message-box")).to_have_count(0)
    expect(page.locator(".el-overlay").locator("visible=true")).to_have_count(0)


def test_browser_back_with_a_typed_form_offers_the_question_first(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(app.url + "#/jobs")
    open_tab(page, "API keys")
    typed_form(page, "typed before back")
    page.go_back()
    expect(question(page)).to_contain_text("Discard what you typed?")
    question(page).get_by_role("button", name="Keep editing").click()
    expect(question(page)).to_have_count(0)
    expect(page).to_have_url(re.compile(r"#/keys$"))
    expect(form(page).get_by_label("Label", exact=True)).to_have_value("typed before back")
    page.go_back()
    expect(question(page)).to_have_count(1)
    question(page).get_by_role("button", name="Discard").click()
    expect(page).to_have_url(re.compile(r"#/jobs"))


def test_a_clean_form_leaves_with_browser_back_without_asking(app, app_session, db):
    page = app_session
    page.goto(app.url + "#/jobs")
    open_tab(page, "API keys")
    open_form(page)
    page.go_back()
    expect(page).to_have_url(re.compile(r"#/jobs"))
    expect(question(page)).to_have_count(0)


def test_a_reload_with_a_typed_form_is_warned_about(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    typed_form(page)
    warned = page.evaluate("""() => {
      const e = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(e);
      return e.defaultPrevented;
    }""", isolated_context=False)
    assert warned


# ---- the label is optional ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("typed", ("", "   "))
def test_a_key_with_no_label_is_named_by_its_prefix_everywhere(app, app_session, db, typed):
    page = app_session
    goto_keys(app, page)
    open_form(page)
    if typed:
        form(page).get_by_label("Label", exact=True).fill(typed)
    create_clicked(page)
    banner = page.get_by_test_id("new-key")
    expect(banner).to_be_visible()
    key_id, label, prefix = db.one("SELECT id, label, key_prefix FROM api_keys")
    assert label == "", "the server stores a label of spaces as none: %r" % label
    expect(banner).to_contain_text("Key created for %s." % prefix)
    assert "Key created for ." not in banner.inner_text()
    # the list: the prefix in the label cell, muted and mono, no "(no label)", no copy-prefix button
    cell = rows(page).first.locator(".k-label")
    expect(cell).to_have_text(prefix)
    expect(page.get_by_text("(no label)")).to_have_count(0)
    assert "mono" in (cell.get_attribute("class") or "")
    muted, body = (page.evaluate("sel => getComputedStyle(document.querySelector(sel)).color", sel,
                                 isolated_context=False) for sel in (".k-label--prefix", ".keys-note"))
    assert muted == body, "the prefix is drawn in the muted colour (the note under the table is the muted text)"
    # a job, a log and an alert of this key carry its prefix
    done_job(db, key_id)
    log(db, key_id, "POST /v1/video/generations", 200)
    unclear = "Words under a key with no label"
    plant_unclear(db, key_id, unclear)
    open_tab(page, "Jobs")
    expect(page.locator(".el-table__body").first).to_contain_text(prefix)
    open_tab(page, "Request logs")
    expect(page.locator(".el-table__body").first).to_contain_text(prefix)
    api = app.url.rsplit("/manage", 1)[0]
    token = page.evaluate("sessionStorage.getItem('relay_admin_token')", isolated_context=False)
    answer = page.request.get(api + "/admin/alerts/groups/unclear_reply/%s" % group_of(unclear),
                              headers={"X-Admin-Session": token})
    assert answer.ok and answer.json()["items"][0]["key_label"] == prefix
    # the delete question names it by its prefix
    open_tab(page, "API keys")
    rows(page).first.get_by_role("button", name="Delete").click()
    expect(page.locator(".el-message-box")).to_contain_text("Delete key %s?" % prefix)


def test_a_form_can_clear_the_label_of_a_key(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    goto_keys(app, page)
    rows(page).filter(has_text="rk-plain").get_by_role("button", name="Edit").click()
    box = form(page)
    box.get_by_label("Label", exact=True).fill("")
    box.get_by_role("button", name="Save").click()
    expect(form(page)).to_have_count(0)
    assert label_of(db, key) == ""
    expect(rows(page).first.locator(".k-label")).to_have_text("rk-plain")


def test_the_raw_key_is_never_in_the_drawer_and_not_in_the_page_after_dismiss(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    typed_form(page, "secret holder")
    create_clicked(page)
    callout = page.get_by_role("textbox", name="New API key")
    expect(callout).to_be_visible()
    raw = callout.input_value()
    assert raw.startswith("rk-")
    expect(form(page)).to_have_count(0)
    assert raw not in page.content()
    page.get_by_test_id("new-key").get_by_role("button", name="Dismiss").click()
    expect(page.get_by_test_id("new-key")).to_have_count(0)
    assert raw not in page.content()
    assert raw not in page.evaluate("[...document.querySelectorAll('input, textarea')].map(e => e.value).join('|')",
                                    isolated_context=False)


# ---- keys behind the drawer: the page does not move --------------------------------------------------------------

def tall_keys(db, count=14):
    for n in range(count):
        key = new_key(db)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", "filler %d" % n, key)


@pytest.mark.parametrize("opened_by", ("button", "blank page space"))
def test_space_and_page_down_with_the_drawer_open_leave_the_page_where_it_was(app, app_session, db, opened_by):
    page = app_session
    tall_keys(db)
    page.set_viewport_size({"width": 1440, "height": 500})
    page.goto(app.url + "#/keys")
    expect(page.get_by_role("heading", name="API keys", level=1)).to_be_visible()
    expect(rows(page)).to_have_count(14)
    room = page.evaluate("(() => { const w = document.querySelector('.app-scroll__wrap'); "
                         "return w.scrollHeight - w.clientHeight; })()", isolated_context=False)
    assert room > 300, "the page has room to scroll: %s" % room
    if opened_by == "button":
        new_key_button(page).click()
    else:
        page.mouse.click(700, 140)              # blank space beside the heading: focus is on the page, not a button
        page.evaluate("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'New key').click()",
                      isolated_context=False)
    expect(form(page)).to_be_visible()
    page.wait_for_timeout(400)
    for key in ("PageDown", "Space", "PageDown", "End"):
        page.keyboard.press(key)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(200)
    top = page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop", isolated_context=False)
    assert top == 0, "the page behind the drawer scrolled to %s" % top


# ---- wallet bars -------------------------------------------------------------------------------------------------

def open_unlimited(db, label, prefix, used_day, used_total, *, limit=False):
    """A key whose video wallet has used the given figures; unlimited unless `limit`."""
    key = new_key(db, wallets=WALLETS)
    set_key(db, key, label, prefix, True, ["*"], ("limit", 100, 1000, used_day, used_total), ("closed",),
            datetime.now(UTC))
    if not limit:
        db.run("UPDATE key_wallets SET day_limit = NULL, total_limit = NULL WHERE key_id = %s AND kind = 'video'", key)
    return key


def share(used, most):
    """The rounded share the page draws (halves go up, as Math.round does)."""
    return floor(used / most * 100 + 0.5)


FILL_SHARE_JS = """el => {
  const own = el.getBoundingClientRect(), track = el.parentElement.getBoundingClientRect();
  return Math.round(own.width / track.width * 100);
}"""


@pytest.mark.parametrize("width, height", [(1440, 900), (768, 1024), (390, 844)])
def test_unlimited_wallets_draw_lighter_bars_as_a_share_of_the_busiest_key(app, app_session, db, width, height):
    page = app_session
    open_unlimited(db, "busiest", "rk-busy", 10, 40)
    open_unlimited(db, "half", "rk-half", 5, 10)
    open_unlimited(db, "idle", "rk-idle", 0, 0)
    open_unlimited(db, "capped", "rk-cap", 16, 32, limit=True)        # open wallets count toward the maximum too
    new_key(db, wallets=WALLETS)
    goto_keys(app, page, width, height)
    expect(rows(page).first if width > 767 else page.locator(".p-key").first).to_be_visible()
    scope = page.locator("tbody tr") if width > 767 else page.locator(".p-key")

    def fills(label):
        item = scope.filter(has_text=label)
        return [item.locator(".wallet__fill").nth(i).evaluate(FILL_SHARE_JS) for i in range(2)]

    # the busiest use among the open wallets is the limit key's 16 today, the busiest key's 40 in total
    assert fills("busiest") == [share(10, 16), 100]
    assert fills("half") == [share(5, 16), 25]
    assert fills("idle") == [0, 0]
    assert fills("capped") == [16, 3], "a limit wallet keeps the share of its own limit"
    relative = scope.filter(has_text="busiest").locator(".wallet__fill--relative").first
    limit_bar = scope.filter(has_text="capped").locator(".wallet__fill").first
    assert limit_bar.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert float(relative.evaluate("el => getComputedStyle(el).opacity")) < 1, "lighter than a limit bar"
    label = scope.filter(has_text="busiest").locator('[role="img"]').first.get_attribute("aria-label")
    assert label == "Used 10 today, 40 in total; the bars show the share of the busiest key's use", label


def test_a_closed_wallet_draws_no_bar_and_a_busy_key_elsewhere_is_not_a_subset(app, app_session, db):
    page = app_session
    open_unlimited(db, "only", "rk-only", 3, 6)
    closed = new_key(db, wallets=WALLETS)
    db.run("UPDATE api_keys SET label = 'closed one', key_prefix = 'rk-clsd' WHERE id = %s", closed)
    db.run("UPDATE key_wallets SET day_limit = 0, total_limit = NULL WHERE key_id = %s", closed)
    goto_keys(app, page)
    item = rows(page).filter(has_text="rk-clsd")
    expect(item).to_have_count(1)
    assert item.locator(".wallet__bars").count() == 0, "a closed wallet has no bar"
    assert rows(page).filter(has_text="rk-only").locator(".wallet__fill").first.evaluate(FILL_SHARE_JS) == 100


# ---- a create in flight keeps the page -------------------------------------------------------------------------

def test_a_create_in_flight_is_not_abandoned_by_browser_back_and_its_key_is_still_shown(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(app.url + "#/jobs")
    open_tab(page, "API keys")
    held = held_creates(page)
    typed_form(page, "in flight")
    create_clicked(page)
    expect(form(page).get_by_role("button", name="Create key")).to_be_disabled()
    assert page.evaluate("(() => { const e = new Event('beforeunload', {cancelable: true}); "
                         "window.dispatchEvent(e); return e.defaultPrevented; })()", isolated_context=False)
    page.go_back()
    page.wait_for_timeout(600)
    expect(question(page)).to_have_count(0)                       # refused without a question
    expect(page).to_have_url(re.compile(r"#/keys$"))
    expect(form(page)).to_be_visible()
    assert len(held) == 1
    held[0].continue_()
    callout = page.get_by_role("textbox", name="New API key")
    expect(callout).to_be_visible()
    assert callout.input_value().startswith("rk-")
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1
