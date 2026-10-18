"""The Accounts page against the canvas, element by element (element_diff.py), as test_element_diff.py does for Jobs.

`test_accounts_matches_the_canvas_element_by_element`: the whole B-, T- and P-Accounts boards, the two dialogs
(B-AccountImport, B-AccountEdit: the dialog only, the page behind the scrim is a mock), the page More menu the T- and
P-boards draw open, and the row More menu B-States draws. A state the canvas draws after a click (an open menu) is
clicked on the board too (`canvas_drive`). Each comparison writes `<name>-<theme>.md` and `.side.png` into
`<run>/element-diff/` and fails while its table holds a difference that is not live data."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest
from element_diff import diff, extract, markdown, popup_place, side_by_side
from patchright.sync_api import expect
from pixel_accounts import accounts_seed, open_edit, open_import, phone_ready
from pixel_capture import opened, shoot
from pixel_diff import compare
from pixel_manifest import Entry
from pixel_seed import BOARD_USER, NOW
from test_element_diff import BOARDS, STATES, THEMES, TIMEOUT_MS, WIDEN_JS, Check, _png, _root

pytestmark = pytest.mark.browser


@dataclass(frozen=True)
class Look(Check):
    canvas_drive: Callable | None = None    # canvas_drive(board page): the clicks that reach the drawn state
    popup_gap: float = 6                    # px between the open popup and its trigger, as drawn
    redraw: tuple = ()                      # (board text, redrawn text, deviation #): a canvas redraw still pending


# The board text a ruling has sent back to the designer: the reference is patched to the redrawn value, and every table
# says so. Once the canvas is redrawn the board text is gone, the patch finds nothing and the check fails: drop it then.
REDRAW_JS = """([board, redrawn]) => {
  let n = 0;
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let t = walk.nextNode(); t; t = walk.nextNode()) {
    if (t.data.trim() === board) { t.data = t.data.replace(board, redrawn); n++; }
  }
  return n;
}"""
# design-deviations 20: the pool counts available accounts only (as Video points left), so the dead account is not
# "unknown": the canvas is to be redrawn to 1 account.
POINTS_UNKNOWN = (("2 accounts", "1 account", 20),)

LIST = Entry("B-Accounts", "#/accounts", seed=accounts_seed, now=NOW, drive=phone_ready, user=BOARD_USER)
BOARD_CHECKS = [Look(board, replace(LIST, board=board), redraw=POINTS_UNKNOWN)
                for board in ("B-Accounts", "T-Accounts", "P-Accounts")]

# The dialogs: the board draws the dialog over a mock page; the dialog alone is compared. The rejected line of the
# import result names no credential (design-deviations 5): the canvas's jar token is left out, and the sentence is the
# one for the server's reason where the canvas writes its sample sentence.
DIALOG_CHECKS = [
    Look("B-AccountImport", replace(LIST, board="B-AccountImport", drive=open_import), '[role="dialog"]',
         ".el-dialog.acct-dialog", accepted=("cookie-jar-n11-11 …",), data=("The jar has no session cookie.",),
         data_app=("The line is not a cookie jar.",)),
    Look("B-AccountEdit", replace(LIST, board="B-AccountEdit", drive=open_edit), '[role="dialog"]',
         ".el-dialog.acct-dialog"),
]


# ---- open menus --------------------------------------------------------------------------------------------------
MENU = ('[role="menu"]', '[aria-haspopup="menu"][aria-expanded="true"]')


def _click(name: str) -> Callable:
    def drive(page) -> None:
        page.get_by_role("button", name=name, exact=True).click()
        expect(page.get_by_role("menu")).to_be_visible(timeout=TIMEOUT_MS)
    return drive


def _open_page_menu(name: str) -> Callable:
    def drive(page, live, data) -> None:
        phone_ready(page, live, data)
        _click(name)(page)
    return drive


def open_row_menu(page, live, data) -> None:
    """The row More menu of acct-n11-02, the account B-States draws it for (n11-02)."""
    phone_ready(page, live, data)
    _click("More actions for acct-n11-02")(page)


MENU_CHECKS = [
    Look("menu-page-768", replace(LIST, board="T-Accounts", drive=_open_page_menu(
        "More actions: Check all, Refresh catalog")), '[role="menu"][aria-label="More actions"]', '[role="menu"]',
        popup=MENU, canvas_drive=_click("More actions: Check all, Refresh catalog")),
    Look("menu-page-390", replace(LIST, board="P-Accounts", drive=_open_page_menu("More actions")),
         '[role="menu"][aria-label="More actions"]', '[role="menu"]', popup=MENU, canvas_drive=_click("More actions")),
    # B-States draws the row menu 8 px under the row's More button (top 52 under a 32 px button centred in 56)
    Look("menu-row-1440", replace(LIST, board=STATES, drive=open_row_menu),
         '[role="menu"][aria-label="Account actions"]', '[role="menu"]', viewport=(1440, 1190), popup=MENU,
         popup_gap=8),
]


# ---- the states: the board's own switch, and the list states B-States draws for Jobs and API keys ---------------
# A click on the switch, which lies past the card's edge at 1440 (D123), scrolls the card sideways (and the page): both
# sides are scrolled back before the comparison.
UNSCROLL_JS = ("() => { document.querySelectorAll('*').forEach(e => { if (e.scrollLeft) e.scrollLeft = 0; }); "
               "window.scrollTo(0, 0); }")


def switched_off(page, live, data) -> None:
    """acct-n11-05 switched off with its own switch (the server stores it), as a click on the board's switch draws."""
    phone_ready(page, live, data)
    row = page.locator("tbody tr").nth(5)
    row.get_by_role("switch", name="Enable acct-n11-05").click()
    expect(row.get_by_role("switch", name="Enable acct-n11-05")).to_have_attribute("aria-checked", "false",
                                                                                   timeout=TIMEOUT_MS)
    expect(row).to_contain_text("disabled", timeout=TIMEOUT_MS)
    page.evaluate(UNSCROLL_JS, isolated_context=False)
    page.mouse.move(0, 0)


