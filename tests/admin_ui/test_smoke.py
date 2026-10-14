"""Every tab of the dashboard in Chrome against the real app (contracts §16, D60): the browser tier, run on the Mac with
the installed Google Chrome. Each test plants rows through the database, drives the page like an operator and reads
the result back from the database or the page. A script error, a failed request that the test did not expect, or a CSP
violation in the console fails the test."""

import base64
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from jobkit import run
from patchright.sync_api import expect, sync_playwright
from test_admin_alert_groups import group_of, plant_unclear, stamp
from test_admin_alerts import plant
from test_admin_jobs import failed_job
from test_frame_plan import put

from relay_core import crypto, storage
from relay_core.providers import FrameUse
from relay_core.testing.fake_provider import FAKE_FRAMES
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
TEST_JS = "packages/relay-core/src/relay_core/admin/static/tab-test.js"
KEYS_JS = "packages/relay-core/src/relay_core/admin/static/tab-keys.js"


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


@mutation(STATS, "banner.classList.toggle('hidden', s.alerts.length === 0 && open === 0);",
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
    expect(page.get_by_text("Wake: clear cooldown and strikes: queued")).to_be_visible()

    free_row.get_by_role("button", name="Edit").click()
    page.fill("#editLabel", "renamed")
    page.click("#modalBody >> text=Save")
    expect(rows.filter(has_text="renamed")).to_have_count(1)
    assert db.one("SELECT label FROM accounts WHERE id = %s", free)[0] == "renamed"

    page.once("dialog", lambda d: d.accept())
    rows.filter(has_text="renamed").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Delete: queued")).to_be_visible()
    assert db.one("SELECT count(*) FROM commands WHERE kind = 'delete_account'")[0] == 1


@mutation(ACCOUNTS_JS, "const WAKE_LABEL = 'Wake: clear cooldown and strikes';", "const WAKE_LABEL = 'Clear cooldown';",
          expect="Locator expected")
def test_wake_says_it_clears_the_cooldown_and_the_strikes(page, live, db):
    new_account(db, label="asleep one", checked=True)
    sign_in(page, live)
    open_tab(page, "accounts")
    wake = page.get_by_role("button", name="Wake")
    expect(wake).to_have_attribute("title", re.compile("strike"))
    wake.click()
    expect(page.get_by_text("Wake: clear cooldown and strikes: queued")).to_be_visible()


@mutation(ACCOUNTS_JS, "(a.state_reason ? ' (' + a.state_reason + ')' : '')", "''", expect="Locator expected")
def test_a_check_shows_what_it_found_and_a_dead_account_shows_its_reason(page, live, db):
    gone = new_account(db, label="gone one", checked=True)
    db.run("UPDATE accounts SET state = 'dead', state_reason = 'signed out' WHERE id = %s", gone)
    sign_in(page, live)
    open_tab(page, "accounts")
    row = page.locator("#panel-accounts tbody tr").filter(has_text="gone one")
    row.get_by_role("button", name="Check", exact=True).click()
    expect(page.get_by_text("Check: queued")).to_be_visible()
    db.run("UPDATE commands SET status = 'done', result = %s::jsonb, finished_at = clock_timestamp() "
           "WHERE kind = 'check' AND account_id = %s", json.dumps({"outcome": "checked"}), gone)
    expect(page.get_by_text("Check: checked - account dead (signed out)")).to_be_visible(timeout=10000)


@mutation(COMMON, "if (navigator.clipboard && window.isSecureContext)", "if (navigator.clipboard)",
          expect="the fallback is taken")
def test_copy_falls_back_to_a_selected_field_when_the_page_is_not_a_secure_context(page, live, db):
    sign_in(page, live)
    page.evaluate("""() => {
        Object.defineProperty(window, 'isSecureContext', { value: false });
        navigator.clipboard.writeText = () => { window.__api = true; return Promise.resolve(); };
        window.__copied = [];
        document.execCommand = (cmd) => { window.__copied.push([cmd, document.activeElement.value]); return true; };
    }""", isolated_context=False)
    done = page.evaluate(
        "async () => [await copyToClipboard('rk-secret-text'), window.__copied, window.__api === true]",
        isolated_context=False)
    assert done == [True, [["copy", "rk-secret-text"]], False], "the fallback is taken: %s" % done
    page.evaluate("document.execCommand = () => false", isolated_context=False)
    page.evaluate("""() => {
        const c = document.createElement('code');
        c.id = 'probe';
        c.textContent = 'pick me';
        document.body.appendChild(c);
    }""", isolated_context=False)
    page.evaluate("ACTIONS.copyText({ dataset: { from: '#probe' }, textContent: 'Copy' })", isolated_context=False)
    expect(page.get_by_text("Ctrl+C")).to_be_visible()
    assert page.evaluate("getSelection().toString()", isolated_context=False) == "pick me"


@mutation(COMMON, "await navigator.clipboard.writeText(text); return true;", "return true;",
          expect="the clipboard holds the key")
@mutation(KEYS_JS, "button('copyText', 'Copy', { from: '#newKeyValue' }, BTN)", "''", expect="Locator expected")
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
    copy = page.locator("#newKeyBox button")
    expect(copy).to_be_visible()
    copy.click()
    expect(copy).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == raw, "the clipboard holds the key"
    expect(page.locator("#newKeyBox")).to_contain_text("not shown again")
    assert raw not in json.dumps(db.all("SELECT key_hash, key_prefix FROM api_keys")), "only the hash is stored"
    row = page.locator("#keysTable tbody tr").first
    expect(row).to_contain_text("customer one")
    expect(row).to_contain_text("fake video: day 0/5")
    expect(row).to_contain_text("fake image: unlimited")
    prefix = row.locator("td[data-col=label] .font-mono").inner_text().removesuffix("Copy").strip()
    row.get_by_role("button", name="Copy").click()
    assert page.evaluate("navigator.clipboard.readText()") == prefix and raw.startswith(prefix), "only the prefix"
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
@mutation(LOGS_JS, "ACTIONS.refreshLogs = () => {\n    readLogFilters();",
          "ACTIONS.refreshLogs = () => {\n    logsView.status = 'all';",
          expect="Locator expected")
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
    db.run("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms, error_code) "
           "VALUES (%s, 'fake', '/v1/video/generations', 429, 4, 'rate.limited')", k)
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("#logsTable tbody tr")).to_have_count(2)
    page.select_option("#logsStatus", "all")
    expect(page.locator("#logsTable tbody tr")).to_have_count(3)
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


@mutation(TEST_JS, "testState.apiKey = tid('apiKey').value.trim();",
          "testState.apiKey = tid('apiKey').value.trim(); localStorage.setItem('k', testState.apiKey);",
          expect="no key in a persistent store")
@mutation(TEST_JS, "const files = await testDb('get');", "const files = null;", expect="Locator expected")
@mutation(TEST_JS, "for (const id of TEST_FIELDS) if (typeof saved[id] === 'string') tid(id).value = saved[id];",
          "", expect="Locator expected")
