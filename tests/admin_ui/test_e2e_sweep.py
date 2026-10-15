"""The whole-app CSP sweep of the Vue admin, in both themes, against the real app (browser tier). One signed-in session
walks the login page, all eight tabs and the job detail, and opens every dialog, confirm box, toast, select popper,
dropdown, date field and loading state they have, under the CSP watch of `conftest.CspWatch`: a violation on either
channel fails the sweep. `OPENS` is the one explicit list of what it opens; a control that cannot be found fails the
step (nothing is caught), and `finish()` fails when a listed state was not reached, so a renamed button cannot
silently shrink the sweep.

With RELAY_SCREENSHOTS_DIR set, every opened state is also saved as a full-page `<theme>-<route>-<state>.png` for the
design review; with it unset nothing is written."""

import os
import re
from pathlib import Path

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from test_admin_alert_groups import plant_unclear
from test_admin_alerts import plant
from test_admin_jobs import failed_job

from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

THEMES = ("light", "dark")
SHOTS_ENV = "RELAY_SCREENSHOTS_DIR"
TIMEOUT_MS = 10_000
TABS = {"jobs": "Jobs", "accounts": "Accounts", "keys": "API keys", "logs": "Request logs", "alerts": "Alerts",
        "frames": "Prompt frames", "settings": "Settings", "test": "Test"}

# Everything the sweep opens, as (route, state), in the order it opens them.
OPENS = [
    ("login", "form"), ("login", "busy-button"),
    ("jobs", "loading-skeleton"), ("jobs", "list"), ("jobs", "refresh-busy"), ("jobs", "status-select"),
    ("jobs", "key-select"), ("jobs", "bulk-selected"), ("jobs", "regenerate-confirm"), ("jobs", "recheck-toast"),
    ("jobs", "detail-loading"), ("jobs", "detail"), ("jobs", "detail-regenerate-confirm"),
    ("accounts", "loading-skeleton"), ("accounts", "list"), ("accounts", "more-menu"), ("accounts", "edit-dialog"),
    ("accounts", "delete-confirm"), ("accounts", "import-dialog"), ("accounts", "import-select"),
    ("accounts", "import-toast"),
    ("keys", "loading-skeleton"), ("keys", "list"), ("keys", "new-dialog"), ("keys", "wallet-select"),
    ("keys", "one-time-callout"), ("keys", "edit-dialog"), ("keys", "delete-confirm"),
    ("logs", "loading-skeleton"), ("logs", "list"), ("logs", "status-select"), ("logs", "date-fields"),
    ("logs", "detail-dialog"), ("logs", "clear-confirm"),
    ("alerts", "loading-skeleton"), ("alerts", "list"), ("alerts", "status-select"), ("alerts", "kind-select"),
    ("alerts", "detail-panel"),
    ("frames", "list"), ("frames", "provider-select"), ("frames", "editor"),
    ("settings", "loading-skeleton"), ("settings", "list"), ("settings", "edited-row"), ("settings", "saved-toast"),
    ("settings", "error-toast"), ("settings", "password-form"),
    ("test", "form"), ("test", "load-pending"), ("test", "models-loaded"),
    ("shell", "scope-select"), ("shell", "multiple-select-tick"),
]


class Sweep:
    """Runs and records the states of one themed pass."""

    def __init__(self, page, theme, expected=OPENS):
        self.page, self.theme, self.expected, self.done = page, theme, list(expected), []

    def step(self, route, state, action):
        action()  # raises when a control is missing: nothing is caught
        self.page.csp.check()
        self.shot(route, state)
        self.done.append((route, state))

    def shot(self, route, state):
        folder = os.environ.get(SHOTS_ENV)
        if not folder:
            return None
        path = Path(folder) / ("%s-%s-%s.png" % (self.theme, route, state))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        return path

    def finish(self):
        missing = [s for s in self.expected if s not in self.done]
        assert not missing, "the sweep did not open: %r" % (missing,)


# ---- small helpers --------------------------------------------------------------------------------------------------

def tab(page, route):
    page.get_by_role("tab", name=TABS[route], exact=True).click()
    expect(page.get_by_role("tab", name=TABS[route], exact=True)).to_have_attribute("aria-selected", "true")


def slow(page, pattern, ms=1200):
    """Answer the matching requests late, so a loading state can be seen."""
    def handle(route):
        try:
            page.wait_for_timeout(ms)
            route.continue_()
        except Exception:                          # the page was closed while the answer was held back
            pass
    page.route(pattern, handle)


