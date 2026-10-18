"""Prompt frames, Settings and Test: the sample data of specs/sample-data.md sections 8 to 10 and the actions that reach
the state of each board (B-, T- and P- boards share one seed and one drive per screen).

What the app cannot be made to show, and so stays a known difference of these boards:
- the model display names (`Dreamina Seedance 2.5`): the catalog has an id and no name, the app shows the id;
- the version numbers of the defaults and of the models that follow them: the app prints the database id of a
  version, one sequence for every target (the history of seedance_v2.5 is the board's v2, v3, v4);
- the preview's tracking code (the server's sample marker) and so the preview's length;
- the password rule's number: the board's 12 is sample data, the form holds the server's 10 (design-deviations);
- the job id `e17c55b2` (a new job has a new id). The Test drive moves the job to the boards' step itself (the fake
  worker is not running), and its seed names the models and charges the boards' 2 points."""

import dataclasses
import io
import json
import re

from patchright.sync_api import expect
from pixel_seed import PROVIDER, shell_seed

from relay_core.providers import FrameRules, ModelSpec, check_catalog
from relay_core.testing.fake_provider import FAKE_FRAMES

TIMEOUT_MS = 10_000

# ---- the models of the boards ---------------------------------------------------------------------------------------
VIDEO_MODELS = ("seedance_v2.5", "seedance_v2.0", "seedance_v1.0")
IMAGE_MODELS = ("seedream-4.5", "seedream-5.0-pro")
DURATIONS = (5, 10, 20, 25, 30)
RATIOS = ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9")
TEMPLATES = ("Product close-up", "Cinematic landscape", "Street at night", "Portrait, soft light")


def use_the_boards_models(live, *, frames: bool = True) -> None:
    """The live server's one provider lists the boards' models, takes prompt frames and offers ratios and templates.
    Set on the spec object this test's server owns, so nothing outlives the test."""
    spec = live.env.registry.spec(PROVIDER)
    # the shipped defaults of the fake, without its warning rule (the boards show no warning)
    spec.frames = FrameRules(defaults=FAKE_FRAMES.defaults, warnings=lambda kind, text: (),
                             max_sent_chars=FAKE_FRAMES.max_sent_chars) if frames else None
    spec.catalog = lambda: [
        *(ModelSpec(m, "video", ("generate",), limits={"durations": list(DURATIONS)}, prices={"default": 10})
          for m in VIDEO_MODELS),
        *(ModelSpec(m, "image", ("generate",), prices={"default": 1}) for m in IMAGE_MODELS)]
    original = spec.plan

    def plan(req, settings, snapshots):
        # the fake plans a video when the model id ends in -video: give it the id it knows, then put the board's back
        suffixed = dataclasses.replace(req, model=req.model + ("-video" if req.model in VIDEO_MODELS else "-image"))
        return dataclasses.replace(original(suffixed, settings, snapshots), model=req.model)

    spec.plan = plan
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])    # what resolves a model id
    spec.extras = lambda snapshots: {"ratios": [{"name": r} for r in RATIOS],
                                     "templates": [{"show_value": t, "prompt": t} for t in TEMPLATES]}


DROP_BANNER_JS = "() => document.querySelector('main > [role=status]')?.remove()"


def drop_the_banner(page) -> None:
    """The boards draw the attention banner on Jobs only; the shell still draws it on the other tabs while its lane is
    open, and its height would move every box below it. The banner is the shell's, not this lane's: take it out of the
    photo so the screen is compared where it stands. A shell without the banner finds nothing to remove."""
    page.evaluate(DROP_BANNER_JS, isolated_context=False)


# ---- Prompt frames --------------------------------------------------------------------------------------------------
FRAME_TEXT = ("Create exactly one cinematic video for this request, no on-screen text, no watermark, no follow-up "
              "questions. Request: {{client_prompt}} Reference: {{tracking_code}} Keep the camera steady and the "
              "light natural.")
SAMPLE_PROMPT = "a cat on a sofa"
OLDER = "Make one video of {{client_prompt}} {{tracking_code}}"
# The board's Save is drawn enabled: the editor holds a change. The stored v4 is the frame before it (the board's text
# without its last sentence) and the drive types the board's text over it.
STORED_V4 = FRAME_TEXT.removesuffix(" Keep the camera steady and the light natural.")
INSERT_FRAME = ("INSERT INTO prompt_frames (id, provider, kind, model, text, created_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s::timestamptz, 'admin')")
# A version's tag is its id, one sequence for every target: the history is the board's (v2, v3, v4); the defaults and
# the image model's own frame take ids no board tag uses, so a tag that is data never pairs with an equal one elsewhere.
VIDEO_DEFAULT_ID, IMAGE_DEFAULT_ID, IMAGE_OWN_ID = 7, 8, 9


