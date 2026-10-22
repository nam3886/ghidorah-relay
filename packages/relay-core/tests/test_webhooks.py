"""Webhook sender (invariant I16): claim, send, mark."""

import asyncio

import httpx
from hookkit import Receiver, hook, hook_job, make_due, resolver
from jobkit import run

from relay_core import failpoints
from relay_core.jobs import webhooks
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/jobs/webhooks.py"

STATUS_RULE = '("failed" if 400 <= r.status_code < 500 else "retry")'
EVENT_ID = 'return "%s:%d" % (self.job_id, (self.callback_for - _EPOCH) // timedelta(milliseconds=1))'
EVENT_ID_PER_ATTEMPT = 'return "%s:%d" % (self.job_id, self.attempt)'
MARK_GUARD = "WHERE id = %(id)s AND callback_for = %(for)s"
LOCK = "ORDER BY callback_next_at LIMIT %(limit)s FOR UPDATE SKIP LOCKED)"
NO_LOCK = "ORDER BY callback_next_at LIMIT %(limit)s)"


async def send(p, receiver, **kw):
    return await webhooks.deliver_due(p, resolver=resolver, transport=receiver.transport, **kw)


def seconds_to_next(db, job_id) -> float:
    return db.one("SELECT extract(epoch FROM callback_next_at - clock_timestamp()) FROM jobs WHERE id = %s",
                  job_id)[0]


def test_2xx_is_delivered_with_a_thin_body(db):
    job_id = hook_job(db, db.key())
    rx = Receiver(204)
    out = run(db, lambda p: send(p, rx))
    assert [(o.job_id, o.result) for o in out] == [(job_id, "sent")]
    body, = rx.bodies()
    assert set(body) == {"event", "event_id", "job_id", "status"}
    assert body["event"] == "job.failed" and body["status"] == "failed" and body["job_id"] == str(job_id)
    req = rx.seen[0]
    assert req.headers["x-relay-event-id"] == body["event_id"] and req.headers["x-relay-event"] == "job.failed"
    assert req.headers["user-agent"] == "relay/1"
    assert hook(db, job_id) == ("sent", 1, "HTTP 204")
    assert db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND stage = 'webhook'", job_id)[0] == 1
    assert run(db, lambda p: send(p, rx)) == []                 # a sent webhook is never claimed again


@mutation(SRC, STATUS_RULE, '"retry"', expect="assert ['retry'] == ['failed']")
def test_4xx_is_final(db):
    job_id = hook_job(db, db.key())
    rx = Receiver(410)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["failed"]
    assert hook(db, job_id)[:2] == ("failed", 1)
    make_due(db, job_id)
    assert run(db, lambda p: send(p, rx)) == [] and len(rx.seen) == 1


def test_failures_retry_at_30s_then_300s_then_fail(db):
    job_id = hook_job(db, db.key())
    rx = Receiver(503, httpx.ConnectTimeout("slow"), 500)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["retry"]
    assert hook(db, job_id)[:2] == ("pending", 1) and 25 < seconds_to_next(db, job_id) <= 30
    assert run(db, lambda p: send(p, rx)) == []                  # not due yet
    make_due(db, job_id)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["retry"]
    assert hook(db, job_id)[:2] == ("pending", 2) and 295 < seconds_to_next(db, job_id) <= 300
    make_due(db, job_id)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["failed"]
    assert hook(db, job_id)[:2] == ("failed", 3)
    make_due(db, job_id)
    assert run(db, lambda p: send(p, rx)) == [] and len(rx.seen) == 3


@mutation(SRC, EVENT_ID, EVENT_ID_PER_ATTEMPT, expect="first[\"event_id\"] == second[\"event_id\"]")
def test_event_id_is_stable_across_attempts(db):
    job_id = hook_job(db, db.key())
    rx = Receiver(500, 200)
    run(db, lambda p: send(p, rx))
    make_due(db, job_id)
    run(db, lambda p: send(p, rx))
    first, second = rx.bodies()
    assert first["event_id"] == second["event_id"]
    ms = db.one("SELECT floor(extract(epoch FROM callback_for) * 1000)::bigint FROM jobs WHERE id = %s", job_id)[0]
    assert first["event_id"] == "%s:%d" % (job_id, ms)


def test_recheck_during_a_send_drops_the_old_mark_and_delivers_the_newer_event(db):
    job_id = hook_job(db, db.key())

    def recheck_and_fail_again(_request):
        db.run("UPDATE jobs SET callback_for = clock_timestamp(), callback_status = 'pending', "
               "callback_attempts = 0, callback_next_at = clock_timestamp() WHERE id = %s", job_id)

    rx = Receiver(200, on_request=recheck_and_fail_again)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["dropped"]
    assert hook(db, job_id)[:2] == ("pending", 0)
    rx.on_request = None
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["sent"]
    old, new = rx.bodies()
    assert old["event_id"] != new["event_id"] and hook(db, job_id)[0] == "sent"


