"""The API keys page against the canvas, element by element (element_diff.py, as the Jobs page in test_element_diff.py),
its states the canvas does not draw, and the page with data shaped like the live box's.

`test_keys_matches_the_canvas_element_by_element`: one comparison per check and theme. The whole B-, T- and P-Keys
boards (the one-time key callout of Partner D, its row tinted, Partner B disabled), the callout after Copy (the board's
own Copied state, reached by clicking on both sides), the Edit dialog of B-KeyForm (the dialog only: the board's page
behind the scrim is a mock), and the keys states that B-States draws (the empty list, the delete confirm, and the New
key form refusing an empty label: its field, its tooltip and the disabled Create key). A state card of B-States is
widened to the app's width first. Each comparison writes, into `<run>/element-diff/`,
`<name>-<theme>.md` (the table) and `<name>-<theme>.side.png` (canvas | app | pixel diff), and fails while the table
holds a difference, an element missing on one side, or a text that differs and is not live data. A dialog must also sit
where the board draws it in the window, and the tooltip where it is drawn against its button.

`test_keys_undrawn_states_are_photographed`: hovers, focus, the New key dialog at every width, a refused create, the
list loading and failing, the empty list on a tablet and a phone, the toast after a delete (D125: drawn from the nearest
drawn pattern), viewport photos in both themes.

`test_keys_with_live_like_data_fits`: keys shaped like the live box's (no label and an 11-character prefix, short
labels, any model, unlimited wallets) plus one key with every long value, at 1440, 768 and 390 in both themes: nothing
is cut off and the page never scrolls sideways."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pixel_manifest  # before pixel_kla: the two import each other, and the manifest must load first
import pytest
from element_diff import EDGE_PX, diff, extract, markdown, popup_place, side_by_side
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_capture import opened, shoot
from pixel_diff import compare
from pixel_kla import keys_callout, keys_edit, keys_seed, set_key
from pixel_seed import BOARD_USER, NOW, PROVIDER, WALLETS, board_user
from test_element_diff import BOARDS, THEMES, WIDEN_JS, Check, _png, _root
from test_element_diff_alerts import FIELD_RECTS_JS, FIELDS, SELECT_MISMATCH_PCT, _drawn_inside_a_field, select_pixels

pytestmark = pytest.mark.browser

TIMEOUT_MS = 10_000
STATES = "B-States"
DESKTOP, TABLET = (1440, 1000), (768, 1320)
PHONE_VIEW = (390, 844)          # a phone window for the photos (the board is drawn 2280 tall)
ROOT_BOX_JS = ("s => { const r = document.querySelector(s).getBoundingClientRect(); "
               "return [r.x, r.y, r.width, r.height]; }")


@dataclass(frozen=True)
class KeyCheck(Check):
    canvas_click: str | None = None     # clicked on the canvas board first: a state its own logic draws (Copied)
    same_place: bool = False            # the root must also sit where the board draws it in the window (a dialog)
    anchor: tuple = ()                  # (canvas, app) selectors of an element the root must sit against as drawn
    fields: bool = False                # FilterSelect fields against the canvas's native selects (see SELECT_VALUES_JS)


def _keys(board, drive, seed=keys_seed):
    """The keys page as `board` draws it: the boards' user and clock, the seed's data, `drive`'s state."""
    return pixel_manifest.Entry(board, "#/keys", seed=seed, now=NOW, drive=drive, user=BOARD_USER)


# ---- the boards --------------------------------------------------------------------------------------------------

def copied(page, live, data):
    """The callout after its Copy: the label reads Copied (for 2 s)."""
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    keys_callout(page, live, data)
    page.get_by_test_id("new-key").get_by_role("button", name="Copy").click()
    expect(page.get_by_test_id("new-key").get_by_role("button", name="Copied")).to_be_visible()
    page.mouse.move(0, 0)


BOARD_CHECKS = [KeyCheck(board, _keys(board, keys_callout)) for board in ("B-Keys", "T-Keys", "P-Keys")]
COPIED = KeyCheck("B-Keys-copied", _keys("B-Keys", copied), 'section[role="status"]', '[data-testid="new-key"]',
                  canvas_click='section[role="status"] button.solid')

# The model rows read `<kind> · <display name>` as drawn (D129): the harness's catalog sends the boards' names. The
# Mode selects are native on the canvas and FilterSelect fields in the app: to the tool a select is one box (the browser
# paints its value and arrow inside it), so what a field draws inside its box is set aside in the table and compared as
# pixels instead, field by field (`select-B-KeyForm-<theme>-<n>.side.png`), as Alerts does. Our renderer leaves a
# select on its first option (it writes `value` as an attribute, which a select ignores): the board's own values are
# put in first (video limit, image unlimited).
FORM = KeyCheck("B-KeyForm", _keys("B-KeyForm", keys_edit), '[role="dialog"]', ".key-dialog", same_place=True,
                fields=True)
