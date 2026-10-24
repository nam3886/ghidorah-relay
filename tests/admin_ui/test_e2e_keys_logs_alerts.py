"""The API keys, Request logs and Alerts tabs of the Vue admin in Chrome against the real app (browser tier): the
one-time key (shown once, copied over plain http, gone from the page and every store), the logs date window, and the
alert groups with their detail, acknowledge and generation guard. Every test runs under the CSP watch of
`conftest.CspWatch`."""

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from page_helpers import click_tab, settle, sideways
from patchright.sync_api import expect
from test_admin_alert_groups import group_of, plant_unclear, stamp
from test_admin_alerts import plant

pytestmark = pytest.mark.browser

HOSTILE_REPLY = 'Which style? <img src=x onerror="window.__pwned=1">'
PLAIN_REPLY = "Plain wording of one key"


def open_tab(page, label):
    click_tab(page, label)
    # the page title (an empty or failed card repeats the name in its own heading, as drawn)
    expect(page.get_by_role("heading", name=label, level=1)).to_be_visible()


def rows(page):
    """The data rows of a table: a day header row (Request logs) is not one."""
    return page.locator(".el-table__body tbody tr:not(.is-day)")


# ---- API keys ------------------------------------------------------------------------------------------------------


def mode(box, wallet, option):
    """Choose a wallet's Mode in the key form (FilterSelect's field: a combobox in the wallet's group, its list opened
    in the body)."""
    field = box.get_by_role("group", name=wallet, exact=True).get_by_role("combobox", name="Mode")
    field.click()
    box.page.get_by_role("option", name=option, exact=True).click()
    expect(field).to_contain_text(option)


def key_places(page, raw):
    """Every place of the page the raw key could be found in, except the callout's own field."""
    found = []
    if raw in page.content():
        found.append("dom")
    stores = page.evaluate("JSON.stringify([Object.entries(sessionStorage), Object.entries(localStorage)])")
    if raw in stores:
        found.append("storage")
    if raw in page.url:
        found.append("url")
    if raw in page.evaluate("[...document.querySelectorAll('input, textarea')].map(e => e.value).join('|')"):
        found.append("field")
    if raw in page.evaluate("JSON.stringify(history.state)"):
        found.append("history")
    databases = page.evaluate("indexedDB.databases().then(list => JSON.stringify(list))")
    if raw in databases:
        found.append("indexeddb")
    if raw in " ".join(page.locator(".el-message").all_inner_texts()):
        found.append("toast")
    return found


def create_key(page, label="customer one"):
    page.get_by_role("button", name="New key").first.click()
    box = page.get_by_role("dialog")
    box.get_by_label("Label", exact=True).fill(label)
    mode(box, "fake video", "limit")
    box.get_by_label("fake video per day").fill("5")
    mode(box, "fake image", "unlimited")
    box.get_by_role("button", name="Create key").click()
    # the dialog has the same name; the field is the textbox
    callout = page.get_by_role("textbox", name="New API key")
    expect(callout).to_be_visible()
    return callout.input_value()


def test_create_shows_the_raw_key_once_copies_it_over_plain_http_and_it_is_gone_after_dismiss_and_reload(
        app, app_session, db):
    page = app_session
    open_tab(page, "API keys")
    # the owner opens the admin over plain http on the LAN, which is not a secure context: the page takes its fallback
    page.evaluate("Object.defineProperty(window, 'isSecureContext', { value: false })", isolated_context=False)
    raw = create_key(page)
    assert raw.startswith("rk-") and db.one("SELECT count(*) FROM api_keys")[0] == 1
    # the callout's own field (its value only: a property of the input, so the markup holds no copy of it) is the
    # only place: no storage, no address
    assert key_places(page, raw) == ["field"]
    assert page.content().count(raw) == 0
    expect(page.get_by_test_id("new-key")).to_contain_text("Key created for customer one.")
    expect(page.get_by_test_id("new-key")).to_contain_text("Copy it now: it is shown once and not stored.")
    copy = page.get_by_test_id("new-key").get_by_role("button", name="Copy")
    copy.click()
    expect(page.get_by_test_id("new-key").get_by_role("button", name="Copied")).to_be_visible()
    assert page.evaluate("navigator.clipboard.readText()") == raw, "the clipboard holds the key"
    assert raw not in json.dumps(db.all("SELECT key_hash, key_prefix FROM api_keys")), "only the hash is stored"
    row = rows(page).first
    expect(row).to_contain_text("customer one")
    expect(row).to_contain_text("fake video")
    expect(row).to_contain_text("day 0/5")
    expect(row).to_contain_text("fake image")
    expect(row).to_contain_text("unlimited")
    page.get_by_test_id("new-key").get_by_role("button", name="Dismiss").click()
    expect(page.get_by_test_id("new-key")).to_have_count(0)
    assert key_places(page, raw) == [], "gone after Dismiss"
    page.reload()
    expect(page.get_by_role("heading", name="API keys")).to_be_visible()
    assert key_places(page, raw) == [], "gone after a reload"


def test_a_real_401_while_the_callout_is_visible_clears_the_key(app, app_session, db):
    page = app_session
    open_tab(page, "API keys")
    raw = create_key(page)
    assert page.evaluate("indexedDB.databases().then(list => list.length)") == 0
    db.run("DELETE FROM admin_sessions")                         # the session is gone on the server
    rows(page).first.get_by_role("switch").click()                 # the next admin call answers 401
    expect(page).to_have_url(re.compile(r"#/login$"))
    expect(page.get_by_test_id("new-key")).to_have_count(0)
    assert key_places(page, raw) == [], "gone after a 401"
    page.problems.clear()                                       # the 401 is a console line


