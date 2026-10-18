"""The pixel entries of lane 6: API keys, Request logs and Alerts (B-/T-/P- boards), with their seeds and drives.

Built from specs/sample-data.md sections 5 to 7. Where the canvas shows figures no rows can be behind (the logs total
4,812, a group of 6 rows with 4 listed, the ids of requests) the seed plants what a row can be and the diff report
explains the rest."""

import dataclasses
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from accountkit import new_account
from jobkit import key as new_key
from patchright.sync_api import Error as PlaywrightError
from patchright.sync_api import expect
from pixel_lane7 import use_the_boards_models
from pixel_manifest import TIMEOUT_MS, Entry
from pixel_seed import (
    ACCOUNTS,
    ALERT_ACCOUNTS_LOW,
    ALERT_TEXT,
    BOARD_USER,
    IN_USE_LEASE,
    NOW,
    PROVIDER,
    WALLETS,
    board_user,
    set_setting,
)
from test_admin_alert_groups import plant_unclear, stamp

from relay_core import alerts

SAIGON = timezone(timedelta(hours=7))
CALLOUT_KEY = "rk_live_XXXXXXXXXXXXXXXXXXXXXXXXXXXX7d2e"       # the board's text; the real key is random (masked)

JOB_SQL = ("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, "
           "after_sent, submit_timeout_s, wallet_kind, price, title, finished_at) "
           "VALUES (%s, %s, %s, 'generate', %s, 'm', 'p', %s, 'done', 'detach', 'reconcile', 60, %s, 0, %s, now())")


def at(day: int, hour: int, minute: int, second: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=SAIGON)


# ---- the top bar: Jobs 4, Accounts 7/8, Alerts 1 ----------------------------------------------------------------
def top_bar(db, live, keys: list, rows: tuple) -> None:
    """Four jobs in progress (one per index of `rows`, which also carry the open unclear reply the Alerts count reads),
    nine accounts (7 usable of 8 available) and the pool threshold of the boards."""
    set_setting(db, live, "alert_accounts_low", ALERT_ACCOUNTS_LOW)
    for label, state, asleep, in_use in ACCOUNTS:
        account = new_account(db, label=label, state=state, checked=True, provider=PROVIDER)
        if asleep:
            db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '35 min' WHERE id = %s", account)
        if in_use:
            db.run(IN_USE_LEASE, account)
    for index in rows:
        plant_unclear(db, keys[index], ALERT_TEXT, provider=PROVIDER)


# ---- keys ------------------------------------------------------------------------------------------------------------
# (label, prefix, enabled, models, video wallet, image wallet, jobs total, created)
# wallet: ("limit", day limit, total limit, used today, used in total) | ("unlimited", used) | ("closed",)
KEY_ROWS = (
    ("Shop A", "rk_live_…a1b2", True, ["seedance_v2.5", "seedance_v2.0"], ("limit", 20, 200, 3, 14),
     ("unlimited", 9), 61, at(3, 12, 0, month=9)),
    ("Shop B", "rk_live_…e5f6", True, ["seedance_v2.5", "seedream-4.5", "seedream-5.0-pro"],
     ("limit", 30, 300, 12, 88), ("limit", 10, 80, 2, 17), 148, at(21, 12, 0, month=9)),
    ("Partner B", "rk_live_…c3d4", False, ["seedance_v2.5", "seedance_v2.0"], ("closed",), ("closed",), 23,
     at(30, 12, 0, month=9)),
    ("Partner C", "rk_live_…9f3a", True, ["seedance_v2.0", "seedream-4.5"], ("limit", 10, 100, 2, 12),
     ("limit", 10, 50, 1, 5), 17, at(4, 12, 0)),
    ("Internal test", "rk_live_…0b9a", True, ["*"], ("unlimited", 37), ("unlimited", 4), 312, at(12, 12, 0, month=8)),
)
PARTNER_D = ("Partner D", "rk_live_…7d2e", ["seedance_v1.0"], ("limit", 5, 20, 0, 0), ("closed",), at(15, 20, 54))
UNFINISHED = (0, 1, 1, 3)               # index into KEY_ROWS of the four jobs in progress: A 1, B 2, C 1


def wallet_values(spec: tuple) -> tuple:
    """(day_limit, total_limit, used_day, used_total) of a wallet spec."""
    if spec[0] == "limit":
        return spec[1], spec[2], spec[3], spec[4]
    if spec[0] == "unlimited":
        return None, None, 0, spec[1]
    return 0, None, 0, 0


