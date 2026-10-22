"""The job drawer boards (B-JobDetail, T-JobDetail, P-JobDetail, B-JobFailed): their three jobs (specs/jobs.md
sections 8 to 11, specs/sample-data.md) planted through the database, and the clicks that open each drawer.

The jobs keep the canvas's ids, titles, times and prompts. Every stored image is one fixed picture, `PICTURE`: a
transparent 2×3 PNG. A tile then shows the app's real loaded thumbnail (the picture fills the tile) while the tile's own
colour shows through it, so only the canvas's placeholder icon and size label differ inside a tile: the element diff
compares the tile's box and leaves its inside as data, and the pixel diff stays meaningful. `picture=False` stores
`BROKEN` instead (the right type, no picture in it): the tile draws its broken placeholder (icon and `1024×1536`), which
is what the boards draw, and is compared whole. The video file is never a playable one: the frame shows its play button
and `0:30` badge over the dark frame, as drawn. Times are absolute (the page shows them at their sample time); the clock
the boards assume is `pixel_seed.NOW`."""

import base64
import json
import uuid

from accountkit import new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_seed import PROVIDER, WALLETS

from relay_core import storage

TIMEOUT_MS = 10_000
ZONE = "+07"

PICTURE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAYAAAC56t6BAAAAC0lEQVR42mNgwAkAABsAAVmYPeoAAAAASUVORK5C"
                           "YII=")
BROKEN = b"not a picture"

IMAGE_DONE = {
    "id": "7f20b6ce-91a4-4f37-b0d2-6c8e5a13f7b4", "title": "Portrait, soft window light", "kind": "image",
    "model": "seedream-5.0-pro", "status": "done", "price": 1, "refunded": 0, "account": "acct-n11-07",
    "created": "2026-10-15 19:40:02", "finished": "2026-10-15 19:41:37", "params": {"ratio": "2:3", "count": 4},
    "prompt": "Head-and-shoulders portrait of a woman by a north-facing window, soft diffused light, muted linen "
              "tones, 85 mm look, natural skin texture.",
    "outputs": [("image/png", 1_275_000, 1024, 1536, None)] * 4, "refs": 0,
    "external_ref": "conv-31ad…90c4", "account_cost": None, "upstream_model": "seedream-5.0-pro", "frame": 2,
    "callback": ("sent", 1, "HTTP 200"),
    "events": [("19:40:02", "queued", "queued for an account"), ("19:40:09", "accepted", "accepted by upstream"),
               ("19:41:37", "done", "4 outputs stored · 5.1 MB"), ("19:41:44", "webhook", "200 (attempt 1)")],
}
IMAGE_FAILED = {
    "id": "5a0f3c88-2d17-4b6e-9c41-e07a83d5b1f2", "title": "Product close-up, white marble", "kind": "image",
    "model": "seedream-4.5", "status": "failed", "price": 1, "refunded": 1, "account": "n11-01",
    "created": "2026-10-15 20:33:48", "finished": "2026-10-15 20:34:35", "params": {"ratio": "2:3", "count": 1},
    "prompt": "Close-up of a perfume bottle on white marble, hard window light, crisp reflections.",
    "outputs": [], "refs": 2, "external_ref": None, "account_cost": None, "upstream_model": "seedream-4.5", "frame": 2,
    "error_code": "dola.content_refused",
    "error": "Dola refused the prompt as content. Dola used no credit, so the key got its 1 point back.",
    "events": [("20:33:48", "queued", "queued for an account"), ("20:33:54", "accepted", "sent to dola on n11-01"),
               ("20:34:35", "failed", "dola.content_refused · the provider refused the content"),
               ("20:34:36", "refund", "1 point returned to Shop A")],
}
VIDEO_DONE = {
    "id": "3b9e1f0a-7c21-4d0e-9a55-2f6b1e8c4d90", "title": "Cat on a sofa, sunset light", "kind": "video",
    "model": "seedance_v2.5", "status": "done", "price": 2, "refunded": 0, "account": "acct-n11-03",
    "created": "2026-10-15 20:41:12", "finished": "2026-10-15 20:45:24",
    "params": {"ratio": "16:9", "duration": 30},
    "prompt": "A ginger cat stretches on a linen sofa while warm sunset light slides across the room. Slow dolly-in, "
              "shallow depth of field, soft film grain.",
    "outputs": [("video/mp4", 18_400_000, 1280, 720, 30.08)], "refs": 0, "external_ref": "conv-7f1c…e2a9",
    "account_cost": 2, "upstream_model": "seedance_v2.5", "frame": 3,
    "events": [("20:41:12", "queued", "queued for an account"), ("20:41:19", "accepted", "accepted by upstream"),
               ("20:45:24", "done", "1 output stored · 18.4 MB"), ("20:45:31", "webhook", "200 (attempt 1)")],
}