def switch_off_on_the_board(board) -> None:
    board.get_by_role("switch", name="Enable acct-n11-05").click()
    board.evaluate(UNSCROLL_JS)


# The drawn empty list is the API keys card's: a header row, then the empty area with a hairline on top. The Accounts
# list card has no header row (the boards draw none), so the reference's hairline goes with the header.
def no_header_line(board) -> None:
    board.evaluate("() => { document.querySelector('section[aria-label=\"API keys, empty\"] > div:nth-child(2)')"
                   ".style.borderTop = '0'; }")


def failing(page, live, data) -> None:
    """The list answers 502, as the drawn error card says."""
    page.route(re.compile(r"/admin/accounts\?"), lambda route: route.fulfill(
        status=502, content_type="application/json",
        body='{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}'))
    page.reload()
    expect(page.locator("[data-state=error]")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def no_accounts_seed(db, live):
    from pixel_seed import board_user
    board_user(live)


def empty(page, live, data) -> None:
    expect(page.locator("[data-state=empty]")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


DESKTOP = (1440, 1190)
STATE_CHECKS = [
    Look("state-switched-off", replace(LIST, drive=switched_off), "tbody tr@5", "tbody tr@5",
         canvas_drive=switch_off_on_the_board),
    Look("state-error", replace(LIST, board=STATES, drive=failing), 'section[aria-label="Jobs, error"] [role="alert"]',
         ".list-card [data-state=error]", DESKTOP, widen=True,
         data=("Could not load the jobs: the relay answered 502 (bad gateway).",)),
    # the drawn empty list is the API keys' one: its words are that page's; centred, so their places are too
    Look("state-empty", replace(LIST, board=STATES, seed=no_accounts_seed, drive=empty),
         'section[aria-label="API keys, empty"] > div@1', ".list-card [data-state=empty]", DESKTOP, widen=True,
         data=("No API keys yet.", "New key"), loose={"": "xw"}, canvas_drive=no_header_line),
]
CHECKS = BOARD_CHECKS + DIALOG_CHECKS + MENU_CHECKS + STATE_CHECKS


def shape_differences(res) -> list:
    """Two icons paired by their place are compared by size and colour only: a ⋮ drawn where the canvas has a ⋯ passes.
    Their shapes (the svg's signature) must be equal too."""
    return [(c, a, "shape", c["sig"], a["sig"]) for c, a, _how in res.pairs
            if c["kind"] == "icon" and c["sig"] != a["sig"]]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.name)
def test_accounts_matches_the_canvas_element_by_element(app, db, live, renderer, diff_page, run_dir, check, theme):
    board_w, board_h = BOARDS[check.state.board]
    width, height = check.viewport or (board_w, board_h)
    dark = theme == "dark"
    with opened(app, db, live, check.state, dark, width, height) as page:
        app_root = _root(page, check.app_root)
        mine = extract(page, app_root)
        app_png = _png(page, app_root)
        misplaced = popup_place(page, *check.popup, gap=check.popup_gap) if check.popup else None
    renderer.open(check.state.board, dark, board_w, board_h)
    if check.canvas_drive:
        check.canvas_drive(renderer.page)
        renderer.settle()
    for board_text, redrawn, row in check.redraw:
        found = renderer.page.evaluate(REDRAW_JS, [board_text, redrawn], isolated_context=False)
        assert found, "%r is gone from %s: the canvas was redrawn, drop the patch (design-deviations %d)" % (
            board_text, check.state.board, row)
        renderer.settle()
    canvas_root = _root(renderer.page, check.canvas_root)
    if check.widen:
        renderer.page.evaluate(WIDEN_JS, [canvas_root, mine["width"]], isolated_context=False)
        renderer.settle()
    theirs = extract(renderer.page, canvas_root)
    ref_png = _png(renderer.page, canvas_root)
    pixels = compare(diff_page, ref_png, app_png)

    theirs["texts"] = [t for t in theirs["texts"] if t["text"] not in check.accepted]
    mine["texts"] = [t for t in mine["texts"] if t["text"] not in check.accepted_app]
    mine["boxes"] = [b for b in mine["boxes"] if b["sig"] not in check.accepted_app]
    res = diff(check.name, theirs, mine, data=check.data, data_app=check.data_app, loose=check.loose)
    res.differences += shape_differences(res)
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (check.name, theme)
    notes = ("Canvas %s%s, app %s at %dx%d. Pixel diff of the compared part: %.3f %% mismatch + %.3f %% anti-aliased."
             % (check.state.board, " " + check.canvas_root if check.canvas_root else "", check.app_root or "page",
                width, height, pixels.percent, pixels.aa_percent))
    if check.popup:
        notes += " Open menu against its trigger: %s." % (misplaced or "%g px clear" % check.popup_gap)
    for board_text, redrawn, row in check.redraw:
        notes += (" **Pending canvas redraw** (design-deviations %d): the reference shows `%s` where the board still "
                  "writes `%s`." % (row, redrawn, board_text))
    (out / (stem + ".md")).write_text(markdown(res, "%s, %s" % (check.name, theme), notes), encoding="utf-8")
    (out / (stem + ".side.png")).write_bytes(side_by_side(diff_page, ref_png, app_png, pixels.png))
    print("ELEMENTS %s %s: %d differences, %d canvas only, %d app only, %d data; pixels %.3f %% + %.3f %%" % (
        check.name, theme, len(res.differences), len(res.canvas_only), len(res.app_only), len(res.data),
        pixels.percent, pixels.aa_percent))
    assert res.failures == 0, "%s %s: %d element differences, see %s.md" % (check.name, theme, res.failures, stem)
    assert misplaced is None, misplaced


# ---- states the canvas does not draw: photographed, each in the style of its nearest drawn pattern -----------------
def _held(pattern: str) -> Callable:
    """A request that is never answered: what it waits for stays on screen."""
    def act(page) -> None:
        page.route(re.compile(pattern), lambda route: None)
    return act


def _answer(pattern: str, status: int, body: str) -> Callable:
    def act(page) -> None:
        page.route(re.compile(pattern), lambda route: route.fulfill(status=status, content_type="application/json",
                                                                   body=body))
    return act


COMMAND_FAILED = ('{"id": "c1", "kind": "check", "status": "failed", "result": {"error": "connect timeout after 8 s"},'
                  ' "created_at_ms": 0, "finished_at_ms": 0}')


def _row(page, label: str):
    return page.locator("tbody tr, li").filter(has_text=label).first


def _loading(page):
    _held(r"/admin/accounts\?")(page)
    page.reload()
    expect(page.locator("[data-state=loading]")).to_be_visible(timeout=TIMEOUT_MS)


def _pool_failed(page):
    _answer(r"/admin/stats/pool", 502, '{"error": {"code": "upstream.bad_gateway", "message": "bad gateway"}}')(page)
    page.reload()
    expect(page.get_by_test_id("pool-error")).to_be_visible(timeout=TIMEOUT_MS)


def _check_running(page):
    _held(r"/admin/commands/")(page)
    _row(page, "acct-n11-05").get_by_role("button", name="Check acct-n11-05").click()
    expect(_row(page, "acct-n11-05").get_by_role("button", name="Check acct-n11-05")).to_have_attribute(
        "aria-disabled", "true", timeout=TIMEOUT_MS)


def _check_failed(page):
    _answer(r"/admin/commands/", 200, COMMAND_FAILED)(page)
    _row(page, "acct-n11-05").get_by_role("button", name="Check acct-n11-05").click()
    expect(page.locator(".el-message").filter(has_text="failed")).to_be_visible(timeout=TIMEOUT_MS)


def _saved(page):
    _row(page, "acct-n11-05").get_by_role("button", name="More actions for acct-n11-05").click()
    page.get_by_role("menuitem", name="Edit", exact=True).click()
    page.get_by_role("textbox", name="Label").fill("acct-n11-05b")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".el-message").filter(has_text="Saved")).to_be_visible(timeout=TIMEOUT_MS)


