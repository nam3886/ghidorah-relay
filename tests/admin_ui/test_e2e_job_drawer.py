"""The job drawer of the Vue admin in Chrome against the real app (browser tier): its tabs and sections, the actions it
offers (Recover, Re-check, Resend webhook, Revoke, Regenerate…), the results and the media safety, and the regenerate
money guard (one click, one job: a double click, a failed send, a drawer closed mid-flight, a sign-out). Every test
runs under the CSP watch of `conftest.CspWatch` (through `app_page`): a violation on either channel fails it. Rows are
planted through the database with the helpers the API tests use (the fake provider); the page is read, and the result
is read back from the database. The list is in test_e2e_jobs.py."""

import base64
import json
import re

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from test_admin_jobs import failed_job

from relay_core import storage

pytestmark = pytest.mark.browser

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
SAFE_AFTER_LOST = "Trying again is safe"
SCRIPT_SCHEME = "java" + "script:"
REGENERATE = "Regenerate…"


def rows(page):
    return page.locator(".jobs__table tbody tr")


def drawer(page):
    return page.locator("[data-testid=job-drawer]")


def confirm(page, name: str) -> None:
    page.locator(".el-message-box").get_by_role("button", name=name, exact=True).click()


def confirm_inline(page, name: str) -> None:
    """Answer the el-popconfirm that is open (Revoke or No)."""
    page.locator(".el-popconfirm").get_by_role("button", name=name, exact=True).click()


def open_row(page, job) -> None:
    page.locator('.jobs__table [data-id="%s"]' % job).click()
    expect(drawer(page)).to_be_visible()


def close_drawer(page) -> None:
    drawer(page).get_by_role("button", name="Close · Esc", exact=True).click()
    expect(drawer(page)).to_be_hidden()          # EP keeps the closed panel in the DOM, hidden


def pick(page, tab: str) -> None:
    name = re.compile(r"^%s( \d+)?$" % tab)     # Timeline carries its count
    drawer(page).get_by_role("tab", name=name).click()
    expect(drawer(page).get_by_role("tab", name=name)).to_have_attribute("aria-selected", "true")


def sent_keys(page, suffix: str) -> list:
    """The operation_key of every regenerate request the page sends, in order."""
    keys: list = []
    page.on("request", lambda r: r.url.endswith(suffix) and keys.append(r.post_data_json["operation_key"]))
    return keys


def sent(page, keys: list, n: int):
    for _ in range(100):
        if len(keys) >= n:
            return keys[n - 1]
        page.wait_for_timeout(50)
    raise AssertionError("the page sent %d regenerate requests, not %d" % (len(keys), n))


def lose_first_answer(page, pattern: str) -> None:
    """The server makes the job, the answer never reaches the page; later requests go through."""
    state = {"lost": False}

    def route(r):
        if not state["lost"]:
            state["lost"] = True
            r.fetch()
            r.abort()
        else:
            r.continue_()

    page.route(pattern, route)


def fail_first_send(page, pattern: str) -> None:
    """The first request never reaches the server (the network fails); later requests go through."""
    state = {"failed": False}

    def route(r):
        if not state["failed"]:
            state["failed"] = True
            r.abort()
        else:
            r.continue_()

    page.route(pattern, route)


def hold_first_answer(page, pattern: str) -> dict:
    """The first request is held until `release` is set, then goes through; later requests go through at once."""
    state = {"held": False, "release": False}

    def route(r):
        if not state["held"]:
            state["held"] = True
            while not state["release"]:
                page.wait_for_timeout(50)
        r.continue_()

    page.route(pattern, route)
    return state


def regenerate_button(page):
    return drawer(page).get_by_role("button", name=REGENERATE, exact=True)


# ---- the job drawer ----------------------------------------------------------------------------------------------


def test_a_drawer_header_with_a_tab_row_has_no_padding_under_it(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True), ref="r1", status="failed", prompt="a cat")
    db.run("UPDATE jobs SET error_code = 'render.deadline', error = 'too slow' WHERE id = %s", job)
    app.sign_in(page)
    open_row(page, job)
    expect(drawer(page).get_by_role("tab", name="Overview", exact=True)).to_be_visible()
    got = page.evaluate("""() => {
      const header = document.querySelector('[data-testid=job-drawer] .el-drawer__header');
      const head = header.querySelector('.rl-drawer__head');
      const tabs = head.querySelector('.rl-drawer__tabs');
      const pad = (el) => parseFloat(getComputedStyle(el).paddingBottom);
      return {padding: pad(head) + pad(header),
              gap: Math.round(header.getBoundingClientRect().bottom - 1 - tabs.getBoundingClientRect().bottom)};
    }""", isolated_context=False)
    assert got == {"padding": 0, "gap": 0}, "the tab row does not sit on the line: %r" % got


