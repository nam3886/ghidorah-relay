"""The Request logs page against the canvas, element by element (element_diff.py, the checks of test_element_diff.py).

`test_logs_matches_the_canvas_element_by_element`: the whole B-, T- and P-Logs boards (P-Logs with its More menu open,
as drawn), the request drawer of B-LogDetail (the panel only: the page behind its scrim is a mock), the Clear logs
confirm of B-States, and the error card of B-States, the nearest drawn pattern of the list's own error. Each writes its
table and side-by-side into `<run>/element-diff/` and fails while the table holds a difference that is not live data.

`test_logs_with_live_like_data_fits`: requests shaped like the live box's at 1440, 768 and 390 in both themes: no
sideways scroll, nothing cut but what is drawn to end in "…", no live-shaped value cut, the drawer of the longest row.

`test_logs_undrawn_states_are_photographed`: the states the canvas does not draw (D125), photographed in both themes
for the side-by-side with their nearest drawn pattern."""

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from json import dumps

import pytest
from element_diff import extract, popup_place
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_capture import opened, settle, shoot
from pixel_manifest import ENTRIES
from pixel_seed import PROVIDER, WALLETS
from test_element_diff import DESKTOP, STATES, THEMES, TIMEOUT_MS, Check
from test_element_diff import test_jobs_matches_the_canvas_element_by_element as compare
from test_element_diff_alerts import SELECT_MISMATCH_PCT, failed, run_check

pytestmark = pytest.mark.browser

# the Logs entries of the pixel manifest (pixel_kla.py: the seed, the boards' totals, the filters, the menu, the drawer)
BOARD = {e.name: e for e in ENTRIES}
LOGS = BOARD["B-Logs"]
logs_filters = LOGS.drive
# Live data: yesterday's note. D124: the day header says the day's total under the filters, "4,761 requests shown"; the
# boards write "5 of 4,761 requests shown". The request ids are the database's (rq_8f2174 is a sample id).
BOARD_DATA = ("· 5 of 4,761 requests shown",)
# The phone rows' names carry the request ids (sample ids on the boards). The app names each row's open area, which
# fills the row: "Open request 5".
ROW_NAMES = tuple("Open request rq_" + rid for rid in ("8f21c4", "8f21b9", "8f21a0", "8f2187", "8f2174", "8f215e",
                                                      "8f2149", "8e90d2", "8e90c1", "8e90a8", "8e9091", "8e907a"))
# The request body as stored: the column is jsonb, which keeps its keys in its own order (n, model, ratio, prompt, ...),
# not in the order the client sent them; the drawer shows what is stored.
REQUEST_BODY = ('{ "model": "seedream-4.5", "prompt": "Close-up of a perfume bottle on white marble, hard window '
                'light, crisp reflections.", "n": 1, "ratio": "2:3", "references": 2 }')
MORE_MENU = ('[role="menu"]', 'button[aria-label="More actions"]')


def clear_confirm(page, live, data):
    logs_filters(page, live, data)
    page.get_by_role("button", name="Clear logs").click()
    expect(page.locator(".rl-confirm")).to_be_visible(timeout=TIMEOUT_MS)


