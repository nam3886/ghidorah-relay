"""Prompt frames and Settings against the canvas, element by element (element_diff.py), as the Jobs page is
(test_element_diff.py, whose `Check` this reuses).

`test_frames_settings_match_the_canvas_element_by_element`: the page under the shell (`main`) of B-, T- and P-Frames
and B-, T- and P-Settings, light and dark, and the states of B-States these pages use. The Frames editor is a right
drawer in the app and a column in the boards: the list is compared with the board's editor column taken out (the list
runs across the page), the editor's body with the board's editor card at the drawer's width (its own title row and
buttons set aside), and the buttons with the drawer's footer at 1440 and 768. Each comparison writes the
table and the side-by-side into `<run>/element-diff/` and fails while the table holds a difference that is not live
data. The text inside a form control is no text run of the tables (a control is one box), so the controls are also
compared by their accessible name: the font, colour, alignment and figures of the value they show."""

import base64
import json
import re
import time
from dataclasses import dataclass

import pytest
from element_diff import diff, extract, markdown, popup_place, side_by_side
from patchright.sync_api import expect
from pixel_capture import full_photo, opened, shoot, sideways
from pixel_diff import compare
from pixel_lane7 import (
    SETTINGS_NOTE,
    SETTINGS_NOTE_APP,
    frames_drive,
    frames_seed,
    open_the_boards_editor,
    open_the_boards_frame,
    settings_drive,
    settings_seed,
    use_the_boards_models,
)
from pixel_manifest import Entry
from pixel_seed import BOARD_USER, NOW, shell_seed
from test_element_diff import BOARDS, THEMES, WIDEN_JS, Check, _png, _root

pytestmark = pytest.mark.browser

# ---- the boards --------------------------------------------------------------------------------------------------
# Live data on the Frames boards (pixel_lane7.py says why): the version tags of the defaults and of the models that
# follow them, and the preview (the server's sample tracking code, so its length too).
FRAMES_DATA = ("v3", "v2", "v1")
EDITOR_DATA = ("Create exactly one cinematic video for this request, no on-screen text, no watermark, no follow-up "
               "questions. Request: a cat on a sofa Reference: rq-3b9e1f0a Keep the camera steady and the light "
               "natural.", "203 characters")
# Settings: the password rule's number, the board's sample 12 against the server's 10 (design-deviations)
SETTINGS_DATA, SETTINGS_DATA_APP = ("At least 12 characters",), ("At least 10 characters",)
# Settings: what the page has that the boards do not draw (declared deviations). The canvas's card note names the
# environment, which is no longer a layer; the page's note says what Reset does now. After Change password the page
# adds two cards with their sub-navigation entries: their titles, notes and the "None" an empty card reads.
# The note is a sentence of another length: a different number of lines moves everything under it, so the canvas's
# sentence is replaced by the page's before it is read (the words are the declared deviation, the layout is compared).
# The board's sample row names the source `env`, which the page cannot show (db or default): the canvas chip reads
# `default`, as the page's row does. The row's stored value is the fixture's 120 (the default), where the canvas draws
# 60: a value in a text box, which the element diff compares by font and place, not by its text. Exactly one canvas
# text may be rewritten: a board that draws two `env` rows, or none, fails here instead of passing unnoticed.
NOTE_AS_APP = """root => {
  const old = %s, now = %s;
  for (const el of document.querySelectorAll(root + ' p')) if (el.textContent.trim() === old) el.textContent = now;
  const walk = document.createTreeWalker(document.querySelector(root), NodeFilter.SHOW_TEXT);
  let rewritten = 0;
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (/^\\s*env(\\s|$)/.test(n.nodeValue)) {
      n.nodeValue = n.nodeValue.replace('env', 'default');
      rewritten += 1;
    }
  }
  if (rewritten !== 1) throw new Error('the canvas has ' + rewritten + ' env texts, not the one sample row');
}""" % (json.dumps(SETTINGS_NOTE), json.dumps(SETTINGS_NOTE_APP))
SETTINGS_ACCEPTED_APP = (
    "Fixed limits", "Fixed limits*", "Containers", "Containers*", "None",
    "Constants in the code that shape behaviour. They change only with a new release.",
    "Each relay service's own limits, read from its cgroup when it started. A worker that stopped cleanly is not "
    "listed.")
FRAMES = Entry("B-Frames", "#/frames", seed=frames_seed, now=NOW, drive=frames_drive, user=BOARD_USER)
SETTINGS = Entry("B-Settings", "#/settings", seed=settings_seed, now=NOW, drive=settings_drive, user=BOARD_USER)


# The board's editor card (title row, hint, field, buttons, preview block, history) and its parts by place.
EDITOR_CARD = 'section[aria-labelledby="edTitle"]'
EDITOR_TITLE_ROW = EDITOR_CARD + " > div:nth-child(1)"
EDITOR_BUTTONS = EDITOR_CARD + " > div:nth-child(4)"
# The editor's own title row (the heading and the source pill) is the drawer's header and chip, and its buttons are the
# drawer's footer: the body is compared without them. The card is plain content in the drawer: no border, padding or
# radius of its own.
CARD_AS_BODY = """root => {
  const el = document.querySelector(root);
  el.style.border = '0';
  el.style.padding = '0';
  el.style.borderRadius = '0';
}"""
# The board marks the row whose editor is open; the page has no open-editor row (the editor is a drawer over it)
ROW_MARK_OFF = """root => document.querySelectorAll(root + ' [style*="primary-soft"]').forEach(
  (el) => { el.style.background = 'transparent'; })"""
