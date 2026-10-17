"""The Jobs list against the canvas, element by element (element_diff.py), and the Jobs list with data shaped like the
live box's.

`test_jobs_matches_the_canvas_element_by_element`: one comparison per entry and theme. The whole B-, T- and P-Jobs
boards, and the Jobs states that B-States draws (no results, loading, error, the hour chip in the toolbar and in the
phone filter row, the open listbox and its trigger at 1440, 768 and 390). A state card of B-States is drawn narrower
than the app's card, so the card is widened to the app's width first (its markup is fluid: the canvas pattern at the
app's width). Each comparison writes, into `<run>/element-diff/`:
`<name>-<theme>.md` (the table), `<name>-<theme>.side.png` (canvas | app | pixel diff) and fails while the table holds
a difference, an element missing on one side, or a text that differs and is not live data.

`test_jobs_with_live_like_data_fits`: long titles and ids, keys without labels, several older days, three and four
digit counts, failed and refunded rows, at 1440, 768 and 390 in both themes: full-page photos, and nothing is clipped
but the texts that are drawn to end in an ellipsis."""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import pytest
from accountkit import new_account
from element_diff import diff, extract, markdown, popup_place, side_by_side
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_capture import opened, shoot
from pixel_diff import compare
from pixel_jobs import SAIGON, canvas_band, jobs_seed, tick_the_two
from pixel_manifest import Entry
from pixel_render import boards
from pixel_seed import BOARD_USER, NOW, PROVIDER, WALLETS

pytestmark = pytest.mark.browser

BOARDS = {name: (w, h) for name, w, h in boards()}
THEMES = ("light", "dark")
TIMEOUT_MS = 10_000
STATES = "B-States"


@dataclass(frozen=True)
class Check:
    name: str
    state: Entry                    # how the app reaches the state (seed, clock, drive); its board is the canvas board
    canvas_root: str | None = None  # CSS selector of the compared part of the canvas, `@N` picks the N-th match
    app_root: str | None = None     # the same part in the app
    viewport: tuple | None = None   # the app's viewport when it is not the board's (a state drawn on B-States)
    widen: bool = False             # widen the canvas part to the app part's width first
    data: tuple = ()                # canvas texts that are live data where the app has its own value
    accepted: tuple = field(default=())   # canvas texts the app leaves out by an accepted deviation
    data_app: tuple = ()            # app texts that are live data where the canvas has none (more options)
    loose: dict = field(default_factory=dict)   # role (or "*") -> box axes that are data there (see element_diff)
    accepted_app: tuple = ()        # app texts or box signatures added by an instruction the canvas does not draw
    popup: tuple = ()               # (open popup, its trigger) in the app: the popup must sit 6 px clear of it


# ---- the boards --------------------------------------------------------------------------------------------------
# Live data on the boards (pixel_jobs.py says why): the list total (1,325 in the database) and the banner's quote,
# which B-Jobs writes with a straight apostrophe (the reply as stored has the curly one, as T-Jobs and P-Jobs write it).
BOARD_DATA = ("Showing 1–10 of 1,321",
              "Dola replied “Sorry, I can't create that video” to 4 jobs from 3 keys.")
JOBS = Entry("B-Jobs", "#/jobs", seed=jobs_seed, now=NOW, drive=tick_the_two, prepare=canvas_band, user=BOARD_USER)
BOARD_CHECKS = [Check(board, replace(JOBS, board=board), data=BOARD_DATA) for board in ("B-Jobs", "T-Jobs", "P-Jobs")]


# ---- the states of B-States --------------------------------------------------------------------------------------

def _choose(page, label: str, option: str) -> None:
    """Pick an option of the Jobs filter select named `label` (Status, Key)."""
    page.get_by_role("combobox", name=label, exact=True).click()
    page.get_by_role("option", name=option, exact=True).click()
    expect(page.get_by_role("combobox", name=label, exact=True)).to_contain_text(option)


