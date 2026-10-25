"""Settings, Prompt frames and the Test tab of the Vue admin in Chrome against the real app (browser tier). Every test
runs under the CSP watch of conftest (a violation on either channel fails it). The data is the fake provider's: the
`framed` fixture makes it serve prompt frames. Markup that a server string could carry must stay text; the Test tab's
API key must be in sessionStorage only, and gone after sign-out and a 401."""

import base64
import json
import os
import re
import uuid

import pytest
from accountkit import new_account
from jobkit import key as new_key
from page_helpers import click_tab, settle, sideways
from patchright.sync_api import TimeoutError as PlaywrightTimeout
from patchright.sync_api import expect
from test_admin_prices import fabricated, priced
from test_e2e_form_drawers import goto_keys
from test_e2e_keys_logs_alerts import key_menu, new_key_button
from test_frame_plan import put

from relay_core.testing.fake_provider import FAKE_FRAMES
from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

HOSTILE = '<img src=x onerror="window.__pwned=1"> {{tracking_code}} {{client_prompt}}'
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
SCRIPT_URL = "java" + "script:alert(1)"


def open_tab(page, name: str) -> None:
    click_tab(page, name, exact=False)


# ---- Settings -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_settings_edit_marks_the_field_saves_and_reset_returns_to_the_default(app_session, db, color_scheme):
    page = app_session
    open_tab(page, "Settings")
    row = page.locator('#settings .field[data-key="log_retention_days"]')
    source = row.locator(".key__default")
    bar = page.get_by_role("region", name="Unsaved changes")
    expect(source).to_contain_text("default · default 30")
    expect(bar).to_have_count(0)
    page.get_by_label("Value of log_retention_days").fill("45")
    expect(row).to_contain_text("edited")
    expect(bar).to_contain_text("1 unsaved change")
    expect(bar).to_contain_text("Log retention days: 30 → 45")
    bar.get_by_role("button", name="Save changes").click()
    expect(page.locator(".el-message")).to_contain_text("Saved")
    expect(source).to_contain_text("db · default 30")
    expect(bar).to_have_count(0)
    assert db.one("SELECT value FROM settings_kv WHERE key = 'log_retention_days'")[0] in (45, "45")
    page.get_by_role("button", name="Reset log_retention_days").click()
    expect(source).to_contain_text("default · default 30")
    assert db.one("SELECT count(*) FROM settings_kv WHERE key = 'log_retention_days'")[0] == 0
    expect(page.get_by_text("BILLING_TZ")).to_be_visible()
    expect(page.get_by_text("No job has an upstream figure yet")).to_be_visible()


def test_settings_refuses_a_bad_value_with_the_server_text_and_the_field(app_session, db):
    page = app_session
    open_tab(page, "Settings")
    page.get_by_label("Value of log_retention_days").fill("not a number")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.locator(".el-message--error")).to_contain_text("(value)")
    # the refusal stays under the field with what was typed, until Discard
    field = page.locator('#settings .field[data-key="log_retention_days"]')
    expect(field.get_by_role("alert")).to_contain_text("(value)")
    expect(page.get_by_label("Value of log_retention_days")).to_have_value("not a number")
    page.get_by_role("button", name="Discard changes", exact=True).click()
    expect(page.get_by_label("Value of log_retention_days")).to_have_value("30")
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
        page.get_by_role("navigation", name="Settings areas").get_by_test_id("settings-tab-fake").click()
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
    """A signed-in Prompt frames tab: the video and image defaults, and optionally an own frame for fake-video (then
    the page opens on it, else on the video default). Returns the models card, fake-video's row, the frame text box and
    the editor's heading."""
    put(db, "video", None, FAKE_FRAMES.defaults["video"])
    put(db, "image", None, FAKE_FRAMES.defaults["image"])
    if own:
        put(db, "video", "fake-video", own)
    open_tab(page, "Prompt frames")
    editor = page.get_by_test_id("frame-editor")
    expect(editor).to_be_visible()
    models = page.get_by_test_id("frame-models")
    return models, models.locator("tbody tr", has_text="fake-video"), page.get_by_label("Frame text"), \
        editor.get_by_role("heading", level=2)


def pick_frame(page, label: str) -> None:
    """Put another frame in the editor through its 'Frame for' list (the default, or a model)."""
    page.get_by_role("combobox", name="Frame for").click()
    page.get_by_role("option", name=re.compile(label)).click()


def frame_for(page):
    """The value of the 'Frame for' control, as text."""
    return page.locator(".filter-select__value")


def asked(page):
    """The discard question over a typed draft."""
    return page.locator(".el-message-box")


