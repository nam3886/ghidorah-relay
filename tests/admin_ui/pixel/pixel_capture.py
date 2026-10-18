"""The app side: put the app in the state of an artboard and photograph it at the board's viewport."""

from contextlib import contextmanager

from patchright.sync_api import expect

TIMEOUT_MS = 10_000
TIMEZONE = "Asia/Saigon"            # the zone the boards' times are written in
TWO_FRAMES_JS = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
AT_REST_JS = """() => document.getAnimations().every(
    a => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)"""


def settle(page) -> None:
    """Fonts loaded, two frames drawn, every finite transition finished."""
    page.evaluate("document.fonts.ready.then(() => true)", isolated_context=False)
    page.evaluate(TWO_FRAMES_JS)
    page.wait_for_function(AT_REST_JS, timeout=TIMEOUT_MS)


@contextmanager
def opened(app, db, live, entry, dark: bool, width: int, height: int):
    """The app's page at the board's viewport, in the state `entry` describes (seeded, clock frozen, driven, settled).
    On the way out the page must have reported no CSP, script or console problem."""
    data = entry.seed(db, live) if entry.seed else None
    context, page = app.new_page("dark" if dark else "light")
    try:
        # the shared page factory takes no time zone: set it on the page's own session before anything loads
        context.new_cdp_session(page).send("Emulation.setTimezoneOverride", {"timezoneId": TIMEZONE})
        page.set_viewport_size({"width": width, "height": height})
        if entry.now:
            context.clock.set_fixed_time(entry.now)
        if entry.prepare:
            entry.prepare(page, live, data)
        if entry.signed_in:
            app.sign_in(page, entry.user)
        page.goto(app.url + entry.route)
        if entry.drive:
            entry.drive(page, live, data)
        expect(page.locator("body")).to_be_visible()
        settle(page)
        yield page
        page.csp.check()
        # a script error, a failed resource or a console error means the photo may be of a half-drawn app
        assert not page.problems, "the app reported problems while %s was drawn: %r" % (entry.name, page.problems)
    finally:
        context.close()


# the page scroller's own bar only: the harness Chrome draws no native bars and the canvas has none, so a photo of the
# app must not show this one; every other bar stays in the photo. Hidden through the element's CSSOM: the CSP has no
# style-src, so a style sheet injected by the screenshot call (its `style=` option) would be blocked and reported.
PAGE_BAR_JS = """(hide) => document.querySelectorAll('.app-scroll > .el-scrollbar__bar')
  .forEach((bar) => { bar.style.visibility = hide ? 'hidden' : ''; })"""
WRAP = ".app-scroll__wrap"
SIDEWAYS_JS = """() => {
  const w = document.querySelector('%s'), d = document.documentElement;
  return Math.max(d.scrollWidth - innerWidth, w ? w.scrollWidth - w.clientWidth : 0);
}""" % WRAP


def shoot(page, region: tuple | None = None, bars: bool = False) -> bytes:
    """A viewport photo. `bars=True` keeps the page scroller's bar in it (the scrollbar scene only)."""
    clip = dict(zip(("x", "y", "width", "height"), region, strict=True)) if region else None
    if not bars:
        page.evaluate(PAGE_BAR_JS, True, isolated_context=False)
    try:
        return page.screenshot(clip=clip, caret="hide", animations="disabled")
    finally:
        if not bars:
            page.evaluate(PAGE_BAR_JS, False, isolated_context=False)


@contextmanager
def flow(page):
    """The page laid out as one block (the phone layout: no scroll area, the document scrolls), so a whole-page photo
    and an element read see all of it. The same rule set as print: no test-only hook in the app."""
    page.emulate_media(media="print")
    try:
        yield
    finally:
        page.emulate_media(media="null")


def scroll_page_to(page, y: int) -> None:
    """Scroll the page (the scroll area's wrap when there is one, else the window) to `y`."""
    page.evaluate("y => { const w = document.querySelector('%s'); if (w) w.scrollTop = y; else scrollTo(0, y); }"
                  % WRAP, y, isolated_context=False)


def full_photo(page) -> bytes:
    """The whole page from the top, in the flow layout (taken while scrolled, a sticky bar is drawn mid-page)."""
    scroll_page_to(page, 0)
    with flow(page):
        settle(page)
        return page.screenshot(full_page=True, animations="disabled", caret="hide")


def sideways(page) -> int:
    """How far the page runs past its width: the document or the scroll area, whichever is wider."""
    return page.evaluate(SIDEWAYS_JS, isolated_context=False)


# Entries whose page is taller than its board at the board's size, each with the reason. The canvas side fails a board
# that does not fit (`test_pixel.py`); this is its twin for the app, which reads the page scroller.
TALL_PAGES: dict[str, str] = {
    "B-AccountEdit": "a dialog over the Accounts list: the rows behind the scrim run past the board's height",
    "B-AccountImport": "a dialog over the Accounts list: the rows behind the scrim run past the board's height",
    "B-LogDetail": "a drawer over the Logs list: the rows behind the scrim run past the board's height",
}


def assert_fits_board(page, entry) -> None:
    """The page scrolls by no more than a pixel at the board's size, unless the entry is listed in TALL_PAGES."""
    room = page.evaluate("""() => { const w = document.querySelector('%s');
      return w ? w.scrollHeight - w.clientHeight : document.documentElement.scrollHeight - innerHeight; }""" % WRAP,
                         isolated_context=False)
    if entry.name in TALL_PAGES:
        assert room > 1, "%s is listed as taller than its board but fits: drop it from TALL_PAGES" % entry.name
    else:
        assert room <= 1, "the app's page for %s is %d px taller than its board" % (entry.name, room)


def capture(app, db, live, entry, dark: bool, width: int, height: int) -> bytes:
    """A viewport screenshot (or the entry's region of it) of the app in the state `entry` describes."""
    with opened(app, db, live, entry, dark, width, height) as page:
        assert_fits_board(page, entry)
        return shoot(page, entry.region)
