"""The sample data of the canvas, in one place (specs/sample-data.md, revised 16/10), and the seeds that plant it.

Every figure here is what the artboards show. When the sample data changes, change it here and nowhere else. A lane that
adds a screen to `pixel_manifest.py` adds its seed function here, built from these tables.

The clock the boards assume is Thursday 15 Oct 2026 20:55:02, Asia/Saigon. The browser's clock is frozen to it
(`Entry.now`) and the hourly band's `_now` too (conftest); the SQL windows use the database clock and cannot be moved,
so a seed plants rows a "24 h" count reads relative to the real now, and rows whose time the page shows at their
absolute sample time."""

import json

from accountkit import new_account
from jobkit import key as new_key
from test_admin_alert_groups import plant_unclear

NOW = "2026-10-15T20:55:02+07:00"
PROVIDER = "dola"               # the one provider of the boards (conftest names the live server's provider after it)
WALLETS = ((PROVIDER, "video", None, None), (PROVIDER, "image", None, None))

# The top bar: Jobs = in progress + queued, Accounts = usable / available, Alerts = open alert groups.
# Accounts 7/8 = 9 accounts minus 1 dead = 8 available, minus 1 asleep = 7 usable (an account in use is still usable).
TOP_BAR = {"jobs": 4, "accounts": "7/8", "alerts": 1}

KEYS = ("Shop A", "Shop B", "Partner C")

# (label, state, asleep, in use): all nine were checked minutes ago; acct-n11-04 is asleep until 21:30 and, like
# acct-n11-02, in use; acct-n11-08 is dead.
ACCOUNTS = (
    ("acct-n11-00", "available", False, False), ("acct-n11-01", "available", False, False),
    ("acct-n11-02", "available", False, True), ("acct-n11-03", "available", False, False),
    ("acct-n11-04", "available", True, True), ("acct-n11-05", "available", False, False),
    ("acct-n11-06", "available", False, False), ("acct-n11-07", "available", False, False),
    ("acct-n11-08", "dead", False, False),
)

# The one alert group of the Jobs banner: Dola's refusal, 4 jobs from 3 keys (they are the four jobs in progress).
ALERT_TEXT = "Sorry, I can’t create that video"
ALERT_ROWS = (0, 0, 1, 2)                       # index into KEYS of each of the 4 jobs

IN_USE_LEASE = ("INSERT INTO leases (account_id, purpose, owner, until, hard_until) "
                "VALUES (%s, 'check', 'pixel', now() + interval '1 h', now() + interval '1 h')")


ALERT_ACCOUNTS_LOW = 8          # the pool alert reads "<provider> has 7 usable accounts (alert below 8)"


def set_setting(db, live, key, value):
    """Store a setting the way the admin does (row, notify) and make the running server's cache read it now."""
    db.run("INSERT INTO settings_kv (key, value, updated_by) VALUES (%s, %s::jsonb, 'pixel') "
           "ON CONFLICT (key) DO UPDATE SET value = excluded.value", key, json.dumps(value))
    live.call(live.env.settings.reload())


def plant_capacity(db, account, pool, tz, left):
    """A capacity row on the pool's day as the server reckons it: the DB clock in the pool's time zone, which is not
    the frozen browser clock (so an anchor written from NOW would read as yesterday's and roll back to the budget)."""
    db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) "
           "VALUES (%s, %s, %s, 'reported', (clock_timestamp() AT TIME ZONE %s)::date)", account, pool, left, tz)


def shell_seed(db, live):
    """What the top bar counts: 4 jobs in progress, 7 of 8 accounts usable, 1 open alert group."""
    set_setting(db, live, "alert_accounts_low", ALERT_ACCOUNTS_LOW)
    keys = [new_key(db, wallets=WALLETS) for _ in KEYS]
    for label, state, asleep, in_use in ACCOUNTS:
        account = new_account(db, label=label, state=state, checked=True, provider=PROVIDER)
        if asleep:
            db.run("UPDATE accounts SET cooldown_until = clock_timestamp() + interval '35 min' WHERE id = %s",
                   account)
        if in_use:
            db.run(IN_USE_LEASE, account)
    for index in ALERT_ROWS:
        plant_unclear(db, keys[index], ALERT_TEXT, provider=PROVIDER)
    for key, label in zip(keys, KEYS, strict=True):
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, key)
    return {"keys": dict(zip(KEYS, keys, strict=True))}
