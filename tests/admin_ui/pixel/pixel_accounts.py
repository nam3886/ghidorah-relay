"""The Accounts boards (B-Accounts, T-Accounts, P-Accounts, B-AccountImport, B-AccountEdit): the nine accounts of
specs/sample-data.md section 4 and the states that open the two dialogs."""

import json
import re

from accountkit import done_job
from patchright.sync_api import expect
from pixel_seed import ACCOUNTS, NOW, PROVIDER, plant_capacity, set_setting, shell_seed

TIMEOUT_MS = 10_000
VIDEO_BUDGET = 30
# The boards' times, written as the boards' clock reads them: the last full check, and the wake-up of the asleep
# account (its cooldown must still lie ahead on the database clock, so it is the next 21:30 in Saigon after the real
# now; the page shows its clock time only).
LAST_FULL_CHECK = "2026-10-15T20:48:10+07:00"
NEXT_2130 = ("SELECT CASE WHEN t > clock_timestamp() + interval '5 min' THEN t ELSE t + interval '1 day' END FROM "
             "(SELECT (date_trunc('day', clock_timestamp() AT TIME ZONE 'Asia/Saigon') + interval '21 hours 30 min') "
             "AT TIME ZONE 'Asia/Saigon' AS t) s")
# The board's import result: lines 1 and 2 imported, line 3 rejected (design-deviations 5: a reason code, no echo).
IMPORT_ANSWER = {"imported": [{"index": 0, "id": "00000000-0000-4000-8000-000000000009", "label": "acct-n11-09"},
                              {"index": 1, "id": "00000000-0000-4000-8000-000000000010", "label": "acct-n11-10"}],
                 "rejected": [{"index": 2, "reason": "credential_unparseable"}]}
IMPORT_LINES = "\n".join("cookie-jar-n11-%02d …" % n for n in (9, 10, 11))

# label -> (display name, composer, minutes since the check, points left or None, videos, images, proxy, proxy check,
# last error). Proxy check: minutes since it ran and whether it passed (None = never checked).
SAMPLE = {
    "acct-n11-00": ("Demo User H", "v2", 3, 24, 31, 12, "10.0.0.10:8000", (4, True), None),
    "acct-n11-01": ("Demo User B", "v2", 3, 30, 58, 22, "10.0.0.10:8000", (4, True), None),
    "acct-n11-02": ("Demo User C", "v1", 6, 14, 47, 15, "10.0.0.11:8000", (4, True), None),
    "acct-n11-03": ("Demo User A", None, 4, 18, 42, 9, "10.0.0.11:8000", (4, True), None),
    "acct-n11-04": ("Demo User D", "v2", 2, None, 36, 18, "10.0.0.12:8000", (4, True), "dola.likeness_refused"),
    "acct-n11-05": ("Demo User E", "v2", 5, 21, 27, 6, "10.0.0.12:8000", (5, False),
                    "proxy check: connect timeout after 8 s"),
    "acct-n11-06": ("Demo User F", "v2", 4, 27, 64, 31, "10.0.0.13:8000", (4, True),
                    "drift: page shows 26, ledger says 27"),
    "acct-n11-07": ("Demo User G", None, 11, 9, 19, 4, None, None, None),
    "acct-n11-08": ("Demo User I", "v2", 60, None, 11, 3, "10.0.0.13:8000", None, "account.dead_session"),
}


