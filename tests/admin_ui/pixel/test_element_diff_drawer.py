"""The job drawer against the canvas, element by element (element_diff.py), and what the canvas does not draw of it.

`test_the_drawer_matches_the_canvas_element_by_element`: B-JobDetail, B-JobFailed, T-JobDetail and P-JobDetail, each on
Overview, Timeline and Technical (the tab picked on both sides), the Regenerate confirm open over the failed drawer, and
the broken thumbnail, in both themes. The drawer panel is compared (the B/T backdrop is a mock); the panel, the scrims
and the confirm must also sit where the board draws them. A thumbnail's picture is data (pixel_drawer.PICTURE). Each
comparison writes `<run>/element-diff/<name>-<theme>.md` and `.side.png` (canvas | app | pixel diff) and fails while
the table holds a difference.

Then photos and guards of what the canvas does not draw (the in-flight statuses, a done video on desktop, eight
results, hover, focus, the copied id, loading, a failed read, the Revoke popover, the picture viewer), the focus given
back on close, and live-shaped data (`test_drawer_with_live_like_data_fits`)."""

import re
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from element_diff import diff, extract, markdown, place_differences, place_of, popup_place, side_by_side
from patchright.sync_api import expect
from pixel_capture import opened, settle, shoot
from pixel_diff import compare
from pixel_drawer import (
    EIGHT_OUTPUTS,
    IMAGE_DONE,
    IMAGE_FAILED,
    LIVE_DONE,
    LIVE_DRAWER_VALUES,
    LIVE_FAILED,
    VIDEO_DONE,
    in_flight,
    open_drawer,
    seed_of,
)
from pixel_manifest import Entry
from pixel_seed import NOW
from test_element_diff import BOARDS, THEMES, TIMEOUT_MS, Check, _png, _root

pytestmark = pytest.mark.browser


@dataclass(frozen=True)
class DrawerCheck(Check):
    canvas_drive: Callable | None = None    # canvas_drive(page): the clicks that reach the state on the board (a tab)
    opaque: tuple = ("", "")        # (canvas, app) selectors of boxes whose inside is data (a thumbnail's picture)
    skip: tuple = ("", "")          # (canvas, app) selectors left out whole (sections an accepted deviation adds)
    place: tuple = ()               # (canvas, app) selector pairs that must sit at one place in the viewport, with one
                                    # background and shadow (the compared roots, a scrim)


# ---- the job drawer ----------------------------------------------------------------------------------------------
# The drawer panel only: the B/T boards' backdrop is a mock (specs/canvas-normalization.md 1); the panel, and the
# scrim over the page, must sit where the board draws them. Each board draws three tabs: each is compared, the tab
# picked on both sides. A thumbnail's picture is data (pixel_drawer.PICTURE); the broken thumbnail is compared whole.
APP_PANEL = ".detail-drawer.el-drawer"
CANVAS_PANEL = 'aside[role="dialog"]'
SCRIMS = ('div[aria-hidden="true"]:has(+ aside[role="dialog"])', ".el-overlay:has(.detail-drawer)")
TILES = (".tile", ".media__tile")
# Kept features the canvas does not draw (design-deviations 3, 4): Rename, More actions (Recover of a done job, Resend
# webhook, Revoke share links, Fetch title), Debug files, Text sent upstream, Diagnostics; photographed apart (D125).
KEPT = ("[data-section=title], [data-section=more-actions], [data-section=debug], [data-section=sent], .tech__title, "
        "[data-testid=job-diagnostics]")
# The video file is never a playable one: the <video> is the picture, its frame is compared.
VIDEO = ".media__frame video"


# A done job cannot be re-checked (design-deviations 2): the board's Re-check of a done job is taken out of the board
# before it is compared, so the buttons after it sit where they would without it (the app's layout).
DROP_JS = """(words) => { for (const b of [...document.querySelectorAll('button')])
  if (words.includes(b.textContent.trim())) b.remove(); }"""


