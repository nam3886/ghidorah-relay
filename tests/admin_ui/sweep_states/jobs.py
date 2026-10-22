"""The Jobs list: loading skeleton, rows, the busy Refresh, an hour filter from the 24-hour band, the two selects, a
ticked row, the bulk confirm, toast."""

import re

from patchright.sync_api import expect

from . import TIMEOUT_MS, cancel_box, close_select, dismiss_box, open_select, ready, skeleton_step, slow, tab, unslow

ROUTE = "jobs"
STATES = [
    ("jobs", "loading-skeleton"), ("jobs", "list"), ("jobs", "refresh-busy"), ("jobs", "hour-filter"),
    ("jobs", "status-select"), ("jobs", "key-select"), ("jobs", "bulk-selected"), ("jobs", "regenerate-confirm"),
    ("jobs", "recheck-toast"),
]


def run(page, app, live, sw, data):
    # the first load after sign-in is the loading state; open it again late for the skeleton
    skeleton_step(sw, "jobs", re.compile(r"/admin/jobs\?"), before=lambda: tab(page, "accounts"))
    sw.step("jobs", "list", lambda: ready(page))
    # a phone names its Refresh by the update time ("Refresh, updated 20:55:02"), the others by the word alone
    refresh = page.get_by_role("button", name=re.compile(r"^Refresh"))

    def refresh_busy():
        slow(page, re.compile(r"/admin/jobs\?"))
        refresh.click()
        expect(refresh).to_have_class(re.compile("is-loading"))
        expect(refresh).not_to_have_class(re.compile("is-loading"), timeout=TIMEOUT_MS * 2)
        unslow(page, re.compile(r"/admin/jobs\?"))
    sw.step("jobs", "refresh-busy", refresh_busy)

    def hour_filter():
        page.get_by_test_id("hour-bar").last.click()
        expect(page.get_by_test_id("hour-chip")).to_contain_text("Hour ")
        ready(page)
    sw.step("jobs", "hour-filter", hour_filter,
            after=lambda: page.get_by_role("button", name="Clear the hour filter").click())

    sw.step("jobs", "status-select", lambda: (open_select(page, "Status"), close_select(page, "Status")))
    sw.step("jobs", "key-select", lambda: (open_select(page, "Key"), close_select(page, "Key")))
    # a table row on a desktop or tablet, a stacked item on a phone; both have the tick box in a label
    first_row = page.locator("tbody tr:not(.is-day), li.data-table__item").first
    sw.step("jobs", "bulk-selected", lambda: (
        first_row.locator("label.el-checkbox, label.data-table__check").first.click(),
        expect(page.locator(".jobs__count")).to_have_text("1 selected")))

    def regenerate_confirm():
        page.get_by_role("button", name="Regenerate…").click()
        expect(cancel_box(page)).to_contain_text("Regenerate")
    sw.step("jobs", "regenerate-confirm", regenerate_confirm)
    dismiss_box(page)

    sw.step("jobs", "recheck-toast", lambda: (
        page.get_by_role("button", name="Re-check").click(),
        expect(page.locator(".el-message").first).to_be_visible()))
