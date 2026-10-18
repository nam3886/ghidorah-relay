"""The Test page and the Login page against the canvas, element by element, through the Jobs page's comparison
(`test_element_diff.py`: the same pairing, tables and side-by-sides, written into `<run>/element-diff/`).

`test_test_and_login_match_the_canvas_element_by_element`: the B-, T- and P-Test boards (a key loaded, seedance_v2.5
picked, the form filled, the job waiting at step 2/4 with its empty video slot), the B- and P-Login boards (the wrong
password answered), and the Test result cards of B-States: nothing loaded yet, and a done video job."""

import re
from dataclasses import replace

import pytest
import test_element_diff as page_one
import test_element_diff_alerts as alerts_page
from element_diff import extract
from patchright.sync_api import expect
from pixel_capture import opened, shoot
from pixel_lane7 import key_page_drive, key_page_seed
from pixel_manifest import Entry, login_error
from pixel_seed import BOARD_USER, NOW, PROVIDER, shell_seed
from test_element_diff import Check

pytestmark = pytest.mark.browser

THEMES = ("light", "dark")
TIMEOUT_MS = 10_000
STATES = "B-States"
DESKTOP = (1440, 1000)

# ---- the boards --------------------------------------------------------------------------------------------------
TAB_STRIP_AT_START_JS = "() => document.querySelectorAll('header *').forEach((el) => { el.scrollLeft = 0; })"


def board_state(page, live, data):
    """The boards' state (pixel_lane7), with the tab strip at the start: the phone strip scrolls the current tab, the
    last one, into view (the shell's rule, DESIGN 10), where the board draws the strip unscrolled."""
    key_page_drive(page, live, data)
    page.evaluate(TAB_STRIP_AT_START_JS, isolated_context=False)


# Live data: the job id (a new job has a new id). The B and T boards write "tab's" with a straight apostrophe, P-Test
# and specs/test.md with U+2019 ("use U+2019 everywhere"): the app writes the curly one.
TEST = Entry("B-Test", "#/test", seed=key_page_seed, now=NOW, drive=board_state, user=BOARD_USER)
BOARD_DATA = ("e17c55b2", "The key lives only in this tab's session storage.")
BOARD_CHECKS = [Check(board, replace(TEST, board=board), data=BOARD_DATA) for board in ("B-Test", "T-Test", "P-Test")]
LOGIN_CHECKS = [Check(board, Entry(board, "#/login", signed_in=False, drive=login_error))
                for board in ("B-Login", "P-Login")]


# ---- the Test result cards of B-States -----------------------------------------------------------------------------
RESULT = 'section[aria-labelledby="result-title"]'


def nothing_loaded(page, live, data):
    expect(page.get_by_role("button", name="Load models")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.locator(RESULT)).to_be_visible()


def done_video(page, live, data):
    """A video job made with the board's model, answered done by its first poll with one share link."""
    def answer(route):
        made = route.request.url.rsplit("/", 1)[1]
        route.fulfill(status=200, content_type="application/json", body=(
            '{"id": "%s", "status": "done", "stage": null, "result_urls": ["/m/test-video"], "images": [], '
            '"model": "seedance_v2.5", "duration": 5, "ratio": "16:9", "created_at": "2026-10-15T20:50:12+07:00", '
            '"finished_at": "2026-10-15T20:52:00+07:00"}' % made))

    page.route(re.compile(r"/v1/video/generations/[0-9a-f-]+$"), answer)
    # the share link answers (an app that loads the video at once must not log a failed request)
    page.route(re.compile(r"/m/test-video$"), lambda route: route.fulfill(status=200, content_type="video/mp4",
                                                                       body=b""))
    key_page_drive(page, live, data, wait_for="done")


STATE_CHECKS = [
    Check("state-result-empty", Entry(STATES, "#/test", seed=shell_seed, now=NOW, drive=nothing_loaded,
                                      user=BOARD_USER),
          'section[aria-label="Result, empty"]', RESULT, DESKTOP, widen=True),
    Check("state-result-done", Entry(STATES, "#/test", seed=key_page_seed, now=NOW, drive=done_video,
                                     user=BOARD_USER),
          'section[aria-label="Result, done"]', RESULT, DESKTOP, widen=True, data=("3b9e1f0a", "5.04 s"),
          data_app=("5 s",)),
]
CHECKS = LOGIN_CHECKS + STATE_CHECKS


