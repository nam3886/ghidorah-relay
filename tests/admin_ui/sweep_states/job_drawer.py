"""The job drawer: loading, its three tabs, and the regenerate confirm."""

import base64
import re

from patchright.sync_api import expect

from . import TIMEOUT_MS, cancel_box, dismiss_box, slow, unslow

ROUTE = "job_drawer"
STATES = [("jobs", "detail-loading"), ("jobs", "detail"), ("jobs", "detail-timeline"), ("jobs", "detail-technical"),
          ("jobs", "detail-regenerate-confirm"),
          # what the app keeps and the boards do not draw (D125): each is photographed on its own
          ("jobs", "detail-warnings"), ("jobs", "detail-rename"), ("jobs", "detail-more-actions"),
          ("jobs", "detail-revoke-confirm"), ("jobs", "detail-debug-files"), ("jobs", "detail-sent-text"),
          ("jobs", "detail-recover-done")]


# the drawer is named by its heading (the job's title), so it is found by its test id
def detail(page):
    # the role leaves a drawer that is closed (its hidden element stays in the page)
    return page.get_by_role("dialog").and_(page.get_by_test_id("job-drawer"))


def pick_tab(page, name):
    detail(page).get_by_role("tab", name=name, exact=True).click()
    expect(detail(page).get_by_role("tab", name=name, exact=True)).to_have_attribute("aria-selected", "true")


def close_drawer(page):
    """Close the drawer by its own button: Close on a drawer, Back on the phone sheet."""
    detail(page).get_by_role("button", name=re.compile(r"^(Close|Back)")).first.click()
    expect(detail(page)).to_have_count(0)


JOB = re.compile(r"/admin/jobs/[0-9a-f-]{36}$")
DEBUG = "**/admin/jobs/*/debug/0"
# The share link `tamper` adds is served a real picture: the tile loads it as it would any result, and no request of
# the sweep ends in a 404 (a 1x1 PNG).
SWEEP_LINK = "**/m/sweep-link"
PICTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
SENT = "Create exactly one image for this request, no on-screen text. Request: a perfume bottle on white marble."


def tamper(route):
    """The failed job as the server would answer it with the extras the boards do not draw: a warning, a failed
    webhook, a share link to revoke, a debug file and the text sent upstream."""
    response = route.fetch()
    data = response.json()
    data["warnings"] = [{"code": "upstream.slow", "text": "the provider answered late"}]
    data["callback"] = {"status": "failed", "attempts": 3, "last": None}
    data["sent_text"] = SENT
    data["debug_files"] = [{"idx": 0, "mime": "text/plain", "size": 64, "url": "/admin/jobs/x/debug/0"}]
    data["media"] = [*data["media"], {"role": "output", "idx": 7, "mime": "image/png", "size": 120, "width": 1024,
                                      "height": 1536, "duration_s": None, "url": "/m/sweep-link"}]
    route.fulfill(response=response, json=data)


def debug_file(route):
    route.fulfill(status=200, content_type="text/plain", body="GET /v1/images -> 422\n{\"refused\": \"content\"}")


def sweep_picture(route):
    route.fulfill(status=200, content_type="image/png", body=PICTURE)


def show(page, selector):
    """Bring a block of the drawer into view (the drawer scrolls inside the page)."""
    block = detail(page).locator(selector)
    expect(block).to_be_visible()
    block.scroll_into_view_if_needed()


def run(page, app, live, sw, data):
    failed = str(data["failed"])[:8]

    def detail_loading():
        pattern = re.compile(r"/admin/jobs/[0-9a-f-]{36}$")
        slow(page, pattern)
        page.locator("tbody tr, li").filter(has_text=failed).locator("a.title-link, .prow").first.click()
        expect(detail(page).locator("[data-state=loading]")).to_be_visible()
        expect(detail(page).locator("[data-section=conclusion]")).to_be_visible(timeout=TIMEOUT_MS * 2)
        unslow(page, pattern)
    sw.step("jobs", "detail-loading", detail_loading)
    sw.step("jobs", "detail", lambda: expect(detail(page).locator("[data-section=summary]")).to_be_visible())

    def detail_timeline():
        pick_tab(page, "Timeline")
        expect(detail(page).locator("[data-section=timeline]")).to_be_visible()
    sw.step("jobs", "detail-timeline", detail_timeline)

    def detail_technical():
        pick_tab(page, "Technical")
        expect(detail(page).locator("[data-section=technical]")).to_be_visible()
    sw.step("jobs", "detail-technical", detail_technical)

    def detail_regenerate():
        pick_tab(page, "Overview")
        detail(page).get_by_role("button", name="Regenerate…", exact=True).click()
        expect(cancel_box(page)).to_be_visible()
    sw.step("jobs", "detail-regenerate-confirm", detail_regenerate)
    dismiss_box(page)
    close_drawer(page)

    # ---- what the boards do not draw (D125) ----
    failed_title = page.locator("tbody tr, li").filter(has_text=failed).locator("a.title-link, .prow").first
    page.route(JOB, tamper)
    page.route(DEBUG, debug_file)
    page.route(SWEEP_LINK, sweep_picture)
    failed_title.click()
    expect(detail(page).locator("[data-section=conclusion]")).to_be_visible(timeout=TIMEOUT_MS * 2)
    sw.step("jobs", "detail-warnings", lambda: show(page, "[data-section=warnings]"))

    def rename():
        pick_tab(page, "Technical")
        detail(page).locator("[data-section=title] input").fill("Perfume bottle on marble")
        show(page, "[data-section=title]")
    sw.step("jobs", "detail-rename", rename)
    sw.step("jobs", "detail-more-actions", lambda: show(page, "[data-section=more-actions]"))

    def revoke_confirm():
        detail(page).get_by_role("button", name="Revoke share links").click()
        expect(page.locator(".el-popconfirm")).to_be_visible()
    sw.step("jobs", "detail-revoke-confirm", revoke_confirm,
            after=lambda: page.locator(".el-popconfirm").get_by_role("button", name="Cancel", exact=True).click())

    def debug_files():
        detail(page).get_by_role("button", name="File 0 (text/plain)").click()
        expect(detail(page).locator("[data-section=debug] pre")).to_be_visible()
        show(page, "[data-section=debug]")
    sw.step("jobs", "detail-debug-files", debug_files)
    sw.step("jobs", "detail-sent-text", lambda: show(page, "[data-section=sent]"))
    close_drawer(page)
    page.unroute(JOB, tamper)
    page.unroute(DEBUG, debug_file)
    page.unroute(SWEEP_LINK, sweep_picture)

    def recover_done():
        page.locator("tbody tr, li").filter(has_text="done").locator("a.title-link, .prow").first.click()
        expect(detail(page).locator("[data-section=conclusion]")).to_be_visible(timeout=TIMEOUT_MS * 2)
        pick_tab(page, "Technical")
        show(page, "[data-section=more-actions]")
        expect(detail(page).get_by_role("button", name="Recover", exact=True)).to_be_visible()
    sw.step("jobs", "detail-recover-done", recover_done)
    close_drawer(page)