def unslow(page, pattern):
    page.unroute(pattern)


def skeleton_on_first_visit(page, route, pattern):
    """Open a tab whose list answers late: the skeleton is shown, then the rows."""
    slow(page, pattern)
    try:
        tab(page, route)
        expect(page.locator("[data-state=loading]").first).to_be_visible()
    finally:
        page.wait_for_timeout(100)
    expect(page.locator("[data-state=ready]").first).to_be_visible(timeout=TIMEOUT_MS * 2)
    unslow(page, pattern)


def ready(page):
    expect(page.locator("[data-state=ready]").first).to_be_visible()


def open_select(page, scope, label):
    scope.locator(".el-select").filter(has=page.get_by_role("combobox", name=label, exact=True)).click()
    page.wait_for_timeout(400)                     # the popper slides in
    expect(page.locator("li.el-select-dropdown__item:visible").first).to_be_visible()


def close_select(page, label):
    page.keyboard.press("Escape")
    expect(page.get_by_role("combobox", name=label, exact=True)).to_have_attribute("aria-expanded", "false")


def close_dialog(page):
    page.locator(".el-dialog__headerbtn").last.click()
    expect(page.locator(".el-dialog:visible")).to_have_count(0)


def cancel_box(page, name="Cancel"):
    box = page.locator(".el-message-box")
    expect(box).to_be_visible()
    return box


def seed(app, db, live):
    k = new_key(db)
    new_account(db, label="alive", checked=True)
    new_account(db, label="gone", state="dead", checked=True)
    done_job(db, k, new_account(db, label="worker", checked=True), ref="r1", status="done", prompt="a cat on a roof")
    failed = app.run(db, lambda p: failed_job(p, k, "one"))
    db.one("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms) VALUES (%s, 'fake', "
           "'/v1/videos', 422, 5) RETURNING id", k)
    plant(db)
    plant_unclear(db, k, "Which style do you want?")
    return {"key": k, "failed": failed}


def check_multiple_select_tick(page):
    """No view has a multiple select, but the theme overrides the tick Element Plus draws with a data: image there
    (blocked by the CSP). Build the markup it styles and read the computed mask: it must be none."""
    got = page.evaluate("""() => {
        const box = document.createElement('div');
        box.className = 'el-select-dropdown is-multiple';
        const list = document.createElement('ul');
        const item = document.createElement('li');
        item.className = 'el-select-dropdown__item is-selected';
        item.textContent = 'x';
        list.appendChild(item);
        box.appendChild(list);
        document.body.appendChild(box);
        const after = getComputedStyle(item, '::after');
        const out = [after.maskImage, after.webkitMaskImage];
        box.remove();
        return out;
    }""", isolated_context=False)
    assert all(v in ("none", "", None) for v in got), "the tick still has a mask image: %r" % (got,)


# ---- the sweep ------------------------------------------------------------------------------------------------------