def test_the_test_tab_generates_keeps_the_form_and_images_over_a_reload_and_the_key_only_per_browser_tab(
        page, live, db):
    new_account(db, checked=True)
    api_key = live.call(live.env.new_key())
    sign_in(page, live)
    open_tab(page, "test")
    page.fill("#apiKey", api_key.raw)
    page.click("#loadKey")
    model = page.locator("#models [data-act=testPick]", has_text="fake-video")
    expect(model).to_be_visible()
    model.click()
    page.fill("#prompt", "a cat")
    page.fill("#title", "kept title")
    page.set_input_files("#imageFiles", files=[{"name": "ref.png", "mimeType": "image/png", "buffer": PNG}])
    expect(page.locator("#imageList")).to_contain_text("ref.png")
    page.click("#generate")
    expect(page.locator("#status")).to_contain_text("Job")
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'a cat'")[0] == 1
    assert api_key.raw not in page.evaluate("JSON.stringify(Object.assign({}, localStorage))"), \
        "no key in a persistent store"
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") == api_key.raw
    assert api_key.raw not in json.dumps(page.context.cookies())
    page.reload()
    expect(page.locator("#prompt")).to_have_value("a cat")
    expect(page.locator("#title")).to_have_value("kept title")
    expect(page.locator("#imageList")).to_contain_text("ref.png")
    expect(page.locator("#apiKey")).to_have_value(api_key.raw)
    expect(page.locator("#modelName")).to_have_text("fake-video")
    page.get_by_role("button", name="Clear form").click()
    expect(page.locator("#prompt")).to_have_value("")
    page.reload()
    expect(page.locator("#prompt")).to_have_value("")
    expect(page.locator("#imageList")).to_have_text("")
    expect(page.locator("#modelName")).to_have_text("Pick a model")


GUARD_NON_IMAGE = "if (!f || !(f.blob instanceof Blob) || !f.blob.type.startsWith('image/')) continue;"
GUARD_LIMITS = "if (kept.length >= TEST_MAX_IMAGES || bytes + f.blob.size > TEST_MAX_BYTES) continue;"
OPEN_STORE = """const db = await new Promise((ok, no) => {
            const r = indexedDB.open('relay-admin-test', 1);
            r.onupgradeneeded = () => r.result.createObjectStore('kv');
            r.onsuccess = () => ok(r.result);
            r.onerror = no;
        });"""
SEED_STORE = """async (kind) => {
        const item = (name, type, size) => ({ name, type, blob: new Blob([new Uint8Array(size)], { type }) });
        const list = kind === 'many' ? Array.from({ length: 12 }, (_, i) => item('p' + i, 'image/png', 1024))
            : kind === 'big' ? [item('big.png', 'image/png', 13 * 1048576)]
            : [item('p.png', 'image/png', 1024), item('x.html', 'text/html', 3)];
        %s
        await new Promise((ok, no) => {
            const tx = db.transaction('kv', 'readwrite');
            tx.objectStore('kv').put(list, 'files');
            tx.oncomplete = ok;
            tx.onerror = no;
        });
        db.close();
    }""" % OPEN_STORE
READ_STORE = """async () => {
        %s
        const list = await new Promise((ok) => {
            const q = db.transaction('kv').objectStore('kv').get('files');
            q.onsuccess = () => ok(q.result || []);
        });
        db.close();
        return list.map((f) => f.blob.type);
    }""" % OPEN_STORE


@mutation(TEST_JS, GUARD_NON_IMAGE, "if (!f) continue;", expect="Locator expected")
@mutation(TEST_JS, GUARD_LIMITS, "", expect="Locator expected")
def test_a_stored_image_set_over_the_limits_or_with_a_non_image_is_not_restored_and_is_cleaned(page, live):
    sign_in(page, live)
    open_tab(page, "test")
    for kind, left in (("many", ["image/png"] * 10), ("big", []), ("html", ["image/png"])):
        page.evaluate(SEED_STORE, kind, isolated_context=False)
        page.reload()
        expect(page.locator("#panel-test")).to_be_visible()
        expect(page.locator("#imageList li")).to_have_count(len(left))
        expect(page.locator("#imageNote")).to_contain_text("broke the limits")
        assert page.evaluate(READ_STORE, isolated_context=False) == left, "the store is cleaned: %s" % kind


def test_the_test_tab_still_works_when_every_store_is_blocked_and_the_old_link_lands_on_it(page, live, db):
    new_account(db, checked=True)
    api_key = live.call(live.env.new_key())
    sign_in(page, live)
    page.add_init_script("""
        for (const name of ['localStorage', 'indexedDB']) {
            Object.defineProperty(window, name, { get() { throw new DOMException('blocked', 'SecurityError'); } });
        }""")
    page.goto("/test")
    page.wait_for_url(lambda url: url.endswith("/manage#test"))
    expect(page.locator("#panel-test")).to_be_visible()
    page.fill("#apiKey", api_key.raw)
    page.click("#loadKey")
    expect(page.locator("#models [data-act=testPick]", has_text="fake-video")).to_be_visible()
    page.locator("#models [data-act=testPick]", has_text="fake-video").click()
    page.fill("#prompt", "a dog")
    page.set_input_files("#imageFiles", files=[{"name": "ref.png", "mimeType": "image/png", "buffer": PNG}])
    expect(page.locator("#imageNote")).to_contain_text("could not be saved")
    page.click("#generate")
    expect(page.locator("#status")).to_contain_text("Job")
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'a dog'")[0] == 1


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


# ---- prompt frames ---------------------------------------------------------------------------------------------------
FRAMES_JS = "packages/relay-core/src/relay_core/admin/static/tab-frames.js"
VIDEO = '#panel-frames [data-frame-kind="video"]'
HOSTILE = '<img src=x onerror="window.__pwned=1"> {{tracking_code}} {{client_prompt}}'


def frame_texts(db, model):
    """The stored texts of a video target, oldest first (model None = the kind's default)."""
    return [r[0] for r in db.all("SELECT text FROM prompt_frames WHERE provider = 'fake' AND kind = 'video' AND "
                                 "model IS NOT DISTINCT FROM %s::text ORDER BY id", model)]


@mutation(FRAMES_JS, ", text: draft }", " }", expect="Draft single.")
@mutation(FRAMES_JS, "showWarnings(saved.warnings);", "showWarnings([]);", expect="frameWarnings")
@mutation(FRAMES_JS, "err.param ? err.message + ' (' + err.param + ')' : err.message;", "err.message;",
          expect="frameRefusal")
@mutation(FRAMES_JS, "(answer.kept.join(', ') || '-')", "'-'", expect="Kept (locked): fake-video")
@mutation(FRAMES_JS, "(answer.unchanged.join(', ') || '-')", "'-'", expect="Unchanged: fake-video")
@mutation(FRAMES_JS, "{ locked: locked }", "{ locked: true }", expect="unlocked by")
@mutation(FRAMES_JS, "body: { text: null }", "body: { text: '' }", expect='to_have_text("default")')
@mutation(FRAMES_JS, "'/reset', { method", "'/resex', { method", expect="to_have_count(0)")
@mutation(FRAMES_JS, "el.classList.toggle('hidden', !(warnings && warnings.length));",
          "el.classList.toggle('hidden', true);", expect="frameWarnings")
@mutation(FRAMES_JS, "    el.classList.remove('hidden');\n}\nfunction clearRefusal", "}\nfunction clearRefusal",
          expect="frameRefusal")