# The boards draw the form's selects as native selects, the app as FilterSelect fields: the Alerts page's comparison
# (test_element_diff_alerts.run_check) compares each field's box as a box and what it draws inside (its value and
# arrow) against the native select as pixels. The boards write `<select value="30 s">`, which HTML does not read (the
# browser shows the first option, `5 s`): the renderer is made to show the option the attribute names.
SELECT_VALUES_JS = """() => document.querySelectorAll('select[value]').forEach((s) => {
  const want = s.getAttribute('value');
  const option = [...s.options].find((o) => o.text === want || o.value === want);
  if (option) s.value = option.value;
})"""


class BoardSelects:
    """The canvas renderer, each native select of the board showing the option its `value` attribute names."""

    def __init__(self, renderer):
        self._renderer = renderer

    def __getattr__(self, name):
        return getattr(self._renderer, name)

    def open(self, *args, **kwargs) -> None:
        self._renderer.open(*args, **kwargs)
        self._renderer.page.evaluate(SELECT_VALUES_JS, isolated_context=False)
        self._renderer.settle()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", BOARD_CHECKS, ids=lambda c: c.name)
def test_test_boards_match_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    fields = []
    bad = alerts_page.failed(alerts_page.run_check(app, db, live, BoardSelects(renderer), diff_page, run_dir, check,
                                                   theme, fields=fields))
    assert not bad, "%s %s: element differences in %s" % (check.name, theme, ", ".join(bad))
    assert len(fields) == 2, "the Duration and Ratio fields against the board's two native selects: %r" % fields
    off = [(n, f) for n, f in enumerate(fields) if f[0] > alerts_page.SELECT_MISMATCH_PCT]
    assert not off, "a field draws its value or arrow apart from the native select (n, (mismatch %%, aa %%)): %r" % off


def requested_duration_leader(item, data) -> bool:
    """The dotted leader of the done card's Duration row: the app shows the requested "5 s" where the board draws the
    measured "5.04 s" (the public status has no measured length, an accepted data limit), so the leader beside the
    shorter value is longer. Only that leader, in that row."""
    rows = [c["rect"][1] for c, _a in data if c and c["text"] == "5.04 s"]
    return (item["kind"] == "box" and item["tag"] == "span" and "dotted" in item["style"]["border"]
            and any(abs(item["rect"][1] - y - 8) <= 6 for y in rows))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_test_and_login_match_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check,
                                                            theme, monkeypatch):
    if check.name == "state-result-done":
        compare = page_one.diff

        def diff(*args, **kwargs):
            res = compare(*args, **kwargs)
            res.differences = [d for d in res.differences
                               if not (d[2] == "box" and requested_duration_leader(d[0], res.data))]
            return res

        monkeypatch.setattr(page_one, "diff", diff)
    page_one.test_jobs_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check,
                                                             theme)


# ---- data shaped like the live box -------------------------------------------------------------------------------
# Read on the box 17/10, read-only, lengths and shapes only. The live catalog is dola's (relay_dola/catalog.py): three
# video models with their own lengths and two image models, ids of 12 to 16 characters, each with its display name.
# Its options (provider_snapshots): 43 templates whose label is the whole prompt, 54 to 3,954 characters (median 1,210);
# 6 ratios labelled in 19 to 35 characters around a value of 3 or 4 ("16:9"); 13 styles of 2 to 18 characters.
LIVE_MODELS = (("seedance-2.5", "video", (5, 10, 20, 25, 30), "Dreamina Seedance 2.5"),
               ("seedance-2.0", "video", (5, 10, 15), "Seedance 2.0 Fast"),
               ("seedance-1.0", "video", (5, 10), "Seedance 1.0"),
               ("seedream-5.0-pro", "image", (), "Seedream 5.0 Pro"),
               ("seedream-4.5", "image", (), "Seedream 4.5"))
TEMPLATE_LENGTHS = (54, 58, 75, 90, 120, 180, 240, 320, 400, 480, 560, 640, 720, 800, 880, 960, 1040, 1100, 1150,
                    1190, 1210, 1210, 1230, 1290, 1350, 1420, 1500, 1600, 1700, 1800, 1950, 2053, 2200, 2400, 2519,
                    2700, 2900, 3100, 3300, 3500, 3700, 3850, 3954)
