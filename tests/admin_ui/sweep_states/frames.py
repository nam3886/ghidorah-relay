"""The Prompt frames tab: the lists, the Provider select and the editor."""

from patchright.sync_api import expect

from . import close_select, open_select, tab

ROUTE = "frames"
STATES = [("frames", "list"), ("frames", "provider-select"), ("frames", "editor")]


def run(page, app, live, sw, data):
    def frames_list():
        tab(page, "frames")
        expect(page.locator("[data-frame-kind=video]")).to_be_visible()
    sw.step("frames", "list", frames_list)
    sw.step("frames", "provider-select", lambda: (open_select(page, "Provider"), close_select(page, "Provider")))
    sw.step("frames", "editor", lambda: (
        page.locator("[data-frame-kind=video]").locator("tbody tr", has_text="fake-video")
        .get_by_role("button", name="Edit fake-video").click(),
        expect(page.get_by_label("Frame text")).to_be_visible()))
