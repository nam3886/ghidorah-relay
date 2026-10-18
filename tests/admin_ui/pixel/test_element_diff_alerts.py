"""The Alerts page against the canvas, element by element (element_diff.py), as test_element_diff.py does for Jobs.

`test_alerts_matches_the_canvas_element_by_element`: the B-, T- and P-Alerts boards in both themes, with the status
filter on All and group 1's detail open (pixel_kla.py). The page below the top bar (the top bar and the tab strip are
page-shell's, TopBar.vue) is compared whole, and each card on its own too, so a difference in one card does not move
the pairs of the next. Each comparison writes `<name>-<theme>.md` and `<name>-<theme>.side.png` into
`<run>/element-diff/` and fails while its table holds a difference that is not live data.

Live data on the boards: the canvas counts six rows in group 1 and lists four of them; no database holds both (the
detail lists every row of the group), so the seed plants the four listed rows and the row count reads 4 where the
boards write 6. B-Alerts writes a straight apostrophe in three sentences that T- and P-Alerts write with the curly one,
as the app and specs/alerts.md 4 do.

`test_alerts_undrawn_states_are_photographed`: the states the canvas does not draw (D125: the nearest drawn pattern),
each photographed at 1440, 768 and 390 in both themes from one page (the viewport is resized), into
`<run>/element-diff/undrawn-<state>-<theme>-<width>.png`.

`test_alerts_with_live_like_data_fits`: the page with data shaped like the live box (its one open group is the
read-failure group), with long values beside it, at 1440, 768 and 390: no sideways scroll, and no text cut off but the
ones drawn to end in an ellipsis; a live-shaped value is never cut."""

import base64
import json
import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from accountkit import new_account
from element_diff import diff, extract, markdown, popup_place, side_by_side
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_capture import TIMEOUT_MS, full_photo, opened, settle, shoot, sideways
from pixel_diff import compare
from pixel_manifest import ENTRIES
from pixel_render import boards
from pixel_seed import BOARD_USER, NOW, PROVIDER, WALLETS, board_user
from test_admin_alert_groups import stamp
from test_element_diff import WIDEN_JS, Check, _png, _root

from relay_core import alerts

pytestmark = pytest.mark.browser

BOARDS = {name: (w, h) for name, w, h in boards()}
THEMES = ("light", "dark")

# the pixel entry of B-Alerts (pixel_kla.py): the sample data, the status filter on All, group 1's detail open
ALERTS = next(e for e in ENTRIES if e.name == "B-Alerts")
# group 1 has 6 rows on the boards and 4 listed; the database holds the 4 (see above). The tablet writes `4 / 6` with
# the open count in its own span: the row count is the text ` / 6`
ROWS = ("6", "/ 6", "rows 6")
# B-Alerts only: the straight apostrophe of three sentences that the other boards and the spec write curly
STRAIGHT = ("Dola replied “Sorry, I can't create that video”",
            "The reply is stored as the video's answer; the job outcome decides what the customer got.",
            "One customer only: may be the customer's own words.")
# (name suffix, canvas root, app root): the page below the top bar, then each card
PARTS = (("", "main", "main.app-main"),
         ("-head", "main > div:first-child", ".page-header"),
         ("-live", 'section[aria-labelledby="liveTitle"]', 'section[aria-label="Live conditions"]'),
         ("-groups", 'section[aria-label="Alert groups"]', 'section[aria-label="Alert groups"]'),
         ("-detail", "#alertDetail", "#alertDetail"))
BOARD_CHECKS = [Check(board, replace(ALERTS, board=board), data=ROWS + STRAIGHT)
                for board in ("B-Alerts", "T-Alerts", "P-Alerts")]


