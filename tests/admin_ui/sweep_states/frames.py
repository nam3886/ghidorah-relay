"""The Prompt frames tab: the lists, the provider strip, the editor drawer (its preview, lock history and discard
question) and the two notes drawn on the menu surface."""

import re

from patchright.sync_api import expect

from . import close_dialog, expect_dialog, tab

ROUTE = "frames"
STATES = [("frames", "list"), ("frames", "provider-strip"), ("frames", "editor"), ("frames", "preview-tip"),
          ("frames", "lock-history-tip"), ("frames", "editor-default"), ("frames", "editor-discard-confirm")]

VIDEO = "[data-frame-kind=video]"
MODEL_EDITOR = "Editing video / fake-video"
DEFAULT_EDITOR = "Editing video / default"


def shut(page, name):
    """Close the editor drawer by its own button and wait for its scrim to be gone: the list takes clicks again."""
    close_dialog(page, name)
    expect(page.locator(".el-overlay:visible")).to_have_count(0)


def run(page, app, live, sw, data):
    def frames_list():
        tab(page, "frames")
        expect(page.locator(VIDEO)).to_be_visible()
        # the page opens with no drawer, whatever was open the last time
        expect(page.get_by_role("dialog")).to_have_count(0)
    sw.step("frames", "list", frames_list)
    # the provider strip picks the provider: one tab per provider, no All
    sw.step("frames", "provider-strip", lambda: (
        page.get_by_role("navigation", name="Provider").get_by_test_id("provider-tab-fake").click(),
        expect(page.get_by_test_id("provider-tab-fake")).to_have_attribute("aria-pressed", "true"),
        expect(page.get_by_test_id("provider-tab-all")).to_have_count(0)))
    sw.step("frames", "editor", lambda: (
        page.locator(VIDEO).get_by_role("button", name="Edit frame for fake-video").click(),
        expect_dialog(page, MODEL_EDITOR),
        expect(page.get_by_label("Frame text")).to_be_visible()))
    # the note on the preview's character count, opened from the keyboard
    sw.step("frames", "preview-tip", lambda: (
        # a draft to preview
        page.get_by_label("Frame text").fill("Preview frame. {{tracking_code}} {{client_prompt}}"),
        page.get_by_role("button", name="Preview", exact=True).click(),
        page.get_by_text(re.compile(r"^\d+ characters$")).focus(),
        expect(page.get_by_role("tooltip")).to_contain_text("Shown with the sample tracking code")))

    # the note on the History heading holds the lock history, which exists once the model's lock was toggled
    def lock_history_tip():
        # a lock needs an own frame (the server refuses one for a model that follows the default): save one first;
        # the list is behind the drawer, so the drawer is closed (clean after a save) for the checkbox and opened again
        page.get_by_label("Frame text").fill("Sweep frame. {{tracking_code}} {{client_prompt}}")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_role("button", name="Follow the default")).to_be_visible()     # the frame is now its own
        shut(page, MODEL_EDITOR)
        page.locator(VIDEO).get_by_label("Keep own frame of fake-video").first.check()
        expect(page.locator(VIDEO)).to_contain_text("locked")
        page.locator(VIDEO).get_by_role("button", name="Edit frame for fake-video").click()
        expect_dialog(page, MODEL_EDITOR)
        tip = page.get_by_role("heading", name="History").locator(".hint-tip")
        expect(tip).to_be_visible()
        tip.focus()
        expect(page.get_by_role("tooltip")).to_contain_text("Lock history:")
    sw.step("frames", "lock-history-tip", lock_history_tip)
    sw.step("frames", "editor-default", lambda: (
        shut(page, MODEL_EDITOR),
        page.locator(VIDEO).get_by_role("button", name="Edit the default").click(),
        expect_dialog(page, DEFAULT_EDITOR)))

    # something typed, then Escape: the question over the drawer; Discard ends the sweep of this tab with no drawer
    def discard_confirm():
        page.get_by_label("Frame text").fill("half typed {{tracking_code}} {{client_prompt}}")
        page.keyboard.press("Escape")
        expect(page.locator(".el-message-box")).to_be_visible()
        expect(page.locator(".el-message-box")).to_contain_text("Discard what you typed?")
    sw.step("frames", "editor-discard-confirm", discard_confirm, after=lambda: (
        page.locator(".el-message-box").get_by_role("button", name="Discard").click(),
        expect(page.get_by_role("dialog", name=DEFAULT_EDITOR)).to_have_count(0),
        expect(page.locator(".el-overlay:visible")).to_have_count(0)))