def test_frames_save_refuse_preview_roll_back_follow_reset_lock_and_apply_to_all(app_session, db, framed):
    page = app_session
    default = FAKE_FRAMES.defaults["video"]
    models, row, text, heading = frames_page(page, db)
    source = row.locator(".fm__pill").first
    save = page.get_by_role("button", name="Save", exact=True)
    expect(source).to_have_text("default")
    # the page opens on the kind's default, inline: no drawer, no dialog
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(frame_for(page)).to_have_text("Default · all video models")
    expect(text).to_have_value(default)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(frame_for(page)).to_have_text("fake-video · follows default")
    expect(text).to_have_value(default)
    expect(page.get_by_text("This model follows the default.")).to_be_visible()
    expect(save).to_be_disabled()

    # a save with a warning: saved, the warning shown, the model has its own frame
    first = "Own words. {{tracking_code}} {{client_prompt}}"
    text.fill(first)
    expect(save).to_be_enabled()
    expect(page.get_by_test_id("frame-unsaved")).to_be_visible()
    save.click()
    expect(page.get_by_test_id("frame-warnings")).to_contain_text("asks for no single result")
    expect(page.locator(".el-message").first).to_be_visible()
    expect(source).to_have_text("own")
    expect(frame_for(page)).to_have_text(re.compile(r"^fake-video · own frame v\d+$"))
    assert frame_texts(db, "fake-video") == [first]
    expect(save).to_be_disabled()
    expect(page.get_by_test_id("frame-unsaved")).to_have_count(0)

    # a refused save shows its param beside the editor and stores nothing
    text.fill("no placeholders at all")
    expect(page.get_by_test_id("frame-check")).to_contain_text("is in 0 times")
    save.click()
    refusal = page.locator("#frame-refusal")
    expect(refusal).to_have_text(re.compile(r"\(\{\{(client_prompt|tracking_code)\}\}\)$"))
    assert frame_texts(db, "fake-video") == [first]
    page.problems.clear()                                    # the 4xx of a refused save is a console line

    # the preview follows the draft as it is typed, and the Preview button renders it at once; neither stores anything
    text.fill("Draft single. {{tracking_code}} {{client_prompt}}")
    shown = page.get_by_test_id("frame-preview")
    expect(shown).to_contain_text("Draft single.")
    expect(shown).to_contain_text("a cat on a sofa")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(shown).to_contain_text("Draft single.")
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
    # a blank sample prompt: no preview, and the Preview button shows the server's refusal
    page.get_by_label("Sample prompt").fill("")
    expect(shown).to_have_count(0)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(refusal).to_contain_text("(prompt)")
    page.problems.clear()
    page.get_by_label("Sample prompt").fill("a cat on a sofa")

    # a second version, then a rollback to the first
    second = "Second single. {{tracking_code}} {{client_prompt}}"
    text.fill(second)
    save.click()
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
    page.get_by_role("button", name="Follow the default").click()
    expect(source).to_have_text("default")
    assert frame_texts(db, "fake-video")[-1] is None

    # the default differs from the shipped one: the flag, then the reset
    pick_frame(page, "Default")
    expect(frame_for(page)).to_have_text("Default · all video models")
    text.fill("Changed single. {{tracking_code}} {{client_prompt}}")
    page.get_by_role("button", name="Preview", exact=True).click()           # a preview from the default's editor
    expect(shown).to_contain_text("Changed single.")
    save.click()
    flag = page.get_by_text("differs from the shipped default")
    expect(flag).to_be_visible()
    page.get_by_role("button", name="Reset to the shipped default").click()
    expect(flag).to_have_count(0)
    assert frame_texts(db, None)[-1] == default

    # a lock keeps a model's own text when a default is applied to all; unlocked, it takes it
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(frame_for(page)).to_have_text("fake-video · follows default")
    mine = "Mine single. {{tracking_code}} {{client_prompt}}"
    text.fill(mine)
    save.click()
    expect(source).to_have_text("own")
    row.get_by_role("checkbox").click()
    expect(row.locator("label")).to_have_attribute("title", re.compile(r"^Locked \d+ \w{3} by "))
    pick_frame(page, "Default")
    everywhere = "All single. {{tracking_code}} {{client_prompt}}"
    text.fill(everywhere)
    page.get_by_role("button", name="Apply to all models of this kind").click()
    applied = page.get_by_test_id("frame-applied")
    expect(applied).to_contain_text("Applied: -. Kept (locked): fake-video.")
    assert frame_texts(db, "fake-video")[-1] == mine
    row.get_by_role("checkbox").click()
    expect(row.locator("label")).to_have_attribute("title", re.compile(r"^Unlocked \d+ \w{3} by "))
    text.fill(everywhere)
    page.get_by_role("button", name="Apply to all models of this kind").click()
    expect(applied).to_contain_text("Applied: fake-video.")
    assert frame_texts(db, "fake-video")[-1] == everywhere
    text.fill(everywhere)                                    # the same text again: the model already has it
    page.get_by_role("button", name="Apply to all models of this kind").click()
    expect(applied).to_contain_text("Applied: -. Kept (locked): -. Unchanged: fake-video.")


