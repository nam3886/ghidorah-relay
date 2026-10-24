"""An account's history for the admin detail: its health by hour and its newest errors (worker writes only).

`record_health` stores the account's health as it stands after a check or a job outcome, in the UTC hour it happens:
the worst health of the hour wins (0 usable, 1 asleep, 2 dead or its proxy down). A switched-off account is not
recorded. It must be the LAST statement of its transaction: two outcomes of one account in one hour meet on the same
row, and one that waits for that row then waits for a transaction with no lock left to take. `record_error` keeps an
error with the job it came from; it runs where the account row is already locked, so an account's errors are written
one at a time. Both prune as they write, so the tables stay bounded: HEALTH_DAYS of hours and KEEP_ERRORS errors per
account.
"""

from psycopg import AsyncConnection

from relay_core.redact import redact_log

HEALTH_DAYS = 15            # one more than the detail shows, so its first day is whole in every time zone
KEEP_ERRORS = 20
USABLE, ASLEEP, DOWN = 0, 1, 2


async def record_health(conn: AsyncConnection, account_id) -> None:
    await conn.execute(
        "DELETE FROM account_health WHERE account_id = %(a)s "
        "AND hour < clock_timestamp() - make_interval(days => %(d)s)", {"a": account_id, "d": HEALTH_DAYS})
    await conn.execute(
        "INSERT INTO account_health (account_id, hour, worst) "
        "SELECT a.id, date_trunc('hour', clock_timestamp(), 'UTC'), CASE "
        "WHEN a.state = 'dead' OR (a.proxy_host IS NOT NULL AND a.proxy_ok IS FALSE) THEN %(down)s "
        "WHEN a.cooldown_until > clock_timestamp() OR EXISTS (SELECT 1 FROM account_pools p "
        "WHERE p.account_id = a.id AND p.rest_until > clock_timestamp()) THEN %(asleep)s ELSE %(usable)s END "
        "FROM accounts a WHERE a.id = %(a)s AND a.state <> 'disabled' "
        "ON CONFLICT (account_id, hour) DO UPDATE SET worst = GREATEST(account_health.worst, excluded.worst)",
        {"a": account_id, "down": DOWN, "asleep": ASLEEP, "usable": USABLE})


async def record_error(conn: AsyncConnection, account_id, code: str, job_id=None) -> None:
    await conn.execute("INSERT INTO account_errors (account_id, code, job_id) VALUES (%s, %s, %s)",
                       (account_id, redact_log(code)[:200], job_id))
    await conn.execute(
        "DELETE FROM account_errors WHERE account_id = %(a)s AND id <= (SELECT id FROM account_errors "
        "WHERE account_id = %(a)s ORDER BY id DESC OFFSET %(n)s LIMIT 1)", {"a": account_id, "n": KEEP_ERRORS})
