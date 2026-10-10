"""Every tab of the dashboard in Chrome against the real app (contracts §16, D60): the browser tier, run on the Mac with
the installed Google Chrome. Each test plants rows through the database, drives the page like an operator and reads
the result back from the database or the page. A script error, a failed request that the test did not expect, or a CSP
violation in the console fails the test."""

import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from jobkit import run
from patchright.sync_api import expect, sync_playwright
from test_admin_jobs import failed_job

from relay_core import crypto, storage
from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

COMMON = "packages/relay-core/src/relay_core/admin/static/common.js"
TABLES = "packages/relay-core/src/relay_core/admin/static/tables.js"
LOGIN = "packages/relay-core/src/relay_core/admin/static/login.js"
STATS = "packages/relay-core/src/relay_core/admin/static/stats.js"
JOBS_JS = "packages/relay-core/src/relay_core/admin/static/tab-jobs.js"
LOGS_JS = "packages/relay-core/src/relay_core/admin/static/tab-logs.js"
ACCOUNTS_JS = "packages/relay-core/src/relay_core/admin/static/tab-accounts.js"
PAGES_PY = "packages/relay-core/src/relay_core/admin/pages.py"
TEST_JS = "packages/relay-core/src/relay_core/admin/static/test.js"


def planted(db, scenario):
    """jobkit's helpers run their own event loop; the browser driver already owns this thread's, so run them aside."""
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(run, db, scenario).result()


# Module scope: a session-wide driver keeps its event loop running and breaks every later `asyncio.run` in the session.
@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        yield chrome
        chrome.close()


@pytest.fixture
def page(browser, live):
    context = browser.new_context(base_url=live.url, viewport={"width": 1500, "height": 1000})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live.url)
    page = context.new_page()
    page.problems = []
    page.on("pageerror", lambda e: page.problems.append("script error: %s" % e))
    page.on("console", lambda m: page.problems.append("console %s: %s" % (m.type, m.text))
            if m.type == "error" else None)
    yield page
    context.close()
    assert not page.problems, page.problems


@pytest.fixture
def pair(monkeypatch):
    keys = crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)
    return keys


def sign_in(page, live):
    page.goto("/login")
    page.fill("#username", live.user)
    page.fill("#password", live.password)
    page.click("#loginButton")
    page.wait_for_url("**/manage")
    expect(page.locator("#whoami")).to_have_text(live.user)


def open_tab(page, name):
    page.click("#tab-%s" % name)
    expect(page.locator("#panel-%s" % name)).to_be_visible()


def column(page, tab, key, row=0):
    return page.locator("#panel-%s tbody tr" % tab).nth(row).locator('td[data-col="%s"]' % key)


def headers(page, tab):
    return page.locator("#panel-%s thead th" % tab).evaluate_all("els => els.map(e => e.dataset.col)")


def cells(page, tab, row=0):
    return page.locator("#panel-%s tbody tr" % tab).nth(row).locator("td").evaluate_all(
        "els => els.map(e => e.dataset.col)")


@mutation(LOGIN, "sessionStorage.setItem(TOKEN_KEY, session.token);", "void session.token;",
          expect="Locator expected")
@mutation(COMMON, "'X-Admin-Session': token()", "'X-Admin-Sess': token()", expect="Locator expected")
def test_login_lands_on_the_dashboard_keeps_the_token_in_session_storage_and_sign_out_ends_it(page, live, db):
    page.goto("/login")
    page.fill("#username", live.user)
    page.fill("#password", "wrong password")
    page.click("#loginButton")
    expect(page.get_by_text("Wrong username or password")).to_be_visible()
    page.problems.clear()                                   # the 401 of a wrong password is a console line
    sign_in(page, live)
    assert page.evaluate("sessionStorage.getItem('relay_admin_token')"), "the token is in sessionStorage"
    assert page.evaluate("Object.keys(localStorage).length") == 0 and page.evaluate("document.cookie") == ""
    assert db.one("SELECT count(*) FROM admin_sessions")[0] == 1
    page.click("text=Sign out")
    page.wait_for_url("**/login")
    assert page.evaluate("sessionStorage.getItem('relay_admin_token')") is None
    assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0
    page.goto("/manage")
    page.wait_for_url("**/login")


