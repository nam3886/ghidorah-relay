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
    """The job rows: table rows without the day headers, or the phone's stacked list items."""
    return page.locator("tbody tr:not(.is-day), li.data-table__item")


def settled(page, showing: str, count: int, left: list[str] | None = None) -> list[str]:
    """The list has landed on a page, and its rows' texts: the pager says `showing`, no read is under way, it holds
    `count` rows and (when given) its first row is not the page it `left`. The table keeps the old rows while the next
    page loads, so the count alone can still be the old page's."""
    expect(page.locator(".pager .pager__text").first).to_have_text(showing)
    expect(page.locator(".data-table.is-refreshing")).to_have_count(0)
    expect(rows(page)).to_have_count(count)
    if left:
        expect(rows(page).first).not_to_have_text(left[0], use_inner_text=True)
    return rows(page).all_inner_texts()


def tick(page, i: int) -> None:
    rows(page).nth(i).locator("label.el-checkbox").click()


def confirm(page, name: str) -> None:
    page.locator(".el-message-box").get_by_role("button", name=name, exact=True).click()


def choose(page, which: int, option: str) -> None:
    """Pick an option of the n-th filter select (0 status, 1 key): the listbox closes and the control shows it."""
    page.locator(".jobs__filter").nth(which).click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(page.get_by_role("listbox")).to_have_count(0)
    expect(page.locator(".jobs__filter").nth(which)).to_contain_text(option)


def drawer(page):
    """The job detail: the shared right-hand drawer."""
    return page.locator("[data-testid=job-drawer]")