def _delete_confirm(page):
    _row(page, "acct-n11-05").get_by_role("button", name="More actions for acct-n11-05").click()
    page.get_by_role("menuitem", name="Delete", exact=True).click()
    expect(page.locator(".el-message-box")).to_be_visible(timeout=TIMEOUT_MS)


def _row_menu(page):
    _row(page, "acct-n11-02").get_by_role("button", name="More actions for acct-n11-02").click()
    expect(page.get_by_role("menu")).to_be_visible(timeout=TIMEOUT_MS)


def _edit(page):
    _row(page, "acct-n11-05").get_by_role("button", name="More actions for acct-n11-05").click()
    page.get_by_role("menuitem", name="Edit", exact=True).click()
    expect(page.get_by_role("dialog", name=re.compile(r"^Edit account"))).to_be_visible(timeout=TIMEOUT_MS)


def _import(page):
    page.get_by_role("button", name="Import", exact=True).click()
    expect(page.get_by_role("dialog", name="Import accounts")).to_be_visible(timeout=TIMEOUT_MS)


def _hover_row(page):
    _row(page, "acct-n11-03").hover()


def _hover_check(page):
    _row(page, "acct-n11-03").get_by_role("button", name="Check acct-n11-03").hover()


def _focus_switch(page):
    switch = _row(page, "acct-n11-03").get_by_role("switch")
    switch.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(switch).to_be_focused()