def _canvas_tab(tab: str, cancel: bool = False, drop: tuple = ()) -> Callable:
    """On the board: close the open confirm (B-JobFailed draws it open), pick the tab, then take out what an accepted
    deviation does not build (`drop`, button words; last, as a click re-renders the board)."""
    def drive(page):
        if cancel:
            page.get_by_role("alertdialog").get_by_role("button", name="Cancel", exact=True).click()
            expect(page.get_by_role("alertdialog")).to_have_count(0)
        if tab != "Overview":
            page.get_by_role("tab", name=tab, exact=True).click()
            expect(page.get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")
        if drop:
            page.evaluate(DROP_JS, list(drop))
    return drive


def _drawer_checks(board: str, spec: dict, phone: bool = False, drop: tuple = ()) -> list[DrawerCheck]:
    canvas_root = '[role="dialog"]' if phone else CANVAS_PANEL
    out = []
    for tab in ("Overview", "Timeline", "Technical"):
        state = Entry(board, "#/jobs", seed=seed_of(spec), now=NOW, drive=open_drawer(spec, tab=tab))
        out.append(DrawerCheck(
            "%s-%s" % (board, tab.lower()), state, canvas_root, APP_PANEL,
            canvas_drive=_canvas_tab(tab, cancel=board == "B-JobFailed", drop=drop), opaque=TILES,
            skip=("", ", ".join(s for s in (KEPT if tab == "Technical" else "", VIDEO) if s)),
            place=((canvas_root, APP_PANEL),) if phone else ((CANVAS_PANEL, APP_PANEL), SCRIMS)))
    return out


DONE_RECHECK = ("Re-check",)
CONFIRM = ('[role="alertdialog"]', ".rl-confirm.el-message-box")
CONFIRM_SCRIM = ('div[style*="z-index: 5"] > div[aria-hidden="true"]', ".el-overlay.is-message-box")
DRAWER_CHECKS = [
    *_drawer_checks("B-JobDetail", IMAGE_DONE, drop=DONE_RECHECK),
    *_drawer_checks("B-JobFailed", IMAGE_FAILED),
    *_drawer_checks("T-JobDetail", IMAGE_DONE, drop=DONE_RECHECK),
    *_drawer_checks("P-JobDetail", VIDEO_DONE, phone=True, drop=DONE_RECHECK),
    # the Regenerate confirm, open over the failed job's drawer where B-JobFailed draws it
    DrawerCheck("B-JobFailed-confirm",
                Entry("B-JobFailed", "#/jobs", seed=seed_of(IMAGE_FAILED), now=NOW,
                      drive=open_drawer(IMAGE_FAILED, confirm=True)),
                *CONFIRM, place=(CONFIRM, CONFIRM_SCRIM)),
    # a thumbnail that does not load draws the canvas's tile: its colour, the image icon and the size
    DrawerCheck("state-broken-thumbnail",
                Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE, picture=False), now=NOW,
                      drive=open_drawer(IMAGE_DONE)),
                'section[aria-label="Results"]', "[data-section=results]"),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", DRAWER_CHECKS, ids=lambda c: c.name)
