"""The job drawer boards (B-JobDetail, T-JobDetail, P-JobDetail, B-JobFailed): their three jobs (specs/jobs.md
sections 8 to 11, specs/sample-data.md) planted through the database, and the clicks that open each drawer.

The jobs keep the canvas's ids, titles, times and prompts. Media rows carry a share link but no stored object, so an
image tile is drawn as its loading/broken placeholder (icon and `1024×1536`), which is what the boards draw; the video
frame shows its play button and `0:30` badge over the dark frame. Times are absolute (the page shows them at their
sample time); the clock the boards assume is `pixel_seed.NOW`."""

import json
import uuid

from accountkit import new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_seed import PROVIDER, WALLETS

from relay_core import storage

TIMEOUT_MS = 10_000
ZONE = "+07"

# Served with the right type but no picture in it: an image tile draws its placeholder (icon and size), as the
# boards do.
PLACEHOLDER = b"not a picture"

IMAGE_DONE = {
    "id": "7f20b6ce-91a4-4f37-b0d2-6c8e5a13f7b4", "title": "Portrait, soft window light", "kind": "image",
    "model": "seedream-5.0-pro", "status": "done", "price": 1, "refunded": 0, "account": "acct-n11-07",
    "created": "2026-10-15 19:40:02", "finished": "2026-10-15 19:41:37", "params": {"ratio": "2:3", "count": 4},
    "prompt": "Head-and-shoulders portrait of a woman by a north-facing window, soft diffused light, muted linen "
              "tones, 85 mm look, natural skin texture.",
    "outputs": [("image/png", 1_275_000, 1024, 1536, None)] * 4, "refs": 0,
    "external_ref": "conv-31ad…90c4", "account_cost": None, "upstream_model": "seedream-5.0-pro",
    "events": [("19:40:02", "queued", "queued for an account"), ("19:40:09", "accepted", "accepted by upstream"),
               ("19:41:37", "done", "4 outputs stored · 5.1 MB"), ("19:41:44", "webhook", "200 (attempt 1)")],
}
IMAGE_FAILED = {
    "id": "5a0f3c88-2d17-4b6e-9c41-e07a83d5b1f2", "title": "Product close-up, white marble", "kind": "image",
    "model": "seedream-4.5", "status": "failed", "price": 1, "refunded": 1, "account": "n11-01",
    "created": "2026-10-15 20:33:48", "finished": "2026-10-15 20:34:35", "params": {"ratio": "2:3", "count": 1},
    "prompt": "Close-up of a perfume bottle on white marble, hard window light, crisp reflections.",
    "outputs": [], "refs": 2, "external_ref": None, "account_cost": None, "upstream_model": "seedream-4.5",
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
    "account_cost": 2, "upstream_model": "seedance_v2.5",
    "events": [("20:41:12", "queued", "queued for an account"), ("20:41:19", "accepted", "accepted by upstream"),
               ("20:45:24", "done", "1 output stored · 18.4 MB"), ("20:45:31", "webhook", "200 (attempt 1)")],
}


def plant_job(db, live, spec):
    """One job of the boards with its key (Shop A), account, ledger, media and events."""
    key = new_key(db, wallets=WALLETS)
    db.run("UPDATE api_keys SET label = 'Shop A' WHERE id = %s", key)
    account = new_account(db, label=spec["account"], checked=True, provider=PROVIDER)
    job = uuid.UUID(spec["id"])
    kind = spec["kind"]
    db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, "
           "after_sent, submit_timeout_s, wallet_kind, price) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', "
           "'detach', 'reconcile', 60, %s, %s)",
           job, PROVIDER, key, kind, kind, spec["model"], spec["prompt"], "marker-" + spec["id"][:8], kind,
           spec["price"])
    db.run("UPDATE jobs SET status = %s, title = %s, params = %s::jsonb, account_id = %s, external_ref = %s, "
           "pool = %s, account_cost = COALESCE(%s, 0), taken_at = %s::timestamptz, attempts = 1, claims = 1, "
           "upstream_model = %s, "
           "error_code = %s, error = %s, created_at = %s::timestamptz, sent_at = %s::timestamptz, "
           "finished_at = %s::timestamptz, updated_at = %s::timestamptz WHERE id = %s",
           spec["status"], spec["title"], json.dumps(spec["params"]), account, spec["external_ref"],
           kind, spec["account_cost"], spec["created"] + ZONE, spec["upstream_model"], spec.get("error_code"),
           spec.get("error"),
           spec["created"] + ZONE, spec["created"] + ZONE, spec["finished"] + ZONE, spec["finished"] + ZONE, job)
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
    if spec["account_cost"]:
        db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) "
               "VALUES (%s, %s, %s, %s, current_date)", account, job, kind, spec["account_cost"])
    for idx, (mime, size, width, height, seconds) in enumerate(spec["outputs"]):
        name = "%s/output/%d.%s" % (job, idx, mime.split("/")[1])
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, duration_s, "
               "share_token, share_expires_at, upstream_id) VALUES (%s, 'output', %s, %s, %s, %s, %s, %s, %s, %s, "
               "now() + interval '30 days', %s)",
               job, idx, name, mime, size, width, height, seconds, storage.new_share_token(),
               "up-%d" % idx)
        live.call(live.env.store.put_bytes(name, PLACEHOLDER, mime))
    for idx in range(spec["refs"]):
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, width, height, share_token, "
               "share_expires_at, upstream_id) VALUES (%s, 'input', %s, %s, 'image/png', 900000, 1024, 1536, %s, "
               "now() + interval '30 days', %s)",
               job, idx, "%s/input/%d.png" % (job, idx), storage.new_share_token(), "in-%d" % idx)
        live.call(live.env.store.put_bytes("%s/input/%d.png" % (job, idx), PLACEHOLDER, "image/png"))
    day = spec["created"][:10]
    for clock, stage, text in spec["events"]:
        db.run("INSERT INTO job_events (job_id, at, source, stage, text) "
               "VALUES (%s, %s::timestamptz, 'engine', %s, %s)",
               job, "%s %s%s" % (day, clock, ZONE), stage, text)
    return job


def seed_of(spec):
    def seed(db, live):
        return {"job": plant_job(db, live, spec)}
    return seed


def open_drawer(spec, confirm=False):
    """Open the job's drawer from the list by its title; `confirm` also presses Regenerate… (B-JobFailed)."""
    def drive(page, live, data):
        page.get_by_text(spec["title"], exact=True).first.click()
        drawer = page.locator("[data-testid=job-drawer]")
        expect(drawer.locator("[data-section=summary]")).to_be_visible(timeout=TIMEOUT_MS)
        if spec["refs"] or spec["outputs"]:
            expect(drawer.locator("[data-section=results], [data-section=references]").first).to_be_visible()
        if confirm:
            drawer.get_by_role("button", name="Regenerate…", exact=True).click()
            expect(page.locator(".el-message-box")).to_be_visible(timeout=TIMEOUT_MS)
    return drive
