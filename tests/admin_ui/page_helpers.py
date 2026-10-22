"""Page readings the browser tests of this folder share: wait until the app is at rest, how far the page runs past its
width, and the page laid out as one block. The page scrolls in its own area (`.app-scroll__wrap`), not in the window,
so a reading of the document alone sees only the viewport: go through these."""

from contextlib import contextmanager

TIMEOUT_MS = 10_000
WRAP = ".app-scroll__wrap"
TWO_FRAMES_JS = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
AT_REST_JS = """() => document.getAnimations().every(
    a => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)"""
SIDEWAYS_JS = """() => {
  const w = document.querySelector('%s'), d = document.documentElement;
  return Math.max(d.scrollWidth - innerWidth, w ? w.scrollWidth - w.clientWidth : 0);
}""" % WRAP


def settle(page) -> None:
    """Fonts loaded, two frames drawn, every finite transition finished."""
    page.evaluate("document.fonts.ready.then(() => true)", isolated_context=False)
    page.evaluate(TWO_FRAMES_JS)
    page.wait_for_function(AT_REST_JS, timeout=TIMEOUT_MS)


@contextmanager
def flow(page):
    """The page laid out as one block (the phone layout: no scroll area, the document scrolls), so an element read sees
    all of it. The same rule set as print: no test-only hook in the app."""
    page.emulate_media(media="print")
    try:
        yield
    finally:
        page.emulate_media(media="null")


def scroll_page_to(page, y: int) -> None:
    """Scroll the page (the scroll area's wrap when there is one, else the window) to `y`."""
    page.evaluate("y => { const w = document.querySelector('%s'); if (w) w.scrollTop = y; else scrollTo(0, y); }"
                  % WRAP, y, isolated_context=False)


def sideways(page) -> int:
    """How far the page runs past its width: the document or the scroll area, whichever is wider."""
    return page.evaluate(SIDEWAYS_JS, isolated_context=False)