UNDRAWN = [
    ("loading", _loading, (1440, 768, 390)), ("pool-failed", _pool_failed, (1440,)),
    ("check-running", _check_running, (1440, 768)), ("check-failed", _check_failed, (1440,)),
    ("saved", _saved, (1440,)), ("delete-confirm", _delete_confirm, (1440, 390)),
    ("row-menu", _row_menu, (768, 390)), ("edit", _edit, (768, 390)), ("import", _import, (390,)),
    ("hover-row", _hover_row, (1440,)), ("hover-check", _hover_check, (1440,)),
    ("focus-switch", _focus_switch, (1440,)),
]
UNDRAWN_RUNS = [(name, act, width) for name, act, widths in UNDRAWN for width in widths]
HEIGHTS = {1440: 1190, 768: 1024, 390: 844}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("state", "act", "width"), UNDRAWN_RUNS, ids=["%s-%d" % (s, w) for s, _a, w in UNDRAWN_RUNS])
def test_accounts_undrawn_states_are_photographed(app, db, live, run_dir, state, act, width, theme):
    def drive(page, live, data):
        phone_ready(page, live, data)
        act(page)

    entry = replace(LIST, drive=drive)
    with opened(app, db, live, entry, theme == "dark", width, HEIGHTS[width]) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("undrawn-%s-%d-%s.png" % (state, width, theme))).write_bytes(shoot(page))
        if page.get_by_role("menu").count():
            gap = 8 if width >= 768 and page.locator(".menu-pop--row").count() else 6
            misplaced = popup_place(page, *MENU, gap=gap)
            assert misplaced is None, misplaced