# The drawer's footer is the shared one: padding 10 24, a strong rule above, the surface fill, the buttons at the right.
BUTTONS_AS_FOOTER = """root => {
  const el = document.querySelector(root);
  el.style.padding = '10px 24px';
  el.style.borderTop = '1px solid var(--line-strong)';
  el.style.background = 'var(--surface)';
  el.style.justifyContent = 'flex-end';
}"""
# the editor body in pixels at the drawer's width: 0.68 measured on the box (worst of light and dark), + 1.0
EDITOR_BODY_GATE = 1.69


@dataclass(frozen=True)
class FrameCheck(Check):
    cut_canvas: str = ""        # removed from the canvas page before it is read: what the drawer holds elsewhere
    fix_canvas: str = ""        # JS run on the canvas root before it is read: a declared deviation
    same_height: bool = False   # the canvas part is given the app part's height
    gate: float | None = None   # most mismatch + anti-aliasing (%) the compared part may have in pixels
    canvas_drop: tuple = ()     # canvas boxes set aside before the pairing (a signature, "prefix*" or "role:<role>")


def _board(entry: Entry, board: str, data: tuple, data_app: tuple = (), accepted: tuple = (),
           accepted_app: tuple = (), fix_canvas: str = "") -> Check:
    return FrameCheck(board, Entry(board, entry.route, seed=entry.seed, now=entry.now, drive=entry.drive,
                                   user=entry.user), "main", "main", data=data, data_app=data_app, accepted=accepted,
                      accepted_app=accepted_app, fix_canvas=fix_canvas)


def _list(board: str) -> FrameCheck:
    entry = Entry(board, FRAMES.route, seed=FRAMES.seed, now=FRAMES.now, drive=frames_drive, user=FRAMES.user)
    return FrameCheck(board + "-list", entry, "main", "main", data=FRAMES_DATA, cut_canvas=EDITOR_CARD,
                      fix_canvas=ROW_MARK_OFF)


def _editor(board: str, *, body: bool) -> FrameCheck:
    entry = Entry(board, FRAMES.route, seed=FRAMES.seed, now=FRAMES.now, drive=open_the_boards_editor,
                  user=FRAMES.user)
    if body:
        return FrameCheck(board + "-editor", entry, EDITOR_CARD, ".detail-drawer__body .frame-editor", widen=True,
                          data=EDITOR_DATA, cut_canvas=EDITOR_TITLE_ROW + ", " + EDITOR_BUTTONS,
                          fix_canvas=CARD_AS_BODY, same_height=True, gate=EDITOR_BODY_GATE,
                          canvas_drop=("One line, at most 1,000 characters*",))
    return FrameCheck(board + "-footer", entry, EDITOR_BUTTONS, ".detail-drawer__foot", widen=True,
                      fix_canvas=BUTTONS_AS_FOOTER)


FRAMES_CHECKS = [*(_list(b) for b in ("B-Frames", "T-Frames", "P-Frames")),
                 *(_editor(b, body=True) for b in ("B-Frames", "T-Frames", "P-Frames")),
                 *(_editor(b, body=False) for b in ("B-Frames", "T-Frames"))]
BOARD_CHECKS = FRAMES_CHECKS + [
    _board(SETTINGS, b, SETTINGS_DATA, SETTINGS_DATA_APP, (), SETTINGS_ACCEPTED_APP, NOTE_AS_APP)
    for b in ("B-Settings", "T-Settings", "P-Settings")]


# ---- the states of B-States --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StateCheck(Check):
    # canvas boxes set aside before the pairing (a signature, a prefix ending in "*", or "role:<role>"): what the
    # drawn pattern holds beside the part this page takes from it (the New key button of the empty card); the app's
    # `accepted_app` takes the same forms here
    canvas_drop: tuple = ()
    # (canvas selector, app selector, CSS properties) that must compute alike: what is not a box of either table (the
    # raised fill of an open trigger, the bad border of a refused text box)
    same_style: tuple = ()


TIMEOUT_MS = 10_000
# the drawer's body fills the drawer from header to footer, the card is as tall as its content: the part compared in
# pixels is as tall as the app's, so the empty rest is not counted
HEIGHT_JS = """([sel, height]) => {
  const el = document.querySelector(sel);
  el.style.boxSizing = 'border-box';
  el.style.flex = 'none';
  el.style.height = height + 'px';
}"""
DROP_JS = "sel => document.querySelectorAll(sel).forEach((el) => el.remove())"
STATES = "B-States"
DESKTOP, TABLET, PHONE = (1440, 1000), (768, 1000), (390, 900)
OPEN_LIST = ('[role="listbox"]', '[role="combobox"][aria-expanded="true"]')
OPEN_TRIGGER = 'button[aria-haspopup="listbox"][aria-expanded="true"]@0'
SETTINGS_READ = re.compile(r"/admin/settings$")
PRICES_READ = re.compile(r"/admin/prices(\?.*)?$")
BAD_GATEWAY = '{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'


