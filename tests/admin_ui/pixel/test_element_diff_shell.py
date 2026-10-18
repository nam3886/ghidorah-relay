"""The app shell against the canvas, element by element (element_diff.py), the canvas's global rules against the app's,
and the scrollbars, photographed.

`test_the_shell_matches_the_canvas_element_by_element`: the header (logo, tabs and their counts, Provider, theme toggle,
account button) of every B, T and P app board, in light and dark, and the open states B-States draws at 1440, 768 and
390: the account menu and the Provider listbox, each with its open trigger. The canvas cannot scroll the tab row, so the
app's row is put back at its start before it is compared (the app scrolls the current tab into view: DESIGN 10, shown
in `test_the_shell_undrawn_states_are_photographed`). Each comparison writes `<run>/element-diff/shell-*.md` and
`.side.png` and fails while the table holds a difference.

`test_the_global_rules_match_the_canvas`: the same probe markup (a link, a button, the form controls, a summary, a
number, a paragraph, a scrolling box) put into the canvas board and into the app; every computed value that a global
rule can set is compared (fonts, colours, the focus ring, the selection, the hover colour of a link, color-scheme,
scrollbars). The canvas side is B-Test (its `.gd` lists name every control) and, for the scrollbar, Main (the only
board with the `.gd *` scrollbar rule). Writes `<run>/element-diff/global-rules-<theme>.md`.

`test_the_scrollbars_are_photographed`: the custom scrollbar on the page, a table card that scrolls sideways, the job
drawer, an open list and the tab rows, in a Chrome that draws its scrollbars (the harness's own hides them), light and
dark; once more with scrollbar-color unset, which makes Chrome draw the ::-webkit-scrollbar fallback Safari uses."""

import json
from dataclasses import dataclass, field
from itertools import count

import pytest
from element_diff import EDGE_PX as EDGE
from element_diff import diff, extract, markdown, popup_place, side_by_side
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_accounts import accounts_seed, phone_ready
from pixel_capture import opened, shoot
from pixel_diff import compare
from pixel_drawer import IMAGE_DONE, open_drawer, seed_of
from pixel_jobs import canvas_band, jobs_seed
from pixel_manifest import ENTRIES, Entry, shell_counts
from pixel_render import boards
from pixel_seed import BOARD_USER, NOW, WALLETS, shell_seed
from test_element_diff import _png

pytestmark = pytest.mark.browser

BOARDS = {name: (w, h) for name, w, h in boards()}
THEMES = ("light", "dark")
TIMEOUT_MS = 10_000
STATES = "B-States"
SCREENS = (("Jobs", "#/jobs"), ("Accounts", "#/accounts"), ("Keys", "#/keys"), ("Logs", "#/logs"),
           ("Alerts", "#/alerts"), ("Frames", "#/frames"), ("Settings", "#/settings"), ("Test", "#/test"))
APP_HEADER = "header.topbar"
REST_JS = """() => {
  const nav = document.querySelector('header nav');
  if (nav) nav.scrollLeft = 0;
}"""


@dataclass(frozen=True)
class Check:
    name: str
    state: Entry
    canvas_root: str
    app_root: str
    viewport: tuple | None = None
    data: tuple = ()             # canvas texts that are live data
    loose: dict = field(default_factory=dict)   # role -> box axes that are data there (a list of fewer options)
    popup: tuple = ()            # (open popup, its trigger) in the app: the popup must sit 6 px clear of it
    place: tuple = ()            # (canvas popup, canvas trigger, app popup, app trigger): placed by the rule
    align: str = "start"         # the trigger edge the popup is drawn on: start (left) or end (right)


def shell_at_rest(page, live, data):
    """The counts arrived, the tab row at its start (as the canvas draws it), the pointer off the bar."""
    shell_counts(page, live, data)
    page.evaluate(REST_JS)
    page.mouse.move(0, 400)


