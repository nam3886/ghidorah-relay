"""The Jobs list: loading skeleton, rows, a status chip picked, a provider picked (a card on a desktop, the strip below
it), the key select, a ticked row, the bulk confirm, toast. A phone draws no tick boxes (JobsPhone), so the three bulk
states are a desktop's and a tablet's only (`WIDE_ONLY`)."""

import re

from patchright.sync_api import expect

from . import cancel_box, close_select, dismiss_box, open_select, ready, skeleton_step, tab

ROUTE = "jobs"
STATES = [
    ("jobs", "loading-skeleton"), ("jobs", "list"), ("jobs", "status-picked"), ("jobs", "provider-picked"),
    ("jobs", "key-select"), ("jobs", "bulk-selected"), ("jobs", "regenerate-confirm"), ("jobs", "recheck-toast"),
]
WIDE_ONLY = {("jobs", "bulk-selected"), ("jobs", "regenerate-confirm"), ("jobs", "recheck-toast")}


def run(page, app, live, sw, data):
    # the first load after sign-in is the loading state; open it again late for the skeleton
    skeleton_step(sw, "jobs", re.compile(r"/admin/jobs\?"), before=lambda: tab(page, "accounts"))
    sw.step("jobs", "list", lambda: ready(page))
    chips = page.locator(".jobs__chips")

    def status_picked():
        look = chips.get_by_role("button", name=re.compile(r"^Needs a look · "))
        look.click()
        expect(look).to_have_attribute("aria-pressed", "true")
        ready(page)
    sw.step("jobs", "status-picked", status_picked,
            after=lambda: chips.get_by_role("button", name="All", exact=True).click())

    desktop = page.viewport_size["width"] >= 1280
    picker = page.get_by_test_id("provider-card-fake" if desktop else "provider-tab-fake")

    def provider_picked():
        picker.click()
        expect(picker).to_have_attribute("aria-pressed", "true")
        ready(page)
    sw.step("jobs", "provider-picked", provider_picked,
            after=lambda: (picker if desktop else page.get_by_test_id("provider-tab-all")).click())

    sw.step("jobs", "key-select", lambda: (open_select(page, "Key"), close_select(page, "Key")))
    if page.viewport_size["width"] < 768:
        return
    first_row = page.locator(".jobs__table tbody tr.is-row").first
    sw.step("jobs", "bulk-selected", lambda: (
        first_row.locator("input[type=checkbox]").click(),
        expect(page.locator(".jobs__count")).to_have_text("1 selected")))

    def regenerate_confirm():
        page.get_by_role("button", name="Regenerate…").click()
        expect(cancel_box(page)).to_contain_text("Regenerate")
    sw.step("jobs", "regenerate-confirm", regenerate_confirm)
    dismiss_box(page)

    sw.step("jobs", "recheck-toast", lambda: (
        page.get_by_role("button", name="Re-check").click(),
        expect(page.locator(".el-message").first).to_be_visible()))
