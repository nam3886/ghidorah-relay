"""Settings, Prompt frames and the Test tab of the Vue admin in Chrome against the real app (browser tier). Every test
runs under the CSP watch of conftest (a violation on either channel fails it). The data is the fake provider's: the
`framed` fixture makes it serve prompt frames. Markup that a server string could carry must stay text; the Test tab's
API key must be in sessionStorage only, and gone after sign-out and a 401."""

import base64
import json
import re
import uuid

import pytest
from accountkit import new_account
from jobkit import key as new_key
from patchright.sync_api import TimeoutError as PlaywrightTimeout
from patchright.sync_api import expect
from pixel_capture import sideways
from test_admin_prices import fabricated, priced
from test_frame_plan import put

from relay_core.testing.fake_provider import FAKE_FRAMES
from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

TIMEOUT_MS = 10_000
VIDEO = '[data-frame-kind="video"]'
HOSTILE = '<img src=x onerror="window.__pwned=1"> {{tracking_code}} {{client_prompt}}'
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
SCRIPT_URL = "java" + "script:alert(1)"


def open_tab(page, name: str) -> None:
    page.get_by_role("tab", name=name).click()


# ---- Settings -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_settings_edit_marks_the_row_saves_and_reset_returns_to_the_default(app_session, db, color_scheme):
    page = app_session
    open_tab(page, "Settings")
    row = page.locator("#settings tbody tr", has_text="log_retention_days")
    source = row.locator(".status-chip")
    save = page.get_by_role("button", name="Save log_retention_days")
    expect(source).to_have_text("default")
    expect(save).to_be_disabled()
    page.get_by_label("Value of log_retention_days").fill("45")
    expect(row).to_contain_text("edited")
    save.click()
    expect(page.locator(".el-message")).to_contain_text("Saved")
    expect(source).to_have_text("db")
    expect(save).to_be_disabled()
    assert db.one("SELECT value FROM settings_kv WHERE key = 'log_retention_days'")[0] in (45, "45")
    page.get_by_role("button", name="Reset log_retention_days").click()
    expect(source).to_have_text("default")
    assert db.one("SELECT count(*) FROM settings_kv WHERE key = 'log_retention_days'")[0] == 0
    expect(page.get_by_text("BILLING_TZ")).to_be_visible()
    expect(page.get_by_text("No job has an upstream figure yet")).to_be_visible()


def test_settings_refuses_a_bad_value_with_the_server_text_and_the_field(app_session, db):
    page = app_session
    open_tab(page, "Settings")
    page.get_by_label("Value of log_retention_days").fill("not a number")
    page.get_by_role("button", name="Save log_retention_days").click()
    expect(page.locator(".el-message--error")).to_contain_text("(value)")
    assert db.one("SELECT count(*) FROM settings_kv WHERE key = 'log_retention_days'")[0] == 0
    page.problems.clear()                                    # the 400 is a console line


def test_a_wrong_current_password_shows_on_its_field_and_a_change_ends_every_session(app, app_session, live, db):
    page = app_session
    open_tab(page, "Settings")
    page.get_by_label("Current password").fill("not the password")
    page.get_by_label("New password").fill("another long passphrase " + uuid.uuid4().hex[:6])
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator('#password [role="alert"]')).to_have_count(1)
    expect(page).to_have_url(re.compile(r"#/settings$"))
    page.problems.clear()                                    # the 400 is a console line

    token = page.evaluate("sessionStorage.getItem('relay_admin_token')")
    other = page.request.post(live.url + "/admin/login", data={"username": live.user, "password": live.password})
    assert other.ok, "a second session exists"
    page.get_by_label("Current password").fill(live.password)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page).to_have_url(re.compile(r"#/login$"))
    assert db.one("SELECT count(*) FROM admin_sessions")[0] == 0
    assert page.evaluate("sessionStorage.getItem('relay_admin_token')") is None
    assert page.request.get(live.url + "/admin/me", headers={"X-Admin-Session": token}).status == 401
    assert page.request.get(live.url + "/admin/me", headers={"X-Admin-Session": other.json()["token"]}).status == 401


