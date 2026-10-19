"""The Request logs tab: skeleton, rows, the date range, the request drawer and the clear confirm."""

import re

from patchright.sync_api import expect
from pixel_capture import sideways

from . import cancel_box, dialog, dismiss_box, expect_dialog, ready, skeleton_step

ROUTE = "logs"
STATES = [
    ("logs", "loading-skeleton"), ("logs", "list"), ("logs", "date-fields"), ("logs", "date-range-open"),
    ("logs", "request-drawer"), ("logs", "clear-confirm"),
]
DETAIL = "Request"                       # the drawer is titled "Request <id>"
OPEN_REQUEST = re.compile(r"Open (details of )?request")    # the chevron of a table row, the row of the phone list


def days(page, first, last):
    """Type a range (or empty both ends) and confirm with Enter: the picker reports a change when its panel closes."""
    start, end = page.get_by_label("From", exact=True), page.get_by_label("To", exact=True)
    start.click()
    start.fill(first)
    end.fill(last)
    end.press("Enter")


PANEL = ".el-picker-panel:visible"


def range_pane(page):
    """Open the range picker on its two months (side by side, or stacked on a phone) with the quick options."""
    page.get_by_label("From", exact=True).click()
    expect(page.locator(PANEL).locator(".el-date-range-picker__content.is-left .el-date-table")).to_be_visible()
    expect(page.locator(PANEL).locator(".el-date-range-picker__content.is-right .el-date-table")).to_be_visible()
    expect(page.locator(PANEL).get_by_role("button", name="Last 30 days", exact=True)).to_be_visible()


def inside_the_window(page):
    """The panel, and so the page, fits the window: nothing sideways, the panel's edges inside the viewport."""
    box = page.locator(PANEL).bounding_box()
    width = page.evaluate("innerWidth")
    assert box["x"] >= 0 and box["x"] + box["width"] <= width, "the panel leaves the window: %r in %r" % (box, width)
    assert sideways(page) <= 1, "the page scrolls sideways"


def close_panel(page):
    page.get_by_label("From", exact=True).press("Escape")
    expect(page.locator(PANEL)).to_have_count(0)


def run(page, app, live, sw, data):
    skeleton_step(sw, "logs", re.compile(r"/admin/logs"))
    sw.step("logs", "list", lambda: ready(page))
    sw.step("logs", "date-fields", lambda: days(page, "2026-10-01", "2026-10-31"))
    sw.step("logs", "date-range-open", lambda: (range_pane(page), inside_the_window(page)),
            after=lambda: close_panel(page))

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
