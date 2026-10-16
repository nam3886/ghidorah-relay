"""The Accounts boards (B-Accounts, T-Accounts, P-Accounts, B-AccountImport, B-AccountEdit): the nine accounts of
specs/sample-data.md section 4 and the states that open the two dialogs."""

import re

from accountkit import done_job, new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from pixel_seed import ACCOUNTS, IN_USE_LEASE, PROVIDER, WALLETS, plant_capacity, set_setting

TIMEOUT_MS = 10_000
VIDEO_BUDGET = 30

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
    """The shell's data (keys, nine accounts, the alert) plus everything the Accounts rows show."""
    # No alert is planted: the app draws its attention banner on every tab, the Accounts boards draw none (shell.md 5).
    # The nine accounts come from the table of pixel_seed, newest first as the list reads them (acct-n11-00 on top).
    set_setting(db, live, "dola.video_budget", VIDEO_BUDGET)
    keys = [new_key(db, wallets=WALLETS)]
    data = {"keys": {"Shop A": keys[0]}}
    for index, (label, state, asleep, in_use) in enumerate(ACCOUNTS):
        account = new_account(db, label=label, state=state, checked=True, provider=PROVIDER)
        db.run("UPDATE accounts SET created_at = clock_timestamp() - make_interval(secs => %s) WHERE id = %s",
               index + 1, account)
        if asleep:
            db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '35 min' WHERE id = %s", account)
        if in_use:
            db.run(IN_USE_LEASE, account)
    ids = {r[0]: r[1] for r in db.all("SELECT label, id FROM accounts WHERE provider = %s", PROVIDER)}
    for label, *_ in ACCOUNTS:
        name, composer, checked, left, videos, images, proxy, check, error = SAMPLE[label]
        account = ids[label]
        db.run("UPDATE accounts SET display_name = %s, "
               "last_checked_at = clock_timestamp() - make_interval(mins => %s), last_error = %s, "
               "provider_state = %s::jsonb WHERE id = %s",
               name, checked, error, '{"composer": "%s"}' % composer if composer else "{}", account)
        if proxy:
            host, port = proxy.split(":")
            db.run("UPDATE accounts SET proxy_host = %s, proxy_port = %s, proxy_auth_enc = '\\x01' WHERE id = %s",
                   host, int(port), account)
        if check:
            db.run("UPDATE accounts SET proxy_ok = %s, "
                   "proxy_checked_at = clock_timestamp() - make_interval(mins => %s) WHERE id = %s",
                   check[1], check[0], account)
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
        key = keys[0]
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
    expect(page.get_by_role("button", name="Import", exact=True)).to_be_visible(timeout=TIMEOUT_MS)
    page.get_by_role("button", name="Import", exact=True).click()
    dialog = page.get_by_role("dialog", name="Import accounts")
    expect(dialog).to_be_visible(timeout=TIMEOUT_MS)
    dialog.get_by_label("Credentials").fill("\n".join("cookie-jar-n11-%02d …" % n for n in (9, 10, 11)))


def open_edit(page, live, data):
    row = page.locator("tbody tr").filter(has_text="acct-n11-05")
    expect(row).to_be_visible(timeout=TIMEOUT_MS)
    row.get_by_role("button", name=re.compile(r"^More actions for ")).click()
    page.get_by_role("menuitem", name="Edit", exact=True).click()
    expect(page.get_by_role("dialog", name=re.compile(r"^Edit account"))).to_be_visible(timeout=TIMEOUT_MS)


def phone_ready(page, live, data):
    expect(page.get_by_text("acct-n11-05")).to_be_visible(timeout=TIMEOUT_MS)