def run_sweep(page, app, live, db, sw, data):
    page.set_default_timeout(TIMEOUT_MS)
    failed = str(data["failed"])[:8]

    # login
    def login_form():
        page.goto(app.url + "#/login")
        expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    sw.step("login", "form", login_form)

    def login_busy():
        slow(page, "**/admin/login", 1500)
        page.get_by_label("Username").fill(live.user)
        page.get_by_label("Password").fill(live.password)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("button", name="Signing in...")).to_be_visible()
        expect(page).to_have_url(re.compile(r"#/jobs$"), timeout=TIMEOUT_MS * 2)
        unslow(page, "**/admin/login")
    sw.step("login", "busy-button", login_busy)

    # jobs (the first load after sign-in is the loading state; open it again late for the skeleton)
    sw.step("jobs", "loading-skeleton", lambda: (tab(page, "accounts"), skeleton_on_first_visit(
        page, "jobs", re.compile(r"/admin/jobs\?"))))
    sw.step("jobs", "list", lambda: ready(page))

    def refresh_busy():
        slow(page, re.compile(r"/admin/jobs\?"))
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(page.get_by_role("button", name="Refresh", exact=True)).to_have_class(re.compile("is-loading"))
        expect(page.get_by_role("button", name="Refresh", exact=True)).not_to_have_class(re.compile("is-loading"),
                                                                                         timeout=TIMEOUT_MS * 2)
        unslow(page, re.compile(r"/admin/jobs\?"))
    sw.step("jobs", "refresh-busy", refresh_busy)

    sw.step("jobs", "status-select", lambda: (open_select(page, page, "Status"), close_select(page, "Status")))
    sw.step("jobs", "key-select", lambda: (open_select(page, page, "Key"), close_select(page, "Key")))
    sw.step("jobs", "bulk-selected", lambda: (
        page.locator("tbody tr").first.locator("label.el-checkbox").click(),
        expect(page.locator(".jobs__count")).to_have_text("1 selected")))

    def regenerate_confirm():
        page.get_by_role("button", name="Regenerate selected").click()
        expect(cancel_box(page)).to_contain_text("Regenerate")
    sw.step("jobs", "regenerate-confirm", regenerate_confirm)
    page.locator(".el-message-box").get_by_role("button", name="Cancel").click()
    expect(page.locator(".el-message-box")).to_have_count(0)

    sw.step("jobs", "recheck-toast", lambda: (
        page.get_by_role("button", name="Re-check selected").click(),
        expect(page.locator(".el-message").first).to_be_visible()))

    def detail_loading():
        pattern = re.compile(r"/admin/jobs/[0-9a-f-]{36}$")
        slow(page, pattern)
        page.locator("tbody tr").filter(has_text=failed).get_by_role("button", name="Details").click()
        expect(page.locator(".el-dialog .el-skeleton")).to_be_visible()
        expect(page.locator(".el-dialog [data-section=timeline]")).to_be_visible(timeout=TIMEOUT_MS * 2)
        unslow(page, pattern)
    sw.step("jobs", "detail-loading", detail_loading)
    sw.step("jobs", "detail", lambda: expect(page.locator(".el-dialog [data-section=technical]")).to_be_visible())

    def detail_regenerate():
        page.locator(".el-dialog:visible").get_by_role("button", name="Regenerate", exact=True).click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("jobs", "detail-regenerate-confirm", detail_regenerate)
    page.locator(".el-message-box").get_by_role("button", name="Cancel").click()
    page.locator(".el-dialog:visible").get_by_role("button", name="Close", exact=True).last.click()
    expect(page.locator(".el-dialog:visible")).to_have_count(0)

    # accounts
    sw.step("accounts", "loading-skeleton", lambda: skeleton_on_first_visit(page, "accounts",
                                                                         re.compile(r"/admin/accounts")))
    sw.step("accounts", "list", lambda: expect(page.locator("tbody tr").filter(has_text="alive")).to_be_visible())

    def more_menu():
        page.get_by_role("button", name="More actions for alive").click()
        expect(page.get_by_role("menuitem", name="Edit")).to_be_visible()
    sw.step("accounts", "more-menu", more_menu)

    def edit_dialog():
        page.get_by_role("menuitem", name="Edit").click()
        expect(page.locator(".el-dialog__title:visible")).to_have_text("Edit account")
    sw.step("accounts", "edit-dialog", edit_dialog)
    close_dialog(page)

    def delete_confirm():
        page.get_by_role("button", name="More actions for alive").click()
        page.get_by_role("menuitem", name="Delete").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("accounts", "delete-confirm", delete_confirm)
    page.locator(".el-message-box").get_by_role("button", name="Cancel").click()

    sw.step("accounts", "import-dialog", lambda: (
        page.get_by_role("button", name="Import", exact=True).click(),
        expect(page.locator(".el-dialog__title:visible")).to_have_text("Import accounts")))
    sw.step("accounts", "import-select", lambda: (open_select(page, page.locator(".el-dialog:visible"), "Provider"),
                                                  close_select(page, "Provider")))

    def import_toast():
        page.locator(".el-dialog:visible").get_by_role("button", name="Import", exact=True).click()
        expect(page.locator(".el-message--error, .el-message--warning").first).to_be_visible()
    sw.step("accounts", "import-toast", import_toast)
    page.locator(".el-dialog:visible").get_by_role("button", name="Close", exact=True).last.click()
    expect(page.locator(".el-dialog:visible")).to_have_count(0)

    # keys
    sw.step("keys", "loading-skeleton", lambda: skeleton_on_first_visit(page, "keys", re.compile(r"/admin/keys")))
    sw.step("keys", "list", lambda: ready(page))
    sw.step("keys", "new-dialog", lambda: (
        page.get_by_role("button", name="New key").click(),
        expect(page.locator(".el-dialog__title:visible")).to_have_text("New API key")))
    sw.step("keys", "wallet-select", lambda: (
        open_select(page, page.locator(".el-dialog:visible"), "fake video mode"),
        close_select(page, "fake video mode")))

    def callout():
        dialog = page.locator(".el-dialog:visible")
        dialog.get_by_label("Label", exact=True).fill("sweep key")
        dialog.get_by_role("button", name="Create").click()
        expect(page.get_by_test_id("new-key")).to_be_visible()
    sw.step("keys", "one-time-callout", callout)
    page.get_by_test_id("new-key").get_by_role("button", name="Dismiss").click()

    def key_edit():
        page.locator("tbody tr").filter(has_text="sweep key").get_by_role("button", name="Edit").click()
        expect(page.locator(".el-dialog__title:visible")).to_have_text("Edit API key")
    sw.step("keys", "edit-dialog", key_edit)
    close_dialog(page)

    def key_delete():
        page.locator("tbody tr").filter(has_text="sweep key").get_by_role("button", name="Delete").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("keys", "delete-confirm", key_delete)
    page.locator(".el-message-box").get_by_role("button", name="Cancel").click()

    # request logs
    sw.step("logs", "loading-skeleton", lambda: skeleton_on_first_visit(page, "logs", re.compile(r"/admin/logs")))
    sw.step("logs", "list", lambda: ready(page))
    sw.step("logs", "status-select", lambda: (open_select(page, page, "Status"), close_select(page, "Status")))
    sw.step("logs", "date-fields", lambda: (page.get_by_label("From", exact=True).fill("2026-10-01"),
                                            page.get_by_label("To", exact=True).fill("2026-10-31"),
                                            page.get_by_label("From", exact=True).fill(""),
                                            page.get_by_label("To", exact=True).fill("")))

    def log_detail():
        page.locator("tbody tr").first.get_by_role("button", name="Details").click()
        expect(page.locator(".el-dialog__title:visible")).to_contain_text("Request")
    sw.step("logs", "detail-dialog", log_detail)
    page.locator(".el-dialog:visible").get_by_role("button", name="Close", exact=True).last.click()
    expect(page.locator(".el-dialog:visible")).to_have_count(0)

    def clear_confirm():
        page.get_by_role("button", name="Clear logs").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("logs", "clear-confirm", clear_confirm)
    page.locator(".el-message-box").get_by_role("button", name="Cancel").click()

    # alerts
    sw.step("alerts", "loading-skeleton", lambda: skeleton_on_first_visit(page, "alerts",
                                                                       re.compile(r"/admin/alerts/groups")))
    sw.step("alerts", "list", lambda: ready(page))
    sw.step("alerts", "status-select", lambda: (open_select(page, page, "Status"), close_select(page, "Status")))
    sw.step("alerts", "kind-select", lambda: (open_select(page, page, "Kind"), close_select(page, "Kind")))
    sw.step("alerts", "detail-panel", lambda: (
        page.locator("tbody tr").first.get_by_role("button", name="Details").click(),
        expect(page.get_by_test_id("alert-detail")).to_be_visible()))

    # prompt frames
    def frames_list():
        tab(page, "frames")
        expect(page.locator("[data-frame-kind=video]")).to_be_visible()
    sw.step("frames", "list", frames_list)
    sw.step("frames", "provider-select", lambda: (open_select(page, page, "Provider"), close_select(page, "Provider")))
    sw.step("frames", "editor", lambda: (
        page.locator("[data-frame-kind=video]").locator("tbody tr", has_text="fake-video")
        .get_by_role("button", name="Edit fake-video").click(),
        expect(page.get_by_label("Frame text")).to_be_visible()))

    # settings
    sw.step("settings", "loading-skeleton", lambda: skeleton_on_first_visit(page, "settings",
                                                                         re.compile(r"/admin/settings")))
    sw.step("settings", "list", lambda: expect(page.get_by_label("Value of log_retention_days")).to_be_visible())
    sw.step("settings", "edited-row", lambda: (
        page.get_by_label("Value of log_retention_days").fill("45"),
        expect(page.locator(".settings tbody tr", has_text="log_retention_days")).to_contain_text("edited")))
    sw.step("settings", "saved-toast", lambda: (
        page.get_by_role("button", name="Save log_retention_days").click(),
        expect(page.locator(".el-message--success").first).to_be_visible()))

    def error_toast():
        page.get_by_label("Value of log_retention_days").fill("not a number")
        page.get_by_role("button", name="Save log_retention_days").click()
        expect(page.locator(".el-message--error").first).to_be_visible()
    sw.step("settings", "error-toast", error_toast)
    sw.step("settings", "password-form", lambda: expect(page.get_by_label("Current password")).to_be_visible())

    # the Test tab
    sw.step("test", "form", lambda: (tab(page, "test"),
                                     expect(page.get_by_label("API key", exact=True)).to_be_visible()))

    def load_busy():
        slow(page, "**/v1/models*")
        page.get_by_label("API key", exact=True).fill(data["raw_key"])
        page.get_by_role("button", name="Load models").click()
        expect(page.locator(".test-tab__model")).to_have_count(0)     # the answer is still on its way
    sw.step("test", "load-pending", load_busy)
    unslow(page, "**/v1/models*")
    sw.step("test", "models-loaded", lambda: expect(page.locator(".test-tab__model", has_text="fake-video"))
            .to_be_visible(timeout=TIMEOUT_MS * 2))

    # shell
    sw.step("shell", "scope-select", lambda: (open_select(page, page, "Provider filter"),
                                              close_select(page, "Provider filter")))
    sw.step("shell", "multiple-select-tick", lambda: check_multiple_select_tick(page))
    page.unroute_all(behavior="wait")              # no late-answer handler may outlive the page
    sw.finish()


