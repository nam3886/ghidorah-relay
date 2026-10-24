"""The key forms as right drawers, in Chrome against the real app (browser tier): New key and Edit key open in the
shared `SideDrawer` (640 px on a desktop, 48 px short of the left edge on a tablet, the whole screen on a phone), carry
the close guard (what was typed is never lost to a stray Escape, Back, Close, Cancel, scrim click or browser Back; a
write in flight makes every way out inert), keep the optional label; the Keys list names a key without a label by its
prefix, draws the points wallet's balance and its caps, and Adjust wallet tops a wallet up. Every test runs under the
CSP watch of `conftest.CspWatch`."""

import re
from datetime import UTC, datetime

import pytest
from accountkit import done_job
from jobkit import key as new_key
from patchright.sync_api import expect
from test_admin_alert_groups import group_of, plant_unclear
from test_e2e_keys_logs_alerts import key_menu, key_rows, log, new_key_button, open_tab, secret

pytestmark = pytest.mark.browser


PROVIDER = "dola"
WALLETS = ((PROVIDER, None, None),)       # the caps a test key gets next to its points wallet


def wallet_values(spec: tuple) -> tuple:
    """(day_limit, total_limit, used_day, used_total) of a wallet spec: ("limit", day, total, used day, used total),
    ("unlimited", used) or ("closed",)."""
    if spec[0] == "limit":
        return spec[1], spec[2], spec[3], spec[4]
    if spec[0] == "unlimited":
        return None, None, 0, spec[1]
    return 0, None, 0, 0


def set_key(db, key_id, label, prefix, enabled, models, points, cap, created) -> None:
    """`points` is the spec of the key's points wallet (`*`), `cap` the one of its `PROVIDER` cap."""
    db.run("UPDATE api_keys SET label = %s, key_prefix = %s, enabled = %s, models = %s, max_unfinished = 5, "
           "created_at = %s WHERE id = %s", label, prefix, enabled, models, created, key_id)
    if label == "Internal test":
        db.run("UPDATE api_keys SET max_unfinished = 0 WHERE id = %s", key_id)
    for scope, spec in (("*", points), (PROVIDER, cap)):
        day, total, used_day, used_total = wallet_values(spec)
        db.run("UPDATE key_wallets SET day_limit = %s, total_limit = %s, used_day = %s, used_total = %s, "
               "day_anchor = current_date WHERE key_id = %s AND scope = %s", day, total, used_day,
               used_total, key_id, scope)
        # the ledger the counters add up from (the checker holds every test to it): what was used before today,
        # then what was used today
        for amount, day_offset in ((used_total - used_day, 1), (used_day, 0)):
            if amount:
                db.run("INSERT INTO ledger (key_id, scope, entry, amount, billing_day) VALUES (%s, %s, "
                       "'adjust', %s, current_date - %s)", key_id, scope, amount, day_offset)

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


def form(page):
    """The key form drawer (a discard question is an alertdialog, the one-time key a modal of its own)."""
    return page.get_by_test_id("key-drawer")


def done_with_secret(page):
    """Copy the one-time key (Done unlocks) and close its modal."""
    box = secret(page)[0]
    box.get_by_role("button", name="Copy").click()
    box.get_by_role("button", name="Done").click()
    expect(page.get_by_test_id("new-key")).to_have_count(0)


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
    wide = 640 if width >= 1280 else width - 48
    assert abs(got["width"] - wide) < 1 and abs(got["x"] + got["width"] - width) < 1, (
        "a right drawer %s px wide: %r" % (wide, got))
    assert page.locator(".el-dialog").count() == 0
    expect(box.get_by_role("button", name="Close · Esc")).to_be_visible()
    expect(box.get_by_role("button", name="Back to API keys")).to_be_visible()