# The key every board shows (Shop A), with the prefix P-JobDetail writes beside its label.
KEY_PREFIX = "rk_live_…a1b2"
FRAME_TEXT = "Frame {{client_prompt}} {{tracking_code}}"


def plant_frame(db, frame_id, kind):
    """The kind's default frame as version `frame_id` (the boards write `image v2`, `video v3`: the Frames page tags a
    version by its id), the id sequence moved past it."""
    db.run("INSERT INTO prompt_frames (id, provider, kind, model, text, created_by) VALUES (%s, %s, %s, NULL, %s, "
           "'pixel') ON CONFLICT (id) DO NOTHING", frame_id, PROVIDER, kind, FRAME_TEXT)
    db.run("SELECT setval('prompt_frames_id_seq', (SELECT max(id) FROM prompt_frames))")


def plant_job(db, live, spec, picture=True):
    """One job of the boards with its key (Shop A), account, ledger, media, events, frame version and webhook. Its
    images are `PICTURE`, or `BROKEN` with `picture=False`."""
    image = PICTURE if picture else BROKEN
    key = new_key(db, wallets=WALLETS)
    db.run("UPDATE api_keys SET label = %s, key_prefix = %s WHERE id = %s", spec.get("key_label", "Shop A") or "",
           spec.get("key_prefix", KEY_PREFIX), key)
    account = new_account(db, label=spec["account"], checked=True, provider=PROVIDER) if spec["account"] else None
    stamp = lambda clock: clock and clock + ZONE  # noqa: E731
    # sent to dola from waiting on; polled (far ahead, so no poller of the live server takes it) while it waits
    sent = spec["status"] not in ("queued", "submitting")
    polled = spec["status"] in ("waiting", "fetching", "unknown")
    job = uuid.UUID(spec["id"])
    kind = spec["kind"]
    if spec.get("frame"):
        plant_frame(db, spec["frame"], kind)
    # the frame a job used is fixed at its insert (a trigger keeps it)
    db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, "
           "after_sent, submit_timeout_s, wallet_kind, price, frame_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
           "'queued', 'detach', 'reconcile', 60, %s, %s, %s)",
           job, PROVIDER, key, kind, kind, spec["model"], spec["prompt"], "marker-" + spec["id"][:8], kind,
           spec["price"], spec.get("frame"))
    if spec.get("callback"):
        # a retry still pending has its next attempt far ahead, so no sender of the live server takes it
        status, attempts, last = spec["callback"]
        db.run("UPDATE jobs SET callback_url = 'https://example.test/hook', callback_status = %s, "
               "callback_attempts = %s, callback_last = %s, callback_for = %s::timestamptz, callback_next_at = "
               "CASE WHEN %s THEN clock_timestamp() + interval '30 days' END WHERE id = %s",
               status, attempts, last, stamp(spec["finished"] or spec["created"]), status == "pending", job)
    db.run("UPDATE jobs SET status = %s, title = %s, params = %s::jsonb, account_id = %s, external_ref = %s, "
           "pool = %s, account_cost = COALESCE(%s, 0), taken_at = %s::timestamptz, attempts = %s, claims = %s, "
           "upstream_model = %s, "
           "error_code = %s, error = %s, created_at = %s::timestamptz, sent_at = %s::timestamptz, "
           "finished_at = %s::timestamptz, updated_at = %s::timestamptz, "
           "next_poll_at = CASE WHEN %s THEN clock_timestamp() + interval '30 days' END WHERE id = %s",
           spec["status"], spec["title"], json.dumps(spec["params"]), account, spec["external_ref"],
           kind, spec["account_cost"], stamp(spec["created"]) if account else None, spec.get("attempts", 1),
           spec.get("attempts", 1), spec["upstream_model"], spec.get("error_code"), spec.get("error"),
           stamp(spec["created"]), stamp(spec["created"]) if sent else None, stamp(spec["finished"]),
           stamp(spec["finished"] or spec["created"]), polled, job)
    if spec["status"] == "submitting":         # a job being sent holds its account's job lease
        db.run("INSERT INTO leases (account_id, job_id, purpose, pool, owner, until, hard_until) VALUES (%s, %s, "
               "'job', %s, 'pixel', now() + interval '1 h', now() + interval '1 h')", account, job, kind)
    db.run("UPDATE key_wallets SET day_anchor = current_date WHERE key_id = %s AND provider = %s AND kind = %s",
           key, PROVIDER, kind)
    day = "(SELECT day_anchor FROM key_wallets WHERE key_id = %s AND provider = %s AND kind = %s)"
    charge = db.one("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
                    "VALUES (%s, %s, %s, %s, 'charge', %s, " + day + ") RETURNING id",
                    key, job, PROVIDER, kind, spec["price"], key, PROVIDER, kind)[0]
    if spec["refunded"]:
        db.run("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day, refund_of) "
               "VALUES (%s, %s, %s, %s, 'refund', %s, " + day + ", %s)",
               key, job, PROVIDER, kind, spec["refunded"], key, PROVIDER, kind, charge)
    net = spec["price"] - spec["refunded"]
    db.run("UPDATE key_wallets SET used_total = %s, used_day = %s WHERE key_id = %s AND provider = %s AND kind = %s",
           net, net, key, PROVIDER, kind)
    if spec["account_cost"] and account:
        db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) "
               "VALUES (%s, %s, %s, %s, current_date)", account, job, kind, spec["account_cost"])
    for idx, (mime, size, width, height, seconds) in enumerate(spec["outputs"]):
        name = "%s/output/%d.%s" % (job, idx, mime.split("/")[1])
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, duration_s, "
               "share_token, share_expires_at, upstream_id) VALUES (%s, 'output', %s, %s, %s, %s, %s, %s, %s, %s, "
               "now() + interval '30 days', %s)",
               job, idx, name, mime, size, width, height, seconds, storage.new_share_token(),
               "up-%d" % idx)
        live.call(live.env.store.put_bytes(name, image if mime.startswith("image/") else BROKEN, mime))
    for idx in range(spec["refs"]):
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, share_token, "
               "share_expires_at, upstream_id) VALUES (%s, 'input', %s, %s, 'image/png', 900000, 1024, 1536, %s, "
               "now() + interval '30 days', %s)",
               job, idx, "%s/input/%d.png" % (job, idx), storage.new_share_token(), "in-%d" % idx)
        live.call(live.env.store.put_bytes("%s/input/%d.png" % (job, idx), image, "image/png"))
    day = spec["created"][:10]
    for clock, stage, text in spec["events"]:
        db.run("INSERT INTO job_events (job_id, at, source, stage, text) "
               "VALUES (%s, %s::timestamptz, 'engine', %s, %s)",
               job, "%s %s%s" % (day, clock, ZONE), stage, text)
    return job