def test_a_late_answer_for_a_frame_that_was_replaced_is_dropped(app_session, db, framed):
    page = app_session
    own = "Own of A single. {{tracking_code}} {{client_prompt}}"
    models, row, text, heading = frames_page(page, db, own=own)
    expect(text).to_have_value(own)                          # the page opens on the model's own frame
    held = []

    def hold(route):
        if route.request.method == "GET" and not held:
            held.append(route)
        else:
            route.continue_()

    page.route(re.compile(r"/admin/frames/fake/video/default(\?|$)"), hold)
    pick_frame(page, "Default")
    for _ in range(250):
        if held:
            break
        page.wait_for_timeout(20)
    assert held, "the request was not sent"
    # the editor still holds the frame it had, and cannot be written in while the next one is read
    expect(text).to_have_attribute("readonly", "")
    expect(text).to_have_value(own)
    # the operator picks the image kind before the answer comes; the late answer must change nothing
    page.get_by_test_id("frame-kind-image").click()
    expect(frame_for(page)).to_have_text("Default · all image models")
    expect(text).to_have_value(FAKE_FRAMES.defaults["image"])
    held[0].continue_()
    page.wait_for_timeout(500)
    expect(frame_for(page)).to_have_text("Default · all image models")
    expect(text).to_have_value(FAKE_FRAMES.defaults["image"])


def test_the_frames_page_opens_on_a_frame_and_a_typed_draft_asks_before_it_is_lost(app_session, db, framed):
    page = app_session
    own = "Own of A single. {{tracking_code}} {{client_prompt}}"
    models, row, text, heading = frames_page(page, db, own=own)
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(text).to_have_value(own)
    # a clean editor lets another frame in at once; one with typing in it asks, and Keep editing leaves it as typed
    typed = "Typed single. {{tracking_code}} {{client_prompt}}"
    text.fill(typed)
    pick_frame(page, "Default")
    expect(asked(page)).to_contain_text("Discard what you typed?")
    page.keyboard.press("Escape")
    expect(asked(page)).to_have_count(0)
    expect(text).to_have_value(typed)
    expect(frame_for(page)).to_have_text(re.compile(r"^fake-video · own frame"))
    # leaving the page with the draft asks too (a browser Back changes the address like this), and Keep editing stays
    page.evaluate("window.location.hash = '#/jobs'", isolated_context=False)
    expect(asked(page)).to_contain_text("Discard what you typed?")
    asked(page).get_by_role("button", name="Keep editing").click()
    expect(asked(page)).to_have_count(0)
    expect(text).to_have_value(typed)
    # Discard lets the other frame in
    pick_frame(page, "Default")
    asked(page).get_by_role("button", name="Discard").click()
    expect(frame_for(page)).to_have_text("Default · all video models")
    expect(text).to_have_value(FAKE_FRAMES.defaults["video"])
    # a draft left behind is not restored when the operator comes back (they chose to discard to leave)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    text.fill(typed)
    page.evaluate("window.location.hash = '#/jobs'", isolated_context=False)
    asked(page).get_by_role("button", name="Discard").click()
    expect(page).to_have_url(re.compile(r"#/jobs$"))
    open_tab(page, "Prompt frames")
    expect(page.get_by_test_id("frame-editor")).to_be_visible()
    expect(text).to_have_value(own)
    assert db.one("SELECT count(*) FROM prompt_frames WHERE model = 'fake-video'")[0] == 1


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (820, 1100), (390, 844)])
def test_the_editor_is_inline_at_the_three_widths_with_its_actions_in_reach_and_no_sideways_scroll(
        app_session, db, framed, width, height):
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    models, _row, text, heading = frames_page(page, db, own="Own of A single. {{tracking_code}} {{client_prompt}}")
    editor = page.get_by_test_id("frame-editor")
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(heading).to_have_text("fake Video")
    expect(text).to_have_value("Own of A single. {{tracking_code}} {{client_prompt}}")
    expect(page.get_by_role("heading", name="Prompt frames", level=1)).to_be_visible()
    # the list of providers and kinds is on the left of a desktop; a tablet and a phone have chips and a segmented row
    nav, picker = page.get_by_test_id("frames-nav"), page.get_by_test_id("frames-picker")
    if width >= 1280:
        expect(nav).to_be_visible()
        expect(picker).to_have_count(0)
        assert box_of(editor)["x"] > box_of(nav)["x"] + box_of(nav)["width"], "the editor is beside the list"
    else:
        expect(nav).to_have_count(0)
        expect(picker).to_be_visible()
        assert box_of(editor)["y"] > box_of(picker)["y"], "the editor stacks under the picker"
    if width >= 768:
        assert button_names(page.locator(".fed__foot")) == [
            "Save", "Follow the default", "Apply to all models of this kind", "Preview"]
    else:
        # the phone: Follow and Save in a bar above the tab bar, 44 px buttons; Apply and Preview in the card
        bar = page.locator(".fed__bar")
        assert button_names(bar) == ["Follow the default", "Save"]
        assert all(b.bounding_box()["height"] >= 43.5 for b in bar.get_by_role("button").all())
        assert box_of(bar)["y"] + box_of(bar)["height"] <= height - 64 + 1, "the bar is above the tab bar"
        assert button_names(page.locator(".fed__phone-actions")) == ["Apply to all models of this kind", "Preview"]
    assert sideways(page) <= 0


