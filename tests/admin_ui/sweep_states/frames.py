"""The Prompt frames tab: the lists, the Provider select and the editor."""

from patchright.sync_api import expect

from . import tab

ROUTE = "frames"
STATES = [("frames", "list"), ("frames", "provider-select"), ("frames", "editor"), ("frames", "editor-default")]


def run(page, app, live, sw, data):
    def frames_list():
        tab(page, "frames")
        expect(page.locator("[data-frame-kind=video]")).to_be_visible()
    sw.step("frames", "list", frames_list)
    # the provider list is the browser's own popup, which a page shot does not hold: the focused control is the state
    sw.step("frames", "provider-select", lambda: (
        page.locator('select[aria-label="Provider"]').focus(),
        expect(page.locator('select[aria-label="Provider"]')).to_be_focused()))
    sw.step("frames", "editor", lambda: (
        page.locator("[data-frame-kind=video]").get_by_role("button", name="Edit frame for fake-video").click(),
        expect(page.get_by_label("Frame text")).to_be_visible()))
    sw.step("frames", "editor-default", lambda: (
        page.locator("[data-frame-kind=video]").get_by_role("button", name="Edit the default").click(),
        expect(page.get_by_role("heading", name="Editing video / default")).to_be_visible()))