SELECT_VALUES_JS = ("() => { for (const s of document.querySelectorAll('select[value]')) "
                    "s.value = s.getAttribute('value'); }")


# ---- the states of B-States --------------------------------------------------------------------------------------

def no_keys_seed(db, live):
    board_user(live)


def no_keys(page, live, data):
    expect(page.locator(".keys-card [data-state=empty]")).to_be_visible(timeout=TIMEOUT_MS)


def delete_partner_b(page, live, data):
    page.get_by_role("button", name="Delete key Partner B").click()
    expect(page.locator(".rl-confirm")).to_be_visible(timeout=TIMEOUT_MS)


def label_required(page, live, data):
    """New key with no label: a click on Create key marks the field; the pointer stays on it, so its reason shows."""
    page.get_by_role("button", name="New key").first.click()
    box = page.get_by_role("dialog", name="New API key")
    expect(box).to_be_visible(timeout=TIMEOUT_MS)
    # aria-disabled, not disabled: the click reaches the button (Playwright waits for "enabled" unless forced)
    box.get_by_role("button", name="Create key").click(force=True)
    expect(box.get_by_text("A label is required.")).to_be_visible(timeout=TIMEOUT_MS)
    box.get_by_role("button", name="Create key").hover()
    expect(box.get_by_role("tooltip")).to_be_visible(timeout=TIMEOUT_MS)


def _open_mode(page, wallet: str = "dola video") -> None:
    page.get_by_role("group", name=wallet, exact=True).get_by_role("combobox", name="Mode").click()
    expect(page.get_by_role("listbox")).to_be_visible(timeout=TIMEOUT_MS)


def mode_list(page, live, data):
    """The Edit dialog with the video wallet's Mode list open (its second option, limit, chosen)."""
    keys_edit(page, live, data)
    _open_mode(page)


def new_mode_list(page, live, data):
    """The New key form with the video wallet's Mode list open: its first option, closed, chosen, as the drawn list."""
    page.get_by_role("button", name="New key").first.click()
    expect(page.get_by_role("dialog", name="New API key")).to_be_visible(timeout=TIMEOUT_MS)
    _open_mode(page)