def _opener(name: str):
    def drive(page, live, data):
        shell_counts(page, live, data)
        page.get_by_test_id(name).click()
        expect(page.locator('[role="menu"], header [role="listbox"]').first).to_be_visible(timeout=TIMEOUT_MS)
        page.mouse.move(0, 400)
    return drive


BOARD_CHECKS = [
    Check("%s-%s" % (size, screen), Entry("%s-%s" % (size, screen), route, seed=shell_seed, now=NOW,
                                          drive=shell_at_rest, user=BOARD_USER), "header", APP_HEADER)
    for size in ("B", "T", "P") for screen, route in SCREENS
]

DESKTOP, TABLET, PHONE = (1440, 1000), (768, 1000), (390, 900)
MENU = '[role="menu"][aria-label="Account"]'
LIST = '[role="listbox"][aria-label="Provider"]'
# B-States draws other open menus and lists (row actions, the Jobs filters): only those of a header are the top bar's
MENU_TRIGGER = 'header button[aria-haspopup="menu"][aria-expanded="true"]'
LIST_TRIGGER = 'header button[aria-haspopup="listbox"][aria-expanded="true"]'
# the drawn list names four providers; the test server has one (dola): the others are the catalog's data
PROVIDERS_DATA = ("vibes", "meta")


def _open_state(which: str):
    return Entry(STATES, "#/jobs", seed=shell_seed, now=NOW, drive=_opener(which), user=BOARD_USER)


OPEN_CHECKS = []
for i, (width, view) in enumerate((("1440", DESKTOP), ("768", TABLET), ("390", PHONE))):
    account, provider = _open_state("account-menu"), _open_state("provider-filter")
    OPEN_CHECKS += [
        Check("menu-" + width, account, "%s@%d" % (MENU, i), MENU, view, popup=(MENU, MENU_TRIGGER),
              place=("%s@%d" % (MENU, i), "%s@%d" % (MENU_TRIGGER, i), MENU, MENU_TRIGGER)),
        Check("menu-trigger-" + width, account, "%s@%d" % (MENU_TRIGGER, i), MENU_TRIGGER, view),
        Check("provider-list-" + width, provider, "%s@%d" % (LIST, i), LIST, view, data=PROVIDERS_DATA,
              loose={"listbox": "h"}, popup=(LIST, LIST_TRIGGER), align="end" if width == "390" else "start",
              place=("%s@%d" % (LIST, i), "%s@%d" % (LIST_TRIGGER, i), LIST, LIST_TRIGGER)),
        Check("provider-trigger-" + width, provider, "%s@%d" % (LIST_TRIGGER, i), LIST_TRIGGER, view),
    ]
CHECKS = BOARD_CHECKS + OPEN_CHECKS

# the N-th element a selector matches (`sel@N`), marked with a value of its own: one page can hold several picks
PICK_JS = """([sel, n, mark]) => {
  const el = document.querySelectorAll(sel)[n];
  if (!el) return null;
  el.setAttribute('data-shell-pick', mark);
  return '[data-shell-pick="' + mark + '"]';
}"""


_PICKS = count()


def _root(page, selector: str) -> str:
    if "@" not in selector:
        return selector
    sel, n = selector.rsplit("@", 1)
    got = page.evaluate(PICK_JS, [sel, int(n), str(next(_PICKS))], isolated_context=False)
    assert got, "nothing matches %s" % selector
    return got


# the left and right edges of a popup against its trigger's (px, unclipped boxes)
SIDES_JS = """([popup, trigger]) => {
  const p = document.querySelector(popup).getBoundingClientRect();
  const t = document.querySelector(trigger).getBoundingClientRect();
  return [Math.round(p.left - t.left), Math.round(p.right - t.right)];
}"""


CARD_JS = """(sel) => {
  const card = document.querySelector(sel).closest('div[style*="border-radius: 16px"]');
  card.setAttribute('data-shell-card', '1');
  return '[data-shell-card="1"]';
}"""


