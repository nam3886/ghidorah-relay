"""The API keys tab: skeleton, rows, the new-key drawer (its discard question and its scrolling body), the one-time
key modal, edit and delete (both from the row menu)."""

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
    ("keys", "new-dialog-scrolls"), ("keys", "new-dialog-discard-confirm"),
    ("keys", "one-time-callout"), ("keys", "edit-dialog"), ("keys", "delete-confirm"),
]
SHORT_HEIGHT = 460          # a window too short for the form: its body scrolls inside the drawer


def key_menu(page, name, item):
    """Pick an item of a key's row menu (More actions for <name>)."""
    page.get_by_role("button", name="More actions for %s" % name).first.click()
    page.get_by_role("menuitem", name=item, exact=True).click()


def run(page, app, live, sw, data):
    skeleton_step(sw, "keys", re.compile(r"/admin/keys"))
    sw.step("keys", "list", lambda: ready(page))
    sw.step("keys", "new-dialog", lambda: (
        page.get_by_role("button", name=re.compile(r"^(Create|New) key$")).first.click(),
        expect_dialog(page, "New API key")))

    window = {}

    def short_window():
        window["size"] = page.viewport_size
        page.set_viewport_size({"width": window["size"]["width"], "height": SHORT_HEIGHT})
        body = page.locator('[data-testid="key-drawer"] .el-drawer__body')
        expect(body).to_be_visible()
        assert body.evaluate("el => el.scrollHeight > el.clientHeight"), "the form body does not scroll"
        body.evaluate("el => { el.scrollTop = el.scrollHeight; }")

    sw.step("keys", "new-dialog-scrolls", short_window, after=lambda: page.set_viewport_size(window["size"]))

    def discard_confirm():
        new = dialog(page, "New API key")
        new.get_by_label("Label", exact=True).fill("half typed")
        page.keyboard.press("Escape")
        expect(page.locator(".el-message-box")).to_be_visible()
        expect(page.locator(".el-message-box")).to_contain_text("Discard what you typed?")

    sw.step("keys", "new-dialog-discard-confirm", discard_confirm, after=lambda: (
        page.locator(".el-message-box").get_by_role("button", name="Keep editing").click(),
        expect(page.locator(".el-message-box")).to_have_count(0)))

    def callout():
        new = dialog(page, "New API key")
        new.get_by_label("Label", exact=True).fill("sweep key")
        new.get_by_role("button", name="Create key").click()
        expect(page.get_by_test_id("new-key")).to_be_visible()
    sw.step("keys", "one-time-callout", callout)
    secret = page.get_by_test_id("new-key")
    secret.get_by_role("button", name="Copy").click()       # Done unlocks once the key was copied
    secret.get_by_role("button", name="Done").click()
    expect(secret).to_have_count(0)

    def key_edit():
        key_menu(page, "sweep key", "Edit key")
        expect_dialog(page, "Edit key · sweep key")
    sw.step("keys", "edit-dialog", key_edit)
    close_dialog(page, "Edit key · sweep key")

    def key_delete():
        key_menu(page, "sweep key", "Delete key")
        expect(cancel_box(page)).to_be_visible()
    sw.step("keys", "delete-confirm", key_delete)
    dismiss_box(page)