def set_key(db, key_id, label, prefix, enabled, models, video, image, created) -> None:
    db.run("UPDATE api_keys SET label = %s, key_prefix = %s, enabled = %s, models = %s, max_unfinished = 5, "
           "created_at = %s WHERE id = %s", label, prefix, enabled, models, created, key_id)
    if label == "Internal test":
        db.run("UPDATE api_keys SET max_unfinished = 0 WHERE id = %s", key_id)
    for kind, spec in (("video", video), ("image", image)):
        day, total, used_day, used_total = wallet_values(spec)
        db.run("UPDATE key_wallets SET day_limit = %s, total_limit = %s, used_day = %s, used_total = %s, "
               "day_anchor = current_date WHERE key_id = %s AND provider = %s AND kind = %s", day, total, used_day,
               used_total, key_id, PROVIDER, kind)
        # the ledger the counters add up from (the checker holds every test to it): what was used before today,
        # then what was used today
        for amount, day_offset in ((used_total - used_day, 1), (used_day, 0)):
            if amount:
                db.run("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) VALUES (%s, %s, %s, "
                       "'adjust', %s, current_date - %s)", key_id, PROVIDER, kind, amount, day_offset)


# The display names B-KeyForm draws for the boards' five models; the catalog sends them as `name`, as the server does.
BOARD_MODEL_NAMES = {"seedance_v2.5": "Dreamina Seedance 2.5", "seedance_v2.0": "Seedance 2.0 Fast",
                     "seedance_v1.0": "Seedance 1.0", "seedream-4.5": "Seedream 4.5",
                     "seedream-5.0-pro": "Seedream 5.0 Pro"}


def name_the_boards_models(live) -> None:
    """The live server's catalog (the boards' models, see use_the_boards_models) with the boards' display names."""
    spec = live.env.registry.spec(PROVIDER)
    plain = spec.catalog
    spec.catalog = lambda: [dataclasses.replace(m, display_name=BOARD_MODEL_NAMES.get(m.id, "")) for m in plain()]


def keys_seed(db, live):
    """Five of the six keys of the board; Partner D is made by the drive (its one-time key is the callout). The
    boards' user (the top bar reads "admin") and the boards' five models with their names (the Edit dialog lists
    them)."""
    board_user(live)
    use_the_boards_models(live, frames=False)
    name_the_boards_models(live)
    keys = [new_key(db, wallets=WALLETS) for _ in KEY_ROWS]
    top_bar(db, live, keys, UNFINISHED)
    for key_id, (label, prefix, enabled, models, video, image, jobs, created) in zip(keys, KEY_ROWS, strict=True):
        set_key(db, key_id, label, prefix, enabled, models, video, image, created)
        # the jobs of the board's `Jobs total`, beyond the ones in progress: finished, so they are not unfinished
        planted = db.one("SELECT count(*) FROM jobs WHERE key_id = %s", key_id)[0]
        db.run("INSERT INTO jobs (id, provider, key_id, operation, media_type, model, prompt, marker, status, "
               "completion, after_sent, submit_timeout_s, wallet_kind, price, error_code, finished_at) "
               "SELECT gen_random_uuid(), %s, %s, 'generate', 'video', 'm', 'p', 'marker-' || gen_random_uuid()::text, "
               "'failed', 'detach', 'reconcile', 60, 'video', 0, 'render.deadline', now() - interval '3 days' "
               "FROM generate_series(1, %s)", PROVIDER, key_id, jobs - planted)
    return {"db": db, "keys": keys}


def make_partner_d(page, data) -> None:
    """The callout of the board: create Partner D through the dialog, then give its row the board's figures."""
    page.get_by_role("button", name="New key").click()
    box = page.get_by_role("dialog")
    box.get_by_label("Label", exact=True).fill("Partner D")
    box.get_by_role("button", name="Create key").click()
    expect(page.get_by_test_id("new-key")).to_be_visible(timeout=TIMEOUT_MS)
    db = data["db"]
    label, prefix, models, video, image, created = PARTNER_D
    key_id = db.one("SELECT id FROM api_keys WHERE label = %s", label)[0]
    set_key(db, key_id, label, prefix, True, models, video, image, created)
    # the page reads the list again when a switch is used: flip the last key off and on, the callout stays
    switch = page.get_by_role("switch", name="Enable key Internal test")
    switch.click()
    expect(switch).not_to_be_checked(timeout=TIMEOUT_MS)
    switch.click()
    expect(switch).to_be_checked(timeout=TIMEOUT_MS)
    expect(page.get_by_text(prefix)).to_be_visible(timeout=TIMEOUT_MS)
    # the one-time key is random: show the board's text instead (a mask for the diff, nothing else changes)
    page.evaluate("t => { document.querySelector('[data-testid=\"new-key\"] input').value = t; }", CALLOUT_KEY,
                  isolated_context=False)
    page.mouse.move(0, 0)               # no row is hovered: the boards draw none (the switch was the last click)