def box_of(locator):
    box = locator.bounding_box()
    assert box is not None
    return box


def button_names(locator) -> list[str]:
    """The buttons inside, by their text, or by their name when they are an icon."""
    return [b.inner_text().strip() or b.get_attribute("aria-label") for b in locator.get_by_role("button").all()]


@pytest.mark.parametrize(("width", "height"), [(1440, 900), (390, 844)])
def test_the_text_box_picks_out_the_markers_lies_over_its_mirror_inserts_at_the_caret_and_keeps_one_line(
        app_session, db, framed, width, height):
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    models, _row, text, heading = frames_page(page, db, own="Scene. {{client_prompt}} then {{tracking_code}}")
    mirror = page.locator(".fed__mirror")
    expect(mirror.locator(".fed__ph")).to_have_text(["{{client_prompt}}", "{{tracking_code}}"])
    expect(page.get_by_test_id("frame-check")).to_contain_text("once each")
    # the text box lies exactly over the mirror that draws the markers
    assert all(abs(box_of(mirror)[k] - box_of(text)[k]) <= 1 for k in ("x", "y", "width", "height"))
    # Insert puts a marker at the caret
    text.fill("ab")
    text.evaluate("el => el.setSelectionRange(1, 1)")
    page.get_by_role("button", name="Insert {{tracking_code}}").click()
    expect(text).to_have_value("a{{tracking_code}}b")
    # the editor is one line: Enter adds nothing
    text.press("Enter")
    expect(text).to_have_value("a{{tracking_code}}b")
    # a long frame wraps, and the box and its mirror grow with it
    text.fill("word " * 150)
    expect(page.get_by_test_id("frame-check")).to_contain_text("is in 0 times")
    assert box_of(text)["height"] > 100
    assert abs(box_of(mirror)["height"] - box_of(text)["height"]) <= 1
    assert sideways(page) <= 0


def test_the_models_and_the_history_follow_the_frame_in_the_editor(app_session, db, framed):
    page = app_session
    put(db, "video", "fake-video", "Older single. {{tracking_code}} {{client_prompt}}")
    models, row, text, heading = frames_page(page, db, own="Newer single. {{tracking_code}} {{client_prompt}}")
    history = page.get_by_test_id("frame-history-card")
    expect(history.get_by_role("heading")).to_contain_text("History · fake-video")
    expect(history.locator("li")).to_have_count(2)
    expect(row).to_have_class(re.compile("is-selected"))
    pick_frame(page, "Default")
    expect(history.get_by_role("heading")).to_contain_text("History · default")
    expect(row).not_to_have_class(re.compile("is-selected"))
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(row).to_have_class(re.compile("is-selected"))
    expect(models.locator("thead th")).to_have_text(["Model", "Source", "Version", "Keep own"])


WATCH = "apps/admin-web/src/lib/notify.ts"


def keys_toast_page(app, page, db, width, height):
    """The API keys list with one key whose Disable key the server refuses (500): an error toast with no drawer open.
    The New key form is the drawer that has a footer."""
    key = new_key(db)
    db.run("UPDATE api_keys SET label = %s WHERE id = %s", "toast key", key)
    goto_keys(app, page, width, height)
    page.route(re.compile(r"/admin/keys/[^/]+/(enable|disable)$"), lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"error": {"code": "server.error", "message": "no"}}')
        if route.request.method == "POST" else route.continue_())