@mutation(FRAMES_JS, "    el.classList.remove('hidden');\n    el.textContent = 'Applied: '",
          "    el.textContent = 'Applied: '", expect="frameApplied")
@mutation(FRAMES_JS, "    pre.classList.remove('hidden');\n    pre.textContent = answer.text;",
          "    pre.textContent = answer.text;", expect="framePreviewText")
@mutation(FRAMES_JS, "    document.getElementById('framePreviewText').classList.add('hidden');\n", "",
          expect="expect(shown).to_be_hidden()")
@mutation(FRAMES_JS, "(keep && keep.key === targetKey(view)) ? keep.draft : loaded", "loaded",
          expect="expect(text).to_have_value(mine)")
@mutation(FRAMES_JS, "save.disabled = framesView.busy || framesView.loading > 0 || text.value === framesView.loaded;",
          "save.disabled = framesView.busy || framesView.loading > 0;", expect="expect(save).to_be_disabled()")
@mutation(FRAMES_JS, "esc(v.text === null ? '(follows the default)' : v.text)",
          "(v.text === null ? '(follows the default)' : v.text)", expect="planted markup became an element")
@mutation(JOBS_JS, "textContent = job.sent_text === null ? '-' : job.sent_text;",
          "innerHTML = job.sent_text === null ? '-' : job.sent_text;", expect="planted markup became an element")
def test_the_frames_tab_saves_refuses_previews_rolls_back_follows_resets_locks_applies_and_keeps_markup_as_text(
        page, live, db, framed):
    DEFAULT = FAKE_FRAMES.defaults["video"]
    put(db, "video", None, DEFAULT)
    put(db, "image", None, FAKE_FRAMES.defaults["image"])
    k = new_key(db)
    new_account(db, label="a", checked=True)
    sign_in(page, live)
    open_tab(page, "frames")
    video = page.locator(VIDEO)
    row = video.locator("tbody tr", has_text="fake-video")
    source = row.locator('td[data-col="source"]')
    text = page.locator("#frameText")
    expect(source).to_have_text("default")
    row.get_by_role("button", name="Edit").click()
    expect(text).to_have_value(DEFAULT)
    page.evaluate("window.__canary = 1", isolated_context=False)
    assert page.evaluate("window.__canary", isolated_context=False) == 1, "the main world is readable here"
    save = page.get_by_role("button", name="Save", exact=True)
    expect(page.locator("#frameFollowing")).to_be_visible()          # saving gives this model its own frame
    expect(save).to_be_disabled()
    assert save.is_disabled(), "an untouched draft cannot be saved"

    # a save with a warning: saved, and the warning shown
    first = "Own words. {{tracking_code}} {{client_prompt}}"
    text.fill(first)
    expect(save).to_be_enabled()
    save.click()
    expect(page.locator("#frameWarnings")).to_be_visible()
    expect(page.locator("#frameWarnings")).to_contain_text("asks for no single result")
    expect(source).to_have_text("own")
    assert frame_texts(db, "fake-video") == [first]

    # a refused save shows its param and stores nothing
    text.fill("no placeholders at all")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#frameRefusal")).to_be_visible()
    expect(page.locator("#frameRefusal")).to_have_text(re.compile(r"\(\{\{(client_prompt|tracking_code)\}\}\)$"))
    assert frame_texts(db, "fake-video") == [first]
    page.problems.clear()                               # the 400 of a refused save is a console line

    # the preview renders the draft, with its length, and stores nothing
    draft = "Draft single. {{tracking_code}} {{client_prompt}}"
    text.fill(draft)
    page.get_by_role("button", name="Preview", exact=True).click()
    shown = page.locator("#framePreviewText")
    expect(shown).to_be_visible()
    expect(shown).to_contain_text("Draft single.")
    expect(shown).to_contain_text("a cat on a sofa")
    expect(page.locator("#framePreviewMeta")).to_contain_text("%d characters" % len(shown.text_content()))
    assert frame_texts(db, "fake-video") == [first]
    # a refused preview (a blank sample prompt) shows its refusal and clears the old render
    page.fill("#framePreviewPrompt", "")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.locator("#frameRefusal")).to_be_visible()
    expect(page.locator("#frameRefusal")).to_contain_text("(prompt)")
    expect(shown).to_be_hidden()
    assert not shown.is_visible(), "a refused preview clears the old render"
    page.problems.clear()
    page.fill("#framePreviewPrompt", "a cat on a sofa")

    # a second version, then a rollback to the first
    second = "Second single. {{tracking_code}} {{client_prompt}}"
    text.fill(second)
    page.get_by_role("button", name="Save", exact=True).click()
    history = page.locator("#frameHistory li")
    expect(history).to_have_count(2)
    expect(page.locator("#frameWarnings")).to_be_hidden()
    history.filter(has_text="Own words.").get_by_role("button", name="Roll back to this").click()
    expect(history).to_have_count(3)
    expect(text).to_have_value(first)
    assert frame_texts(db, "fake-video") == [first, second, first]
    assert db.one("SELECT restored_from FROM prompt_frames WHERE model = 'fake-video' ORDER BY id DESC LIMIT 1")[0]

    # follow the default again
    page.get_by_role("button", name="Follow the default").click()
    expect(source).to_have_text("default")
    assert frame_texts(db, "fake-video")[-1] is None

    # the default differs from the shipped one: the badge, then the reset
    video.get_by_role("button", name="Edit the default").click()
    expect(page.locator("#frameEditor h3")).to_have_text("video / default")
    text.fill("Changed single. {{tracking_code}} {{client_prompt}}")
    page.get_by_role("button", name="Preview", exact=True).click()      # a preview from the default's editor
    expect(shown).to_contain_text("Changed single.")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(video.get_by_text("differs from the shipped default")).to_be_visible()
    video.get_by_role("button", name="Reset to the shipped default").click()
    expect(video.get_by_text("differs from the shipped default")).to_have_count(0)
    assert frame_texts(db, None)[-1] == DEFAULT

    # a lock keeps a model's own text when a default is applied to all; unlocked, it takes it
    row.get_by_role("button", name="Edit").click()
    expect(page.locator("#frameEditor h3")).to_have_text("video / fake-video")
    mine = "Mine single. {{tracking_code}} {{client_prompt}}"
    text.fill(mine)
    row.get_by_label("keep own").check()                # a lock toggle does not touch the unsaved draft
    expect(row).to_contain_text("locked by")
    expect(text).to_have_value(mine)
    assert text.input_value() == mine, "a lock toggle keeps the draft"
    page.get_by_role("button", name="Save", exact=True).click()
    expect(source).to_have_text("own")
    video.get_by_role("button", name="Edit the default").click()
    everywhere = "All single. {{tracking_code}} {{client_prompt}}"
    text.fill(everywhere)
    page.get_by_role("button", name="Apply to all models of this kind").click()
    applied = page.locator("#frameApplied")
    expect(applied).to_be_visible()
    expect(applied).to_contain_text("Applied: -. Kept (locked): fake-video.")
    assert frame_texts(db, "fake-video")[-1] == mine
    row.get_by_label("keep own").uncheck()
    expect(row).to_contain_text("unlocked by")
    text.fill(everywhere)
    page.get_by_role("button", name="Apply to all models of this kind").click()
    expect(applied).to_contain_text("Applied: fake-video.")
    assert frame_texts(db, "fake-video")[-1] == everywhere
    text.fill(everywhere)                               # the same text again: the model already has it
    page.get_by_role("button", name="Apply to all models of this kind").click()
    expect(applied).to_contain_text("Applied: -. Kept (locked): -. Unchanged: fake-video.")

    # markup in a frame is text: in the history and in the job's detail
    row.get_by_role("button", name="Edit").click()
    expect(page.locator("#frameEditor h3")).to_have_text("video / fake-video")
    text.fill(HOSTILE)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#frameHistory li").first).to_be_visible()
    page.wait_for_timeout(300)                           # a script in the markup would have run by now
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "markup ran as a script"
    assert page.locator("#panel-frames img").count() == 0, "planted markup became an element"
    expect(page.locator("#frameHistory li").first).to_contain_text("<img src=x")
    frame_id = db.one("SELECT id FROM prompt_frames WHERE model = 'fake-video' ORDER BY id DESC LIMIT 1")[0]
    job = planted(db, lambda p: failed_job(p, k, "a cat", frame=FrameUse(frame_id, HOSTILE)))
    open_tab(page, "jobs")
    page.get_by_role("button", name="Details").click()
    technical = page.locator("#jobBox")
    expect(technical).to_contain_text("Prompt frame version")
    expect(technical.locator("dt", has_text="Prompt frame version").locator("xpath=following-sibling::dd[1]")
           ).to_have_text(str(frame_id))
    expect(page.locator("#jobSentText")).to_be_visible()
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "markup ran as a script"
    assert page.locator("#jobBox img").count() == 0, "planted markup became an element"
    expect(page.locator("#jobSentText")).to_contain_text("<img src=x onerror")
    assert db.one("SELECT sent_text FROM jobs WHERE id = %s", job)[0].startswith("<img src=x")