@mutation(TABLES, "columns.map((c) => '<td data-col=", "columns.slice().reverse().map((c) => '<td data-col=",
          expect="cells are in the header order")
def test_every_tab_renders_its_columns_in_the_header_order(page, live, db):
    k = new_key(db)
    done_job(db, k, new_account(db, label="acct", checked=True))
    db.one("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms) VALUES (%s, 'fake', '/v1/x', "
           "422, 5) RETURNING id", k)
    sign_in(page, live)
    for tab in ("accounts", "keys", "jobs", "logs"):
        open_tab(page, tab)
        expect(page.locator("#panel-%s tbody tr" % tab).first).to_be_visible()
        assert headers(page, tab) == cells(page, tab), "%s: cells are in the header order" % tab
    open_tab(page, "settings")
    expect(page.locator("#settingsTable tbody tr").first).to_be_visible()
    assert page.locator("#settingsTable thead th").evaluate_all("els => els.map(e => e.dataset.col)") == \
        page.locator("#settingsTable tbody tr").first.locator("td").evaluate_all("els => els.map(e => e.dataset.col)")


@mutation(STATS, "banner.classList.toggle('hidden', s.alerts.length === 0);",
          "banner.classList.toggle('hidden', true);", expect="Locator expected")
def test_the_stat_cards_and_the_attention_banner_follow_the_data(page, live, db):
    k = new_key(db)
    new_account(db, label="only", checked=True)
    done_job(db, k)
    sign_in(page, live)
    expect(page.locator("#statsCards")).to_contain_text("Completed")
    expect(page.locator("#statsCards div", has_text="Completed").locator("h3").first).to_have_text("1")
    expect(page.locator("#alertsBanner")).to_be_visible()                  # one usable account is below the limit of 3
    expect(page.locator("#alertsList")).to_contain_text("accounts_low")
    page.select_option("#providerFilter", "fake")
    expect(page.locator("#alertsBanner")).to_be_visible()