def test_the_drawer_shows_its_tabs_copies_the_prompt_and_renames(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True), ref="r1", status="failed",
                   prompt="a cat on a roof")
    db.run("UPDATE jobs SET error_code = 'render.deadline', error = 'too slow' WHERE id = %s", job)
    db.run("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'engine', 'submitted', 'went out')", job)
    app.sign_in(page)
    open_row(page, job)
    panel = drawer(page)
    for section in ("conclusion", "summary", "actions", "prompt"):
        expect(panel.locator("[data-section=%s]" % section)).to_be_visible()
    # the dialog is named by its visible heading (read once the job is loaded: the heading is its title)
    heading = panel.get_by_role("heading", level=2).inner_text()
    expect(page.get_by_role("dialog", name=heading, exact=True)).to_be_visible()
    expect(panel.locator("[data-section=conclusion]")).to_contain_text("Failed · render.deadline — too slow")
    expect(panel.locator("[data-section=prompt] p")).to_have_text("a cat on a roof")
    marker = db.one("SELECT marker FROM jobs WHERE id = %s", job)[0]
    assert marker not in panel.locator("[data-section=prompt]").inner_text(), "the marker is not in the prompt"
    pick(page, "Timeline")
    expect(panel.locator("[data-section=timeline]")).to_contain_text("submitted")
    pick(page, "Technical")
    expect(panel.locator("[data-section=technical]")).to_contain_text("Provider / pool")
    expect(panel.locator("[data-field=sent-text]")).to_have_text("—")      # the list's empty-cell dash
    pick(page, "Overview")
    panel.get_by_role("button", name="Copy prompt").click()
    assert page.evaluate("navigator.clipboard.readText()") == "a cat on a roof"
    expect(page.get_by_text("Prompt copied")).to_be_visible()
    pick(page, "Technical")
    panel.locator("[data-section=title] input").fill("My new title")
    panel.get_by_role("button", name="Rename").click()
    expect(page.get_by_text("Renamed")).to_be_visible()
    assert db.one("SELECT title FROM jobs WHERE id = %s", job)[0] == "My new title"
    close_drawer(page)
    first_job = page.locator(".jobs__table tbody tr:not(.is-day)").first
    expect(first_job).to_contain_text("My new title")     # the list reloaded after the rename


def test_the_drawer_shows_the_margin_the_proxy_the_upstream_ids_the_raw_exchanges_and_diagnostics(
        app, app_page, live, db):
    page = app_page
    k = new_key(db)
    acct = new_account(db, label="acct", checked=True)
    db.run("UPDATE accounts SET proxy_host = '203.0.113.24', proxy_port = 8080 WHERE id = %s", acct)
    job = done_job(db, k, acct, ref="task-77", prompt="a lantern")
    db.run("UPDATE jobs SET upstream_price = 1, taken_at = sent_at, provider_state = %s::jsonb WHERE id = %s",
           json.dumps({"chat": "c_9f31", "send_path": "http", "token": "state-secret-1"}), job)
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, body_redacted, at) "
           "SELECT %s, 'fake', 'POST /v1/video/generations', 201, %s, 5, %s::jsonb, created_at FROM jobs WHERE id = %s",
           k, job, json.dumps({"model": "fake-video", "api_key": "sk-live-raw-secret"}), job)
    app.sign_in(page)
    open_row(page, job)
    panel = drawer(page)
    money = panel.locator("[data-section=money]")
    expect(money).to_contain_text("Upstream cost")
    expect(money).to_contain_text("Margin")
    facts = panel.locator("[data-section=summary]")
    for text in ("203.0.113.24:8080", "task-77", "c_9f31"):
        expect(facts).to_contain_text(text)
    raw = panel.locator("[data-section=raw]")
    expect(raw).to_contain_text("POST /v1/video/generations · 201")
    expect(raw).to_contain_text('"model": "fake-video"')
    expect(raw).to_contain_text("GET /v1/video/generations/%s · 200" % job)
    assert "sk-live-raw-secret" not in raw.inner_text()
    pick(page, "Technical")
    diag = panel.locator("[data-section=diagnostics]")
    expect(diag).to_contain_text("Send path")
    expect(diag).to_contain_text("http")
    assert "state-secret-1" not in diag.inner_text()