@mutation(SRC, MARK_GUARD, "WHERE id = %(id)s", expect="assert ['sent'] == ['dropped']")
def test_a_send_of_a_cleared_event_does_not_mark_the_job(db):
    job_id = hook_job(db, db.key())

    def recheck(_request):                  # the job went back to waiting: Re-check cleared the callback fields
        db.run("UPDATE jobs SET status = 'waiting', external_ref = 'ref-1', "
               "callback_status = NULL, callback_attempts = 0, callback_next_at = NULL, "
               "callback_for = NULL WHERE id = %s", job_id)

    rx = Receiver(200, on_request=recheck)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["dropped"]
    assert hook(db, job_id)[0] is None


def test_a_batch_is_at_most_five(db):
    k = db.key()
    ids = {hook_job(db, k) for _ in range(12)}
    rx = Receiver(200)
    got = run(db, lambda p: send(p, rx))
    assert len(got) == 5 and {o.job_id for o in got} <= ids
    assert len(run(db, lambda p: send(p, rx))) == 5


@mutation(SRC, LOCK, NO_LOCK, expect="canceling statement due to lock timeout")
def test_two_claimers_never_take_one_delivery(db):
    k = db.key()
    for _ in range(12):
        hook_job(db, k)

    async def scenario(p):
        gate, calls = asyncio.Event(), []

        async def hold_first():
            calls.append(1)
            if len(calls) == 1:
                await asyncio.wait_for(gate.wait(), 5)

        failpoints.arm("webhook.claim.locked", hold_first)
        try:
            first = asyncio.create_task(webhooks.claim_due(p))
            while not calls:
                await asyncio.sleep(0.01)
            second = await webhooks.claim_due(p)             # while the first still holds its rows
            gate.set()
            return await first, second
        finally:
            failpoints.disarm()

    a, b = run(db, scenario)
    assert len(a) == 5 and len(b) == 5
    assert not {d.job_id for d in a} & {d.job_id for d in b}


def test_concurrent_senders_post_each_delivery_once(db):
    k = db.key()
    for _ in range(12):
        hook_job(db, k)
    rx = Receiver(200)

    async def scenario(p):
        return await asyncio.gather(send(p, rx), send(p, rx), send(p, rx))

    runs = run(db, scenario)
    assert all(len(r) <= 5 for r in runs)
    posted = [b["job_id"] for b in rx.bodies()]
    assert len(posted) == len(set(posted)) == sum(len(r) for r in runs)


def test_ssrf_refusals_are_final_and_send_nothing(db):
    k = db.key()
    http_job = hook_job(db, k, url="http://hooks.example.test/in")
    private_job = hook_job(db, k, url="https://evil.test/in")
    rx = Receiver(200)
    assert sorted(o.result for o in run(db, lambda p: send(p, rx))) == ["failed", "failed"]
    assert rx.seen == []
    assert "scheme" in hook(db, http_job)[2] and "private_address" in hook(db, private_job)[2]


def test_a_redirect_is_not_followed(db):
    job_id = hook_job(db, db.key())
    rx = Receiver(httpx.Response(302, headers={"location": "https://other.example.test/x"}))
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["retry"]
    assert len(rx.seen) == 1 and hook(db, job_id)[2] == "HTTP 302: "


def test_a_long_answer_is_stored_as_omitted_and_its_secret_never_lands(db):
    job_id = hook_job(db, db.key())
    secret = "sk-" + "A1b2C3d4" * 8
    text = "x" * 2500 + secret
    rx = Receiver(httpx.Response(500, content=text.encode()))
    run(db, lambda p: send(p, rx))
    assert hook(db, job_id)[2] == "HTTP 500: [omitted len=%d]" % len(text)
    stored = " ".join(r[0] for r in db.all("SELECT text FROM job_events WHERE job_id = %s", job_id))
    assert secret not in stored and "[omitted len=" in stored


def test_admin_resend_of_a_failed_webhook_keeps_its_event_id(db):
    job_id = hook_job(db, db.key())
    rx = Receiver(404, 200)
    run(db, lambda p: send(p, rx))
    assert hook(db, job_id)[0] == "failed"
    assert run(db, lambda p: webhooks.resend_failed(p, job_id)) is True
    assert hook(db, job_id)[:2] == ("pending", 0)
    assert [o.result for o in run(db, lambda p: send(p, rx))] == ["sent"]
    first, second = rx.bodies()
    assert first["event_id"] == second["event_id"]
    assert run(db, lambda p: webhooks.resend_failed(p, job_id)) is False         # only a failed one is resent


def test_a_claim_that_died_before_its_mark_ends_as_failed(db):
    job_id = hook_job(db, db.key())
    db.run("UPDATE jobs SET callback_attempts = 3 WHERE id = %s", job_id)
    assert run(db, lambda p: send(p, Receiver(200))) == []
    assert hook(db, job_id)[0] == "failed"