@fabricated
def test_measured_prices_are_read_from_the_real_route_and_follow_the_provider_filter(app_session, db):
    k = new_key(db)
    priced(db, k, price=10, upstream=8, duration=5, model="fake-video")
    priced(db, k, price=10, upstream=10, duration=5, model="fake-video")
    priced(db, k, price=3, upstream=3, duration=None, model="other-image", provider="other")
    page = app_session
    with page.expect_response(lambda r: r.url.endswith("/admin/prices")) as asked:
        open_tab(page, "Settings")
    assert asked.value.status == 200
    items = asked.value.json()["items"]
    assert [(i["provider"], i["model"], i["duration"], i["charged"], i["jobs"], i["disagreed"], i["lowest_upstream"])
            for i in items] == [("fake", "fake-video", 5, 10, 2, 1, 8), ("other", "other-image", None, 3, 1, 0, None)]
    rows = page.locator("#prices tbody tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("fake-video")
    expect(rows.nth(0)).to_contain_text("5 s")
    expect(rows.nth(1)).to_contain_text("other-image")

    with page.expect_response(lambda r: r.url.endswith("/admin/prices?provider=fake")) as scoped:
        page.get_by_test_id("provider-filter").click()
        page.get_by_role("option", name="fake", exact=True).click()
    assert [i["provider"] for i in scoped.value.json()["items"]] == ["fake"]
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("fake-video")
    expect(page.get_by_text("other-image")).to_have_count(0)


# ---- Prompt frames --------------------------------------------------------------------------------------------------

def frame_texts(db, model):
    """The stored texts of a video target, oldest first (model None = the kind's default)."""
    return [r[0] for r in db.all("SELECT text FROM prompt_frames WHERE provider = 'fake' AND kind = 'video' AND "
                                 "model IS NOT DISTINCT FROM %s::text ORDER BY id", model)]


def frames_page(page, db, own: str | None = None):
    """A signed-in Prompt frames tab: the video and image defaults, and optionally an own frame for fake-video."""
    put(db, "video", None, FAKE_FRAMES.defaults["video"])
    put(db, "image", None, FAKE_FRAMES.defaults["image"])
    if own:
        put(db, "video", "fake-video", own)
    open_tab(page, "Prompt frames")
    video = page.locator(VIDEO)
    expect(video).to_be_visible()
    return video, video.locator("tbody tr", has_text="fake-video"), page.get_by_label("Frame text"), \
        page.get_by_role("dialog").get_by_role("heading", level=2)


SETTLED_BOXES_JS = """async () => {
  const box = (el) => { const r = el.getBoundingClientRect(); return {y: r.y, bottom: r.bottom, height: r.height}; };
  const toast = document.querySelector('.el-message');
  const foot = document.querySelector('.detail-drawer__foot');
  let last = -1;
  for (let i = 0; i < 40; i++) {                     // frames: the toast slides in, then holds still
    const now = toast.getBoundingClientRect().bottom;
    if (now === last) break;
    last = now;
    await new Promise((ok) => requestAnimationFrame(ok));
    await new Promise((ok) => requestAnimationFrame(ok));
  }
  return {toast: box(toast), foot: box(foot)};
}"""


def press(page, button) -> None:
    """Click a footer button and nothing more: the toast of the last write is above the footer (own assertions
    check that), so no wait for it is needed."""
    button.click()


def close_editor(page) -> None:
    """Close the editor drawer by its X (a clean editor closes at once) and wait until it is gone: the list behind it
    cannot be reached while it is open."""
    page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^(Close|Back)")).last.click()   # Back on a sheet
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_frames_save_refuse_preview_roll_back_follow_reset_lock_and_apply_to_all(app_session, db, framed):
    page = app_session
    default = FAKE_FRAMES.defaults["video"]
    video, row, text, heading = frames_page(page, db)
    source = row.locator(".status-chip")
    save = page.get_by_role("button", name="Save", exact=True)
    expect(source).to_have_text("default")
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    expect(text).to_have_value(default)
    expect(page.get_by_text("This model follows the default.")).to_be_visible()
    expect(save).to_be_disabled()

    # a save with a warning: saved, the warning shown, the model has its own frame
    first = "Own words. {{tracking_code}} {{client_prompt}}"
    text.fill(first)
    expect(save).to_be_enabled()
    press(page, save)
    expect(page.get_by_test_id("frame-warnings")).to_contain_text("asks for no single result")
    # the toast of the save sits above the footer: it covers none of its buttons
    expect(page.locator(".el-message").first).to_be_visible()
    boxes = page.evaluate(SETTLED_BOXES_JS, isolated_context=False)
    assert boxes["toast"]["bottom"] <= boxes["foot"]["y"] + 1, boxes
    expect(source).to_have_text("own")
    assert frame_texts(db, "fake-video") == [first]
    expect(save).to_be_disabled()

    # a refused save shows its param beside the editor and stores nothing
    text.fill("no placeholders at all")
    press(page, save)
    refusal = page.locator("#frame-refusal")
    expect(refusal).to_have_text(re.compile(r"\(\{\{(client_prompt|tracking_code)\}\}\)$"))
    assert frame_texts(db, "fake-video") == [first]
    page.problems.clear()                                    # the 4xx of a refused save is a console line

    # the preview renders the draft, with its length, and stores nothing
    text.fill("Draft single. {{tracking_code}} {{client_prompt}}")
    press(page, page.get_by_role("button", name="Preview", exact=True))
    shown = page.get_by_test_id("frame-preview")
    expect(shown).to_contain_text("Draft single.")
    expect(shown).to_contain_text("a cat on a sofa")
    expect(page.get_by_text(re.compile(r"^\d+ characters$"))).to_be_visible()
    # what the preview was made with is a note on the menu surface, reached by the keyboard as well as the pointer
    count = page.get_by_text(re.compile(r"^\d+ characters$"))
    expect(count).not_to_have_attribute("title", re.compile(".*"))
    count.focus()
    expect(page.get_by_role("tooltip")).to_have_text("Shown with the sample tracking code (draft frame).")
    page.keyboard.press("Escape")                            # Escape dismisses it while the focus stays on the text
    expect(page.get_by_role("tooltip")).to_have_count(0)
    expect(count).to_be_focused()
    assert frame_texts(db, "fake-video") == [first]
    # a refused preview (a blank sample prompt) shows its refusal and clears the old render
    page.get_by_label("Sample prompt").fill("")
    press(page, page.get_by_role("button", name="Preview", exact=True))
    expect(refusal).to_contain_text("(prompt)")
    expect(shown).to_have_count(0)
    page.problems.clear()
    page.get_by_label("Sample prompt").fill("a cat on a sofa")

    # a second version, then a rollback to the first
    second = "Second single. {{tracking_code}} {{client_prompt}}"
    text.fill(second)
    press(page, save)
    history = page.get_by_test_id("frame-history").locator("li")
    expect(history).to_have_count(2)
    expect(page.get_by_test_id("frame-warnings")).to_have_count(0)
    # only an older version has a Roll back: the first text is the one below the current
    history.get_by_role("button", name=re.compile("Roll back")).click()
    expect(history).to_have_count(3)
    expect(text).to_have_value(first)
    assert frame_texts(db, "fake-video") == [first, second, first]
    assert db.one("SELECT restored_from FROM prompt_frames WHERE model = 'fake-video' ORDER BY id DESC LIMIT 1")[0]

    # follow the default again
    press(page, page.get_by_role("button", name="Follow the default"))
    expect(source).to_have_text("default")
    assert frame_texts(db, "fake-video")[-1] is None

    # the default differs from the shipped one: the flag, then the reset
    close_editor(page)
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("Editing video / default")
    text.fill("Changed single. {{tracking_code}} {{client_prompt}}")
    press(page, page.get_by_role("button", name="Preview", exact=True))      # a preview from the default's editor
    expect(shown).to_contain_text("Changed single.")
    press(page, save)
    flag = video.get_by_text("differs from the shipped default")
    expect(flag).to_be_visible()
    close_editor(page)
    video.get_by_role("button", name="Reset to the shipped default").click()
    expect(flag).to_have_count(0)
    assert frame_texts(db, None)[-1] == default

    # a lock keeps a model's own text when a default is applied to all; unlocked, it takes it. The list is behind the
    # drawer while an editor is open, so each lock is toggled with the drawer shut.
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    mine = "Mine single. {{tracking_code}} {{client_prompt}}"
    text.fill(mine)
    press(page, save)
    expect(source).to_have_text("own")
    close_editor(page)
    row.get_by_role("checkbox").click()
    expect(row).to_contain_text(re.compile(r"locked \d\d/\d\d by"))
    video.get_by_role("button", name="Edit the default").click()
    everywhere = "All single. {{tracking_code}} {{client_prompt}}"
    text.fill(everywhere)
    press(page, page.get_by_role("button", name="Apply to all models of this kind"))
    applied = page.get_by_test_id("frame-applied")
    expect(applied).to_contain_text("Applied: -. Kept (locked): fake-video.")
    assert frame_texts(db, "fake-video")[-1] == mine
    close_editor(page)
    row.get_by_role("checkbox").click()
    expect(row).to_contain_text(re.compile(r"unlocked \d\d/\d\d by"))
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("Editing video / default")
    text.fill(everywhere)
    press(page, page.get_by_role("button", name="Apply to all models of this kind"))
    expect(applied).to_contain_text("Applied: fake-video.")
    assert frame_texts(db, "fake-video")[-1] == everywhere
    text.fill(everywhere)                                    # the same text again: the model already has it
    press(page, page.get_by_role("button", name="Apply to all models of this kind"))
    expect(applied).to_contain_text("Applied: -. Kept (locked): -. Unchanged: fake-video.")


def test_a_late_answer_for_an_editor_that_was_closed_is_dropped_and_the_drawer_opens_at_once(
        app_session, db, framed):
    page = app_session
    video, row, text, heading = frames_page(page, db, own="Own of A single. {{tracking_code}} {{client_prompt}}")
    default = FAKE_FRAMES.defaults["video"]

    held = []

    def hold(route):
        if route.request.method == "GET" and not held:
            held.append(route)
        else:
            route.continue_()

    page.route(re.compile(r"/admin/frames/fake/video/fake-video(\?|$)"), hold)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    for _ in range(250):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "the request was not sent"
    # Edit gives its answer at once: the drawer is open and says it is loading; nothing can be written yet
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Loading the frame")
    expect(text).to_have_count(0)
    expect(page.get_by_role("button", name="Save", exact=True)).to_have_count(0)
    # the operator shuts it before the answer comes, and opens the default; the late answer must change nothing
    close_editor(page)
    held[0].continue_()
    page.wait_for_timeout(500)
    expect(page.get_by_role("dialog")).to_have_count(0)
    video.get_by_role("button", name="Edit the default").click()
    expect(heading).to_have_text("Editing video / default")
    expect(text).to_have_value(default)


def test_the_frames_page_opens_with_no_drawer_and_a_typed_draft_asks_before_it_is_lost(app_session, db, framed):
    page = app_session
    video, row, text, heading = frames_page(page, db, own="Own of A single. {{tracking_code}} {{client_prompt}}")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.get_by_text("Pick a model or a default")).to_have_count(0)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    # a clean editor closes at once; one with typing in it asks, and Keep editing leaves it as typed
    typed = "Typed single. {{tracking_code}} {{client_prompt}}"
    text.fill(typed)
    page.keyboard.press("Escape")
    box = page.locator(".el-message-box")
    expect(box).to_contain_text("Discard what you typed?")
    page.keyboard.press("Escape")
    expect(box).to_have_count(0)
    expect(text).to_have_value(typed)
    # leaving the page with the draft asks too (the tabs are behind the scrim: the address changes, as a browser Back
    # does), and Keep editing stays on the page with the draft
    page.evaluate("window.location.hash = '#/jobs'", isolated_context=False)
    expect(box).to_contain_text("Discard what you typed?")
    box.get_by_role("button", name="Keep editing").click()
    expect(box).to_have_count(0)
    expect(text).to_have_value(typed)
    # Discard shuts it; coming back to the page, no drawer is open and the provider stays
    page.keyboard.press("Escape")
    box.get_by_role("button", name="Discard").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    open_tab(page, "Jobs")
    open_tab(page, "Prompt frames")
    expect(video).to_be_visible()
    expect(page.get_by_role("dialog")).to_have_count(0)
    # a draft left in an open drawer is not restored when the operator comes back (they chose to discard to leave)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    expect(text).to_have_value("Own of A single. {{tracking_code}} {{client_prompt}}")
    assert db.one("SELECT count(*) FROM prompt_frames WHERE model = 'fake-video'")[0] == 1


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (768, 1024), (390, 844)])
def test_the_editor_drawer_is_named_by_its_heading_keeps_its_buttons_in_the_footer_and_gives_focus_back(
        app_session, db, framed, width, height):
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    video, _row, text, heading = frames_page(page, db, own="Own of A single. {{tracking_code}} {{client_prompt}}")
    opener = page.get_by_role("button", name="Edit frame for fake-video")     # a table row, or a card on a phone
    opener.focus()
    opener.click()
    drawer = page.get_by_role("dialog", name="Editing video / fake-video")
    expect(drawer).to_be_visible()
    expect(text).to_have_value("Own of A single. {{tracking_code}} {{client_prompt}}")
    # the list runs across the page: it is not squeezed beside an editor
    assert video.bounding_box()["width"] > width - 200
    footer = drawer.locator(".detail-drawer__foot")
    names = [b.inner_text().strip() for b in footer.get_by_role("button").all()]
    assert names == ["Save", "Follow the default", "Preview"], names
    assert drawer.locator(".detail-drawer__body").get_by_role("button", name="Save", exact=True).count() == 0
    if width < 768:
        # the phone sheet: a Back button to the page and a footer of 44 px buttons
        expect(drawer.get_by_role("button", name="Back to Prompt frames")).to_be_visible()
        assert all(b.bounding_box()["height"] >= 44 for b in footer.get_by_role("button").all())
    assert sideways(page) <= 0
    # a clean editor closes on Escape and the page gets its focus back on the button that opened it
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(opener).to_be_focused()