def failing(page, live, data):
    """The list answers 502, as the canvas's error card says."""
    page.route(re.compile(r"/admin/logs\?"), lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.reload()
    expect(page.locator("[data-state=error]")).to_be_visible(timeout=TIMEOUT_MS)


# Owner deviation 28: the day fields are Element Plus date pickers, not the browser's date inputs. The picker draws its
# edge as an inset shadow on a wrapper and its text in an input of its own, so to the tool the control is the wrapper:
# its box, radius and edge (read from the shadow, which must be the 1 px line colour the canvas's border is) stand
# in for the input's, and the calendar icon, which the canvas's indicator is not, is set aside. The date text is not
# compared
# (the canvas shows the browser's locale format, the picker `YYYY-MM-DD`; a unit and a browser test hold the text). On a
# phone the picker needs room for its text and icon, so the canvas's chip date is widened to the app's width.
DATE_WRAPPERS_JS = """(root) => {
  const rootEl = root ? document.querySelector(root) : document.documentElement;
  const rr = rootEl.getBoundingClientRect();
  const r1 = (n) => Math.round(n * 10) / 10;
  // the visible part, as the tool lists a box: clipped by every ancestor that hides overflow (a phone's row scrolls)
  const rect = (el) => {
    const b = el.getBoundingClientRect();
    const c = {l: b.left, t: b.top, r: b.right, b: b.bottom};
    for (let a = el.parentElement; a; a = a.parentElement) {
      const as = getComputedStyle(a), q = a.getBoundingClientRect();
      if (as.overflowX !== 'visible') { c.l = Math.max(c.l, q.left); c.r = Math.min(c.r, q.right); }
      if (as.overflowY !== 'visible') { c.t = Math.max(c.t, q.top); c.b = Math.min(c.b, q.bottom); }
    }
    return [r1(c.l - rr.left), r1(c.t - rr.top), r1(c.r - c.l), r1(c.b - c.t)];
  };
  return [...rootEl.querySelectorAll('.el-date-editor')].map((ed) => {
    const w = ed.querySelector('.el-input__wrapper'), s = getComputedStyle(w);
    let under = ed.parentElement;
    const clear = 'rgba(0, 0, 0, 0)';
    while (under.parentElement && getComputedStyle(under).backgroundColor === clear) under = under.parentElement;
    return {editor: rect(ed), rect: rect(w), shadow: s.boxShadow, background: s.backgroundColor,
            under: getComputedStyle(under).backgroundColor,
            radius: ["TopLeft", "TopRight", "BottomRight", "BottomLeft"].map((d) => s["border" + d + "Radius"])
              .join(" ")};
  });
}"""
DATE_CHIP_WIDTH_JS = """(width) => window.innerWidth <= 767 &&
  document.querySelectorAll('label > input[type=date]').forEach((i) => {
  i.style.boxSizing = 'border-box'; i.style.flex = 'none'; i.style.width = width + 'px'; })"""
DATE_CHIP_WIDTH_PX = 116
EDGE = re.compile(r"^(rgb\([^)]*\)) 0px 0px 0px 1px inset$")


def _inside_rect(box, rect) -> bool:
    x, y, w, h = box["rect"]
    rx, ry, rw, rh = rect
    return x >= rx - 0.5 and y >= ry - 0.5 and x + w <= rx + rw + 0.5 and y + h <= ry + rh + 0.5


def date_pickers_as_controls(page, items, root) -> None:
    pickers = sorted(page.evaluate(DATE_WRAPPERS_JS, root, isolated_context=False), key=lambda p: p["rect"][0])
    controls = [b for b in items["boxes"] if b["kind"] == "control" and b["sig"].startswith("input text")]
    # a phone's row of chips scrolls: a picker out of view has no control in the items
    assert len(pickers) == 2 and controls, "the From and To pickers: %r %r" % (pickers, controls)
    items["boxes"] = [b for b in items["boxes"] if b in controls or not any(_inside_rect(b, p["editor"])
                                                                            for p in pickers)]
    for control in controls:
        x, y, w, h = control["full"]
        picker = next(p for p in pickers if p["editor"][0] - 1 <= x + w / 2 <= p["editor"][0] + p["editor"][2] + 1)
        edge = EDGE.match(picker["shadow"])
        color = edge.group(1) if edge else None
        control["rect"] = control["full"] = picker["rect"]
        control["sig"] = "input date "           # the canvas's name for it: a date input with no label
        control["style"] = {**control["style"], "radius": picker["radius"],
                            # the tool lists a fill only where it differs from what lies under the box
                            "background": "none" if picker["background"] in (picker["under"], "rgba(0, 0, 0, 0)")
                            else picker["background"],
                            "border": " / ".join(["1px solid " + color] * 4) if color else "none / none / none / none"}


def _chip_dates(page) -> None:
    page.evaluate(DATE_CHIP_WIDTH_JS, DATE_CHIP_WIDTH_PX, isolated_context=False)


WITH_STATUS_FIELD = ("B-Logs", "T-Logs", "P-Logs")
CHECKS = [
    Check("B-Logs", LOGS, data=BOARD_DATA),
    Check("T-Logs", replace(LOGS, board="T-Logs"), data=BOARD_DATA),
    Check("P-Logs", BOARD["P-Logs"], data=BOARD_DATA + ROW_NAMES),   # its open menu: the menu-open-390 photo
    Check("B-LogDetail", BOARD["B-LogDetail"], 'aside[role="dialog"]',
          '[data-testid="request-drawer"]', data=("Request rq_8f2174", REQUEST_BODY)),
    Check("state-clear-confirm", replace(LOGS, board=STATES, drive=clear_confirm), '[aria-labelledby="cfClr"]',
          ".rl-confirm", DESKTOP),
    Check("state-error", replace(LOGS, board=STATES, drive=failing), 'section[aria-label="Jobs, error"] [role="alert"]',
          ".logs-table [role='alert']", DESKTOP, widen=True,
          data=("Could not load the jobs: the relay answered 502 (bad gateway).",)),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_logs_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    if check.name not in WITH_STATUS_FIELD:
        compare(app, db, live, renderer, diff_page, run_dir, check, theme)
        return
    # The boards draw Status as a native select; the app's is FilterSelect's field: compared as page-alerts compares
    # its fields (the box in the tables, what the field draws inside it as pixels of the select's crop).
    fields = []
    bad = failed(run_check(app, db, live, renderer, diff_page, run_dir, check, theme, fields=fields,
                           app_hook=date_pickers_as_controls, canvas_hook=_chip_dates))
    assert not bad, "%s %s: element differences in %s" % (check.name, theme, ", ".join(bad))
    assert len(fields) == 1, "the Status field against the native select: %r" % fields
    assert fields[0][0] <= SELECT_MISMATCH_PCT, "the Status field draws apart from the native select: %r" % fields


# ---- states the canvas does not draw: photographed for the side-by-side with their nearest drawn pattern ------------

def _held(page):
    """The list answers never: the skeleton stays."""
    page.route(re.compile(r"/admin/logs\?"), lambda route: None)
    page.reload()
    expect(page.locator("[data-state=loading]")).to_be_visible(timeout=TIMEOUT_MS)


def _empty(page):
    page.get_by_label("Search", exact=True).fill("nothing matches this")
    page.get_by_label("Search", exact=True).press("Enter")
    expect(page.locator("[data-state=empty]")).to_be_visible(timeout=TIMEOUT_MS)
    page.evaluate("document.activeElement.blur()")


def _failing(page):
    failing(page, None, None)


def _open_drawer_at(page, n):
    """The details of the n-th request (a desktop or tablet chevron, a phone row)."""
    page.get_by_role("button", name=re.compile(r"^Open (details of )?request")).nth(n).click()
    expect(page.get_by_test_id("request-drawer")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def _open_drawer(page):
    """The 422 of 20:33:48, the drawn one."""
    _open_drawer_at(page, 4)


def _no_answer(page):
    """The 200 of 20:54:22 kept no answer."""
    _open_drawer_at(page, 0)


def _copied(page):
    _open_drawer(page)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.get_by_role("button", name="Copy Request body").click()
    expect(page.get_by_role("button", name="Copy Request body")).to_have_text("Copied")


def _menu(page):
    page.get_by_role("button", name="More actions").click()
    expect(page.get_by_role("menuitem", name="Clear logs")).to_be_visible()
    page.mouse.move(0, 0)


def _status_list(page):
    """The Status field open: the drawn listbox, as on every filter."""
    page.get_by_role("combobox", name="Status", exact=True).click()
    expect(page.get_by_role("listbox")).to_be_visible()
    page.mouse.move(0, 0)


def _cleared(page):
    page.get_by_role("button", name="Clear logs").click()
    page.locator(".rl-confirm").get_by_role("button", name="Clear logs").click()
    expect(page.locator("[data-state=empty]")).to_be_visible(timeout=TIMEOUT_MS)


def _hover_row(page):
    page.locator("tbody tr:not(.is-day)").nth(4).hover()


def _hover_clear(page):
    page.get_by_role("button", name="Clear logs").hover()


def _hover_open(page):
    page.get_by_role("button", name="Open details of request").nth(2).hover()


def _focus_search(page):
    page.get_by_label("Search", exact=True).focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")


def _focus_date(page):
    page.get_by_label("From", exact=True).focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")


def _drawer_hover_copy(page):
    _open_drawer(page)
    page.get_by_role("button", name="Copy Request body").hover()


WIDTHS = {1440: 1128, 768: 1330, 390: 1860}
UNDRAWN = [(name, act, width) for name, act in (("loading", _held), ("no-results", _empty), ("error", _failing),
                                                ("drawer", _open_drawer), ("drawer-no-answer", _no_answer))
           for width in WIDTHS]
UNDRAWN += [("hover-row", _hover_row, 1440), ("hover-clear", _hover_clear, 1440), ("hover-open", _hover_open, 1440),
            ("focus-search", _focus_search, 1440), ("focus-date", _focus_date, 1440),
            ("drawer-hover-copy", _drawer_hover_copy, 1440),
            ("drawer-copied", _copied, 1440), ("cleared", _cleared, 1440), ("menu-open", _menu, 768),
            ("menu-open", _menu, 390), ("status-list", _status_list, 1440), ("status-list", _status_list, 390)]


# ---- data shaped like the live box ---------------------------------------------------------------------------------
# The live box's request logs (read 18/10, read-only, lengths and shapes only): 68 rows over 5 days, at most 61 a day;
# routes `POST /v1/images/generations` and `POST /v1/video/generations` (27 characters at most); 200, 400 and 403;
# error codes `auth.key_disabled` and `input.bad_ratio`; 3 rows with no provider; 5 to 360 ms; key labels `aNa-aaaaN`
# and keys with no label; job titles of 10 to 30 characters (median 18), 2 jobs with no title, 3 rows with no job;
# bodies of up to 186 characters, answers of up to 151.
LONG_TITLE = "Slow aerial push-in over a misty rice terrace at sunrise, farmers in conical hats, golden haze, 35 mm"
LONG_CODE = "dola.content_refused_by_the_safety_review"
LIVE_TITLES = ("Rainy Tokyo crossing", "Cat on a sofa, sunset light", "Logo reveal, purple smoke",
               "Portrait, soft light", "Coastal drone shot at dawn", "Lantern festival")
# the live-shaped values, always whole at every width
LIVE_VALUES = ("k7a-team2", "m2c-shop1", "POST /v1/images/generations", "POST /v1/video/generations",
               "auth.key_disabled", "input.bad_ratio", *LIVE_TITLES)
# texts drawn to end in "…" when they do not fit: the title link, the phone's route, title and time line; a cell of a
# fixed desktop column (Jobs' rule: one line ending in "…") for the outliers only
ELLIPSIS = re.compile(r"l-job-link|p-req__route|p-req__title|p-req__when|l-err|l-route")
LIVE_JOB = ("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, "
            "after_sent, submit_timeout_s, wallet_kind, price, title, finished_at) VALUES (gen_random_uuid(), %s, %s, "
            "'generate', 'image', 'm', 'p', 'm' || gen_random_uuid(), 'done', 'detach', 'reconcile', 60, 'image', 0, "
            "%s, now()) RETURNING id")
LIVE_LOG = ("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, error_code, "
            "body_redacted, response_redacted, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)")


def live_like_seed(db, live):
    """Shaped like the live box: 61 requests today, a few on each of the four days before, two labelled keys and one
    with no label. One row carries every long value (title, error code, duration, body) to prove what ends in "…"."""
    keys = []
    for label in ("k7a-team2", "m2c-shop1", ""):
        k = new_key(db, wallets=WALLETS)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, k)
        keys.append(k)
    today = datetime(2026, 10, 15, 20, 54, tzinfo=timezone(timedelta(hours=7)))
    body = {"model": "seedream-4.5", "prompt": "Close-up of a perfume bottle on white marble, hard window light, crisp "
            "reflections, a soft shadow to the left, 2:3", "duration": 5}
    rows = [(today - timedelta(minutes=13 * i), i) for i in range(61)]
    rows += [(today - timedelta(days=d, hours=i), i) for d in (1, 2, 3, 4) for i in range(2)]
    for at, i in rows:
        key = keys[i % 3]
        if i == 4:      # every long value
            job = db.one(LIVE_JOB, PROVIDER, key, LONG_TITLE)[0]
            db.run(LIVE_LOG, key, PROVIDER, "POST /v1/video/generations", 502, job, 12345, LONG_CODE,
                   dumps({**body, "prompt": LONG_TITLE * 3}), dumps({"error": {"code": LONG_CODE}}), at)
        elif i % 9 == 5:    # refused before a job: no provider
            db.run(LIVE_LOG, key, None, "POST /v1/images/generations", 403, None, 7, "auth.key_disabled",
                   dumps(body), dumps({"error": {"code": "auth.key_disabled"}}), at)
        elif i % 11 == 7:
            db.run(LIVE_LOG, key, PROVIDER, "POST /v1/video/generations", 400, None, 5, "input.bad_ratio",
                   dumps(body), None, at)
        else:
            title = None if i % 17 == 3 else LIVE_TITLES[i % len(LIVE_TITLES)]
            job = db.one(LIVE_JOB, PROVIDER, key, title)[0]
            route = "POST /v1/video/generations" if i % 4 == 1 else "POST /v1/images/generations"
            db.run(LIVE_LOG, key, PROVIDER, route, 200, job, 32 + (i * 37) % 329, None, dumps(body), None, at)
    # a done job has its output (the checker holds every test to it)
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) SELECT id, 'output', 0, 'k', "
           "'image/png', 1 FROM jobs")


