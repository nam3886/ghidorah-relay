"""The Accounts tab: skeleton, rows, the row More menu, the edit and import dialogs, a delete confirm, the import
toast."""

import re

from patchright.sync_api import expect

from . import cancel_box, close_dialog, dialog, dismiss_box, expect_dialog, skeleton_step

ROUTE = "accounts"
STATES = [
    ("accounts", "loading-skeleton"), ("accounts", "list"), ("accounts", "more-menu"), ("accounts", "edit-dialog"),
    ("accounts", "delete-confirm"), ("accounts", "import-dialog"), ("accounts", "import-toast"),
]

ROW = "tbody tr, [aria-label='Account list'] li"      # a table row from 768 px, a list row on a phone


def run(page, app, live, sw, data):
    skeleton_step(sw, "accounts", re.compile(r"/admin/accounts"))
    alive = page.locator(ROW).filter(has_text="alive")
    sw.step("accounts", "list", lambda: expect(alive).to_be_visible())

    def more_menu(name=None):
        alive.get_by_role("button", name="More actions for alive", exact=True).click()
        expect(page.get_by_role("menu", name="Account actions")).to_be_visible()
        if name:
            page.get_by_role("menuitem", name=name, exact=True).click()

    def close_menu():
        page.keyboard.press("Escape")
        expect(page.get_by_role("menu", name="Account actions")).to_have_count(0)

    sw.step("accounts", "more-menu", more_menu)
    close_menu()

    def edit_dialog():
        more_menu("Edit")
        expect_dialog(page, "Edit account")
    sw.step("accounts", "edit-dialog", edit_dialog)
    close_dialog(page, "Edit account")

    def delete_confirm():
        more_menu("Delete")
        expect(cancel_box(page)).to_be_visible()
    sw.step("accounts", "delete-confirm", delete_confirm)
    dismiss_box(page)

    sw.step("accounts", "import-dialog", lambda: (
        page.get_by_role("button", name="Import", exact=True).click(),
        expect_dialog(page, "Import accounts")))

    def import_toast():
        dialog(page, "Import accounts").get_by_role("button", name="Import", exact=True).click()
        expect(page.locator(".el-message--error, .el-message--warning").first).to_be_visible()
    sw.step("accounts", "import-toast", import_toast)
    close_dialog(page, "Import accounts")