def test_drawer_recheck_resend_and_revoke_each_toast_and_change_the_job(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True), ref="r1", status="failed")
    db.run("UPDATE jobs SET error_code = 'render.deadline', callback_url = 'https://hooks.example/h', "
           "callback_status = 'failed', callback_attempts = 3, callback_for = clock_timestamp() WHERE id = %s", job)
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token) "
           "VALUES (%s, 'output', 1, %s, 'image/png', 70, %s)", job, "%s/output/1.png" % job,
           storage.new_share_token())
    live.call(live.env.store.put_bytes("%s/output/1.png" % job, PNG, "image/png"))    # the preview must load
    app.sign_in(page)
    open_row(page, job)
    panel = drawer(page)
    pick(page, "Technical")
    panel.get_by_role("button", name="Resend webhook").click()
    expect(page.get_by_text("Webhook queued again")).to_be_visible()
    assert db.one("SELECT callback_status FROM jobs WHERE id = %s", job)[0] == "pending"
    expect(panel.get_by_role("button", name="Resend webhook")).to_have_count(0)
    pick(page, "Overview")
    panel.get_by_role("button", name="Re-check job · R", exact=True).click()
    expect(page.get_by_text("Re-checked")).to_be_visible()
    pick(page, "Technical")
    panel.get_by_role("button", name="Revoke share links").click()
    expect(page.locator(".el-popconfirm")).to_contain_text("Every share link of this job stops working")
    confirm_inline(page, "Cancel")
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND share_token IS NOT NULL", job)[0] == 1
    panel.get_by_role("button", name="Revoke share links").click()
    confirm_inline(page, "Revoke")
    expect(page.get_by_text("Revoked 1 link(s)")).to_be_visible()
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND share_token IS NOT NULL", job)[0] == 0
    expect(panel.get_by_role("button", name="Revoke share links")).to_have_count(0)