SETTLED_BOXES_JS = """async () => {
  const box = (el) => { const r = el.getBoundingClientRect(); return {y: r.y, bottom: r.bottom, height: r.height}; };
  const toast = document.querySelector('.el-message');
  const foot = document.querySelector('.rl-drawer__foot');
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

TOAST_AND_FOOTER_JS = """() => {
  const toast = document.querySelector('.el-message');
  // a drawer that was shut keeps its footer in the page, undrawn: only a footer with a box counts
  const foot = [...document.querySelectorAll('.rl-drawer__foot')].find((f) => f.getClientRects().length);
  const bottom = (el) => el.getBoundingClientRect().bottom;
  return {
    toast: toast ? bottom(toast) : null,
    footTop: foot ? foot.getBoundingClientRect().top : null,
    fromBottom: toast ? innerHeight - bottom(toast) : null,
  };
}"""


GAP_JS = """([lo, hi]) => {
  const t = document.querySelector('.el-message'), f = document.querySelector('.rl-drawer__foot');
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


@mutation(WATCH, "watchDrawerFooter();\n\nconst base", "const base", expect="the toast did not slide above the footer")
@pytest.mark.parametrize(("width", "height", "default_gap"), [(1440, 900, 16), (390, 844, 72)])
def test_a_toast_showing_when_a_drawer_opens_slides_above_its_footer_and_back_when_it_closes(
        app, app_session, db, width, height, default_gap):
    page = app_session
    keys_toast_page(app, page, db, width, height)
    # an error toast (5 s) from an enable switch the server refuses, raised with no drawer on the page
    key_menu(page, "toast key", "Disable key")
    expect(page.locator(".el-message--error")).to_be_visible()
    page.problems.clear()                                    # the 500 is a console line
    page.wait_for_function("""(gap) => {                  // it slides in, then sits at its default place
      const t = document.querySelector('.el-message');
      return t && Math.abs(innerHeight - t.getBoundingClientRect().bottom - gap) <= 6;
    }""", arg=default_gap, timeout=3000)
    # the drawer opens while the toast is showing: the toast slides above the footer, none closes early
    new_key_button(page).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    toast_settles_above_footer(page, "slide above the footer")
    # the drawer closes: the toast comes back down to its default place
    page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    page.wait_for_function("""(gap) => {
      const t = document.querySelector('.el-message');
      return t && Math.abs(innerHeight - t.getBoundingClientRect().bottom - gap) <= 6;
    }""", arg=default_gap, timeout=3000)
    back = page.evaluate(TOAST_AND_FOOTER_JS, isolated_context=False)
    assert back["footTop"] is None and abs(back["fromBottom"] - default_gap) <= 6, back


@mutation(WATCH, "if (foot) sizes?.observe(foot);", "", expect="the toast did not follow the footer that grew")
def test_a_toast_follows_a_drawer_footer_that_grows(app, app_session, db):
    page = app_session
    keys_toast_page(app, page, db, 1440, 900)
    key_menu(page, "toast key", "Disable key")
    expect(page.locator(".el-message--error")).to_be_visible()
    page.problems.clear()                                    # the 500 is a console line
    new_key_button(page).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    before = toast_settles_above_footer(page, "slide above the footer")
    # the footer grows by script (a CSSOM write, as a wrapping footer would): the toast follows it
    page.evaluate("document.querySelector('.rl-drawer__foot').style.minHeight = '160px'", isolated_context=False)
    after = toast_settles_above_footer(page, "follow the footer that grew")
    assert after["foot"]["height"] > before["foot"]["height"] + 40, (before, after)


def test_on_a_phone_a_toast_does_not_cover_the_forms_footer(app, app_session, db):
    page = app_session
    keys_toast_page(app, page, db, 390, 844)
    key_menu(page, "toast key", "Disable key")
    expect(page.locator(".el-message--error")).to_be_visible()
    page.problems.clear()                                    # the 500 is a console line
    new_key_button(page).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    boxes = page.evaluate(SETTLED_BOXES_JS, isolated_context=False)
    assert boxes["toast"]["bottom"] <= boxes["foot"]["y"] + 1, boxes


def test_follow_the_default_and_roll_back_over_an_unsaved_draft_ask_first(app_session, db, framed):
    page = app_session
    older = "Older single. {{tracking_code}} {{client_prompt}}"
    put(db, "video", "fake-video", older)
    models, row, text, heading = frames_page(page, db, own="Newer single. {{tracking_code}} {{client_prompt}}")
    box = asked(page)
    expect(frame_for(page)).to_have_text(re.compile(r"^fake-video · own frame"))
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
    page.get_by_role("button", name="Follow the default").click()
    expect(box).to_contain_text("Discard what you typed?")
    box.get_by_role("button", name="Keep editing").click()
    expect(box).to_have_count(0)
    expect(text).to_have_value(typed)
    assert frame_texts(db, "fake-video")[-1] == older
    page.get_by_role("button", name="Follow the default").click()
    box.get_by_role("button", name="Discard").click()
    expect(row.locator(".fm__pill").first).to_have_text("default")
    assert frame_texts(db, "fake-video")[-1] is None
    expect(box).to_have_count(0)


def test_frame_text_with_markup_is_text_in_the_editor_the_history_and_the_preview(app_session, db, framed):
    page = app_session
    models, row, text, heading = frames_page(page, db)
    row.get_by_role("button", name="Edit frame for fake-video").click()
    expect(frame_for(page)).to_have_text("fake-video · follows default")
    text.fill(HOSTILE)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_test_id("frame-preview")).to_contain_text("<img src=x")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(text).to_have_value(HOSTILE)
    page.wait_for_timeout(300)                               # a script in the markup would have run by now
    assert page.evaluate("window.__pwned", isolated_context=False) is None, "markup ran as a script"
    assert page.locator(".frames img").count() == 0, "planted markup became an element"


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_a_provider_without_frames_says_the_core_frame_applies_and_the_list_names_it(
        app_session, db, color_scheme):
    page = app_session
    open_tab(page, "Prompt frames")
    expect(page.get_by_text("This provider has no prompt frames: the core frame applies.")).to_be_visible()
    nav = page.get_by_role("navigation", name="Providers and kinds")
    expect(nav.get_by_test_id("provider-tab-fake")).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("navigation", name="Provider", exact=True)).to_have_count(0)    # no strip: its own list
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)
    page.problems.clear()                                    # the 404 is a console line


