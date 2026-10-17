# Pixel harness

Compares the admin app with the design canvas, pixel by pixel. The canvas is rendered by our own renderer; none of the
design tool's runtime is used.

| File | Role |
|---|---|
| `canvas/` | copy of the B-, T- and P- boards (`*.dc.html`) and `canvas.json` (each board's width and height) |
| `dc-mini.js` | the renderer: `{{path}}`, `<sc-for>`, `<sc-if>`, `onClick/onChange/onSubmit`, `DCLogic` with `setState`, `?dark=1` |
| `pixel_render.py` | serves a board from local files only (fonts from the built dist, `/_blob/<id>` logos from `apps/admin-web/src/assets/brand`) and screenshots it at the board's size |
| `pixel_capture.py` | puts the app in the state of an artboard (seed, frozen clock, actions) and photographs it |
| `pixel_manifest.py` | one `Entry` per compared board: route, sign-in, seed, clock, drive, optional region. Lanes add theirs here |
| `pixel_diff.py` | pixelmatch-style diff computed in the browser: mismatch, anti-aliasing apart, worst 64 px cells, diff image |
| `pixel_report.py` | `<run>/<name>-<theme>.{ref,app,diff}.png`, `.json`, and `report.md` / `report.html` (worst first) |
| `test_pixel.py` | the browser tests; every entry's `gate` is enforced (35 % ceiling by default); the gate itself is self-tested |
| `element_diff.py` | element by element: every visible text run and painted box of the canvas paired with the app's (by text, signature, then position) and compared (box edges over 0.5 px, font, colour, background, border, radius, padding, opacity); a markdown table per comparison and a canvas / app / diff PNG side by side |
| `test_element_diff.py` | the Jobs page through `element_diff.py`: the B/T/P-Jobs boards and the Jobs states of B-States (a state card is widened to the app's width first); fails while a table holds a difference that is not live data. Also the Jobs list with data shaped like the live box, at 1440, 768 and 390 |

Run (on the box, narrowed; the images land in `shots/pixel/` of the tree folder, never in git):

```
scripts/box.sh --tree <folder> -- env RELAY_SCREENSHOTS_DIR=shots scripts/check-in-container.sh tests/admin_ui/pixel/test_pixel.py
scripts/box.sh --tree <folder> -- env RELAY_SCREENSHOTS_DIR=shots scripts/check-in-container.sh "tests/admin_ui/pixel/test_pixel.py::test_the_app_matches_the_canvas[B-Login-light]"
```

The run folder is deleted by the next sync into the same tree folder: copy `shots/pixel` away in the same box command.

Adding a screen: add an `Entry` (and its seed and drive functions) for the board; both themes are compared. Every board is
also rendered by `test_every_board_renders_in_both_themes`, which fails on a text path with no value, an undrawn
sc-element, an image or font that did not load, or a request that was not answered from a local file.

## Adding your artboard (lanes)

1. In `pixel_manifest.py`, in a block of your own, add `Entry(board, route, seed=..., now=NOW, drive=..., region=..., label=...)`:
   - `board`: the file name without `.dc.html` (`B-Accounts`). Width and height come from `canvas.json`; the app is
     photographed at that size, light and dark.
   - `route`: the hash route (`#/accounts`). `signed_in=False` for the login page.
   - `seed(db, live)`: plants the board's sample data (tables in `pixel_seed.py`; build from them, never retype a figure).
     It runs before the page opens and may return data for `drive`. `set_setting(db, live, key, value)` stores a setting
     and refreshes the server's cache; `plant_capacity(db, account, pool, tz, left)` writes a capacity row on the pool's
     own day (the server counts the pool day by the DB clock in the pool's time zone, not by the frozen browser clock).
     The live server's provider is named `dola` like the boards (conftest, autouse): give accounts, wallets
     (`WALLETS`) and planted jobs `provider=PROVIDER` from `pixel_seed.py`, not the kits' default `fake`.
   - `drive(page, live, data)`: the clicks and fills that reach the state (a dialog or drawer open, a filter set, an
     error shown). Wait for the state with `expect(...)`, never with a sleep.
   - `prepare(page, live, data)`: runs before the app loads, for what must be in place on the first request (a route
     answered with the board's own figures when no database can hold them, as the Jobs band: `pixel_jobs.py`).
   - `now=NOW`: freezes the browser clock to the boards' Thu 15 Oct 2026 20:55:02 (Asia/Saigon, set on every page). The
     server's SQL clock cannot be frozen: plant rows relative to the real now when the page shows "x min ago".
   - `region=(x, y, w, h)`: compare only part of the viewport; `label` tells two entries of one board apart.
   - `gate`: the largest mismatch + anti-aliasing (% of the viewport) the entry may have. Leave it out
     for the 35 % ceiling (`CEILING_PCT`; a blank or broken page fails); the manager sets the board's real gate at the
     lane's merge. No entry may be looser than the ceiling. The app must also report no console or script error.
2. Add the entry to `ENTRIES`. Two entries of one board need different `label`s.
3. A board whose content is taller than the board fails `test_every_board_renders_in_both_themes`; a full-page PNG of
   the spill is written to `shots/pixel/overflow/`.

Run one board, narrowed, on the box (names are `<board>[-<label>]-<theme>`):

```
scripts/box.sh --tree <folder> -- env RELAY_SCREENSHOTS_DIR=shots scripts/check-in-container.sh "tests/admin_ui/pixel/test_pixel.py::test_the_app_matches_the_canvas[B-Accounts-light]" "tests/admin_ui/pixel/test_pixel.py::test_the_app_matches_the_canvas[B-Accounts-dark]"
```

The element diff of the Jobs page, one comparison or all (the tables and pictures land in `shots/pixel/element-diff/`):

```
scripts/box.sh --tree <folder> -- env RELAY_SCREENSHOTS_DIR=shots scripts/check-in-container.sh tests/admin_ui/pixel/test_element_diff.py
scripts/box.sh --tree <folder> -- env RELAY_SCREENSHOTS_DIR=shots scripts/check-in-container.sh "tests/admin_ui/pixel/test_element_diff.py::test_jobs_matches_the_canvas_element_by_element[B-Jobs-light]"
```

Another page reuses it with its own `Check` list (canvas root, app root, viewport, live data texts).

`check-in-container.sh` takes node ids only (no `-k`). The run prints `PIXEL <name> <theme>: <pct> %`; the images and
`report.md` are in `shots/pixel/` (`<name>-<theme>.{ref,app,diff}.png`; the diff shows matches faded, anti-aliasing
yellow, mismatch red). Copy them away in the same box command, then `scp`.