def _at_rest(page, live, data):
    expect(page.locator("[data-state=ready]")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
def test_logs_with_live_like_data_fits(app, db, live, run_dir, width, theme):
    state = replace(LOGS, seed=live_like_seed, drive=_at_rest, prepare=None, user=None)
    with opened(app, db, live, state, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("live-like-%d-%s.png" % (width, theme))).write_bytes(
            page.screenshot(full_page=True, animations="disabled", caret="hide"))
        sideways = page.evaluate("document.documentElement.scrollWidth - innerWidth", isolated_context=False)
        assert sideways <= 0, "the page scrolls sideways by %d px" % sideways
        items = extract(page, ".logs-view")
        clipped = [t for t in items["texts"] if t["clipped"] and not ELLIPSIS.search(t.get("cls", ""))]
        assert not clipped, "texts cut off where the canvas draws them whole: %r" % [
            (t["text"], t["cls"], t["rect"], t["full"]) for t in clipped]
        cut = [t["text"] for t in items["texts"] if t["clipped"] and (
            t["text"] in LIVE_VALUES or set(t["text"].split(" · ")) & set(LIVE_VALUES))]
        assert not cut, "live-shaped values cut off: %r" % cut
        # the drawer of the row with every long value: its bodies wrap, nothing in it is cut
        page.get_by_role("button", name=re.compile(r"^Open (details of )?request")).nth(4).click()
        drawer = page.get_by_test_id("request-drawer")
        expect(drawer).to_be_visible(timeout=TIMEOUT_MS)
        page.mouse.move(0, 0)
        settle(page)
        (out / ("live-like-drawer-%d-%s.png" % (width, theme))).write_bytes(shoot(page))
        inside = extract(page, '[data-testid="request-drawer"]')
        cut = [t["text"][:60] for t in inside["texts"] if t["clipped"] and t["tag"] != "h1"]
        assert not cut, "drawer texts cut off: %r" % cut


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("state", "act", "width"), UNDRAWN, ids=["%s-%d" % (u[0], u[2]) for u in UNDRAWN])
def test_logs_undrawn_states_are_photographed(app, db, live, run_dir, state, act, width, theme):
    def drive(page, live, data):
        logs_filters(page, live, data)
        act(page)

    entry = replace(LOGS, drive=drive)
    with opened(app, db, live, entry, theme == "dark", width, WIDTHS[width]) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-%s-%d-%s.png" % (state, width, theme))).write_bytes(shoot(page))
        if page.get_by_role("menu").count():
            assert popup_place(page, *MORE_MENU) is None
        if page.get_by_role("listbox").count():
            assert popup_place(page, '[role="listbox"]', ".lf__status") is None