def _hold_toast(page, kind: str) -> None:
    """The toast of `kind`, kept open: Element Plus pauses its timer while the pointer is on it (over the icon, away
    from a close button)."""
    toast = page.locator(".el-message--" + kind).first
    expect(toast).to_be_visible(timeout=TIMEOUT_MS)
    box = toast.bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + box["height"] / 2)


def provider_open(page, live, data):
    expect(page.locator("[data-frame-kind=video]")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("combobox", name="Provider", exact=True).click()
    expect(page.get_by_role("listbox", name="Provider")).to_be_visible()


def frame_saved(page, live, data):
    open_the_boards_frame(page)
    page.get_by_role("button", name="Save", exact=True).click()
    _hold_toast(page, "success")


def frame_refused(page, live, data):
    """A frame without its markers: the server refuses it, and the editor shows the refusal under the text box."""
    open_the_boards_frame(page)
    page.get_by_label("Frame text").fill("One cinematic video, no markers.")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#frame-refusal")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def frames_ready(page, live, data):
    expect(page.locator("[data-frame-kind=video]")).to_be_visible(timeout=TIMEOUT_MS)


def _value(page, key: str, text: str) -> None:
    box = page.get_by_label("Value of " + key)
    expect(box).to_be_visible(timeout=TIMEOUT_MS)
    box.fill(text)
    page.get_by_role("button", name="Save " + key).click()


def setting_saved(page, live, data):
    _value(page, "log_retention_days", "45")
    _hold_toast(page, "success")


def setting_saved_thrice(page, live, data):
    """Three saves while the first toast is open: one toast, counted "×3" (B-States' grouped toast)."""
    for value in ("45", "46", "47"):
        _value(page, "log_retention_days", value)
        expect(page.get_by_label("Value of log_retention_days")).to_have_value(value, timeout=TIMEOUT_MS)
        expect(page.get_by_role("button", name="Save log_retention_days")).to_be_disabled(timeout=TIMEOUT_MS)
    expect(page.locator(".rl-toast__count")).to_have_text("×3", timeout=TIMEOUT_MS)
    _hold_toast(page, "success")


def setting_refused(page, live, data):
    _value(page, "log_retention_days", "not a number")
    _hold_toast(page, "error")


def _failing(pattern):
    def drive(page, live, data):
        page.route(pattern, lambda route: route.fulfill(status=502, content_type="application/json", body=BAD_GATEWAY)
                   if route.request.method == "GET" else route.fallback())
        page.reload()
        expect(page.locator('[data-state="error"]')).to_be_visible(timeout=TIMEOUT_MS)
    return drive


def settings_held(page, live, data):
    """The settings never answer: the card says it is loading."""
    page.route(SETTINGS_READ, lambda route: None)
    page.reload()
    expect(page.locator('#settings [data-state="loading"]')).to_be_visible(timeout=TIMEOUT_MS)


def prices_empty(page, live, data):
    expect(page.locator('#prices [data-state="empty"]')).to_be_visible(timeout=TIMEOUT_MS)


def _frames_state(drive):
    return Entry(STATES, "#/frames", seed=frames_seed, now=NOW, drive=drive, user=BOARD_USER)


def _settings_state(drive):
    return Entry(STATES, "#/settings", seed=shell_seed, now=NOW, drive=drive, user=BOARD_USER)


EMPTY_CARD_BODY = 'section[aria-label="API keys, empty"] > div:last-child'
EMPTY_PADDING = ("padding-top", "padding-right", "padding-bottom", "padding-left", "text-align", "border-top-color",
                 "border-top-width")
ERROR_ALERT = 'section[aria-label="Jobs, error"] [role="alert"]'
# the drawn skeleton's first row: its time bar, the title bar's second line, the status pill (the N-th span of the row)
SKEL_BAR = 'section[aria-label="Jobs, loading"] tbody span@%d'
SKEL_STYLE = ("height", "border-top-left-radius", "background-color", "margin-top")
TOAST_BOX = ("padding-top", "padding-right", "padding-bottom", "padding-left", "column-gap", "min-width")
CLOSE_BOX = ("width", "height", "border-top-left-radius", "color", "background-color")
LOAD_FAILED = ("Could not load the jobs: the relay answered 502 (bad gateway).",)
LISTBOX = '[role="listbox"][aria-label="Provider"]@%d'
LIST_SURFACE = ("width", "background-color", "border-top-color", "border-top-width", "border-top-left-radius",
                "box-shadow", "padding-top", "padding-left")
# the server's words for a refused frame, with the placeholder it names (refusalText adds it)
FRAME_REFUSED = ("a prompt frame has a missing, repeated or unknown placeholder ({{client_prompt}})",)
STATE_CHECKS = [
    # the Provider list of Frames is the drawn Provider list. Its options are the live providers: `All` is the drawn
    # chosen one, ours is dola; the other drawn options are set aside. The list's height follows the count, so the two
    # surfaces are compared by their style, not paired by place
    *(StateCheck("frames-list-%d" % width, _frames_state(provider_open), LISTBOX % n, '[role="listbox"]', viewport,
                 data=("All",), accepted=("dola", "vibes", "meta"), popup=OPEN_LIST,
                 canvas_drop=("role:listbox",), accepted_app=("role:listbox",),
                 same_style=((OPEN_TRIGGER, '[role="combobox"][aria-expanded="true"]', ("background-color",)),
                             (LISTBOX % n, '[role="listbox"]', LIST_SURFACE)))
      for n, (width, viewport) in enumerate(((1440, DESKTOP), (768, TABLET), (390, PHONE)))),
    StateCheck("frames-saved-toast", _frames_state(frame_saved), '[role="status"]@0', ".el-message", DESKTOP,
               data=("Saved",), data_app=("Saved: applies to jobs created from now on",), loose={"*": "w"},
               same_style=(('[role="status"]@0', ".el-message", TOAST_BOX),)),
    StateCheck("frames-refusal", _frames_state(frame_refused), "#fvErr", "#frame-refusal", DESKTOP,
               data=("A label is required.",), data_app=FRAME_REFUSED,
               same_style=(("#fvLabel", "#frame-text", ("border-top-color", "border-top-width", "border-radius")),)),
    StateCheck("settings-saved-toast", _settings_state(setting_saved), '[role="status"]@0', ".el-message", DESKTOP),
    # the toast is as wide as its text, which is data: what sits right of the text (the X) moves with it, so the X and
    # the toast's padding are compared by their style instead of their place
    StateCheck("settings-error-toast", _settings_state(setting_refused), '[role="alert"]@1', ".el-message", DESKTOP,
               data=("Check failed: connect timeout after 8 s",), loose={"*": "w", "": "x"},
               same_style=(('[role="alert"]@1', ".el-message", TOAST_BOX),
                           ('button[aria-label="Close message"]', ".rl-toast__close", CLOSE_BOX))),
    StateCheck("settings-load-error", _settings_state(_failing(SETTINGS_READ)), ERROR_ALERT,
               '#settings [role="alert"]', DESKTOP, widen=True, data=LOAD_FAILED,
               data_app=("Could not load the settings: the relay answered 502 (bad gateway).",)),
    StateCheck("prices-load-error", _settings_state(_failing(PRICES_READ)), ERROR_ALERT, '#prices [role="alert"]',
               DESKTOP, widen=True, data=LOAD_FAILED,
               data_app=("Could not load the measured prices: the relay answered 502 (bad gateway).",)),
    # the empty area of the drawn empty card: its New key button is not ours, so the area is as tall as its text
    StateCheck("prices-empty", _settings_state(prices_empty), EMPTY_CARD_BODY, "#prices .state__note", DESKTOP,
               widen=True, data=("No API keys yet.",), data_app=("No job has an upstream figure yet",),
               accepted=("New key",), canvas_drop=("New key", "svg *"), loose={"": "h"},
               same_style=((EMPTY_CARD_BODY, "#prices .state__note", EMPTY_PADDING),)),
    # the loading rows: the drawn skeleton's bars (a 12 px line and its 10 px second line, the 22 px pill), compared bar
    # with bar (the columns are the card's own), and a loading row as tall as the drawn one
    StateCheck("settings-loading", _settings_state(settings_held), SKEL_BAR % 1, "#settings tbody .set-skel@0",
               DESKTOP, loose={"*": "w"},
               same_style=((SKEL_BAR % 2, "#settings tbody .set-skel--sub@0", SKEL_STYLE),
                           (SKEL_BAR % 3, "#settings tbody .set-skel--pill@0", SKEL_STYLE))),
    StateCheck("settings-saved-toast-x3", _settings_state(setting_saved_thrice), '[role="status"]@1', ".el-message",
               DESKTOP),
]
CHECKS = BOARD_CHECKS + STATE_CHECKS

STYLE_JS = """([sel, n, props]) => {
  const el = document.querySelectorAll(sel)[n];
  if (!el) return null;
  const s = getComputedStyle(el);
  return Object.fromEntries(props.map((p) => [p, s.getPropertyValue(p)]));
}"""


def styles_of(page, selector: str, props: tuple) -> dict | None:
    """The computed `props` of the element `selector` names (`sel@N`: its N-th match)."""
    sel, _at, n = selector.partition("@") if "@" in selector else (selector, "", "0")
    return page.evaluate(STYLE_JS, [sel, int(n), list(props)], isolated_context=False)


def style_rows(pairs: tuple, theirs: list, mine: list) -> list[str]:
    """`| element | property | canvas | app |` for each property of `pairs` (canvas selector, app selector, properties)
    that computes differently, and for an element missing on either side."""
    out = []
    for (canvas_sel, app_sel, _props), c, a in zip(pairs, theirs, mine, strict=True):
        if c is None or a is None:
            out.append("| %s / %s | (missing) | %s | %s |" % (canvas_sel, app_sel, c is not None, a is not None))
            continue
        out += ["| %s | %s | %s | %s |" % (app_sel, k, v, a.get(k)) for k, v in c.items() if a.get(k) != v]
    return out


def _dropped(box: dict, drop: tuple) -> bool:
    return any(box["sig"] == d or (d.endswith("*") and box["sig"].startswith(d[:-1]))
               or (d.startswith("role:") and box["role"] == d[5:]) for d in drop)

# ---- the form controls -------------------------------------------------------------------------------------------
CONTROLS_JS = r"""(root) => {
  const rootEl = root ? document.querySelector(root) : document.documentElement;
  const ox = rootEl.getBoundingClientRect().left, oy = rootEl.getBoundingClientRect().top;
  const out = [];
  for (const el of rootEl.querySelectorAll("input, textarea, select")) {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity) < 0.01 || !r.width) continue;
    const name = el.getAttribute("aria-label") || (el.labels && el.labels[0] ? el.labels[0].textContent.trim() : "")
      || el.getAttribute("placeholder") || el.type;
    out.push({name, rect: [r.left - ox, r.top - oy], style: {
      type: el.type, family: s.fontFamily.split(",")[0].replace(/["']/g, "").trim(), size: s.fontSize,
      weight: s.fontWeight, lineHeight: s.lineHeight, color: s.color, align: s.textAlign,
      numeric: s.fontVariantNumeric, letterSpacing: s.letterSpacing, accent: s.accentColor}});
  }
  return out;
}"""


CROP_JS = r"""async ({png, height}) => {
  const bitmap = await createImageBitmap(await (await fetch("data:image/png;base64," + png)).blob());
  const c = new OffscreenCanvas(bitmap.width, Math.min(height, bitmap.height));
  c.getContext("2d").drawImage(bitmap, 0, 0);
  const bytes = new Uint8Array(await (await c.convertToBlob({type: "image/png"})).arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}"""


def crop_to(page, png: bytes, height: float) -> bytes:
    """The top `height` px of a PNG: the app's `main` runs to the bottom of the window, the board's ends with its
    content, and the rows below the content are no element of either."""
    return base64.b64decode(page.evaluate(CROP_JS, {"png": base64.b64encode(png).decode(), "height": round(height)}))


def controls(page, root: str | None) -> list:
    return page.evaluate(CONTROLS_JS, root, isolated_context=False)


def compare_controls(theirs: list, mine: list) -> list[str]:
    """Each canvas control against the app's of the same accessible name, then what is left in reading order (a
    checkbox the app names for a screen reader, `Keep own frame of <model>`, is the drawn `Keep own`): every style
    value that differs, and a control on one side only."""
    pairs, rest_c, rest_a = [], [], list(mine)
    for c in theirs:
        a = next((m for m in rest_a if m["name"] == c["name"]), None)
        if a is None:
            rest_c.append(c)
        else:
            rest_a.remove(a)
            pairs.append((c, a))
    place = lambda m: (round(m["rect"][1]), m["rect"][0])  # noqa: E731
    rest_c.sort(key=place)
    rest_a.sort(key=place)
    pairs += [(c, a) for c, a in zip(rest_c, rest_a, strict=False) if c["style"]["type"] == a["style"]["type"]]
    paired_c, paired_a = [id(c) for c, _a in pairs], [id(a) for _c, a in pairs]
    out = []
    for c, a in pairs:
        out += ["| %s | %s | %s | %s |" % (c["name"], k, v, a["style"].get(k))
                for k, v in c["style"].items() if a["style"].get(k) != v]
    out += ["| %s | (no app control) | | |" % c["name"] for c in theirs if id(c) not in paired_c]
    out += ["| %s | (no canvas control) | | |" % a["name"] for a in mine if id(a) not in paired_a]
    return out


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_frames_settings_match_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check,
                                                              theme):
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, board_h)
    dark = theme == "dark"
    same_style = getattr(check, "same_style", ())
    with opened(app, db, live, check.state, dark, width, height) as page:
        mine_styles = [styles_of(page, app_sel, props) for _c, app_sel, props in same_style]
        app_root = _root(page, check.app_root)
        mine = extract(page, app_root)
        mine_controls = controls(page, app_root)
        app_png = _png(page, app_root)
        misplaced = popup_place(page, *check.popup) if check.popup else None
    renderer.open(check.state.board, dark, board_w, board_h)
    theirs_styles = [styles_of(renderer.page, canvas_sel, props) for canvas_sel, _a, props in same_style]
    if getattr(check, "cut_canvas", ""):
        renderer.page.evaluate(DROP_JS, check.cut_canvas, isolated_context=False)
    canvas_root = _root(renderer.page, check.canvas_root)
    if getattr(check, "fix_canvas", ""):
        renderer.page.evaluate(check.fix_canvas, canvas_root, isolated_context=False)
    if check.widen:
        renderer.page.evaluate(WIDEN_JS, [canvas_root, mine["width"]], isolated_context=False)
        renderer.settle()
    if getattr(check, "same_height", False):
        renderer.page.evaluate(HEIGHT_JS, [canvas_root, mine["height"]], isolated_context=False)
    theirs = extract(renderer.page, canvas_root)
    theirs_controls = controls(renderer.page, canvas_root)
    ref_png = _png(renderer.page, canvas_root)
    if check.app_root == "main" and mine["height"] > theirs["height"]:
        app_png = crop_to(diff_page, app_png, theirs["height"])
    elif check.app_root == "main" and theirs["height"] > mine["height"]:
        ref_png = crop_to(diff_page, ref_png, mine["height"])
    pixels = compare(diff_page, ref_png, app_png)

    theirs["texts"] = [t for t in theirs["texts"] if t["text"] not in check.accepted]
    theirs["boxes"] = [b for b in theirs["boxes"] if not _dropped(b, getattr(check, "canvas_drop", ()))]
    mine["texts"] = [t for t in mine["texts"] if t["text"] not in check.accepted_app]
    mine["boxes"] = [b for b in mine["boxes"] if not _dropped(b, check.accepted_app)]
    res = diff(check.name, theirs, mine, data=check.data, data_app=check.data_app, loose=check.loose)
    control_rows = compare_controls(theirs_controls, mine_controls) + style_rows(same_style, theirs_styles,
                                                                                 mine_styles)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (check.name, theme)
    notes = ("Canvas %s%s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% anti-aliased."
             % (check.state.board, " " + check.canvas_root if check.canvas_root else "", check.app_root or "page",
                width, height, pixels.percent, pixels.aa_percent))
    if check.popup:
        notes += " Open list against its trigger: %s." % (misplaced or "6 px clear")
    table = markdown(res, "%s, %s" % (check.name, theme), notes)
    table += ("\n## Form controls and computed styles (%d): a control's value font, colour and alignment, by "
              "accessible name; the styles the check names\n\n" % len(control_rows))
    table += ("| control | property | canvas | app |\n|---|---|---|---|\n" + "\n".join(control_rows) + "\n"
              if control_rows else "None.\n")
    (out / (stem + ".md")).write_text(table, encoding="utf-8")
    (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
    print("ELEMENTS %s %s: %d differences, %d canvas only, %d app only, %d data, %d control; pixels %.3f %% + %.3f %%"
          % (check.name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), len(res.data),
             len(control_rows), pixels.percent, pixels.aa_percent))
    assert res.failures == 0 and not control_rows, "%s %s: %d element differences, %d control, see %s.md" % (
        check.name, theme, res.failures, len(control_rows), stem)
    assert misplaced is None, misplaced
    gate = getattr(check, "gate", None)
    if gate is not None:
        got = pixels.percent + pixels.aa_percent
        assert got <= gate, "%s %s: %.3f %% of the compared part differs in pixels, the gate is %.2f %%" % (
            check.name, theme, got, gate)