UNCLIP_JS = """(sel) => {
  document.querySelector(sel).closest('div[style*="border-radius: 16px"]').style.overflow = 'visible';
}"""


def _card(page, popup: str) -> bytes:
    """The B-States card the popup is drawn in, whole: where it sits against its trigger, as drawn."""
    mark = page.evaluate(CARD_JS, _root(page, popup), isolated_context=False)
    png = page.locator(mark).screenshot(caret="hide", animations="disabled")
    page.evaluate("document.querySelector('[data-shell-card]').removeAttribute('data-shell-card')",
                  isolated_context=False)
    return png


# The place rule of every popup, as FilterSelect's: on the trigger edge it is drawn on (start: its left edge on the
# trigger's; end: its right edge on the trigger's, the Provider list on a phone), on the other one when that would pass
# the window; never off the window. The B-States cards draw the popover
# in a card of their own width, so where the card puts it is not compared.
RULE_JS = """([popup, trigger]) => {
  const p = document.querySelector(popup).getBoundingClientRect();
  const t = document.querySelector(trigger).getBoundingClientRect();
  return {popup: [p.left, p.right], trigger: [t.left, t.right], window: document.documentElement.clientWidth};
}"""


def _by_the_rule(page, popup: str, trigger: str, align: str = "start") -> str | None:
    got = page.evaluate(RULE_JS, [popup, trigger], isolated_context=False)
    (left, right), (t_left, t_right), window = got["popup"], got["trigger"], got["window"]
    if left < -EDGE or right > window + EDGE:
        return "the popup runs off the window: %r" % got
    width = right - left
    if align == "start":
        end = t_left + width > window
    else:
        end = t_right - width >= 0
    if end:
        return None if abs(right - t_right) <= EDGE else "the popup is not right-aligned to its trigger: %r" % got
    return None if abs(left - t_left) <= EDGE else "the popup does not start at its trigger's left edge: %r" % got


def _sides(page, popup: str, trigger: str) -> list:
    return page.evaluate(SIDES_JS, [_root(page, popup), _root(page, trigger)], isolated_context=False)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_the_shell_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, min(board_h, 1000))
    dark = theme == "dark"
    with opened(app, db, live, check.state, dark, width, height) as page:
        app_root = _root(page, check.app_root)
        mine = extract(page, app_root)
        app_png = _png(page, app_root)
        misplaced = popup_place(page, *check.popup) if check.popup else None
        app_sides = _sides(page, *check.place[2:]) if check.place else None
        misruled = _by_the_rule(page, *check.place[2:], check.align) if check.place else None
        app_view = shoot(page, (0, 0, width, 320)) if check.place else None
    renderer.open(check.state.board, dark, board_w, board_h)
    canvas_sides = _sides(renderer.page, *check.place[:2]) if check.place else None
    canvas_card = _card(renderer.page, check.place[0]) if check.place else None
    if check.place:
        # the drawn card hides what runs past its edge (the account menu at 1440 and 768): the popup is compared
        # whole, as its own markup draws it; where it sits against the trigger is the `place` check
        renderer.page.evaluate(UNCLIP_JS, _root(renderer.page, check.place[0]), isolated_context=False)
    canvas_root = _root(renderer.page, check.canvas_root)
    theirs = extract(renderer.page, canvas_root)
    ref_png = _png(renderer.page, canvas_root)
    pixels = compare(diff_page, ref_png, app_png)

    res = diff(check.name, theirs, mine, data=check.data, loose=check.loose)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    stem = "shell-%s-%s" % (check.name, theme)
    notes = ("Canvas %s %s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% anti-aliased."
             % (check.state.board, check.canvas_root, check.app_root, width, height, pixels.percent,
                pixels.aa_percent))
    if check.popup:
        notes += " Open popup against its trigger: %s." % (misplaced or "6 px clear")
    if check.place:
        notes += (" Popup edges against the trigger's (left, right, px): canvas %s (the card's own geometry), app %s;"
                  " placed by the rule: %s." % (canvas_sides, app_sides, misruled or "yes"))
    (out / (stem + ".md")).write_text(markdown(res, "shell %s, %s" % (check.name, theme), notes), encoding="utf-8")
    (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
    if check.place:
        (out / (stem + ".in-place.png")).write_bytes(side_by_side(diff_page, canvas_card, app_view))
    print("ELEMENTS shell-%s %s: %d differences, %d canvas only, %d app only; pixels %.3f %% + %.3f %%%s" % (
        check.name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), pixels.percent,
        pixels.aa_percent, (" sides canvas %s app %s" % (canvas_sides, app_sides)) if check.place else ""))
    assert res.failures == 0, "%s %s: %d element differences, see %s.md" % (check.name, theme, res.failures, stem)
    assert misplaced is None, misplaced
    assert misruled is None, misruled