# ---- prompt frames: an answer belongs to the editor that asked ------------------------------------------------------
def hold(page, pattern, method):
    """Keep the first request of `method` to `pattern` in Chrome's network layer until the test releases it."""
    held = []

    def handler(route):
        if route.request.method == method and not held:
            held.append(route)
        else:
            route.continue_()

    page.route(pattern, handler)
    return held


def wait_held(page, held):
    for _ in range(250):
        if held:
            return
        page.wait_for_timeout(20)
    raise AssertionError("the request was not sent")


def settled(page):
    """Wait until the tab has no load in flight (a dropped answer shows nothing, so the page itself says when it has
    dealt with it), then a beat for what a settled load draws or toasts."""
    for _ in range(250):
        if page.evaluate("framesView.loading", isolated_context=False) == 0:
            break
        page.wait_for_timeout(20)
    page.wait_for_timeout(150)


def release(page, held):
    """Let the held request go and wait for the page to deal with the answer (or drop it)."""
    held[0].continue_()
    settled(page)
    page.wait_for_timeout(250)                         # a preview is not a load: give its answer a beat as well


def framed_page(page, live, db):
    """A signed-in frames tab: video default, one own frame for fake-video, an image default."""
    put(db, "video", None, FAKE_FRAMES.defaults["video"])
    put(db, "image", None, FAKE_FRAMES.defaults["image"])
    put(db, "video", "fake-video", "Own of A single. {{tracking_code}} {{client_prompt}}")
    sign_in(page, live)
    open_tab(page, "frames")
    return page.locator(VIDEO), page.locator("#frameText"), page.locator("#frameEditor h3")


@mutation(FRAMES_JS, "        if (g !== framesView.gen) return null;\n        draw(answer);", "        draw(answer);",
          expect='to_have_text("video / default")')
def test_a_late_answer_for_an_editor_that_is_no_longer_open_is_dropped_and_save_writes_the_editor_shown(
        page, live, db, framed):
    video, text, heading = framed_page(page, live, db)
    held = hold(page, "**/admin/frames/fake/video/fake-video", "GET")
    video.locator("tbody tr", has_text="fake-video").get_by_role("button", name="Edit").click()
    wait_held(page, held)
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    expect(text).to_have_value(FAKE_FRAMES.defaults["video"])
    release(page, held)
    expect(heading).to_have_text("video / default")
    expect(text).to_have_value(FAKE_FRAMES.defaults["video"])
    typed = "Written for the default single. {{tracking_code}} {{client_prompt}}"
    text.fill(typed)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved: applies to jobs created from now on")).to_be_visible()
    assert frame_texts(db, None)[-1] == typed, "the save went to the editor that was shown"
    assert frame_texts(db, "fake-video") == ["Own of A single. {{tracking_code}} {{client_prompt}}"]


@mutation(FRAMES_JS, "if (text) text.readOnly = writing || loading;", "if (text) text.readOnly = loading;",
          expect='to_have_js_property("readOnly", True)')
@mutation(FRAMES_JS, "el.disabled = writing || (loading && !opens);", "el.disabled = loading && !opens;",
          expect="to_be_disabled()")
def test_while_a_save_is_in_flight_the_draft_cannot_be_typed_over_and_nothing_else_can_be_clicked(
        page, live, db, framed):
    video, text, heading = framed_page(page, live, db)
    held = hold(page, "**/admin/frames/fake/video/default", "PUT")
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    typed = "Typed before the save single. {{tracking_code}} {{client_prompt}}"
    text.fill(typed)
    page.get_by_role("button", name="Save", exact=True).click()
    wait_held(page, held)
    expect(text).to_have_js_property("readOnly", True)
    expect(video.locator("tbody tr", has_text="fake-video").get_by_role("button", name="Edit")).to_be_disabled()
    expect(video.get_by_role("button", name="Edit the default")).to_be_disabled()
    held[0].continue_()
    expect(page.get_by_text("Saved: applies to jobs created from now on")).to_be_visible()
    expect(text).to_have_js_property("readOnly", False)
    expect(text).to_have_value(typed)
    assert frame_texts(db, None)[-1] == typed
    # a lock toggle cannot be repeated while its request runs
    held = hold(page, "**/admin/frames/fake/video/fake-video/lock", "PUT")
    box = video.locator("tbody tr", has_text="fake-video").get_by_label("keep own")
    box.check()
    wait_held(page, held)
    expect(box).to_be_disabled()
    held[0].continue_()
    expect(box).to_be_enabled()
    expect(box).to_be_checked()


@mutation(FRAMES_JS, "if (!answer || !stillAsked()) return;", "if (!answer) return;",
          expect='expect(page.locator("#framePreviewText")).to_be_hidden()')
@mutation(FRAMES_JS, "if (g === framesView.gen && (!stillAsked || stillAsked())) showRefusal(err);",
          "showRefusal(err);", expect='expect(page.locator("#frameRefusal")).to_be_hidden()')