def in_flight(status):
    """VIDEO_DONE while it is still `status` (the drawers specs/jobs.md 13 lists as not drawn): nothing delivered or
    finished yet; an account from submitting on; sent to dola (an external ref, the upstream model) from waiting on."""
    sent = status in ("waiting", "fetching", "unknown")
    return {**VIDEO_DONE, "status": status, "outputs": [], "finished": None, "account_cost": None,
            "account": None if status == "queued" else VIDEO_DONE["account"],
            "external_ref": VIDEO_DONE["external_ref"] if sent else None,
            "upstream_model": VIDEO_DONE["upstream_model"] if sent else None,
            "events": VIDEO_DONE["events"][:2 if sent else 1]}


# A done image job with eight results (the live box makes up to four; eight proves the grid's second row).
EIGHT_OUTPUTS = {**IMAGE_DONE, "params": {"ratio": "4:3", "count": 8},
                 "outputs": [("image/png", 9_026_019, 2304, 1728, None)] * 8}


# ---- data shaped like the live box (read 17/10 on the box, read-only, lengths and shapes only) ----------------------
# 66 jobs: prompts 37 to 100,000 characters (median 47, p90 85; words up to 14), three with line breaks; titles up to
# 30; error codes up to 27 (`aaaa.aa_aaaaa_aaaaaa_aaaaaa`), error texts up to 188; outputs 1, 3 or 4 (2304×1728,
# 2496×1664, 2048², 1536×2688); models of 12, 20 and 21 (`aaaaaaaa-N.N-NNa-NN:N`); 3 keys without a label (an
# 11-character prefix); account labels of 6; external refs of 17 digits; events per job up to 280 (p90 88), their
# texts up to 608 characters (p90 464, words up to 36); attempts up to 3; account cost 1 or 2; 10 refunded failed
# jobs; no webhook was ever sent (a retry is planted to show the Callback fact as it would read).
LONG_WORD = "c0nv-2f8a91d4b6e3407c9a5d1e8f2b7c6a03"         # 36 characters, the longest word of an event text


