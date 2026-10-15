"""The Jobs list of the Vue admin in Chrome against the real app (browser tier): columns, filters, paging, selection and
the bulk actions. Every test runs under the CSP watch of `conftest.CspWatch` (through `app_page`): a violation on
either channel fails it. Rows are planted through the database with the helpers the API tests use (the fake provider);
the page is read, and the result is read back from the database. The job detail is in test_e2e_job_drawer.py."""

import re

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from test_admin_jobs import failed_job

pytestmark = pytest.mark.browser

SAFE_AFTER_LOST = "Trying again is safe"


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
    for _ in range(25):
        db.job(k, provider="fake")
    app.sign_in(page)
    expect(rows(page)).to_have_count(10)          # 10 a page by default
    expect(page.locator(".pager .el-pagination__total")).to_contain_text("25")
    first_page = rows(page).all_inner_texts()
    nxt, prev = page.locator(".pager button.btn-next"), page.locator(".pager button.btn-prev")
    expect(prev).to_be_disabled()
    nxt.click()
    expect(rows(page)).to_have_count(10)
    second_page = rows(page).all_inner_texts()
    assert not set(second_page) & set(first_page), "no job on both pages"
    page.locator(".pager li.number", has_text="3").click()      # jump to the last page
    expect(rows(page)).to_have_count(5)
    expect(nxt).to_be_disabled()
    third_page = rows(page).all_inner_texts()
    assert not set(third_page) & (set(first_page) | set(second_page)), "no job on two pages"
    prev.click()
    expect(rows(page)).to_have_count(10)
    assert rows(page).all_inner_texts() == second_page, "the second page comes back as it was"
    page.locator(".pager li.number", has_text="1").click()
    expect(rows(page)).to_have_count(10)
    assert rows(page).all_inner_texts() == first_page, "and so does the first"


def test_a_bigger_page_size_shows_more_rows_and_keeps_the_ticks_across_a_reload(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    for _ in range(25):
        db.job(k, provider="fake")
    app.sign_in(page)
    expect(rows(page)).to_have_count(10)
    page.locator(".pager .el-select").click()
    page.get_by_role("option", name="20/page").click()
    expect(rows(page)).to_have_count(20)
    tick(page, 0)
    tick(page, 3)
    expect(page.locator(".jobs__count")).to_have_text("2 selected")
    page.get_by_role("button", name="Refresh").click()
    expect(rows(page)).to_have_count(20)
    expect(page.locator(".jobs__count")).to_have_text("2 selected")
    expect(rows(page).nth(3).locator(".el-checkbox")).to_have_class(re.compile("is-checked"))


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
    page.locator("thead label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("3 selected")
    expect(recheck).to_be_enabled()
    page.locator("thead label.el-checkbox").click()
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


def test_a_key_with_no_label_is_named_by_its_prefix_in_the_key_filter(app, app_page, live, db):
    page = app_page
    named, nameless = new_key(db), new_key(db)
    db.run("UPDATE api_keys SET label = 'named-key' WHERE id = %s", named)
    db.run("UPDATE api_keys SET label = '' WHERE id = %s", nameless)
    prefix = db.one("SELECT key_prefix FROM api_keys WHERE id = %s", nameless)[0]
    done_job(db, named, ref="a")
    app.sign_in(page)
    page.locator(".jobs__filter").nth(1).click()
    for name in ("All keys", "named-key", prefix):
        expect(page.get_by_role("option", name=name, exact=True)).to_be_visible()
    options = page.get_by_role("option")
    assert all(t.strip() for t in options.all_inner_texts()), "no option is blank: %r" % options.all_inner_texts()


@pytest.mark.parametrize("width", [375, 768, 1280])
def test_the_list_and_the_detail_fit_the_screen_at_phone_tablet_and_desktop_widths(app, app_page, live, db, width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 800})
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True), prompt="a cat")
    app.sign_in(page)
    expect(rows(page)).to_have_count(1)
    fits = page.evaluate("(e => e.scrollWidth <= e.clientWidth + 1)(document.querySelector('.jobs'))")
    assert fits, "the list scrolls inside its own container, not the page section"
    open_row(page, job)
    box = page.locator(".el-dialog").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
    expect(page.locator(".el-dialog [data-section=technical] .el-descriptions")).to_be_visible()


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
