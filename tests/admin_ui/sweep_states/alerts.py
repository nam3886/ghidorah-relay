"""The Alerts tab: skeleton, groups and the inline detail of a group."""

import re

from patchright.sync_api import expect

from . import ready, skeleton_step

ROUTE = "alerts"
STATES = [("alerts", "loading-skeleton"), ("alerts", "list"), ("alerts", "detail-panel")]


def run(page, app, live, sw, data):
    skeleton_step(sw, "alerts", re.compile(r"/admin/alerts/groups"))
    sw.step("alerts", "list", lambda: ready(page))
    sw.step("alerts", "detail-panel", lambda: (
        page.get_by_role("button", name="Details of").first.click(),
        expect(page.get_by_test_id("alert-detail")).to_be_visible()))