def test_a_preview_answer_or_refusal_for_another_editor_or_draft_is_not_shown_in_this_one(page, live, db, framed):
    video, text, heading = framed_page(page, live, db)
    row = video.locator("tbody tr", has_text="fake-video")
    preview = page.get_by_role("button", name="Preview", exact=True)
    # a good preview of the default's draft, held while the operator opens A
    held = hold(page, "**/admin/frames/preview", "POST")
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    text.fill("Draft of the default single. {{tracking_code}} {{client_prompt}}")
    preview.click()
    wait_held(page, held)
    row.get_by_role("button", name="Edit").click()
    expect(heading).to_have_text("video / fake-video")
    release(page, held)
    expect(page.locator("#framePreviewText")).to_be_hidden()
    expect(page.locator("#framePreviewMeta")).to_have_text("")
    # a refused preview of A's bad draft, held while the operator opens the default
    page.unroute("**/admin/frames/preview")
    held = hold(page, "**/admin/frames/preview", "POST")
    text.fill("no placeholders at all")
    preview.click()
    wait_held(page, held)
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    release(page, held)
    expect(page.locator("#frameRefusal")).to_be_hidden()
    page.problems.clear()                               # the 400 of a refused preview is a console line
    # the same draft still gets its own answer
    text.fill("Draft again single. {{tracking_code}} {{client_prompt}}")
    preview.click()
    expect(page.locator("#framePreviewText")).to_contain_text("Draft again single.")


@mutation(FRAMES_JS, "el.disabled = writing || (loading && !opens);", "el.disabled = writing;",
          expect='name="Apply to all models of this kind")).to_be_disabled()')
@mutation(FRAMES_JS, "save.disabled = framesView.busy || framesView.loading > 0 || text.value === framesView.loaded;",
          "save.disabled = framesView.busy || text.value === framesView.loaded;",
          expect="expect(save).to_be_disabled()")
def test_while_another_editor_loads_the_old_one_cannot_save_reset_or_apply_to_all(page, live, db, framed):
    put(db, "video", None, "Changed single. {{tracking_code}} {{client_prompt}}")     # differs from the shipped default
    put(db, "image", None, FAKE_FRAMES.defaults["image"])
    put(db, "video", "fake-video", "Own of A single. {{tracking_code}} {{client_prompt}}")
    sign_in(page, live)
    open_tab(page, "frames")
    video, text, heading = page.locator(VIDEO), page.locator("#frameText"), page.locator("#frameEditor h3")
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    text.fill("Edited default single. {{tracking_code}} {{client_prompt}}")
    save = page.get_by_role("button", name="Save", exact=True)
    expect(save).to_be_enabled()
    held = hold(page, "**/admin/frames/fake/video/fake-video", "GET")
    video.locator("tbody tr", has_text="fake-video").get_by_role("button", name="Edit").click()
    wait_held(page, held)
    expect(save).to_be_disabled()
    expect(page.get_by_role("button", name="Apply to all models of this kind")).to_be_disabled()
    expect(video.get_by_role("button", name="Reset to the shipped default")).to_be_disabled()
    expect(video.locator("tbody tr", has_text="fake-video").get_by_label("keep own")).to_be_disabled()
    expect(text).to_have_js_property("readOnly", True)
    expect(video.get_by_role("button", name="Edit the default")).to_be_enabled()      # how a view is replaced
    held[0].continue_()
    expect(heading).to_have_text("video / fake-video")
    expect(video.get_by_role("button", name="Reset to the shipped default")).to_be_enabled()
    expect(text).to_have_js_property("readOnly", False)


@mutation(FRAMES_JS, "finally { framesView.busy = false; applyBusy(); }", "finally { applyBusy(); }",
          expect='to_have_js_property("readOnly", False)')
def test_a_save_that_fails_with_a_server_error_shows_it_and_gives_the_controls_back(page, live, db, framed):
    video, text, heading = framed_page(page, live, db)
    held = hold(page, "**/admin/frames/fake/video/default", "PUT")
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    typed = "Typed before the failure single. {{tracking_code}} {{client_prompt}}"
    text.fill(typed)
    page.get_by_role("button", name="Save", exact=True).click()
    wait_held(page, held)
    expect(text).to_have_js_property("readOnly", True)
    held[0].fulfill(status=500, content_type="application/json", body=json.dumps(
        {"error": {"code": "internal.unexpected", "message": "an internal error occurred", "param": None}}))
    expect(page.get_by_text("an internal error occurred")).to_be_visible()
    expect(text).to_have_js_property("readOnly", False)
    expect(text).to_have_value(typed)                    # the draft is still there
    expect(page.get_by_role("button", name="Save", exact=True)).to_be_enabled()
    expect(video.get_by_role("button", name="Edit the default")).to_be_enabled()
    page.problems.clear()                               # a 500 is a console line
    assert frame_texts(db, None)[-1] == FAKE_FRAMES.defaults["video"], "nothing was saved"


@mutation(FRAMES_JS, "catch (err) { if (g !== framesView.gen) return null; throw err; }", "catch (err) { throw err; }",
          expect="a stale failure showed a toast")
def test_a_failed_answer_for_an_editor_that_is_no_longer_open_shows_nothing(page, live, db, framed):
    video, text, heading = framed_page(page, live, db)
    held = hold(page, "**/admin/frames/fake/video/fake-video", "GET")
    video.locator("tbody tr", has_text="fake-video").get_by_role("button", name="Edit").click()
    wait_held(page, held)
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("video / default")
    held[0].fulfill(status=500, content_type="application/json", body=json.dumps(
        {"error": {"code": "internal.unexpected", "message": "an internal error occurred", "param": None}}))
    settled(page)
    # not expect(...).to_have_count(0): that waits for the toast to fade, and passes
    assert page.get_by_text("an internal error occurred").count() == 0, "a stale failure showed a toast"
    expect(heading).to_have_text("video / default")
    page.problems.clear()                               # a 500 is a console line


# ---- alerts hub ----------------------------------------------------------------------------------------------------
ALERTS_JS = "packages/relay-core/src/relay_core/admin/static/tab-alerts.js"
HOSTILE_REPLY = 'Which style? <img src=x onerror="window.__pwned=1">'
PLAIN_REPLY = "Plain wording of one key"
SERVER_ERROR = {"error": {"code": "internal.unexpected", "message": "an internal error occurred", "param": None}}
ONE_CUSTOMER = "the one-customer mark is for a wording seen under one key only"
NOT_A_WORDING = "the API read failure is not a wording seen by one customer"


def group_row(page, text):
    return page.locator("#alertsTable tbody tr", has_text=text)


def alert_rows(db, group):
    return [r[0] for r in db.all("SELECT id FROM unclear_replies WHERE group_id = %s ORDER BY id", group)]


def alerts_planted(db):
    """A clamp and a drift (one wallet), a hostile wording under two keys (a delivered, a refunded and a still open
    job), a plain wording under one key, the empty wording, an acknowledged wording, and one account (a live
    condition)."""
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


def alerts_page(page, live, db):
    keys = alerts_planted(db)
    sign_in(page, live)
    open_tab(page, "alerts")
    expect(page.locator("#alertsTable tbody tr")).to_have_count(5)
    return keys


@mutation(ALERTS_JS, "through_id: g.newest_id", "through_id: 9007199254740991",
          expect="Acknowledged 3 row(s)")
@mutation(ALERTS_JS, "esc(groupTitle(g))", "groupTitle(g)", expect="planted markup became an element in the list")
@mutation(ALERTS_JS, "esc(i.text === '' ? '(no text)' : i.text)", "(i.text === '' ? '(no text)' : i.text)",
          expect="planted markup became an element in the detail")