def test_the_controls_are_compared_by_their_value_font(diff_page):
    """A value typed in another font, size or alignment is a few pixels of a whole board and no text run: the control
    list names it."""
    diff_page.set_content('<label for="v">Value</label><input id="v" value="5400" '
                          'style="font: 13px monospace; text-align: right">')
    canvas = controls(diff_page, None)
    diff_page.evaluate("document.getElementById('v').style.textAlign = 'left'")
    app = controls(diff_page, None)
    assert compare_controls(canvas, app) == ["| Value | align | right | left |"]
    assert compare_controls(canvas, canvas) == []


# ---- data shaped like the live box -------------------------------------------------------------------------------
# Read on the box 17/10, read-only, names, lengths and counts only: the settings are the core registry and dola's (19
# rows, sorted by key; descriptions up to about 170 characters; `dola.prices` is a JSON table of 12 tiers); two are
# stored (dola.browser_slots, dola.claims_enabled); BILLING_TZ has 3 characters, PUBLIC_BASE_URL 26 and TRUSTED_PROXIES
# is empty; one measured-price row (a 21-character model, 5 jobs, no disagreement). Frames: dola's video and image
# defaults (275 and 35 characters), one version each, no model with its own frame, no lock.
LIVE_STORED = {"dola.browser_slots": 6, "dola.claims_enabled": False}