# The canvas draws its filters as native selects: to the tool a select is one box (the browser paints its value and
# arrow inside it, out of the DOM's reach), while the app's FilterSelect field is a box holding its value and its arrow.
# The boxes are compared as every box is (they pair by their options' text); what the field draws inside its box is
# set aside in the tables and compared as pixels instead, select by select (`select-<board>-<n>-<theme>.side.png`).
FIELDS = ".filter-select.is-field"
FIELD_RECTS_JS = """([root, sel]) => {
  const r = root ? document.querySelector(root).getBoundingClientRect() : {left: 0, top: 0};
  return [...document.querySelectorAll(sel)].map((b) => {
    const q = b.getBoundingClientRect();
    return [q.left - r.left, q.top - r.top, q.width, q.height];
  });
}"""
# On a phone the canvas's select is borderless inside its chip, and so is the field's trigger: the tool lists a select
# whatever it paints, but a button only when it paints, so the trigger is listed here as the tool lists a box (its
# visible part, clipped by every ancestor that hides overflow), to be paired with the select and compared.
CHIP_TRIGGERS = ".filter-field.is-chip .filter-select.is-field"
TRIGGER_BOXES_JS = """([root, sel]) => {
  const rootEl = root ? document.querySelector(root) : document.documentElement;
  const rr = root ? rootEl.getBoundingClientRect() : {left: 0, top: 0};
  const r1 = (n) => Math.round(n * 10) / 10;
  return [...rootEl.querySelectorAll(sel)].map((el) => {
    const s = getComputedStyle(el), b = el.getBoundingClientRect();
    const c = {l: b.left, t: b.top, r: b.right, b: b.bottom};
    let op = 1;
    for (let a = el; a; a = a.parentElement) op *= parseFloat(getComputedStyle(a).opacity);
    for (let a = el.parentElement; a; a = a.parentElement) {
      const as = getComputedStyle(a), q = a.getBoundingClientRect();
      if (as.overflowX !== "visible") { c.l = Math.max(c.l, q.left); c.r = Math.min(c.r, q.right); }
      if (as.overflowY !== "visible") { c.t = Math.max(c.t, q.top); c.b = Math.min(c.b, q.bottom); }
    }
    const radius = ["TopLeft", "TopRight", "BottomRight", "BottomLeft"].map((d) => s["border" + d + "Radius"]);
    return {kind: "box", sig: (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 80), tag: "button",
            role: el.getAttribute("role") || "", holdsText: false,
            rect: [r1(c.l - rr.left), r1(c.t - rr.top), r1(c.r - c.l), r1(c.b - c.t)],
            full: [r1(b.left - rr.left), r1(b.top - rr.top), r1(b.width), r1(b.height)],
            style: {background: "none", border: "none / none / none / none", radius: radius.join(" "),
                    padding: ["Top", "Right", "Bottom", "Left"].map((d) => s["padding" + d]).join(" "),
                    shadow: s.boxShadow, outline: "none", color: "", checked: "", indeterminate: "", margin: "",
                    opacity: String(Math.round(op * 100) / 100)}};
  });
}"""
# the most a field's drawn value may differ from the native select's, as pixels of its crop
SELECT_MISMATCH_PCT = 0.5

# Owner deviation 27: the field draws the page's 14 px chevron 12 px in from the box edge, not Chrome's arrow. The box
# is the longest value + 12 + 36 (+ the 2 border) where the canvas's native select is the value + 14 + 27: every
# field is 7 px wider, and its value starts 2 px earlier. The deviation is declared once, here, as exact numbers:
# each canvas select gets 7 px more padding at its end before it is read (so the row it sits in lays out as the app's
# does and the boxes are compared at the declared offset, nothing else is loosened), the value text is compared 2 px
# earlier, and the arrow zone is not compared as pixels but asserted by its geometry (14 px, 12 px in, the label's
# colour).
FIELD_EXTRA_PX = 7
FIELD_TEXT_SHIFT_PX = 2
FIELD_CORNER_PX = 6          # the rounded corner and its edge are left out of the crops: they are boxes, compared above
FIELD_ENLARGE_JS = """(extra) => document.querySelectorAll('main select').forEach((s) => {
  s.style.paddingRight = (parseFloat(getComputedStyle(s).paddingRight) + extra) + 'px';
})"""
# each field's trigger: its width, its chevron's width, the gap from the chevron to the bordered edge (the trigger's,
# the phone chip's own), and the chevron's colour beside its label's
FIELD_GEOMETRY_JS = """(sel) => [...document.querySelectorAll(sel)].map((t) => {
  const svg = t.querySelector('.filter-field__chevron').getBoundingClientRect();
  const chip = t.closest('.filter-field.is-chip');
  const edge = (chip || t).getBoundingClientRect();
  const field = t.closest('.filter-field');
  const box = t.getBoundingClientRect();
  return {chip: !!chip, width: box.width, height: box.height, chevron: svg.width,
          gap: edge.right - svg.right,
          color: getComputedStyle(t.querySelector('.filter-field__chevron')).color,
          label: getComputedStyle(field.querySelector('.filter-field__label')).color};
})"""
CROP_JS = r"""async ({png, x, y, w, h}) => {
  const bitmap = await createImageBitmap(await (await fetch("data:image/png;base64," + png)).blob());
  const c = new OffscreenCanvas(w, h);
  c.getContext("2d").drawImage(bitmap, x, y, w, h, 0, 0, w, h);
  const bytes = new Uint8Array(await (await c.convertToBlob({type: "image/png"})).arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}"""