def test_every_provider_has_its_own_frames_and_a_vibes_edit_previews_verbatim_and_touches_no_other(
        app_session, db, real_providers):
    """The real dola, meta, vibes and flow specs, their shipped defaults seeded: each provider is in the list and
    draws its own kinds; a vibes frame previews as written (no tracking code) and its save adds one row, for vibes
    only."""
    import os

    def shoot(name):
        if os.environ.get("RELAY_SCREENSHOTS_DIR"):
            page.screenshot(path=os.path.join(os.environ["RELAY_SCREENSHOTS_DIR"], name))

    page = app_session
    page.set_viewport_size({"width": 1440, "height": 900})
    open_tab(page, "Prompt frames")
    nav = page.get_by_role("navigation", name="Providers and kinds")
    kinds = {"dola": ["video", "image"], "meta": ["video", "image"], "vibes": ["video", "image"],
             "flow": ["video", "image"]}
    for name, drawn in kinds.items():
        if name == "dola":
            for listed in kinds:
                expect(nav.get_by_test_id("provider-tab-%s" % listed)).to_have_count(1)
        nav.get_by_test_id("provider-tab-%s" % name).click()
        expect(nav.get_by_test_id("provider-tab-%s" % name)).to_have_attribute("aria-expanded", "true")
        if name == "dola":
            shoot("frames-providers.png")
        expect(page.get_by_text("This provider has no prompt frames: the core frame applies.")).to_have_count(0)
        expect(nav.locator("[data-testid^=frame-kind-]")).to_have_count(len(drawn))
        assert nav.locator("[data-testid^=frame-kind-]").evaluate_all(
            "els => els.map(e => e.dataset.testid.replace('frame-kind-', ''))") == drawn
    before = db.all("SELECT provider, kind, model, text FROM prompt_frames ORDER BY id")
    assert sorted({r[0] for r in before}) == ["dola", "flow", "meta", "vibes"]
    nav.get_by_test_id("provider-tab-vibes").click()
    expect(page.get_by_test_id("frame-verbatim")).to_contain_text("vibes sends the text as written")
    page.get_by_test_id("frame-models").get_by_role("button", name="Edit frame for vibes-video", exact=True).click()
    expect(page.locator(".filter-select__value")).to_contain_text("vibes-video")
    expect(page.get_by_test_id("frame-verbatim")).to_contain_text("vibes sends the text as written")
    page.get_by_label("Frame text").fill("Cinematic, {{client_prompt}} {{tracking_code}}")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_test_id("frame-preview")).to_have_text("Cinematic, a cat on a sofa")
    shoot("frames-vibes-editor.png")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()      # saved: the draft is the text
    after = db.all("SELECT provider, kind, model, text FROM prompt_frames ORDER BY id")
    assert after == before + [("vibes", "video", "vibes-video", "Cinematic, {{client_prompt}} {{tracking_code}}")]
    nav.get_by_test_id("provider-tab-meta").click()
    page.get_by_test_id("frame-models").get_by_role("button", name="Edit frame for meta-video").click()
    expect(page.get_by_label("Frame text")).to_have_value("{{client_prompt}} {{tracking_code}}")
    expect(page.get_by_test_id("frame-verbatim")).to_have_count(0)


SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize(("width", "height"), [(1440, 1300), (820, 1560), (390, 1640)])
def test_the_frames_page_fits_each_width_in_both_themes_the_list_and_an_inline_edit(
        app_session, db, framed, color_scheme, width, height):
    """The page as the design draws it at 1440, 820 and 390 px, light and dark: no sideways scroll, and (with
    RELAY_SCREENSHOTS_DIR set) a shot of the list and one with an inline edit open."""
    page = app_session
    page.set_viewport_size({"width": width, "height": height})
    models, row, text, heading = frames_page(page, db, own=FRAMES_SHOT_OWN)
    page.get_by_test_id("frame-history").wait_for()
    expect(page.get_by_test_id("frame-preview")).to_be_visible()           # the preview has been read
    settle(page)
    assert sideways(page) <= 0
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, "%d-%s-frames-list.png" % (width, color_scheme)))
    text.click()
    text.press("ControlOrMeta+End")
    text.type(" Keep camera moves smooth.")
    expect(page.get_by_test_id("frame-unsaved")).to_be_visible()
    expect(page.get_by_test_id("frame-preview")).to_contain_text("Keep camera moves smooth.")
    settle(page)
    assert sideways(page) <= 0
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, "%d-%s-frames-edit.png" % (width, color_scheme)))


FRAMES_SHOT_OWN = ("Create one single video from the scene below. Do not ask questions and do not split it into parts. "
                   "Scene: {{client_prompt}} {{tracking_code}}")


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
    page.get_by_role("button", name="Send test job", exact=True).click()
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
    expect(page.get_by_role("button", name="Send test job", exact=True)).to_be_disabled()
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
    page.get_by_role("button", name="Send test job", exact=True).click()
    expect(page.locator(".test-result__status")).to_contain_text("Job")
    assert db.one("SELECT count(*) FROM jobs WHERE prompt = 'a dog'")[0] == 1
    assert page.evaluate("sessionStorage.getItem('relay_test_key')") == api_key.raw


def offered_then_pick(page, field: str, offered: list, option: str) -> None:
    """Check what one of the Test form's fields offers and pick `option`: Duration and Ratio are segments (a group of
    pressed buttons named by the field), Resolution and Style the design's select, drawn by us."""
    if field in ("Duration", "Ratio"):
        group = page.locator('[role="group"][aria-label="%s"]' % field)
        segments = group.locator("button[data-value]")
        expect(segments).to_have_text(offered)
        segments.filter(has_text=option).first.click()
        expect(segments.filter(has_text=option).first).to_have_attribute("aria-pressed", "true")
        return
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
        page.get_by_role("button", name="Send test job", exact=True).click()
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
        page.get_by_role("button", name="Send test job", exact=True).click()
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
        page.get_by_role("button", name="Send test job", exact=True).click()
    assert sent.value.post_data_json["style"] == "oil_painting"
    expect(page.locator(".test-result__status")).to_contain_text("Job")


def test_a_model_that_takes_no_ratio_style_or_image_shows_none_of_them_and_sends_none(app_session, live, db):
    """A model that takes no ratio, style or input image: one that names no ratio or style list and
    `max_images` 0 gets no such field, and the request carries none. With RELAY_SCREENSHOTS_DIR set the form is saved
    as test-meta-image.png."""
    import dataclasses
    import os

    from relay_core.providers import check_catalog

    page = app_session
    for name in live.env.registry.names():
        spec = live.env.registry.spec(name)
        listed = spec.catalog()
        spec.catalog = lambda listed=listed: [
            dataclasses.replace(m, limits={**m.limits, "max_images": 0}) if m.media_type == "image" else m
            for m in listed]
        spec.extras = lambda snapshots: {}
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-image").click()
    expect(page.locator("#test-form-title")).to_have_text("fake-image")
    page.get_by_label("Prompt", exact=True).fill("a cat")
    if os.environ.get("RELAY_SCREENSHOTS_DIR"):
        page.locator("section.form").screenshot(path=os.path.join(os.environ["RELAY_SCREENSHOTS_DIR"],
                                                                   "test-meta-image.png"))
    form = page.locator("section.form")
    for field in ("Ratio", "Style"):
        expect(form.get_by_label(field, exact=True)).to_have_count(0)
    expect(form.locator("#test-refs")).to_have_count(0)
    expect(page.get_by_test_id("attach-input")).to_have_count(0)
    with page.expect_request(lambda r: r.method == "POST" and "/generations" in r.url) as sent:
        page.get_by_role("button", name="Send test job", exact=True).click()
    assert sent.value.post_data_json == {"model": "fake-image", "prompt": "a cat"}