@mutation(ACCOUNTS_JS, "(a.in_use ? '' : switchHtml(", "(false ? '' : switchHtml(", expect="Locator expected")
def test_accounts_import_edit_check_wake_delete_and_the_switch_hidden_while_in_use(page, live, db, pair):
    busy = new_account(db, label="busy one", checked=True)
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'check', 'w1', "
           "clock_timestamp() + interval '1 hour', clock_timestamp() + interval '2 hours')", busy)
    free = new_account(db, label="free one", checked=True)
    sign_in(page, live)
    open_tab(page, "accounts")
    rows = page.locator("#panel-accounts tbody tr")
    expect(rows).to_have_count(2)
    busy_row = rows.filter(has_text="busy one")
    free_row = rows.filter(has_text="free one")
    expect(busy_row.locator("input[data-change=toggleAccount]")).to_have_count(0)
    expect(free_row.locator("input[data-change=toggleAccount]")).to_have_count(1)

    page.click("text=Import")
    page.fill("#importCredentials", "cookie-one\ncookie-two")
    page.fill("#importProxy", "10.1.2.3:8080:user:pw-secret")
    page.click("#modalBody >> text=Import")
    expect(page.locator("#importResult")).to_have_text("Imported 2, rejected 0")
    assert db.one("SELECT count(*) FROM accounts")[0] == 4
    assert db.one("SELECT count(*) FROM accounts WHERE proxy_host = '10.1.2.3' AND proxy_auth_enc IS NOT NULL")[0] == 2
    page.click("text=Close")

    page.get_by_role("button", name="Refresh catalog").click()
    expect(page.get_by_text("Refresh catalog fake: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'refresh_catalog'")[0] == 1
    page.get_by_role("button", name="Check all").click()
    expect(page.get_by_text("Check all fake: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'check_all'")[0] == 1
    free_row.get_by_role("button", name="Check", exact=True).click()
    expect(page.get_by_text("Check: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'check' AND account_id = %s", free)[0] == 1
    free_row.get_by_role("button", name="Wake").click()
    expect(page.get_by_text("Clear cooldown: queued")).to_be_visible()

    free_row.get_by_role("button", name="Edit").click()
    page.fill("#editLabel", "renamed")
    page.click("#modalBody >> text=Save")
    expect(rows.filter(has_text="renamed")).to_have_count(1)
    assert db.one("SELECT label FROM accounts WHERE id = %s", free)[0] == "renamed"

    page.once("dialog", lambda d: d.accept())
    rows.filter(has_text="renamed").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Delete: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'delete_account'")[0] == 1


def test_keys_create_shows_the_raw_key_once_toggle_and_delete(page, live, db):
    sign_in(page, live)
    open_tab(page, "keys")
    page.click("text=New key")
    page.fill("#keyLabel", "customer one")
    page.locator("[data-wallet][data-provider=fake][data-kind=video] select").select_option("limit")
    page.locator("[data-wallet][data-provider=fake][data-kind=video] [data-f=day]").fill("5")
    page.locator("[data-wallet][data-provider=fake][data-kind=image] select").select_option("unlimited")
    page.click("#modalBody >> text=Create")
    raw = page.locator("#newKeyValue").inner_text()
    assert raw.startswith("rk-") and db.one("SELECT count(*) FROM api_keys")[0] == 1
    assert raw not in json.dumps(db.all("SELECT key_hash, key_prefix FROM api_keys")), "only the hash is stored"
    row = page.locator("#keysTable tbody tr").first
    expect(row).to_contain_text("customer one")
    expect(row).to_contain_text("fake video: day 0/5")
    expect(row).to_contain_text("fake image: unlimited")
    page.reload()
    open_tab(page, "keys")
    expect(page.locator("#newKeyBox")).to_be_hidden()
    assert raw not in page.content(), "the raw key is gone after a reload"
    page.locator("#keysTable input[data-change=toggleKey]").evaluate("el => el.click()")
    expect(page.locator("#keysTable tbody tr").first).to_contain_text("disabled")
    assert db.one("SELECT enabled FROM api_keys")[0] is False
    page.once("dialog", lambda d: d.accept())
    page.locator("#keysTable").get_by_role("button", name="Delete").click()
    expect(page.locator("#keysTable tbody tr").first).to_contain_text("No API keys")
    assert db.one("SELECT count(*) FROM api_keys")[0] == 0


def setting_row(page):
    return page.locator("#settingsTable tbody tr", has_text="log_retention_days")


def test_settings_edit_marks_the_source_and_reset_returns_to_the_default(page, live, db):
    sign_in(page, live)
    open_tab(page, "settings")
    row = setting_row(page)
    expect(row.locator("td[data-col=source]")).to_have_text("default")
    row.locator("input").fill("45")
    row.get_by_role("button", name="Save").click()
    expect(setting_row(page).locator("td[data-col=source]")).to_have_text("db")
    assert db.one("SELECT value FROM settings_kv WHERE key = 'log_retention_days'")[0] in (45, "45")
    setting_row(page).get_by_role("button", name="Reset").click()
    expect(setting_row(page).locator("td[data-col=source]")).to_have_text("default")
    expect(page.locator("#envTable")).to_contain_text("BILLING_TZ")
    expect(page.locator("#pricesTable")).to_be_visible()


@mutation(LOGS_JS, "if (!confirm('Clear every request log row", "if (false && !confirm('Clear every request log row",
          expect="a clear asks first")
def test_logs_filter_search_detail_and_clear(page, live, db):
    k = new_key(db)
    job = done_job(db, k)
    db.run("UPDATE jobs SET title = %s WHERE id = %s", "Quiet 100% Lake", job)
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, body_redacted) "
           "VALUES (%s, 'fake', '/v1/video/generations', 200, %s, 9, '{\"prompt\": \"a cat\"}')", k, job)
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms, error_code) "
           "VALUES (%s, 'fake', '/v1/video/generations', 422, 3, 'input.invalid')", k)
    sign_in(page, live)
    open_tab(page, "logs")
    expect(page.locator("#logsTable tbody tr")).to_have_count(2)
    page.select_option("#logsStatus", "pre_job")
    expect(page.locator("#logsTable tbody tr")).to_have_count(1)
    expect(column(page, "logs", "error")).to_have_text("input.invalid")
    page.select_option("#logsStatus", "all")
    page.fill("#logsQuery", "100%")
    page.press("#logsQuery", "Enter")
    expect(page.locator("#logsTable tbody tr")).to_have_count(1)
    column(page, "logs", "actions").get_by_role("button", name="Details").click()
    expect(page.locator("#logRequest")).to_contain_text("a cat")
    page.click("text=Close")
    page.fill("#logsQuery", "")
    page.press("#logsQuery", "Enter")
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
    page.click("text=Clear logs")
    expect(page.locator("#logsTable")).to_contain_text("No log rows")
    assert asked, "a clear asks first"
    assert db.one("SELECT count(*) FROM request_logs")[0] == 0 and db.one("SELECT count(*) FROM jobs")[0] == 1