# ---- the command chips (B-States "Command status", the manager's ruling): running, ok and error at each width ------
# A command's POST is answered with an id that says how it ends; its poll then never answers (running), says done at
# 20:55:07 (the board's chip) or failed with the board's reason. The worker never runs here: only the page is shown.
ROW_STATES = {"acct-n11-03": "running", "acct-n11-05": "ok", "acct-n11-06": "error"}
FINISHED_MS = 1792072507000           # 2026-10-15 20:55:07 in Saigon, the time the drawn ok chip shows
COMMAND_POSTS = re.compile(r"/admin/(accounts/[^/]+/(check|proxy-recheck)"
                           r"|providers/[^/]+/(check-all|refresh-catalog))$")
COMMAND_ANSWERS = {
    "ok": {"status": "done", "result": {}, "finished_ms": FINISHED_MS},
    "error": {"status": "failed", "result": {"error": "connect timeout after 8 s"}, "finished_ms": FINISHED_MS},
}


def chips_seed(db, live):
    data = accounts_seed(db, live)
    data["ids"] = {label: str(account) for label, account in db.all("SELECT label, id FROM accounts")}
    return data


def _answer_commands(page, data, header: dict) -> None:
    labels = {account: label for label, account in data["ids"].items()}

    def post(route):
        url = route.request.url
        found = re.search(r"/admin/accounts/([^/]+)/(?:check|proxy-recheck)$", url)
        state = ROW_STATES.get(labels.get(found.group(1), ""), "ok") if found else header[url.rsplit("/", 1)[-1]]
        route.fulfill(status=202, content_type="application/json", body=json.dumps({"command_id": "cmd-" + state}))

    def poll(route):
        state = route.request.url.rsplit("cmd-", 1)[-1]
        if state in COMMAND_ANSWERS:          # a running command is never answered
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "id": "cmd-" + state, "provider": "dola", "kind": "check", "account_id": None, "job_id": None,
                "attempts": 1, "created_ms": FINISHED_MS, **COMMAND_ANSWERS[state]}))

    page.route(COMMAND_POSTS, post)
    page.route(re.compile(r"/admin/commands/cmd-"), poll)


def _page_command(page, name: str, width: int) -> None:
    if width >= 1280:
        page.get_by_role("button", name=name, exact=True).click()
    else:
        page.get_by_role("button", name=re.compile(r"^More actions(:|$)")).click()
        page.get_by_role("menuitem", name=name, exact=True).click()


def _row_check(page, label: str, width: int) -> None:
    if width >= 768:
        page.get_by_role("button", name="Check " + label, exact=True).click()
    else:
        page.get_by_role("button", name="More actions for " + label, exact=True).click()
        page.get_by_role("menuitem", name="Check", exact=True).click()


def _photo(page, run_dir, name: str, full: bool = False) -> None:
    out = run_dir / "element-diff"
    out.mkdir(parents=True, exist_ok=True)
    page.mouse.move(0, 0)
    page.evaluate("() => document.querySelectorAll('.acct-scroll').forEach(e => e.scrollLeft = e.scrollWidth)",
                  isolated_context=False)
    (out / (name + ".png")).write_bytes(
        page.screenshot(full_page=full, animations="disabled", caret="hide") if full else shoot(page))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