def frames_seed(db, live):
    """Video: a default that differs from the shipped one; seedance_v2.5 has its own frame (three versions, locked
    14/10 by admin). Image: the shipped default; seedream-5.0-pro has an own frame."""
    data = shell_seed(db, live)
    use_the_boards_models(live)
    shipped_image = FAKE_FRAMES.defaults["image"]
    for row in (
        (VIDEO_DEFAULT_ID, "video", None, "Default video frame {{client_prompt}} {{tracking_code}}",
         "2026-10-08 10:00+07"),
        (2, "video", "seedance_v2.5", OLDER, "2026-10-09 16:22+07"),
        (3, "video", "seedance_v2.5", OLDER + " again", "2026-10-12 11:05+07"),
        (4, "video", "seedance_v2.5", STORED_V4, "2026-10-14 18:40+07"),
        (IMAGE_OWN_ID, "image", "seedream-5.0-pro", OLDER, "2026-10-10 09:00+07"),
        (IMAGE_DEFAULT_ID, "image", None, shipped_image, "2026-10-08 10:00+07"),
    ):
        db.run(INSERT_FRAME, row[0], PROVIDER, row[1], row[2], row[3], row[4])
    # the ids were given: the next saved version takes the one after the highest
    db.run("SELECT setval(pg_get_serial_sequence('prompt_frames', 'id'), (SELECT max(id) FROM prompt_frames))")
    db.run("INSERT INTO prompt_frame_locks (provider, kind, model, locked, created_at, created_by) "
           "VALUES (%s, 'video', 'seedance_v2.5', true, '2026-10-14 18:40+07'::timestamptz, 'admin')", PROVIDER)
    return data


def open_the_boards_frame(page) -> None:
    """The editor of seedance_v2.5, with the board's text typed over the stored one (so Save is on, as drawn)."""
    expect(page.locator("[data-frame-kind=video]")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Edit frame for seedance_v2.5").click()
    box = page.get_by_label("Frame text")
    expect(box).to_have_value(STORED_V4, timeout=TIMEOUT_MS)
    box.fill(FRAME_TEXT)


def frames_drive(page, live, data):
    open_the_boards_frame(page)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_test_id("frame-preview")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)               # no hover left on the button that was clicked
    drop_the_banner(page)


# ---- Settings -------------------------------------------------------------------------------------------------------
# The boards' rows cannot come from the live server: it lists its own registry sorted by key, its boot-only values are
# the deployment's, and the boards' measured prices are not what one set of jobs adds up to (a lowest figure with no
# disagreement). So the page is answered with the boards' rows in the server's shape (the reads only; a write still
# goes to the server).
# (key, description, stored value, source, default); dola.max_wait_s is the edited row: the drive types 5400 over it
BOARD_SETTINGS = (
    ("fake.render_deadline_s", "Render deadline.", 900, "default", 900),
    ("dola.max_wait_s", "Longest wait for one dola render before the job is marked unknown.", 4500, "db", 3600),
    ("dola.poll_interval_s", "Pause between checks of a running render.", 5, "default", 5),
    ("jobs.reaper_interval_s", "How often stuck jobs are looked for.", 60, "env", 120),
    ("jobs.max_attempts", "Tries on another account before a job fails.", 3, "db", 2),
    ("alerts.keep_days", "Days an acknowledged alert row is kept.", 30, "default", 30),
)
EDITED_KEY, EDITED_VALUE = "dola.max_wait_s", "5400"
BOARD_BOOT = (("RELAY_BIND", "0.0.0.0:8100"), ("RELAY_CHROME_PATH", "/opt/chrome-154/chrome"), ("RELAY_WORKERS", "1"),
              ("RELAY_DATA_DIR", "/var/lib/relay"), ("RELAY_LOG_LEVEL", "info"),
              ("RELAY_PROFILE_DIR", "/var/lib/relay/profiles"))
# (model, duration, charged, jobs with a figure, upstream disagreed, lowest upstream, substituted)
BOARD_PRICES = (("seedance_v2.5", 5, 2, 48, 0, 2, 0), ("seedance_v2.5", 30, 2, 17, 2, 1, 3),
                ("seedance_v2.0", 10, 1, 31, 0, 1, 1), ("seedream-4.5", None, 1, 112, 0, 1, 0))