def test_the_edit_key_form_is_named_by_its_heading_too(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    goto_keys(app, page)
    key_menu(page, "rk-plain", "Edit key")
    expect(page.get_by_role("dialog", name="Edit key · plain")).to_be_visible()
    expect(form(page).get_by_role("heading", name="Edit key · plain")).to_be_visible()
    expect(form(page)).to_contain_text("rk-plain•••• · created")


def test_on_a_phone_the_form_is_a_full_screen_sheet_with_44_px_footer_buttons(app, app_session, db):
    page = app_session
    goto_keys(app, page, 390, 844)
    box = open_form(page)
    got = box_of(page.get_by_test_id("key-drawer"))
    assert abs(got["x"]) < 1 and abs(got["width"] - 390) < 1, "the whole screen: %r" % got
    expect(box.get_by_role("button", name="Close · Esc")).to_be_visible()
    assert box.get_by_role("button", name="Back to API keys").count() == 0, "the full screen has Close only"
    for name in ("Cancel", "Create key"):
        height = box_of(box.get_by_role("button", name=name))["height"]
        assert height == 44, "%s is %s px high" % (name, height)
    expect(box.get_by_role("heading", name="New API key")).to_be_visible()


@pytest.mark.parametrize("width, height", [(1440, 900), (390, 844)])
def test_the_footer_buttons_sit_at_the_right_of_a_drawer_and_wrap_on_a_sheet(app, app_session, db, width, height):
    page = app_session
    goto_keys(app, page, width, height)
    box = open_form(page)
    cancel, create = (box_of(box.get_by_role("button", name=n)) for n in ("Cancel", "Create key"))
    foot = box_of(page.locator(".rl-drawer__foot"))
    if width >= 768:
        assert abs(create["x"] + create["width"] - (foot["x"] + foot["width"] - 24)) < 1, "flush right"
        assert cancel["x"] < create["x"]


@pytest.mark.parametrize("width, height", [(1440, 900), (768, 1024), (390, 844)])
def test_every_model_chip_sits_whole_inside_the_models_box_and_no_text_is_cut(app, app_session, db, width, height):
    page = app_session
    goto_keys(app, page, width, height)
    open_form(page)
    page.get_by_test_id("key-drawer").locator(".kf__any input").wait_for()
    found = page.evaluate(
        """() => {
          const box = document.querySelector('.kf__models').getBoundingClientRect();
          const chips = [...document.querySelectorAll('.kf__model')];
          return {
            count: chips.length,
            outside: chips.filter((c) => c.getBoundingClientRect().right > box.right + 0.5).length,
            cut: [...document.querySelectorAll('.kf *')].filter((e) => e.scrollWidth > e.clientWidth + 1
              && getComputedStyle(e).overflowX === 'hidden').map((e) => e.className),
          };
        }""", isolated_context=False)
    assert found["count"], "the catalog shows models"
    assert found["outside"] == 0 and found["cut"] == [], found


def test_on_a_phone_cancel_and_create_key_are_two_equal_buttons_in_the_footer_row(app, app_session, db):
    page = app_session
    goto_keys(app, page, 390, 844)
    box = open_form(page)
    cancel, create = (box_of(box.get_by_role("button", name=n)) for n in ("Cancel", "Create key"))
    foot = box_of(page.locator(".rl-drawer__foot"))
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
    key_menu(page, "rk-plain", "Edit key")
    expect(form(page)).to_be_visible()
    # the list the save reads again no longer holds the key, so the row menu that opened the form is gone
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
    combo = box.get_by_role("group", name="points", exact=True).get_by_role("combobox", name="Mode")
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
            box.get_by_role("button", name="Close · Esc" if way == "close" else "Cancel", exact=True).click()

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
    box.get_by_role("button", name="Close · Esc", exact=True).click()
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
    for name in ("Close · Esc", "Cancel"):
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
    expect(secret(page)[1]).to_be_visible()
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1


def test_a_good_save_puts_focus_in_the_field_of_the_one_time_key(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 1440, "height": 420})
    for n in range(10):
        key = new_key(db)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", "filler %d" % n, key)
    page.goto(app.url + "#/keys")
    expect(page.get_by_role("heading", name="API keys", level=1)).to_be_visible()
    expect(key_rows(page)).to_have_count(10)
    wrap = page.locator(".app-scroll__wrap")
    page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop = 400", isolated_context=False)
    open_tab_scroll = wrap.evaluate("el => el.scrollTop")
    assert open_tab_scroll > 0, "the page scrolls, so the banner can start out of sight"
    page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop = 0", isolated_context=False)
    typed_form(page, "banner key")
    create_clicked(page)
    field = secret(page)[1]
    expect(field).to_be_visible()
    expect(field).to_be_focused()
    expect(field).to_be_in_viewport()