def test_accounts_command_chips_are_photographed(app, db, live, run_dir, width, theme):
    """Rows: running, ok and error at once; the page commands: Check all running and Refresh catalog failed, then
    (read again) Refresh catalog ok; Check proxy in the Edit dialog: running, ok, error."""
    height = HEIGHTS[width]
    header = {"check-all": "running", "refresh-catalog": "error"}

    def drive(page, live, data):
        phone_ready(page, live, data)
        _answer_commands(page, data, header)
        for label in ROW_STATES:
            _row_check(page, label, width)
        _page_command(page, "Check all", width)
        _page_command(page, "Refresh catalog", width)
        for state in ("running", "ok", "error"):
            expect(page.locator("tbody td.last, li").locator("[data-state=%s]" % state)).to_have_count(1)
        expect(page.locator(".head [data-state=error]")).to_be_visible()

    entry = replace(LIST, seed=chips_seed, drive=drive)
    tag = "%d-%s" % (width, theme)
    with opened(app, db, live, entry, theme == "dark", width, height) as page:
        _photo(page, run_dir, "chips-rows-%s" % tag, full=True)
        _photo(page, run_dir, "chips-head-running-error-%s" % tag)
        page.reload()
        expect(page.get_by_text("acct-n11-05")).to_be_visible(timeout=TIMEOUT_MS)
        header["refresh-catalog"] = "ok"
        _page_command(page, "Refresh catalog", width)
        expect(page.locator(".head [data-state=ok]")).to_be_visible()
        _photo(page, run_dir, "chips-head-ok-%s" % tag)
        for label, state in ROW_STATES.items():
            row = page.locator("tbody tr, li").filter(has_text=label).first
            row.get_by_role("button", name="More actions for " + label, exact=True).click()
            page.get_by_role("menuitem", name="Edit", exact=True).click()
            dialog = page.get_by_role("dialog", name=re.compile(r"^Edit account"))
            dialog.get_by_role("button", name="Check proxy").click()
            expect(dialog.locator("[data-state=%s]" % state)).to_be_visible(timeout=TIMEOUT_MS)
            _photo(page, run_dir, "chips-proxy-%s-%s" % (state, tag))
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0, timeout=TIMEOUT_MS)


# ---- data shaped like the live box ---------------------------------------------------------------------------------
# The live box's accounts (read 17/10, lengths and shapes only): 9 available, labels `aNN-NN`, names of 5 to 20
# characters, a 3-letter composer, every account behind a proxy (host:port up to 19 characters, with a password), and
# two pools each: video (0 to 4 left of 4) and image (33 to 39 counted). Every row here is live-shaped but one, which
# carries every long value (label, name, reason, cooldown reason, error) to prove the cells that end in "…".
LIVE_NAMES = ("Trang", "Minh Tuan", "Lan Nguyen", "Hoang Phuc", "Duc Anh Tran", "Nguyen Thi Mai", "Pham Quoc Bao Long",
              "Tran Hoang Minh Duc")
