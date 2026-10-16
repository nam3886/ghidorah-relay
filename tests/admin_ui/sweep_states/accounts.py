"""The Accounts tab: skeleton, rows, the row menu, the edit and import dialogs, a delete confirm, the import toast."""

import re

from patchright.sync_api import expect

from . import cancel_box, close_dialog, close_select, dialog, dismiss_box, expect_dialog, open_select, skeleton_step

ROUTE = "accounts"
STATES = [
    ("accounts", "loading-skeleton"), ("accounts", "list"), ("accounts", "row-tooltip"), ("accounts", "edit-dialog"),
    ("accounts", "delete-confirm"), ("accounts", "import-dialog"), ("accounts", "import-select"),
    ("accounts", "import-toast"),
]


def run(page, app, live, sw, data):
    skeleton_step(sw, "accounts", re.compile(r"/admin/accounts"))
    sw.step("accounts", "list", lambda: expect(page.locator("tbody tr").filter(has_text="alive")).to_be_visible())

    alive = page.locator("tbody tr").filter(has_text="alive")

    def row_tooltip():
        alive.get_by_role("button", name="Edit", exact=True).hover()
        expect(page.get_by_role("tooltip").filter(has_text="Edit")).to_be_visible()
    sw.step("accounts", "row-tooltip", row_tooltip)

    def edit_dialog():
        alive.get_by_role("button", name="Edit", exact=True).click()
        expect_dialog(page, "Edit account")
    sw.step("accounts", "edit-dialog", edit_dialog)
    close_dialog(page, "Edit account")

    def delete_confirm():
        alive.get_by_role("button", name="Delete", exact=True).click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("accounts", "delete-confirm", delete_confirm)
    dismiss_box(page)

    sw.step("accounts", "import-dialog", lambda: (
        page.get_by_role("button", name="Import", exact=True).click(),
        expect_dialog(page, "Import accounts")))
    sw.step("accounts", "import-select", lambda: (open_select(page, "Provider", scope=dialog(page, "Import accounts")),
                                                  close_select(page, "Provider")))

    def import_toast():
        dialog(page, "Import accounts").get_by_role("button", name="Import", exact=True).click()
        expect(page.locator(".el-message--error, .el-message--warning").first).to_be_visible()
    sw.step("accounts", "import-toast", import_toast)
    close_dialog(page, "Import accounts")