def empty_seed(db, live):
    """Two keys: Partner B has no job, Shop A has one that is done."""
    from accountkit import done_job
    keys = {}
    for label in ("Shop A", "Partner B"):
        keys[label] = new_key(db, wallets=WALLETS)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, keys[label])
    done_job(db, keys["Shop A"], ref="one")
    return keys


def no_results(page, live, data):
    expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)
    _choose(page, "Status", "Unknown")
    _choose(page, "Key", "Partner B")
    expect(page.locator("[data-state=empty]")).to_be_visible(timeout=TIMEOUT_MS)


def held(page, live, data):
    """The list answers never: the skeleton stays."""
    page.route(re.compile(r"/admin/jobs\?"), lambda route: None)
    page.reload()
    expect(page.locator("[data-state=loading]")).to_be_visible(timeout=TIMEOUT_MS)


def failing(page, live, data):
    """The list answers 502, as the canvas's error card says."""
    page.route(re.compile(r"/admin/jobs\?"), lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.reload()
    expect(page.locator("[data-state=error]")).to_be_visible(timeout=TIMEOUT_MS)


def hour_chip(page, live, data):
    """A click on the 20:00 column (the last one) adds the chip."""
    page.get_by_test_id("hour-bar").last.click()
    expect(page.get_by_test_id("hour-chip")).to_contain_text("Hour 20:00–21:00")
    expect(page.locator("[data-state=ready], [data-state=empty]").first).to_be_visible(timeout=TIMEOUT_MS)


def status_open(page, live, data):
    page.get_by_role("combobox", name="Status", exact=True).click()
    expect(page.get_by_role("listbox")).to_be_visible()


def _state(drive, seed=jobs_seed):
    return Entry(STATES, "#/jobs", seed=seed, now=NOW, drive=drive, prepare=canvas_band,
                 user=BOARD_USER if seed is jobs_seed else None)


DESKTOP, TABLET, PHONE = (1440, 1000), (768, 1000), (390, 900)
# The open list is compared with the drawn Provider list (B-States): its four options and the word "Provider" are
# that list's data, the eight statuses this one's; the list's height follows the number of options.
LISTBOX_DATA = ("All", "dola", "vibes", "meta", "Provider")
STATUS_WORDS = ("Queued", "Submitting", "Waiting", "Fetching", "Unknown", "Done", "Failed")
OPEN_LIST = ('[role="listbox"]', '[role="combobox"][aria-expanded="true"]')
STATE_CHECKS = [
    Check("state-no-results", _state(no_results, empty_seed), 'section[aria-label="Jobs, no results"]',
          'section[aria-label="Job list"]', DESKTOP, widen=True),
    Check("state-loading", _state(held), 'section[aria-label="Jobs, loading"] table', ".data-table.jobs__table",
          DESKTOP, widen=True),
    Check("state-error", _state(failing), 'section[aria-label="Jobs, error"] [role="alert"]',
          ".jobs__table [role='alert']", DESKTOP, widen=True),
    Check("state-hour-chip", _state(hour_chip), 'section[aria-label="Jobs toolbar, hour filter"] > div',
          ".jobs .toolbar", DESKTOP, widen=True),
    Check("state-hour-chip-phone", _state(hour_chip), 'section[aria-label="Jobs filter row, hour filter"] > div',
          ".jobs .toolbar", PHONE, widen=True),
    Check("state-listbox-1440", _state(status_open), '[role="listbox"][aria-label="Provider"]@0', '[role="listbox"]',
          DESKTOP, data=LISTBOX_DATA, data_app=STATUS_WORDS,
          loose={"listbox": "h"}, popup=OPEN_LIST),
    Check("state-listbox-768", _state(status_open), '[role="listbox"][aria-label="Provider"]@1', '[role="listbox"]',
          TABLET, data=LISTBOX_DATA, data_app=STATUS_WORDS,
          loose={"listbox": "h"}, popup=OPEN_LIST),
    Check("state-listbox-390", _state(status_open), '[role="listbox"][aria-label="Provider"]@2', '[role="listbox"]',
          PHONE, data=LISTBOX_DATA, data_app=STATUS_WORDS,
          loose={"listbox": "h"}, popup=OPEN_LIST),
    # the drawn open trigger is the Provider one: its word, so its width and where its value and chevron sit, is data
    Check("state-trigger-open-1440", _state(status_open), 'button[aria-haspopup="listbox"][aria-expanded="true"]@0',
          '[role="combobox"][aria-expanded="true"]', DESKTOP, data=LISTBOX_DATA, loose={"*": "xw"},
          popup=OPEN_LIST),
]
CHECKS = BOARD_CHECKS + STATE_CHECKS

# the N-th element a selector matches (`sel@N`), for the boards that draw one thing several times
PICK_JS = """([sel, n]) => {
  const el = document.querySelectorAll(sel)[n];
  if (!el) return null;
  el.setAttribute('data-element-diff', '1');
  return '[data-element-diff="1"]';
}"""
WIDEN_JS = """([sel, width]) => {
  const el = document.querySelector(sel);
  for (let a = el; a && a.id !== 'dc-root'; a = a.parentElement) {
    a.style.maxWidth = 'none';
    if (getComputedStyle(a.parentElement).display === 'grid') { a.style.gridColumn = '1 / -1'; break; }
    if (getComputedStyle(a.parentElement).display === 'flex') a.style.flex = 'none';
  }
  el.style.boxSizing = 'border-box';
  el.style.width = width + 'px';
  el.style.flex = 'none';
}"""


def _root(page, selector: str | None) -> str | None:
    if not selector or "@" not in selector:
        return selector
    sel, n = selector.rsplit("@", 1)
    got = page.evaluate(PICK_JS, [sel, int(n)], isolated_context=False)
    assert got, "nothing matches %s" % selector
    return got


def _png(page, root: str | None) -> bytes:
    return page.locator(root).first.screenshot(animations="disabled", caret="hide") if root else shoot(page)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_jobs_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, board_h)
    dark = theme == "dark"
    with opened(app, db, live, check.state, dark, width, height) as page:
        app_root = _root(page, check.app_root)
        mine = extract(page, app_root)
        app_png = _png(page, app_root)
        misplaced = popup_place(page, *check.popup) if check.popup else None
    renderer.open(check.state.board, dark, board_w, board_h)
    canvas_root = _root(renderer.page, check.canvas_root)
    if check.widen:
        renderer.page.evaluate(WIDEN_JS, [canvas_root, mine["width"]], isolated_context=False)
        renderer.settle()
    theirs = extract(renderer.page, canvas_root)
    ref_png = _png(renderer.page, canvas_root)
    pixels = compare(diff_page, ref_png, app_png)

    # what an accepted deviation leaves out or adds is set aside before the pairing, so it cannot pair with a neighbour
    theirs["texts"] = [t for t in theirs["texts"] if t["text"] not in check.accepted]
    mine["texts"] = [t for t in mine["texts"] if t["text"] not in check.accepted_app]
    mine["boxes"] = [b for b in mine["boxes"] if b["sig"] not in check.accepted_app]
    res = diff(check.name, theirs, mine, data=check.data, data_app=check.data_app, loose=check.loose)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (check.name, theme)
    notes = ("Canvas %s%s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% anti-aliased."
             % (check.state.board, " " + check.canvas_root if check.canvas_root else "", check.app_root or "page",
                width, height, pixels.percent, pixels.aa_percent))
    if check.popup:
        notes += " Open list against its trigger: %s." % (misplaced or "6 px clear")
    (out / (stem + ".md")).write_text(markdown(res, "%s, %s" % (check.name, theme), notes), encoding="utf-8")
    (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
    print("ELEMENTS %s %s: %d differences, %d canvas only, %d app only, %d data; pixels %.3f %% + %.3f %%" % (
        check.name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), len(res.data),
        pixels.percent, pixels.aa_percent))
    assert res.failures == 0, "%s %s: %d element differences, see %s.md" % (check.name, theme, res.failures, stem)
    assert misplaced is None, misplaced


