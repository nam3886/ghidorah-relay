"""Which app state each canvas board is compared with.

One `Entry` per board: the hash route the app opens, whether the session is signed in, the data to plant first (the
board's sample data, from specs/sample-data.md), the clock the board assumes, and the actions that reach the state (a
dialog open, a drawer open, an error). The board's width and height come from `canvas.json`; the app is photographed at
the same viewport. Every entry is run in light and dark. A board with no entry is rendered (so the renderer is proven
on it) but not compared.

Lanes add their screens' entries here, in their own block, with their own seed and drive functions."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from patchright.sync_api import expect
from pixel_drawer import IMAGE_DONE, IMAGE_FAILED, VIDEO_DONE, open_drawer, seed_of
from pixel_jobs import canvas_band, jobs_seed, tick_the_two
from pixel_lane7 import frames_drive, frames_seed, key_page_drive, key_page_seed, settings_drive, settings_seed
from pixel_seed import BOARD_USER, NOW, shell_seed

TIMEOUT_MS = 10_000
# Every entry starts under this coarse ceiling: a broken or blank page is far above it (the shell and login of drop 1
# are at 2 to 6 %). The manager sets each board's real gate, lower, when its lane merges.
CEILING_PCT = 35.0


@dataclass(frozen=True)
class Entry:
    board: str
    route: str                                  # hash route the app opens, e.g. "#/login"
    signed_in: bool = True
    seed: Callable | None = None                # seed(db, live) -> data: plant the board's sample data
    now: str | None = None                      # frozen clock, e.g. "2026-10-15T14:00:00+07:00"
    drive: Callable | None = None               # drive(page, live, data): reach the state
    prepare: Callable | None = None             # prepare(page, live, data): before the app loads (a route to answer)
    user: str | None = None                     # sign in as this user (the seed makes it), else the test's own
    region: tuple | None = None                 # (x, y, width, height): compare only this part of the viewport
    gate: float = CEILING_PCT                   # most mismatch + anti-aliasing (% of the viewport) the entry may have
    label: str = ""                             # tells two entries of one board apart (a board's shell and its body)

    @property
    def name(self) -> str:
        return self.board + ("-" + self.label if self.label else "")


# ---- login ---------------------------------------------------------------------------------------------------------
# The boards show the form with a wrong password: username "admin", an example password, and the error alert.

def login_error(page, live, data):
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password", exact=True).fill("example-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_be_visible(timeout=TIMEOUT_MS)


LOGIN = [
    Entry("B-Login", "#/login", signed_in=False, drive=login_error),
    Entry("P-Login", "#/login", signed_in=False, drive=login_error),
]

# ---- the shell -----------------------------------------------------------------------------------------------------
# The header and the tab strip of the Jobs boards, cropped to the shell (the board height where the Jobs content
# begins), with the data behind the badges (Jobs 4, Accounts 7/8, Alerts 1) from pixel_seed.py.

def shell_counts(page, live, data):
    """The tab badges arrive with the stats, after the bar itself: a photo taken before them is a bar shifted left."""
    for tab in ("jobs", "accounts", "alerts"):
        expect(page.get_by_test_id("tab-count-" + tab)).to_be_visible(timeout=TIMEOUT_MS)


SHELL = [
    Entry("B-Jobs", "#/jobs", seed=shell_seed, now=NOW, region=(0, 0, 1440, 65), label="shell", user=BOARD_USER,
          drive=shell_counts),
    Entry("T-Jobs", "#/jobs", seed=shell_seed, now=NOW, region=(0, 0, 768, 110), label="shell", user=BOARD_USER,
          drive=shell_counts),
    Entry("P-Jobs", "#/jobs", seed=shell_seed, now=NOW, region=(0, 0, 390, 100), label="shell", user=BOARD_USER,
          drive=shell_counts),
]

# ---- Prompt frames, Settings, Test (lane 7) ------------------------------------------------------------------------
# One seed and one drive per screen (pixel_lane7.py), shared by its desktop, tablet and phone board. The module says
# what the app cannot be made to show of the boards' data (model names, version numbers, the settings rows).

LANE7 = [
    Entry(board, route, seed=seed, now=NOW, drive=drive)
    for route, seed, drive, boards in (
        ("#/frames", frames_seed, frames_drive, ("B-Frames", "T-Frames", "P-Frames")),
        ("#/settings", settings_seed, settings_drive, ("B-Settings", "T-Settings", "P-Settings")),
        ("#/test", key_page_seed, key_page_drive, ("B-Test", "T-Test", "P-Test")),
    )
    for board in boards
]
# The Test boards are signed in as the boards' user, so the top bar reads "admin" as drawn (page-test-login).
LANE7 = [replace(e, user=BOARD_USER) if e.board.endswith("-Test") else e for e in LANE7]

# ---- accounts (lane 5)---------------------------------------------------------------------------------------------
from pixel_accounts import accounts_seed, open_edit, open_import, phone_ready  # noqa: E402

ACCOUNTS_BOARDS = [
    Entry("B-Accounts", "#/accounts", seed=accounts_seed, now=NOW, drive=phone_ready, user=BOARD_USER),
    Entry("T-Accounts", "#/accounts", seed=accounts_seed, now=NOW, drive=phone_ready, user=BOARD_USER),
    Entry("P-Accounts", "#/accounts", seed=accounts_seed, now=NOW, drive=phone_ready, user=BOARD_USER),
    Entry("B-AccountImport", "#/accounts", seed=accounts_seed, now=NOW, drive=open_import, user=BOARD_USER),
    Entry("B-AccountEdit", "#/accounts", seed=accounts_seed, now=NOW, drive=open_edit, user=BOARD_USER),
]

from pixel_kla import KLA  # noqa: E402  (lane 6: keys, request logs, alerts; imports Entry from here)

# ---- jobs ----------------------------------------------------------------------------------------------------------
# The whole page: head, band, banner, list with the failed and the unknown job ticked (pixel_jobs.py: the band's
# columns are the board's own, served to the page, as no database holds both them and the board's page).

JOBS = [Entry(board, "#/jobs", seed=jobs_seed, now=NOW, drive=tick_the_two, prepare=canvas_band, user=BOARD_USER)
        for board in ("B-Jobs", "T-Jobs", "P-Jobs")]

# ---- the job drawer ------------------------------------------------------------------------------------------------
# The drawer panel only (the boards' backdrop is a mock): 620 px at the right edge of the board.

DRAWER = [
    Entry("B-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW, drive=open_drawer(IMAGE_DONE),
          region=(820, 0, 620, 1180)),
    Entry("B-JobFailed", "#/jobs", seed=seed_of(IMAGE_FAILED), now=NOW, drive=open_drawer(IMAGE_FAILED, confirm=True),
          region=(820, 0, 620, 1100)),
    Entry("T-JobDetail", "#/jobs", seed=seed_of(IMAGE_DONE), now=NOW, drive=open_drawer(IMAGE_DONE),
          region=(148, 0, 620, 1280)),
    Entry("P-JobDetail", "#/jobs", seed=seed_of(VIDEO_DONE), now=NOW, drive=open_drawer(VIDEO_DONE)),
]

# Each board holds its own measured gate: the worst of its light and dark mismatch + anti-aliasing on the box after the
# consolidation round (the Jobs boards and their shell: after the Jobs page round; the Accounts boards: after the
# Accounts page round, 18/10), plus 1.0 point (17/10).
# B-KeyForm has no entry and no gate: the key form is a right drawer now, not the board's 640 px dialog, so the board is
# still drawn by the renderer (test_every_board_renders_in_both_themes) but compared by element instead
# (test_element_diff_keys.py: the form body at the drawer's width, with a pixel gate of its own, and the footer).
# The Keys boards were measured again after the copy-prefix buttons went and the wallet columns were re-fitted to the
# canvas without them: T-Keys 4.72 (gate 8.41) and P-Keys 1.93 (gate 7.96) are better and are lowered. B-Keys is 3.07
# (gate 3.07, which was measured 2.07 + 1.0): the board changes on purpose, since the page no longer has the copy
# buttons and draws relative bars for unlimited wallets, and the board still draws the copy buttons and no relative
# bars, so it takes the measured value + 1.0.
# The Frames boards were measured again after the editor became a right drawer the boards do not draw: the page now
# opens with its list across the page and no drawer, so the boards (which draw the editor column) differ more by design
# (B-Frames 2.98, T-Frames 2.61, P-Frames 4.83, worst of light and dark); each takes its measured value + 1.0. The editor
# is compared by element instead (test_element_diff_frames_settings.py: its body at the drawer width with a pixel gate
# of its own, and the footer).
# CEILING_PCT stays only the default of a new entry; a board that gets worse than its gate is a regression, and one
# that gets better should have its gate lowered.
GATES: dict[str, float] = {
    "B-AccountEdit": 4.04,
    "B-AccountImport": 4.45,
    "B-Accounts": 1.02,
    "B-Alerts": 1.17,
    "B-Frames": 3.98,
    "B-JobDetail": 1.40,
    "B-JobFailed": 1.32,
    "B-Jobs": 1.14,
    "B-Jobs-shell": 1.00,
    "B-Keys": 4.08,
    "B-LogDetail": 4.08,
    "B-Login": 1.04,
    "B-Logs": 1.39,
    "B-Settings": 1.08,
    "B-Test": 1.14,
    "P-Accounts": 1.04,
    "P-Alerts": 1.31,
    "P-Frames": 5.83,
    "P-JobDetail": 2.23,
    "P-Jobs": 1.02,
    "P-Jobs-shell": 1.00,
    "P-Keys": 2.93,
    "P-Login": 1.16,
    "P-Logs": 1.23,
    "P-Settings": 1.25,
    "P-Test": 1.30,
    "T-Accounts": 1.03,
    "T-Alerts": 1.07,
    "T-Frames": 3.61,
    "T-JobDetail": 1.37,
    "T-Jobs": 1.23,
    "T-Jobs-shell": 1.00,
    "T-Keys": 5.73,
    "T-Logs": 1.32,
    "T-Settings": 1.06,
    "T-Test": 1.07,
}

ENTRIES: list[Entry] = [
    replace(e, gate=GATES.get(e.name, e.gate))
    for e in [*LOGIN, *SHELL, *LANE7, *ACCOUNTS_BOARDS, *KLA, *JOBS, *DRAWER]
]