def test_the_drawer_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, board_h)
    dark = theme == "dark"
    with opened(app, db, live, check.state, dark, width, height) as page:
        app_root = _root(page, check.app_root)
        mine = extract(page, app_root, opaque=check.opaque[1], skip=check.skip[1])
        app_png = _png(page, app_root)
        app_places = [place_of(page, a) for _c, a in check.place]
    renderer.open(check.state.board, dark, board_w, board_h)
    if check.canvas_drive:
        check.canvas_drive(renderer.page)
        renderer.settle()
    canvas_root = _root(renderer.page, check.canvas_root)
    theirs = extract(renderer.page, canvas_root, opaque=check.opaque[0], skip=check.skip[0])
    ref_png = _png(renderer.page, canvas_root)
    pixels = compare(diff_page, ref_png, app_png)
    places = ["%s / %s: %s" % (c, a, "; ".join(d))
              for (c, a), got in zip(check.place, app_places, strict=True)
              if (d := place_differences(place_of(renderer.page, c), got))]
    res = diff(check.name, theirs, mine, data=check.data, data_app=check.data_app, loose=check.loose)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (check.name, theme)
    notes = ("Canvas %s %s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% anti-aliased."
             % (check.state.board, check.canvas_root, check.app_root, width, height, pixels.percent,
                pixels.aa_percent))
    if check.place:
        notes += " Place in the viewport (canvas / app): %s." % ("; ".join(places) or "the same")
    if check.opaque[0]:
        notes += " Inside a thumbnail tile is data (the picture), its box is compared."
    (out / (stem + ".md")).write_text(markdown(res, "%s, %s" % (check.name, theme), notes), encoding="utf-8")
    (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
    print("ELEMENTS %s %s: %d differences, %d canvas only, %d app only, %d data, %d places; pixels %.3f %% + %.3f %%"
          % (check.name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), len(res.data),
             len(places), pixels.percent, pixels.aa_percent))
    assert res.failures == 0, "%s %s: %d element differences, see %s.md" % (check.name, theme, res.failures, stem)
    assert not places, "%s %s: not where the canvas draws it: %s" % (check.name, theme, places)


# ---- the job drawer: what the canvas does not draw, photographed; data shaped like the live box ---------------------
DRAWER_SIZES = ((1440, 1180), (768, 1280), (390, 1060))        # the drawer boards' own sizes
IN_FLIGHT = ("queued", "submitting", "waiting", "fetching", "unknown")
UNDRAWN_DRAWERS = {**{s: in_flight(s) for s in IN_FLIGHT}, "done-video": VIDEO_DONE, "eight-outputs": EIGHT_OUTPUTS}
# What each one offers (JobActions' rules, unchanged): the overview row (a phone's footer) holds Download when there
# are results, Re-check when the server says the job can be (a waiting or unknown job that was sent), Regenerate… once
# the job is done; More actions (Technical) holds Recover once done (design-deviations 4), Revoke share links while a
# result has a link, Fetch title once dola has the job on an account. A job still on its way offers nothing to undo.
ROW_ACTIONS = {"queued": [], "submitting": [], "waiting": ["Re-check"], "fetching": [], "unknown": ["Re-check"],
               "done-video": ["Download", "Regenerate…"], "eight-outputs": ["Download all", "Regenerate…"]}
MORE_ACTIONS = {"queued": [], "submitting": [], "waiting": ["Fetch title"], "fetching": ["Fetch title"],
                "unknown": ["Fetch title"], "done-video": ["Recover", "Revoke share links", "Fetch title"],
                "eight-outputs": ["Recover", "Revoke share links", "Fetch title"]}
ROW_BUTTONS = "[data-section=actions] .job-actions--row button, .detail-drawer__foot button"