@mutation(JOBS_JS, "textContent = job.prompt;", "textContent = job.prompt + ' ' + job.marker;",
          expect="Locator expected to have text")
def test_jobs_detail_copy_prompt_rename_bulk_recheck_and_the_pager(page, live, db):
    k = new_key(db)
    jobs = [done_job(db, k, ref="j%d" % i, status="failed", prompt="a cat on a roof" if i == 0 else "a prompt")
            for i in range(3)]
    db.run("UPDATE jobs SET error_code = 'render.deadline'")
    sign_in(page, live)
    open_tab(page, "jobs")
    expect(page.locator("#jobsTable tbody tr")).to_have_count(3)
    row = page.locator("#jobsTable tbody tr", has_text=str(jobs[0])[:8])
    row.get_by_role("button", name="Details").click()
    expect(page.locator("#jobPrompt")).to_have_text("a cat on a roof")
    marker = db.one("SELECT marker FROM jobs WHERE id = %s", jobs[0])[0]
    assert marker not in page.locator("#jobPrompt").inner_text(), "the marker is not part of the prompt"
    page.click("text=Copy prompt")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied == "a cat on a roof", "Copy prompt gives the customer's words"
    page.fill("#jobTitle", "My new title")
    page.get_by_role("button", name="Rename").click()
    expect(page.get_by_text("Renamed")).to_be_visible()
    assert db.one("SELECT title FROM jobs WHERE id = %s", jobs[0])[0] == "My new title"
    page.click("text=Close")
    page.locator("#jobsTable [data-job-select]").nth(0).check()
    page.locator("#jobsTable [data-job-select]").nth(1).check()
    page.get_by_role("button", name="Re-check selected").click()
    expect(page.get_by_text("Re-checked")).to_be_visible()


@mutation(PAGES_PY, "CSP.replace(\"script-src 'self';\",", "CSP.replace(\"zz\",", expect="no CSP violation")
def test_the_reference_page_renders_its_content_and_the_browser_reports_no_csp_violation(page, live):
    page.goto("/reference")
    page.wait_for_timeout(4000)
    violations = [p for p in page.problems if "Content Security Policy" in p]
    assert not violations, "no CSP violation: %s" % violations
    assert page.evaluate("document.getElementById('fallback').hidden") is True, "the renderer loaded"
    assert page.evaluate("document.body.innerText.length") > 200, "rendered content"


@mutation(TEST_JS, "apiKey = el('apiKey').value.trim();",
          "apiKey = el('apiKey').value.trim(); sessionStorage.setItem('k', apiKey);", expect="no key in a store")