def crop(page, png: bytes, x: float, w: float, height: float) -> bytes:
    """`png` cut to x..x+w across its whole height (a PNG is the element's own screenshot, so x is its own)."""
    got = page.evaluate(CROP_JS, {"png": base64.b64encode(png).decode(), "x": int(x), "y": 0, "w": int(w),
                                  "h": int(height)})
    return base64.b64decode(got)


def assert_chevrons(geometry: list) -> None:
    """The arrow zone of every field by geometry: a 14 px chevron, 12 px in from the edge, in the label's colour."""
    bad = [g for g in geometry if abs(g["chevron"] - 14) > 0.5 or abs(g["gap"] - 12) > 0.5 or g["color"] != g["label"]]
    assert geometry and not bad, "a field's chevron is not 14 px, 12 px in, in the label colour: %r" % (bad or geometry)


def _inside(item, rects) -> bool:
    x, y, w, h = item["rect"]
    return any(x >= fx - 0.5 and y >= fy - 0.5 and x + w <= fx + fw + 0.5 and y + h <= fy + fh + 0.5
               for fx, fy, fw, fh in rects)


def _drawn_inside_a_field(items: dict, rects: list) -> dict:
    """The app's items with what a field draws inside its own box (its value text, its arrow) taken out."""
    return {**items, "texts": [t for t in items["texts"] if not _inside(t, rects)],
            "boxes": [b for b in items["boxes"] if b["kind"] == "box" or not _inside(b, rects)]}


def select_pixels(page_pngs: list, canvas_pngs: list, diff_page, out, stem: str, sizes: list | None = None) -> list:
    """Each field against its native select, as pixels of the two crops; returns (mismatch %, anti-aliased %). With
    `sizes` (per field: width, height, on a phone chip) the crops are of the value zone only: what is left of the
    arrow zone, the corner at the start left out, the app's value 2 px earlier than the canvas's; without it, the
    whole box."""
    got = []
    for n, (app_png, ref_png) in enumerate(zip(page_pngs, canvas_pngs, strict=True)):
        if sizes is not None:
            width, height, chip = sizes[n]
            if chip:        # borderless in its chip on both sides: the arrow zone is the 24 px at the end
                span = width - 24
                app_png, ref_png = crop(diff_page, app_png, 0, span, height), crop(diff_page, ref_png, 0, span, height)
            else:           # the app: border 1, then the value, up to 36 + border from the end; the canvas 2 px later
                span = width - 37 - FIELD_CORNER_PX
                app_png = crop(diff_page, app_png, FIELD_CORNER_PX, span, height)
                ref_png = crop(diff_page, ref_png, FIELD_CORNER_PX + FIELD_TEXT_SHIFT_PX, span, height)
        pixels = compare(diff_page, ref_png, app_png)
        (out / ("select-%s-%d.side.png" % (stem, n))).write_bytes(side_by_side(diff_page, ref_png, app_png,
                                                                               pixels.png))
        print("SELECT %s %d: %.3f %% + %.3f %%" % (stem, n, pixels.percent, pixels.aa_percent))
        got.append((pixels.percent, pixels.aa_percent))
    return got


