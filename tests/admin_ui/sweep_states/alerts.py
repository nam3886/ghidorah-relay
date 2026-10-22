"""The Alerts tab: skeleton, groups and the detail drawer of a group, read and failed."""

import re

from patchright.sync_api import expect

from . import ready, skeleton_step

ROUTE = "alerts"
STATES = [("alerts", "loading-skeleton"), ("alerts", "list"), ("alerts", "detail-drawer"),
          ("alerts", "detail-drawer-error")]
DETAIL = re.compile(r"/admin/alerts/groups/[^?]+\?")


def close_drawer(page):
    """Close the detail drawer by its own button (Close; Back on a phone sheet) and wait until it is gone."""
    drawer = page.get_by_test_id("alert-detail")
    drawer.get_by_role("button", name=re.compile(r"^(Close|Back)")).last.click()
    expect(drawer).to_be_hidden()


def run(page, app, live, sw, data):
    skeleton_step(sw, "alerts", re.compile(r"/admin/alerts/groups"))
    sw.step("alerts", "list", lambda: ready(page))
    sw.step("alerts", "detail-drawer", lambda: (
        page.get_by_role("button", name="Details of").first.click(),
        expect(page.get_by_test_id("alert-detail")).to_be_visible(),
        expect(page.get_by_test_id("alert-detail").locator("#alertDetail")).to_be_visible()),
        after=lambda: close_drawer(page))

    def failed_read():
        page.route(DETAIL, lambda route: route.fulfill(
            status=500, content_type="application/json",
            body='{"error": {"code": "server.error", "message": "the group could not be read"}}'))
        page.get_by_role("button", name="Details of").first.click()
        expect(page.get_by_test_id("alert-detail").get_by_role("alert")).to_be_visible()
    sw.step("alerts", "detail-drawer-error", failed_read, after=lambda: (
        close_drawer(page), page.unroute(DETAIL)))