def keys_callout(page, live, data) -> None:
    make_partner_d(page, data)


def keys_edit(page, live, data) -> None:
    expect(page.get_by_role("button", name="Edit key Shop A")).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Edit key Shop A").click()
    expect(page.get_by_role("dialog", name="Edit API key")).to_be_visible(timeout=TIMEOUT_MS)


# ---- request logs ----------------------------------------------------------------------------------------------------
VG, IG = "POST /v1/videos/generations", "POST /v1/images/generations"
# (day, h, m, s, http, route, provider, key, job id prefix, job title, error code, ms)
LOG_ROWS = (
    (15, 20, 54, 22, 200, IG, PROVIDER, "Partner C", "2c6b8e90", "(untitled)", None, 388),
    (15, 20, 52, 30, 200, VG, PROVIDER, "Shop A", "e17c55b2", "Street food night market, handheld", None, 431),
    (15, 20, 49, 41, 200, "GET /v1/models", None, "Shop B", None, None, None, 9),
    (15, 20, 41, 12, 200, VG, PROVIDER, "Shop A", "3b9e1f0a", "Cat on a sofa, sunset light", None, 412),
    (15, 20, 33, 48, 422, IG, PROVIDER, "Shop A", "5a0f3c88", "Product close-up, white marble",
     "dola.content_refused", 1980),
    (15, 20, 21, 9, 400, VG, PROVIDER, "Partner B", None, None, "input.bad_duration", 9),
    (15, 20, 7, 15, 502, VG, PROVIDER, "Shop B", "a93e0d71", "Rainy Tokyo crossing, 9:16",
     "account.dead_session", 2140),
    (14, 23, 58, 10, 200, VG, PROVIDER, "Shop B", "c41f9a07", "Lantern festival, slow pan", None, 405),
    (14, 23, 41, 55, 200, IG, PROVIDER, "Shop B", "f6a2d1e4", "Kids drawing a dragon, crayon", None, 372),
    (14, 22, 15, 3, 200, "GET /v1/jobs/{id}", PROVIDER, "Shop A", "0d4a9f13", "Logo reveal, purple smoke", None, 14),
    (14, 19, 52, 40, 200, VG, PROVIDER, "Partner C", "0d4a9f13", "Logo reveal, purple smoke", None, 447),
    (14, 10, 2, 18, 422, VG, PROVIDER, "Shop A", "8e51a2c6", "Crowd at a night festival", "dola.content_refused", 1640),
)
REQUEST_BODY = {"model": "seedream-4.5", "prompt": "Close-up of a perfume bottle on white marble, hard window light, "
                "crisp reflections.", "n": 1, "ratio": "2:3", "references": 2}
ANSWER_BODY = {"error": {"code": "dola.content_refused", "message": "Dola refused the prompt as content.",
                         "request_id": "rq_8f2174"}}


def logs_seed(db, live):
    board_user(live)
    labels = ("Shop A", "Shop B", "Partner B", "Partner C")
    keys = dict(zip(labels, [new_key(db, wallets=WALLETS) for _ in labels], strict=True))
    for label, key_id in keys.items():
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, key_id)
    top_bar(db, live, list(keys.values()), (0, 1, 1, 3))
    jobs: dict[str, uuid.UUID] = {}
    for _d, _h, _m, _s, _http, route, _p, key, prefix, title, _e, _ms in LOG_ROWS:
        if prefix and prefix not in jobs:
            jobs[prefix] = uuid.UUID(prefix + "-0000-4000-8000-000000000000")
            kind = "image" if route == IG else "video"
            db.run(JOB_SQL, jobs[prefix], PROVIDER, keys[key], kind, "marker-" + prefix, kind, title)
            db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
                   "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1)", jobs[prefix])
    for d, h, m, s, http, route, provider, key, prefix, _t, error, ms in LOG_ROWS:
        request = REQUEST_BODY if (d, h, m, s) == (15, 20, 33, 48) else {}
        response = ANSWER_BODY if (d, h, m, s) == (15, 20, 33, 48) else None
        db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, error_code, "
               "body_redacted, response_redacted, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)",
               keys[key], provider, route, http, jobs.get(prefix), ms, error, json.dumps(request),
               None if response is None else json.dumps(response), at(d, h, m, s))
    return {"db": db}


