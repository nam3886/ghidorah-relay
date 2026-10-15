"""The API keys tab: skeleton, rows, the new-key dialog and its select, the one-time callout, edit and delete."""

import re

from patchright.sync_api import expect

from . import (
    cancel_box,
    close_dialog,
    close_select,
    dialog,
    dismiss_box,
    expect_dialog,
    open_select,
    ready,
    skeleton_step,
)

ROUTE = "keys"
STATES = [
    ("keys", "loading-skeleton"), ("keys", "list"), ("keys", "new-dialog"), ("keys", "wallet-select"),
    ("keys", "one-time-callout"), ("keys", "edit-dialog"), ("keys", "delete-confirm"),
]


def run(page, app, live, sw, data):
    skeleton_step(sw, "keys", re.compile(r"/admin/keys"))
    sw.step("keys", "list", lambda: ready(page))
    sw.step("keys", "new-dialog", lambda: (
        page.get_by_role("button", name="New key").click(),
        expect_dialog(page, "New API key")))
    sw.step("keys", "wallet-select", lambda: (
        open_select(page, "fake video mode", scope=dialog(page, "New API key")),
        close_select(page, "fake video mode")))

    def callout():
        new = dialog(page, "New API key")
        new.get_by_label("Label", exact=True).fill("sweep key")
        new.get_by_role("button", name="Create").click()
        expect(page.get_by_test_id("new-key")).to_be_visible()
    sw.step("keys", "one-time-callout", callout)
    page.get_by_test_id("new-key").get_by_role("button", name="Dismiss").click()

    def key_edit():
        page.locator("tbody tr").filter(has_text="sweep key").get_by_role("button", name="Edit").click()
        expect_dialog(page, "Edit API key")
    sw.step("keys", "edit-dialog", key_edit)
    close_dialog(page, "Edit API key")

    def key_delete():
        page.locator("tbody tr").filter(has_text="sweep key").get_by_role("button", name="Delete").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("keys", "delete-confirm", key_delete)
    dismiss_box(page)