# ---- data shaped like the live box -------------------------------------------------------------------------------
LONG = "Slow aerial push-in over a misty rice terrace at sunrise, farmers in conical hats, golden haze, 35 mm film"
# the texts that are drawn to end in an ellipsis when they do not fit: title links, the phone title and id line, the
# tablet id line, and the one-line cells of the fixed desktop columns (account, key, model, code)
ELLIPSIS = re.compile(r"title-link|prow__title|prow__ids|jobs__id|jobs__code|jobs__one-line|jobs__model|jobs__kind")


# The live box's shapes (read 17/10, lengths and shapes only): account labels `aNN-NN` (6 characters), key labels
# `aNa-aaaaN` (9), keys without a label named by an 11-character prefix, titles 10 to 30 characters, models 12 to 21.
TITLES = ("Rainy Tokyo crossing", "Cat on a sofa, sunset light", "Logo reveal, purple smoke", "Portrait, soft light",
          "Coastal drone shot at dawn", "Lantern festival")
LONG_KEY = "Partner Collective Southeast Asia, wholesale"
LONG_ACCOUNT = "acct-n11-00007919"
# the live-shaped labels and models: always drawn whole, at every width
LIVE_VALUES = ("d07-12", "d07-31", "d11-04", "k7a-team2", "m2c-shop1", "rk-q7mfz2kd", "seedance_v2.5", "seedream-4.5")