SETTINGS_READ = re.compile(r"/admin/settings$")
PRICES_READ = re.compile(r"/admin/prices(\?.*)?$")
SHORT_PASSWORD = "short-pw!"                # one character under the server's 10: the board draws `short-pass`


def board_settings_body() -> dict:
    return {
        "items": [{"key": k, "type": "int", "value": v, "source": s, "default": d, "description": text,
                   "env_name": k.upper().replace(".", "_"), "read_only": False}
                  for k, text, v, s, d in BOARD_SETTINGS],
        "env_only": [{"name": n, "value": v, "read_only": True} for n, v in BOARD_BOOT],
    }


def board_prices_body() -> dict:
    items = [{"provider": PROVIDER, "model": m, "duration": d, "charged": c, "jobs": n, "disagreed": bad,
              "lowest_upstream": low, "substituted": sub} for m, d, c, n, bad, low, sub in BOARD_PRICES]
    return {"items": items, "disagreed_total": sum(i["disagreed"] for i in items),
            "substituted_total": sum(i["substituted"] for i in items)}


def _answer(body: dict):
    """A read answered with `body`; anything else (a write) goes on to the server."""
    def handle(route):
        if route.request.method != "GET":
            route.fallback()
            return
        route.fulfill(content_type="application/json", body=json.dumps(body))
    return handle


def serve_the_boards_settings(page) -> None:
    """The settings, boot-only values and measured prices of the boards, then the page drawn again with them."""
    page.route(SETTINGS_READ, _answer(board_settings_body()))
    page.route(PRICES_READ, _answer(board_prices_body()))
    page.reload()
    expect(page.get_by_label("Value of " + EDITED_KEY)).to_be_visible(timeout=TIMEOUT_MS)


def settings_seed(db, live):
    return shell_seed(db, live)


def settings_drive(page, live, data):
    """The boards' state: one value typed over its stored one (the row is edited), and the password form refused on
    the new password. The board's rule is sample data (12); the form holds the server's, 10, so a 9-character new
    password reaches the drawn error before anything is sent."""
    serve_the_boards_settings(page)
    page.get_by_label("Value of " + EDITED_KEY).fill(EDITED_VALUE)
    expect(page.get_by_role("button", name="Save " + EDITED_KEY)).to_be_enabled()
    page.get_by_label("Current password").fill("placeholder-secret")
    page.get_by_label("New password").fill(SHORT_PASSWORD)
    page.locator("#password").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#password").get_by_role("alert")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)
    drop_the_banner(page)


# ---- Test -----------------------------------------------------------------------------------------------------------
PROMPT = ("A handheld shot walks through a busy night market in Ho Chi Minh City. Steam rises from street-food "
          "stalls, neon signs reflect on wet pavement, and vendors call out in the background. The camera drifts past "
          "a grill, then settles on a bowl of noodles being handed to a customer. Warm tungsten light, shallow depth "
          "of field, natural ambient sound.")
EXTRA = '{ "seed": 4182, "camera_fixed": false }'
KEY_SHOWN = "rk_live_example_a1b2"
FILES = (("market-stall-reference.jpg", "image/jpeg", int(1.8 * 1048576)),
         ("noodle-bowl-closeup.png", "image/png", int(3.2 * 1048576)))


BOARD_NAMES = {"seedance_v2.5": "Dreamina Seedance 2.5", "seedance_v2.0": "Seedance 2.0 Fast",
               "seedance_v1.0": "Seedance 1.0", "seedream-4.5": "Seedream 4.5", "seedream-5.0-pro": "Seedream 5.0 Pro"}


def name_and_price_the_boards_models(live) -> None:
    """The boards' models with their display names (the catalog sends `name`, as dola's does), and the boards' price of
    a video job, 2 points (the fake charges 10), which the result card's Points row shows."""
    spec = live.env.registry.spec(PROVIDER)
    models = [dataclasses.replace(m, display_name=BOARD_NAMES.get(m.id, "")) for m in spec.catalog()]
    spec.catalog = lambda: models
    boards_plan = spec.plan

    def plan(req, settings, snapshots):
        made = boards_plan(req, settings, snapshots)
        return dataclasses.replace(made, price=2) if made.media_type == "video" else made

    spec.plan = plan
    registry = live.env.registry
    registry._models = check_catalog([registry.spec(n) for n in registry.names()])