@mutation(ALERTS_JS, "g.kind === 'unclear_reply' && g.keys === 1", "g.keys === 1", expect=ONE_CUSTOMER)
@mutation(ALERTS_JS, " && !isEmptyWording(g)\n", "\n", expect=NOT_A_WORDING)
@mutation(ALERTS_JS, "g.kind === 'unclear_reply' && g.title === EMPTY_WORDING_TITLE", "false",
          expect="API read failure")
@mutation(ALERTS_JS, "(at.kind ? '&kind=' + encodeURIComponent(at.kind) : '')", "''",
          expect="to_have_count(1, timeout=3000)")
@mutation(ALERTS_JS, "'?status=' + encodeURIComponent(at.status)", "'?status=open'",
          expect='expect(group_row(page, "seen and acknowledged"))')
@mutation(ALERTS_JS, "live: stats.alerts", "live: []", expect='expect(page.locator("#alertsLiveList"))')
@mutation(ALERTS_JS, "    await refreshStats();", "    void 0;", expect='to_contain_text("4 open alert group(s)")')
def test_the_alerts_tab_lists_every_kind_with_a_live_condition_filters_shows_details_and_acknowledges_what_it_showed(
        page, live, db):
    k1, _k2 = alerts_page(page, live, db)
    hostile, plain = group_of(HOSTILE_REPLY), group_of(PLAIN_REPLY)
    assert headers(page, "alerts") == cells(page, "alerts")
    expect(page.locator("#alertsTable")).to_contain_text("clamp: fake")
    expect(page.locator("#alertsTable")).to_contain_text("drift: fake")
    expect(page.locator("#alertsLiveList")).to_contain_text("accounts_low")
    assert page.locator("#alertsLive button").count() == 0, "a live condition has nothing to acknowledge"
    expect(page.locator("#alertsOpenGroups")).to_contain_text("5 open alert group(s)")
    expect(page.locator("#alertsMore")).to_be_hidden()
    page.wait_for_timeout(300)                           # a script in the planted markup would have run by now
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "markup ran as a script"
    assert page.locator("#panel-alerts img").count() == 0, "planted markup became an element in the list"
    hostile_row, plain_row = group_row(page, "which style?"), group_row(page, "plain wording")
    expect(hostile_row).to_contain_text("<img src=x")
    expect(hostile_row.locator('td[data-col="keys"]')).to_have_text("2")
    expect(plain_row.locator('td[data-col="keys"]')).to_have_text("1")
    expect(plain_row).to_contain_text("one customer only: may be the customer's own words")
    for other in (hostile_row, group_row(page, "clamp: fake"), group_row(page, "drift: fake")):
        assert "one customer only" not in other.inner_text(), ONE_CUSTOMER
    assert page.locator("#panel-alerts input, #panel-alerts textarea").count() == 0, "there is no phrase field"
    expect(page.locator("#panel-alerts")).to_contain_text("new wording is a code change")

    # the filters
    page.select_option("#alertsStatus", "acknowledged")
    expect(group_row(page, "seen and acknowledged")).to_be_visible()
    expect(page.locator("#alertsTable tbody tr")).to_have_count(1)
    page.select_option("#alertsStatus", "all")
    expect(page.locator("#alertsTable tbody tr")).to_have_count(6)
    page.select_option("#alertsKind", "drift")
    expect(page.locator("#alertsTable tbody tr")).to_have_count(1, timeout=3000)
    page.select_option("#alertsKind", "")
    page.select_option("#alertsStatus", "open")
    expect(page.locator("#alertsTable tbody tr")).to_have_count(5)

    # the detail of the hostile wording: outcomes, failure codes, a job link, its texts as text
    posted = []
    page.on("request", lambda r: posted.append(r) if r.method == "POST" and "/acknowledge" in r.url else None)
    hostile_row.get_by_role("button", name="Details").click()
    detail = page.locator("#alertsDetail")
    expect(page.locator("#alertDetailTitle")).to_contain_text("<img src=x")
    expect(page.locator("#alertOutcomes")).to_contain_text(
        "delivered 1, refunded 1, still open 1 (refused with: render.deadline x1)")
    expect(detail.locator("tbody tr")).to_have_count(3)
    page.wait_for_timeout(300)                           # the raw reply is in the detail now: a script would have run
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "markup ran as a script"
    assert page.locator("#alertsDetail img").count() == 0, "planted markup became an element in the detail"
    detail.locator("tbody tr", has_text="render.deadline").get_by_role("button").click()
    expect(page.locator("#jobBox")).to_contain_text("render.deadline")
    page.locator("#modalRoot").get_by_role("button", name="Close").click()

    # acknowledge from the detail sends the newest row it showed; a row that arrives later keeps the group open
    seen = max(alert_rows(db, hostile))
    late = plant_unclear(db, k1, HOSTILE_REPLY)
    detail.get_by_role("button", name="Acknowledge").click()
    expect(page.get_by_text("Acknowledged 3 row(s)")).to_be_visible()
    assert posted[0].post_data_json == {"through_id": seen}, "acknowledge sends the newest row the page showed"
    assert db.one("SELECT acknowledged_at FROM unclear_replies WHERE id = %s", late)[0] is None, \
        "a row that arrived after the page loaded must leave the group open"
    expect(hostile_row.locator('td[data-col="open"]')).to_have_text("1")
    expect(detail.locator("tbody tr")).to_have_count(4)

    # the empty wording is an API read failure; a billing group shows its rows; an acknowledge from the list
    assert "one customer only" not in group_row(page, "API read failure").inner_text(), NOT_A_WORDING
    group_row(page, "API read failure").get_by_role("button", name="Details").click()
    expect(page.locator("#alertReadFailure")).to_be_visible()
    assert "one customer only" not in detail.inner_text(), NOT_A_WORDING
    expect(page.locator("#alertDetailTitle")).to_contain_text("API read failure")
    group_row(page, "clamp: fake").get_by_role("button", name="Details").click()
    expect(detail.locator("tbody tr")).to_have_count(1)
    expect(detail.locator("tbody tr")).to_contain_text("clamp")
    detail.get_by_role("button", name="Close").click()
    expect(detail).to_be_empty()
    plain_row.get_by_role("button", name="Acknowledge").click()
    expect(page.locator("#alertsTable tbody tr")).to_have_count(4)
    assert posted[1].post_data_json == {"through_id": max(alert_rows(db, plain))}
    expect(page.locator("#alertsOpenGroups")).to_contain_text("4 open alert group(s)")


@mutation(STATS, "(open ? '<li id=\"alertsOpenGroups\">'", "(false ? '<li id=\"alertsOpenGroups\">'",
          expect='expect(page.locator("#alertsOpenGroups"))')
def test_the_banner_counts_open_alert_groups_and_a_click_opens_the_alerts_tab(page, live, db):
    for n in range(3):
        new_account(db, label="a%d" % n, checked=True)                  # no live condition: only groups can show it
    sign_in(page, live)
    expect(page.locator("#alertsBanner")).to_be_hidden()
    plant(db)                                                           # a clamp and a drift
    page.reload()
    expect(page.locator("#alertsOpenGroups")).to_contain_text("2 open alert group(s)")
    page.get_by_role("button", name="Open the Alerts tab").click()
    expect(page.locator("#panel-alerts")).to_be_visible()
    expect(page.locator("#alertsTable tbody tr")).to_have_count(2)


