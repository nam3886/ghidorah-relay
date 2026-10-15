"""The Jobs tab and the job detail of the Vue admin in Chrome against the real app (browser tier). Every test runs under
the CSP watch of `conftest.CspWatch` (through `app_page`): a violation on either channel fails it. Rows are planted
through the database with the helpers the API tests use (the fake provider); the page is read, and the result is read
back from the database."""

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


def tick(page, i: int) -> None:
    rows(page).nth(i).locator("label.el-checkbox").click()


def confirm(page, name: str) -> None:
    page.locator(".el-message-box").get_by_role("button", name=name, exact=True).click()


def choose(page, which: int, option: str) -> None:
    """Pick an option of the n-th filter select (0 status, 1 key)."""
    page.locator(".jobs__filter").nth(which).click()
    page.get_by_role("option", name=option, exact=True).click()


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


# ---- the list ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_list_shows_its_columns_and_a_row_opens_the_detail_but_its_tick_box_does_not(
        app, app_page, live, db, color_scheme):
    page = app_page
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct-a", checked=True), prompt="a cat")
    db.run("UPDATE jobs SET title = 'clickable', model = 'fake-video' WHERE id = %s", job)
    app.sign_in(page)
    expect(rows(page)).to_have_count(1)
    headers = page.locator("thead th").all_inner_texts()
    assert [h.strip() for h in headers] == ["", "Progress", "Title", "Job", "Operation", "Status", "Points",
                                           "Results", "Refs", "Account", "Key", "Created", "Actions"]
    row = rows(page).first
    for text in (str(job)[:8], "clickable", "acct-a", "done"):
        expect(row).to_contain_text(text)
    row.locator("label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("1 selected")
    expect(page.locator(".el-dialog")).to_have_count(0)
    row.locator("td").nth(2).click()
    expect(page.locator(".el-dialog__title")).to_have_text("Job " + str(job)[:8])


@pytest.mark.fabricated_rows("plants a submitting job with no lease row and a failed job with no error code",
                             "a submitting job with no lease row", "without an error code")
def test_the_status_filter_offers_submitting_and_the_key_filter_narrows_the_list(app, app_page, live, db):
    page = app_page
    k1, k2 = new_key(db), new_key(db)
    db.run("UPDATE api_keys SET label = 'first-key' WHERE id = %s", k1)
    db.run("UPDATE api_keys SET label = 'second-key' WHERE id = %s", k2)
    a = done_job(db, k1, ref="a")
    b = done_job(db, k2, ref="b", status="failed")
    c = db.job(k1, provider="fake")
    db.run("UPDATE jobs SET status = 'submitting' WHERE id = %s", c)
    app.sign_in(page)
    expect(rows(page)).to_have_count(3)
    choose(page, 0, "submitting")
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text(str(c)[:8])
    choose(page, 0, "All statuses")
    expect(rows(page)).to_have_count(3)
    choose(page, 1, "second-key")
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text(str(b)[:8])
    choose(page, 1, "first-key")
    expect(rows(page)).to_have_count(2)
    choose(page, 0, "done")
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text(str(a)[:8])


@pytest.mark.fabricated_rows("plants a failed job with no error code", "without an error code")
def test_a_stats_link_opens_jobs_already_filtered(app, app_page, live, db):
    k = new_key(db)
    done_job(db, k, ref="a")
    failed = done_job(db, k, ref="b", status="failed")
    page = app_page
    app.sign_in(page)
    expect(rows(page)).to_have_count(2)
    missing = []
    page.on("response", lambda r: missing.append(r.url) if r.status == 404 else None)
    page.locator("a", has_text="Completed").first.click()
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).not_to_contain_text(str(failed)[:8])
    page.evaluate("location.hash = '#/jobs?status=failed'", isolated_context=False)
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text(str(failed)[:8])
    assert not missing, missing