@pytest.mark.parametrize("color_scheme", THEMES)
def test_every_dialog_popper_toast_and_loading_state_runs_under_the_csp_in_both_themes(
        app, app_page, live, db, framed, color_scheme):
    data = seed(app, db, live)
    data["raw_key"] = live.call(live.env.new_key()).raw
    run_sweep(app_page, app, live, db, Sweep(app_page, color_scheme), data)
    # the sweep provokes a refused save, which the browser logs as a failed load; nothing else may be in the list
    unexpected = [p for p in app_page.problems if "Failed to load resource" not in p]
    assert not unexpected, unexpected
    app_page.problems.clear()


# ---- the sweep's own guards -----------------------------------------------------------------------------------------

SELF = "tests/admin_ui/test_e2e_sweep.py"


# The anchors are written as two adjacent literals, so the marker's own text is not a second match of its anchor.
@mutation(SELF, 'THEMES = ("light", ' '"dark")', 'THEMES = ("light",)', expect="{'light'}")
def test_the_sweep_covers_both_themes():
    assert set(THEMES) == {"light", "dark"}


@mutation(SELF, "action()  # raises when " "a control is missing: nothing is caught",
          "try:\n            action()\n        except AssertionError:\n            pass", expect="DID NOT RAISE")
def test_a_listed_control_that_cannot_be_found_fails_the_step_and_the_sweep(app, app_page):
    page = app_page
    page.set_default_timeout(1500)
    page.goto(app.url + "#/login")
    sw = Sweep(page, "light", expected=[("login", "form"), ("login", "ghost")])
    sw.step("login", "form", lambda: expect(page.get_by_role("button", name="Sign in")).to_be_visible())
    with pytest.raises(AssertionError):
        sw.step("login", "ghost", lambda: expect(page.get_by_role("button", name="No such button")).to_be_visible())
    with pytest.raises(AssertionError, match="did not open"):
        sw.finish()


@mutation(SELF, "assert not " "missing,", "assert True or missing,", expect="DID NOT RAISE")
def test_a_state_that_was_never_reached_fails_the_sweep(app, app_page):
    sw = Sweep(app_page, "dark", expected=[("jobs", "list")])
    with pytest.raises(AssertionError, match="did not open"):
        sw.finish()


def test_screenshots_are_saved_only_when_a_folder_is_given(app, app_page, tmp_path, monkeypatch):
    page = app_page
    page.goto(app.url + "#/login")
    sw = Sweep(page, "light")
    monkeypatch.delenv(SHOTS_ENV, raising=False)
    sw.step("login", "form", lambda: None)
    assert not list(tmp_path.iterdir()), "nothing is written without the variable"
    monkeypatch.setenv(SHOTS_ENV, str(tmp_path / "shots"))
    sw.step("login", "form", lambda: None)
    files = [p.name for p in (tmp_path / "shots").iterdir()]
    assert files == ["light-login-form.png"], files
    assert (tmp_path / "shots" / files[0]).stat().st_size > 1000