def _drawer_out(run_dir):
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resize(page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    expect(page.locator(APP_PANEL)).to_have_class(re.compile(r"\bis-sheet\b") if width < 768
                                                  else re.compile(r"^(?!.*\bis-sheet\b)"))
    settle(page)


def _tab(page, name: str) -> None:
    tab = page.locator(APP_PANEL).get_by_role("tab", name=name, exact=True)
    tab.click()
    expect(tab).to_have_attribute("aria-selected", "true")
    settle(page)


def _texts(locator) -> list[str]:
    return [t.strip() for t in locator.all_inner_texts()]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("state", list(UNDRAWN_DRAWERS))
def test_drawers_the_canvas_does_not_draw_are_photographed(app, db, live, run_dir, state, theme):
    """The drawers specs/jobs.md 13 lists as not drawn, at 1440, 768 and 390: each photographed (Overview, and the
    Technical tab with its More actions at 1440), and the actions it offers are the rules above at every width."""
    spec = UNDRAWN_DRAWERS[state]
    entry = Entry("B-JobDetail", "#/jobs", seed=seed_of(spec), now=NOW, drive=open_drawer(spec))
    out = _drawer_out(run_dir)
    with opened(app, db, live, entry, theme == "dark", 1440, 1180) as page:
        drawer = page.locator(APP_PANEL)
        for width, height in DRAWER_SIZES:
            _resize(page, width, height)
            (out / ("drawer-%s-%d-%s.png" % (state, width, theme))).write_bytes(shoot(page))
            assert _texts(drawer.locator(ROW_BUTTONS)) == ROW_ACTIONS[state], "%s at %d" % (state, width)
        _resize(page, 1440, 1180)
        _tab(page, "Technical")
        (out / ("drawer-%s-technical-1440-%s.png" % (state, theme))).write_bytes(shoot(page))
        assert _texts(drawer.locator("[data-section=more-actions] button")) == MORE_ACTIONS[state]


def _hover(name: str, role: str = "button"):
    return lambda page: page.locator(APP_PANEL).get_by_role(role, name=name, exact=True).hover()


def _keyboard_focus(name: str, role: str = "button"):
    """Focus by the keyboard (so :focus-visible draws the ring): focus it, step away and back."""
    def act(page):
        target = page.locator(APP_PANEL).get_by_role(role, name=name, exact=True)
        target.focus()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        expect(target).to_be_focused()
    return act


def _copied(page):
    chip = page.locator(APP_PANEL).get_by_role("button", name="Copy job id 7f20b6ce", exact=True)
    chip.click()
    expect(chip.locator('path[d="M20 6 9 17l-5-5"]')).to_have_count(1)     # the copy icon became the check
    page.mouse.move(0, 0)


# Hover and focus are not drawn for the drawer: each is the canvas's own rule for its kind (`.ghost:hover` raised,
# `.solid:hover` the hover purple, `.tile:hover` brightness .97, no hover on Regenerate… or a tab, the 2 px focus ring).
DRAWER_MOMENTS = [
    ("hover-download", _hover("Download all")), ("hover-regenerate", _hover("Regenerate…")),
    ("hover-copy-prompt", _hover("Copy prompt")), ("hover-copy-id", _hover("Copy job id 7f20b6ce")),
    ("hover-close", _hover("Close")), ("hover-tab", _hover("Timeline", "tab")),
    ("hover-tile", _hover("Open image 2 in the viewer")),
    ("focus-tab", _keyboard_focus("Overview", "tab")), ("focus-download", _keyboard_focus("Download all")),
    ("copied-id", _copied),
]


@pytest.mark.parametrize("theme", THEMES)
def test_drawer_hover_focus_and_copy_are_photographed(app, db, live, run_dir, theme):
    entry = Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW, drive=open_drawer(IMAGE_DONE))
    out = _drawer_out(run_dir)
    with opened(app, db, live, entry, theme == "dark", 1440, 1180) as page:
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        for name, act in DRAWER_MOMENTS:
            act(page)
            settle(page)
            (out / ("drawer-%s-%s.png" % (name, theme))).write_bytes(shoot(page, (820, 0, 620, 1180)))
            page.mouse.move(0, 0)


REVOKE = (".el-popper.rl-job-pop", "[data-section=more-actions] .job-btn--danger")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("scroll", (0, 300))
def test_the_drawer_popups_sit_by_their_trigger(app, db, live, run_dir, scroll, theme):
    """The drawer's one popover, Revoke share links (Technical), sits 6 px under or above its button, with the drawer
    body scrolled or not; the picture viewer a tile opens covers the window. Both photographed."""
    entry = Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW,
                  drive=open_drawer(IMAGE_DONE, tab="Technical"))
    out = _drawer_out(run_dir)
    with opened(app, db, live, entry, theme == "dark", 1440, 900) as page:
        drawer = page.locator(APP_PANEL)
        drawer.locator(".detail-drawer__body").evaluate("(b, y) => { b.scrollTop = y; }", scroll)
        drawer.get_by_role("button", name="Revoke share links", exact=True).click()
        expect(page.locator(REVOKE[0])).to_be_visible()
        settle(page)
        (out / ("drawer-revoke-pop-scroll%d-%s.png" % (scroll, theme))).write_bytes(shoot(page))
        misplaced = popup_place(page, *REVOKE)
        assert misplaced is None, misplaced
        page.keyboard.press("Escape")
        expect(page.locator(REVOKE[0])).to_be_hidden()
        if scroll:
            return
        _tab(page, "Overview")
        # a tile is a button: the keyboard reaches it and opens the viewer; Escape shuts the viewer, not the drawer
        drawer.get_by_role("button", name="Open image 2 in the viewer", exact=True).focus()
        page.keyboard.press("Enter")
        expect(page.locator(".el-image-viewer__wrapper")).to_be_visible()
        settle(page)
        (out / ("drawer-viewer-%s.png" % theme)).write_bytes(shoot(page))
        page.keyboard.press("Escape")
        expect(page.locator(".el-image-viewer__wrapper")).to_have_count(0)
        expect(drawer).to_be_visible()