RATIOS = (("1:1", 19), ("2:3", 25), ("3:4", 19), ("4:3", 29), ("9:16", 33), ("16:9", 35))
STYLE_LENGTHS = (9, 9, 5, 2, 9, 12, 10, 17, 18, 5, 14, 8, 9)
WORDS = ("A slow dolly shot along a rain soaked street at night, neon reflections ripple in the puddles while a "
         "lone cyclist passes, steam rises from a food stall, cinematic lighting, shallow depth of field. ")
LONG_FILE = "reference-photo-of-the-night-market-stall-under-neon-light-take-07.jpg"


def text_of(length: int, head: str = "") -> str:
    """`head` and then words, exactly `length` characters, ending in a full stop."""
    return (head + WORDS * (length // len(WORDS) + 2))[:length - 1] + "."


def live_catalog(live) -> None:
    """The live server's one provider serves the live catalog's shapes (see above)."""
    import dataclasses

    from relay_core.providers import ModelSpec, check_catalog

    spec = live.env.registry.spec(PROVIDER)
    spec.frames = None
    spec.catalog = lambda: [ModelSpec(m, kind, ("generate",), limits={"durations": list(lengths)} if lengths else {},
                                      prices={"default": 2 if kind == "video" else 1}, display_name=name)
                            for m, kind, lengths, name in LIVE_MODELS]
    original = type(spec).plan.__get__(spec)            # the fake's own plan, not the boards' wrapper of it

    def plan(req, settings, snapshots):
        video = any(m == req.model and kind == "video" for m, kind, _l, _n in LIVE_MODELS)
        suffixed = dataclasses.replace(req, model=req.model + ("-video" if video else "-image"))
        return dataclasses.replace(original(suffixed, settings, snapshots), model=req.model)

    spec.plan = plan
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])
    spec.extras = lambda snapshots: {
        "templates": [{"show_value": t, "prompt": t} for t in (text_of(n) for n in TEMPLATE_LENGTHS)],
        "ratios": [{"label": text_of(n - len(value) - 3, "Frame ") + " (" + value + ")", "value": value}
                   for value, n in RATIOS],
        "styles": [{"label": text_of(n), "value": text_of(n)} for n in STYLE_LENGTHS]}


def live_like_seed(db, live):
    data = key_page_seed(db, live)
    live_catalog(live)
    return data


def live_like_drive(page, live, data):
    """The live catalog loaded, seedance-2.5 picked, a long prompt, ten long-named images, a job made, the wallet
    limits open."""
    page.get_by_role("textbox", name="API key", exact=True).fill(data["raw_key"])
    page.get_by_role("button", name="Load models").click()
    model = page.get_by_test_id("test-model").filter(has_text="seedance-2.5")
    expect(model).to_be_visible(timeout=TIMEOUT_MS)
    model.click()
    expect(page.get_by_role("group", name="Prompt templates")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_label("Prompt", exact=True).fill(text_of(400))    # the test provider takes no longer prompt
    page.get_by_label("Title", exact=True).fill(text_of(120))
    page.get_by_role("button", name="Generate", exact=True).click()
    expect(page.get_by_role("link", name="Open job", exact=False)).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_test_id("attach-input").set_input_files(files=[
        {"name": "%02d-%s" % (i, LONG_FILE), "mimeType": "image/jpeg", "buffer": b"\0" * 2048} for i in range(10)])
    expect(page.get_by_test_id("attachment")).to_have_count(10)
    page.locator(".test-tab__points-box summary").click()
    page.mouse.move(0, 0)


# the texts drawn to end in an ellipsis when they do not fit: the phone model title, a file name, a template chip
ELLIPSIS = re.compile(r"model__title|images__name|chip")
# a box whose text runs out of it (a long chip, a long button): the guard reads it, as an overflow clip does not show it
SPILL_JS = """() => [...document.querySelectorAll('.test-tab button, .test-tab label, .test-tab span, .test-tab p')]
  .filter((el) => el.offsetParent && getComputedStyle(el).textOverflow !== 'ellipsis'
                  && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
                  && getComputedStyle(el).overflowX === 'visible' && getComputedStyle(el).display !== 'inline')
  .map((el) => [el.className, el.textContent.slice(0, 40), el.clientWidth, el.scrollWidth, el.clientHeight,
                el.scrollHeight])"""


def whole_page(page) -> bytes:
    """A full-page photo from the top: taken while scrolled, it draws the sticky top bar in the middle of the page."""
    page.evaluate("window.scrollTo(0, 0)")
    return page.screenshot(full_page=True, animations="disabled", caret="hide")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
def test_test_page_with_live_like_data_fits(app, db, live, run_dir, width, theme):
    state = Entry("B-Test", "#/test", seed=live_like_seed, now=NOW, drive=live_like_drive, user=BOARD_USER)
    with opened(app, db, live, state, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("live-like-test-%d-%s.png" % (width, theme))).write_bytes(whole_page(page))
        sideways = page.evaluate("document.documentElement.scrollWidth - innerWidth", isolated_context=False)
        assert sideways <= 0, "the page scrolls sideways by %d px" % sideways
        items = extract(page, ".test-tab")
        # below 1280 the models are one row that scrolls sideways, as the T and P boards draw it: a card past the edge
        # is scrolled to, not cut
        may_cut = re.compile(ELLIPSIS.pattern + ("|model__" if width < 1280 else ""))
        clipped = [t for t in items["texts"] if t["clipped"] and not may_cut.search(t.get("cls", ""))]
        assert not clipped, "texts cut off where the canvas draws them whole: %r" % [
            (t["text"][:40], t["cls"], t["rect"], t["full"]) for t in clipped]
        spilled = page.evaluate(SPILL_JS, isolated_context=False)
        assert not spilled, "texts that run out of their box: %r" % spilled
        # a template chip is one line of at most 240 px (manager's ruling 18/10): a whole prompt is never one chip
        wide = page.evaluate("() => [...document.querySelectorAll('.chip')].filter((c) => "
                             "c.getBoundingClientRect().width > 240.5).length", isolated_context=False)
        assert not wide, "%d template chips are wider than 240 px" % wide


# ---- states the canvas does not draw: photographed beside their nearest drawn pattern ------------------------------

def _login_normal(page):
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password", exact=True).fill("example-password")


def _login_busy(page):
    page.route("**/admin/login", lambda route: None)                 # the answer never comes: the button stays busy
    _login_normal(page)
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator("button[type=submit]")).to_be_disabled()