def live_settings_body() -> dict:
    from relay_core.settings import REGISTRY
    from relay_dola.spec import _SETTINGS as DOLA
    specs = {**REGISTRY.specs, **DOLA}
    items = [{"key": k, "type": s.type.__name__, "value": LIVE_STORED.get(k, s.default),
              "source": "db" if k in LIVE_STORED else "default", "default": s.default, "description": s.description,
              "allowed": s.allowed, "read_only": False} for k, s in sorted(specs.items())]
    env = (("BILLING_TZ", "UTC"), ("PUBLIC_BASE_URL", "https://relay.example.test"), ("TRUSTED_PROXIES", ""))
    return {"items": items, "env_only": [{"name": n, "value": v, "read_only": True} for n, v in env],
            "fixed": live_fixed_limits(), "containers": live_containers()}


def live_fixed_limits() -> list[dict]:
    """The card's rows as the live api serves them: the core's, every provider's Caps rows and dola's own."""
    from relay_core.limits import fixed_limits
    from relay_core.providers import ProviderRegistry
    from relay_dola.spec import SPEC

    registry = ProviderRegistry()
    registry.register(SPEC)
    return fixed_limits(registry)


def live_containers() -> list[dict]:
    """Box-shaped: the api at 1.5 CPUs, 2 GiB, 512 pids and 64 MiB of /dev/shm, and worker-dola at 8, 12 GiB, 2048 and
    1 GiB that reported a minute ago, with its boot knobs."""
    now = int(time.time() * 1000)
    boot = {"WORKER_GRACE_S": "720", "WORKER_ID": "worker-dola", "RELAY_PROFILE_ROOT": "/profiles",
            "RELAY_CHROME_PATH": "/opt/chrome-154/chrome", "RELAY_BROWSER_HEADLESS": None}
    return [
        {"service": "api", "provider": None, "this_process": True, "reported": True, "stale": False, "cpus": "1.5",
         "memory": "2 GiB", "pids": "512", "shm": "64 MiB", "boot": None, "reported_ms": now - 3_600_000,
         "started_ms": now - 3_600_000},
        {"service": "worker-dola", "provider": "dola", "this_process": False, "reported": True, "stale": False,
         "cpus": "8", "memory": "12 GiB", "pids": "2048", "shm": "1 GiB", "boot": boot, "reported_ms": now - 60_000,
         "started_ms": now - 7_200_000}]


