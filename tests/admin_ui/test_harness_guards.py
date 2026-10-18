"""Source scans that keep the browser tests honest now that the page scrolls in its own area, not in the window.

A whole-page photo or a "does the page scroll sideways" check that reads the document alone sees only the viewport (or
nothing) once the page scroller is an element. So the readings go through the helpers in `pixel/pixel_capture.py`
(`full_photo`, `sideways`, `scroll_page_to`), which know the scroller. These scans fail when a test goes around them:

- `full_page=` with any value, outside the helper and the canvas renderer;
- the document's scroll width, outside the helper and the canvas renderer;
- a bare `scrollWidth`, outside the files that read the width of ONE element (a table card, a drawer): a new use fails
  until it is looked at and its file and count are added here.
"""

import re
from pathlib import Path

from relay_core.testing.mutation import mutation

HERE = Path(__file__).resolve().parent
SOURCES = sorted(p for p in HERE.rglob("*.py") if "__pycache__" not in p.parts)

# the helper itself and the canvas renderer (it photographs a board, not the app); this file is never scanned for the
# patterns it spells out
FULL_PAGE_OK = {"pixel_capture.py", "test_pixel.py"}
DOCUMENT_WIDTH_OK = {"pixel_capture.py", "test_pixel.py"}
# files that read the scroll width of one element, and how many times
ELEMENT_WIDTH = {
    "pixel_capture.py": 2,                  # the helper: the document and the page scroller
    "test_pixel.py": 4,                     # the canvas renderer's own page
    "test_e2e_accounts.py": 2,              # the table card's own sideways scroll
    "test_e2e_jobs.py": 1,
    "test_e2e_shell.py": 1,                 # elements wider than the page, named in a failure message
    "test_element_diff_alerts.py": 2,       # each table box scrolled to its start and end
    "test_element_diff_accounts.py": 2,
    "test_element_diff_drawer.py": 1,
    "test_element_diff_shell.py": 3,        # the scroll-bar scenes: one scroller per scene
    "test_element_diff_test_login.py": 2,   # a box whose text runs out of it
    "test_harness_guards.py": 10,           # the patterns of this file
}


def _uses(pattern: str, own: bool = False) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in SOURCES:
        if path.name == Path(__file__).name and not own:
            continue
        n = len(re.findall(pattern, path.read_text(encoding="utf-8")))
        if n:
            found[path.name] = n
    return found


# an anchor with a double quote in it is written with an escape here, so the marker's own text is not a second match
@mutation("tests/admin_ui/test_harness_guards.py", "_uses(r\"full_page", "_uses(r\"import|full_page",
          expect="use pixel_capture.full_photo")
def test_no_photo_of_the_whole_page_goes_around_the_helper():
    loose = {name: n for name, n in _uses(r"full_page\s*=").items() if name not in FULL_PAGE_OK}
    assert not loose, "use pixel_capture.full_photo (the page scroller hides the rest of the page): %r" % loose


@mutation("tests/admin_ui/test_harness_guards.py", "_uses(r\"documentElement", "_uses(r\"import|documentElement",
          expect="use pixel_capture.sideways")
def test_no_sideways_check_reads_the_document_alone():
    loose = {name: n for name, n in _uses(r"documentElement\.scrollWidth|scrollingElement").items()
             if name not in DOCUMENT_WIDTH_OK}
    assert not loose, "use pixel_capture.sideways (it reads the page scroller too): %r" % loose


@mutation("tests/admin_ui/test_harness_guards.py", "_uses(r\"scrollWidth\", own=True) == ELEMENT_WIDTH",
          "_uses(r\"scrollWidth\", own=True) != ELEMENT_WIDTH", expect="a scrollWidth was added or removed")
def test_a_bare_scroll_width_is_one_element_that_was_looked_at():
    assert _uses(r"scrollWidth", own=True) == ELEMENT_WIDTH, (
        "a scrollWidth was added or removed: a page-level reading belongs in pixel_capture.sideways; an element-level "
        "one is added to ELEMENT_WIDTH once it is reviewed")


def test_the_scans_see_what_they_are_meant_to_see():
    """The patterns bite: each loophole they exist for is matched."""
    for text in ("page.screenshot(full_page=True)", "full_page = not viewport", "screenshot(full_page=full)"):
        assert re.search(r"full_page\s*=", text), text
    for text in ("document.documentElement.scrollWidth - innerWidth", "document.scrollingElement.scrollWidth"):
        assert re.search(r"documentElement\.scrollWidth|scrollingElement", text), text
