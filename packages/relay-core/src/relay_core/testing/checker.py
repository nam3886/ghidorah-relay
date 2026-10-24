"""The global invariant checker: run after every test and by the daily api sweep.

`violations(conn)` returns one readable line per broken invariant; an empty list means the data is consistent.
It covers the money invariants (I3, I4, I8, I21, I25), the lease and command rules (I14), the job rules (I1, I7, I9,
I11, I13, I16, I23), the media rules (I11, I12) and stored text (I24).

`violations(conn)` is what must hold after ANY test, including one that fabricates rows with plain SQL.
`violations(conn, strict=True)` adds the rules that only the platform's own writers promise (a terminal job has
finished_at, a failed job has an error code, ...): the soak, the crash-window tests and the daily sweep use it.

Every rule is a state that must hold when a test ends. A state that the reaper, the poller or a retry would repair
later is NOT a rule here unless the repair belongs to the same transaction that created it, so a violation is always
a bug and never just a moment in time.
"""

from psycopg import Connection

# Per wallet: used_total = sum(charge) - sum(refund) + sum(adjust). used_day is the same sum restricted to the
# wallet's day_anchor: a refund counts there only when it was made on the day of the charge it undoes, the rule
# billing.refund applies.
_LEDGER_VS_COUNTERS = """
SELECT w.key_id, w.scope, w.used_total, w.used_day,
       COALESCE(sum(CASE l.entry WHEN 'charge' THEN l.amount WHEN 'refund' THEN -l.amount ELSE l.amount END), 0),
       COALESCE(sum(CASE WHEN l.billing_day = w.day_anchor THEN
                     CASE l.entry WHEN 'charge' THEN l.amount
                                  WHEN 'refund' THEN CASE WHEN c.billing_day = l.billing_day THEN -l.amount ELSE 0 END
                                  ELSE l.amount END
                   ELSE 0 END), 0)
  FROM key_wallets w
  LEFT JOIN ledger l ON l.key_id = w.key_id AND l.scope = w.scope
  LEFT JOIN ledger c ON c.id = l.refund_of
 GROUP BY w.key_id, w.scope, w.used_total, w.used_day, w.day_anchor
"""

# A refund undoes a charge of the same key, wallet and amount; a refund is never refunded (I4).
_BAD_REFUNDS = """
SELECT r.id, r.refund_of FROM ledger r LEFT JOIN ledger c ON c.id = r.refund_of
 WHERE r.entry = 'refund'
   AND (c.id IS NULL OR c.entry <> 'charge' OR (c.key_id, c.scope, c.amount)
        IS DISTINCT FROM (r.key_id, r.scope, r.amount))
"""


def violations(conn: Connection, *, strict: bool = False) -> list[str]:
    found = []
    for key, scope, used_total, used_day, ledger_total, ledger_day in conn.execute(_LEDGER_VS_COUNTERS):
        if used_total != ledger_total:
            found.append("I25 wallet %s %s: used_total %s, ledger says %s" % (key, scope, used_total, ledger_total))
        if used_day != ledger_day:
            found.append("I25 wallet %s %s: used_day %s, ledger says %s" % (key, scope, used_day, ledger_day))
    for refund_id, charge_id in conn.execute(_BAD_REFUNDS):
        found.append("I4 refund row %s does not undo a matching charge (refund_of %s)" % (refund_id, charge_id))
    for (charge_id, n) in conn.execute("SELECT refund_of, count(*) FROM ledger WHERE refund_of IS NOT NULL "
                                       "GROUP BY refund_of HAVING count(*) > 1"):
        found.append("I4 charge %s has %s refund rows" % (charge_id, n))
    found += _lease_violations(conn)
    found += job_violations(conn, strict=strict)
    return found


# I14: no lease runs past its hard bound (a NULL bound is allowed only for a later poll-held job lease, and
# `leases.acquire` always sets one); a live job lease belongs to a job that is still submitting (the lease is
# released when the job leaves that state); a live reconcile lease belongs to a job that is still unknown (the
# result application deletes the lease in its own transaction). An EXPIRED lease on a job that moved on is a state
# the reaper repairs, so it is not a violation.
_LEASES = """
SELECT l.id, l.purpose, l.until, l.hard_until, l.until > clock_timestamp(), j.status, l.job_id
  FROM leases l LEFT JOIN jobs j ON j.id = l.job_id
"""