DETAIL_ROUTE = re.compile(r"/admin/jobs/[0-9a-f-]{36}$")


@pytest.mark.parametrize("theme", THEMES)
def test_drawer_loading_and_a_failed_read_are_photographed(app, db, live, run_dir, theme):
    """The read held: the loading bars; the read answered 502: the error as the list's is drawn, with Retry."""
    def ready(page, live, data):
        expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)

    entry = Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW, drive=ready)
    out = _drawer_out(run_dir)
    with opened(app, db, live, entry, theme == "dark", 1440, 1180) as page:
        drawer = page.locator(APP_PANEL)
        page.route(DETAIL_ROUTE, lambda route: None)
        page.get_by_text(IMAGE_DONE["title"], exact=True).first.click()
        expect(drawer.locator("[data-state=loading]")).to_be_visible(timeout=TIMEOUT_MS)
        settle(page)
        (out / ("drawer-loading-%s.png" % theme)).write_bytes(shoot(page))
        page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()
        page.unroute(DETAIL_ROUTE)
        page.route(DETAIL_ROUTE, lambda route: route.fulfill(
            status=502, content_type="application/json",
            body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
        page.get_by_text(IMAGE_DONE["title"], exact=True).first.click()
        alert = drawer.get_by_role("alert")
        expect(alert).to_have_text(re.compile(r"Could not load the job: the relay answered 502 \(bad gateway\)\."))
        settle(page)
        (out / ("drawer-read-fails-%s.png" % theme)).write_bytes(shoot(page))


@pytest.mark.parametrize(("width", "close"), [(1440, "Escape"), (1440, "Close"), (390, "Escape"),
                                               (390, "Back to Jobs")])
def test_closing_the_drawer_gives_the_focus_back_to_the_row(app, db, live, width, close):
    """Opened from the keyboard (the title link; a phone row), closed by Escape or its button: the focus is where it
    was, on that row's opener."""
    def ready(page, live, data):
        expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)

    entry = Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW, drive=ready)
    with opened(app, db, live, entry, False, width, 900) as page:
        opener = page.locator("a.title-link" if width >= 768 else ".data-table__main").filter(
            has_text=IMAGE_DONE["title"]).first
        opener.focus()
        page.keyboard.press("Enter")
        drawer = page.locator(APP_PANEL)
        expect(drawer.locator("[data-section=summary]")).to_be_visible(timeout=TIMEOUT_MS)
        if close == "Escape":
            page.keyboard.press("Escape")
        else:
            drawer.get_by_role("button", name=close, exact=True).click()
        expect(drawer).to_be_hidden()
        expect(opener).to_be_focused()


