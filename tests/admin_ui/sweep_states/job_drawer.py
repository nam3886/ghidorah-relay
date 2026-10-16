"""The job detail: loading, the sections, and the regenerate confirm."""

import re

from patchright.sync_api import expect

from . import TIMEOUT_MS, cancel_box, close_dialog, dialog, dismiss_box, slow, unslow

ROUTE = "job_drawer"
STATES = [("jobs", "detail-loading"), ("jobs", "detail"), ("jobs", "detail-regenerate-confirm")]
DETAIL = re.compile(r"^Job ")          # the title of the detail is "Job <first 8 characters of the id>"


def run(page, app, live, sw, data):
    failed = str(data["failed"])[:8]

    def detail_loading():
        pattern = re.compile(r"/admin/jobs/[0-9a-f-]{36}$")
        slow(page, pattern)
        page.locator("tbody tr").filter(has_text=failed).get_by_role("button", name="Details").click()
        expect(dialog(page, DETAIL).locator(".el-skeleton")).to_be_visible()
        expect(dialog(page, DETAIL).locator("[data-section=timeline]")).to_be_visible(timeout=TIMEOUT_MS * 2)
        unslow(page, pattern)
    sw.step("jobs", "detail-loading", detail_loading)
    sw.step("jobs", "detail", lambda: expect(dialog(page, DETAIL).locator("[data-section=technical]")).to_be_visible())

    def detail_regenerate():
        dialog(page, DETAIL).get_by_role("button", name="Regenerate", exact=True).click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("jobs", "detail-regenerate-confirm", detail_regenerate)
    dismiss_box(page)
    close_dialog(page, DETAIL)