# ---- the canvas's global rules ------------------------------------------------------------------------------------
# One probe of each kind of element a global rule can reach, built through the DOM (no markup string: the app's CSP
# refuses inline style attributes, so the scrolling box is sized through the CSSOM).
PROBE_JS = r"""(host) => {
  const parent = host ? document.querySelector(host) : document.body;
  const box = document.createElement('div');
  box.id = 'gprobe';
  const add = (tag, id, props = {}) => {
    const el = document.createElement(tag);
    el.id = id;
    Object.assign(el, props);
    box.appendChild(el);
    return el;
  };
  add('p', 'p-text', {textContent: 'A sentence to select'});
  add('a', 'p-link', {href: '#probe', textContent: 'A link'});
  add('button', 'p-button', {type: 'button', textContent: 'A button'});
  add('input', 'p-input', {type: 'text', value: 'typed'});
  add('input', 'p-check', {type: 'checkbox'});
  const select = add('select', 'p-select');
  select.appendChild(new Option('An option'));
  add('textarea', 'p-textarea', {value: 'text'});
  const details = add('details', 'p-details');
  const summary = document.createElement('summary');
  summary.id = 'p-summary';
  summary.textContent = 'A summary';
  details.appendChild(summary);
  add('span', 'p-num', {className: 'num', textContent: '1,234'});
  add('h1', 'p-h1', {textContent: 'Title'});
  add('code', 'p-code', {textContent: 'code'});
  add('label', 'p-label', {textContent: 'Label'});
  const scroll = add('div', 'p-scroll');
  scroll.style.width = '120px';
  scroll.style.height = '40px';
  scroll.style.overflow = 'auto';
  const inner = document.createElement('div');
  inner.style.width = '400px';
  inner.style.height = '200px';
  scroll.appendChild(inner);
  parent.appendChild(box);
  return true;
}"""
PROBES = ("p-text", "p-link", "p-button", "p-input", "p-check", "p-select", "p-textarea", "p-summary", "p-num",
          "p-h1", "p-code", "p-label", "p-scroll")
FOCUSABLE = ("p-link", "p-button", "p-input", "p-check", "p-select", "p-textarea", "p-summary")
STYLE_JS = r"""(ids) => {
  const props = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight', 'letterSpacing', 'color',
    'backgroundColor', 'textAlign', 'textDecorationLine', 'fontVariantNumeric', 'fontFeatureSettings', 'boxSizing',
    'marginTop', 'marginRight', 'marginBottom', 'marginLeft', 'paddingTop', 'paddingRight', 'paddingBottom',
    'paddingLeft', 'borderTopWidth', 'borderTopStyle', 'borderTopColor', 'borderTopLeftRadius', 'outlineStyle',
    'outlineWidth', 'outlineColor', 'outlineOffset', 'scrollbarColor', 'scrollbarWidth', 'colorScheme',
    'accentColor', 'caretColor', 'cursor', 'webkitFontSmoothing', 'textRendering', 'fontKerning', 'fontSynthesis',
    'webkitTapHighlightColor', 'textSizeAdjust'];
  const out = {};
  for (const id of ids) {
    const s = getComputedStyle(document.getElementById(id));
    for (const p of props) {
      let v = s[p];
      if (p === 'fontFamily') v = v.split(',')[0].replace(/["']/g, '').trim();
      out[id + ' ' + p] = v;
    }
  }
  const sel = getComputedStyle(document.getElementById('p-text'), '::selection');
  out['p-text ::selection backgroundColor'] = sel.backgroundColor;
  out['p-text ::selection color'] = sel.color;
  return out;
}"""
OUTLINE_JS = r"""(id) => {
  const s = getComputedStyle(document.getElementById(id));
  return [document.activeElement && document.activeElement.id, s.outlineStyle, s.outlineWidth, s.outlineColor,
          s.outlineOffset].join(' ');
}"""
FOCUS_FROM_JS = r"""(id) => {
  const el = document.getElementById(id);
  const before = document.createElement('span');
  before.tabIndex = 0;
  before.id = 'p-before';
  el.parentNode.insertBefore(before, el);
  before.focus();
}"""