def _login_revealed(page):
    _login_normal(page)
    page.get_by_role("button", name="Show password").click()
    expect(page.get_by_role("button", name="Hide password")).to_be_visible()


def _login_focus(page):
    _login_normal(page)
    page.get_by_label("Password", exact=True).focus()
    page.keyboard.press("Shift+Tab")
    expect(page.get_by_label("Username")).to_be_focused()


def _login_error_focus(page):
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password", exact=True).fill("example-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_label("Username").focus()
    page.keyboard.press("Tab")
    expect(page.get_by_label("Password", exact=True)).to_be_focused()


def _login_hover(page):
    _login_normal(page)
    page.get_by_role("button", name="Sign in").hover()


LOGIN_UNDRAWN = [("normal", _login_normal), ("busy", _login_busy), ("revealed", _login_revealed),
                 ("focus-username", _login_focus), ("error-focus-password", _login_error_focus),
                 ("hover-sign-in", _login_hover)]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
@pytest.mark.parametrize(("state", "act"), LOGIN_UNDRAWN, ids=[u[0] for u in LOGIN_UNDRAWN])
def test_login_undrawn_states_are_photographed(app, db, live, run_dir, state, act, width, theme):
    entry = Entry("B-Login", "#/login", signed_in=False, drive=lambda page, live, data: act(page))
    with opened(app, db, live, entry, theme == "dark", width, 760) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-login-%s-%d-%s.png" % (state, width, theme))).write_bytes(shoot(page))


def _test_fresh(page, live, data):
    expect(page.get_by_role("button", name="Load models")).to_be_visible(timeout=TIMEOUT_MS)


def _test_loaded(page, live, data):
    page.get_by_role("textbox", name="API key", exact=True).fill(data["raw_key"])
    page.get_by_role("button", name="Load models").click()
    expect(page.get_by_test_id("test-model").first).to_be_visible(timeout=TIMEOUT_MS)


def _test_load_pending(page, live, data):
    page.route(re.compile(r"/v1/models$"), lambda route: None)
    page.get_by_role("textbox", name="API key", exact=True).fill(data["raw_key"])
    page.get_by_role("button", name="Load models").click()
    expect(page.get_by_role("button", name="Load models")).to_have_attribute("aria-busy", "true")


def _test_refused(page, live, data):
    page.get_by_role("textbox", name="API key", exact=True).fill("rk-not-a-real-key")
    page.get_by_role("button", name="Load models").click()
    expect(page.locator(".test-result__status")).to_contain_text(":", timeout=TIMEOUT_MS)
    page.problems.clear()                                    # the refused key is a console line