def test_the_test_page_loads_models_generates_and_keeps_the_key_in_memory_only(page, live, db):
    new_account(db, checked=True)
    api_key = live.call(live.env.new_key())
    page.goto("/test")
    page.fill("#apiKey", api_key.raw)
    page.click("#loadKey")
    expect(page.locator(".model-item", has_text="fake-video")).to_be_visible()
    stored = "Object.keys(localStorage).length + Object.keys(sessionStorage).length"
    assert page.evaluate(stored) == 0, "no key in a store"
    page.locator(".model-item", has_text="fake-video").click()
    page.fill("#prompt", "a cat")
    page.click("#generate")
    expect(page.locator("#status")).to_contain_text("Job")
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'a cat'")[0] == 1
    assert page.evaluate(stored) == 0, "no key in a store"
    assert api_key.raw not in json.dumps(page.context.cookies())
    page.reload()
    expect(page.locator("#apiKey")).to_have_value("")


@mutation(COMMON, ".replace(/</g, '&lt;')", "", expect="planted markup became an element")
@mutation(COMMON, "parsed.origin !== location.origin || ", "", expect="safeMediaUrl")
def test_server_text_is_escaped_everywhere_and_a_foreign_or_script_media_url_never_renders(page, live, db, pair):
    hostile = '<img src=x onerror="window.__pwned=1">'
    k = new_key(db)
    acct = new_account(db, label=hostile, checked=True)
    db.run("UPDATE accounts SET display_name = %s, last_error = %s WHERE id = %s", hostile, hostile, acct)
    job = done_job(db, k, acct, status="failed", prompt=hostile)
    db.run("UPDATE jobs SET title = %s, error_code = 'render.deadline', error = %s WHERE id = %s",
           hostile, hostile, job)
    db.run("INSERT INTO job_events (job_id, source, stage, text) VALUES (%s, 'engine', %s, %s)", job, hostile, hostile)
    db.run("UPDATE api_keys SET label = %s WHERE id = %s", hostile, k)
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, body_redacted) VALUES "
           "(%s, 'fake', %s, 422, NULL, 1, %s::jsonb)", k, hostile, json.dumps({"prompt": hostile}))
    sign_in(page, live)
    for tab in ("accounts", "keys", "jobs", "logs"):
        open_tab(page, tab)
        expect(page.locator("#panel-%s tbody tr" % tab).first).to_be_visible()
        assert page.locator("#panel-%s img" % tab).count() == 0, "%s: planted markup became an element" % tab
    open_tab(page, "jobs")
    page.get_by_role("button", name="Details").click()
    expect(page.locator("#jobPrompt")).to_have_text(hostile)
    assert page.locator("#modalBody img").count() == 0
    page.click("text=Close")
    open_tab(page, "logs")
    page.get_by_role("button", name="Details").click()
    expect(page.locator("#logRequest")).to_contain_text("<img src=x")
    assert page.evaluate("window.__pwned", isolated_context=False) is None
    safe = page.evaluate("""() => ['/m/abc', 'javascript:alert(1)', 'data:text/html,x', 'blob:http://a/b',
        'https://evil.example/m/abc', '//evil.example/m/abc', '/admin/jobs/1/content', location.origin + '/m/ok?x=1']
        .map((u) => safeMediaUrl(u))""", isolated_context=False)
    assert safe == ["/m/abc", None, None, None, None, None, None, "/m/ok?x=1"], "safeMediaUrl: %s" % safe


def test_a_second_session_after_a_password_change_must_sign_in_again(page, live, db):
    sign_in(page, live)
    open_tab(page, "settings")
    page.fill("#oldPassword", live.password)
    page.fill("#newPassword", "another long passphrase " + uuid.uuid4().hex[:6])
    page.get_by_role("button", name="Change password").click()
    page.wait_for_url("**/login")
    assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