TOAST_AND_FOOTER_JS = """() => {
  const toast = document.querySelector('.el-message');
  const foot = document.querySelector('.detail-drawer__foot');
  const bottom = (el) => el.getBoundingClientRect().bottom;
  return {
    toast: toast ? bottom(toast) : null,
    footTop: foot ? foot.getBoundingClientRect().top : null,
    fromBottom: toast ? innerHeight - bottom(toast) : null,
  };
}"""


GAP_JS = """([lo, hi]) => {
  const t = document.querySelector('.el-message'), f = document.querySelector('.detail-drawer__foot');
  if (!t || !f) return false;
  const gap = f.getBoundingClientRect().top - t.getBoundingClientRect().bottom;
  return gap >= lo && gap <= hi;
}"""


def toast_settles_above_footer(page, what: str) -> dict:
    """Wait until the toast sits 16 px (+-2) above the footer: the slide has settled. Then read both boxes."""
    try:
        page.wait_for_function(GAP_JS, arg=[14, 18], timeout=3000)
    except PlaywrightTimeout as err:
        raise AssertionError("the toast did not " + what) from err
    boxes = page.evaluate(SETTLED_BOXES_JS, isolated_context=False)
    gap = boxes["foot"]["y"] - boxes["toast"]["bottom"]
    assert 14 <= gap <= 18, ("the gap above the footer", gap, boxes)
    return boxes