def _probe(page, host: str | None) -> dict:
    page.evaluate(PROBE_JS, host, isolated_context=False)
    got = page.evaluate(STYLE_JS, list(PROBES), isolated_context=False)
    for target in FOCUSABLE:
        page.evaluate(FOCUS_FROM_JS, target, isolated_context=False)
        page.keyboard.press("Tab")
        got[target + " :focus-visible outline"] = page.evaluate(OUTLINE_JS, target, isolated_context=False)
        page.evaluate("document.getElementById('p-before').remove()", isolated_context=False)
    page.evaluate("document.activeElement && document.activeElement.blur()", isolated_context=False)
    page.locator("#p-link").hover()
    got["p-link :hover color"] = page.evaluate("getComputedStyle(document.getElementById('p-link')).color",
                                               isolated_context=False)
    page.mouse.move(0, 0)
    return got


# An accepted difference, as (canvas value, app value) by probe and property. None so far.
ACCEPTED: dict[str, tuple[str, str]] = {}
# The scrollbar rule is on Main only: these are compared with Main's values.
FROM_MAIN = ("scrollbarColor", "scrollbarWidth")
MAIN_SIZE = (1440, 1180)        # canvas.json; Main is not one of the compared boards


@pytest.mark.parametrize("theme", THEMES)
def test_the_global_rules_match_the_canvas(app, db, live, renderer, run_dir, theme):
    dark = theme == "dark"
    entry = Entry("B-Test", "#/test", seed=shell_seed, now=NOW, drive=shell_counts, user=BOARD_USER)
    with opened(app, db, live, entry, dark, 1440, 900) as page:
        mine = _probe(page, None)
    renderer.open("B-Test", dark, *BOARDS["B-Test"])
    theirs = _probe(renderer.page, ".gd")
    renderer.open("Main", dark, *MAIN_SIZE)
    main = _probe(renderer.page, ".gd")

    rows, wrong = [], []
    for key, app_value in mine.items():
        canvas_value = main[key] if key.split(" ")[-1] in FROM_MAIN else theirs[key]
        same = canvas_value == app_value
        accepted = ACCEPTED.get(key) == (canvas_value, app_value)
        if not same:
            rows.append((key, canvas_value, app_value, "accepted" if accepted else "DIFFERENT"))
            if not accepted:
                wrong.append(key)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# Global rules, %s: the canvas (B-Test; Main for the scrollbar) against the app" % theme, "",
             "%d values compared, %d differ (%d accepted)." % (len(mine), len(rows), len(rows) - len(wrong)), "",
             "| probe and property | canvas | app | |", "|---|---|---|---|"]
    lines += ["| %s | %s | %s | %s |" % row for row in rows] or ["| (none) | | | |"]
    (out / ("global-rules-%s.md" % theme)).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("GLOBAL %s: %d values, %d different, %d accepted" % (theme, len(mine), len(wrong), len(rows) - len(wrong)))
    assert not wrong, "global rules differ from the canvas: %r" % [r for r in rows if r[0] in wrong]