@mutation(JOBS_JS, "'/revoke-links'", "'/revoke-linkz'", expect="Locator expected")
@mutation(JOBS_JS, "'/resend-webhook'", "'/resend-webhooq'", expect="Locator expected")
def test_job_detail_shows_results_from_share_links_downloads_revokes_resends_and_shows_debug_as_text(page, live, db):
    k = new_key(db)
    job = done_job(db, k, new_account(db, label="acct", checked=True))
    db.run("UPDATE jobs SET callback_url = 'https://hooks.example/h', callback_status = 'failed', "
           "callback_attempts = 3, callback_for = clock_timestamp() WHERE id = %s", job)
    out_key = db.one("SELECT object_key FROM job_media WHERE job_id = %s", job)[0]
    token = storage.new_share_token()
    db.run("UPDATE job_media SET mime = 'image/png', share_token = %s WHERE job_id = %s", token, job)
    live.call(live.env.store.put_bytes(out_key, PNG, "image/png"))
    debug_key = "%s/debug/0.html" % job
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
           "VALUES (%s, 'debug', 0, %s, 'text/html', 40)", job, debug_key)
    live.call(live.env.store.put_bytes(debug_key, b"<script>window.__debug_ran = 1</script><b>x</b>", "text/html"))
    sign_in(page, live)
    open_tab(page, "jobs")
    page.get_by_role("button", name="Details").click()
    image = page.locator("#modalBody img")
    expect(image).to_have_count(1)
    assert image.get_attribute("src") == "/m/" + token
    page.wait_for_function("document.querySelector('#modalBody img').naturalWidth === 1")
    with page.expect_download() as download:
        page.get_by_role("button", name="Download").click()
    assert download.value.suggested_filename.startswith("job-")
    page.get_by_role("button", name=" (text/html)", exact=False).click()
    expect(page.locator("#debugView")).to_contain_text("<script>window.__debug_ran = 1</script>")
    assert page.evaluate("window.__debug_ran", isolated_context=False) is None, "a debug page never runs"
    page.get_by_role("button", name="Resend webhook").click()
    expect(page.get_by_text("Webhook queued again")).to_be_visible()
    assert db.one("SELECT callback_status FROM jobs WHERE id = %s", job)[0] == "pending"
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Revoke share links").click()
    expect(page.get_by_text("Revoked 1 link(s)")).to_be_visible()
    assert db.one("SELECT share_token FROM job_media WHERE job_id = %s AND role = 'output'", job)[0] is None
    expect(page.locator("#modalBody img")).to_have_count(0)


@mutation(JOBS_JS, "if (!confirm('Regenerate '", "if (false && !confirm('Regenerate '",
          expect="a bulk regenerate asks first")
def test_bulk_regenerate_asks_first_and_makes_new_jobs_and_no_page_scrolls_inside_itself(page, live, db):
    k = new_key(db)
    new_account(db, label="a", checked=True)
    planted(db, lambda p: failed_job(p, k, "one"))
    sign_in(page, live)
    open_tab(page, "jobs")
    page.locator("#jobsTable [data-job-select]").first.check()
    messages = []
    page.once("dialog", lambda d: (messages.append(d.message), d.accept()))
    page.get_by_role("button", name="Regenerate selected").click()
    expect(page.get_by_text("Made 1, already made 0, failed 0")).to_be_visible()
    assert messages, "a bulk regenerate asks first"
    assert "today" in messages[0] and "charges" in messages[0]
    assert db.one("SELECT count(*) FROM jobs")[0] == 2
    for tab in ("accounts", "keys", "jobs", "logs", "settings"):
        open_tab(page, tab)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), tab
        assert page.evaluate("""(id) => [...document.querySelectorAll('#' + id + ' *')].filter((e) =>
            getComputedStyle(e).overflowY === 'auto' && e.scrollHeight > e.clientHeight + 1).length""",
                             "panel-" + tab) == 0, "%s: a block scrolls inside the page" % tab


@mutation(JOBS_JS, "        forgetOperation(action);\n        return out;", "        return out;",
          expect="a new action gets a new key")
@mutation(JOBS_JS, "        else err.retrySafe = true;",
          "        else { forgetOperation(action); err.retrySafe = true; }", expect="the retry sends the same key")
