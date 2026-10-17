"""The Jobs boards (B-, T-, P-Jobs): the sample data of specs/sample-data.md section 2 planted in the database, the
board's 24 band columns served to the page, and the two ticked rows. Built from the tables of `pixel_seed.py`; the
figures below are the ones the boards show.

The board's sample data cannot all live in one database: its band puts 100+ jobs on 15 Oct before 19:00, yet its
page shows two jobs of yesterday as rows 9 and 10, which would then be newer than none of those. The list, the day
totals and the ledger are the database's (planted as the page shows them: rows 9 and 10 on 14 Oct, 212 jobs that day);
the band's columns come from the board itself (`canvas_band`, the hourly route answered with them).

What the database still says otherwise than the canvas (live data, see the element-diff report):
- the list total is 1,325 here (done 1,284 + failed 37 + open 4), the canvas says 1,321;
- Videos made / Images made are counted from done jobs (335 / 120), the canvas sums account counters."""

import json
import re
from datetime import datetime, timedelta, timezone

from accountkit import new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_seed import ACCOUNTS, IN_USE_LEASE, KEYS, PROVIDER, WALLETS, board_user, set_setting

from relay_core import alerts

ALERT_TEXT = "Sorry, I can’t create that video"
SAIGON = timezone(timedelta(hours=7))

# (id prefix, day of October, clock in Asia/Saigon, title, kind, model, status, account, key, outputs, refs, price,
#  refunded, error code, model substituted): rows 1-10 of the sample page. The Jobs boards write an account without
#  its `acct-` prefix (n11-02), so these accounts carry the board's label.
NAMED = (
    ("2c6b8e90", 15, "20:54:22", None, "image", "seedream-4.5", "queued", None, "Partner C", 0, 0, 0, 0, None, False),
    ("e17c55b2", 15, "20:52:30", "Street food night market, handheld", "video", "seedance_v2.5", "waiting",
     "n11-02", "Shop A", 0, 0, 2, 0, None, False),
    ("9c2d7e41", 15, "20:49:03", "Coastal drone shot at dawn", "video", "seedance_v2.0", "fetching",
     "n11-06", "Shop B", 0, 1, 1, 0, None, False),
    ("3b9e1f0a", 15, "20:41:12", "Cat on a sofa, sunset light", "video", "seedance_v2.5", "done", "n11-03",
     "Shop A", 1, 0, 2, 0, None, False),
    ("5a0f3c88", 15, "20:33:48", "Product close-up, white marble", "image", "seedream-4.5", "failed", "n11-01",
     "Shop A", 0, 2, 1, 1, "dola.content_refused", False),
    ("a93e0d71", 15, "20:07:15", "Rainy Tokyo crossing, 9:16", "video", "seedance_v2.5", "unknown", "n11-05",
     "Shop B", 0, 0, 2, 0, None, False),
    ("0d4a9f13", 15, "19:52:40", "Logo reveal, purple smoke", "video", "seedance_v2.0", "done", "n11-00",
     "Partner C", 1, 1, 1, 0, None, True),
    ("7f20b6ce", 15, "19:40:02", "Portrait, soft window light", "image", "seedream-5.0-pro", "done", "n11-07",
     "Shop A", 4, 0, 1, 0, None, False),
    ("c41f9a07", 14, "23:58:10", "Lantern festival, slow pan", "video", "seedance_v2.5", "done", "n11-04",
     "Shop B", 1, 0, 2, 0, None, False),
    ("f6a2d1e4", 14, "23:41:55", "Kids drawing a dragon, crayon", "image", "seedream-4.5", "done", "n11-03",
     "Shop B", 4, 0, 1, 0, None, False),
)
POLLING = ("waiting", "fetching", "unknown")
OPEN = ("queued", *POLLING)

# the band's columns (delivered, failed, in progress) from 21:00 on 14 Oct, one per hour, oldest first, as the board
# draws them
BAND = ((4, 0, 0), (3, 0, 0), (2, 1, 0), (1, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0), (5, 0, 0), (6, 0, 0), (8, 0, 0),
        (9, 0, 0), (11, 0, 0), (9, 0, 0), (10, 0, 0), (12, 1, 0), (13, 0, 0), (11, 0, 0), (14, 0, 0), (12, 0, 0),
        (13, 1, 0), (15, 0, 0), (14, 2, 0), (2, 0, 0), (1, 1, 4))
