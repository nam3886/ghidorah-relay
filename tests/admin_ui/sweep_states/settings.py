"""The Settings tab: skeleton, rows, an edited row, the saved and error toasts and the password form."""

import re

from patchright.sync_api import expect

from . import skeleton_step

ROUTE = "settings"
STATES = [
    ("settings", "loading-skeleton"), ("settings", "list"), ("settings", "edited-row"), ("settings", "saved-toast"),
    ("settings", "error-toast"), ("settings", "password-form"),
]


def run(page, app, live, sw, data):
    skeleton_step(sw, "settings", re.compile(r"/admin/settings"))
    sw.step("settings", "list", lambda: expect(page.get_by_label("Value of log_retention_days")).to_be_visible())
    sw.step("settings", "edited-row", lambda: (
        page.get_by_label("Value of log_retention_days").fill("45"),
        expect(page.locator("#settings tbody tr, #settings li", has_text="log_retention_days"))
        .to_contain_text("edited")))
    sw.step("settings", "saved-toast", lambda: (
        page.get_by_role("button", name="Save log_retention_days").click(),
        expect(page.locator(".el-message--success").first).to_be_visible()))

    def error_toast():
        page.get_by_label("Value of log_retention_days").fill("not a number")
        page.get_by_role("button", name="Save log_retention_days").click()
        expect(page.locator(".el-message--error").first).to_be_visible()
    sw.step("settings", "error-toast", error_toast)
    sw.step("settings", "password-form", lambda: expect(page.get_by_label("Current password")).to_be_visible())