WATCH = "apps/admin-web/src/lib/notify.ts"


@mutation(WATCH, "watchDrawerFooter();\n\nconst base", "const base", expect="the toast did not slide above the footer")
@pytest.mark.parametrize(("width", "height", "default_gap"), [(1440, 900, 16), (390, 844, 72)])
def test_a_toast_showing_when_a_drawer_opens_slides_above_its_footer_and_back_when_it_closes(
        app_session, db, framed, width, height, default_gap):
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    video, row, text, heading = frames_page(page, db, own="Own single. {{tracking_code}} {{client_prompt}}")
    # an error toast (5 s) from a lock the server refuses, raised with no drawer on the page
    page.route(re.compile(r"/admin/frames/fake/video/fake-video/lock$"), lambda route: route.fulfill(
        status=500, content_type="application/json",
        body='{"error": {"code": "server.error", "message": "lock failed"}}'))
    page.get_by_label("Keep own frame of fake-video").first.click()
    expect(page.locator(".el-message--error")).to_be_visible()
    page.problems.clear()                                    # the 500 is a console line
    page.wait_for_function("""(gap) => {                  // it slides in, then sits at its default place
      const t = document.querySelector('.el-message');
      return t && Math.abs(innerHeight - t.getBoundingClientRect().bottom - gap) <= 6;
    }""", arg=default_gap, timeout=3000)
    # the drawer opens while the toast is showing: the toast slides above the footer, none closes early
    video.get_by_role("button", name="Edit the default").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    toast_settles_above_footer(page, "slide above the footer")
    # the drawer closes: the toast comes back down to its default place
    close_editor(page)
    page.wait_for_function("""(gap) => {
      const t = document.querySelector('.el-message');
      return t && Math.abs(innerHeight - t.getBoundingClientRect().bottom - gap) <= 6;
    }""", arg=default_gap, timeout=3000)
    back = page.evaluate(TOAST_AND_FOOTER_JS, isolated_context=False)
    assert back["footTop"] is None and abs(back["fromBottom"] - default_gap) <= 6, back