DRAWN_CREATE, APP_CREATE = 'button[aria-describedby="fvTip"]', 'button[aria-describedby="key-create-why"]'
# The open Mode list against the drawn Provider list (B-States 1440): its four options and the word "Provider" are that
# list's data, the three modes this one's; the list's height follows the number of options. It hangs 6 px under its
# field (the field's box: on a phone the chip is the box), and it is on top: the element at its centre is the list (the
# tables cannot see one layer under another, a dialog over the list).
LISTBOX_DATA = ("All", "dola", "vibes", "meta", "Provider")
MODE_WORDS = ("closed", "limit", "unlimited")
OPEN_LIST = ('[role="listbox"]', ".filter-field.is-open")
ON_TOP_JS = """sel => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return Boolean(top && el.contains(top));
}"""
STATE_CHECKS = [
    KeyCheck("state-empty", _keys(STATES, no_keys, no_keys_seed), 'section[aria-label="API keys, empty"]',
             ".keys-card", DESKTOP, widen=True),
    KeyCheck("state-confirm-delete", _keys(STATES, delete_partner_b), '[role="dialog"][aria-labelledby="cfDel"]',
             ".rl-confirm", DESKTOP, widen=True),
    KeyCheck("state-label-field", _keys(STATES, label_required), "div:has(> #fvLabel)",
             ".key-dialog .kd__field:has(#key-label)", DESKTOP, widen=True),
    KeyCheck("state-label-tip", _keys(STATES, label_required), "#fvTip", "#key-create-why", DESKTOP,
             anchor=(DRAWN_CREATE, APP_CREATE)),
    KeyCheck("state-label-create", _keys(STATES, label_required), DRAWN_CREATE, APP_CREATE, DESKTOP),
    KeyCheck("state-mode-list", _keys(STATES, new_mode_list), '[role="listbox"][aria-label="Provider"]@0',
             '[role="listbox"]',
             DESKTOP, data=LISTBOX_DATA, data_app=MODE_WORDS, loose={"listbox": "h"}, popup=OPEN_LIST),
]
CHECKS = [*BOARD_CHECKS, COPIED, FORM, *STATE_CHECKS]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_keys_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, board_h)
    dark = theme == "dark"
    with opened(app, db, live, check.state, dark, width, height) as page:
        app_root = _root(page, check.app_root)
        mine = extract(page, app_root)
        app_png = _png(page, app_root)
        app_box = page.evaluate(ROOT_BOX_JS, app_root, isolated_context=False) if check.same_place else None
        app_place = _against(page, app_root, check.anchor[1]) if check.anchor else None
        misplaced_list = popup_place(page, *check.popup) if check.popup else None
        if check.popup and not page.evaluate(ON_TOP_JS, check.popup[0], isolated_context=False):
            misplaced_list = "the open list is under another layer (the element at its centre is not the list)"
        field_pngs = []
        if check.fields:
            rects = page.evaluate(FIELD_RECTS_JS, [app_root, FIELDS], isolated_context=False)
            mine = _drawn_inside_a_field(mine, rects)
            field_pngs = [loc.screenshot(animations="disabled", caret="hide") for loc in page.locator(FIELDS).all()]
    renderer.open(check.state.board, dark, board_w, board_h)
    if check.canvas_click:
        renderer.page.click(check.canvas_click)
        renderer.settle()
    if check.fields:
        renderer.page.evaluate(SELECT_VALUES_JS, isolated_context=False)
    canvas_root = _root(renderer.page, check.canvas_root)
    if check.widen:
        renderer.page.evaluate(WIDEN_JS, [canvas_root, mine["width"]], isolated_context=False)
        renderer.settle()
    theirs = extract(renderer.page, canvas_root)
    ref_png = _png(renderer.page, canvas_root)
    canvas_box = renderer.page.evaluate(ROOT_BOX_JS, canvas_root, isolated_context=False) if check.same_place else None
    canvas_place = _against(renderer.page, canvas_root, check.anchor[0]) if check.anchor else None
    pixels = compare(diff_page, ref_png, app_png)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (check.name, theme)
    selects = []
    if check.fields:
        canvas_pngs = [loc.screenshot(animations="disabled", caret="hide")
                       for loc in renderer.page.locator(canvas_root + " select").all()]
        selects = select_pixels(field_pngs, canvas_pngs, diff_page, out, stem)

    theirs["texts"] = [t for t in theirs["texts"] if t["text"] not in check.accepted]
    mine["texts"] = [t for t in mine["texts"] if t["text"] not in check.accepted_app]
    mine["boxes"] = [b for b in mine["boxes"] if b["sig"] not in check.accepted_app]
    res = diff(check.name, theirs, mine, data=check.data, data_app=check.data_app, loose=check.loose)
    misplaced = None
    if check.same_place and any(abs(c - a) > EDGE_PX for c, a in zip(canvas_box, app_box, strict=True)):
        misplaced = "the dialog is at %s in the window, the board draws it at %s" % (
            [round(v, 1) for v in app_box], [round(v, 1) for v in canvas_box])
    if check.anchor and any(abs(c - a) > EDGE_PX for c, a in zip(canvas_place, app_place, strict=True)):
        misplaced = "against its anchor (left, top, right, bottom) it sits at %s, the board draws %s" % (
            [round(v, 1) for v in app_place], [round(v, 1) for v in canvas_place])
    notes = ("Canvas %s%s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% anti-aliased."
             % (check.state.board, " " + check.canvas_root if check.canvas_root else "", check.app_root or "page",
                width, height, pixels.percent, pixels.aa_percent))
    if check.same_place or check.anchor:
        notes += " Place: %s." % (misplaced or "as drawn")
    if check.popup:
        notes += " Open list against its field: %s." % (misplaced_list or "6 px clear")
    if check.fields:
        notes += " Fields against the native selects (pixels of each crop): %s." % ", ".join(
            "%.3f %% + %.3f %%" % f for f in selects)
    (out / (stem + ".md")).write_text(markdown(res, "%s, %s" % (check.name, theme), notes), encoding="utf-8")
    (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
    print("ELEMENTS %s %s: %d differences, %d canvas only, %d app only, %d data; pixels %.3f %% + %.3f %%" % (
        check.name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), len(res.data),
        pixels.percent, pixels.aa_percent))
    assert res.failures == 0, "%s %s: %d element differences, see %s.md" % (check.name, theme, res.failures, stem)
    assert misplaced is None, misplaced
    assert misplaced_list is None, misplaced_list
    if check.fields:
        assert len(selects) == 2, "the video and image Mode fields against the two native selects: %r" % selects
        off = [(n, f) for n, f in enumerate(selects) if f[0] > SELECT_MISMATCH_PCT]
        assert not off, "a Mode field drawn unlike its native select (pixels of its crop): %r" % off