LIVE_LABELS = tuple("d%02d-%02d" % (7 + n // 3, 11 + n) for n in range(len(LIVE_NAMES)))
LONG_LABEL, LONG_NAME = "acct-n11-00007919", "Nguyen Thi Thanh Huong Collective"
LONG_ERROR = "dola.content_refused_by_the_safety_review: the page said the account must verify its phone number first"
# a text may be cut only where the canvas draws an ellipsis: the Last error cell
ELLIPSIS = re.compile(r"\berr\b")


def live_like_accounts_seed(db, live):
    from accountkit import done_job, new_account
    from jobkit import key as new_key
    from pixel_seed import PROVIDER, WALLETS, board_user, plant_capacity, set_setting
    board_user(live)
    set_setting(db, live, "dola.video_budget", 4)
    key = new_key(db, wallets=WALLETS)
    rows = [(label, name, "available") for label, name in zip(LIVE_LABELS, LIVE_NAMES, strict=True)]
    rows.insert(4, (LONG_LABEL, LONG_NAME, "dead"))
    for n, (label, name, state) in enumerate(rows):
        account = new_account(db, label=label, state=state, checked=True, provider=PROVIDER)
        db.run("UPDATE accounts SET display_name = %s, provider_state = '{\"composer\": \"new\"}'::jsonb, "
               "proxy_host = '103.152.112.14', proxy_port = 8080, proxy_auth_enc = '\\x01', proxy_ok = true, "
               "proxy_checked_at = %s::timestamptz - interval '3 min', "
               "last_checked_at = %s::timestamptz - make_interval(mins => %s), "
               "created_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
               name, NOW, NOW, n + 1, n + 1, account)
        plant_capacity(db, account, "video", "UTC", n % 5)
        plant_capacity(db, account, "image", "UTC", 33 + n % 7)
        db.run("UPDATE account_capacity SET source = 'counted' WHERE account_id = %s AND pool = 'image'", account)
        for m in range(n % 8):
            done_job(db, key, account, ref="%s-%d" % (label, m))
        if label == LONG_LABEL:
            db.run("UPDATE accounts SET state_reason = 'account.dead_session_after_password_change', "
                   "last_error = %s, strikes = 2, cooldown_until = clock_timestamp() + interval '2 h', "
                   "cooldown_reason = 'dola.likeness_refused_by_the_review' WHERE id = %s", LONG_ERROR, account)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width", (1440, 768, 390))
def test_accounts_with_live_like_data_fits(app, db, live, run_dir, width, theme):
    def ready(page, live, data):
        expect(page.get_by_text(LIVE_LABELS[-1], exact=True)).to_be_visible(timeout=TIMEOUT_MS)
        page.mouse.move(0, 0)

    entry = replace(LIST, seed=live_like_accounts_seed, drive=ready)
    with opened(app, db, live, entry, theme == "dark", width, 900) as page:
        out = run_dir / "element-diff"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("live-like-%d-%s.png" % (width, theme))).write_bytes(
            page.screenshot(full_page=True, animations="disabled", caret="hide"))
        sideways = page.evaluate("document.documentElement.scrollWidth - innerWidth", isolated_context=False)
        assert sideways <= 0, "the page scrolls sideways by %d px" % sideways
        # the desktop card scrolls sideways by design (D123): what lies past its edge is reached by scrolling, so the
        # card is opened up before the check; a text cut inside its own cell is still cut
        page.evaluate("document.querySelectorAll('.acct-scroll, .list-card')"
                      ".forEach(e => e.style.overflow = 'visible')", isolated_context=False)
        items = extract(page, ".accounts-view")
        clipped = [t for t in items["texts"] if t["clipped"] and not ELLIPSIS.search(t.get("cls", ""))]
        assert not clipped, "texts cut off where the canvas draws them whole: %r" % [
            (t["text"], t["cls"], t["rect"], t["full"]) for t in clipped]
        live_values = set(LIVE_LABELS) | set(LIVE_NAMES)
        cut = [t["text"] for t in items["texts"] if t["clipped"] and set(t["text"].split(" · ")) & live_values]
        assert not cut, "live-shaped values cut off: %r" % cut


def test_the_tool_tells_one_dots_icon_from_the_other(diff_page):
    """A ⋯ drawn where the canvas has a ⋮, in the same place: the shapes differ and nothing else does."""
    svg = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">'
           '<path d="%s"/></svg>')
    diff_page.set_content(svg % "M12 5h.01M12 12h.01M12 19h.01")
    canvas = extract(diff_page)
    diff_page.set_content(svg % "M5 12h.01M12 12h.01M19 12h.01")
    res = diff("dots", canvas, extract(diff_page))
    assert not res.differences and not res.canvas_only and not res.app_only
    assert [prop for _c, _a, prop, _cv, _av in shape_differences(res)] == ["shape"]