FIRST_HOUR = datetime(2026, 10, 14, 21, tzinfo=SAIGON)
COMPLETED, FAILED, VIDEOS, IMAGES = 1284, 37, 335, 120
YESTERDAY_TOTAL = 212           # the Yesterday header: every job of 14 Oct
PRE_JOB_FAILURES_24H = 6

_COLUMNS = ("id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, after_sent, "
            "submit_timeout_s, wallet_kind, price, title, created_at, updated_at, finished_at, error_code")


def _series(db, key_id, count, start, step_s, *, media, status, error_code=None):
    """`count` filler jobs from `start`, one every `step_s` seconds. `media` "other" keeps a done job out of the
    video and image counts."""
    if count <= 0:
        return
    db.run("INSERT INTO jobs (" + _COLUMNS + ") SELECT gen_random_uuid(), %s, %s, 'generate', %s, 'filler', 'filler', "
           "'m' || gen_random_uuid()::text, %s, 'detach', 'reconcile', 60, %s, 0, NULL, s.ts, s.ts, "
           "s.ts + interval '1 minute', %s "
           "FROM (SELECT %s::timestamptz + make_interval(secs => g * %s) AS ts FROM generate_series(1, %s) g) s",
           PROVIDER, key_id, media, status, "image" if media == "image" else "video", error_code, start, step_s, count)


def _named(db, keys, accounts, row):
    short, day, clock, title, kind, model, status, account, key_name, outs, refs, price, refund, code, swapped = row
    job_id = short + "-0000-4000-8000-000000000000"
    key_id = keys[key_name]
    at = "2026-10-%02d %s+07" % (day, clock)
    settled = status in ("done", "failed")
    polls = "clock_timestamp() + interval '30 days'" if status in POLLING else "NULL"
    db.run("INSERT INTO jobs (" + _COLUMNS + ", account_id, external_ref, next_poll_at, warnings) VALUES "
           "(%s, %s, %s, 'generate', %s, %s, 'a prompt', %s, %s, 'detach', 'reconcile', 60, %s, %s, %s, "
           "%s::timestamptz, %s::timestamptz, %s::timestamptz + interval '1 minute', %s, %s, %s, " + polls + ", "
           "%s::jsonb)",
           job_id, PROVIDER, key_id, kind, model, "m" + short, status, kind, price, title, at, at,
           at if settled else None, code, accounts[account] if account else None,
           job_id if status in POLLING else None,
           json.dumps([{"code": "dola.model_substituted", "message": "x"}] if swapped else []))
    for idx in range(outs):
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'output', %s, 'k', %s, 1)", job_id, idx, "video/mp4" if kind == "video" else "image/png")
    for idx in range(refs):
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'input', %s, 'k', 'image/png', 1)", job_id, idx)
    if price:
        charge = db.one("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
                        "VALUES (%s, %s, %s, %s, 'charge', %s, %s::date) RETURNING id",
                        key_id, job_id, PROVIDER, kind, price, at)[0]
        if refund:
            db.run("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day, refund_of) "
                   "VALUES (%s, %s, %s, %s, 'refund', %s, %s::date, %s)", key_id, job_id, PROVIDER, kind, refund,
                   at, charge)
    if status in OPEN:
        built = alerts.build(PROVIDER, ALERT_TEXT)
        db.run("INSERT INTO unclear_replies (provider, job_id, account_id, text, wording, group_id) "
               "VALUES (%s, %s, %s, %s, %s, %s)", PROVIDER, job_id, accounts[account] if account else None,
               built.text, built.wording, built.group_id)


