"""The global invariant checker (contracts §14.3): run after every database test and by the daily api sweep.

`violations(conn)` returns one readable line per broken invariant; an empty list means the data is consistent.
This is the skeleton: it covers the money invariants (I4, I25). Later phases add their rules here.
"""

from psycopg import Connection

# Per wallet: used_total = sum(charge) - sum(refund) + sum(adjust). used_day is the same sum restricted to the
# wallet's day_anchor: a refund counts there only when it was made on the day of the charge it undoes, the rule
# billing.refund applies.
_LEDGER_VS_COUNTERS = """
SELECT w.key_id, w.provider, w.kind, w.used_total, w.used_day,
       COALESCE(sum(CASE l.entry WHEN 'charge' THEN l.amount WHEN 'refund' THEN -l.amount ELSE l.amount END), 0),
       COALESCE(sum(CASE WHEN l.billing_day = w.day_anchor THEN
                     CASE l.entry WHEN 'charge' THEN l.amount
                                  WHEN 'refund' THEN CASE WHEN c.billing_day = l.billing_day THEN -l.amount ELSE 0 END
                                  ELSE l.amount END
                   ELSE 0 END), 0)
  FROM key_wallets w
  LEFT JOIN ledger l ON l.key_id = w.key_id AND l.provider = w.provider AND l.kind = w.kind
  LEFT JOIN ledger c ON c.id = l.refund_of
 GROUP BY w.key_id, w.provider, w.kind, w.used_total, w.used_day, w.day_anchor
"""

# A refund undoes a charge of the same key, wallet and amount; a refund is never refunded (I4).
_BAD_REFUNDS = """
SELECT r.id, r.refund_of FROM ledger r LEFT JOIN ledger c ON c.id = r.refund_of
 WHERE r.entry = 'refund'
   AND (c.id IS NULL OR c.entry <> 'charge' OR (c.key_id, c.provider, c.kind, c.amount)
        IS DISTINCT FROM (r.key_id, r.provider, r.kind, r.amount))
"""


def violations(conn: Connection) -> list[str]:
    found = []
    for key, provider, kind, used_total, used_day, ledger_total, ledger_day in conn.execute(_LEDGER_VS_COUNTERS):
        if used_total != ledger_total:
            found.append("I25 wallet %s %s/%s: used_total %s, ledger says %s"
                         % (key, provider, kind, used_total, ledger_total))
        if used_day != ledger_day:
            found.append("I25 wallet %s %s/%s: used_day %s, ledger says %s"
                         % (key, provider, kind, used_day, ledger_day))
    for refund_id, charge_id in conn.execute(_BAD_REFUNDS):
        found.append("I4 refund row %s does not undo a matching charge (refund_of %s)" % (refund_id, charge_id))
    for (charge_id, n) in conn.execute("SELECT refund_of, count(*) FROM ledger WHERE refund_of IS NOT NULL "
                                       "GROUP BY refund_of HAVING count(*) > 1"):
        found.append("I4 charge %s has %s refund rows" % (charge_id, n))
    return found