@mutation(WATCH, "if (foot) sizes?.observe(foot);", "", expect="the toast did not follow the footer that grew")
def test_a_toast_follows_a_drawer_footer_that_grows(app_session, db, framed):
    page = app_session
    video, _row, text, _heading = frames_page(page, db)
    video.get_by_role("button", name="Edit the default").click()
    expect(text).to_be_visible()
    text.fill("Grow single. {{tracking_code}} {{client_prompt}}")
    page.route(re.compile(r"/admin/frames/fake/video/default$"), lambda route: route.fulfill(
        status=500, content_type="application/json",
        body='{"error": {"code": "server.error", "message": "save failed"}}')
        if route.request.method == "PUT" else route.continue_())
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".el-message--error")).to_be_visible()
    page.problems.clear()                                    # the 500 is a console line
    before = toast_settles_above_footer(page, "slide above the footer")
    # the footer grows by script (a CSSOM write, as a wrapping footer would): the toast follows it
    page.evaluate("document.querySelector('.detail-drawer__foot').style.minHeight = '160px'", isolated_context=False)
    after = toast_settles_above_footer(page, "follow the footer that grew")
    assert after["foot"]["height"] > before["foot"]["height"] + 40, (before, after)


def test_on_a_phone_the_toast_after_a_save_does_not_cover_the_default_editors_footer(app_session, db, framed):
    page = app_session
    page.set_viewport_size({"width": 390, "height": 844})
    video, _row, text, _heading = frames_page(page, db)
    video.get_by_role("button", name="Edit the default").click()
    expect(text).to_be_visible()
    text.fill("Phone single. {{tracking_code}} {{client_prompt}}")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".el-message").first).to_be_visible()
    boxes = page.evaluate(SETTLED_BOXES_JS, isolated_context=False)
    # the sheet footer holds three buttons and wraps to two rows (117 px at 390): the toast clears all of it
    assert boxes["foot"]["height"] > 100, boxes
    assert boxes["toast"]["bottom"] <= boxes["foot"]["y"] + 1, boxes


def test_follow_the_default_and_roll_back_over_an_unsaved_draft_ask_first(app_session, db, framed):
    page = app_session
    older = "Older single. {{tracking_code}} {{client_prompt}}"
    put(db, "video", "fake-video", older)
    video, row, text, heading = frames_page(page, db, own="Newer single. {{tracking_code}} {{client_prompt}}")
    box = page.locator(".el-message-box")
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    before = frame_texts(db, "fake-video")
    typed = "Typed single. {{tracking_code}} {{client_prompt}}"
    text.fill(typed)
    # Roll back: Keep editing changes nothing, Discard rolls back
    roll = page.get_by_test_id("frame-history").get_by_role("button", name=re.compile("Roll back")).first
    roll.click()
    expect(box).to_contain_text("Discard what you typed?")
    box.get_by_role("button", name="Keep editing").click()
    expect(box).to_have_count(0)
    expect(text).to_have_value(typed)
    assert frame_texts(db, "fake-video") == before
    roll.click()
    box.get_by_role("button", name="Discard").click()
    expect(text).to_have_value(older)
    assert len(frame_texts(db, "fake-video")) == len(before) + 1
    # Follow the default: the same question; a clean draft acts at once
    text.fill(typed)
    press(page, page.get_by_role("button", name="Follow the default"))
    expect(box).to_contain_text("Discard what you typed?")
    box.get_by_role("button", name="Keep editing").click()
    expect(box).to_have_count(0)
    expect(text).to_have_value(typed)
    assert frame_texts(db, "fake-video")[-1] == older
    page.get_by_role("button", name="Follow the default").click()
    box.get_by_role("button", name="Discard").click()
    expect(row.locator(".status-chip")).to_have_text("default")
    assert frame_texts(db, "fake-video")[-1] is None
    expect(box).to_have_count(0)


def test_a_click_on_the_scrim_acts_like_escape(app_session, db, framed):
    page = app_session
    video, row, text, heading = frames_page(page, db, own="Own of A single. {{tracking_code}} {{client_prompt}}")
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    page.mouse.click(20, 300)                                # beside the drawer: on the scrim
    expect(page.get_by_role("dialog")).to_have_count(0)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    text.fill("Typed single. {{tracking_code}} {{client_prompt}}")
    page.mouse.click(20, 300)
    box = page.locator(".el-message-box")
    expect(box).to_contain_text("Discard what you typed?")
    box.get_by_role("button", name="Keep editing").click()
    expect(box).to_have_count(0)
    expect(text).to_have_value("Typed single. {{tracking_code}} {{client_prompt}}")
    page.mouse.click(20, 300)
    expect(box).to_contain_text("Discard what you typed?")
    box.get_by_role("button", name="Discard").click()
    expect(box).to_have_count(0)
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_frame_text_with_markup_is_text_in_the_editor_the_history_and_the_preview(app_session, db, framed):
    page = app_session
    video, row, text, heading = frames_page(page, db)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(heading).to_have_text("Editing video / fake-video")
    text.fill(HOSTILE)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_test_id("frame-preview")).to_contain_text("<img src=x")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(text).to_have_value(HOSTILE)
    page.wait_for_timeout(300)                               # a script in the markup would have run by now
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "markup ran as a script"
    assert page.locator(".frames img").count() == 0, "planted markup became an element"


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_a_provider_without_frames_says_the_core_frame_applies_and_the_provider_select_lists_it(
        app_session, db, color_scheme):
    page = app_session
    open_tab(page, "Prompt frames")
    expect(page.get_by_text("This provider has no prompt frames: the core frame applies.")).to_be_visible()
    page.get_by_role("combobox", name="Provider", exact=True).click()
    expect(page.get_by_role("option", name="fake", exact=True)).to_have_count(1)
    page.problems.clear()                                    # the 404 is a console line