def test_drawer_recover_and_fetch_title_queue_a_command_and_poll_it(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct-r", checked=True), ref="r1", status="failed")
    db.run("UPDATE jobs SET error_code = 'render.deadline' WHERE id = %s", job)
    # the worker that would finish a command is not part of this server: the poll's answer is the page's own concern
    page.route("**/admin/commands/*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"id": "c", "provider": "fake", "kind": "x", "account_id": None, "job_id": None,
                         "status": "done", "result": None, "attempts": 1, "created_ms": 1, "finished_ms": 2})))
    app.sign_in(page)
    open_row(page, job)
    panel = drawer(page)
    pick(page, "Technical")
    panel.get_by_role("button", name="Recover").click()
    expect(page.get_by_text("Enter the account id or label to recover from")).to_be_visible()
    panel.get_by_placeholder("Account id or label, e.g. dola-n11-01").fill("acct-r")
    panel.get_by_role("button", name="Recover").click()
    expect(page.get_by_text("Recover done")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'recover' AND job_id = %s", job)[0] == 1
    panel.get_by_role("button", name="Fetch title").click()
    expect(page.get_by_text("Fetch title done")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'titles' AND job_id = %s", job)[0] == 1


# ---- the regenerate money guard ----------------------------------------------------------------------------------

def test_drawer_regenerate_after_a_lost_answer_sends_the_same_key_after_a_reload_and_a_new_click_a_new_one(
        app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    lose_first_answer(page, "**/admin/jobs/*/regenerate")
    app.sign_in(page)

    def regenerate():
        open_row(page, source)
        regenerate_button(page).click()
        confirm(page, "Regenerate")

    regenerate()
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the first request made the job"
    expect(drawer(page).get_by_role("button", name="Start a new regenerate")).to_be_visible()
    page.reload()
    expect(rows(page).first).to_be_visible()
    regenerate()
    assert sent(page, keys, 2) == keys[0], "the retry after a reload sends the same key"
    expect(page.get_by_text("Already made: job")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the retry made no second job"
    close_drawer(page)
    regenerate()
    assert sent(page, keys, 3) != keys[0], "a new action gets a new key"
    # the toast only: the confirm box still fading out says "makes a NEW job" and would match a bare text search
    expect(page.locator(".el-message").get_by_text("New job ")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 3


def test_drawer_start_a_new_regenerate_is_offered_after_a_kept_key_and_warns(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    lose_first_answer(page, "**/admin/jobs/*/regenerate")
    app.sign_in(page)
    open_row(page, source)
    panel = drawer(page)
    expect(panel.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    regenerate_button(page).click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    # the warning names the double charge, and Cancel keeps the key: the button stays and the next send repeats it
    panel.get_by_role("button", name="Start a new regenerate").click()
    expect(page.locator(".el-message-box")).to_contain_text("Starting a new one can charge again")
    confirm(page, "Cancel")
    expect(panel.get_by_role("button", name="Start a new regenerate")).to_be_visible()
    panel.get_by_role("button", name="Start a new regenerate").click()
    confirm(page, "Start a new one")
    expect(panel.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    regenerate_button(page).click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) != keys[0]


def test_drawer_a_double_click_sends_one_request_and_makes_one_job(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    app.sign_in(page)
    open_row(page, source)
    # two clicks before the page can paint: the button is busy after the first, so one confirm opens
    regenerate_button(page).evaluate("el => { el.click(); el.click(); }")
    expect(page.locator(".el-message-box")).to_have_count(1)
    # and two clicks on the confirm's own button send one request
    page.locator(".el-message-box").get_by_role("button", name="Regenerate", exact=True).dblclick()
    expect(page.locator(".el-message").get_by_text("New job ")).to_be_visible()
    page.wait_for_timeout(300)
    assert len(keys) == 1, "one confirmed action sends one request"
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "and makes one job"


def test_drawer_a_regenerate_that_failed_to_send_is_retried_with_the_same_key(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    fail_first_send(page, "**/admin/jobs/*/regenerate")
    app.sign_in(page)
    open_row(page, source)
    regenerate_button(page).click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    assert db.one("SELECT count(*) FROM jobs")[0] == 1, "the request never reached the server"
    regenerate_button(page).click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) == keys[0], "the retry sends the key the failed send kept"
    expect(page.locator(".el-message").get_by_text("New job ")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2


def test_drawer_closing_it_mid_flight_leaves_one_request_and_a_second_click_joins_it(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    held = hold_first_answer(page, "**/admin/jobs/*/regenerate")
    app.sign_in(page)
    try:
        open_row(page, source)
        regenerate_button(page).click()
        confirm(page, "Regenerate")
        sent(page, keys, 1)
        close_drawer(page)
        open_row(page, source)
        regenerate_button(page).click()
        confirm(page, "Regenerate")
        page.wait_for_timeout(300)
        assert len(keys) == 1, "a second confirm while the first is in flight joins it and sends nothing"
    finally:
        held["release"] = True
    expect(page.locator(".el-message").get_by_text("New job ").first).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "one job, however many times it was asked"
    page.unroute_all(behavior="ignoreErrors")


def test_drawer_a_kept_key_survives_a_sign_out_and_the_next_regenerate_repeats_it(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    lose_first_answer(page, "**/admin/jobs/*/regenerate")
    app.sign_in(page)
    open_row(page, source)
    regenerate_button(page).click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the first request made the job"
    close_drawer(page)            # the open drawer covers the account menu
    app.sign_out(page)
    assert str(source) in (page.evaluate("sessionStorage.getItem('regenerateOperations')") or ""), \
        "the kept key is still there after the sign-out"
    app.sign_in(page)
    open_row(page, source)
    regenerate_button(page).click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) == keys[0], "the repeat after a sign-out sends the same key"
    expect(page.get_by_text("Already made: job")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "and makes no second job"


# ---- results, references and the media safety --------------------------------------------------------------------

def test_results_show_from_share_links_download_and_open_through_the_proxy_once_the_link_is_gone(
        app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True))
    out_key = db.one("SELECT object_key FROM job_media WHERE job_id = %s", job)[0]
    token = storage.new_share_token()
    db.run("UPDATE job_media SET mime = 'image/png', share_token = %s WHERE job_id = %s", token, job)
    live.call(live.env.store.put_bytes(out_key, PNG, "image/png"))
    debug_key = "%s/debug/0.html" % job
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
           "VALUES (%s, 'debug', 0, %s, 'text/html', 40)", job, debug_key)
    live.call(live.env.store.put_bytes(debug_key, b"<script>window.__debug_ran = 1</script><b>x</b>", "text/html"))
    app.sign_in(page)
    open_row(page, job)
    panel = drawer(page)
    image = panel.locator("[data-section=results] img")
    expect(image).to_have_count(1)
    assert image.get_attribute("src") == "/m/" + token
    page.wait_for_function("() => document.querySelector('[data-testid=job-drawer] img').naturalWidth === 1")
    expect(panel.locator("[data-section=results]").get_by_role("button", name="Open", exact=True)).to_have_count(0)
    with page.expect_download() as download:
        panel.get_by_role("button", name="Download · D", exact=True).click()
    assert download.value.suggested_filename.startswith("job-%s-0" % str(job)[:8])
    pick(page, "Technical")
    panel.get_by_role("button", name="File 0 (text/html)").click()
    expect(panel.locator("[data-section=debug] pre")).to_contain_text("<script>window.__debug_ran = 1</script>")
    assert page.evaluate("window.__debug_ran", isolated_context=False) is None, "a debug page never runs"
    assert panel.locator("[data-section=debug] b").count() == 0
    panel.get_by_role("button", name="Revoke share links").click()
    confirm_inline(page, "Revoke")
    expect(page.get_by_text("Revoked 1 link(s)")).to_be_visible()
    pick(page, "Overview")
    expect(panel.locator("[data-section=results] img")).to_have_count(0)
    expect(panel.locator("[data-section=results]")).to_contain_text("no preview")
    with page.expect_response(re.compile(r"/admin/jobs/%s/outputs/0/content$" % job)) as proxied:
        panel.locator("[data-section=results]").get_by_role("button", name="Open", exact=True).click()
    assert proxied.value.status == 200
    assert proxied.value.headers["content-type"].startswith("image/png")


def test_a_click_on_a_result_image_opens_the_viewer_and_escape_closes_it(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True))
    out_key = db.one("SELECT object_key FROM job_media WHERE job_id = %s", job)[0]
    db.run("UPDATE job_media SET mime = 'image/png', share_token = %s WHERE job_id = %s",
           storage.new_share_token(), job)
    live.call(live.env.store.put_bytes(out_key, PNG, "image/png"))
    app.sign_in(page)
    open_row(page, job)
    image = drawer(page).locator("[data-section=results] img")
    expect(image).to_be_visible()
    image.click()
    viewer = page.locator(".el-dialog.rl-viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator("img.mv__img")).to_have_attribute("src", re.compile(r"^/m/"))
    expect(viewer.locator(".mv__file")).to_have_text("job-%s-0.png" % str(job)[:8])
    viewer.get_by_role("button", name="Zoom in · +").click()
    expect(viewer.locator(".mv__zoom")).to_have_text("125%")
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    expect(drawer(page)).to_be_visible()


def test_the_timeline_lists_the_newest_event_first(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True))
    db.run("INSERT INTO job_events (job_id, source, stage, text, at) "
           "VALUES (%s, 'engine', 'submitted', 'the early one', now() - interval '2 hours')", job)
    db.run("INSERT INTO job_events (job_id, source, stage, text, at) "
           "VALUES (%s, 'engine', 'failed', 'the late one', now() - interval '1 hour')", job)
    app.sign_in(page)
    open_row(page, job)
    pick(page, "Timeline")
    items = drawer(page).locator("[data-section=timeline] .tl__row")
    expect(items.first).to_contain_text("the late one")
    expect(items.last).to_contain_text("the early one")


# ---- server text is text -----------------------------------------------------------------------------------------

def test_a_script_url_or_a_foreign_host_in_a_media_link_is_never_drawn_or_requested(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True))
    db.run("UPDATE job_media SET mime = 'image/png' WHERE job_id = %s", job)
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) VALUES "
           "(%s, 'output', 1, %s, 'image/png', 70), (%s, 'output', 2, %s, 'video/mp4', 70), "
           "(%s, 'output', 3, %s, 'image/png', 70)", job, "%s/o1.png" % job, job, "%s/o2.mp4" % job, job,
           "%s/o3.png" % job)
    bad = {0: SCRIPT_SCHEME + "alert(1)", 1: "https://evil.example/m/abc", 2: "//evil.example/m/abc",
           3: "data:image/png;base64,AAAA"}
    requested: list[str] = []
    page.on("request", lambda r: requested.append(r.url))

    def tamper(route):
        response = route.fetch()
        data = response.json()
        for item in data["media"]:
            item["url"] = bad[item["idx"]]
        route.fulfill(response=response, json=data)

    page.route(re.compile(r"/admin/jobs/[0-9a-f-]{36}$"), tamper)
    app.sign_in(page)
    open_row(page, job)
    panel = drawer(page)
    expect(panel.locator("[data-section=results]")).to_contain_text("no preview")
    assert panel.locator("[data-section=results] img, [data-section=results] video, a").count() == 0
    html = panel.inner_html()
    assert "evil.example" not in html and SCRIPT_SCHEME not in html
    assert not [u for u in requested if "evil.example" in u], requested
    assert panel.locator("[data-section=results]").get_by_role("button", name="Open", exact=True).count() == 4