@mutation(ALERTS_JS, "        if (g !== alertsView.gen) return null;\n        draw(answer);", "        draw(answer);",
          expect='expect(page.locator("#alertsDetail h3"))')
def test_a_late_detail_for_a_group_that_is_no_longer_asked_for_is_dropped(page, live, db):
    alerts_page(page, live, db)
    held = hold(page, "**/admin/alerts/groups/unclear_reply/%s*" % group_of(HOSTILE_REPLY), "GET")
    group_row(page, "which style?").get_by_role("button", name="Details").click()
    wait_held(page, held)
    group_row(page, "plain wording").get_by_role("button", name="Details").click()
    expect(page.locator("#alertDetailTitle")).to_contain_text("plain wording")
    held[0].continue_()
    alerts_idle(page)
    expect(page.locator("#alertsDetail h3")).to_contain_text("plain wording")
    assert page.locator("#alertsDetail tbody tr").count() == 1, "the late detail was drawn"


@mutation(ALERTS_JS, "catch (err) { if (g !== alertsView.gen) return null; throw err; }", "catch (err) { throw err; }",
          expect="a stale failure showed a toast")
def test_a_failed_answer_for_a_group_that_is_no_longer_asked_for_shows_nothing(page, live, db):
    alerts_page(page, live, db)
    held = hold_nth(page, GROUPS_GET, 1, early=False)           # the list of the first load fails after the second drew
    group_row(page, "which style?").get_by_role("button", name="Details").click()
    wait_held(page, held)
    group_row(page, "plain wording").get_by_role("button", name="Details").click()
    expect(page.locator("#alertDetailTitle")).to_contain_text("plain wording")
    let_go(held, failing=True)
    alerts_idle(page)
    assert page.get_by_text("an internal error occurred").count() == 0, "a stale failure showed a toast"
    page.problems.clear()                               # a 500 is a console line


@mutation(ALERTS_JS, "el.disabled = writing || (loading && !opens);", "el.disabled = writing;",
          expect="expect(each).to_be_disabled()")
def test_while_a_group_loads_nothing_can_be_acknowledged_and_only_views_can_be_asked_for(page, live, db):
    alerts_page(page, live, db)
    held = hold(page, "**/admin/alerts/groups/unclear_reply/%s*" % group_of(HOSTILE_REPLY), "GET")
    group_row(page, "which style?").get_by_role("button", name="Details").click()
    wait_held(page, held)
    for each in page.locator("#alertsTable").get_by_role("button", name="Acknowledge").all():
        expect(each).to_be_disabled()
    expect(group_row(page, "plain wording").get_by_role("button", name="Details")).to_be_enabled()
    expect(page.locator("#alertsStatus")).to_be_enabled()
    held[0].continue_()
    expect(page.locator("#alertDetailTitle")).to_contain_text("<img src=x")
    expect(group_row(page, "plain wording").get_by_role("button", name="Acknowledge")).to_be_enabled()


@mutation(ALERTS_JS, "const writing = alertsView.busy,", "const writing = false,",
          expect='expect(page.locator("#alertsStatus")).to_be_disabled()')
@mutation(ALERTS_JS, "finally { alertsView.busy = false; applyAlertsBusy(); }", "finally { applyAlertsBusy(); }",
          expect="Locator expected to be enabled")
def test_while_an_acknowledge_is_in_flight_nothing_else_can_be_clicked_and_a_failure_gives_the_controls_back(
        page, live, db):
    alerts_page(page, live, db)
    held = hold(page, "**/admin/alerts/groups/**/acknowledge", "POST")
    plain = group_row(page, "plain wording")
    plain.get_by_role("button", name="Acknowledge").click()
    wait_held(page, held)
    expect(page.locator("#alertsStatus")).to_be_disabled()
    expect(plain.get_by_role("button", name="Details")).to_be_disabled()
    expect(group_row(page, "which style?").get_by_role("button", name="Acknowledge")).to_be_disabled()
    held[0].fulfill(status=500, content_type="application/json", body=json.dumps(SERVER_ERROR))
    expect(page.get_by_text("an internal error occurred")).to_be_visible()
    expect(plain.get_by_role("button", name="Acknowledge")).to_be_enabled()
    expect(page.locator("#alertsStatus")).to_be_enabled()
    page.problems.clear()                               # a 500 is a console line
    assert db.one("SELECT count(*) FROM unclear_replies WHERE acknowledged_at IS NOT NULL")[0] == 1, "nothing stamped"


# ---- alerts hub: only the newest load, and the newest banner refresh, draws -----------------------------------------
GROUPS_GET = re.compile(r".*/admin/alerts/groups\?status=open&limit=200")


def hold_nth(page, pattern, nth, early):
    """Keep the `nth` GET of `pattern`. `early`: it is sent at once and only its answer is kept, so the answer shows the
    data of now (a slow server). Returns the list that holds (route, answer) once the request is caught."""
    held, seen = [], []

    def handler(route):
        if route.request.method == "GET":
            seen.append(1)
            if len(seen) == nth and not held:
                held.append((route, route.fetch() if early else None))
                return
        route.continue_()

    page.route(pattern, handler)
    return held


def let_go(held, failing=False):
    route, answer = held[0]
    if failing:
        route.fulfill(status=500, content_type="application/json", body=json.dumps(SERVER_ERROR))
    elif answer is not None:
        route.fulfill(response=answer)
    else:
        route.continue_()


def alerts_idle(page):
    for _ in range(250):
        if page.evaluate("alertsView.loading", isolated_context=False) == 0:
            break
        page.wait_for_timeout(20)
    page.wait_for_timeout(250)


def older_load_after_an_acknowledge(page, live, db, failing):
    """An acknowledge is in flight; the operator leaves the tab and comes back (a load starts and its answer is slow);
    the acknowledge finishes and reloads; then the slow older answer arrives."""
    alerts_page(page, live, db)
    posted = hold(page, "**/admin/alerts/groups/**/acknowledge", "POST")
    plain = group_row(page, "plain wording")
    plain.get_by_role("button", name="Acknowledge").click()
    wait_held(page, posted)
    open_tab(page, "accounts")
    page.wait_for_timeout(300)
    late = hold_nth(page, GROUPS_GET, 2, early=not failing)       # the banner asks first, then the tab's own load
    open_tab(page, "alerts")
    wait_held(page, late)
    posted[0].continue_()
    expect(page.get_by_text("Acknowledged 1 row(s)")).to_be_visible()
    expect(plain).to_have_count(0)
    let_go(late, failing)
    alerts_idle(page)
    return plain


@mutation(ALERTS_JS, "const g = ++alertsView.gen;", "const g = alertsView.gen;",
          expect="the older load redrew the acknowledged group as open")
def test_an_older_load_answering_after_an_acknowledge_does_not_draw_the_group_as_open_again(page, live, db):
    plain = older_load_after_an_acknowledge(page, live, db, failing=False)
    assert plain.count() == 0, "the older load redrew the acknowledged group as open"


@mutation(ALERTS_JS, "const g = ++alertsView.gen;", "const g = alertsView.gen;",
          expect="a stale load failure showed a toast")
def test_an_older_load_failing_after_an_acknowledge_shows_nothing(page, live, db):
    older_load_after_an_acknowledge(page, live, db, failing=True)
    assert page.get_by_text("an internal error occurred").count() == 0, "a stale load failure showed a toast"
    page.problems.clear()                               # a 500 is a console line