def _lease_violations(conn: Connection) -> list[str]:
    found = []
    for lease_id, purpose, until, hard_until, live, job_status, job_id in conn.execute(_LEASES):
        if hard_until is not None and until > hard_until:
            found.append("I14 lease %s (%s) runs past its hard_until" % (lease_id, purpose))
        if live and purpose == "job" and job_status != "submitting":
            found.append("I14 live job lease %s on a job that is %s" % (lease_id, job_status))
        if live and purpose == "reconcile" and job_id is not None and job_status != "unknown":
            found.append("I14 live reconcile lease %s on a job that is %s" % (lease_id, job_status))
    return found


# Job rules: each query returns the ids of the jobs that break one rule.
_TERMINAL = "('done', 'failed')"
_JOB_RULES = (
    ("I9", "a queued job still has sent_at (a requeue clears it)",
     "SELECT id FROM jobs WHERE status = 'queued' AND sent_at IS NOT NULL"),
    ("I21", "a failed job whose key charge is not refunded (on the points wallet or on its provider's cap)",
     "SELECT job_id FROM ledger WHERE job_id IN (SELECT id FROM jobs WHERE status = 'failed') "
     "GROUP BY job_id HAVING sum(CASE entry WHEN 'charge' THEN amount WHEN 'refund' THEN -amount ELSE 0 END) <> 0"),
    ("I7", "an account charge for a job whose order was never proven taken",
     "SELECT j.id FROM jobs j WHERE j.taken_at IS NULL AND j.status IN ('waiting', 'fetching', 'done', 'unknown') "
     "AND EXISTS (SELECT 1 FROM account_charges c WHERE c.job_id = j.id AND c.refunded_at IS NULL)"),
    ("I7", "facts stored for a job whose order was never proven taken",
     "SELECT id FROM jobs WHERE taken_at IS NULL AND (upstream_price IS NOT NULL OR upstream_model IS NOT NULL)"),
    ("I7", "a taken job of a pool without its account charge",
     "SELECT id FROM jobs WHERE taken_at IS NOT NULL AND pool IS NOT NULL AND account_cost > 0 AND NOT EXISTS "
     "(SELECT 1 FROM account_charges c WHERE c.job_id = jobs.id)"),
    ("I11", "a done job without an output media row",
     "SELECT id FROM jobs WHERE status = 'done' AND NOT EXISTS "
     "(SELECT 1 FROM job_media m WHERE m.job_id = jobs.id AND m.role = 'output')"),
    ("I11", "a done job with fewer output rows than outputs recorded",
     "SELECT id FROM jobs WHERE status = 'done' AND jsonb_typeof(provider_state -> 'outputs') = 'array' "
     "AND jsonb_array_length(provider_state -> 'outputs') > "
     "(SELECT count(*) FROM job_media m WHERE m.job_id = jobs.id AND m.role = 'output')"),
    ("I1", "a waiting job without an upstream reference",
     "SELECT id FROM jobs WHERE status = 'waiting' AND external_ref IS NULL"),
    ("I8", "a job whose net charge on a scope is neither 0 nor its price, or differs between its points wallet and "
     "its provider's cap",
     "SELECT job_id FROM (SELECT j.id AS job_id, j.price, "
     "sum(CASE l.entry WHEN 'charge' THEN l.amount WHEN 'refund' THEN -l.amount ELSE 0 END) AS net "
     "FROM jobs j JOIN ledger l ON l.job_id = j.id WHERE j.price > 0 GROUP BY j.id, j.price, l.scope) s "
     "GROUP BY job_id, price HAVING bool_or(net NOT IN (0, price)) OR count(DISTINCT net) > 1"),
    ("I23", "a poll token on a job that is neither waiting nor fetching",
     "SELECT id FROM jobs WHERE poll_token IS NOT NULL AND status NOT IN ('waiting', 'fetching')"),
    ("I16", "a webhook with more than 3 attempts",
     "SELECT id FROM jobs WHERE callback_attempts > 3"),
    ("I16", "a terminal job with a webhook url and no webhook status",
     "SELECT id FROM jobs WHERE status IN %s AND callback_url IS NOT NULL AND callback_status IS NULL" % _TERMINAL),
)