def pick(page, tab: str) -> None:
    """A tab of the drawer (Overview, Timeline, Technical): only the open tab's sections are in the page."""
    drawer(page).get_by_role("tab", name=tab, exact=True).click()
    expect(drawer(page).get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")


def open_row(page, job) -> None:
    rows(page).filter(has_text=str(job)[:8]).locator("a.title-link, .prow").first.click()
    expect(drawer(page)).to_be_visible()


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
    assert [h.strip() for h in headers] == ["", "Time", "Job", "Status", "Model", "Account", "Key", "Out · Refs",
                                           "Net points"]
    row = rows(page).first
    for text in (str(job)[:8], "clickable", "acct-a", "done", "fake-video"):
        expect(row).to_contain_text(text)
    expect(page.locator('[data-testid="day-row"]')).to_contain_text("Today")
    expect(page.locator('[data-testid="day-row"]')).to_contain_text("· 1 job shown")  # today: its jobs on the page
    row.locator("label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("1 selected")
    expect(drawer(page)).to_be_hidden()
    row.locator("td").nth(2).click()
    expect(page.get_by_test_id("job-drawer")).to_be_visible()


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
    choose(page, 0, "Submitting")
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text(str(c)[:8])
    choose(page, 0, "All")
    expect(rows(page)).to_have_count(3)
    choose(page, 1, "second-key")
    expect(rows(page)).to_have_count(1)
    expect(rows(page).first).to_contain_text(str(b)[:8])
    choose(page, 1, "first-key")
    expect(rows(page)).to_have_count(2)
    choose(page, 0, "Done")
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
    page.evaluate("location.hash = '#/jobs?status=done'", isolated_context=False)
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
    first_page = settled(page, "Showing 1–10 of 25", 10)          # 10 a page by default
    nxt, prev = page.locator(".pager button.btn-next"), page.locator(".pager button.btn-prev")
    expect(prev).to_have_attribute("aria-disabled", "true")
    nxt.click()
    second_page = settled(page, "Showing 11–20 of 25", 10, left=first_page)
    assert not set(second_page) & set(first_page), "no job on both pages"
    page.locator(".pager button.number").get_by_text("3", exact=True).click()      # jump to the last page
    third_page = settled(page, "Showing 21–25 of 25", 5, left=second_page)
    expect(nxt).to_have_attribute("aria-disabled", "true")
    assert not set(third_page) & (set(first_page) | set(second_page)), "no job on two pages"
    prev.click()
    assert settled(page, "Showing 11–20 of 25", 10, left=third_page) == second_page, \
        "the second page comes back as it was"
    page.locator(".pager button.number").get_by_text("1", exact=True).click()
    assert settled(page, "Showing 1–10 of 25", 10, left=second_page) == first_page, "and so does the first"


def test_a_bigger_page_size_shows_more_rows_and_keeps_the_ticks_across_a_reload(app, app_page, live, db):
    page = app_page
    k = new_key(db)
    for _ in range(25):
        db.job(k, provider="fake")
    app.sign_in(page)
    expect(rows(page)).to_have_count(10)
    page.locator(".pager button.is-pager").click()
    page.get_by_role("listbox", name="Page size").get_by_role("option", name="20 / page", exact=True).click()
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
    recheck = page.get_by_role("button", name="Re-check")
    regenerate = page.get_by_role("button", name="Regenerate…")
    # nothing ticked: "0 selected" and no bulk buttons, as the canvas draws it
    expect(page.locator("[data-state=ready]")).to_be_visible()
    expect(recheck).to_be_hidden()
    expect(regenerate).to_be_hidden()
    expect(page.locator(".jobs__count")).to_be_visible()
    expect(page.locator(".jobs__count")).to_have_text("0 selected")
    page.locator("thead label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("3 selected")
    expect(recheck).to_be_enabled()
    expect(regenerate).to_be_enabled()
    page.locator("thead label.el-checkbox").click()
    expect(page.locator(".jobs__count")).to_have_text("0 selected")
    expect(recheck).to_be_hidden()
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
    page.get_by_role("button", name="Regenerate…").click()
    box = page.locator(".el-message-box")
    expect(box).to_contain_text("Regenerate 1 job(s)?")
    expect(box).to_contain_text("NEW job")
    expect(box).to_contain_text("charges its key")
    box.get_by_role("button", name="Cancel").click()
    assert db.one("SELECT count(*) FROM jobs")[0] == 1, "Cancel makes nothing"
    expect(page.locator(".jobs__count")).to_have_text("1 selected")
    page.get_by_role("button", name="Regenerate…").click()
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
    page.get_by_role("button", name="Regenerate…").click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()           # the lost answer is the browser's own "Failed to load resource" console error
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the first request made the job"
    expect(page.locator(".jobs__count")).to_have_text("1 selected")
    expect(page.get_by_role("button", name="Start a new regenerate")).to_be_visible()
    page.get_by_role("button", name="Regenerate…").click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) == keys[0], "the retry sends the same key"
    expect(page.get_by_text("Made 0, already made 1, failed 0")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the retry made no second job"
    expect(page.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    # after an answer the next click is a new action with a new key
    expect(rows(page)).to_have_count(2)
    rows(page).filter(has_text=str(source)[:8]).locator("label.el-checkbox").click()
    page.get_by_role("button", name="Regenerate…").click()
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
    page.get_by_role("button", name="Regenerate…").click()
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
    page.get_by_role("button", name="Regenerate…").click()
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
    page.get_by_role("button", name="Regenerate…").click()
    confirm(page, "Regenerate")
    expect(page.get_by_text(SAFE_AFTER_LOST)).to_be_visible()
    page.problems.clear()
    page.get_by_role("button", name="Start a new regenerate").click()
    confirm(page, "Start a new one")
    expect(page.get_by_role("button", name="Start a new regenerate")).to_have_count(0)
    page.get_by_role("button", name="Regenerate…").click()
    confirm(page, "Regenerate")
    assert sent(page, keys, 2) != keys[0], "an abandoned key is not sent again"
    expect(page.get_by_text("Made 1, already made 0, failed 0")).to_be_visible()


@pytest.mark.fabricated_rows("plants a failed job with no error code", "without an error code")
def test_the_filter_select_is_the_drawn_listbox_and_answers_the_keyboard(app, app_page, live, db):
    """No native select and no Element Plus popup: our listbox, opened and driven from the keyboard, the focus back on
    the control after a choice or Escape, and on a phone the list is not cut by the filter row that scrolls sideways."""
    page = app_page
    k = new_key(db)
    done_job(db, k, ref="a")
    done_job(db, k, ref="b", status="failed")
    app.sign_in(page)
    expect(rows(page)).to_have_count(2)
    expect(page.locator(".jobs select, .el-select, .el-select-dropdown")).to_have_count(0)
    status = page.get_by_role("combobox", name="Status", exact=True)
    expect(status).to_have_attribute("aria-haspopup", "listbox")
    status.focus()
    page.keyboard.press("ArrowDown")
    listbox = page.get_by_role("listbox", name="Status")
    expect(listbox).to_be_visible()
    expect(page.get_by_role("option", name="All", exact=True)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    expect(listbox).to_have_count(0)
    expect(status).to_be_focused()
    expect(status).to_contain_text("Failed")
    expect(rows(page)).to_have_count(1)
    page.keyboard.press("ArrowDown")
    expect(listbox).to_be_visible()
    page.keyboard.press("Escape")
    expect(listbox).to_have_count(0)
    expect(status).to_be_focused()
    page.keyboard.press("d")                        # type-to-select opens the list on Done
    expect(page.get_by_role("option", name="Done", exact=True)).to_have_class(re.compile("is-active"))
    page.keyboard.press("Enter")
    expect(status).to_contain_text("Done")
    expect(rows(page)).to_have_count(1)
    # a phone: the filter row scrolls sideways and the card hides its overflow, the list is drawn whole above both
    page.set_viewport_size({"width": 390, "height": 800})
    status.click()
    box = listbox.bounding_box()
    assert box and box["height"] >= 8 * 44, "the phone list shows all eight options at 44 px: %r" % box
    seen = page.evaluate("""() => { const r = document.querySelector('[role=listbox]').getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.bottom - 10);
        return !!hit && !!hit.closest('[role=listbox]'); }""", isolated_context=False)
    assert seen, "the bottom of the list is covered or cut"
    page.mouse.click(5, 795)
    expect(listbox).to_have_count(0)


def test_a_key_with_no_label_is_named_by_its_prefix_in_the_key_filter(app, app_page, live, db):
    page = app_page
    named, nameless = new_key(db), new_key(db)
    db.run("UPDATE api_keys SET label = 'named-key' WHERE id = %s", named)
    db.run("UPDATE api_keys SET label = '' WHERE id = %s", nameless)
    prefix = db.one("SELECT key_prefix FROM api_keys WHERE id = %s", nameless)[0]
    done_job(db, named, ref="a")
    app.sign_in(page)
    page.locator(".jobs__filter").nth(1).click()
    for name in ("All", "named-key", prefix):
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
    # the drawer slides in: measure it once it has stopped (its right edge is on the screen, or the wait times out)
    page.wait_for_function(
        "w => document.querySelector('[data-testid=job-drawer]').getBoundingClientRect().right <= w + 1", arg=width)
    box = drawer(page).bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
    pick(page, "Technical")
    technical = drawer(page).locator("[data-section=technical]")
    expect(technical).to_be_visible()
    # the tab holds real facts: the labels the job has and the seeded job's own id
    expect(technical).to_contain_text("Provider / pool")
    expect(technical).to_contain_text("Job id")
    expect(technical).to_contain_text(str(job))


@pytest.mark.parametrize("width", [390, 768, 1280])
def test_the_band_draws_its_columns_filters_by_hour_and_breaks_no_csp_rule(app, app_page, live, db, width):
    """The columns are sized by script (`:style` through the CSSOM): the CSP watch of `app_page` fails the test on any
    violation, at every width, while the band is drawn, an hour is chosen and the chip is cleared."""
    page = app_page
    page.set_viewport_size({"width": width, "height": 844})
    k = new_key(db)
    done_job(db, k, new_account(db, label="acct", checked=True), prompt="a cat")
    app.sign_in(page)
    expect(rows(page)).to_have_count(1)
    bars = page.get_by_test_id("hour-bar")
    expect(bars).to_have_count(24)
    expect(page.get_by_role("group", name=re.compile(r"^Jobs per hour over the last 24 hours\."))).to_be_visible()
    heights = page.evaluate(
        "[...document.querySelectorAll('[data-testid=hour-bar] span')].map(s => s.style.height)",
        isolated_context=False)
    assert any(h not in ("", "0%") for h in heights), "the current hour has a drawn segment"
    bars.last.click()
    expect(page.get_by_test_id("hour-chip")).to_contain_text("Hour ")
    expect(rows(page)).to_have_count(1)
    page.get_by_role("button", name="Clear the hour filter").click()
    expect(page.get_by_test_id("hour-chip")).to_have_count(0)


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
    dialog = drawer(page)
    expect(dialog.locator("[data-section=prompt] .jd-prompt__text")).to_have_text(hostile)
    expect(dialog.locator("[data-section=conclusion]")).to_contain_text(hostile)
    pick(page, "Timeline")
    expect(dialog.locator("[data-section=timeline]")).to_contain_text("<img src=x")
    pick(page, "Technical")
    expect(dialog.locator("[data-section=title] input")).to_have_value(hostile)
    assert dialog.locator("img").count() == 0, "planted markup became an element in the detail"
    app.assert_planted_as_text(page)