def key_page_seed(db, live):
    """A key whose wallets read as the boards' Wallet limits: video 3 / 20 today and 14 / 200 in total, image
    unlimited with 9 used."""
    data = shell_seed(db, live)
    use_the_boards_models(live, frames=False)           # the Test board needs no stored frame to make its job
    name_and_price_the_boards_models(live)
    key = live.call(live.env.new_key())
    day = "(clock_timestamp() AT TIME ZONE (SELECT tz FROM billing_pin))::date"
    db.run("UPDATE key_wallets SET day_limit = 20, total_limit = 200, used_day = 3, used_total = 14, "
           "day_anchor = " + day
           + " WHERE key_id = %s AND provider = %s AND kind = 'video'", key.id, PROVIDER)
    db.run("UPDATE key_wallets SET used_total = 9 WHERE key_id = %s AND provider = %s AND kind = 'image'",
           key.id, PROVIDER)
    # the ledger agrees with the counters (invariant I25): 3 today and 11 earlier for video, 9 earlier for image
    for kind, amount, back in (("video", 3, 0), ("video", 11, 1), ("image", 9, 1)):
        db.run("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) VALUES (%s, %s, %s, 'adjust', "
               "%s, " + day + " - %s)", key.id, PROVIDER, kind, amount, back)
    return {**data, "raw_key": key.raw, "key_id": key.id, "db": db}


# The boards' job: waiting at step 2/4 since 20:52:30, with the length and ratio it was asked for (the fake worker
# never moves it, and the fake plan keeps neither in the job's params, so the drive does).
BOARD_JOB = ("UPDATE jobs SET status = 'waiting', sent_at = %s::timestamptz, created_at = %s::timestamptz, "
             "external_ref = gen_random_uuid()::text, next_poll_at = clock_timestamp() + interval '30 days', "
             "params = params || '{\"duration\": 30, \"ratio\": \"16:9\"}'::jsonb WHERE key_id = %s")
BOARD_JOB_AT = "2026-10-15 20:52:30+07"


def choose(page, field: str, option: str) -> None:
    """Pick an option of one of the Test form's selects (the design's select, drawn by us): open it, click it."""
    box = page.get_by_role("combobox", name=field, exact=True)
    box.click()
    page.get_by_role("listbox").get_by_role("option", name=option, exact=True).click()
    expect(box.locator(".is-current")).to_have_text(option)


def key_page_drive(page, live, data, wait_for: str = "waiting"):
    """The boards' state: the key loaded, seedance_v2.5 picked, the form filled, the job made and shown `wait_for`
    (the boards' waiting job, or "done" when the caller answers the poll itself)."""
    box = page.get_by_role("textbox", name="API key", exact=True)
    box.fill(data["raw_key"])
    page.get_by_role("button", name="Load models").click()
    model = page.get_by_test_id("test-model").filter(has_text="seedance_v2.5")
    expect(model).to_be_visible(timeout=TIMEOUT_MS)
    model.click()
    expect(model).to_have_attribute("aria-pressed", "true")
    page.get_by_label("Prompt", exact=True).fill(PROMPT)
    choose(page, "Duration", "30 s")
    choose(page, "Ratio", "16:9")
    page.get_by_label("Count", exact=True).fill("1")
    page.get_by_label("Title", exact=True).fill("Night market, handheld")
    page.get_by_label("Extra options (JSON)").fill(EXTRA)
    page.get_by_role("button", name="Generate", exact=True).click()
    expect(page.get_by_role("link", name="Open job", exact=False)).to_be_visible(timeout=TIMEOUT_MS)
    if wait_for == "waiting":
        expect(page.get_by_text("Created", exact=True)).to_be_visible(timeout=TIMEOUT_MS * 2)  # the first poll answered
        data["db"].run(BOARD_JOB, BOARD_JOB_AT, BOARD_JOB_AT, data["key_id"])
    result = page.locator('section[aria-labelledby="result-title"]')
    expect(result.locator(".status-chip")).to_contain_text(wait_for, timeout=TIMEOUT_MS)   # the next poll answered
    # attached after the job was made: the request would carry them as data, and the board only shows the list
    page.get_by_test_id("attach-input").set_input_files(files=[
        {"name": name, "mimeType": mime, "buffer": io.BytesIO(b"\0" * size).getvalue()} for name, mime, size in FILES])
    expect(page.get_by_test_id("attachment")).to_have_count(len(FILES))
    drop_the_banner(page)
    box.fill(KEY_SHOWN)
    box.blur()                          # the box shows the boards' example key; the loaded key is kept apart