def test_a_model_with_resolutions_offers_them_and_sends_the_one_picked_in_options(app_session, live, db):
    """vibes-video names 480p and 720p: the tab offers them (Default sends none) and the request carries the one picked
    as `options.resolution`; the image model, which names none, has no Resolution field."""
    import dataclasses

    from relay_core.providers import check_catalog

    page = app_session
    for name in live.env.registry.names():
        spec = live.env.registry.spec(name)
        listed = spec.catalog()
        spec.catalog = lambda listed=listed: [
            dataclasses.replace(m, limits={**m.limits, "resolutions": ["480p", "720p"]})
            if m.media_type == "video" else m for m in listed]
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])
    open_test_tab(page, db, live)
    page.get_by_test_id("test-model").filter(has_text="fake-image").click()
    expect(page.locator("section.form").get_by_label("Resolution", exact=True)).to_have_count(0)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    offered_then_pick(page, "Resolution", ["Default", "480p", "720p"], "720p")
    page.get_by_label("Prompt", exact=True).fill("a cat")
    with page.expect_request(lambda r: r.method == "POST" and "/generations" in r.url) as sent:
        page.get_by_role("button", name="Send test job", exact=True).click()
    assert sent.value.post_data_json["options"] == {"resolution": "720p"}


def test_a_model_that_works_on_a_source_job_takes_its_id_and_sends_it_and_no_other_model_shows_the_field(
        app_session, live, db):
    """A model whose catalog names `limits.source` (vibes' extend and edits) has a Source job field; the request
    carries the id typed as `source_job_id`. A model that names none has no such field. With RELAY_SCREENSHOTS_DIR set
    the form is saved as test-source-job.png."""
    import dataclasses
    import os

    from relay_core.providers import check_catalog

    page = app_session
    for name in live.env.registry.names():
        spec = live.env.registry.spec(name)
        listed = spec.catalog()
        spec.catalog = lambda listed=listed: [
            dataclasses.replace(m, limits={**m.limits, "source": "video", "max_images": 0})
            if m.media_type == "video" else m for m in listed]
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])
    open_test_tab(page, db, live)
    form = page.locator("section.form")
    page.get_by_test_id("test-model").filter(has_text="fake-image").click()
    expect(form.get_by_label("Source job", exact=True)).to_have_count(0)
    page.get_by_test_id("test-model").filter(has_text="fake-video").click()
    page.get_by_label("Prompt", exact=True).fill("the birds fly away")
    form.get_by_label("Source job", exact=True).fill(" 6f0c3c1e-7a58-4b8e-9d57-0a2d1f0e5a11 ")
    if os.environ.get("RELAY_SCREENSHOTS_DIR"):
        form.screenshot(path=os.path.join(os.environ["RELAY_SCREENSHOTS_DIR"], "test-source-job.png"))
    with page.expect_request(lambda r: r.method == "POST" and "/generations" in r.url) as sent:
        page.get_by_role("button", name="Send test job", exact=True).click()
    assert sent.value.post_data_json["source_job_id"] == "6f0c3c1e-7a58-4b8e-9d57-0a2d1f0e5a11"


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
    page.get_by_role("button", name="Send test job", exact=True).click()
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
    expect(page.get_by_role("button", name="Send test job", exact=True)).to_be_enabled(timeout=10000)


# ---- phone and tablet: each screen draws, fits the width and keeps its main action in reach --------------------------

SMOKE = [
    ("Prompt frames", "frames", lambda page: page.get_by_role("heading", name="Prompt frames", level=1)),
    ("Settings", "settings", lambda page: page.get_by_role("heading", name="Settings", level=1)),
    # the Test page's title is the top bar's below 1280 px, its h1 kept for screen readers
    ("Test", "test", lambda page: page.get_by_test_id("page-title").filter(has_text="Test")),
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
        expect(page.get_by_test_id("frame-editor")).to_be_visible()
        main = page.get_by_role("button", name="Save", exact=True)
    elif route == "settings":
        page.get_by_label("Value of log_retention_days").fill("45")
        main = page.get_by_role("region", name="Unsaved changes").get_by_role("button", name="Save changes")
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