def test_a_failed_create_shows_fixed_text_and_never_what_the_server_said(app, app_session, db):
    page = app_session
    open_tab(page, "API keys")
    fake = "rk-FAKE-SECRET-NOT-A-REAL-KEY"
    body = {"error": {"code": "input.invalid", "message": "bad " + fake, "param": "label " + fake}}
    page.route("**/admin/keys", lambda r: r.fulfill(status=422, content_type="application/json",
                                                     body=json.dumps(body)) if r.request.method == "POST"
               else r.continue_())
    page.get_by_role("button", name="New key").first.click()
    box = page.get_by_role("dialog")
    box.get_by_label("Label", exact=True).fill("customer one")
    box.get_by_role("button", name="Create key").click()
    expect(box).to_contain_text("Some of the values are not accepted. Check the highlighted field.")
    assert key_places(page, fake) == []
    assert fake not in page.locator("body").inner_text()
    assert db.one("SELECT count(*) FROM api_keys")[0] == 0
    page.problems.clear()                                       # the 422 is a console line


def test_the_raw_key_is_gone_when_the_tab_is_left_and_when_the_operator_signs_out(app, app_session, db):
    page = app_session
    open_tab(page, "API keys")
    raw = create_key(page)
    page.get_by_role("tab", name="Jobs", exact=True).click()
    open_tab(page, "API keys")
    expect(page.get_by_test_id("new-key")).to_have_count(0)
    assert key_places(page, raw) == []
    raw = create_key(page, "second")
    app.sign_out(page)
    expect(page).to_have_url(re.compile(r"#/login$"))
    assert key_places(page, raw) == [], "gone on sign-out"
    app.sign_in(page)
    open_tab(page, "API keys")
    expect(page.get_by_test_id("new-key")).to_have_count(0)


def test_edit_disable_and_delete_with_and_without_jobs(app, app_session, db):
    page = app_session
    plain = new_key(db)
    used = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", plain)
    db.run("UPDATE api_keys SET label = 'used', key_prefix = 'rk-used' WHERE id = %s", used)
    done_job(db, used)
    open_tab(page, "API keys")
    sent = []
    page.on("request", lambda r: sent.append(r.post_data_json) if r.method == "PATCH" else None)
    row = rows(page).filter(has_text="rk-plain")
    row.get_by_role("button", name="Edit").click()
    box = page.get_by_role("dialog")
    box.get_by_label("Label", exact=True).fill("plain renamed")
    box.get_by_role("button", name="Save").click()
    expect(rows(page).filter(has_text="plain renamed")).to_have_count(1)
    assert sent[0]["label"] == "plain renamed" and sent[0]["models"] == ["*"]
    assert db.one("SELECT label FROM api_keys WHERE id = %s", plain)[0] == "plain renamed"
    switch = rows(page).filter(has_text="plain renamed").get_by_role("switch")
    switch.click()
    expect(switch).not_to_be_checked()
    assert db.one("SELECT enabled FROM api_keys WHERE id = %s", plain)[0] is False
    # delete: a key with no job goes, a key with jobs is only disabled
    rows(page).filter(has_text="plain renamed").get_by_role("button", name="Delete").click()
    expect(page.locator(".el-message-box")).to_contain_text("Delete key plain renamed?")
    expect(page.locator(".el-message-box")).to_contain_text("A key that has jobs is only disabled.")
    page.locator(".el-message-box").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Key deleted")).to_be_visible()
    assert db.one("SELECT count(*) FROM api_keys WHERE id = %s", plain)[0] == 0
    rows(page).filter(has_text="rk-used").get_by_role("button", name="Delete").click()
    page.locator(".el-message-box").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Key has jobs: disabled instead")).to_be_visible()
    assert db.one("SELECT enabled FROM api_keys WHERE id = %s", used)[0] is False


