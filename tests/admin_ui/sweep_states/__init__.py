"""What the whole-app sweep opens, one module per route, and the helpers they share.

Every route module has `ROUTE`, `STATES` (the (route, state) pairs it opens, in order) and
`run(page, app, live, sw, data)`, which opens each state with `sw.step(route, state, action, after=None)`. A control
that cannot be found fails the step: nothing is caught. The order of the modules is `test_e2e_sweep.MODULES`.

Elements are found by role and name: a dialog or drawer by its accessible name (`dialog`), a select by its label
(`open_select`), a tab by its name (`tab`)."""

import os
import re

from patchright.sync_api import expect

SHOTS_ENV = "RELAY_SCREENSHOTS_DIR"
TIMEOUT_MS = 10_000
TABS = {"jobs": "Jobs", "accounts": "Accounts", "keys": "API keys", "logs": "Request logs", "alerts": "Alerts",
        "frames": "Prompt frames", "settings": "Settings", "test": "Test"}


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


def hold_ms():
    """How long a late answer is held: long enough to photograph the skeleton when shots are wanted."""
    return 5000 if os.environ.get(SHOTS_ENV) else 1200


def open_skeleton(page, route, pattern):
    """Open a tab whose list answers late and stop while the skeleton is on screen (the shot is taken then)."""
    slow(page, pattern, hold_ms())
    tab(page, route)
    expect(page.locator("[data-state=loading]").first).to_be_visible()


def release_skeleton(page, pattern):
    """Let the held answer through: the rows replace the skeleton."""
    expect(page.locator("[data-state=ready]").first).to_be_visible(timeout=TIMEOUT_MS * 2)
    unslow(page, pattern)


def skeleton_step(sw, route, pattern, before=None):
    """The loading state of a tab, photographed while the skeleton shows, then released."""
    def action():
        if before:
            before()
        open_skeleton(sw.page, route, pattern)
    sw.step(route, "loading-skeleton", action, after=lambda: release_skeleton(sw.page, pattern))


def ready(page):
    expect(page.locator("[data-state=ready]").first).to_be_visible()


def dialog(page, name):
    """The dialog or drawer with this accessible name (its title); a string matches as a substring."""
    return page.get_by_role("dialog", name=name)


def expect_dialog(page, name):
    expect(dialog(page, name)).to_be_visible()
    return dialog(page, name)


def close_dialog(page, name):
    """Close the dialog or drawer with this name by its own button (Close; Back on a phone sheet), and wait until it
    is gone. A form that was typed in asks first: this closes a clean one."""
    dialog(page, name).get_by_role("button", name=re.compile(r"^(Close|Back)")).last.click()
    expect(dialog(page, name)).to_have_count(0)


def open_select(page, label, scope=None):
    """Open the select that carries this label and wait for its options (the popper slides in)."""
    # The select draws its value as a span over its readonly input, so a click on the input is intercepted by design;
    # Playwright's hit-target check stays on for every other click, and this one opens the select from the keyboard
    # (focus, then ArrowDown), the way a keyboard user does.
    combobox = (scope or page).get_by_role("combobox", name=label, exact=True)
    combobox.focus()
    page.keyboard.press("ArrowDown")
    expect(combobox).to_have_attribute("aria-expanded", "true")
    page.wait_for_timeout(400)
    expect(page.get_by_role("option").first).to_be_visible()


def close_select(page, label):
    page.keyboard.press("Escape")
    expect(page.get_by_role("combobox", name=label, exact=True)).to_have_attribute("aria-expanded", "false")


def cancel_box(page):
    """The confirm box that is open."""
    box = page.locator(".el-message-box")
    expect(box).to_be_visible()
    return box


def dismiss_box(page, name="Cancel"):
    page.locator(".el-message-box").get_by_role("button", name=name).click()
    expect(page.locator(".el-message-box")).to_have_count(0)
