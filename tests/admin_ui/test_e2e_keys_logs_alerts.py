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
from patchright.sync_api import expect
from test_admin_alert_groups import group_of, plant_unclear, stamp
from test_admin_alerts import plant

pytestmark = pytest.mark.browser

HOSTILE_REPLY = 'Which style? <img src=x onerror="window.__pwned=1">'
PLAIN_REPLY = "Plain wording of one key"


def open_tab(page, label):
    page.get_by_role("tab", name=label, exact=True).click()
    # the page title (an empty or failed card repeats the name in its own heading, as drawn)
    expect(page.get_by_role("heading", name=label, level=1)).to_be_visible()


def rows(page):
    """The data rows of a table: a day header row (Request logs) is not one."""
    return page.locator(".el-table__body tbody tr:not(.is-day)")


def logs_filter(page, label):
    """A Request logs filter (FilterSelect's field: a combobox, its list opened in the body)."""
    return page.locator('section[aria-label="Request log list"]').get_by_role("combobox", name=label, exact=True)


def pick(page, label, option):
    """Choose an option of a Request logs filter; the field then shows it."""
    logs_filter(page, label).click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(logs_filter(page, label).locator(".is-current")).to_have_text(option)


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
    box.get_by_label("Label", exact=True).fill("customer one")     # a new key needs a label before it is sent
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


def test_escape_closes_the_edit_key_dialog_from_a_focused_field_and_changes_nothing(app, app_session, db):
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
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert db.one("SELECT label FROM api_keys WHERE id = %s", key)[0] == "plain"


def test_the_key_prefix_copy_button_copies_the_prefix_only(app, app_session, db):
    page = app_session
    key = new_key(db)
    db.run("UPDATE api_keys SET label = 'plain', key_prefix = 'rk-plain' WHERE id = %s", key)
    open_tab(page, "API keys")
    copy = rows(page).filter(has_text="rk-plain").get_by_role("button", name="Copy key prefix of plain")
    copy.click()
    expect(page.get_by_role("alert")).to_have_count(0)               # a copy that worked says nothing
    assert page.evaluate("navigator.clipboard.readText()") == "rk-plain"


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


def test_the_filters_search_detail_refresh_and_clear(app, app_session, db):
    page = app_session
    k = new_key(db)
    job = done_job(db, k)
    db.run("UPDATE jobs SET title = %s WHERE id = %s", "Quiet 100% Lake", job)
    log(db, k, "POST /v1/video/generations", 200, job=job, request={"prompt": "a cat"})
    log(db, k, "POST /v1/video/generations", 422, error="input.invalid")
    open_tab(page, "Request logs")
    expect(rows(page)).to_have_count(2)
    pick(page, "Status", "Failed before a job")
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text("input.invalid")
    log(db, k, "POST /v1/video/generations", 429, error="rate.limited")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(rows(page)).to_have_count(2)
    pick(page, "Status", "All")
    expect(rows(page)).to_have_count(3)
    page.get_by_label("Search", exact=True).fill("100%")
    page.get_by_label("Search", exact=True).press("Enter")
    expect(rows(page)).to_have_count(1)
    rows(page).first.get_by_role("button", name="Open details of request").click()
    box = page.get_by_test_id("request-drawer")
    expect(box).to_contain_text("a cat")
    box.get_by_role("button", name="Close", exact=True).click()
    # the filters are kept across a visit to another tab
    page.get_by_role("tab", name="Jobs", exact=True).click()
    open_tab(page, "Request logs")
    expect(page.get_by_label("Search", exact=True)).to_have_value("100%")
    expect(rows(page)).to_have_count(1)
    page.get_by_label("Search", exact=True).fill("")
    page.get_by_label("Search", exact=True).press("Enter")
    expect(rows(page)).to_have_count(3)
    page.get_by_role("button", name="Clear logs").click()
    expect(page.locator(".el-message-box")).to_contain_text("Clear every request log row?")
    expect(page.locator(".el-message-box")).to_contain_text("Jobs, the ledger and alerts are kept.")
    page.locator(".el-message-box").get_by_role("button", name="Clear logs").click()
    expect(page.get_by_text("Cleared 3 rows")).to_be_visible()
    expect(page.get_by_text("No requests yet.")).to_be_visible()
    assert db.one("SELECT count(*) FROM request_logs")[0] == 0 and db.one("SELECT count(*) FROM jobs")[0] == 1


