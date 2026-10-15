"""The job detail of the Vue admin in Chrome against the real app (browser tier): its sections, the actions it offers
(Recover, Re-check, Resend webhook, Revoke, Regenerate), the results and the media safety. Every test runs under the
CSP watch of `conftest.CspWatch` (through `app_page`): a violation on either channel fails it. Rows are planted through
the database with the helpers the API tests use (the fake provider); the page is read, and the result is read back from
the database. The list is in test_e2e_jobs.py."""

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


def rows(page):
    return page.locator("tbody tr")


def confirm(page, name: str) -> None:
    page.locator(".el-message-box").get_by_role("button", name=name, exact=True).click()


def confirm_inline(page, name: str) -> None:
    """Answer the el-popconfirm that is open (Revoke or No)."""
    page.locator(".el-popconfirm").get_by_role("button", name=name, exact=True).click()


def open_row(page, job) -> None:
    rows(page).filter(has_text=str(job)[:8]).get_by_role("button", name="Details").click()
    expect(page.locator(".el-dialog")).to_be_visible()


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


# ---- the job detail ----------------------------------------------------------------------------------------------

def test_the_detail_shows_every_section_copies_the_prompt_and_renames(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True), ref="r1", status="failed",
                   prompt="a cat on a roof")
    db.run("UPDATE jobs SET error_code = 'render.deadline', error = 'too slow' WHERE id = %s", job)
    db.run("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'engine', 'submitted', 'went out')", job)
    app.sign_in(page)
    open_row(page, job)
    dialog = page.locator(".el-dialog")
    for section in ("conclusion", "summary", "title", "prompt", "actions", "results", "references", "debug", "timeline",
                    "technical"):
        expect(dialog.locator("[data-section=%s]" % section)).to_be_visible()
    expect(dialog.locator("[data-section=conclusion]")).to_contain_text("render.deadline: too slow")
    expect(dialog.locator("[data-section=prompt] pre")).to_have_text("a cat on a roof")
    marker = db.one("SELECT marker FROM jobs WHERE id = %s", job)[0]
    assert marker not in dialog.locator("[data-section=prompt]").inner_text(), "the marker is not in the prompt"
    expect(dialog.locator("[data-section=timeline]")).to_contain_text("submitted")
    expect(dialog.locator("[data-section=technical]")).to_contain_text("Provider / pool")
    expect(dialog.locator("[data-field=sent-text]")).to_have_text("-")
    dialog.get_by_role("button", name="Copy prompt").click()
    assert page.evaluate("navigator.clipboard.readText()") == "a cat on a roof"
    expect(page.get_by_text("Prompt copied")).to_be_visible()
    dialog.locator("[data-section=title] input").fill("My new title")
    dialog.get_by_role("button", name="Rename").click()
    expect(page.get_by_text("Renamed")).to_be_visible()
    assert db.one("SELECT title FROM jobs WHERE id = %s", job)[0] == "My new title"
    dialog.get_by_role("button", name="Close", exact=True).last.click()
    expect(page.locator(".el-dialog")).to_have_count(0)
    expect(rows(page).first).to_contain_text("My new title")     # the list reloaded after the rename


def test_detail_recheck_resend_and_revoke_each_toast_and_change_the_job(app, app_page, live, db):
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
    dialog = page.locator(".el-dialog")
    dialog.get_by_role("button", name="Resend webhook").click()
    expect(page.get_by_text("Webhook queued again")).to_be_visible()
    assert db.one("SELECT callback_status FROM jobs WHERE id = %s", job)[0] == "pending"
    expect(dialog.get_by_role("button", name="Resend webhook")).to_have_count(0)
    dialog.get_by_role("button", name="Re-check", exact=True).click()
    expect(page.get_by_text("Re-checked")).to_be_visible()
    dialog.get_by_role("button", name="Revoke share links").click()
    expect(page.locator(".el-popconfirm")).to_contain_text("Every share link of this job stops working")
    confirm_inline(page, "No")
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND share_token IS NOT NULL", job)[0] == 1
    dialog.get_by_role("button", name="Revoke share links").click()
    confirm_inline(page, "Revoke")
    expect(page.get_by_text("Revoked 1 link(s)")).to_be_visible()
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND share_token IS NOT NULL", job)[0] == 0
    expect(dialog.get_by_role("button", name="Revoke share links")).to_have_count(0)