def accounts_seed(db, live):
    """The shell's data (the board's user, keys, nine accounts, the four jobs and the alert behind the tab counts) plus
    everything the Accounts rows show. A time the page writes as a clock time or as "x min ago" is planted at the
    boards' clock (NOW), which the browser's clock is frozen to."""
    data = shell_seed(db, live)
    set_setting(db, live, "dola.video_budget", VIDEO_BUDGET)
    # the nine accounts newest first, as the list reads them (acct-n11-00 on top)
    ids = {r[0]: r[1] for r in db.all("SELECT label, id FROM accounts WHERE provider = %s", PROVIDER)}
    for index, (label, _state, asleep, _in_use) in enumerate(ACCOUNTS):
        db.run("UPDATE accounts SET created_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
               index + 1, ids[label])
        if asleep:
            db.run("UPDATE accounts SET cooldown_until = (%s) WHERE id = %%s" % NEXT_2130, ids[label])
    db.run("INSERT INTO commands (provider, kind, status, created_at, finished_at) "
           "VALUES (%s, 'check_all', 'done', %s, %s)", PROVIDER, LAST_FULL_CHECK, LAST_FULL_CHECK)
    for label, *_ in ACCOUNTS:
        name, composer, checked, left, videos, images, proxy, check, error = SAMPLE[label]
        account = ids[label]
        db.run("UPDATE accounts SET display_name = %s, "
               "last_checked_at = %s::timestamptz - make_interval(mins => %s), last_error = %s, "
               "provider_state = %s::jsonb WHERE id = %s",
               name, NOW, checked, error, '{"composer": "%s"}' % composer if composer else "{}", account)
        if proxy:
            host, port = proxy.split(":")
            db.run("UPDATE accounts SET proxy_host = %s, proxy_port = %s, proxy_auth_enc = '\\x01' WHERE id = %s",
                   host, int(port), account)
        if check:
            db.run("UPDATE accounts SET proxy_ok = %s, "
                   "proxy_checked_at = %s::timestamptz - make_interval(mins => %s) WHERE id = %s",
                   check[1], NOW, check[0], account)
        # a screenshot of every account: Shot can be picked, as B-States draws the row menu
        db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, %s)", account, "shots/%s.png" % label)
        if label == "acct-n11-04":
            db.run("UPDATE accounts SET strikes = 1, cooldown_reason = 'dola.likeness_refused' WHERE id = %s", account)
        if label == "acct-n11-08":
            db.run("UPDATE accounts SET state_reason = 'account.dead_session' WHERE id = %s", account)
        if left is None:
            db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) VALUES "
                   "(%s, 'video', NULL, 'reported', (clock_timestamp() AT TIME ZONE 'UTC')::date)", account)
        else:
            plant_capacity(db, account, "video", "UTC", left)
            if label == "acct-n11-06":                     # drift is judged on counted points
                db.run("UPDATE account_capacity SET source = 'counted' WHERE account_id = %s", account)
        key = data["keys"]["Shop A"]
        first = None
        for n in range(videos + images):
            job = done_job(db, key, account, ref="%s-%d" % (label, n))
            first = first or job
            if n >= videos:
                db.run("UPDATE jobs SET media_type = 'image' WHERE id = %s", job)
        if label == "acct-n11-06":                     # the page says 26, the ledger 27: a drift
            db.run("UPDATE jobs SET taken_at = clock_timestamp(), account_cost = 4 WHERE id = %s", first)
            db.run("INSERT INTO account_charges (account_id, job_id, pool, amount, account_day) VALUES "
                   "(%s, %s, 'video', 4, (clock_timestamp() AT TIME ZONE 'UTC')::date)", account, first)
    return data


def open_import(page, live, data):
    """The board's state: the result of the previous import (answered with the board's figures, as no credential of
    the board can be imported for real) under the three lines typed again."""
    page.route(re.compile(r"/admin/accounts/import$"), lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(IMPORT_ANSWER)))
    expect(page.get_by_role("button", name="Import", exact=True)).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Import", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import accounts")
    expect(dialog).to_be_visible(timeout=TIMEOUT_MS)
    dialog.get_by_label("Credentials").fill(IMPORT_LINES)
    dialog.get_by_role("button", name="Import", exact=True).click()
    expect(dialog.get_by_role("status", name="Result of the previous import")).to_be_visible(timeout=TIMEOUT_MS)
    dialog.get_by_label("Credentials").fill(IMPORT_LINES)
    dialog.get_by_label("Credentials").blur()
    page.mouse.move(0, 0)


def open_edit(page, live, data):
    row = page.locator("tbody tr").filter(has_text="acct-n11-05")
    expect(row).to_be_visible(timeout=TIMEOUT_MS)
    row.get_by_role("button", name=re.compile(r"^More actions for ")).click()
    page.get_by_role("menuitem", name="Edit", exact=True).click()
    expect(page.get_by_role("dialog", name=re.compile(r"^Edit account"))).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)


def phone_ready(page, live, data):
    """The rows, the pool figures and the tab counts drawn (each arrives with its own read)."""
    expect(page.get_by_text("acct-n11-05")).to_be_visible(timeout=TIMEOUT_MS)
    expect(page.get_by_test_id("pool-card")).to_contain_text("143 / 210", timeout=TIMEOUT_MS)
    for tab in ("jobs", "accounts", "alerts"):
        expect(page.get_by_test_id("tab-count-" + tab)).to_be_visible(timeout=TIMEOUT_MS)
    page.mouse.move(0, 0)                           # no row under the pointer the sign-in left there
