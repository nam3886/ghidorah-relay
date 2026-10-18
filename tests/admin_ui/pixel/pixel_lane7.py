"""Prompt frames, Settings and Test: the sample data of specs/sample-data.md sections 8 to 10 and the actions that reach
the state of each board (B-, T- and P- boards share one seed and one drive per screen).

What the app cannot be made to show, and so stays a known difference of these boards:
- the model display names (`Dreamina Seedance 2.5`): the catalog has an id and no name, the app shows the id;
- the version numbers `v3`, `v4`: the app prints the database id of a version, one sequence for every target;
- the settings rows: the live server's settings are the core ones plus the fake provider's, not the boards' six;
- the boot-only values and the measured-price rows of the live server;
- the job id `e17c55b2` (a new job has a new id), its step (the fake worker is not running) and its Points row."""

import dataclasses
import io

from patchright.sync_api import expect
from pixel_seed import PROVIDER, set_setting, shell_seed

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
INSERT_FRAME = ("INSERT INTO prompt_frames (id, provider, kind, model, text, created_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s::timestamptz, 'admin')")


def frames_seed(db, live):
    """Video: a default that differs from the shipped one; seedance_v2.5 has its own frame (three versions, locked
    14/10 by admin). Image: the shipped default; seedream-5.0-pro has an own frame."""
    data = shell_seed(db, live)
    use_the_boards_models(live)
    shipped_image = FAKE_FRAMES.defaults["image"]
    for row in (
        (1, "video", None, "Default video frame {{client_prompt}} {{tracking_code}}", "2026-10-08 10:00+07"),
        (2, "video", "seedance_v2.5", OLDER, "2026-10-09 16:22+07"),
        (3, "video", "seedance_v2.5", OLDER + " again", "2026-10-12 11:05+07"),
        (4, "video", "seedance_v2.5", FRAME_TEXT, "2026-10-14 18:40+07"),
        (5, "image", "seedream-5.0-pro", OLDER, "2026-10-10 09:00+07"),
        (6, "image", None, shipped_image, "2026-10-08 10:00+07"),
    ):
        db.run(INSERT_FRAME, row[0], PROVIDER, row[1], row[2], row[3], row[4])
    db.run("INSERT INTO prompt_frame_locks (provider, kind, model, locked, created_at, created_by) "
           "VALUES (%s, 'video', 'seedance_v2.5', true, '2026-10-14 18:40+07'::timestamptz, 'admin')", PROVIDER)
    return data


def frames_drive(page, live, data):
    expect(page.locator("[data-frame-kind=video]")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Edit frame for seedance_v2.5").click()
    expect(page.get_by_label("Frame text")).to_have_value(FRAME_TEXT, timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_test_id("frame-preview")).to_be_visible(timeout=TIMEOUT_MS)
    drop_the_banner(page)


# ---- Settings -------------------------------------------------------------------------------------------------------

def settings_seed(db, live):
    """The edited row of the boards: a stored value with another one typed over it."""
    data = shell_seed(db, live)
    use_the_boards_models(live)
    set_setting(db, live, PROVIDER + ".render_deadline_s", 3600)
    return data


def settings_drive(page, live, data):
    box = page.get_by_label("Value of " + PROVIDER + ".render_deadline_s")
    expect(box).to_be_visible(timeout=TIMEOUT_MS)
    box.fill("5400")
    expect(page.get_by_role("button", name="Save " + PROVIDER + ".render_deadline_s")).to_be_enabled()
    password = page.get_by_label("Current password")
    password.fill("placeholder-secret")
    page.get_by_label("New password").fill("short-pass")
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


def key_page_seed(db, live):
    """A key whose wallets read as the boards' Wallet limits: video 3 / 20 today and 14 / 200 in total, image
    unlimited with 9 used."""
    data = shell_seed(db, live)
    use_the_boards_models(live, frames=False)           # the Test board needs no stored frame to make its job
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
    page.get_by_label("Duration", exact=True).select_option("30")
    ratio = page.get_by_label("Ratio", exact=True)
    ratio.select_option("16:9")
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
