"""The Request logs tab: skeleton, rows, the Status select, the date fields, the detail dialog and the clear confirm."""

import re

from patchright.sync_api import expect

from . import cancel_box, close_dialog, close_select, dismiss_box, expect_dialog, open_select, ready, skeleton_step

ROUTE = "logs"
STATES = [
    ("logs", "loading-skeleton"), ("logs", "list"), ("logs", "status-select"), ("logs", "date-fields"),
    ("logs", "detail-dialog"), ("logs", "clear-confirm"),
]
DETAIL = "Request"                       # the dialog is titled "Request <id>"


def run(page, app, live, sw, data):
    skeleton_step(sw, "logs", re.compile(r"/admin/logs"))
    sw.step("logs", "list", lambda: ready(page))
    sw.step("logs", "status-select", lambda: (open_select(page, "Status"), close_select(page, "Status")))
    sw.step("logs", "date-fields", lambda: (page.get_by_label("From", exact=True).fill("2026-10-01"),
                                            page.get_by_label("To", exact=True).fill("2026-10-31"),
                                            page.get_by_label("From", exact=True).fill(""),
                                            page.get_by_label("To", exact=True).fill("")))

    def log_detail():
        page.locator("tbody tr").first.get_by_role("button", name="Details").click()
        expect_dialog(page, DETAIL)
    sw.step("logs", "detail-dialog", log_detail)
    close_dialog(page, DETAIL)

    def clear_confirm():
        page.get_by_role("button", name="Clear logs").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("logs", "clear-confirm", clear_confirm)
    dismiss_box(page)
