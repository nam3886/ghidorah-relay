"""The API keys tab: skeleton, rows, the new-key dialog, the one-time callout, edit and delete."""

import re

from patchright.sync_api import expect

from . import (
    cancel_box,
    close_dialog,
    dialog,
    dismiss_box,
    expect_dialog,
    ready,
    skeleton_step,
)

ROUTE = "keys"
STATES = [
    ("keys", "loading-skeleton"), ("keys", "list"), ("keys", "new-dialog"),
    ("keys", "one-time-callout"), ("keys", "edit-dialog"), ("keys", "delete-confirm"),
]


def run(page, app, live, sw, data):
    skeleton_step(sw, "keys", re.compile(r"/admin/keys"))
    sw.step("keys", "list", lambda: ready(page))
    sw.step("keys", "new-dialog", lambda: (
        page.get_by_role("button", name="New key").click(),
        expect_dialog(page, "New API key")))

    def callout():
        new = dialog(page, "New API key")
        new.get_by_label("Label", exact=True).fill("sweep key")
        new.get_by_role("button", name="Create key").click()
        expect(page.get_by_test_id("new-key")).to_be_visible()
    sw.step("keys", "one-time-callout", callout)
    page.get_by_test_id("new-key").get_by_role("button", name="Dismiss").click()

    def key_edit():
        page.get_by_role("button", name="Edit key sweep key").click()
        expect_dialog(page, "Edit API key")
    sw.step("keys", "edit-dialog", key_edit)
    close_dialog(page, "Edit API key")

    def key_delete():
        page.get_by_role("button", name="Delete key sweep key").click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("keys", "delete-confirm", key_delete)
    dismiss_box(page)
