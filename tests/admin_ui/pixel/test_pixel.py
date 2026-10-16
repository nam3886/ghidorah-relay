"""The pixel-perfect harness (browser tier): reference renders of the canvas by our own renderer, the app photographed
in the state of each artboard, and a pixel diff between them.

- `test_every_board_renders_in_both_themes`: every B-, T- and P- board of the canvas draws fully in light and dark (no
  uninterpolated `{{`, no leftover sc-element, every image and font loaded, nothing fetched from outside).
- `test_the_renderer_*`: the dialect of dc-mini.js (sc-for, sc-if, attributes, events, setState, tables).
- `test_the_diff_*`: the diff counts what differs and nothing else.
- `test_the_app_matches_the_canvas`: one comparison per manifest entry and theme. It writes
  `<run>/<board>-<theme>.{ref,app,diff}.png` and the report. An entry with a `gate` fails when mismatch + anti-aliasing
  is above it (every entry has one, 35 % by default until its lane merges).
- `test_the_manifest_*`, `test_the_gate_*`: the manifest is sound, and the gate itself always bites (a wrong page
  fails it), whatever the entries' own gates are.

A run narrowed to one comparison:
`scripts/check-in-container.sh "tests/admin_ui/pixel/test_pixel.py::test_the_app_matches_the_canvas[B-Login-light]"`."""

import pytest
from pixel_capture import capture
from pixel_diff import compare
from pixel_manifest import CEILING_PCT, ENTRIES
from pixel_render import boards, font_css
from pixel_report import status, write_result

pytestmark = pytest.mark.browser

BOARDS = {name: (w, h) for name, w, h in boards()}
THEMES = ("light", "dark")
WRONG_PAGE_FLOOR_PCT = 0.3            # a page shifted by 16 px must score above this (mismatch + anti-aliasing)


# ---- the reference renders ---------------------------------------------------------------------------------------

CHECK_DRAWN_JS = """() => ({
  undefinedPaths: window.__dcUndefined,
  templates: document.getElementById('dc-root').querySelectorAll('template').length,
  empty: document.getElementById('dc-root').children.length === 0,
  badImages: [...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.src),
  sheets: [...document.fonts].filter(f => f.status === 'error').map(f => f.family),
  scrollHeight: document.documentElement.scrollHeight,
  scrollWidth: document.documentElement.scrollWidth,
  collapsed: [...document.getElementById('dc-root').querySelectorAll(
      'main, section, table, tbody, ul, ol, dl, header, nav, form, aside')]
    .filter(e => e.childNodes.length && e.getBoundingClientRect().height === 0 &&
                 getComputedStyle(e).display !== 'none')
    .map(e => e.tagName.toLowerCase() + (e.getAttribute('aria-label') ? '[' + e.getAttribute('aria-label') + ']' : ''))
})"""



@pytest.mark.parametrize("board", sorted(BOARDS))
def test_every_board_renders_in_both_themes(renderer, run_dir, board):
    width, height = BOARDS[board]
    misfits = []
    for theme in THEMES:
        png = renderer.png(board, theme == "dark", width, height)
        (run_dir / "reference").mkdir(parents=True, exist_ok=True)
        (run_dir / "reference" / ("%s-%s.png" % (board, theme))).write_bytes(png)
        got = renderer.page.evaluate(CHECK_DRAWN_JS, isolated_context=False)
        assert not got["empty"], "%s %s drew nothing" % (board, theme)
        assert not got["undefinedPaths"], "%s %s has text paths with no value: %r" % (
            board, theme, got["undefinedPaths"])
        assert not got["templates"], "%s %s left an sc-element undrawn" % (board, theme)
        assert not got["badImages"], "%s %s has images that did not load: %r" % (board, theme, got["badImages"])
        assert not got["sheets"], "%s %s has fonts that failed to load: %r" % (board, theme, got["sheets"])
        assert not got["collapsed"], "%s %s has collapsed regions: %r" % (board, theme, got["collapsed"])
        if got["scrollHeight"] > height or got["scrollWidth"] > width:       # judged after both themes are drawn
            (run_dir / "overflow").mkdir(parents=True, exist_ok=True)      # the whole page, to see what spills
            (run_dir / "overflow" / ("%s-%s.png" % (board, theme))).write_bytes(
                renderer.page.screenshot(full_page=True, animations="disabled"))
            misfits.append("%s %s does not fit its %dx%d board: the page is %dx%d" % (
                board, theme, width, height, got["scrollWidth"], got["scrollHeight"]))
    assert not misfits, misfits
    assert not renderer.refused, "requests that were not answered from a local file: %r" % renderer.refused
    assert not renderer.problems, renderer.problems