def live_like_seed(db, live):
    """Shaped like the live box: two labelled keys, one named by its prefix, four accounts, short titles. One row of
    each long value (title, key label, account label, model, error code) proves the cells that end in "…". Today:
    1,240 jobs (fillers before 19:00, eight named ones after, every status, failed and refunded ones). Failed and
    refunded jobs on 14, 13, 12 and 9 Oct. The stats then read four-digit totals."""
    keys = {}
    for label in ("k7a-team2", "m2c-shop1", None, LONG_KEY):
        k = new_key(db, wallets=WALLETS)
        db.run("UPDATE api_keys SET label = %s, key_prefix = %s WHERE id = %s", label or "", "rk-q7mfz2kd", k)
        keys[label] = k
    accounts = [new_account(db, label=label, checked=True, provider=PROVIDER)
                for label in ("d07-12", "d07-31", "d11-04", LONG_ACCOUNT)]
    today = datetime(2026, 10, 15, tzinfo=SAIGON)
    db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, "
           "after_sent, submit_timeout_s, wallet_kind, price, created_at, updated_at, finished_at) "
           "SELECT gen_random_uuid(), %s, %s, 'generate', 'video', 'seedance_v2.5', 'p', 'm' || gen_random_uuid(), "
           "'done', 'detach', 'reconcile', 60, 'video', 2, %s::timestamptz + make_interval(secs => g * 20), "
           "%s::timestamptz + make_interval(secs => g * 20), %s::timestamptz + make_interval(secs => g * 20 + 60) "
           "FROM generate_series(1, 1232) g", PROVIDER, keys["k7a-team2"], today, today, today)
    statuses = ("queued", "waiting", "fetching", "unknown", "failed", "done", "failed", "done")
    rows = []
    for i, status in enumerate(statuses):
        at = today + timedelta(hours=20, minutes=54 - i * 6)
        # row 4 (failed) carries every long value; the others are live-shaped
        long_row = i == 4
        key = keys[LONG_KEY if long_row else ("k7a-team2", "m2c-shop1", None)[i % 3]]
        account = None if status == "queued" else accounts[3 if long_row else i % 3]
        title = "%s, take %d" % (LONG, i + 1) if long_row else TITLES[i % len(TITLES)]
        model = "seedance_v2.5-pro-1080p" if long_row else ("seedance_v2.5", "seedream-4.5")[i % 2]
        rows.append((status, at, key, account, title, model))
    for days, status in ((1, "failed"), (2, "failed"), (3, "failed"), (6, "failed")):
        rows.append((status, today + timedelta(days=-days, hours=13), keys[None], accounts[days % 3],
                     TITLES[days % len(TITLES)], "seedance_v2.5"))
    for status, at, key, account, title, model in rows:
        settled = status in ("done", "failed")
        polls = status in ("waiting", "fetching", "unknown")
        db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, "
               "completion, after_sent, submit_timeout_s, wallet_kind, price, title, account_id, created_at, "
               "updated_at, finished_at, error_code, external_ref, next_poll_at) VALUES (gen_random_uuid(), %s, %s, "
               "'generate', 'video', %s, 'p', 'm' || gen_random_uuid(), %s, 'detach', "
               "'reconcile', 60, 'video', 2, %s, %s, %s, %s, %s, %s, "
               + ("gen_random_uuid()::text, clock_timestamp() + interval '30 days')" if polls else "NULL, NULL)"),
               PROVIDER, key, model, status, title, account, at, at, at if settled else None,
               ("dola.content_refused_by_the_safety_review" if title.startswith(LONG) else "dola.content_refused")
               if status == "failed" else None)
    db.run("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) SELECT key_id, id, "
           "provider, 'video', 'charge', price, created_at::date FROM jobs WHERE price > 0")
    db.run("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day, refund_of) "
           "SELECT l.key_id, l.job_id, l.provider, 'video', 'refund', 2, l.billing_day, l.id FROM ledger l "
           "JOIN jobs j ON j.id = l.job_id WHERE j.status = 'failed'")
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) SELECT id, 'output', 0, 'k', "
           "'video/mp4', 1 FROM jobs WHERE status = 'done'")
    db.run("UPDATE key_wallets w SET used_total = (SELECT COALESCE(sum(CASE entry WHEN 'charge' THEN amount "
           "ELSE -amount END), 0) FROM ledger l WHERE l.key_id = w.key_id AND l.provider = w.provider "
           "AND l.kind = w.kind)")
    db.run("UPDATE jobs SET sent_at = created_at WHERE status IN ('unknown', 'waiting', 'fetching')")
    return keys