def test_escape_on_a_typed_edit_key_form_asks_first_and_discarding_changes_nothing(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    open_tab(page, "API keys")
    rows(page).filter(has_text="rk-plain").get_by_role("button", name="Edit").click()
    box = page.get_by_role("dialog")
    label = box.get_by_label("Label", exact=True)
    label.fill("typed, never saved")
    label.focus()
    page.keyboard.press("Escape")
    ask = page.locator(".el-message-box")
    expect(ask).to_contain_text("Discard what you typed?")
    ask.get_by_role("button", name="Discard").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert db.one("SELECT label FROM api_keys WHERE id = %s", key)[0] == "plain"


def test_escape_on_an_edit_key_form_nobody_typed_in_closes_it_without_asking(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    open_tab(page, "API keys")
    rows(page).filter(has_text="rk-plain").get_by_role("button", name="Edit").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.locator(".el-message-box")).to_have_count(0)


def test_no_row_has_a_copy_prefix_button_and_the_prefix_is_still_there(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    open_tab(page, "API keys")
    expect(rows(page).filter(has_text="rk-plain")).to_have_count(1)
    expect(page.get_by_role("button", name=re.compile("^Copy key prefix"))).to_have_count(0)
    expect(page.locator('[title="Copy key prefix"]')).to_have_count(0)


def test_a_key_label_with_markup_is_shown_as_text(app, app_session, db):
    page = app_session
    k = new_key(db)
    db.run("UPDATE api_keys SET label = %s WHERE id = %s", app.planted, k)
    open_tab(page, "API keys")
    expect(rows(page).first).to_contain_text("<b>planted</b>")
    page.wait_for_timeout(300)
    app.assert_planted_as_text(page)


# ---- Request logs --------------------------------------------------------------------------------------------------

def log(db, key, route, status=200, *, at_ms=None, job=None, error=None, request=None):
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, error_code, "
           "body_redacted, at) VALUES (%s, 'fake', %s, %s, %s, 7, %s, %s, "
           "CASE WHEN %s::float8 IS NULL THEN clock_timestamp() ELSE to_timestamp(%s::float8 / 1000.0) END)",
           key, route, status, job, error, json.dumps(request or {}), at_ms, at_ms)


def log_rows(page):
    """The data rows of the Request logs table (a desktop and a tablet; a phone has a list of buttons)."""
    return page.locator('[data-testid="logs-table"] tbody tr')


def request_buttons(page):
    """The button that opens one request: on a row of the table, and the row of the phone list."""
    return page.get_by_role("button", name=re.compile(r"^Open request"))


def status_button(page, label):
    """One choice of the Status filter: a segment on a desktop, a chip below it."""
    return page.get_by_role("group", name="Status", exact=True).get_by_role("button", name=label, exact=True)


def card_chips(page):
    """The Provider chips of the card: Request logs has no provider strip."""
    return page.get_by_test_id("logs-provider-all")


def show_dates(page, width):
    """The date range sits in the toolbar on a desktop and in the Filters sheet below 1280 px."""
    if width < 1280:
        page.get_by_role("button", name="Filters: dates").click()
        expect(page.get_by_test_id("logs-filters-sheet")).to_be_visible()


# the header's bottom padding: how far the line under the title and the chips is from the last of them
HEAD_GAP_JS = """() => {
  const head = document.querySelector('[data-testid=request-drawer] .rl-drawer__head');
  const sub = head.querySelector('.rl-drawer__sub');
  const line = head.closest('.el-drawer__header').getBoundingClientRect().bottom - 1;
  return Math.round(line - sub.getBoundingClientRect().bottom);
}"""


@pytest.mark.parametrize("width", (1440, 390))
def test_a_drawer_header_keeps_14_px_under_its_pill_and_route(app, app_session, db, width):
    """14 px from the pill and route line to the rule under the header, on the drawer and on the phone."""
    page = app_session
    page.set_viewport_size({"width": width, "height": 844})
    k = new_key(db)
    log(db, k, "POST /v1/video/generations", 422, error="input.invalid")
    open_tab(page, "Request logs")
    request_buttons(page).first.click()
    expect(page.get_by_test_id("request-drawer")).to_be_visible()
    got = page.evaluate(HEAD_GAP_JS, isolated_context=False)
    assert got == 14, "the header has not 14 px under the route line at %d: %r" % (width, got)


def test_the_filters_search_detail_and_clear(app, app_session, db):
    page = app_session
    k = new_key(db)
    job = done_job(db, k)
    db.run("UPDATE jobs SET title = %s WHERE id = %s", "Quiet 100% Lake", job)
    log(db, k, "POST /v1/video/generations", 200, job=job, request={"prompt": "a cat"})
    log(db, k, "POST /v1/video/generations", 422, error="input.invalid")
    open_tab(page, "Request logs")
    search = page.get_by_label("Search", exact=True)
    expect(log_rows(page)).to_have_count(2)
    status_button(page, "Failed before a job").click()
    expect(log_rows(page)).to_have_count(1)
    expect(log_rows(page).first).to_contain_text("input.invalid")
    log(db, k, "POST /v1/video/generations", 429, error="rate.limited")
    search.press("Enter")                                     # the same search again reads the new row
    expect(log_rows(page)).to_have_count(2)
    status_button(page, "All").click()
    expect(log_rows(page)).to_have_count(3)
    search.fill("100%")
    search.press("Enter")
    expect(log_rows(page)).to_have_count(1)
    request_buttons(page).first.click()
    box = page.get_by_test_id("request-drawer")
    expect(box).to_contain_text("a cat")
    box.get_by_role("button", name="Close · Esc").click()
    # the filters are kept across a visit to another tab
    page.get_by_role("tab", name="Jobs", exact=True).click()
    open_tab(page, "Request logs")
    expect(page.get_by_label("Search", exact=True)).to_have_value("100%")
    expect(log_rows(page)).to_have_count(1)
    page.get_by_label("Search", exact=True).fill("")
    page.get_by_label("Search", exact=True).press("Enter")
    expect(log_rows(page)).to_have_count(3)
    page.get_by_role("button", name="Clear logs").click()
    expect(page.locator(".el-message-box")).to_contain_text("Clear every request log row?")
    expect(page.locator(".el-message-box")).to_contain_text("Jobs, the ledger and alerts are kept.")
    page.locator(".el-message-box").get_by_role("button", name="Clear logs").click()
    expect(page.get_by_text("Cleared 3 rows")).to_be_visible()
    expect(page.get_by_text("No requests yet")).to_be_visible()
    assert db.one("SELECT count(*) FROM request_logs")[0] == 0 and db.one("SELECT count(*) FROM jobs")[0] == 1


def test_an_empty_filtered_list_offers_clear_filters_and_a_failed_load_says_what_the_relay_answered(
        app, app_session, db):
    page = app_session
    k = new_key(db)
    log(db, k, "POST /v1/video/generations", 200)
    open_tab(page, "Request logs")
    expect(log_rows(page)).to_have_count(1)
    status_button(page, "Failed before a job").click()
    expect(page.get_by_text("No requests match these filters")).to_be_visible()
    page.get_by_role("button", name="Clear filters").click()
    expect(log_rows(page)).to_have_count(1)
    expect(status_button(page, "All")).to_have_attribute("aria-pressed", "true")
    page.route(re.compile(r"/admin/logs\?"), lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.get_by_label("Search", exact=True).press("Enter")
    expect(page.get_by_role("alert")).to_contain_text(
        "Could not load the request logs: the relay answered 502 (bad gateway).")
    page.unroute(re.compile(r"/admin/logs\?"))
    page.get_by_role("button", name="Retry").click()
    expect(log_rows(page)).to_have_count(1)


def test_the_date_window_is_the_viewers_midnights_and_ends_at_the_start_of_the_day_after_to(app, app_session, db):
    page = app_session
    zone = ZoneInfo(page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"))
    k = new_key(db)
    day = datetime(2026, 10, 15, tzinfo=zone)
    after = datetime(2026, 10, 16, tzinfo=zone)

    def ms(moment):
        return int(moment.timestamp() * 1000)

    log(db, k, "POST /before", at_ms=ms(day) - 1000)
    log(db, k, "POST /start-of-day", at_ms=ms(day))
    log(db, k, "POST /last-moment", at_ms=ms(after) - 1)
    log(db, k, "POST /next-day", at_ms=ms(after))
    open_tab(page, "Request logs")
    expect(log_rows(page)).to_have_count(4)
    # one range picker: both days typed and confirmed with Enter search at once
    start = page.get_by_label("From", exact=True)
    end = page.get_by_label("To", exact=True)
    start.click()                                            # the panel opens; the change is told when it closes
    start.fill("2026-10-15")
    end.fill("2026-10-15")
    end.press("Enter")
    expect(log_rows(page)).to_have_count(2)                  # the 15th: from its first moment to its last
    assert (start.input_value(), end.input_value()) == ("2026-10-15", "2026-10-15")
    expect(log_rows(page).filter(has_text="/start-of-day")).to_have_count(1)
    expect(log_rows(page).filter(has_text="/last-moment")).to_have_count(1)
    # a range typed and left by Tab (the blur of the keyboard) is applied too
    start.click()
    start.fill("2026-10-14")
    end.fill("2026-10-15")
    end.press("Tab")
    expect(log_rows(page)).to_have_count(3)                  # the 14th adds the row a second before the 15th
    # the quick options count from the page's own day: with the clock on the 16th, Today is the 16th, Yesterday the 15th
    page.clock.set_fixed_time(datetime(2026, 10, 16, 12, 0, tzinfo=zone))
    start.click()
    page.get_by_role("button", name="Today", exact=True).click()
    expect(log_rows(page)).to_have_count(1)
    expect(log_rows(page).filter(has_text="/next-day")).to_have_count(1)
    assert (start.input_value(), end.input_value()) == ("2026-10-16", "2026-10-16")
    start.click()
    page.get_by_role("button", name="Yesterday", exact=True).click()
    expect(log_rows(page)).to_have_count(2)
    assert (start.input_value(), end.input_value()) == ("2026-10-15", "2026-10-15")
    # a range picked from the open panel replaces the one held: 14 then 15 of the month the panel shows
    start.click()
    left = page.locator(
        ".el-picker-panel:visible .is-left .el-date-table td.available:not(.prev-month):not(.next-month)")
    left.filter(has_text=re.compile(r"^14$")).click()
    left.filter(has_text=re.compile(r"^15$")).click()
    expect(log_rows(page)).to_have_count(3)
    assert (start.input_value(), end.input_value()) == ("2026-10-14", "2026-10-15")
    # the clear icon empties both ends and searches again
    page.mouse.move(0, 0)                                    # a hover that starts: the icon shows on entering
    start.hover()
    page.locator(".lf__date .el-range__close-icon").click()
    expect(log_rows(page)).to_have_count(4)
    assert (start.input_value(), end.input_value()) == ("", "")
    assert timedelta(hours=1) <= after - day <= timedelta(hours=25)


RANGE_GEOMETRY_JS = """() => {
  const rect = (e) => { const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
  const editor = document.querySelector('.lf__date .el-date-editor');
  const inputs = [...editor.querySelectorAll('input')];
  return {editor: rect(editor), inputs: inputs.map(rect), names: inputs.map((i) => i.getAttribute('aria-label')),
          separator: rect(editor.querySelector('.el-range-separator')),
          icon: rect(editor.querySelector('.el-range__icon'))};
}"""


@pytest.mark.parametrize("width", (1440, 768, 390))
def test_the_range_picker_holds_two_named_inputs_a_word_between_them_and_its_icon_first_inside_one_box(
        app, app_session, width):
    """The two outer fields of the canvas are one box here, so the inside is held by geometry: the calendar icon, then
    From, then To, side by side without overlap, the word between them."""
    page = app_session
    page.set_viewport_size({"width": width, "height": 844})
    open_tab(page, "Request logs")
    show_dates(page, width)
    expect(page.get_by_label("From", exact=True)).to_have_count(1)
    expect(page.get_by_label("To", exact=True)).to_have_count(1)
    got = page.evaluate(RANGE_GEOMETRY_JS, isolated_context=False)
    first, second = got["inputs"]
    box = got["editor"]
    why = "the range picker's inner layout at %d: %r" % (width, got)
    assert got["names"] == ["From", "To"], why
    assert first[2] - first[0] > 20 and second[2] - second[0] > 20, why              # both are there, with room
    assert first[0] < second[0] and first[2] <= second[0] + 0.5, why                  # From then To, no overlap
    assert all(i[0] >= box[0] - 0.5 and i[2] <= box[2] + 0.5 for i in (first, second)), why
    sep = got["separator"]
    assert sep[2] - sep[0] > 0 and sep[0] >= first[2] - 0.5 and sep[2] <= second[0] + 0.5, why   # the word between
    icon = got["icon"]
    assert icon[2] <= first[0] + 0.5 and icon[0] >= box[0] - 0.5, why                 # the icon first, inside


TOOLBAR_JS = """() => {
  const rect = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
  const card = document.querySelector('section[aria-label="Request log list"]');
  const status = card.querySelector('.lf__seg') || card.querySelector('[role=group][aria-label=Status]');
  return {card: rect(card), status: rect(status), search: rect(card.querySelector('.lf__search')),
          dates: rect(card.querySelector('.lf__date')), wrap: !!card.querySelector('.asc--wrap')};
}"""


@pytest.mark.parametrize("width", (1440, 768, 390))
def test_the_toolbar_is_one_row_on_a_desktop_and_chips_with_search_and_dates_behind_buttons_below(
        app, app_session, width):
    page = app_session
    page.set_viewport_size({"width": width, "height": 844})
    open_tab(page, "Request logs")
    expect(status_button(page, "All")).to_be_visible()
    expect(card_chips(page)).to_be_visible()
    expect(page.locator("nav.pstrip")).to_have_count(0)
    got = page.evaluate(TOOLBAR_JS, isolated_context=False)
    card, status = got["card"], got["status"]
    why = "the toolbar at %d: %r" % (width, got)
    if width > 1279:
        search, dates = got["search"], got["dates"]
        assert abs(status[1] - dates[1]) < 30 and abs(search[1] - dates[1]) < 30, "one row: " + why
        assert status[2] <= dates[0] + 0.5 and dates[2] <= search[0] + 0.5, "Status, then dates, then search: " + why
        assert abs(search[2] - (card[2] - 20)) < 1, "the search sits at the right edge: " + why
        return
    assert got["search"] is None and got["dates"] is None, "search and dates are behind buttons: " + why
    assert status[0] >= card[0] and status[2] <= card[2] + 0.5, "the chips stay inside the card: " + why
    assert got["wrap"] == (width < 768), "a phone's four chips wrap, a tablet's scroll: " + why
    page.get_by_role("button", name="Search requests").click()
    expect(page.get_by_label("Search", exact=True)).to_be_visible()
    box = page.locator(".lf__search").bounding_box()
    assert box["height"] >= 40 and box["width"] > (card[2] - card[0]) - 40, "the search field is a full row: %r" % box
    show_dates(page, width)
    editor = page.locator('[data-testid="logs-filters-sheet"] .el-date-editor').bounding_box()
    assert editor["height"] >= 44 and editor["width"] > width - 60, "the date field fills the sheet: %r" % editor
    assert sideways(page) <= 1, "the page scrolls sideways"


CODE_FIT_JS = """() => [...document.querySelectorAll('[data-testid=request-drawer] pre')]
  .map((p) => [p.scrollWidth, p.clientWidth, getComputedStyle(p).overflowX])"""


@pytest.mark.parametrize("width", (1440, 820, 390))
def test_a_long_json_value_wraps_inside_the_code_block(app, app_session, db, width):
    """Nothing is clipped and nothing scrolls sideways: a long string wraps and the indentation stays."""
    page = app_session
    page.set_viewport_size({"width": width, "height": 900})
    k = new_key(db)
    prompt = "Neon alley in the rain, handheld, reflections on wet asphalt, a cyclist passes " * 3
    log(db, k, "POST /v1/videos", 402, error="wallet.empty", request={"prompt": prompt, "token": "x" * 160})
    db.run("UPDATE request_logs SET response_redacted = %s::jsonb", json.dumps({"error": {"message": prompt}}))
    open_tab(page, "Request logs")
    request_buttons(page).first.click()
    expect(page.get_by_test_id("request-drawer")).to_be_visible()
    page.wait_for_timeout(500)
    blocks = page.evaluate(CODE_FIT_JS, isolated_context=False)
    assert len(blocks) == 2, blocks
    for scroll, client, overflow in blocks:
        assert scroll <= client, "a code block is wider than its box at %d: %r" % (width, blocks)
        assert overflow != "scroll", blocks
    assert "  \"prompt\"" in page.get_by_test_id("request-drawer").locator("pre").first.inner_text(), "indentation kept"
    assert sideways(page) <= 1


def test_log_text_with_markup_is_shown_as_text(app, app_session, db):
    page = app_session
    k = new_key(db)
    log(db, k, "POST /v1/x", 422, error="input.invalid", request={"prompt": app.planted})
    open_tab(page, "Request logs")
    request_buttons(page).first.click()
    expect(page.get_by_test_id("request-drawer")).to_contain_text("<b>planted</b>")
    page.wait_for_timeout(300)
    app.assert_planted_as_text(page)


# ---- Alerts --------------------------------------------------------------------------------------------------------

def assert_hostile_wording_is_text(page):
    """The wording is shown lower-cased (the hub normalises it), as text: it made no element and ran no handler."""
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "the planted handler ran"
    assert page.locator("img[src='x']").count() == 0, "the planted markup became an element"
    assert "<img src=x" in page.locator("body").inner_text(), "the planted text is not shown"


def alert_rows(db, group):
    return [r[0] for r in db.all("SELECT id FROM unclear_replies WHERE group_id = %s ORDER BY id", group)]


def alerts_planted(db):
    """A clamp and a drift, a hostile wording under two keys (a delivered, a refunded and a still open job), a plain
    wording under one key, the empty wording, an acknowledged wording, and one account (a live condition)."""
    plant(db)
    k1, k2 = new_key(db), new_key(db)
    plant_unclear(db, k1, HOSTILE_REPLY, status="done")
    plant_unclear(db, k2, HOSTILE_REPLY, status="failed", error_code="render.deadline")
    plant_unclear(db, k2, HOSTILE_REPLY)
    plant_unclear(db, k1, PLAIN_REPLY)
    plant_unclear(db, k1, "")
    stamp(db, "unclear_replies", plant_unclear(db, k1, "Seen and acknowledged wording"))
    new_account(db, label="only", checked=True)
    return k1, k2


def groups(page):
    """The rows of the alert group table (a plain table, as the canvas draws it)."""
    return page.locator('section[aria-label="Alert groups"] tbody tr')


def group_row(page, text):
    return groups(page).filter(has_text=text)


def choose(page, label, option):
    """Choose an option of an Alerts filter (FilterSelect's field: a combobox, its list opened in the body)."""
    field = page.locator('section[aria-label="Alert groups"]').get_by_role("combobox", name=label, exact=True)
    field.click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(field).to_contain_text(option)


def open_group(page, text):
    """Open a group's detail drawer by its title button."""
    group_row(page, text).get_by_role("button", name=re.compile(r"^Details of")).click()
    drawer = page.get_by_test_id("alert-detail")
    expect(drawer).to_be_visible()
    # the drawer opens at once with a loading body: wait for the group itself
    expect(drawer.locator("#alertDetail")).to_be_visible()


def close_detail(page):
    drawer = page.get_by_test_id("alert-detail")
    drawer.get_by_role("button", name="Close").click()
    expect(drawer).to_be_hidden()


# Watches the page for the two drawers' bodies at once (a body exists from the drawer's opening until it has left).
OVERLAP_WATCH_JS = """() => {
  window.__overlap = false;
  window.__jobOpened = false;
  new MutationObserver(() => {
    const alert = document.querySelector('[data-testid="alert-detail"] .detail-drawer__body');
    const job = document.querySelector('[data-testid="job-drawer"] .detail-drawer__body');
    if (job) window.__jobOpened = true;
    if (alert && job) window.__overlap = true;
  }).observe(document.body, {childList: true, subtree: true});
}"""


def alerts_page(page, db):
    keys = alerts_planted(db)
    open_tab(page, "Alerts")
    # The layout of the viewport the test set: on a phone the groups are the phone list. Counting table rows there
    # passed only while the page had not yet seen its new width (the table is drawn first, then the list replaces it).
    if page.viewport_size["width"] < 768:
        expect(page.locator('section[aria-label="Alert groups"] .p-group')).to_have_count(5)
    else:
        expect(groups(page)).to_have_count(5)
    return keys


def test_the_lists_the_live_condition_the_filters_and_the_one_customer_mark(app, app_session, db):
    page = app_session
    alerts_planted(db)
    open_tab(page, "Alerts")
    expect(groups(page)).to_have_count(5)
    expect(page.get_by_test_id("alerts-live")).to_contain_text("pool: ")
    assert page.get_by_test_id("alerts-live").get_by_role("button").count() == 0, "nothing to acknowledge there"
    expect(page.get_by_test_id("alerts-more")).to_have_count(0)
    page.wait_for_timeout(300)                           # a script in the planted markup would have run by now
    hostile, plain = group_row(page, "which style?"), group_row(page, "plain wording")
    expect(hostile).to_contain_text("<img src=x")
    expect(plain).to_contain_text("One customer only: may be the customer’s own words.")
    for other in (hostile, group_row(page, "clamp"), group_row(page, "drift"), group_row(page, "API read failure")):
        assert "One customer only" not in other.inner_text()
    assert_hostile_wording_is_text(page)
    # the filters are the design's own selects (no native one); there is no field to type a phrase into
    assert page.locator("select").count() == 0, "a native select"
    assert page.locator("input:not([readonly]), textarea").count() == 0, "there is no phrase field"
    choose(page, "Status", "Acknowledged")
    expect(groups(page)).to_have_count(1)
    expect(group_row(page, "seen and acknowledged")).to_be_visible()
    choose(page, "Status", "All")
    expect(groups(page)).to_have_count(6)
    choose(page, "Kind", "drift")
    expect(groups(page)).to_have_count(1)
    choose(page, "Kind", "All")
    choose(page, "Status", "Open")
    expect(groups(page)).to_have_count(5)


def test_the_detail_shows_outcomes_and_opens_a_job_and_acknowledge_sends_what_it_showed(app, app_session, db):
    page = app_session
    k1, _k2 = alerts_page(page, db)
    hostile, plain = group_of(HOSTILE_REPLY), group_of(PLAIN_REPLY)
    posted = []
    page.on("request", lambda r: posted.append(r) if r.method == "POST" and "/acknowledge" in r.url else None)
    open_group(page, "which style?")
    detail = page.get_by_test_id("alert-detail")
    expect(page.get_by_test_id("alert-detail-title")).to_contain_text("<img src=x")
    expect(page.get_by_test_id("alert-outcomes")).to_contain_text(
        "1 delivered after the reply, 1 refunded, 1 still open.")
    expect(detail.locator("tbody tr")).to_have_count(3)
    assert_hostile_wording_is_text(page)
    page.evaluate(OVERLAP_WATCH_JS, isolated_context=False)
    detail.locator("tbody tr", has_text="render.deadline").get_by_role("button").click()
    # the job opens in its own drawer and this one goes first: never two at once, not even while this one leaves
    expect(detail).to_be_hidden()
    expect(page.get_by_role("dialog")).to_have_count(1)
    assert page.evaluate("window.__jobOpened", isolated_context=False), "the job drawer never opened"
    assert not page.evaluate("window.__overlap", isolated_context=False), \
        "the job drawer opened while the alert drawer was still on the screen"
    page.get_by_role("dialog").get_by_role("button", name="Close").first.click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    open_group(page, "which style?")
    # acknowledge from the detail sends the newest row it showed; a row that arrives later keeps the group open
    seen = max(alert_rows(db, hostile))
    late = plant_unclear(db, k1, HOSTILE_REPLY)
    detail.get_by_role("button", name="Acknowledge").click()
    expect(page.get_by_text("Acknowledged 3 row(s)")).to_be_visible()
    assert posted[0].post_data_json == {"through_id": seen}
    assert db.one("SELECT acknowledged_at FROM unclear_replies WHERE id = %s", late)[0] is None, \
        "a row that arrived after the page loaded must leave the group open"
    expect(detail.locator("tbody tr")).to_have_count(4)
    # the empty wording is an API read failure; a billing group lists its rows; an acknowledge from the list
    close_detail(page)
    open_group(page, "API read failure")
    expect(page.get_by_test_id("alert-read-failure")).to_be_visible()
    close_detail(page)
    open_group(page, "clamp")
    expect(detail.locator("tbody tr")).to_have_count(1)
    close_detail(page)
    group_row(page, "plain wording").get_by_role("button", name="Acknowledge").click()
    expect(groups(page)).to_have_count(4)
    assert posted[1].post_data_json == {"through_id": max(alert_rows(db, plain))}
    assert len(posted) == 2, "one write for the drawer's Acknowledge and one for the row's"
    # the Alerts tab counts the open groups (the banner is on Jobs only)
    expect(page.get_by_role("tab", name=re.compile(r"^Alerts")).first).to_contain_text("4")


def test_the_title_button_a_row_click_and_enter_open_the_drawer_and_focus_comes_back(app, app_session, db):
    page = app_session
    alerts_page(page, db)
    drawer = page.get_by_test_id("alert-detail")
    title = group_row(page, "plain wording").get_by_role("button", name=re.compile(r"^Details of"))
    # the keyboard: Enter on the title, Escape, and focus is back on that very button
    title.focus()
    page.keyboard.press("Enter")
    expect(drawer).to_be_visible()
    expect(page.get_by_role("dialog")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(title).to_be_focused()
    # a click beside the buttons of the row: nothing had focus, so focus falls to the page's scroll area
    group_row(page, "plain wording").locator(".g-kind").click()
    expect(drawer).to_be_visible()
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(page.get_by_role("region", name="Page content")).to_be_focused()
    # a click on the row's own Acknowledge opens nothing
    page.route(re.compile(r"/acknowledge$"), lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"acknowledged": 1}'))
    group_row(page, "plain wording").get_by_role("button", name="Acknowledge").click()
    expect(page.get_by_text("Acknowledged 1 row(s)")).to_be_visible()
    expect(drawer).to_be_hidden()


def test_after_an_acknowledge_empties_the_open_list_closing_the_drawer_puts_focus_on_the_scroll_area(
        app, app_session, db):
    page = app_session
    alerts_page(page, db)
    drawer = page.get_by_test_id("alert-detail")
    title = group_row(page, "plain wording").get_by_role("button", name=re.compile(r"^Details of"))
    title.focus()
    page.keyboard.press("Enter")
    expect(drawer.locator("#alertDetail")).to_be_visible()
    drawer.get_by_role("button", name="Acknowledge").click()
    # the Open filter drops the group: its row, and the button that opened the drawer, are gone
    expect(groups(page)).to_have_count(4)
    expect(drawer.get_by_role("button", name="Acknowledge")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(page.get_by_role("region", name="Page content")).to_be_focused()


def test_the_space_key_on_the_title_button_opens_the_drawer(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 768, "height": 1024})
    alerts_page(page, db)
    drawer = page.get_by_test_id("alert-detail")
    title = group_row(page, "plain wording").get_by_role("button", name=re.compile(r"^Details of"))
    title.focus()
    page.keyboard.press("Space")
    expect(drawer).to_be_visible()
    expect(drawer.locator("#alertDetail")).to_be_visible()
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    expect(title).to_be_focused()


def test_the_drawer_ignores_every_way_out_while_an_acknowledge_runs(app, app_session, db):
    page = app_session
    alerts_page(page, db)
    held = []
    page.route(re.compile(r"/acknowledge$"), lambda r: held.append(r))
    open_group(page, "plain wording")
    drawer = page.get_by_test_id("alert-detail")
    drawer.get_by_role("button", name="Acknowledge").click()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "the acknowledge was not sent"
    page.keyboard.press("Escape")
    drawer.get_by_role("button", name="Close").click(force=True)    # inert (or natively disabled) while it writes
    page.mouse.click(5, 300)                                            # the scrim
    page.wait_for_timeout(500)
    expect(drawer).to_be_visible()
    held[0].fulfill(status=200, content_type="application/json", body='{"acknowledged": 1}')
    expect(page.get_by_text("Acknowledged 1 row(s)")).to_be_visible()
    page.mouse.click(5, 300)                                            # idle again: the scrim closes it
    expect(drawer).to_be_hidden()


def test_on_a_phone_the_filter_chips_sit_one_per_row_at_full_width(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 390, "height": 844})
    alerts_page(page, db)
    card = page.locator('section[aria-label="Alert groups"]')
    chip = "xpath=ancestor::*[contains(@class, 'is-chip')][1]"
    status = card.get_by_role("combobox", name="Status", exact=True).locator(chip)
    kind = card.get_by_role("combobox", name="Kind", exact=True).locator(chip)
    box, a, b = card.bounding_box(), status.bounding_box(), kind.bounding_box()
    # 12 px of the toolbar's padding on each side of the card (1 px border)
    for each in (a, b):
        assert abs(each["x"] - (box["x"] + 13)) <= 1 and abs(each["width"] - (box["width"] - 26)) <= 1, (each, box)
    assert b["y"] >= a["y"] + a["height"], "the Kind chip is not under the Status chip"
    assert abs(b["x"] - a["x"]) <= 1


def test_a_detail_that_cannot_be_read_keeps_the_drawer_open_with_the_error_inside(app, app_session, db):
    page = app_session
    alerts_page(page, db)
    page.route(re.compile(r"/admin/alerts/groups/[^?]+\?"), lambda r: r.fulfill(
        status=500, content_type="application/json",
        body='{"error": {"code": "server.error", "message": "the group could not be read"}}'))
    group_row(page, "plain wording").get_by_role("button", name=re.compile(r"^Details of")).click()
    drawer = page.get_by_test_id("alert-detail")
    expect(drawer.get_by_role("alert")).to_contain_text("the group could not be read")
    expect(groups(page)).to_have_count(5)
    close_detail(page)


@pytest.mark.parametrize("width, height", [(1440, 900), (768, 1024), (390, 844)])
def test_the_detail_drawer_works_from_the_keyboard_at_every_width(app, app_session, db, width, height):
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    alerts_page(page, db)
    drawer = page.get_by_test_id("alert-detail")
    page.get_by_role("button", name=re.compile(r"^Details of")).first.focus()
    page.keyboard.press("Enter")
    expect(drawer).to_be_visible()
    expect(drawer.get_by_role("button", name="Acknowledge")).to_be_visible()
    if width < 768:
        drawer.get_by_role("button", name="Back to Alerts").click()
    else:
        page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    assert sideways(page) <= 1, "the page scrolls sideways"


def test_a_slow_answer_for_an_older_view_does_not_replace_the_newer_one(app, app_session, db):
    page = app_session
    alerts_page(page, db)
    held = []

    def hold(route):
        if "status=open" in route.request.url and not held:
            held.append(route)
        else:
            route.continue_()

    page.route("**/admin/alerts/groups?*", hold)
    page.get_by_role("button", name="Refresh").click()               # the older view: held in the network layer
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "the refresh was not sent"
    choose(page, "Status", "Acknowledged")                              # the newer view
    expect(groups(page)).to_have_count(1)
    held[0].continue_()                                               # the older answer (5 open groups) arrives now
    page.wait_for_timeout(500)
    expect(groups(page)).to_have_count(1)
    expect(group_row(page, "seen and acknowledged")).to_be_visible()


# ---- the phone and the tablet ----------------------------------------------------------------------------------------

def no_sideways_scroll(page):
    """The page itself never scrolls sideways (a wide table scrolls inside its own box)."""
    assert sideways(page) <= 1, "the page scrolls sideways"


@pytest.mark.parametrize("width, height", [(390, 844), (768, 1024)])
def test_each_screen_renders_without_a_sideways_scroll_and_its_main_action_is_reachable(
        app, app_session, db, width, height):
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    k = new_key(db)
    db.run("UPDATE api_keys SET label = 'smoke key' WHERE id = %s", k)
    log(db, k, "POST /v1/video/generations", 422, error="input.invalid")
    plant_unclear(db, k, PLAIN_REPLY)
    open_tab(page, "API keys")
    expect(page.get_by_text("smoke key")).to_be_visible()
    expect(page.get_by_role("button", name="New key")).to_be_visible()
    no_sideways_scroll(page)
    open_tab(page, "Request logs")
    expect(page.get_by_text("input.invalid")).to_be_visible()
    # below 1280 px the head has the icon buttons: Search, Filters and Clear logs
    for name in ("Search requests", "Filters: dates", "Clear logs"):
        expect(page.get_by_role("button", name=name)).to_be_visible()
    no_sideways_scroll(page)
    open_tab(page, "Alerts")
    expect(page.get_by_text("plain wording")).to_be_visible()
    expect(page.get_by_role("button", name="Refresh", exact=True)).to_be_visible()
    page.get_by_role("button", name="Details of").first.click()
    expect(page.get_by_test_id("alert-detail")).to_be_visible()
    no_sideways_scroll(page)


def test_the_phone_request_drawer_actions_are_at_least_44_px_square(app, app_session, db):
    page = app_session
    page.set_viewport_size({"width": 390, "height": 844})
    k = new_key(db)
    job = done_job(db, k)
    log(db, k, "POST /v1/video/generations", 422, job=job, error="input.invalid", request={"prompt": "a cat"})
    db.run("UPDATE request_logs SET response_redacted = %s::jsonb", json.dumps({"error": "no"}))
    open_tab(page, "Request logs")
    request_buttons(page).first.click()
    drawer = page.get_by_test_id("request-drawer")
    expect(drawer).to_be_visible()
    settle(page)   # measured mid slide-in, a 44 px button read 43.99997
    for target in (drawer.get_by_role("button", name="Copy request body"),
                   drawer.get_by_role("button", name="Copy answer body"),
                   drawer.locator(".rd__link")):
        expect(target).to_be_visible()
        box = target.bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44, box