def test_the_fonts_of_the_app_are_what_the_reference_renders_with(renderer):
    css = font_css()
    assert css, "the built app has no @font-face rules to render the canvas with"
    renderer.open("B-Login", False, 1440, 760)
    for family in ("Be Vietnam Pro", "IBM Plex Mono"):
        assert family in css, family
        # a face loads when something uses it: ask for it, then it must be there and the browser must have its file
        loaded = renderer.page.evaluate("f => document.fonts.load('14px \"' + f + '\"').then(l => l.length)", family,
                                        isolated_context=False)
        assert loaded, "%s has no face to load" % family
        assert renderer.page.evaluate("f => document.fonts.check('14px \"' + f + '\"')", family,
                                      isolated_context=False), "%s is not loaded" % family


# ---- the renderer's dialect --------------------------------------------------------------------------------------

SYNTHETIC = """<x-dc>
<helmet><style>.big{font-size:30px}</style></helmet>
<div id="top" class="{{cls}}" data-gone="{{nothing}}" data-on="{{yes}}">
  <p id="greeting">Hello {{who.name}}!</p>
  <table><tbody>
    <sc-for list="{{rows}}" as="r"><sc-if value="{{r.show}}"><tr><td class="cell">{{r.n}}</td></tr></sc-if></sc-for>
  </tbody></table>
  <svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="{{radius}}"/></svg>
  <button id="go" type="button" onClick="{{bump}}">count {{count}}</button>
</div></x-dc>
<script type="text/x-dc" data-dc-script data-props='{"dark":{"editor":"boolean","default":false}}'>
class Component extends DCLogic {
  constructor(props) { super(props); this.state = { count: 0 }; }
  renderVals() {
    return {
      cls: this.props.dark ? 'dark big' : 'light big', nothing: false, yes: true, who: { name: 'Ada' },
      rows: [{ n: 1, show: true }, { n: 2, show: false }, { n: 3, show: true }], radius: 4,
      count: this.state.count, bump: () => this.setState({ count: this.state.count + 1 })
    };
  }
}
</script>"""


def test_the_renderer_draws_the_dialect(renderer):
    renderer.open("synthetic", True, 300, 200, source=SYNTHETIC)
    page = renderer.page
    root = "#dc-root "
    assert page.get_attribute(root + "#top", "class") == "dark big"
    assert page.get_attribute(root + "#top", "data-gone") is None, "a false attribute is left out"
    assert page.get_attribute(root + "#top", "data-on") == "true"
    assert page.inner_text(root + "#greeting") == "Hello Ada!"
    assert page.locator(root + "td.cell").all_inner_texts() == ["1", "3"], "sc-for repeats, sc-if drops, in a table"
    assert page.get_attribute(root + "circle", "r") == "4"
    assert page.evaluate("document.querySelector('#dc-root circle').namespaceURI") == "http://www.w3.org/2000/svg"
    assert page.evaluate("getComputedStyle(document.querySelector('#top')).fontSize") == "30px", "helmet style applies"


def test_the_renderer_reports_a_text_path_that_has_no_value(renderer):
    source = SYNTHETIC.replace("Hello {{who.name}}!", "Hello {{who.name}} {{who.surname}} {{ghost}}!")
    renderer.open("synthetic-missing", False, 300, 200, source=source)
    assert renderer.page.evaluate("window.__dcUndefined", isolated_context=False) == ["who.surname", "ghost"]


def test_the_renderer_redraws_on_set_state(renderer):
    renderer.open("synthetic", False, 300, 200, source=SYNTHETIC)
    page = renderer.page
    assert page.inner_text("#go") == "count 0"
    page.click("#go")
    page.click("#go")
    assert page.inner_text("#go") == "count 2"
    assert page.get_attribute("#top", "class") == "light big"


# ---- the diff ----------------------------------------------------------------------------------------------------

def test_the_diff_of_identical_images_is_zero_and_a_changed_block_is_counted(renderer, diff_page):
    ref = renderer.png("B-Login", False, 1440, 760)
    same = compare(diff_page, ref, ref)
    assert (same.mismatch, same.aa, same.percent, same.same_size) == (0, 0, 0.0, True)
    renderer.open("B-Login", False, 1440, 760)
    renderer.page.evaluate(
        "() => { const d = document.createElement('div'); d.style.cssText = 'position:fixed;left:100px;top:100px;"
        "width:50px;height:40px;background:#f00'; document.body.appendChild(d); }", isolated_context=False)
    changed = renderer.page.screenshot(animations="disabled")
    got = compare(diff_page, ref, changed)
    assert got.mismatch == 50 * 40, got.summary()
    assert got.top and got.top[0]["x"] <= 100 < got.top[0]["x"] + 64