def test_a_pending_question_closes_as_keep_when_the_form_is_closed_under_it(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    typed_form(page, "closed under the question")
    page.keyboard.press("Escape")
    expect(question(page)).to_have_count(1)
    # the create goes out from under the question (the question blocks the pointer, not a script)
    page.evaluate("document.querySelector('[data-testid=key-drawer] .kf__btn--primary').click()",
                  isolated_context=False)
    expect(form(page)).to_have_count(0)
    expect(question(page)).to_have_count(0)
    expect(secret(page)[1]).to_be_visible()
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1
    expect(page.locator(".el-message-box")).to_have_count(0)
    # the only overlay left is the one-time key's own modal
    expect(page.locator(".el-overlay").locator("visible=true")).to_have_count(1)
    expect(page.locator(".el-overlay").locator("visible=true").get_by_test_id("new-key")).to_have_count(1)


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
    expect(banner.get_by_role("heading")).to_have_text("Key created for %s" % prefix)
    done_with_secret(page)
    # the list: the prefix in the label cell, muted and mono, no "(no label)", no copy-prefix button
    cell = key_rows(page).first.locator(".k-label")
    expect(cell).to_have_text(prefix)
    expect(page.get_by_text("(no label)")).to_have_count(0)
    assert "mono" in (cell.get_attribute("class") or "")
    muted, body = (page.evaluate("sel => getComputedStyle(document.querySelector(sel)).color", sel,
                                 isolated_context=False) for sel in (".k-label--prefix", ".kv-sum"))
    assert muted == body, "the prefix is drawn in the muted colour (the sentence under the title is the muted text)"
    # a job, a log and an alert of this key carry its prefix
    done_job(db, key_id)
    log(db, key_id, "POST /v1/video/generations", 200)
    unclear = "Words under a key with no label"
    plant_unclear(db, key_id, unclear)
    open_tab(page, "Jobs")                          # the Ledger list draws no key: the job's detail names it
    page.locator(".jobs__table tbody tr.is-row").first.click()
    detail = page.get_by_test_id("job-drawer")
    expect(detail.locator("[data-section=summary]")).to_contain_text(prefix)
    detail.get_by_role("button", name="Close · Esc").click()
    expect(detail).to_be_hidden()
    open_tab(page, "Request logs")
    expect(page.get_by_test_id("logs-table")).to_contain_text(prefix)
    api = app.url.rsplit("/manage", 1)[0]
    token = page.evaluate("sessionStorage.getItem('relay_admin_token')", isolated_context=False)
    answer = page.request.get(api + "/admin/alerts/groups/unclear_reply/%s" % group_of(unclear),
                              headers={"X-Admin-Session": token})
    assert answer.ok and answer.json()["items"][0]["key_label"] == prefix
    # the delete question names it by its prefix
    open_tab(page, "API keys")
    key_menu(page, prefix, "Delete key")
    expect(page.locator(".el-message-box")).to_contain_text("Delete key %s?" % prefix)


def test_a_form_can_clear_the_label_of_a_key(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    goto_keys(app, page)
    key_menu(page, "rk-plain", "Edit key")
    box = form(page)
    box.get_by_label("Label", exact=True).fill("")
    box.get_by_role("button", name="Save").click()
    expect(form(page)).to_have_count(0)
    assert label_of(db, key) == ""
    expect(key_rows(page).first.locator(".k-label")).to_have_text("rk-plain")


def test_the_raw_key_is_never_in_the_drawer_and_not_in_the_page_after_dismiss(app, app_session, db):
    page = app_session
    goto_keys(app, page)
    typed_form(page, "secret holder")
    create_clicked(page)
    field = secret(page)[1]
    expect(field).to_be_visible()
    raw = field.input_value()
    assert raw.startswith("rk-")
    expect(form(page)).to_have_count(0)
    assert raw not in page.content()
    done_with_secret(page)
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
    expect(key_rows(page)).to_have_count(14)
    room = page.evaluate("(() => { const w = document.querySelector('.app-scroll__wrap'); "
                         "return w.scrollHeight - w.clientHeight; })()", isolated_context=False)
    assert room > 300, "the page has room to scroll: %s" % room
    if opened_by == "button":
        new_key_button(page).click()
    else:
        page.mouse.click(700, 140)              # blank space beside the heading: focus is on the page, not a button
        page.evaluate("document.querySelector('.kv-primary').click()", isolated_context=False)
    expect(form(page)).to_be_visible()
    page.wait_for_timeout(400)
    for key in ("PageDown", "Space", "PageDown", "End"):
        page.keyboard.press(key)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(200)
    top = page.evaluate("document.querySelector('.app-scroll__wrap').scrollTop", isolated_context=False)
    assert top == 0, "the page behind the drawer scrolled to %s" % top


# ---- the balance, the caps and Adjust wallet -----------------------------------------------------------------------

@pytest.mark.parametrize("width, height", [(1440, 900), (768, 1024), (390, 844)])
def test_an_empty_wallet_reads_0_pts_and_a_top_up_raises_its_total(app, app_session, db, width, height):
    page = app_session
    key = new_key(db, wallets=WALLETS)
    set_key(db, key, "Partner trial", "rk-trial", True, ["*"], ("limit", None, 1000, 0, 1000), ("unlimited", 0),
            datetime.now(UTC))
    goto_keys(app, page, width, height)
    row = key_rows(page).filter(has_text="Partner trial")
    expect(row).to_contain_text("0 pts")
    expect(row.get_by_test_id("cap-chip")).to_have_text(PROVIDER)        # the unlimited cap: a chip with no figure
    if width < 768:
        expect(row.locator(".pill")).to_have_text("Empty")
        row.get_by_role("button", name="Top up", exact=True).click()
        box = page.get_by_test_id("wallet-sheet")
    else:
        row.get_by_role("button", name=re.compile(r"^Adjust wallet of Partner trial")).click()
        box = page.get_by_test_id("wallet-pop")
    expect(box).to_be_visible()
    box.get_by_label("Amount").fill("500")
    expect(box).to_contain_text("500 pts")
    box.get_by_role("button", name="Top up 500").click()
    expect(row).to_contain_text("500 pts")
    assert db.one("SELECT day_limit, total_limit FROM key_wallets WHERE key_id = %s AND scope = '*'", key) == (
        None, 1500)


def test_a_closed_points_wallet_reads_closed_and_a_closed_cap_draws_no_chip(app, app_session, db):
    page = app_session
    closed = new_key(db, wallets=WALLETS)
    db.run("UPDATE api_keys SET label = 'closed one', key_prefix = 'rk-clsd' WHERE id = %s", closed)
    db.run("UPDATE key_wallets SET day_limit = 0, total_limit = NULL WHERE key_id = %s", closed)
    goto_keys(app, page)
    item = key_rows(page).filter(has_text="rk-clsd")
    expect(item).to_have_count(1)
    expect(item.locator(".wbtn__bal")).to_have_text("closed")
    expect(item.get_by_test_id("cap-chip")).to_have_count(0)
    expect(item).to_contain_text("no provider open")


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
    field = secret(page)[1]
    expect(field).to_be_visible()
    assert field.input_value().startswith("rk-")
    assert db.one("SELECT count(*) FROM api_keys")[0] == 1