def run_check(app, db, live, renderer, diff_page, run_dir, check: Check, theme: str, parts=None,
              fields: list | None = None, app_hook=None, canvas_hook=None) -> list:
    """The app in the check's state and the canvas board, each opened once; then, for each part (name suffix, canvas
    root, app root), the table and the side-by-side. Returns (result, file stem) per part. With `fields` (a list),
    each FilterSelect field is also compared with its native select as pixels, and the figures land in it. The hooks
    state a page's own declared deviation: `app_hook(page, items, app_root)` rewrites the app's items before pairing,
    `canvas_hook(page)` changes the opened board."""
    parts = parts or (("", check.canvas_root, check.app_root),)
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, board_h)
    dark = theme == "dark"
    mine = {}
    with opened(app, db, live, check.state, dark, width, height) as page:
        for suffix, _canvas, app_sel in parts:
            app_root = _root(page, app_sel)
            rects = page.evaluate(FIELD_RECTS_JS, [app_root, FIELDS], isolated_context=False)
            items = _drawn_inside_a_field(extract(page, app_root), rects)
            items["boxes"] += page.evaluate(TRIGGER_BOXES_JS, [app_root, CHIP_TRIGGERS], isolated_context=False)
            if app_hook:
                app_hook(page, items, app_root)
            mine[suffix] = (items, _png(page, app_root))
        field_pngs = [loc.screenshot(animations="disabled", caret="hide") for loc in page.locator(FIELDS).all()]
        geometry = page.evaluate(FIELD_GEOMETRY_JS, FIELDS, isolated_context=False) if fields is not None else []
        if fields is not None:
            assert_chevrons(geometry)
    renderer.open(check.state.board, dark, board_w, board_h)
    renderer.page.evaluate(FIELD_ENLARGE_JS, FIELD_EXTRA_PX, isolated_context=False)
    if canvas_hook:
        canvas_hook(renderer.page)
    renderer.settle()
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for suffix, canvas_sel, app_sel in parts:
        items, app_png = mine[suffix]
        canvas_root = _root(renderer.page, canvas_sel)
        if check.widen:
            renderer.page.evaluate(WIDEN_JS, [canvas_root, items["width"]], isolated_context=False)
            renderer.settle()
        theirs = extract(renderer.page, canvas_root)
        ref_png = _png(renderer.page, canvas_root)
        pixels = compare(diff_page, ref_png, app_png)
        # what an accepted deviation leaves out or adds is set aside before the pairing
        theirs["texts"] = [t for t in theirs["texts"] if t["text"] not in check.accepted]
        items["texts"] = [t for t in items["texts"] if t["text"] not in check.accepted_app]
        items["boxes"] = [b for b in items["boxes"] if b["sig"] not in check.accepted_app]
        name = check.name + suffix
        res = diff(name, theirs, items, data=check.data, data_app=check.data_app, loose=check.loose)
        stem = "%s-%s" % (name, theme)
        notes = ("Canvas %s %s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% "
                 "anti-aliased." % (check.state.board, canvas_sel or "page", app_sel or "page", width, height,
                                    pixels.percent, pixels.aa_percent))
        (out / (stem + ".md")).write_text(markdown(res, "%s, %s" % (name, theme), notes), encoding="utf-8")
        (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
        print("ELEMENTS %s %s: %d differences, %d canvas only, %d app only, %d data; pixels %.3f %% + %.3f %%" % (
            name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), len(res.data),
            pixels.percent, pixels.aa_percent))
        results.append((res, stem))
    if fields is not None:
        canvas_pngs = [loc.screenshot(animations="disabled", caret="hide")
                       for loc in renderer.page.locator("main select").all()]
        sizes = [(g["width"], g["height"], g["chip"]) for g in geometry]
        fields += select_pixels(field_pngs, canvas_pngs, diff_page, out, "%s-%s" % (check.name, theme), sizes)
    return results


def failed(results) -> list:
    return ["%s.md (%d)" % (stem, res.failures) for res, stem in results if res.failures]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", BOARD_CHECKS, ids=lambda c: c.name)
def test_alerts_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    fields = []
    bad = failed(run_check(app, db, live, renderer, diff_page, run_dir, check, theme, PARTS, fields))
    assert not bad, "%s %s: element differences in %s" % (check.name, theme, ", ".join(bad))
    assert len(fields) == 2, "the Status and Kind fields against the two native selects: %r" % fields
    off = [(n, f) for n, f in enumerate(fields) if f[0] > SELECT_MISMATCH_PCT]
    assert not off, "a field draws its value or arrow apart from the native select (n, (mismatch %%, aa %%)): %r" % off


# ---- states the canvas does not draw: photographed at the three widths, beside their nearest drawn pattern -----------
WIDTHS = ((1440, 1410), (768, 1607), (390, 2480))
GROUPS = re.compile(r"/admin/alerts/groups\?")
STATS = re.compile(r"/admin/stats(\?|$)")


# An Alerts filter, found in the Alerts card: right after sign-in the Jobs page, with its own Status filter, may still
# be on its way out.
def _field(page, label):
    return page.locator('section[aria-label="Alert groups"]').get_by_role("combobox", name=label, exact=True)