def live_events(start: str, n: int) -> list:
    """`n` upstream resolve events from `start` (HH:MM:SS), 10 s apart, each about 464 characters long."""
    h, m, s = (int(x) for x in start.split(":"))
    out = []
    for i in range(n):
        t = h * 3600 + m * 60 + s + i * 10
        text = ("poll %d of the conversation %s: the upstream answered 200 with the render still in progress, "
                "progress %d %%, queue position %d, the next poll in 10 s; the account stays leased, nothing is "
                "charged again and the job keeps its place in the pool while dola renders the video for this key "
                "and this account, as it did on the poll before this one, which is the usual pace of a render of "
                "this length at this time of the evening" % (i + 1, LONG_WORD, i % 100, n - i))
        out.append(("%02d:%02d:%02d" % (t // 3600, t // 60 % 60, t % 60), "resolve", text))
    return out


LIVE_FAILED = {
    **IMAGE_FAILED, "id": "6c1d4e2a-0b9f-4a37-8e15-3f2a9c7d0e61", "kind": "video",
    "title": "Coastal drone shot at dawn, 4K", "model": "seedance-2.0-15s-16:9", "price": 2, "refunded": 2,
    "account": "d07-12", "key_label": None, "key_prefix": "rk-q7mfz2kd", "params": {"ratio": "16:9", "duration": 15},
    "prompt": " ".join(["sunrise", "terrace", "mist", "farmers", "lanterns", "cinematic", "panorama"] * 2381)[:100_000],
    "refs": 1, "external_ref": "73951846203917452", "account_cost": 2, "upstream_model": "seedance-2.0-15s-16:9",
    "attempts": 3, "error_code": "dola.no_video_output_failed",
    "error": "Dola finished the render without a video in its answer three times in a row, so the job stopped "
             "there. Dola used no credit for it, so the key got its 2 points back; regenerate makes a new job.",
    "events": live_events("20:33:48", 88), "frame": 3,
}
LIVE_DONE = {
    **EIGHT_OUTPUTS, "id": "8e3f5a1c-2d47-4b90-a6c8-1e9f7d2b4a35", "title": "Night market, neon rain, 9:16",
    "model": "seedream-5.0-20k-16:9", "account": "d07-31", "key_label": "k7a-team2",
    "prompt": "Rainy night market seen from a stall, neon signs, steam rising,\nshallow focus, handheld, 35 mm.",
    "external_ref": "40817263951730846", "upstream_model": "seedream-5.0-20k-16:9", "attempts": 3,
    "account_cost": 1, "callback": ("pending", 2, "HTTP 503: Service Unavailable"),
    "events": live_events("19:40:02", 88),
}
# each job's values shaped like live ones (code, model, key name, account, external ref, webhook): drawn, and whole, at
# every width, on one of its tabs
LIVE_DRAWER_VALUES = {
    "failed": ("dola.no_video_output_failed", "seedance-2.0-15s-16:9", "rk-q7mfz2kd", "d07-12", "73951846203917452"),
    "done": ("seedream-5.0-20k-16:9", "k7a-team2", "d07-31", "40817263951730846", "pending · 503 · 2 attempts"),
}


def seed_of(spec, picture=True):
    def seed(db, live):
        return {"job": plant_job(db, live, spec, picture)}
    return seed


# Every tile has drawn what it ends on: its picture (loaded, filling the tile) or its broken placeholder; none loads.
MEDIA_AT_REST_JS = """() => [...document.querySelectorAll('[data-testid=job-drawer] .media__tile')].every((t) => {
  if (t.querySelector('.el-image__wrapper, img.is-loading')) return false;
  const img = t.querySelector('img');
  return !img || (img.complete && img.naturalWidth > 0);
})"""
# a loaded picture covers its whole tile: [tile box, picture box] of every tile whose picture does not
UNFILLED_JS = """() => [...document.querySelectorAll('[data-testid=job-drawer] .media__tile')].flatMap((t) => {
  const img = t.querySelector('img');
  if (!img) return [];
  const a = t.getBoundingClientRect(), b = img.getBoundingClientRect();
  const off = [a.left - b.left, a.top - b.top, a.right - b.right, a.bottom - b.bottom].some((d) => Math.abs(d) > 0.5);
  return off ? [[[a.left, a.top, a.width, a.height], [b.left, b.top, b.width, b.height]]] : [];
})"""


def open_drawer(spec, confirm=False, tab=None):
    """Open the job's drawer from the list by its title, wait for its media to be drawn, then pick `tab` (Timeline,
    Technical); `confirm` also presses Regenerate… (B-JobFailed)."""
    def drive(page, live, data):
        page.get_by_text(spec["title"], exact=True).first.click()
        drawer = page.locator("[data-testid=job-drawer]")
        expect(drawer.locator("[data-section=summary]")).to_be_visible(timeout=TIMEOUT_MS)
        if spec["refs"] or spec["outputs"]:
            expect(drawer.locator("[data-section=results], [data-section=references]").first).to_be_visible()
            page.wait_for_function(MEDIA_AT_REST_JS, timeout=TIMEOUT_MS)
            unfilled = page.evaluate(UNFILLED_JS)
            assert not unfilled, "a thumbnail does not cover its tile ([tile], [picture]): %r" % unfilled
        if tab:
            drawer.get_by_role("tab", name=tab, exact=True).click()
            expect(drawer.get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")
        if confirm:
            drawer.get_by_role("button", name="Regenerate…", exact=True).click()
            expect(page.locator(".el-message-box")).to_be_visible(timeout=TIMEOUT_MS)
    return drive