def test_regenerate_retries_with_the_same_key_after_a_lost_answer_and_a_reload_and_a_new_click_gets_a_new_one(
        page, live, db):
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = planted(db, lambda p: failed_job(p, k, "one"))
    keys = []
    page.on("request", lambda r: r.url.endswith("/regenerate") and keys.append(r.post_data_json["operation_key"]))
    page.on("dialog", lambda d: d.accept())
    lose = {"answer": True}

    def route(r):
        if lose["answer"]:
            r.fetch()           # the server makes the job; the answer never reaches the page
            lose["answer"] = False
            r.abort()
        else:
            r.continue_()

    def regenerate():
        if page.locator("#modalBody").count():
            page.get_by_role("button", name="Close").click()
        open_tab(page, "jobs")
        page.locator("#jobsTable tbody tr", has_text=str(source)[:8]).get_by_role("button", name="Details").click()
        page.get_by_role("button", name="Regenerate", exact=True).click()

    def sent(n):            # the key of the n-th request, once the page has sent it
        for _ in range(100):
            if len(keys) >= n:
                return keys[n - 1]
            page.wait_for_timeout(50)
        raise AssertionError("the page sent %d regenerate requests, not %d" % (len(keys), n))

    page.route("**/admin/jobs/*/regenerate", route)
    sign_in(page, live)
    regenerate()
    expect(page.get_by_text("Trying again is safe")).to_be_visible()
    page.problems.clear()       # the lost answer is the browser's own "Failed to load resource" console error
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the first request made the job"
    page.reload()
    regenerate()
    assert sent(2) == keys[0], "the retry sends the same key"
    expect(page.get_by_text("Already made: job")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 2, "the retry made no second job"
    regenerate()
    assert sent(3) != keys[1], "a new action gets a new key"
    expect(page.get_by_text("New job ")).to_be_visible()
    assert db.one("SELECT count(*) FROM jobs")[0] == 3


@mutation(JOBS_JS, "'bulk:' + ids.slice().sort().join(',')", "'bulk:' + Math.random()", expect="the same key")
@mutation(JOBS_JS, "err.status < 500", "err.status < 600", expect="it will not charge twice")
def test_a_bulk_regenerate_sends_the_same_key_after_a_lost_answer_and_after_a_500_and_a_new_one_after_an_answer(
        page, live, db):
    k = new_key(db)
    new_account(db, label="a", checked=True)
    source = planted(db, lambda p: failed_job(p, k, "one"))
    keys = []
    page.on("request", lambda r: r.url.endswith("/bulk-regenerate") and keys.append(r.post_data_json["operation_key"]))
    page.on("dialog", lambda d: d.accept())
    answers = iter(["cut", "500", "through"])

    def route(r):
        how = next(answers, "through")
        if how == "cut":
            r.abort()
        elif how == "500":
            r.fulfill(status=500, content_type="application/json",
                      body='{"error": {"code": "internal.unexpected", "message": "boom"}}')
        else:
            r.continue_()

    def sent(n):
        for _ in range(100):
            if len(keys) >= n:
                return keys[n - 1]
            page.wait_for_timeout(50)
        raise AssertionError("the page sent %d requests, not %d" % (len(keys), n))

    page.route("**/admin/jobs/bulk-regenerate", route)
    sign_in(page, live)
    open_tab(page, "jobs")
    page.locator("#jobsTable [data-job-select]").first.check()
    for attempt in (1, 2, 3):
        page.get_by_role("button", name="Regenerate selected").click()
        sent(attempt)
        if attempt < 3:
            expect(page.get_by_text("it will not charge twice")).to_be_visible()
            page.wait_for_timeout(2800)       # the toast is gone before the next click
        page.problems.clear()                 # a cut or a 500 is the browser's own console error
    assert sent(2) == sent(1) and sent(3) == sent(1), "the same key after a lost answer and after a 500"
    expect(page.get_by_text("Made 1, already made 0, failed 0")).to_be_visible()
    expect(page.locator("#jobsTable tbody tr")).to_have_count(2)      # the reloaded table: the source and its new job
    page.locator("#jobsTable tbody tr", has_text=str(source)[:8]).locator("[data-job-select]").check()
    page.get_by_role("button", name="Regenerate selected").click()
    assert sent(4) != sent(1), "the next action after an answer gets a new key"


def test_the_accounts_tab_shows_the_composer_of_the_account_escaped(page, live, db):
    acct = new_account(db, label="composed", checked=True)
    db.run("UPDATE accounts SET provider_state = %s::jsonb WHERE id = %s", '{"composer": "<b>agent</b>"}', acct)
    sign_in(page, live)
    open_tab(page, "accounts")
    expect(page.get_by_text("composer <b>agent</b>")).to_be_visible()
    assert page.locator("#accountsTable b").count() == 0, "the composer text is escaped"


@pytest.mark.fabricated_rows("plants an account charge that is not the job account_cost", "account_cost")
def test_accounts_show_made_counts_the_drift_badge_and_auto_refresh_picks_up_new_rows(page, live, db):
    k = new_key(db)
    acct = new_account(db, label="counted", checked=True)
    job = done_job(db, k, acct)
    db.run("UPDATE jobs SET taken_at = clock_timestamp() WHERE id = %s", job)
    db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) VALUES "
           "(%s, 'video', 1, 'counted', (clock_timestamp() AT TIME ZONE 'UTC')::date)", acct)
    db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) VALUES "
           "(%s, %s, 'video', 1, (clock_timestamp() AT TIME ZONE 'UTC')::date)", acct, job)
    sign_in(page, live)
    open_tab(page, "accounts")
    expect(column(page, "accounts", "videos")).to_have_text("1")
    expect(column(page, "accounts", "points")).to_contain_text("video: 1 / 4")
    expect(column(page, "accounts", "points")).to_contain_text("drift")            # 4 - 1 spent says 3, the row says 1
    page.locator("input[data-change=toggleAutoRefresh]").evaluate("el => el.click()")
    new_account(db, label="late arrival", checked=True)
    expect(page.locator("#panel-accounts tbody tr")).to_have_count(2, timeout=15000)
    page.locator("input[data-change=toggleAutoRefresh]").evaluate("el => el.click()")