def failed_only(page, live, data):
    expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)
    if data and data.get("filter"):
        _choose(page, "Status", "Failed")
        expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


LIVE_LIKE = [(width, view) for width in (1440, 768, 390) for view in ("all", "failed")]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("width", "view"), LIVE_LIKE, ids=lambda v: str(v))
def test_jobs_with_live_like_data_fits(app, db, live, run_dir, width, view, theme):
    def seed(db, live):
        live_like_seed(db, live)
        return {"filter": view == "failed"}

    state = Entry("B-Jobs", "#/jobs", seed=seed, now=NOW, drive=failed_only)
    with opened(app, db, live, state, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("live-like-%s-%d-%s.png" % (view, width, theme))).write_bytes(
            page.screenshot(full_page=True, animations="disabled", caret="hide"))
        sideways = page.evaluate("document.documentElement.scrollWidth - innerWidth", isolated_context=False)
        assert sideways <= 0, "the page scrolls sideways by %d px" % sideways
        items = extract(page, ".jobs")
        clipped = [t for t in items["texts"] if t["clipped"] and not ELLIPSIS.search(t.get("cls", ""))]
        assert not clipped, "texts cut off where the canvas draws them whole: %r" % [
            (t["text"], t["cls"], t["rect"], t["full"]) for t in clipped]
        cut = [t["text"] for t in items["texts"]
               if t["clipped"] and set(t["text"].split(" · ")) & set(LIVE_VALUES)]
        assert not cut, "live-shaped values cut off: %r" % cut


# ---- states the canvas does not draw: photographed for the side-by-side with their nearest drawn pattern ------------

def _hover_recheck(page):
    page.locator(".jobs__bulk").get_by_role("button", name="Re-check").hover()


def _hover_row(page):
    page.locator("tbody tr:not(.is-day)").nth(3).hover()


def _focus_status(page):
    page.get_by_role("combobox", name="Status", exact=True).focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(page.get_by_role("combobox", name="Status", exact=True)).to_be_focused()


def _key_open(page):
    page.get_by_role("combobox", name="Key", exact=True).click()
    page.get_by_role("option").nth(2).hover()
    expect(page.get_by_role("listbox")).to_be_visible()


def _size_open(page):
    page.get_by_role("combobox", name="Page size").click()
    expect(page.get_by_role("listbox")).to_be_visible()