def test_an_empty_filtered_list_offers_clear_filters_and_a_failed_load_says_what_the_relay_answered(
        app, app_session, db):
    page = app_session
    k = new_key(db)
    log(db, k, "POST /v1/video/generations", 200)
    open_tab(page, "Request logs")
    expect(rows(page)).to_have_count(1)
    pick(page, "Status", "Failed before a job")
    expect(page.get_by_text("No requests match these filters.")).to_be_visible()
    page.get_by_role("button", name="Clear filters").click()
    expect(rows(page)).to_have_count(1)
    expect(logs_filter(page, "Status").locator(".is-current")).to_have_text("All")
    page.route(re.compile(r"/admin/logs\?"), lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text(
        "Could not load the request logs: the relay answered 502 (bad gateway).")


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
    expect(rows(page)).to_have_count(4)
    # two native day fields, each usable alone: a day typed into one searches at once
    start = page.get_by_label("From", exact=True)
    start.fill("2026-10-15")
    expect(rows(page)).to_have_count(3)                      # From alone
    start.fill("")
    expect(rows(page)).to_have_count(4)
    end = page.get_by_label("To", exact=True)
    end.fill("2026-10-15")
    expect(rows(page)).to_have_count(3)                      # To alone: everything before the next day
    start.fill("2026-10-15")
    expect(rows(page)).to_have_count(2)
    expect(rows(page).filter(has_text="/start-of-day")).to_have_count(1)
    expect(rows(page).filter(has_text="/last-moment")).to_have_count(1)
    assert timedelta(hours=1) <= after - day <= timedelta(hours=25)


def test_log_text_with_markup_is_shown_as_text(app, app_session, db):
    page = app_session
    k = new_key(db)
    log(db, k, "POST /v1/x", 422, error="input.invalid", request={"prompt": app.planted})
    open_tab(page, "Request logs")
    rows(page).first.get_by_role("button", name="Open details of request").click()
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


def alerts_page(page, db):
    keys = alerts_planted(db)
    open_tab(page, "Alerts")
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
    group_row(page, "which style?").get_by_role("button", name="Details").click()
    detail = page.get_by_test_id("alert-detail")
    expect(page.get_by_test_id("alert-detail-title")).to_contain_text("<img src=x")
    expect(page.get_by_test_id("alert-outcomes")).to_contain_text(
        "1 delivered after the reply, 1 refunded, 1 still open.")
    expect(detail.locator("tbody tr")).to_have_count(3)
    assert_hostile_wording_is_text(page)
    detail.locator("tbody tr", has_text="render.deadline").get_by_role("button").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("dialog").get_by_role("button", name="Close").first.click()
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
    group_row(page, "API read failure").get_by_role("button", name="Details").click()
    expect(page.get_by_test_id("alert-read-failure")).to_be_visible()
    group_row(page, "clamp").get_by_role("button", name="Details").click()
    expect(detail.locator("tbody tr")).to_have_count(1)
    detail.get_by_role("button", name="Close").click()
    expect(detail).to_have_count(0)
    group_row(page, "plain wording").get_by_role("button", name="Acknowledge").click()
    expect(groups(page)).to_have_count(4)
    assert posted[1].post_data_json == {"through_id": max(alert_rows(db, plain))}
    # the Alerts tab counts the open groups (the banner is on Jobs only)
    expect(page.get_by_role("tab", name=re.compile(r"^Alerts")).first).to_contain_text("4")


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
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"


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
    # below 1280 px the one action of the page sits in the More menu
    page.get_by_role("button", name="More actions").click()
    expect(page.get_by_role("menuitem", name="Clear logs")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("menuitem", name="Clear logs")).to_have_count(0)
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
    page.get_by_role("button", name=re.compile(r"^Open request")).first.click()
    drawer = page.get_by_test_id("request-drawer")
    expect(drawer).to_be_visible()
    for target in (drawer.get_by_role("button", name="Copy Request body"),
                   drawer.get_by_role("button", name="Copy Answer body"),
                   drawer.locator(".rd__link")):
        expect(target).to_be_visible()
        box = target.bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44, box
