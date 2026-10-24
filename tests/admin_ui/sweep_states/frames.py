"""The Prompt frames tab: the page with its inline editor, the provider list, the preview and the two notes drawn on the
menu surface, the other frames of a kind and the question over a typed draft."""

import re

from patchright.sync_api import expect

from . import tab

ROUTE = "frames"
STATES = [("frames", "list"), ("frames", "provider-pick"), ("frames", "editor"), ("frames", "preview-tip"),
          ("frames", "lock-history-tip"), ("frames", "editor-default"), ("frames", "editor-discard-confirm")]

EDITOR = "[data-testid=frame-editor]"


def run(page, app, live, sw, data):
    def frames_list():
        tab(page, "frames")
        expect(page.locator(EDITOR)).to_be_visible()
        # the editor is inline: no drawer, no dialog
        expect(page.get_by_role("dialog")).to_have_count(0)
    sw.step("frames", "list", frames_list)
    # the provider is picked in the page's own list (chips on a tablet and a phone): one tab per provider, no All
    sw.step("frames", "provider-pick", lambda: (
        page.get_by_test_id("provider-tab-fake").click(),
        expect(page.get_by_test_id("provider-tab-all")).to_have_count(0),
        expect(page.get_by_role("navigation", name="Provider")).to_have_count(0),
        expect(page.locator(EDITOR)).to_be_visible()))
    sw.step("frames", "editor", lambda: (
        page.get_by_test_id("frame-models").get_by_role("button", name="Edit frame for fake-video").click(),
        expect(page.locator(".filter-select__value, .filter-field__value").first).to_contain_text("fake-video"),
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
        # a lock needs an own frame (the server refuses one for a model that follows the default): save one first
        page.get_by_label("Frame text").fill("Sweep frame. {{tracking_code}} {{client_prompt}}")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_role("button", name="Follow the default").first).to_be_enabled()   # the frame is now its own
        page.get_by_test_id("frame-models").get_by_label("Keep own frame of fake-video").first.check()
        expect(page.get_by_test_id("frame-models").locator("label[title^=Locked]")).to_have_count(1)
        tip = page.get_by_test_id("frame-history-card").locator(".hint-tip")
        expect(tip).to_be_visible()
        tip.focus()
        expect(page.get_by_role("tooltip")).to_contain_text("Lock history:")
    sw.step("frames", "lock-history-tip", lock_history_tip)
    sw.step("frames", "editor-default", lambda: (
        page.get_by_role("combobox", name="Frame for").click(),
        page.get_by_role("option", name=re.compile("^Default")).click(),
        expect(page.get_by_role("button", name="Apply to all models of this kind")).to_be_enabled()))

    # something typed, then another frame picked: the question over the editor; Discard ends the sweep of this tab
    def discard_confirm():
        page.get_by_label("Frame text").fill("half typed {{tracking_code}} {{client_prompt}}")
        page.get_by_test_id("frame-models").get_by_role("button", name="Edit frame for fake-video").click()
        expect(page.locator(".el-message-box")).to_be_visible()
        expect(page.locator(".el-message-box")).to_contain_text("Discard what you typed?")
    sw.step("frames", "editor-discard-confirm", discard_confirm, after=lambda: (
        page.locator(".el-message-box").get_by_role("button", name="Discard").click(),
        expect(page.locator(".el-message-box")).to_have_count(0)))