def _status_keyboard(page):
    page.get_by_role("combobox", name="Status", exact=True).focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    expect(page.get_by_role("listbox")).to_be_visible()


UNDRAWN = [
    ("hover-recheck", _hover_recheck, True), ("hover-row", _hover_row, True), ("focus-select", _focus_status, False),
    ("key-list-hover", _key_open, False), ("page-size-list", _size_open, False),
    ("status-list-keyboard", _status_keyboard, False),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("state", "act", "ticked"), UNDRAWN, ids=[u[0] for u in UNDRAWN])
def test_jobs_undrawn_states_are_photographed(app, db, live, run_dir, state, act, ticked, theme):
    def drive(page, live, data):
        if ticked:
            tick_the_two(page, live, data)
        act(page)

    entry = Entry("B-Jobs", "#/jobs", seed=jobs_seed, now=NOW, drive=drive, prepare=canvas_band, user=BOARD_USER)
    with opened(app, db, live, entry, theme == "dark", 1440, 1374) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-%s-%s.png" % (state, theme))).write_bytes(shoot(page))
        if page.get_by_role("listbox").count():
            misplaced = popup_place(page, *OPEN_LIST)
            assert misplaced is None, misplaced


# An open list is fixed in the body: it must stay 6 px under its trigger (above it near the bottom of the window) at any
# scroll, and after anything that arrives above the toolbar once the list is open. Viewport photos, never full-page.
LISTS = [("status", "Status"), ("key", "Key"), ("page-size", "Page size")]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("scroll", (0, 300))
@pytest.mark.parametrize(("control", "name"), LISTS, ids=[c for c, _n in LISTS])
def test_an_open_list_sits_by_its_trigger_at_any_scroll(app, db, live, run_dir, control, name, scroll, theme):
    def ready(page, live, data):
        expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)

    entry = Entry("B-Jobs", "#/jobs", seed=jobs_seed, now=NOW, drive=ready, prepare=canvas_band, user=BOARD_USER)
    with opened(app, db, live, entry, theme == "dark", 1440, 900) as page:
        page.evaluate("y => window.scrollTo(0, y)", scroll)
        page.get_by_role("combobox", name=name, exact=True).click()
        expect(page.get_by_role("listbox")).to_be_visible()
        page.wait_for_timeout(300)
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("list-%s-scroll%d-%s.png" % (control, scroll, theme))).write_bytes(shoot(page))
        misplaced = popup_place(page, *OPEN_LIST)
        assert misplaced is None, "at scroll %s: %s" % (page.evaluate("window.scrollY"), misplaced)


def test_the_tool_tells_a_mixed_checkbox_from_an_empty_one(diff_page):
    """A select-all box drawn empty on the canvas and mixed (a dash) in the app is a few pixels of a whole board: the
    tool lists it as a difference of the box itself."""
    diff_page.set_content('<label><input type="checkbox" aria-label="Select all on this page"></label>')
    empty = extract(diff_page)
    diff_page.evaluate("document.querySelector('input').indeterminate = true")
    mixed = extract(diff_page)
    res = diff("mixed-checkbox", empty, mixed)
    assert [(prop, cv, av) for _c, _a, prop, cv, av in res.differences] == [("indeterminate", "false", "true")]
    assert not res.canvas_only and not res.app_only


def test_the_tool_tells_a_list_over_its_trigger_from_one_under_it(diff_page):
    """The open list covering its trigger is invisible to the element tables (the list is not in the trigger's markup):
    popup_place names it; 6 px under or above the trigger passes."""
    diff_page.set_content('<button id="t" style="position:fixed;top:100px;left:40px;height:34px">Status</button>'
                          '<div id="l" style="position:fixed;left:40px;width:180px;height:120px"></div>')
    for top, clear in ((140, True), (-26, True), (60, False), (134, False)):
        diff_page.evaluate("y => document.getElementById('l').style.top = y + 'px'", top)
        assert (popup_place(diff_page, "#l", "#t") is None) is clear, top