def test_the_diff_counts_every_pixel_outside_the_smaller_image(renderer, diff_page):
    ref = renderer.png("B-Login", False, 1440, 760)
    taller = renderer.png("B-Login", False, 1440, 800)
    got = compare(diff_page, ref, taller)
    assert not got.same_size
    assert got.mismatch >= 1440 * 40, got.summary()


# ---- the app against the canvas ----------------------------------------------------------------------------------

def test_the_server_clock_of_the_hourly_band_is_the_clock_of_the_boards(frozen_server_clock):
    from relay_core.admin import routes_stats
    assert routes_stats._now().isoformat() == "2026-10-15T13:55:02+00:00"      # 20:55:02 in Asia/Saigon


def test_the_seed_gives_the_boards_pool_alert_under_the_boards_provider(db, live):
    """The top bar's 7/8 and the pool alert sentence the canvas shows, as the server itself words them."""
    from pixel_seed import shell_seed

    from relay_core.admin import routes_stats
    from relay_core.db import tx
    shell_seed(db, live)

    async def alerts():
        async with tx(live.env.pool) as conn:
            rows = await (await conn.execute(routes_stats._USABLE)).fetchall()
            usable = {r[0]: r[1] for r in rows}
            return rows, await routes_stats.live_alerts(conn, live.env.settings, live.env.registry, usable)

    rows, got = live.call(alerts())
    assert [(r[0], r[1], r[2]) for r in rows] == [("dola", 7, 8)]
    assert [a["message"] for a in got if a["kind"] == "accounts_low"] == [
        "dola has 7 usable accounts (alert below 8)"]


def test_the_manifest_is_sound():
    """An empty manifest would make the comparisons below vanish as a skip, so it is checked here, once."""
    assert ENTRIES, "the manifest has no entries"
    names = [e.name for e in ENTRIES]
    assert len(names) == len(set(names)), "duplicate entries: %r" % sorted({n for n in names if names.count(n) > 1})
    for e in ENTRIES:
        assert e.board in BOARDS, "%s names a board that is not on the canvas" % e.name
        width, height = BOARDS[e.board]
        if e.region:
            x, y, w, h = e.region
            assert w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width and y + h <= height, (
                "%s: the region %r is not inside the %dx%d board" % (e.name, e.region, width, height))
        assert e.gate is not None and 0 < e.gate <= CEILING_PCT, (
            "%s: every entry has a gate, a positive percentage no looser than %s" % (e.name, CEILING_PCT))


def test_the_gate_passes_the_canvas_itself_and_fails_a_wrong_page(renderer, diff_page):
    """Always enforced, whatever the entries' gates are: the reference against itself is within a gate of 0, and a
    blank page and a page shifted by 16 px are not within the floor."""
    ref = renderer.png("B-Login", False, 1440, 760)
    assert status(compare(diff_page, ref, ref).total_percent, 0.0) == "pass"
    for style in ("position:fixed;inset:0;background:#000", None):
        renderer.open("B-Login", False, 1440, 760)
        renderer.page.evaluate(
            "s => { if (s) { const d = document.createElement('div'); d.style.cssText = s;"
            " document.body.appendChild(d); }"
            " else document.getElementById('dc-root').style.transform = 'translateX(16px)'; }", style,
            isolated_context=False)
        wrong = compare(diff_page, ref, renderer.page.screenshot(animations="disabled"))
        assert status(wrong.total_percent, WRONG_PAGE_FLOOR_PCT) == "FAIL", wrong.summary()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.name)
def test_the_app_matches_the_canvas(app, db, live, renderer, diff_page, run_dir, entry, theme):
    width, height = BOARDS[entry.board]
    dark = theme == "dark"
    reference = renderer.reference(entry.board, dark, width, height, entry.region)
    shot = capture(app, db, live, entry, dark, width, height)
    diff = compare(diff_page, reference, shot)
    result = write_result(run_dir, entry.name, theme, reference, shot, diff, entry.gate)
    print("PIXEL %s %s [%s]: %.3f %% + %.3f %% anti-aliased (%d px, %d anti-aliased px)" % (
        entry.name, theme, result["status"], diff.percent, diff.aa_percent, diff.mismatch, diff.aa))
    assert diff.same_size, "the app photo is %r, the canvas %r" % (diff.app_size, diff.reference_size)
    assert result["status"] != "FAIL", "%s %s: %.3f %% mismatch + anti-aliasing is over the gate of %s %%" % (
        entry.name, theme, diff.total_percent, entry.gate)
