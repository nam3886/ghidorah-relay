"""The Request logs tab: skeleton, rows, the date fields, the request drawer and the clear confirm."""

import re

from patchright.sync_api import expect

from . import cancel_box, dialog, dismiss_box, expect_dialog, ready, skeleton_step

ROUTE = "logs"
STATES = [
    ("logs", "loading-skeleton"), ("logs", "list"), ("logs", "date-fields"), ("logs", "date-picker-day"),
    ("logs", "date-picker-year"), ("logs", "date-picker-month"), ("logs", "request-drawer"), ("logs", "clear-confirm"),
]
DETAIL = "Request"                       # the drawer is titled "Request <id>"
OPEN_REQUEST = re.compile(r"Open (details of )?request")    # the chevron of a table row, the row of the phone list


def day(page, label, text):
    """Type (or empty) a day and confirm with Enter: the picker reports a change when its panel closes."""
    field = page.get_by_label(label, exact=True)
    field.click()
    field.fill(text)
    field.press("Enter")


PANEL = ".el-picker-panel:visible"


def day_pane(page):
    """Open the From picker on its day pane."""
    page.get_by_label("From", exact=True).click()
    expect(page.locator(PANEL).locator(".el-date-table")).to_be_visible()


def year_pane(page):
    page.locator(PANEL).locator(".el-date-picker__header-label").first.click()       # the year label
    expect(page.locator(PANEL).locator(".el-year-table")).to_be_visible()


def month_pane(page):
    page.locator(PANEL).locator(".el-year-table td.available").first.click()         # a year lands on the months
    expect(page.locator(PANEL).locator(".el-month-table")).to_be_visible()


def close_panel(page):
    page.get_by_label("From", exact=True).press("Escape")
    expect(page.locator(PANEL)).to_have_count(0)


def run(page, app, live, sw, data):
    skeleton_step(sw, "logs", re.compile(r"/admin/logs"))
    sw.step("logs", "list", lambda: ready(page))
    sw.step("logs", "date-fields", lambda: [day(page, "From", "2026-10-01"), day(page, "To", "2026-10-31"),
                                            day(page, "From", ""), day(page, "To", "")])
    sw.step("logs", "date-picker-day", lambda: day_pane(page))
    sw.step("logs", "date-picker-year", lambda: year_pane(page))
    sw.step("logs", "date-picker-month", lambda: month_pane(page), after=lambda: close_panel(page))

    def log_detail():
        page.get_by_role("button", name=OPEN_REQUEST).first.click()
        expect_dialog(page, DETAIL)
    sw.step("logs", "request-drawer", log_detail)
    # a phone shows the drawer as a sheet whose way out is its Back button
    leave = dialog(page, DETAIL).get_by_role("button", name=re.compile(r"^(Close|Back to Request logs)$")).last
    leave.click()
    expect(dialog(page, DETAIL)).to_have_count(0)

    def clear_confirm():
        # below 1280 px Clear logs lives in the More menu
        more = page.get_by_role("button", name="More actions")
        if more.count():
            more.click()
            page.get_by_role("menuitem", name="Clear logs").click()
        else:
            page.get_by_role("button", name="Clear logs").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("logs", "clear-confirm", clear_confirm)
    dismiss_box(page)