LIVE_PRICES = {"items": [{"provider": "dola", "model": "seedance_v2.5-fast-hd", "duration": 5, "charged": 2, "jobs": 5,
                          "disagreed": 0, "lowest_upstream": None, "substituted": 0}],
               "disagreed_total": 0, "substituted_total": 0}
LIVE_VIDEO_FRAME = ("Make exactly one video for this request and nothing else: no on-screen text, no captions, no "
                    "watermark and no follow-up questions. Request: {{client_prompt}} Reference: {{tracking_code}} "
                    "Keep the subject in frame, the camera steady and the light natural, as a calm short shot.")
LIVE_IMAGE_FRAME = "{{client_prompt}} {{tracking_code}}"


def live_frames_seed(db, live):
    data = shell_seed(db, live)
    use_the_boards_models(live)
    for kind, text in (("video", LIVE_VIDEO_FRAME), ("image", LIVE_IMAGE_FRAME)):
        db.run("INSERT INTO prompt_frames (provider, kind, model, text, created_by) VALUES (%s, %s, NULL, %s, "
               "'admin')", "dola", kind, text)
    return data


def live_frames(page, live, data):
    frames_ready(page, live, data)
    page.get_by_role("button", name="Edit frame for seedance_v2.5").click()
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_test_id("frame-preview")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def live_settings(page, live, data):
    page.route(SETTINGS_READ, lambda route: route.fulfill(content_type="application/json",
                                                          body=json.dumps(live_settings_body()))
               if route.request.method == "GET" else route.fallback())
    page.route(PRICES_READ, lambda route: route.fulfill(content_type="application/json", body=json.dumps(LIVE_PRICES)))
    page.reload()
    expect(page.get_by_label("Value of dola.prices")).to_be_visible(timeout=TIMEOUT_MS)