# Nothing in the drawer may be cut, spill sideways or wrap a button or a tab, but the phone title, which the canvas
# draws to end in "…". Read from the elements themselves, so what is scrolled out of the drawer's body counts too.
FIT_JS = r"""(panel) => {
  const root = document.querySelector(panel), edge = root.getBoundingClientRect();
  const out = [];
  for (const el of root.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || el instanceof SVGElement) continue;
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    const name = (el.className && typeof el.className === 'string' ? el.className : el.tagName) + ': ' +
      (el.textContent || '').trim().slice(0, 60);
    if (r.left < edge.left - 0.5 || r.right > edge.right + 0.5) out.push('spills ' + name);
    if (s.overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) out.push('cut or scrolls sideways ' + name);
    if ((el.tagName === 'BUTTON' || el.getAttribute('role') === 'tab') && el.scrollHeight > el.clientHeight + 1)
      out.push('wraps ' + name);
  }
  return out;
}"""
DRAWN_TO_END_IN_ELLIPSIS = re.compile(r"^cut or scrolls sideways detail-drawer__title")
LIVE_JOBS = {"failed": LIVE_FAILED, "done": LIVE_DONE}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("job", list(LIVE_JOBS))
def test_drawer_with_live_like_data_fits(app, db, live, run_dir, job, theme):
    """Shaped like the live box (pixel_drawer.py): a 100,000-character prompt, a 27-character error code, a key
    without a label, 88 long events, a webhook retry, eight results. Each tab at 1440, 768 and 390: photographed at
    its top and its end, nothing cut, spilt or wrapped where the canvas does not, and every live-shaped value whole."""
    spec = LIVE_JOBS[job]
    entry = Entry("B-JobDetail", "#/jobs", seed=seed_of(spec), now=NOW, drive=open_drawer(spec))
    out = _drawer_out(run_dir)
    with opened(app, db, live, entry, theme == "dark", 1440, 1180) as page:
        body = page.locator(APP_PANEL).locator(".detail-drawer__body")
        for width, height in DRAWER_SIZES:
            _resize(page, width, height)
            shown = ""
            for tab in ("Overview", "Timeline", "Technical"):
                _tab(page, tab)
                where = "%s %s at %d" % (job, tab, width)
                for end in ("top", "end"):
                    body.evaluate("(b, end) => { b.scrollTop = end === 'end' ? b.scrollHeight : 0; }", end)
                    settle(page)
                    (out / ("live-drawer-%s-%s-%d-%s-%s.png" % (job, tab.lower(), width, end, theme))).write_bytes(
                        shoot(page))
                bad = [b for b in page.evaluate(FIT_JS, APP_PANEL)
                       if not (width < 768 and DRAWN_TO_END_IN_ELLIPSIS.match(b))]
                assert not bad, "%s: %r" % (where, bad)
                shown += page.locator(APP_PANEL).inner_text()
            missing = [v for v in LIVE_DRAWER_VALUES[job] if v not in shown]
            assert not missing, "%s at %d: live-shaped values not drawn: %r" % (job, width, missing)


def test_the_fit_check_names_a_cut_value_a_spill_and_a_wrapped_button(diff_page):
    """The live-data guard of the drawer fails on what the eye would call broken: a value cut by its box (an ellipsis),
    a block wider than the drawer, a button whose label wraps; a value that fits passes."""
    diff_page.set_content(
        '<div class="detail-drawer el-drawer" style="width:300px">'
        '<span style="display:block;width:60px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">'
        'dola.no_video_output_failed</span>'
        '<div style="width:400px">wide</div>'
        '<button style="width:50px;height:20px;overflow:hidden">Regenerate this job</button>'
        '<span style="display:block;width:280px;overflow:hidden;white-space:nowrap">d07-12</span></div>')
    found = diff_page.evaluate(FIT_JS, APP_PANEL)
    assert [f.split(" ", 1)[0] for f in found] == ["cut", "spills", "cut", "wraps"], found
    assert not [f for f in found if "d07-12" in f]


def test_the_tool_tells_two_names_of_one_button_apart(diff_page):
    """The phone sheet's Back is named `Back to Jobs` on the canvas: a button in its place named otherwise is a
    difference of its name (the signature differs, so the two are paired by their place)."""
    page = '<button aria-label="%s" style="width:44px;height:44px;border:1px solid red"></button>'
    diff_page.set_content(page % "Back to Jobs")
    drawn = extract(diff_page)
    diff_page.set_content(page % "Back")
    built = extract(diff_page)
    res = diff("button-name", drawn, built)
    assert [(prop, cv, av) for _c, _a, prop, cv, av in res.differences] == [("label", "Back to Jobs", "Back")]
