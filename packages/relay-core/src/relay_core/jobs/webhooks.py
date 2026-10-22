"""Webhook sender (invariant I16). Plain functions: the api process calls `deliver_due` on a
tick (wired in a later phase); nothing here starts a task or a loop.

One pass: claim up to 5 due deliveries and commit (the claim counts the attempt), POST them concurrently through the
SSRF-checked client (https only, no redirect, one total deadline) with no connection held, then mark each result.
The mark only applies while `callback_for` still names the event that was sent: a Re-check during the send starts a
newer terminal event, and the old send's result is dropped so the newer one is delivered.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg_pool import AsyncConnectionPool

from relay_core import failpoints, net
from relay_core.db import tx
from relay_core.jobs.events import add_event
from relay_core.redact import redact_log

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
BATCH = 5
MAX_ATTEMPTS = 3
RETRY_AFTER_S = (30, 300)        # after the 1st failed attempt, after the 2nd; the 3rd failure is final
CLAIM_HOLD_S = 60                # a claimed delivery is not due again until the send could not still be running
SEND_TIMEOUT_S = 10.0
MAX_ANSWER_BYTES = 64 * 1024
# A refused URL that will never become valid is final; a name that did not resolve may be a passing DNS fault.
_RETRYABLE_REFUSALS = frozenset({"unresolvable"})

_CLAIM = """
UPDATE jobs SET callback_attempts = callback_attempts + 1,
       callback_next_at = clock_timestamp() + make_interval(secs => %(hold)s)
 WHERE id IN (SELECT id FROM jobs
               WHERE callback_status = 'pending' AND callback_attempts < %(max)s
                 AND callback_next_at <= clock_timestamp()
               ORDER BY callback_next_at LIMIT %(limit)s FOR UPDATE SKIP LOCKED)
RETURNING id, callback_url, status, callback_for, callback_attempts
"""
# A claim that died before its mark leaves a delivery at its last attempt: it ends as failed once its hold passed.
_EXHAUSTED = """
UPDATE jobs SET callback_status = 'failed', callback_last = 'no result was recorded for the last attempt'
 WHERE callback_status = 'pending' AND callback_attempts >= %s AND callback_next_at <= clock_timestamp()
"""
_MARK = """
UPDATE jobs SET callback_status = %(status)s, callback_last = %(last)s,
       callback_next_at = CASE WHEN %(status)s = 'pending' THEN clock_timestamp() + make_interval(secs => %(after)s)
                               ELSE NULL END
 WHERE id = %(id)s AND callback_for = %(for)s
"""


@dataclass(frozen=True)
class Delivery:
    job_id: object
    url: str
    status: str                  # the job's terminal status, `done` or `failed`
    callback_for: datetime
    attempt: int                 # 1-based, counted at claim

    @property
    def event(self) -> str:
        return "job." + self.status

    @property
    def event_id(self) -> str:
        """Stable for one terminal event across attempts and duplicate deliveries; a newer terminal event of the
        same job has another `callback_for`."""
        return "%s:%d" % (self.job_id, (self.callback_for - _EPOCH) // timedelta(milliseconds=1))


@dataclass(frozen=True)
class Outcome:
    job_id: object
    result: str                  # sent | retry | failed | dropped (the mark did not apply: a newer event exists)


async def claim_due(db: AsyncConnectionPool, *, limit: int = BATCH) -> list[Delivery]:
    """At most `limit` due deliveries, each claimed by exactly one caller (SKIP LOCKED) and counted as an attempt."""
    async with tx(db) as conn:
        await conn.execute(_EXHAUSTED, (MAX_ATTEMPTS,))
        params = {"hold": CLAIM_HOLD_S, "max": MAX_ATTEMPTS, "limit": limit}
        rows = await (await conn.execute(_CLAIM, params)).fetchall()
        await failpoints.hit("webhook.claim.locked")
    return [Delivery(*r) for r in rows]


async def _post(d: Delivery, resolver, transport, timeout_s: float) -> tuple[str, str]:
    """(result, text for callback_last): result is sent, retry or failed. No connection is held here."""
    body = json.dumps({"event": d.event, "event_id": d.event_id, "job_id": str(d.job_id), "status": d.status})
    headers = {"Content-Type": "application/json", "User-Agent": "relay/1", "X-Relay-Event": d.event,
               "X-Relay-Event-Id": d.event_id}
    try:
        r = await net.safe_post(d.url, body.encode(), resolver=resolver, transport=transport, headers=headers,
                                timeout_s=timeout_s, max_bytes=MAX_ANSWER_BYTES)
    except net.UnsafeUrl as exc:
        return ("retry" if exc.reason in _RETRYABLE_REFUSALS else "failed"), "error: refused (%s)" % exc.reason
    except net.NetError as exc:
        return "retry", "error: %s" % exc.reason
    if 200 <= r.status_code < 300:
        return "sent", "HTTP %d" % r.status_code
    text = "HTTP %d: %s" % (r.status_code, redact_log(r.content.decode("utf-8", "replace")))
    return ("failed" if 400 <= r.status_code < 500 else "retry"), text


async def _mark(db: AsyncConnectionPool, d: Delivery, result: str, text: str) -> str:
    if result == "retry" and d.attempt >= MAX_ATTEMPTS:
        result = "failed"
    status = {"sent": "sent", "failed": "failed", "retry": "pending"}[result]
    after = RETRY_AFTER_S[min(d.attempt, len(RETRY_AFTER_S)) - 1]
    last = redact_log(text)
    async with tx(db) as conn:
        cur = await conn.execute(_MARK, {"status": status, "last": last, "after": after, "id": d.job_id,
                                         "for": d.callback_for})
        if cur.rowcount != 1:
            return "dropped"
        await add_event(conn, d.job_id, "system", "webhook", "webhook %s (attempt %d): %s" % (result, d.attempt, last))
    return result


async def deliver_due(db: AsyncConnectionPool, *, limit: int = BATCH, resolver: net.Resolver = net.system_resolver,
                      transport=None, timeout_s: float = SEND_TIMEOUT_S) -> list[Outcome]:
    """One sender pass; returns what happened to each claimed delivery. `resolver` and `transport` are for tests."""
    batch = await claim_due(db, limit=limit)
    posted = await asyncio.gather(*(_post(d, resolver, transport, timeout_s) for d in batch), return_exceptions=True)
    out = []
    for d, answer in zip(batch, posted, strict=True):
        result, text = ("retry", "error: internal") if isinstance(answer, BaseException) else answer
        out.append(Outcome(d.job_id, await _mark(db, d, result, text)))
    return out


async def resend_failed(db: AsyncConnectionPool, job_id) -> bool:
    """Admin: deliver a `failed` webhook again, from attempt 0. The event is the same one, so its `event_id` is too.
    False when the job has no failed webhook."""
    async with tx(db) as conn:
        cur = await conn.execute(
            "UPDATE jobs SET callback_status = 'pending', callback_attempts = 0, callback_next_at = clock_timestamp() "
            "WHERE id = %s AND callback_status = 'failed' AND callback_url IS NOT NULL AND callback_for IS NOT NULL",
            (job_id,))
        if cur.rowcount != 1:
            return False
        await add_event(conn, job_id, "system", "webhook", "webhook resend requested")
    return True