LIVE_LIKE = [("frames", Entry("B-Frames", "#/frames", seed=live_frames_seed, now=NOW, drive=live_frames,
                              user=BOARD_USER)),
             ("settings", Entry("B-Settings", "#/settings", seed=shell_seed, now=NOW, drive=live_settings,
                                user=BOARD_USER))]
LIVE_WIDTHS = (1440, 768, 390)
# the sub-navigation pills of a phone are a strip that scrolls sideways: P-Settings draws the third one cut at the edge
SUBNAV = ("Settings", "Boot-only values", "Measured prices", "Change password", "Fixed limits", "Containers")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", LIVE_WIDTHS)
@pytest.mark.parametrize(("page_name", "entry"), LIVE_LIKE, ids=[n for n, _e in LIVE_LIKE])
def test_frames_settings_with_live_like_data_fit(app, db, live, run_dir, page_name, entry, width, theme):
    """Nothing scrolls the page sideways, and no text of the page is cut off: a long value wraps inside its column."""
    with opened(app, db, live, entry, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("live-like-%s-%d-%s.png" % (page_name, width, theme))).write_bytes(
            full_photo(page))
        over = sideways(page)
        assert over <= 0, "the page scrolls sideways by %d px" % over
        items = extract(page, "main")
        # a value that ends in "…" with its whole text in a note (a HintTip) is cut as ruled
        noted = set(page.evaluate("[...document.querySelectorAll('main .hint-tip')].map((e) => e.textContent.trim())",
                                  isolated_context=False))
        clipped = [(t["text"][:60], t["cls"], t["rect"], t["full"]) for t in items["texts"]
                   if t["clipped"] and t["text"] not in SUBNAV and t["text"] not in noted]
        # a part that scrolls sideways (a table card) clips what is past its edge: that is a cut text too
        assert not clipped, "texts cut off: %r" % clipped


# ---- states the canvas does not draw: photographed for the side-by-side with their nearest drawn pattern ------------

def no_frames_seed(db, live):
    """The provider takes no prompt frames: the page says the core frame applies."""
    data = shell_seed(db, live)
    use_the_boards_models(live, frames=False)
    return data


def follows_default(page, live, data):
    frames_ready(page, live, data)
    page.get_by_role("button", name="Edit frame for seedance_v2.0").click()
    expect(page.get_by_text("This model follows the default.")).to_be_visible(timeout=TIMEOUT_MS)


def editing_default(page, live, data):
    frames_ready(page, live, data)
    page.locator("[data-frame-kind=video]").get_by_role("button", name="Edit the default").click()
    expect(page.get_by_role("button", name="Apply to all models of this kind")).to_be_visible(timeout=TIMEOUT_MS)