# ---- Test tab -------------------------------------------------------------------------------------------------------

OPEN_STORE = """const db = await new Promise((ok, no) => {
            const r = indexedDB.open('relay-admin-test', 1);
            r.onupgradeneeded = () => r.result.createObjectStore('kv');
            r.onsuccess = () => ok(r.result);
            r.onerror = no;
        });"""
SEED_STORE = """async (kind) => {
        const item = (name, type, size) => ({ name, type, blob: new Blob([new Uint8Array(size)], { type }) });
        const list = kind === 'many' ? Array.from({ length: 12 }, (_, i) => item('p' + i, 'image/png', 1024))
            : kind === 'big' ? [item('big.png', 'image/png', 13 * 1048576)]
            : kind === 'broken' ? { not: 'a list' }
            : [item('p.png', 'image/png', 1024), item('x.html', 'text/html', 3)];
        %s
        await new Promise((ok, no) => {
            const tx = db.transaction('kv', 'readwrite');
            tx.objectStore('kv').put(list, 'files');
            tx.oncomplete = ok;
            tx.onerror = no;
        });
        db.close();
    }""" % OPEN_STORE
READ_STORE = """async () => {
        %s
        const keys = await new Promise((ok) => {
            const q = db.transaction('kv').objectStore('kv').getAllKeys();
            q.onsuccess = () => ok(q.result);
        });
        const list = await new Promise((ok) => {
            const q = db.transaction('kv').objectStore('kv').get('files');
            q.onsuccess = () => ok(q.result || []);
        });
        db.close();
        return { keys, types: Array.isArray(list) ? list.map((f) => f.blob.type) : 'not a list' };
    }""" % OPEN_STORE


def key_box(page):
    """The API key input of the Test tab (the exact label: the tab 'API keys' also matches a loose one)."""
    return page.get_by_role("textbox", name="API key", exact=True)


def open_test_tab(page, db, live):
    """Make a key and an account for the fake provider, open the Test tab and load the models with the key."""
    new_account(db, checked=True)
    api_key = live.call(live.env.new_key())
    open_tab(page, "Test")
    key_box(page).fill(api_key.raw)
    page.get_by_role("button", name="Load models").click()
    expect(page.get_by_test_id("test-model").filter(has_text="fake-video")).to_be_visible()
    return api_key


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_test_tab_generates_keeps_form_and_images_over_a_reload_and_the_key_only_per_browser_tab(
        app, app_session, live, db, color_scheme):
    page = app_session
    api_key = open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    page.get_by_label("Prompt", exact=True).fill("a cat")
    page.get_by_label("Title", exact=True).fill("kept title")
    page.get_by_test_id("attach-input").set_input_files(
        files=[{"name": "ref.png", "mimeType": "image/png", "buffer": PNG}])
    items = page.get_by_test_id("attachment")
    expect(items).to_have_count(1)
    expect(items).to_contain_text("ref.png")
    expect(items.locator("img")).to_have_count(0)            # names only: no thumbnail
    page.get_by_role("button", name="Generate", exact=True).click()
    expect(page.locator(".test-result__status")).to_contain_text("Job")
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'a cat'")[0] == 1

    # the key: sessionStorage only, never localStorage, cookies or IndexedDB
    assert api_key.raw not in page.evaluate("JSON.stringify(Object.assign({}, localStorage))")
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") == api_key.raw
    assert api_key.raw not in json.dumps(page.context.cookies())
    assert api_key.raw not in page.url
    stored = page.evaluate(READ_STORE, isolated_context=False)
    assert stored == {"keys": ["files"], "types": ["image/png"]}

    # a reload restores the form, the images, the key and the picked model
    page.reload()
    expect(page.get_by_label("Prompt", exact=True)).to_have_value("a cat")
    expect(page.get_by_label("Title", exact=True)).to_have_value("kept title")
    items = page.get_by_test_id("attachment")
    expect(items).to_have_count(1)
    expect(items).to_contain_text("ref.png")
    expect(items.locator("img")).to_have_count(0)            # names only: no thumbnail
    expect(key_box(page)).to_have_value(api_key.raw)
    expect(page.get_by_role("heading", level=2, name="fake-video")).to_be_visible()
    # Clear form empties the form, the picked model and the stored images, also after a reload
    page.get_by_role("button", name="Clear form").click()
    expect(page.get_by_label("Prompt", exact=True)).to_have_value("")
    expect(page.get_by_role("heading", level=2, name="Pick a model")).to_be_visible()
    expect(page.get_by_role("button", name="Generate", exact=True)).to_be_disabled()
    page.reload()
    expect(page.get_by_label("Prompt", exact=True)).to_have_value("")
    expect(page.get_by_test_id("attachment")).to_have_count(0)
    assert page.evaluate("localStorage.getItem('relay_test_form')") is None or \
        json.loads(page.evaluate("localStorage.getItem('relay_test_form')")).get("prompt") == ""
    assert page.evaluate(READ_STORE, isolated_context=False)["types"] == []

    # Forget key, then a sign-out: the key is gone from the browser tab
    expect(key_box(page)).to_have_value(api_key.raw)
    page.get_by_role("button", name="Forget key").click()
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") is None
    expect(page.get_by_test_id("test-model")).to_have_count(0)      # Forget key takes the loaded models with it
    key_box(page).fill(api_key.raw)
    page.get_by_role("button", name="Load models").click()
    expect(page.get_by_test_id("test-model").filter(has_text="fake-video")).to_be_visible()
    # the key is stored when the load answers: wait for it, never read it the moment the models show
    page.wait_for_function("k => sessionStorage.getItem('relay_test_key') === k", arg=api_key.raw)
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") == api_key.raw
    app.sign_out(page)
    expect(page).to_have_url(re.compile(r"#/login$"))
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") is None
    assert api_key.raw not in page.evaluate("JSON.stringify(Object.assign({}, localStorage))")