def _filter(page, label, option):
    _field(page, label).click()
    page.get_by_role("option", name=option, exact=True).click()


def _all(page):
    _filter(page, "Status", "All")
    expect(page.get_by_test_id("alerts-count")).to_have_text(re.compile(r"^\d+ groups?$"), timeout=TIMEOUT_MS)
    expect(page.locator('[data-state="ready"]')).to_be_visible(timeout=TIMEOUT_MS)


def _details(n):
    def act(page):
        _all(page)
        page.get_by_role("button", name="Details of").nth(n).click()
        expect(page.get_by_test_id("alert-detail")).to_be_visible(timeout=TIMEOUT_MS)
        page.mouse.move(0, 0)
    return act


def _as_it_opens(page):
    expect(page.get_by_test_id("alerts-count")).to_have_text("1 group", timeout=TIMEOUT_MS)


def _loading(page):
    page.route(GROUPS, lambda route: None)
    page.reload()
    expect(page.locator('[data-state="loading"]')).to_be_visible(timeout=TIMEOUT_MS)


def _error(page):
    page.route(GROUPS, lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.reload()
    expect(page.locator('[data-state="error"]')).to_be_visible(timeout=TIMEOUT_MS)


def _empty(page):
    _filter(page, "Kind", "drift")
    expect(page.locator('[data-state="empty"]')).to_be_visible(timeout=TIMEOUT_MS)


def _no_live(page):
    def none(route):
        answer = route.fetch()
        route.fulfill(response=answer, json={**answer.json(), "alerts": []})
    page.route(STATS, none)
    page.reload()
    expect(page.get_by_text("No live condition right now.")).to_be_visible(timeout=TIMEOUT_MS)
    _all(page)


def _refreshing(page):
    _all(page)
    page.route(GROUPS, lambda route: None)
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator('[data-state="refreshing"]')).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def _hover(locate):
    def again(page):
        page.mouse.move(0, 0)
        target = locate(page)
        if target.count():
            target.first.hover()
    return again


def _focus_status(page):
    select = _field(page, "Status")
    select.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    # the keyboard moved last, so a focus set from here still shows the ring (a resize can take the focus away)
    if not select.evaluate("e => e === document.activeElement"):
        select.focus()
    expect(select).to_be_focused()


def _toast(page):
    # the acknowledge is answered without a write, so it can be pressed again at the next width
    page.route(re.compile(r"/acknowledge$"), lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"acknowledged": 4, "group": None})))
    page.mouse.move(0, 0)
    page.get_by_role("button", name="Acknowledge", exact=True).first.click()
    expect(page.get_by_text("Acknowledged 4 row(s)")).to_be_visible(timeout=TIMEOUT_MS)


def _row(page):
    return page.locator('section[aria-label="Alert groups"] tbody tr').nth(1)


# (state, what reaches it once, what is done again at each width, a viewport photo instead of the whole page)
UNDRAWN = [
    ("as-it-opens", _as_it_opens, None, False),          # D121: Status Open, the one open group
    ("loading", _loading, None, False),                  # B-States loading: the shared skeleton
    ("error", _error, None, False),                      # B-States error: the card with Retry, the canvas wording
    ("empty", _empty, None, False),                      # B-States no results: the shared empty block
    ("no-live-condition", _no_live, None, False),        # the live card with no row
    ("refreshing", _refreshing, None, False),            # B-States refreshing: the rows at 60 %
    ("billing-detail", _details(1), None, False),        # a clamp group: the reply table's frame, the billing facts
    ("acknowledged-detail", _details(3), None, False),   # nothing open: no Acknowledge, the rows say who and when
    ("hover-details", _all, _hover(lambda p: p.get_by_role("button", name="Details of")), False),
    ("hover-acknowledge", _all, _hover(lambda p: p.get_by_role("button", name="Acknowledge", exact=True)), False),
    ("hover-row", _all, _hover(_row), False),            # .trow:hover td raised
    ("focus-select", _focus_status, _focus_status, False),
    ("toast-acknowledged", _all, _toast, True),          # B-States toasts: the shared success toast
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("state", "reach", "again", "viewport"), UNDRAWN, ids=[u[0] for u in UNDRAWN])
def test_alerts_undrawn_states_are_photographed(app, db, live, run_dir, state, reach, again, viewport, theme):
    entry = replace(ALERTS, drive=None)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    with opened(app, db, live, entry, theme == "dark", *WIDTHS[0]) as page:
        reach(page)
        for width, height in WIDTHS:
            page.set_viewport_size({"width": width, "height": height})
            settle(page)
            if again:
                again(page)
                settle(page)
            png = shoot(page) if viewport else full_photo(page)
            (out / ("undrawn-%s-%s-%d.png" % (state, theme, width))).write_bytes(png)