def test_detail_recover_and_fetch_title_queue_a_command_and_poll_it(app, app_page, live, db):
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
    dialog = page.locator(".el-dialog")
    dialog.get_by_role("button", name="Recover").click()
    expect(page.get_by_text("Enter the account id or label to recover from")).to_be_visible()
    dialog.get_by_placeholder("account id or label").fill("acct-r")
    dialog.get_by_role("button", name="Recover").click()
    expect(page.get_by_text("Recover: done")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'recover' AND job_id = %s", job)[0] == 1
    dialog.get_by_role("button", name="Fetch title").click()
    expect(page.get_by_text("Fetch title: done")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'titles' AND job_id = %s", job)[0] == 1


def test_detail_regenerate_after_a_lost_answer_sends_the_same_key_after_a_reload_and_a_new_click_a_new_one(
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
        page.locator(".el-dialog").get_by_role("button", name="Regenerate", exact=True).click()
        confirm(page, "Regenerate")

    regenerate()
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the first request made the job"
    expect(page.locator(".el-dialog").get_by_role("button", name="Start a new regenerate")).to_be_visible()
    page.reload()
    expect(rows(page).first).to_be_visible()
    regenerate()
    assert sent(page, keys, 2) == keys[0], "the retry after a reload sends the same key"
    expect(page.get_by_text("Already made: job")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the retry made no second job"
    page.locator(".el-dialog").get_by_role("button", name="Close", exact=True).last.click()
    expect(page.locator(".el-dialog")).to_have_count(0)
    regenerate()
    assert sent(page, keys, 3) != keys[0], "a new action gets a new key"
    # the toast only: the confirm box still fading out says "makes a NEW job" and would match a bare text search
    expect(page.locator(".el-message").get_by_text("New job ")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 3


def test_detail_start_a_new_regenerate_is_offered_after_a_kept_key_and_warns(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/regenerate")
    lose_first_answer(page, "**/admin/jobs/*/regenerate")
    app.sign_in(page)
    open_row(page, source)
    dialog = page.locator(".el-dialog")
    expect(dialog.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    dialog.get_by_role("button", name="Regenerate", exact=True).click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    dialog.get_by_role("button", name="Start a new regenerate").click()
    expect(page.locator(".el-message-box")).to_contain_text("Starting a new one can charge again")
    confirm(page, "Start a new one")
    expect(dialog.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    dialog.get_by_role("button", name="Regenerate", exact=True).click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) != keys[0]


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
    dialog = page.locator(".el-dialog")
    image = dialog.locator("[data-section=results] img")
    expect(image).to_have_count(1)
    assert image.get_attribute("src") == "/m/" + token
    page.wait_for_function("document.querySelector('.el-dialog img').naturalWidth === 1")
    expect(dialog.locator("[data-section=results]").get_by_role("button", name="Open")).to_have_count(0)
    with page.expect_download() as download:
        dialog.get_by_role("button", name="Download").click()
    assert download.value.suggested_filename.startswith("job-%s-0" % str(job)[:8])
    dialog.get_by_role("button", name="File 0 (text/html)").click()
    expect(dialog.locator("[data-section=debug] pre")).to_contain_text("<script>window.__debug_ran = 1</script>")
    assert page.evaluate("window.__debug_ran", isolated_context=False) is None, "a debug page never runs"
    assert dialog.locator("[data-section=debug] b").count() == 0
    dialog.get_by_role("button", name="Revoke share links").click()
    confirm_inline(page, "Revoke")
    expect(page.get_by_text("Revoked 1 link(s)")).to_be_visible()
    expect(dialog.locator("[data-section=results] img")).to_have_count(0)
    expect(dialog.locator("[data-section=results]")).to_contain_text("no preview")
    with page.expect_response(re.compile(r"/admin/jobs/%s/outputs/0/content$" % job)) as proxied:
        dialog.locator("[data-section=results]").get_by_role("button", name="Open").click()
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
    image = page.locator(".el-dialog [data-section=results] img")
    expect(image).to_be_visible()
    image.click()
    viewer = page.locator(".el-image-viewer__wrapper")
    expect(viewer).to_be_visible()
    expect(viewer.locator("img.el-image-viewer__img")).to_have_attribute("src", re.compile(r"^/m/"))
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(page.locator(".el-dialog")).to_be_visible()


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
    items = page.locator(".el-dialog [data-section=timeline] .el-timeline-item")
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
    dialog = page.locator(".el-dialog")
    expect(dialog.locator("[data-section=results]")).to_contain_text("no preview")
    assert dialog.locator("[data-section=results] img, [data-section=results] video, a").count() == 0
    html = dialog.inner_html()
    assert "evil.example" not in html and SCRIPT_SCHEME not in html
    assert not [u for u in requested if "evil.example" in u], requested
    assert dialog.locator("[data-section=results]").get_by_role("button", name="Open").count() == 4
