"""The Jobs list: loading skeleton, rows, the busy Refresh, the two selects, a ticked row, the bulk confirm, toast."""

import re

from patchright.sync_api import expect

from . import TIMEOUT_MS, cancel_box, close_select, dismiss_box, open_select, ready, skeleton_step, slow, tab, unslow

ROUTE = "jobs"
STATES = [
    ("jobs", "loading-skeleton"), ("jobs", "list"), ("jobs", "refresh-busy"), ("jobs", "status-select"),
    ("jobs", "key-select"), ("jobs", "bulk-selected"), ("jobs", "regenerate-confirm"), ("jobs", "recheck-toast"),
]


def run(page, app, live, sw, data):
    # the first load after sign-in is the loading state; open it again late for the skeleton
    skeleton_step(sw, "jobs", re.compile(r"/admin/jobs\?"), before=lambda: tab(page, "accounts"))
    sw.step("jobs", "list", lambda: ready(page))

    def refresh_busy():
        slow(page, re.compile(r"/admin/jobs\?"))
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(page.get_by_role("button", name="Refresh", exact=True)).to_have_class(re.compile("is-loading"))
        expect(page.get_by_role("button", name="Refresh", exact=True)).not_to_have_class(re.compile("is-loading"),
                                                                                         timeout=TIMEOUT_MS * 2)
        unslow(page, re.compile(r"/admin/jobs\?"))
    sw.step("jobs", "refresh-busy", refresh_busy)

    sw.step("jobs", "status-select", lambda: (open_select(page, "Status"), close_select(page, "Status")))
    sw.step("jobs", "key-select", lambda: (open_select(page, "Key"), close_select(page, "Key")))
    sw.step("jobs", "bulk-selected", lambda: (
        page.locator("tbody tr").first.locator("label.el-checkbox").click(),
        expect(page.locator(".jobs__count")).to_have_text("1 selected")))

    def regenerate_confirm():
        page.get_by_role("button", name="Regenerate selected").click()
        expect(cancel_box(page)).to_contain_text("Regenerate")
    sw.step("jobs", "regenerate-confirm", regenerate_confirm)
    dismiss_box(page)

    sw.step("jobs", "recheck-toast", lambda: (
        page.get_by_role("button", name="Re-check selected").click(),
        expect(page.locator(".el-message").first).to_be_visible()))