# ---- states the canvas does not draw: photographed with the nearest drawn pattern ---------------------------------

def _hover(selector: str):
    def act(page):
        page.locator(selector).first.hover()
    return act


def _key_focus(test_id: str):
    def act(page):
        page.get_by_test_id(test_id).focus()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        expect(page.get_by_test_id(test_id)).to_be_focused()
    return act


def _menu_hover(page):
    page.get_by_test_id("account-menu").click()
    page.get_by_role("menuitem", name="Sign out").hover()


def _list_keyboard(page):
    page.get_by_test_id("provider-filter").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")


UNDRAWN = [
    ("hover-tab", _hover('header a.tab:not(.tab--current)'), DESKTOP, "#/jobs"),
    ("hover-theme-toggle", _hover('[data-testid="theme-toggle"]'), DESKTOP, "#/jobs"),
    ("hover-menu-item", _menu_hover, DESKTOP, "#/jobs"),
    ("focus-provider", _key_focus("provider-filter"), DESKTOP, "#/jobs"),
    ("keyboard-in-provider-list", _list_keyboard, DESKTOP, "#/jobs"),
    ("phone-current-tab-revealed", lambda page: None, PHONE, "#/test"),
    ("tablet-current-tab-revealed", lambda page: None, TABLET, "#/test"),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("state", "act", "view", "route"), UNDRAWN, ids=[u[0] for u in UNDRAWN])
def test_the_shell_undrawn_states_are_photographed(app, db, live, run_dir, state, act, view, route, theme):
    def drive(page, live, data):
        shell_counts(page, live, data)
        act(page)

    entry = Entry("B-Jobs", route, seed=shell_seed, now=NOW, drive=drive, user=BOARD_USER)
    with opened(app, db, live, entry, theme == "dark", *view) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("shell-undrawn-%s-%s.png" % (state, theme))).write_bytes(shoot(page, (0, 0, view[0], 320)))
        if state.endswith("revealed"):
            box = page.locator('header [aria-current="page"]').bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= view[0], "current tab out of view: %r" % box


# ---- the scrollbar ------------------------------------------------------------------------------------------------
# The harness's Chrome hides every scrollbar (Playwright's default --hide-scrollbars), so these photos come from a
# second Chrome of the same driver that draws them.

@pytest.fixture(scope="module")
def chrome_with_bars(chrome):
    browser = chrome.browser_type.launch(channel="chrome", headless=True, ignore_default_args=["--hide-scrollbars"])
    try:
        yield browser
    finally:
        browser.close()


KEY_LABELS = tuple("Partner %02d" % n for n in range(1, 19))


def many_keys_seed(db, live):
    """The shell's data and eighteen more keys, so the Key list is longer than it may grow and scrolls."""
    data = shell_seed(db, live)
    for label in KEY_LABELS:
        k = new_key(db, wallets=WALLETS)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, k)
    return data


def _ready(page, live, data):
    expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)


def _key_list_open(page, live, data):
    _ready(page, live, data)
    page.get_by_role("combobox", name="Key", exact=True).click()
    expect(page.get_by_role("listbox")).to_be_visible()


def _accounts_card_end(page, live, data):
    """The bottom of the Accounts table card in view: its sideways scrollbar is its last 15 px."""
    phone_ready(page, live, data)
    page.evaluate("document.querySelector('.acct-scroll').scrollIntoView({block: 'end'})")


def _tabs(page, live, data):
    shell_counts(page, live, data)