def test_a_whole_job_row_opens_the_detail_but_its_tick_box_does_not(page, live, db):
    k = new_key(db)
    job = done_job(db, k, ref="row")
    db.run("UPDATE jobs SET title = 'clickable' WHERE id = %s", job)
    sign_in(page, live)
    open_tab(page, "jobs")
    row = page.locator("#jobsTable tbody tr")
    row.locator("[data-job-select]").check()
    assert page.locator("#modalBody").count() == 0, "ticking a row opens nothing"
    column(page, "jobs", "title").click()
    expect(page.locator("#jobBox")).to_be_visible()
    assert page.locator("#jobBox").get_attribute("data-id") == str(job)


@pytest.mark.parametrize("tab", ["jobs", "logs", "accounts"])
def test_every_listing_pages_forward_and_back_without_a_repeat(page, live, db, tab):
    k = new_key(db)
    if tab == "jobs":
        for _ in range(52):
            db.job(k, provider="fake")
    elif tab == "logs":
        db.run("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms, at) SELECT %s, 'fake', "
               "'/v1/x', 422, 1, now() - make_interval(secs => n) FROM generate_series(1, 52) n", k)
    else:
        for i in range(52):
            new_account(db, label="acct%02d" % i, checked=True)
    sign_in(page, live)
    open_tab(page, tab)
    rows = page.locator("#panel-%s tbody tr" % tab)
    expect(rows).to_have_count(50)
    first_page = rows.evaluate_all("els => els.map(e => e.innerText)")
    page.get_by_role("button", name="Next").click()
    expect(rows).to_have_count(2)
    page.get_by_role("button", name="Prev").click()
    expect(rows).to_have_count(50)
    assert rows.evaluate_all("els => els.map(e => e.innerText)") == first_page, "the first page comes back as it was"
    assert page.get_by_role("button", name="Prev").is_disabled()