def test_a_refused_key_is_not_stored_and_a_401_of_the_admin_session_clears_a_stored_one(app_session, live, db):
    page = app_session
    open_tab(page, "Test")
    key_box(page).fill("rk-not-a-real-key")
    page.get_by_role("button", name="Load models").click()
    expect(page.locator(".test-result__status")).to_contain_text(":")
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") is None
    page.problems.clear()                                    # the refused key is a console line
    api_key = open_test_tab(page, db, live)
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") == api_key.raw
    # the admin session ends: any admin call answers 401, the app goes to login and the key goes with it
    page.evaluate("sessionStorage.setItem('relay_admin_token', 'stale')")
    open_tab(page, "Settings")                               # the view's own first call is an admin call
    expect(page).to_have_url(re.compile(r"#/login$"))
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") is None
    page.problems.clear()                                    # the 401 is a console line


@pytest.mark.parametrize("kind, left", [("many", ["image/png"] * 10), ("big", []), ("html", ["image/png"]),
                                         ("broken", "not a list")])
def test_a_stored_image_set_over_the_limits_or_with_a_non_image_is_not_restored_and_is_cleaned(
        app_session, kind, left):
    page = app_session
    # seeded while no Test tab is open, so that its restore is the one that finds the stored set
    page.evaluate(SEED_STORE, kind, isolated_context=False)
    open_tab(page, "Test")
    expect(page.get_by_role("button", name="Load models")).to_be_visible()
    expect(page.get_by_test_id("attachment")).to_have_count(len(left) if isinstance(left, list) else 0)
    expect(page.get_by_test_id("attach-note")).to_contain_text("broke the limits")
    stored = page.evaluate(READ_STORE, isolated_context=False)
    if isinstance(left, list):
        assert stored["types"] == left, "the store is cleaned: %s" % kind
    else:
        assert stored["types"] == [], "a non-array value is dropped whole"


def test_the_test_tab_still_works_when_localstorage_and_indexeddb_are_blocked(app_session, live, db):
    page = app_session
    page.add_init_script("""
        for (const name of ['localStorage', 'indexedDB']) {
            Object.defineProperty(window, name, { get() { throw new DOMException('blocked', 'SecurityError'); } });
        }""")
    open_tab(page, "Test")
    page.reload()
    api_key = open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    page.get_by_label("Prompt", exact=True).fill("a dog")
    page.get_by_test_id("attach-input").set_input_files(
        files=[{"name": "ref.png", "mimeType": "image/png", "buffer": PNG}])
    expect(page.get_by_test_id("attach-note")).to_contain_text(
        "The attached images could not be saved in this browser: they are lost on reload.")
    page.get_by_role("button", name="Generate", exact=True).click()
    expect(page.locator(".test-result__status")).to_contain_text("Job")
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'a dog'")[0] == 1
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") == api_key.raw


def offered_then_pick(page, field: str, offered: list, option: str) -> None:
    """Open one of the Test form's selects (the design's select, drawn by us), check what it offers, pick `option`."""
    page.get_by_role("combobox", name=field, exact=True).click()
    options = page.get_by_role("listbox").get_by_role("option")
    expect(options).to_have_text(offered)
    options.filter(has_text=option).first.click()


def test_a_ratio_labelled_in_words_is_offered_and_sent_by_its_value(app_session, live, db):
    """dola labels each ratio with a sentence around its value and takes only the value: the tab lists the value and
    the request carries it."""
    page = app_session
    for name in live.env.registry.names():
        live.env.registry.spec(name).extras = lambda snapshots: {
            "ratios": [{"label": "Widescreen, for most screens (16:9)", "value": "16:9"}]}
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    offered_then_pick(page, "Ratio", ["Default", "16:9"], "16:9")
    page.get_by_label("Prompt", exact=True).fill("a cat")
    with page.expect_request(lambda r: r.method == "POST" and "/generations" in r.url) as sent:
        page.get_by_role("button", name="Generate", exact=True).click()
    assert sent.value.post_data_json["ratio"] == "16:9"
    expect(page.locator(".test-result__status")).to_contain_text("Job")


def test_a_video_model_offers_its_own_ratios_21_9_and_not_2_3_and_sends_the_one_picked(app_session, live, db):
    """dola's options hold its image ratios (2:3, no 21:9) for every model; each model names its own list in the
    catalog, and the tab offers that one."""
    import dataclasses

    from relay_core.providers import check_catalog

    page = app_session
    video = ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9")
    for name in live.env.registry.names():
        spec = live.env.registry.spec(name)
        listed = spec.catalog()
        spec.catalog = lambda listed=listed: [
            dataclasses.replace(m, limits={**m.limits, "ratios": list(video), "default_ratio": "16:9"})
            if m.media_type == "video" else m for m in listed]
        spec.extras = lambda snapshots: {
            "ratios": [{"value": r} for r in ("1:1", "2:3", "3:4", "4:3", "9:16", "16:9")]}
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    offered_then_pick(page, "Ratio", ["Default", *video], "21:9")
    page.get_by_label("Prompt", exact=True).fill("a cat")
    with page.expect_request(lambda r: r.method == "POST" and "/generations" in r.url) as sent:
        page.get_by_role("button", name="Generate", exact=True).click()
    assert sent.value.post_data_json["ratio"] == "21:9"
    expect(page.locator(".test-result__status")).to_contain_text("Job")