def jobs_seed(db, live):
    """Four jobs in progress (the banner's alert group), the sample page (rows 9 and 10 on 14 Oct, with 210 more jobs
    that day), and the totals the ledger reads: 1,284 done, 37 failed, 335 videos, 120 images, 7 of 8 accounts
    usable, 6 pre-job failures. Everything else is older than 14 Oct."""
    set_setting(db, live, "alert_accounts_low", 8)
    board_user(live)
    keys = {label: new_key(db, wallets=WALLETS) for label in KEYS}
    for label, key_id in keys.items():
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, key_id)
    accounts = {}
    for label, state, asleep, in_use in ACCOUNTS:
        short = label.removeprefix("acct-")
        account = new_account(db, label=short, state=state, checked=True, provider=PROVIDER)
        accounts[short] = account
        if asleep:
            db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '35 min' WHERE id = %s", account)
        if in_use:
            db.run(IN_USE_LEASE, account)
    for row in NAMED:
        _named(db, keys, accounts, row)

    shop_a = keys["Shop A"]
    named_done = [r for r in NAMED if r[6] == "done"]
    older_videos = VIDEOS - sum(1 for r in named_done if r[4] == "video")
    older_images = IMAGES - sum(1 for r in named_done if r[4] == "image")
    older_other = COMPLETED - len(named_done) - older_videos - older_images
    on_14th = YESTERDAY_TOTAL - sum(1 for r in NAMED if r[1] == 14)
    # 14 Oct: one "other" job every 5 minutes from 01:00; everything else on 12 Oct
    _series(db, shop_a, on_14th, "2026-10-14 01:00:00+07", 300, media="other", status="done")
    _series(db, shop_a, older_videos, "2026-10-12 01:00:00+07", 30, media="video", status="done")
    _series(db, shop_a, older_images, "2026-10-12 06:00:00+07", 30, media="image", status="done")
    _series(db, shop_a, older_other - on_14th, "2026-10-12 08:00:00+07", 30, media="other", status="done")
    _series(db, shop_a, FAILED - sum(1 for r in NAMED if r[6] == "failed"), "2026-10-12 15:00:00+07", 60,
            media="video", status="failed", error_code="render.deadline")
    # every done job has its output, and a wallet's total is what its ledger says (the strict invariants of the tier)
    db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) SELECT id, 'output', 0, 'k', "
           "'video/mp4', 1 FROM jobs j WHERE status = 'done' AND NOT EXISTS "
           "(SELECT 1 FROM job_media m WHERE m.job_id = j.id AND m.role = 'output')")
    db.run("UPDATE key_wallets w SET used_total = (SELECT COALESCE(sum(CASE entry WHEN 'charge' THEN amount "
           "ELSE -amount END), 0) FROM ledger l WHERE l.key_id = w.key_id AND l.provider = w.provider "
           "AND l.kind = w.kind AND l.entry IN ('charge', 'refund'))")
    # an unknown job was sent (unknown means sent)
    db.run("UPDATE jobs SET sent_at = created_at WHERE status IN ('unknown', 'waiting', 'fetching')")
    for _ in range(PRE_JOB_FAILURES_24H):
        db.run("INSERT INTO request_logs (at, key_id, provider, route, status_code, duration_ms, error_code) "
               "VALUES (clock_timestamp() - interval '2 hours', %s, %s, 'POST /v1/videos', 400, 10, 'input.invalid')",
               shop_a, PROVIDER)
    return {"keys": keys}


def band_hours() -> list[dict]:
    """The board's 24 columns as the hourly route words them (epoch ms of each UTC hour, oldest first)."""
    return [{"hour_ms": int((FIRST_HOUR + timedelta(hours=i)).timestamp() * 1000), "delivered": d, "failed": f,
             "in_progress": r} for i, (d, f, r) in enumerate(BAND)]


def canvas_band(page, live, data):
    """Before the app loads: the hourly route answers with the board's columns (see the module's note)."""
    body = json.dumps({"hours": band_hours()})
    page.route(re.compile(r"/admin/stats/hourly(\?|$)"),
               lambda route: route.fulfill(status=200, content_type="application/json", body=body))


def tick_the_two(page, live, data):
    """The boards show the failed job and the unknown job ticked."""
    rows = page.locator("tbody tr:not(.is-day), li.data-table__item")
    for short in ("5a0f3c88", "a93e0d71"):
        rows.filter(has_text=short).locator("label.el-checkbox, label.data-table__check").first.click()
    expect(page.locator(".jobs__count")).to_have_text("2 selected")
    page.mouse.move(0, 0)               # no row is hovered: the boards draw none
