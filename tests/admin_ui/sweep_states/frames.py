"""The Prompt frames tab: the lists, the Provider select, the editor and the two notes drawn on the menu surface."""

import re

from patchright.sync_api import expect

from . import tab

ROUTE = "frames"
STATES = [("frames", "list"), ("frames", "provider-select"), ("frames", "editor"), ("frames", "preview-tip"),
          ("frames", "lock-history-tip"), ("frames", "editor-default")]

VIDEO = "[data-frame-kind=video]"


def run(page, app, live, sw, data):
    def frames_list():
        tab(page, "frames")
        expect(page.locator(VIDEO)).to_be_visible()
    sw.step("frames", "list", frames_list)
    # the provider list open: the drawn listbox under its trigger
    sw.step("frames", "provider-select", lambda: (
        page.get_by_role("combobox", name="Provider", exact=True).click(),
        expect(page.get_by_role("listbox", name="Provider")).to_be_visible()))
    sw.step("frames", "editor", lambda: (
        page.keyboard.press("Escape"),
        expect(page.get_by_role("listbox", name="Provider")).to_have_count(0),
        page.locator(VIDEO).get_by_role("button", name="Edit frame for fake-video").click(),
        expect(page.get_by_label("Frame text")).to_be_visible()))
    # the note on the preview's character count, opened from the keyboard (D125)
    sw.step("frames", "preview-tip", lambda: (
        # a draft to preview
        page.get_by_label("Frame text").fill("Preview frame. {{tracking_code}} {{client_prompt}}"),
        page.get_by_role("button", name="Preview", exact=True).click(),
        page.get_by_text(re.compile(r"^\d+ characters$")).focus(),
        expect(page.get_by_role("tooltip")).to_contain_text("Shown with the sample tracking code")))

    # the note on the History heading holds the lock history, which exists once the model's lock was toggled
    def lock_history_tip():
        # a lock needs an own frame (the server refuses one for a model that follows the default): save one first
        page.keyboard.press("Escape")
        page.get_by_label("Frame text").fill("Sweep frame. {{tracking_code}} {{client_prompt}}")
        save = page.get_by_role("button", name="Save", exact=True)
        save.click()
        expect(page.get_by_role("button", name="Follow the default")).to_be_visible()     # the frame is now its own
        page.locator(VIDEO).get_by_label("Keep own frame of fake-video").first.check()
        tip = page.get_by_role("heading", name="History").locator(".hint-tip")
        expect(tip).to_be_visible()
        tip.focus()
        expect(page.get_by_role("tooltip")).to_contain_text("Lock history:")
    sw.step("frames", "lock-history-tip", lock_history_tip)
    sw.step("frames", "editor-default", lambda: (
        page.keyboard.press("Escape"),
        page.locator(VIDEO).get_by_role("button", name="Edit the default").click(),
        expect(page.get_by_role("heading", name="Editing video / default")).to_be_visible()))