# The boards' list total (4,812, so 97 pages of 50) and yesterday's day total (4,761) have no rows behind them: the page
# holds 12 rows, which 4,812 rows could never give a first page of 50. As the Jobs band (pixel_jobs.canvas_band), the
# list answer of the boards' window is served with the database's rows and those two figures; nothing else changes.
LOGS_TOTAL, YESTERDAY_LOGS = 4812, 4761


def canvas_totals(page, live, data) -> None:
    """Before the app loads: the list answer of the first page carries the boards' total and yesterday's total."""
    def answer(route):
        try:
            got = route.fetch()
            body = got.json()
            if "offset=0" in route.request.url and body.get("total") == len(LOG_ROWS):
                body["total"] = LOGS_TOTAL
                body.setdefault("day_totals", {})["2026-10-14"] = YESTERDAY_LOGS
            route.fulfill(response=got, json=body)
        except PlaywrightError:         # the page closed while the answer was on its way
            pass

    page.route(re.compile(r"/admin/logs\?"), answer)


# The boards draw the two dates set, nothing focused or hovered, the phone's row of filters at its start, and the tab
# strip at its start: the app scrolls the current tab into view on a phone (DESIGN 10, which no board can draw), and
# whether it has by the time of the photo depends on when the fonts arrive.
AT_REST_JS = """() => { document.activeElement?.blur();
    document.querySelectorAll('form[role=search] *, nav[aria-label=Main]').forEach((el) => { el.scrollLeft = 0; }); }"""


def logs_filters(page, live, data) -> None:
    page.locator('input[aria-label="From"]').fill("2026-10-14")
    page.locator('input[aria-label="To"]').fill("2026-10-15")
    expect(page.get_by_text("Today · 15 Oct")).to_be_visible(timeout=TIMEOUT_MS)
    page.evaluate(AT_REST_JS, isolated_context=False)
    page.mouse.move(0, 0)


def logs_drawer(page, live, data) -> None:
    logs_filters(page, live, data)
    page.get_by_role("button", name="Open details of request").nth(4).click()      # the 422 of 20:33:48
    expect(page.get_by_test_id("request-drawer")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def logs_menu(page, live, data) -> None:
    logs_filters(page, live, data)
    page.get_by_role("button", name="More actions").click()                         # P-Logs draws the menu open
    expect(page.get_by_role("menuitem", name="Clear logs")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


# ---- alerts ----------------------------------------------------------------------------------------------------------
# The reply rows of group 1 as the boards list them: (seen, job id prefix, job title, status, error code, account, key)
REPLIES = (
    (at(15, 8, 55), "b72e4d19", "Beach wedding, golden hour", "done", None, "n11-03", "Shop A"),
    (at(15, 2, 31), "4e8a0c63", "Crowd at a night festival", "failed", "dola.likeness_refused", "n11-06", "Shop B"),
    (at(14, 16, 12), "91d3f7a8", "Skater at a neon underpass", "done", None, "n11-02", "Partner C"),
    (at(14, 10, 2), "c41f9a07", "Lantern festival, slow pan", "done", None, "n11-04", "Shop B"),
)


def alerts_seed(db, live):
    labels = ("Shop A", "Shop B", "Partner B", "Partner C")
    keys = dict(zip(labels, [new_key(db, wallets=WALLETS) for _ in labels], strict=True))
    for label, key_id in keys.items():
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, key_id)
    # the top bar: Jobs 4 (queued), Accounts 7/8; Alerts 1 is the open group below
    set_setting(db, live, "alert_accounts_low", ALERT_ACCOUNTS_LOW)
    for label, state, asleep, in_use in ACCOUNTS:
        account = new_account(db, label=label, state=state, checked=True, provider=PROVIDER)
        if asleep:
            db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '35 min' WHERE id = %s", account)
        if in_use:
            db.run(IN_USE_LEASE, account)
    for label in ("Shop A", "Shop B", "Shop B", "Partner C"):
        db.job(keys[label], provider=PROVIDER)
    # a live dola worker, so the four queued jobs raise no "no live worker" condition (the boards draw none); its beat
    # is a few minutes ahead, so it cannot go stale while the page is photographed
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) "
           "VALUES ('pixel-dola', %s, clock_timestamp(), clock_timestamp() + interval '10 min')", PROVIDER)
    board_user(live)
    # group 1: Dola's refusal, four replies on the boards' jobs (done, refunded, done, done), open. The detail lists the
    # newest row first: planted oldest first. The reply accounts are disabled, so the Accounts tab keeps 7/8.
    accounts = {label: new_account(db, label=label, state="disabled", checked=True, provider=PROVIDER)
                for label in ("n11-02", "n11-03", "n11-04", "n11-06")}
    for when, prefix, title, status, error, account_label, key in reversed(REPLIES):
        job_id = uuid.UUID(prefix + "-0000-4000-8000-000000000000")
        db.run(JOB_SQL, job_id, PROVIDER, keys[key], "video", "marker-" + prefix, "video", title)
        if status == "failed":
            db.run("UPDATE jobs SET status = 'failed', error_code = %s WHERE id = %s", error, job_id)
        else:
            db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
                   "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1)", job_id)
        built = alerts.build(PROVIDER, ALERT_TEXT)
        db.run("INSERT INTO unclear_replies (provider, job_id, account_id, text, wording, group_id, at) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s)", PROVIDER, job_id, accounts[account_label], built.text,
               built.wording, built.group_id, when)
    # group 2: a clamp of Shop A's video wallet, acknowledged; group 3: a drift of Partner B's image wallet (2 rows)
    ledger = db.one("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) VALUES (%s, %s, 'video', "
                    "'adjust', 0, current_date) RETURNING id", keys["Shop A"], PROVIDER)[0]
    clamp = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, "
                   "used_total, at) VALUES ('clamp', %s, %s, 'video', %s, 5, 2, 3, %s) RETURNING id",
                   keys["Shop A"], PROVIDER, ledger, at(15, 6, 14))[0]
    stamp(db, "billing_alerts", clamp)
    for when in (at(13, 17, 40), at(14, 9, 21)):
        drift = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, amount, used_day, used_total, "
                       "detail, at) VALUES ('drift', %s, %s, 'image', 0, 1, 9, 'used_total 9, ledger says 0', %s) "
                       "RETURNING id", keys["Partner B"], PROVIDER, when)[0]
        stamp(db, "billing_alerts", drift)
    # group 4: one customer's image refusal, acknowledged
    image = "Sorry, I can’t create that image"
    for when in (at(10, 21, 33), at(11, 7, 48), at(11, 1, 5)):
        row = plant_unclear(db, keys["Partner C"], image, status="done", provider=PROVIDER)
        db.run("UPDATE unclear_replies SET at = %s WHERE id = %s", when, row)
        stamp(db, "unclear_replies", row)
    return {"db": db}