def applied(page, live, data):
    editing_default(page, live, data)
    page.get_by_role("button", name="Apply to all models of this kind").click()
    expect(page.get_by_test_id("frame-applied")).to_be_visible(timeout=TIMEOUT_MS)
    _hold_toast(page, "success")


def unlocked(page, live, data):
    frames_ready(page, live, data)
    page.get_by_label("Keep own frame of seedance_v2.5").first.uncheck()
    expect(page.locator("[data-frame-kind=video]")).to_contain_text("unlocked", timeout=TIMEOUT_MS)
    _hold_toast(page, "success")


def frames_row_hover(page, live, data):
    frames_ready(page, live, data)
    page.locator("[data-frame-kind=video] tbody tr").nth(1).hover()


def no_frames(page, live, data):
    expect(page.get_by_text("This provider has no prompt frames: the core frame applies.")).to_be_visible(
        timeout=TIMEOUT_MS)
    page.problems.clear()                                    # the 404 is a console line


def settings_row_hover(page, live, data):
    expect(page.get_by_label("Value of log_retention_days")).to_be_visible(timeout=TIMEOUT_MS)
    page.locator("#settings tbody tr").nth(2).hover()


def settings_input_focus(page, live, data):
    box = page.get_by_label("Value of log_retention_days")
    expect(box).to_be_visible(timeout=TIMEOUT_MS)
    box.focus()


def tablet_nav_stuck(page, live, data):
    expect(page.get_by_label("Value of log_retention_days")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("link", name="Measured prices").click()


def password_failed(page, live, data):
    """Any refusal that names no field is a toast."""
    page.route(re.compile(r"/admin/password$"), lambda route: route.fulfill(
        status=502, content_type="application/json", body=BAD_GATEWAY))
    page.get_by_label("Current password").fill("placeholder-secret")
    page.get_by_label("New password").fill("a-long-new-password")
    page.locator("#password").get_by_role("button", name="Save", exact=True).click()
    _hold_toast(page, "error")


def long_default_note(page, live, data):
    """The live `dola.prices` default: one line ending in "…", its whole value in the note, opened from the keyboard."""
    live_settings(page, live, data)
    page.locator("#settings .hint-tip").first.focus()
    expect(page.get_by_role("tooltip")).to_be_visible(timeout=TIMEOUT_MS)


def _frames(drive, seed=frames_seed):
    return Entry("B-Frames", "#/frames", seed=seed, now=NOW, drive=drive, user=BOARD_USER)


def _settings(drive):
    return Entry("B-Settings", "#/settings", seed=shell_seed, now=NOW, drive=drive, user=BOARD_USER)


ALL_WIDTHS = ((1440, 1000), (768, 1024), (390, 844))
UNDRAWN = [
    ("frames-follows-default", _frames(follows_default), ALL_WIDTHS),
    ("frames-editing-default", _frames(editing_default), ALL_WIDTHS),
    ("frames-applied", _frames(applied), ALL_WIDTHS[:1]),
    ("frames-unlocked", _frames(unlocked), ALL_WIDTHS[:1]),
    ("frames-row-hover", _frames(frames_row_hover), ALL_WIDTHS[:1]),
    ("frames-refusal", _frames(frame_refused), ALL_WIDTHS),
    ("frames-no-frames", _frames(no_frames, no_frames_seed), ALL_WIDTHS),
    ("frames-saved-toast", _frames(frame_saved), ALL_WIDTHS[2:]),
    ("frames-provider-list", _frames(provider_open), ALL_WIDTHS),
    ("settings-row-hover", _settings(settings_row_hover), ALL_WIDTHS[:1]),
    ("settings-input-focus", _settings(settings_input_focus), ALL_WIDTHS),
    ("settings-nav-stuck", _settings(tablet_nav_stuck), ALL_WIDTHS[1:2]),
    ("settings-saved-toast", _settings(setting_saved), ALL_WIDTHS),
    ("settings-error-toast", _settings(setting_refused), ALL_WIDTHS[2:]),
    ("settings-load-error", _settings(_failing(SETTINGS_READ)), ALL_WIDTHS),
    ("settings-loading", _settings(settings_held), ALL_WIDTHS[2:]),
    ("prices-empty", _settings(prices_empty), ALL_WIDTHS),
    ("password-failed", _settings(password_failed), ALL_WIDTHS[:1]),
    ("settings-long-default-note", _settings(long_default_note), ALL_WIDTHS[:1]),
]
PHOTOS = [(name, entry, size) for name, entry, sizes in UNDRAWN for size in sizes]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("name", "entry", "size"), PHOTOS, ids=["%s-%d" % (n, s[0]) for n, _e, s in PHOTOS])
def test_frames_settings_undrawn_states_are_photographed(app, db, live, run_dir, name, entry, size, theme):
    with opened(app, db, live, entry, theme == "dark", *size) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-%s-%d-%s.png" % (name, size[0], theme))).write_bytes(shoot(page))
        if page.get_by_role("listbox").count():
            misplaced = popup_place(page, *OPEN_LIST)
            assert misplaced is None, misplaced
        over = sideways(page)
        assert over <= 0, "the page scrolls sideways by %d px" % over