def _against(page, root: str, anchor: str) -> list:
    """The root's left, top, right and bottom edges less the anchor's."""
    r, a = (page.evaluate(ROOT_BOX_JS, sel, isolated_context=False) for sel in (root, anchor))
    return [r[0] - a[0], r[1] - a[1], r[0] + r[2] - a[0] - a[2], r[1] + r[3] - a[1] - a[3]]


# ---- states the canvas does not draw: photographed for the side-by-side with their nearest drawn pattern ------------
KEYS_API = re.compile(r"/admin/keys(\?|$)")


def _ready(page):
    expect(page.locator(".keys-card [data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)


def _hover(name):
    def act(page, live, data):
        _ready(page)
        page.get_by_role("button", name=name, exact=True).hover()
    return act


def hover_row(page, live, data):
    _ready(page)
    page.locator("tbody tr").nth(3).hover()


def focus_switch(page, live, data):
    _ready(page)
    switch = page.get_by_role("switch", name="Enable key Shop B")
    switch.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(switch).to_be_focused()


def new_key_dialog(page, live, data):
    page.get_by_role("button", name="New key").first.click()
    expect(page.get_by_role("dialog", name="New API key")).to_be_visible(timeout=TIMEOUT_MS)


def refused_create(page, live, data):
    """The server refuses the label: the dialog says so from its fixed table and outlines the field."""
    def answer(route):
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(status=422, content_type="application/json",
                      body='{"error": {"code": "input.invalid", "message": "label", "param": "label"}}')

    page.route(KEYS_API, answer)
    new_key_dialog(page, live, data)
    page.get_by_role("dialog").get_by_label("Label", exact=True).fill("Partner E")
    page.get_by_role("dialog").get_by_role("button", name="Create key").click()
    expect(page.get_by_role("dialog").get_by_role("alert")).to_be_visible(timeout=TIMEOUT_MS)


def load_fails(page, live, data):
    page.route(KEYS_API, lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.reload()
    expect(page.locator(".keys-card [data-state=error]")).to_be_visible(timeout=TIMEOUT_MS)


def load_held(page, live, data):
    page.route(KEYS_API, lambda route: None)
    page.reload()
    expect(page.locator(".keys-card [data-state=loading]")).to_be_visible(timeout=TIMEOUT_MS)


def delete_toast(page, live, data):
    """Partner B has jobs: Delete disables it, and the toast says so."""
    delete_partner_b(page, live, data)
    page.locator(".rl-confirm").get_by_role("button", name="Delete").click()
    expect(page.get_by_text("Key has jobs: disabled instead")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


UNDRAWN = [
    ("hover-row", hover_row, keys_seed, (DESKTOP,)),
    ("hover-delete", _hover("Delete key Shop B"), keys_seed, (DESKTOP, PHONE_VIEW)),
    ("hover-edit", _hover("Edit key Shop B"), keys_seed, (DESKTOP,)),
    ("hover-copy-prefix", _hover("Copy key prefix of Shop B"), keys_seed, (DESKTOP,)),
    ("focus-switch", focus_switch, keys_seed, (DESKTOP,)),
    ("new-key-dialog", new_key_dialog, keys_seed, (DESKTOP, TABLET, PHONE_VIEW)),
    ("refused-create", refused_create, keys_seed, (DESKTOP, PHONE_VIEW)),
    ("label-required", label_required, keys_seed, (DESKTOP, PHONE_VIEW)),
    ("mode-list", mode_list, keys_seed, (DESKTOP, TABLET, PHONE_VIEW)),
    ("load-error", load_fails, keys_seed, (DESKTOP, TABLET, PHONE_VIEW)),
    ("loading", load_held, keys_seed, (DESKTOP, TABLET, PHONE_VIEW)),
    ("empty", no_keys, no_keys_seed, (TABLET, PHONE_VIEW)),
    ("delete-toast", delete_toast, keys_seed, (DESKTOP, PHONE_VIEW)),
]
UNDRAWN_CASES = [(name, drive, seed, size) for name, drive, seed, sizes in UNDRAWN for size in sizes]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("state", "drive", "seed", "size"), UNDRAWN_CASES,
                         ids=["%s-%d" % (c[0], c[3][0]) for c in UNDRAWN_CASES])
def test_keys_undrawn_states_are_photographed(app, db, live, run_dir, state, drive, seed, size, theme):
    entry = _keys("B-Keys", drive, seed)
    with opened(app, db, live, entry, theme == "dark", *size) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-%s-%d-%s.png" % (state, size[0], theme))).write_bytes(shoot(page))
        sideways = page.evaluate("document.documentElement.scrollWidth - innerWidth", isolated_context=False)
        assert sideways <= 0, "the page scrolls sideways by %d px" % sideways
        if page.get_by_role("listbox").count():
            misplaced = popup_place(page, *OPEN_LIST)
            assert misplaced is None, misplaced
            assert page.evaluate(ON_TOP_JS, OPEN_LIST[0], isolated_context=False), (
                "the open list is under another layer (the element at its centre is not the list)")


# ---- data shaped like the live box -------------------------------------------------------------------------------
SAIGON = timezone(timedelta(hours=7))
TODAY = datetime(2026, 10, 15, tzinfo=SAIGON)
LONG_LABEL = "Partner Collective Southeast Asia, wholesale video and image"
FIVE_MODELS = ["seedance_v2.5", "seedance_v2.0", "seedance_v1.0", "seedream-4.5", "seedream-5.0-pro"]
# The live box's keys (read 17/10, lengths and shapes only): three without a label, named by an 11-character prefix
# `aa-aaaaaaaa`; two labelled `aNa-aaaaN` (9 characters), disabled; every key any model; both dola wallets unlimited;
# 1 to 60 jobs; max unfinished empty, 5 or 40; all made this month. Then one key with every long value.
# (label, prefix, enabled, models, video, image, jobs, max unfinished, created)
ANY = ["*"]
LIVE_KEYS = (
    (None, "rk-q7mfz2kd", True, ANY, ("unlimited", 0), ("unlimited", 0), 1, None, TODAY + timedelta(hours=19)),
    (None, "rk-hwpaxnkd", True, ANY, ("unlimited", 2), ("unlimited", 0), 2, None, TODAY + timedelta(hours=9)),
    (None, "rk-a_bq_xmz", True, ANY, ("unlimited", 2), ("unlimited", 0), 1, None, TODAY - timedelta(days=4)),
    ("k7a-team2", "rk-tbqm4aho", False, ANY, ("unlimited", 2), ("unlimited", 1), 2, 5, TODAY - timedelta(days=9)),
    ("m2c-shop1", "rk-dhqwz8c3", False, ANY, ("unlimited", 8), ("unlimited", 49), 60, 40, TODAY - timedelta(days=12)),
    (LONG_LABEL, "rk-longestx", True, FIVE_MODELS, ("limit", 2500, 99999, 1234, 87654),
     ("limit", 900, 50000, 899, 49999), 8765, 0, datetime(2026, 1, 3, 12, tzinfo=SAIGON)),
)
LIVE_VALUES = ("rk-q7mfz2kd", "rk-hwpaxnkd", "rk-a_bq_xmz", "k7a-team2", "m2c-shop1", "(no label)")


def live_like_keys_seed(db, live):
    board_user(live)
    for label, prefix, enabled, models, video, image, jobs, max_unfinished, created in LIVE_KEYS:
        key_id = new_key(db, wallets=WALLETS)
        set_key(db, key_id, label or "", prefix, enabled, models, video, image, created)
        db.run("UPDATE api_keys SET max_unfinished = %s WHERE id = %s", max_unfinished, key_id)
        db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, "
               "completion, after_sent, submit_timeout_s, wallet_kind, price, error_code, finished_at) "
               "SELECT gen_random_uuid(), %s, %s, 'generate', 'video', 'm', 'p', 'marker-' || gen_random_uuid()::text, "
               "'failed', 'detach', 'reconcile', 60, 'video', 0, 'render.deadline', now() - interval '3 days' "
               "FROM generate_series(1, %s)", PROVIDER, key_id, jobs)


def live_like_ready(page, live, data):
    _ready(page)
    page.mouse.move(0, 0)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
def test_keys_with_live_like_data_fits(app, db, live, run_dir, width, theme):
    state = _keys("B-Keys", live_like_ready, live_like_keys_seed)
    with opened(app, db, live, state, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("live-like-%d-%s.png" % (width, theme))).write_bytes(
            page.screenshot(full_page=True, animations="disabled", caret="hide"))
        sideways = page.evaluate("document.documentElement.scrollWidth - innerWidth", isolated_context=False)
        assert sideways <= 0, "the page scrolls sideways by %d px" % sideways
        items = extract(page, ".keys-view")
        clipped = [(t["text"], t["cls"], t["rect"], t["full"]) for t in items["texts"] if t["clipped"]]
        assert not clipped, "texts cut off (the canvas draws every keys text whole): %r" % clipped
        shown = {t["text"] for t in items["texts"]}
        missing = [v for v in LIVE_VALUES if v not in shown]
        assert not missing, "live-shaped values not drawn as one text: %r" % missing