# ---- data shaped like the live box -----------------------------------------------------------------------------------
SAIGON = timezone(timedelta(hours=7))
# The live box (read 17/10, read-only, lengths and shapes only): one open unclear group, the one with no reply text (the
# read failure), one row, on a failed video job with a 27-character title and a 15-character error code; no billing
# alert; key labels of 0 to 9 characters (three keys without one, named by an 11-character prefix); account labels
# of 6 characters. Beside it: a long reply under three keys with twelve rows, a long title and a long code, a clamp on
# the key without a label and a drift on a key with a long label.
LIVE_TITLE = "Rainy Tokyo crossing at 9pm"
LIVE_CODE = "render.deadline"
LONG_REPLY = ("I am sorry, but I cannot help create that video, because it may show a real person in a way that "
              "could be misleading; please try a different description of the scene without that person in it")
LONG_TITLE = ("Slow aerial push-in over a misty rice terrace at sunrise, farmers in conical hats, golden haze, "
              "35 mm film")
LONG_KEY = "Partner Collective Southeast Asia, wholesale"
TITLES = ("Cat on a sofa, sunset light", "Logo reveal, purple smoke", "Portrait, soft light", "Lantern festival")
# the live-shaped values: always drawn whole, at every width
LIVE_VALUES = ("k7a-team2", "m2c-shop1", "rk-q7mfz2kd", "d07-12", LIVE_TITLE, LIVE_CODE)
# the texts drawn to end in an ellipsis: the stored reply and the job title of the desktop reply table
ELLIPSIS = re.compile(r"\bad-reply__text\b|\bad-job\b")
# every table box that scrolls sideways (desktop) to its start or its end; returns by how much each one overflows
SCROLL_TO_END_JS = """(end) => [...document.querySelectorAll('.g-scroll, .ad__scroll')].map((box) => {
  box.scrollLeft = end ? box.scrollWidth : 0;
  return Math.round(box.scrollWidth - box.clientWidth);
})"""
JOB_SQL = ("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, "
           "after_sent, submit_timeout_s, wallet_kind, price, title, error_code, finished_at) VALUES (%s, %s, %s, "
           "'generate', 'video', 'm', 'p', %s, %s, 'detach', 'reconcile', 60, 'video', 0, %s, %s, now())")


def _at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


def _reply(db, key_id, text, when, status, code, account, title):
    job_id = uuid.uuid4()
    db.run(JOB_SQL, job_id, PROVIDER, key_id, "marker-%s" % job_id, status, title, code)
    if status == "done":
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1)", job_id)
    built = alerts.build(PROVIDER, text)
    return db.one("INSERT INTO unclear_replies (provider, job_id, account_id, text, wording, group_id, at) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id", PROVIDER, job_id, account, built.text,
                  built.wording, built.group_id, when)[0]


def live_like_seed(db, live):
    board_user(live)
    keys = {}
    for label, prefix in (("k7a-team2", "rk-k7ateam2"), ("m2c-shop1", "rk-m2cshop1"), ("", "rk-q7mfz2kd"),
                          (LONG_KEY, "rk-l0nglbl1")):
        k = new_key(db, wallets=WALLETS)
        db.run("UPDATE api_keys SET label = %s, key_prefix = %s WHERE id = %s", label, prefix, k)
        keys[label or prefix] = k
    account = new_account(db, label="d07-12", checked=True, provider=PROVIDER)
    _reply(db, keys["rk-q7mfz2kd"], "", _at(15, 20, 41), "failed", LIVE_CODE, account, LIVE_TITLE)
    three = (keys["k7a-team2"], keys["m2c-shop1"], keys["rk-q7mfz2kd"])
    for i in range(12):
        refused = i % 4 == 0
        _reply(db, three[i % 3], LONG_REPLY, _at(15, 8, i), "failed" if refused else "done",
               "dola.content_refused_by_the_safety_review" if refused else None, account if i % 2 else None,
               LONG_TITLE if i == 11 else TITLES[i % len(TITLES)])
    ledger = db.one("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) VALUES (%s, %s, "
                    "'video', 'adjust', 0, current_date) RETURNING id", keys["rk-q7mfz2kd"], PROVIDER)[0]
    db.run("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, used_total, at) "
           "VALUES ('clamp', %s, %s, 'video', %s, 5, 2, 3, %s)", keys["rk-q7mfz2kd"], PROVIDER, ledger,
           _at(15, 6, 14))
    drift = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, amount, used_day, used_total, detail, "
                   "at) VALUES ('drift', %s, %s, 'image', 0, 1, 9, 'used_total 9, ledger says 0', %s) "
                   "RETURNING id", keys[LONG_KEY], PROVIDER, _at(14, 9, 21))[0]
    stamp(db, "billing_alerts", drift)