def test_a_style_is_shown_by_its_label_and_sent_by_its_value(app_session, live, db):
    """dola names a style in words and takes only its value: the tab shows the words and the request carries the
    value."""
    page = app_session
    for name in live.env.registry.names():
        live.env.registry.spec(name).extras = lambda snapshots: {
            "styles": [{"label": "Oil painting", "value": "oil_painting"}]}
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-image").click()
    offered_then_pick(page, "Style", ["Default", "Oil painting"], "Oil painting")
    page.get_by_label("Prompt", exact=True).fill("a cat")
    with page.expect_request(lambda r: r.method == "POST" and "/generations" in r.url) as sent:
        page.get_by_role("button", name="Generate", exact=True).click()
    assert sent.value.post_data_json["style"] == "oil_painting"
    expect(page.locator(".test-result__status")).to_contain_text("Job")


def test_generate_polls_to_a_result_draws_a_share_link_and_shows_any_other_link_as_text(app_session, live, db):
    page = app_session
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-image").click()
    done = {"id": "j-1", "status": "done", "stage": "finished",
            "result_urls": ["/m/test-pixel", SCRIPT_URL, "https://evil.example/m/x.png"],
            "images": ['<img src=x onerror="window.__pwned=1">']}

    def answer(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(done))

    page.route(re.compile(r"/v1/images/generations(/j-1)?$"), answer)
    page.route(re.compile(r"/m/test-pixel$"), lambda route: route.fulfill(body=PNG, content_type="image/png"))
    page.get_by_label("Prompt", exact=True).fill("a cat")
    page.get_by_role("button", name="Generate", exact=True).click()
    expect(page.locator(".test-result__status")).to_have_text("Job j-1 done (finished)", timeout=15000)
    media = page.locator(".test-result__media")
    expect(media.locator("img")).to_have_count(1)
    expect(media.locator("img")).to_have_attribute("src", "/m/test-pixel")
    unsafe = media.locator(".test-result__unsafe")
    expect(unsafe).to_have_count(3)
    expect(unsafe.nth(0)).to_have_text("link not shown: " + SCRIPT_URL)
    expect(unsafe.nth(1)).to_have_text("link not shown: https://evil.example/m/x.png")
    expect(unsafe.nth(2)).to_have_text('link not shown: <img src=x onerror="window.__pwned=1">')
    assert page.evaluate("window.__pwned", isolated_context=False) is None
    assert not [u for u in requests if "evil.example" in u], "a foreign link was requested"
    expect(page.get_by_role("button", name="Generate", exact=True)).to_be_enabled(timeout=10000)


# ---- phone and tablet: each screen draws, fits the width and keeps its main action in reach --------------------------

SMOKE = [
    ("Prompt frames", "frames", lambda page: page.get_by_role("heading", name="Prompt frames", level=1)),
    ("Settings", "settings", lambda page: page.get_by_role("heading", name="Settings", level=1)),
    ("Test", "test", lambda page: page.get_by_role("heading", name="Test", level=1)),
]


@pytest.mark.parametrize("width", [390, 768])
@pytest.mark.parametrize("name, route, heading", SMOKE)
def test_frames_settings_and_test_fit_a_phone_and_a_tablet_with_their_main_action_in_reach(
        app_session, db, framed, width, name, route, heading):
    page = app_session
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 1024})
    if route == "frames":
        put(db, "video", None, FAKE_FRAMES.defaults["video"])
        put(db, "image", None, FAKE_FRAMES.defaults["image"])
    open_tab(page, name)
    expect(heading(page)).to_be_visible()
    if route == "frames":
        expect(page.locator(VIDEO)).to_be_visible()
        page.locator(VIDEO).get_by_role("button", name="Edit frame for fake-video").first.click()
        main = page.get_by_role("button", name="Follow the default").or_(
            page.get_by_role("button", name="Save", exact=True))
    elif route == "settings":
        expect(page.get_by_label("Value of log_retention_days")).to_be_visible()
        main = page.get_by_role("button", name="Save log_retention_days")
    else:
        main = page.get_by_role("button", name="Load models")
    main.first.scroll_into_view_if_needed()
    expect(main.first).to_be_visible()
    assert sideways(page) <= 0, "%s scrolls sideways at %d" % (route, width)
    if width == 390:                                          # a phone control is a finger target
        box = main.first.bounding_box()
        assert box["height"] >= 43.5, box


def test_a_failed_settings_load_has_a_retry_a_finger_can_hit_on_a_phone(app_session):
    page = app_session
    page.set_viewport_size({"width": 390, "height": 844})
    page.route(re.compile(r"/admin/settings$"), lambda route: route.fulfill(
        status=500, content_type="application/json",
        body=json.dumps({"error": {"code": "internal.unexpected", "message": "the settings are not there"}})))
    open_tab(page, "Settings")
    retry = page.locator("#settings").get_by_role("button", name="Retry")
    expect(retry).to_be_visible()
    box = retry.bounding_box()
    assert box["width"] >= 43.5 and box["height"] >= 43.5, box
    page.problems.clear()                                    # the 500 is a console line