def alerts_all(page, live, data) -> None:
    # in the Alerts card: right after sign-in the Jobs page, with its own Status filter, may still be on its way out
    page.locator('section[aria-label="Alert groups"]').get_by_role("combobox", name="Status", exact=True).click()
    page.get_by_role("option", name="All", exact=True).click()
    expect(page.get_by_text("4 groups")).to_be_visible(timeout=TIMEOUT_MS)


def alerts_detail(page, live, data) -> None:
    alerts_all(page, live, data)
    page.get_by_role("button", name="Details of").first.click()
    expect(page.get_by_test_id("alert-detail")).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)       # the boards draw nothing hovered: not the row, not its Details


# ---- the entries -----------------------------------------------------------------------------------------------------
KLA = [
    Entry("B-Keys", "#/keys", seed=keys_seed, now=NOW, drive=keys_callout, user=BOARD_USER),
    Entry("T-Keys", "#/keys", seed=keys_seed, now=NOW, drive=keys_callout, user=BOARD_USER),
    Entry("P-Keys", "#/keys", seed=keys_seed, now=NOW, drive=keys_callout, user=BOARD_USER),
    Entry("B-KeyForm", "#/keys", seed=keys_seed, now=NOW, drive=keys_edit, user=BOARD_USER),
    Entry("B-Logs", "#/logs", seed=logs_seed, now=NOW, drive=logs_filters, prepare=canvas_totals, user=BOARD_USER),
    Entry("T-Logs", "#/logs", seed=logs_seed, now=NOW, drive=logs_filters, prepare=canvas_totals, user=BOARD_USER),
    Entry("P-Logs", "#/logs", seed=logs_seed, now=NOW, drive=logs_menu, prepare=canvas_totals, user=BOARD_USER),
    Entry("B-LogDetail", "#/logs", seed=logs_seed, now=NOW, drive=logs_drawer, prepare=canvas_totals,
          user=BOARD_USER),
    Entry("B-Alerts", "#/alerts", seed=alerts_seed, now=NOW, drive=alerts_detail, user=BOARD_USER),
    Entry("T-Alerts", "#/alerts", seed=alerts_seed, now=NOW, drive=alerts_detail, user=BOARD_USER),
    Entry("P-Alerts", "#/alerts", seed=alerts_seed, now=NOW, drive=alerts_detail, user=BOARD_USER),
]