@mutation(STATS, "    }\n    if (mine !== statsAsked) return;\n    const open", "    }\n    const open",
          expect="the older banner refresh brought back the old count")
def test_a_slow_older_banner_refresh_does_not_bring_back_the_count_from_before_an_acknowledge(page, live, db):
    alerts_page(page, live, db)
    expect(page.locator("#alertsOpenGroups")).to_contain_text("5 open alert group(s)")
    older = hold_nth(page, GROUPS_GET, 1, early=True)             # asked now, answered after the acknowledge
    page.evaluate("() => { refreshStats(); }", isolated_context=False)
    wait_held(page, older)
    group_row(page, "plain wording").get_by_role("button", name="Acknowledge").click()
    expect(page.locator("#alertsOpenGroups")).to_contain_text("4 open alert group(s)")
    let_go(older)
    page.wait_for_timeout(500)
    assert "4 open alert group(s)" in page.locator("#alertsOpenGroups").inner_text(), \
        "the older banner refresh brought back the old count"


@mutation(STATS, "    } catch (err) {\n        if (mine !== statsAsked) return;\n        throw err;",
          "    } catch (err) {\n        throw err;", expect="a stale banner failure showed a toast")
def test_a_slow_older_banner_refresh_that_fails_shows_nothing(page, live, db):
    alerts_page(page, live, db)
    older = hold_nth(page, GROUPS_GET, 1, early=False)
    page.evaluate("() => { runBusy(null, refreshStats); }", isolated_context=False)   # as a tab switch does
    wait_held(page, older)
    group_row(page, "plain wording").get_by_role("button", name="Acknowledge").click()
    expect(page.locator("#alertsOpenGroups")).to_contain_text("4 open alert group(s)")
    let_go(older, failing=True)
    page.wait_for_timeout(500)
    assert page.get_by_text("an internal error occurred").count() == 0, "a stale banner failure showed a toast"
    page.problems.clear()                               # a 500 is a console line


# ---- alerts hub: closing, a failed detail, a failed reload, full pages ----------------------------------------------
@mutation(ALERTS_JS, "    document.getElementById('alertsDetail').innerHTML = '';\n    await loadAlerts();\n};",
          "    document.getElementById('alertsDetail').innerHTML = '';\n};", expect="Close left the detail open")
def test_closing_a_detail_while_a_filter_loads_leaves_a_table_that_matches_the_filter(page, live, db):
    alerts_page(page, live, db)
    group_row(page, "which style?").get_by_role("button", name="Details").click()
    expect(page.locator("#alertDetailTitle")).to_contain_text("<img src=x")
    held = hold_nth(page, re.compile(r".*/admin/alerts/groups\?status=open&kind=drift&limit=200"), 1, early=False)
    page.select_option("#alertsKind", "drift")
    wait_held(page, held)
    page.locator("#alertsDetail").get_by_role("button", name="Close").click()
    let_go(held)
    alerts_idle(page)
    assert page.locator("#alertsDetail").inner_text() == "", "Close left the detail open"
    expect(page.locator("#alertsTable tbody tr")).to_have_count(1)
    assert page.locator("#alertsKind").input_value() == "drift"


@mutation(ALERTS_JS, "        alertsView.asked = Object.assign({}, alertsView.asked, { open: null });\n        toast(",
          "        toast(", expect="the failed detail was asked for again")
def test_a_detail_that_cannot_be_read_is_closed_and_the_list_is_drawn(page, live, db):
    alerts_page(page, live, db)

    def refuse(route):
        if route.request.method == "GET":
            route.fulfill(status=500, content_type="application/json", body=json.dumps(SERVER_ERROR))
        else:
            route.continue_()

    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/admin/alerts/groups/unclear_reply/" in r.url else None)
    page.route(re.compile(r".*/admin/alerts/groups/unclear_reply/.*"), refuse)
    group_row(page, "plain wording").get_by_role("button", name="Details").click()
    expect(page.get_by_text("an internal error occurred")).to_be_visible()
    alerts_idle(page)
    assert page.locator("#alertsDetail").inner_text() == ""
    expect(page.locator("#alertsTable tbody tr")).to_have_count(5)
    page.select_option("#alertsKind", "drift")             # the failed detail is not asked for again
    alerts_idle(page)
    assert page.locator("#alertsTable tbody tr").count() == 1
    assert len(asked) == 1, "the failed detail was asked for again"
    page.problems.clear()                               # a 500 is a console line


@mutation(ALERTS_JS, "try { await loadAlerts(); } catch (err) { failure = err; }", "await loadAlerts();",
          expect="but the list could not be reloaded")
def test_an_acknowledge_that_is_stored_says_so_when_the_list_cannot_be_reloaded(page, live, db):
    alerts_page(page, live, db)
    held = hold_nth(page, GROUPS_GET, 1, early=False)
    group_row(page, "plain wording").get_by_role("button", name="Acknowledge").click()
    wait_held(page, held)
    let_go(held, failing=True)
    expect(page.get_by_text("Acknowledged 1 row(s), but the list could not be reloaded")).to_be_visible()
    assert db.one("SELECT count(*) FROM unclear_replies WHERE acknowledged_at IS NOT NULL")[0] == 2
    alerts_idle(page)
    expect(page.get_by_role("button", name="Acknowledge").first).to_be_enabled()
    page.problems.clear()                               # a 500 is a console line


@mutation(ALERTS_JS, "(d.items.length >= ROW_LIMIT ?", "(false ?", expect="says that older rows exist")
@mutation(ALERTS_JS, "classList.toggle('hidden', view.groups.length < GROUP_LIMIT)", "classList.toggle('hidden', true)",
          expect="says that older groups exist")
def test_a_full_page_of_groups_or_rows_says_that_older_ones_are_not_listed(page, live, db):
    k1, _k2 = alerts_planted(db)
    for _ in range(100):
        plant_unclear(db, k1, "A wording with a hundred rows")

    def full_page(route):
        if route.request.method != "GET":
            route.continue_()
            return
        items = route.fetch().json()["items"]
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"items": items + [items[0]] * (200 - len(items))}))

    page.route(GROUPS_GET, full_page)
    sign_in(page, live)
    open_tab(page, "alerts")
    expect(page.locator("#alertsTable tbody tr")).to_have_count(200)
    assert page.locator("#alertsMore").is_visible(), "says that older groups exist"
    expect(page.locator("#alertsMore")).to_contain_text("Showing the newest 200 groups")
    expect(page.locator("#alertsOpenGroups")).to_contain_text("200+ open alert group(s)")
    group_row(page, "a wording with a hundred rows").first.get_by_role("button", name="Details").click()
    expect(page.locator("#alertDetailTitle")).to_contain_text("a wording with a hundred rows")
    expect(page.locator("#alertsDetail tbody tr")).to_have_count(100)
    assert page.locator("#alertDetailMore").is_visible(), "says that older rows exist"
    expect(page.locator("#alertDetailMore")).to_contain_text("Showing the newest 100 rows")
    group_row(page, "which style?").first.get_by_role("button", name="Details").click()
    expect(page.locator("#alertsDetail tbody tr")).to_have_count(3)
    assert page.locator("#alertDetailMore").count() == 0