# which group's detail is open: the list is open groups first (the clamp, the read failure, the long reply), then
# the drift
LIVE_LIKE = (("read-failure", 1), ("long-reply", 2), ("clamp", 0))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("view", "nth"), LIVE_LIKE, ids=[v[0] for v in LIVE_LIKE])
def test_alerts_with_live_like_data_fits(app, db, live, run_dir, view, nth, theme):
    open_detail = _details(nth)
    entry = replace(ALERTS, seed=live_like_seed, drive=lambda page, live, data: open_detail(page), user=BOARD_USER,
                    now=NOW)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    with opened(app, db, live, entry, theme == "dark", *WIDTHS[0]) as page:
        for width, height in WIDTHS:
            page.set_viewport_size({"width": width, "height": height})
            settle(page)
            (out / ("live-like-%s-%s-%d.png" % (view, theme, width))).write_bytes(
                full_photo(page))
            over = sideways(page)
            assert over <= 0, "%d: the page scrolls sideways by %d px" % (width, over)
            # A wide table scrolls sideways inside its own box, as the canvas wraps it: a text it hides at one end is
            # reachable at the other. A text is cut only when it is clipped with the boxes at both ends.
            ends = []
            for end in (False, True):
                ends.append(page.evaluate(SCROLL_TO_END_JS, end, isolated_context=False))
                ends.append({(t["text"], t["cls"]) for t in extract(page, "main.app-main")["texts"] if t["clipped"]})
            print("LIVE-LIKE %s %s %d: table boxes scrolling sideways by %s px" % (view, theme, width, ends[0]))
            cut = ends[1] & ends[3]
            clipped = sorted(t for t in cut if not ELLIPSIS.search(t[1]))
            assert not clipped, "%d: texts cut off where the canvas draws them whole: %r" % (width, clipped)
            live_cut = sorted(text for text, _cls in cut if any(v in text for v in LIVE_VALUES))
            assert not live_cut, "%d: live-shaped values cut off: %r" % (width, live_cut)



# ---- the open list of a field (the canvas draws none: FilterSelect's listbox, the B-States Provider list) ------------
# Opened at 1440, 768 and 390, viewport photos: it must sit 6 px under its box (above it near the bottom of the
# window), never on it; Escape closes it and leaves the focus on the field. On a phone the box is the chip.
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", ("Status", "Kind"))
def test_an_open_field_list_sits_by_its_box_at_every_width(app, db, live, run_dir, name, theme):
    entry = replace(ALERTS, drive=None)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    with opened(app, db, live, entry, theme == "dark", *WIDTHS[0]) as page:
        expect(page.get_by_test_id("alerts-count")).to_have_text("1 group", timeout=TIMEOUT_MS)
        for width, _height in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            settle(page)
            field = _field(page, name)
            field.click()
            expect(page.get_by_role("listbox")).to_be_visible()
            settle(page)
            (out / ("field-list-%s-%s-%d.png" % (name.lower(), theme, width))).write_bytes(
                page.screenshot(animations="disabled", caret="hide"))
            box = ".filter-field.is-open" if width < 768 else '[role="combobox"][aria-expanded="true"]'
            misplaced = popup_place(page, '[role="listbox"]', box)
            assert misplaced is None, "%d: %s" % (width, misplaced)
            page.keyboard.press("Escape")
            expect(page.get_by_role("listbox")).to_have_count(0)
            expect(field).to_be_focused()
