"""The Alerts tab: skeleton, groups, the Status and Kind selects and the detail panel."""

import re

from patchright.sync_api import expect

from . import close_select, open_select, ready, skeleton_step

ROUTE = "alerts"
STATES = [
    ("alerts", "loading-skeleton"), ("alerts", "list"), ("alerts", "status-select"), ("alerts", "kind-select"),
    ("alerts", "detail-panel"),
]


def run(page, app, live, sw, data):
    skeleton_step(sw, "alerts", re.compile(r"/admin/alerts/groups"))
    sw.step("alerts", "list", lambda: ready(page))
    sw.step("alerts", "status-select", lambda: (open_select(page, "Status"), close_select(page, "Status")))
    sw.step("alerts", "kind-select", lambda: (open_select(page, "Kind"), close_select(page, "Kind")))
    sw.step("alerts", "detail-panel", lambda: (
        page.locator("tbody tr").first.get_by_role("button", name="Details").click(),
        expect(page.get_by_test_id("alert-detail")).to_be_visible()))