def test_the_list_pages_forward_and_back_without_a_repeat(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    for _ in range(52):
        db.job(k, provider="fake")
    app.sign_in(page)
    expect(rows(page)).to_have_count(50)
    first_page = rows(page).all_inner_texts()
    nxt, prev = page.get_by_role("button", name="Next", exact=True), page.get_by_role("button", name="Prev", exact=True)
    expect(prev).to_be_disabled()
    nxt.click()
    expect(rows(page)).to_have_count(2)
    expect(nxt).to_be_disabled()
    second_page = rows(page).all_inner_texts()
    assert not set(second_page) & set(first_page), "no job on both pages"
    prev.click()
    expect(rows(page)).to_have_count(50)
    assert rows(page).all_inner_texts() == first_page, "the first page comes back as it was"
    nxt.click()
    expect(rows(page)).to_have_count(2)
    assert rows(page).all_inner_texts() == second_page, "and so does the second"


# ---- bulk actions ------------------------------------------------------------------------------------------------

def test_select_all_counts_and_the_bulk_buttons_wait_for_a_tick_then_recheck_reports(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    for i in range(3):
        done_job(db, k, ref="j%d" % i, status="failed")
    db.run("UPDATE jobs SET error_code = 'render.deadline'")
    app.sign_in(page)
    recheck = page.get_by_role("button", name="Re-check selected")
    regenerate = page.get_by_role("button", name="Regenerate selected")
    expect(recheck).to_be_disabled()
    expect(regenerate).to_be_disabled()
    expect(page.locator(".jobs__count")).to_have_text("0 selected")
    page.locator(".jobs__bulk label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("3 selected")
    expect(recheck).to_be_enabled()
    page.locator(".jobs__bulk label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("0 selected")
    tick(page, 0)
    tick(page, 1)
    expect(page.locator(".jobs__count")).to_have_text("2 selected")
    recheck.click()
    expect(page.get_by_text("Re-checked 2")).to_be_visible()
    expect(page.locator(".jobs__count")).to_have_text("0 selected")


def test_bulk_regenerate_asks_first_makes_new_jobs_and_says_how_many(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    app.run(db, lambda p: failed_job(p, k, "one"))
    app.sign_in(page)
    tick(page, 0)
    page.get_by_role("button", name="Regenerate selected").click()
    box = page.locator(".el-message-box")
    expect(box).to_contain_text("Regenerate 1 job(s)?")
    expect(box).to_contain_text("NEW job")
    expect(box).to_contain_text("charges its key")
    box.get_by_role("button", name="Cancel").click()
    assert db.one("SELECT count(*) FROM jobs")[0] == 1, "Cancel makes nothing"
    expect(page.locator(".jobs__count")).to_have_text("1 selected")
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    expect(page.get_by_text("Made 1, already made 0, failed 0")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2
    expect(rows(page)).to_have_count(2)


def test_a_bulk_regenerate_after_a_lost_answer_sends_the_same_key_keeps_the_selection_and_a_changed_one_a_new_key(
        app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/bulk-regenerate")
    lose_first_answer(page, "**/admin/jobs/bulk-regenerate")
    app.sign_in(page)
    tick(page, 0)
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()           # the lost answer is the browser's own "Failed to load resource" console error
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the first request made the job"
    expect(page.locator(".jobs__count")).to_have_text("1 selected")
    expect(page.get_by_role("button", name="Start a new regenerate")).to_be_visible()
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) == keys[0], "the retry sends the same key"
    expect(page.get_by_text("Made 0, already made 1, failed 0")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the retry made no second job"
    expect(page.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    # after an answer the next click is a new action with a new key
    expect(rows(page)).to_have_count(2)
    rows(page).filter(has_text=str(source)[:8]).locator("label.el-checkbox").click()
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 3) != keys[0], "the next action after an answer gets a new key"


def test_start_a_new_regenerate_warns_and_only_then_drops_the_kept_key(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/bulk-regenerate")
    lose_first_answer(page, "**/admin/jobs/bulk-regenerate")
    app.sign_in(page)
    tick(page, 0)
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    new = page.get_by_role("button", name="Start a new regenerate")
    new.click()
    box = page.locator(".el-message-box")
    expect(box).to_contain_text("The earlier attempt may already have made jobs")
    expect(box).to_contain_text("Starting a new one can charge again")
    expect(box).to_contain_text("Check the Jobs list first")
    box.get_by_role("button", name="Cancel").click()
    expect(new).to_be_visible()                       # Cancel keeps the key
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) == keys[0], "still the kept key after the cancelled warning"
    expect(page.get_by_text("Made 0, already made 1, failed 0")).to_be_visible()
    expect(rows(page)).to_have_count(2)
    record = page.evaluate("sessionStorage.getItem('regenerateOperations')", isolated_context=False)
    assert "bulk:" not in (record or ""), "a success dropped the key"


def test_abandoning_a_kept_key_makes_the_next_regenerate_use_a_new_one(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    new_account(db, label="a", checked=True)
    app.run(db, lambda p: failed_job(p, k, "one"))
    keys = sent_keys(page, "/bulk-regenerate")
    answers = iter(["cut", "through"])

    def route(r):
        if next(answers, "through") == "cut":
            r.abort()                                   # nothing reached the server, but the page cannot know
        else:
            r.continue_()

    page.route("**/admin/jobs/bulk-regenerate", route)
    app.sign_in(page)
    tick(page, 0)
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    page.get_by_role("button", name="Start a new regenerate").click()
    confirm(page, "Start a new one")
    expect(page.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    page.get_by_role("button", name="Regenerate selected").click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) != keys[0], "an abandoned key is not sent again"
    expect(page.get_by_text("Made 1, already made 0, failed 0")).to_be_visible()


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
    for section in ("conclusion", "title", "prompt", "actions", "results", "references", "debug", "timeline",
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
    expect(page.locator(".el-message-box")).to_contain_text("Every share link of this job stops working")
    page.locator(".el-message-box").get_by_role("button", name="Cancel").click()
    assert db.one("SELECT count(*) FROM job_media WHERE job_id = %s AND share_token IS NOT NULL", job)[0] == 1
    dialog.get_by_role("button", name="Revoke share links").click()
    confirm(page, "Revoke")
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
    expect(page.get_by_text("New job ")).to_be_visible()
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
    confirm(page, "Revoke")
    expect(page.get_by_text("Revoked 1 link(s)")).to_be_visible()
    expect(dialog.locator("[data-section=results] img")).to_have_count(0)
    expect(dialog.locator("[data-section=results]")).to_contain_text("no preview")
    with page.expect_response(re.compile(r"/admin/jobs/%s/outputs/0/content$" % job)) as proxied:
        dialog.locator("[data-section=results]").get_by_role("button", name="Open").click()
    assert proxied.value.status == 200
    assert proxied.value.headers["content-type"].startswith("image/png")


# ---- server text is text -----------------------------------------------------------------------------------------

def test_planted_markup_in_title_name_error_events_and_prompt_renders_as_text_in_the_list_and_the_detail(
        app, app_page, live, db):
    page = app_page
    hostile = app.planted
    k = new_key(db)
    acct = new_account(db, label=hostile, checked=True)
    db.run("UPDATE accounts SET display_name = %s WHERE id = %s", hostile, acct)
    job = done_job(db, k, acct, status="failed", prompt=hostile)
    db.run("UPDATE jobs SET title = %s, error_code = 'render.deadline', error = %s WHERE id = %s",
           hostile, hostile, job)
    db.run("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'engine', %s, %s)", job, hostile, hostile)
    db.run("UPDATE api_keys SET label = %s WHERE id = %s", hostile, k)
    app.sign_in(page)
    expect(rows(page).first).to_be_visible()
    assert page.locator("tbody img").count() == 0, "planted markup became an element in the list"
    open_row(page, job)
    dialog = page.locator(".el-dialog")
    expect(dialog.locator("[data-section=timeline]")).to_contain_text("<img src=x")
    assert dialog.locator("img").count() == 0, "planted markup became an element in the detail"
    expect(dialog.locator("[data-section=prompt] pre")).to_have_text(hostile)
    expect(dialog.locator("[data-section=conclusion]")).to_contain_text(hostile)
    expect(dialog.locator("[data-section=title] input")).to_have_value(hostile)
    app.assert_planted_as_text(page)


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