def _test_wallet_open(page, live, data):
    key_page_drive(page, live, data)
    page.locator(".test-tab__points-box summary").click()


def _test_failed(page, live, data):
    def answer(route):
        made = route.request.url.rsplit("/", 1)[1]
        route.fulfill(status=200, content_type="application/json", body=(
            '{"id": "%s", "status": "failed", "stage": null, "result_urls": [], "images": [], "error": "Dola refused '
            'the prompt as content. Dola used no credit, so the key got its points back.", "error_code": '
            '"dola.content_refused", "model": "seedance_v2.5", "duration": 30, "ratio": "16:9", '
            '"created_at": "2026-10-15T20:52:30+07:00", "finished_at": "2026-10-15T20:53:10+07:00"}' % made))

    page.route(re.compile(r"/v1/video/generations/[0-9a-f-]+$"), answer)
    key_page_drive(page, live, data, wait_for="failed")


def _test_done_played(page, live, data):
    done_video(page, live, data)
    page.get_by_role("button", name="Play video").click()
    expect(page.locator(".result__frame video")).to_be_visible()
    page.problems.clear()                                    # an empty body is not a playable video: a console line


# A 16:9 picture in the canvas's tile colour; the second link answers bytes that are no picture (a broken image)
TILE_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900">'
            b'<rect width="1600" height="900" fill="#E9E2F5"/></svg>')


def _image(route):
    good = route.request.url.endswith("-1")
    route.fulfill(status=200, content_type="image/svg+xml" if good else "image/png",
                  body=TILE_SVG if good else b"not a picture")


def _test_done_image(page, live, data):
    """A done image job (not drawn): the frame of the done video card, each image in a media box; the second one
    broken."""
    def answer(route):
        made = route.request.url.rsplit("/", 1)[1]
        route.fulfill(status=200, content_type="application/json", body=(
            '{"id": "%s", "status": "done", "stage": null, "result_urls": [], "images": ["/m/test-image-1", '
            '"/m/test-image-2"], "model": "seedream-4.5", "ratio": "16:9", "points": 1, "refunded": false, '
            '"created_at": "2026-10-15T20:52:30+07:00", "finished_at": "2026-10-15T20:53:12+07:00"}' % made))

    page.route(re.compile(r"/v1/images/generations/[0-9a-f-]+$"), answer)
    page.route(re.compile(r"/m/test-image-\d$"), _image)
    page.get_by_role("textbox", name="API key", exact=True).fill(data["raw_key"])
    page.get_by_role("button", name="Load models").click()
    model = page.get_by_test_id("test-model").filter(has_text="seedream-4.5")
    expect(model).to_be_visible(timeout=TIMEOUT_MS)
    model.click()
    page.get_by_label("Prompt", exact=True).fill("A bowl of noodles on a market stall, soft light.")
    page.get_by_role("button", name="Generate", exact=True).click()
    expect(page.locator(RESULT).locator(".status-chip")).to_contain_text("done", timeout=TIMEOUT_MS)
    expect(page.locator(".result__done .result__image img")).to_have_count(1, timeout=TIMEOUT_MS)
    expect(page.locator(".result__done .result__broken")).to_have_count(1, timeout=TIMEOUT_MS)


def _test_focus_model(page, live, data):
    key_page_drive(page, live, data)
    page.get_by_test_id("test-model").nth(1).focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")


def _test_hover_chip(page, live, data):
    key_page_drive(page, live, data)
    page.get_by_role("group", name="Prompt templates").get_by_role("button").nth(1).hover()


TEST_UNDRAWN = [("fresh", _test_fresh), ("loaded-no-model", _test_loaded), ("load-pending", _test_load_pending),
                ("refused-key", _test_refused), ("wallet-open", _test_wallet_open), ("failed-job", _test_failed),
                ("done-played", _test_done_played), ("done", done_video), ("done-image", _test_done_image),
                ("focus-model", _test_focus_model),
                ("hover-chip", _test_hover_chip)]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
@pytest.mark.parametrize(("state", "act"), TEST_UNDRAWN, ids=[u[0] for u in TEST_UNDRAWN])
def test_test_undrawn_states_are_photographed(app, db, live, run_dir, state, act, width, theme):
    entry = Entry("B-Test", "#/test", seed=key_page_seed, now=NOW, drive=act, user=BOARD_USER)
    with opened(app, db, live, entry, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-test-%s-%d-%s.png" % (state, width, theme))).write_bytes(whole_page(page))