# Strict tier: what the REAL writers always leave. Not enforced after every test, because plenty of tests fabricate
# rows with plain SQL (a `failed` job with no error code, a charge of 1 on a job whose account_cost is 0); the soak, the
# crash-window tests and the daily sweep run it on data the platform wrote itself.
_STRICT_JOB_RULES = (
    ("I1", "an unknown job that was never sent (unknown means sent)",
     "SELECT id FROM jobs WHERE status = 'unknown' AND sent_at IS NULL"),
    ("I1", "a submitting job with no lease row, so no reaper can ever move it",
     "SELECT id FROM jobs WHERE status = 'submitting' "
     "AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.job_id = jobs.id)"),
    ("I3", "a priced job with no charge row in the ledger",
     "SELECT id FROM jobs WHERE price > 0 AND NOT EXISTS "
     "(SELECT 1 FROM ledger l WHERE l.job_id = jobs.id AND l.scope = '*' AND l.entry = 'charge')"),
    ("I7", "an account charge that is not the job's account_cost",
     "SELECT j.id FROM jobs j JOIN account_charges c ON c.job_id = j.id WHERE c.amount <> j.account_cost"),
    ("I13", "a terminal job without finished_at",
     "SELECT id FROM jobs WHERE status IN %s AND finished_at IS NULL" % _TERMINAL),
    ("I13", "a job that is not terminal still has finished_at",
     "SELECT id FROM jobs WHERE status NOT IN %s AND finished_at IS NOT NULL" % _TERMINAL),
    ("I13", "a failed job without an error code",
     "SELECT id FROM jobs WHERE status = 'failed' AND error_code IS NULL"),
    ("I13", "a terminal job still holds a poll token or a next_poll_at",
     "SELECT id FROM jobs WHERE status IN %s AND (poll_token IS NOT NULL OR next_poll_at IS NOT NULL)" % _TERMINAL),
    ("I14", "a waiting or fetching job with no next_poll_at, which no poller would ever claim",
     "SELECT id FROM jobs WHERE status IN ('waiting', 'fetching') AND next_poll_at IS NULL"),
    ("I16", "a pending webhook without its event time, url or next attempt",
     "SELECT id FROM jobs WHERE callback_status = 'pending' "
     "AND (callback_for IS NULL OR callback_url IS NULL OR callback_next_at IS NULL)"),
)

# Rules over other tables: (rule, text, sql returning one id per offender).
_ROW_RULES = (
    ("I14", "a running command whose lease is gone (the reaper fails it in the transaction that deletes the lease)",
     "SELECT id FROM commands c WHERE status = 'running' AND lease_id IS NOT NULL "
     "AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.id = c.lease_id)"),
    ("I14", "a finished command without finished_at",
     "SELECT id FROM commands WHERE status IN ('done', 'failed') AND finished_at IS NULL"),
    ("I12", "a debug media row with a share token (debug media are never public)",
     "SELECT job_id FROM job_media WHERE role = 'debug' AND share_token IS NOT NULL"),
    ("I12", "an input media link with no expiry",
     "SELECT job_id FROM job_media WHERE role = 'input' AND share_token IS NOT NULL AND share_expires_at IS NULL"),
    # I24: `redact_log` turns any string over 2,000 characters into a short marker, so a stored value much longer
    # than that skipped the writer's redaction (a label such as "HTTP 500: " may precede a 2,000-character text).
    ("I24", "a job error or webhook answer over 2100 characters was stored without redact_log",
     "SELECT id FROM jobs WHERE length(error) > 2100 OR length(callback_last) > 2100"),
    ("I24", "an account error over 2100 characters was stored without redact_log",
     "SELECT id FROM accounts WHERE length(last_error) > 2100 OR length(state_reason) > 2100"),
    ("I24", "an event text over 2100 characters was stored without redact_log",
     "SELECT id FROM job_events WHERE length(text) > 2100"),
)


def job_violations(conn: Connection, *, strict: bool = False) -> list[str]:
    found = []
    for rule, text, sql in _JOB_RULES + (_STRICT_JOB_RULES if strict else ()):
        for (job_id,) in conn.execute(sql):
            found.append("%s job %s: %s" % (rule, job_id, text))
    for rule, text, sql in _ROW_RULES:
        for (row_id,) in conn.execute(sql):
            found.append("%s row %s: %s" % (rule, row_id, text))
    return found