# (name, entry, viewport, the scrolling element, the part photographed: a selector or None for the viewport)
SCENES = [
    ("page", Entry("B-Jobs", "#/jobs", seed=jobs_seed, now=NOW, drive=_ready, prepare=canvas_band, user=BOARD_USER),
     (1440, 900), "html", None),
    ("table-card", Entry("B-Accounts", "#/accounts", seed=accounts_seed, now=NOW, drive=_accounts_card_end),
     (1440, 900), ".acct-scroll", ".list-card"),
    ("drawer", Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW, drive=open_drawer(IMAGE_DONE)),
     (1440, 700), ".detail-drawer__body", None),
    ("list", Entry("B-Jobs", "#/jobs", seed=many_keys_seed, now=NOW, drive=_key_list_open, user=BOARD_USER),
     (1440, 700), '[role="listbox"]', None),
    ("tabs-768", Entry("T-Jobs", "#/test", seed=shell_seed, now=NOW, drive=_tabs, user=BOARD_USER),
     (768, 400), "header nav", "header"),
    ("tabs-390", Entry("P-Jobs", "#/test", seed=shell_seed, now=NOW, drive=_tabs, user=BOARD_USER),
     (390, 400), "header nav", "header"),
]
# Chrome reads ::-webkit-scrollbar only where scrollbar-color is auto: unset it to see what Safari draws.
WEBKIT_JS = """() => {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync('* { scrollbar-color: auto !important; }');
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}"""
# The room the scrollbars take, [the vertical bar's width, the horizontal bar's height]: for the page, what the window
# loses; for an element, what its box loses past its borders.
BARS_JS = """(sel) => {
  const el = document.querySelector(sel);
  const s = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const page = el === document.documentElement;
  const gutter = page ? [innerWidth - el.clientWidth, innerHeight - el.clientHeight]
    : [el.offsetWidth - el.clientWidth - parseFloat(s.borderLeftWidth) - parseFloat(s.borderRightWidth),
       el.offsetHeight - el.clientHeight - parseFloat(s.borderTopWidth) - parseFloat(s.borderBottomWidth)];
  return {color: s.scrollbarColor, sideways: el.scrollWidth - el.clientWidth, down: el.scrollHeight - el.clientHeight,
          gutter: gutter.map(Math.round), box: [r.left, r.top, r.width, r.height].map(Math.round),
          lineStrong: getComputedStyle(document.documentElement).getPropertyValue('--rl-line-strong').trim()};
}"""
# Chrome's bar for scrollbar-color and the Safari fallback both take 15 px, so turning one into the other moves nothing;
# the header's tab row takes none in either
GUTTERS = {"page": [15, 0], "table-card": [0, 15], "drawer": [15, 0], "list": [15, 0], "tabs-768": [0, 0],
           "tabs-390": [0, 0]}
PART_JS = """(sel) => {
  const r = document.querySelector(sel).getBoundingClientRect();
  const x = Math.max(0, Math.floor(r.left)), y = Math.max(0, Math.floor(r.top));
  return [x, y, Math.min(innerWidth, Math.ceil(r.right)) - x, Math.min(innerHeight, Math.ceil(r.bottom)) - y];
}"""


def _hex_rgb(value: str) -> str:
    h = value.lstrip("#")
    return "rgb(%d, %d, %d)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


@pytest.mark.parametrize("fallback", (False, True), ids=("scrollbar-color", "webkit"))
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("scene", "entry", "view", "scroller", "part"), SCENES, ids=[s[0] for s in SCENES])
def test_the_scrollbars_are_photographed(app, chrome_with_bars, db, live, run_dir, scene, entry, view, scroller, part,
                                         theme, fallback):
    with opened(type(app)(chrome_with_bars, live), db, live, entry, theme == "dark", *view) as page:
        bars = page.evaluate(BARS_JS, scroller, isolated_context=False)
        if not scene.startswith("tabs"):
            assert bars["sideways"] > 0 or bars["down"] > 0, "%s does not scroll: %r" % (scroller, bars)
        assert bars["color"] == _hex_rgb(bars["lineStrong"]) + " rgba(0, 0, 0, 0)", bars
        assert bars["gutter"] == GUTTERS[scene], "scrollbar-color: %r" % bars
        if fallback:
            page.evaluate(WEBKIT_JS, isolated_context=False)
            page.wait_for_timeout(100)
            bars = page.evaluate(BARS_JS, scroller, isolated_context=False)
            assert bars["gutter"] == GUTTERS[scene], "the Safari fallback: %r" % bars
        out = run_dir / "scrollbars"
        out.mkdir(parents=True, exist_ok=True)
        # a viewport shot clipped to the part: an element shot leaves the scrollbars out
        png = shoot(page, page.evaluate(PART_JS, part, isolated_context=False) if part else None)
        stem = "%s-%s%s" % (scene, theme, "-webkit" if fallback else "")
        (out / (stem + ".png")).write_bytes(png)
        (out / (stem + ".json")).write_text(json.dumps(bars), encoding="utf-8")


