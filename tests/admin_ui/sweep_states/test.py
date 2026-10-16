"""The Test tab: the form, the pending model load and the loaded models."""

from patchright.sync_api import expect

from . import TIMEOUT_MS, slow, tab, unslow

ROUTE = "test"
STATES = [("test", "form"), ("test", "load-pending"), ("test", "models-loaded")]


def run(page, app, live, sw, data):
    sw.step("test", "form", lambda: (tab(page, "test"),
                                     expect(page.get_by_role("textbox", name="API key", exact=True)).to_be_visible()))

    def load_busy():
        slow(page, "**/v1/models*")
        page.get_by_role("textbox", name="API key", exact=True).fill(data["raw_key"])
        page.get_by_role("button", name="Load models").click()
        expect(page.get_by_test_id("test-model")).to_have_count(0)     # the answer is still on its way
    sw.step("test", "load-pending", load_busy)
    unslow(page, "**/v1/models*")
    sw.step("test", "models-loaded", lambda: expect(page.get_by_test_id("test-model").filter(has_text="fake-video"))
            .to_be_visible(timeout=TIMEOUT_MS * 2))