# Element Plus draws its own bars (a grey div, not a scrollbar) inside a scroll area of its own, such as a table body.
# The app's lists are its own; this proves no Element Plus scroll area overflows on the boards that hold a table, so no
# Element Plus bar is shown where the custom scrollbar should be.
EP_SCROLL_JS = """() => [...document.querySelectorAll(".el-scrollbar__wrap")].filter(
  (w) => w.scrollWidth > w.clientWidth + 1 || w.scrollHeight > w.clientHeight + 1).map((w) => w.className)"""
TABLE_BOARDS = [e for e in ENTRIES if not e.label and e.board.split("-", 1)[1] in ("Jobs", "Logs", "Keys", "Alerts")]


@pytest.mark.parametrize("entry", TABLE_BOARDS, ids=lambda e: e.name)
def test_no_element_plus_scroll_area_overflows(app, db, live, entry):
    width, height = BOARDS[entry.board]
    with opened(app, db, live, entry, False, width, min(height, 1000)) as page:
        overflowing = page.evaluate(EP_SCROLL_JS, isolated_context=False)
    assert not overflowing, "Element Plus scroll areas that overflow on %s: %r" % (entry.name, overflowing)


# the row's own top border is not a bar: what offsetHeight adds to clientHeight past the borders is the scrollbar
TAB_ROW_JS = """(n) => {
  const s = getComputedStyle(n);
  return {bar: n.offsetHeight - n.clientHeight - parseFloat(s.borderTopWidth) - parseFloat(s.borderBottomWidth),
          sideways: n.scrollWidth - n.clientWidth, header: n.closest('header').getBoundingClientRect().height,
          page: document.documentElement.clientWidth};
}"""


@pytest.mark.parametrize(("width", "header"), ((768, 110), (390, 102)), ids=("768", "390"))
def test_the_tab_row_scrolls_without_a_bar(app, chrome_with_bars, db, live, width, header):
    """In a Chrome that draws its scrollbars, as a desktop browser does: the header's tab row shows none, as drawn, so
    the header keeps the drawn height; it still scrolls by the wheel and the shift-wheel, and the current tab (Test, the
    last) is in view once the counts have widened the tabs before it."""
    entry = Entry("T-Jobs", "#/test", seed=shell_seed, now=NOW, drive=shell_counts, user=BOARD_USER)
    with opened(type(app)(chrome_with_bars, live), db, live, entry, False, width, 600) as page:
        nav = page.locator("header nav")
        got = nav.evaluate(TAB_ROW_JS)
        assert got["sideways"] > 0 and got["bar"] == 0 and got["header"] == header, got
        tab = page.locator('header [aria-current="page"]').bounding_box()
        assert tab and tab["x"] >= 0 and tab["x"] + tab["width"] <= got["page"] + EDGE, (tab, got)
        for shift in (False, True):
            nav.evaluate("n => { n.scrollLeft = 0; }")
            nav.hover()
            if shift:
                page.keyboard.down("Shift")
            page.mouse.wheel(0 if shift else 300, 300 if shift else 0)
            if shift:
                page.keyboard.up("Shift")
            page.wait_for_function("() => document.querySelector('header nav').scrollLeft > 0", timeout=TIMEOUT_MS)
