"""The api process's background loops: the webhook sender against a local receiver over HTTP
(I16), the housekeeping sweeps and the daily ledger check."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta

import httpx
from apikit import run_app
from hookkit import DNS, PUBLIC, URL, hook, hook_job, make_due, resolver
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from relay_core import storage
from relay_core.admin import users
from relay_core.api import runners
from relay_core.jobs import sweeps
from relay_core.testing.mutation import mutation

RUNNERS = "packages/relay-core/src/relay_core/api/runners.py"
WEBHOOKS = "packages/relay-core/src/relay_core/jobs/webhooks.py"
NET = "packages/relay-core/src/relay_core/net.py"
FAST = {"webhook_every_s": 0.05, "housekeeping_every_s": 0.05, "ledger_every_s": 0.05}
SLOW = {"webhook_every_s": 3600, "housekeeping_every_s": 3600, "ledger_every_s": 3600}


class Receiver:
    """A real HTTP receiver (a Starlette app behind httpx's ASGI transport) answering from a script; the last answer
    repeats. It records every request it gets."""

    def __init__(self, *script: int, location: str | None = None):
        self.script, self.location, self.seen = list(script) or [204], location, []
        self.transport = httpx.ASGITransport(app=Starlette(routes=[Route("/{path:path}", self.hook,
                                                                         methods=["POST", "GET"])]))

    async def hook(self, request: Request) -> Response:
        self.seen.append((request.url.path, dict(request.headers), json.loads(await request.body() or b"null")))
        code = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return Response(status_code=code, headers={"Location": self.location} if self.location else {})


async def until(check, timeout_s: float = 15.0):
    """Poll `check()` until it is truthy; returns its value, or None on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if value := check():
            return value
        await asyncio.sleep(0.02)
    return None


def failed_hook(db, url=URL):
    job = hook_job(db, db.key(), url=url)
    db.run("UPDATE jobs SET error_code = 'render.deadline', error = 'the render did not finish in time' "
           "WHERE id = %s", job)
    return job


# ---- webhook sender over HTTP (I16) ------------------------------------------------------------------------------
@mutation(WEBHOOKS, '"status": d.status})', '"status": d.status, "refunded": False})', expect="a thin body")
def test_the_sender_posts_a_thin_event_to_the_receiver_and_marks_it_sent(db):
    async def scenario(env):
        job = failed_hook(db)
        rx = Receiver(204)
        rn = runners.start_runners(env.pool, env.settings, env.store, **SLOW, resolver=resolver,
                                   transport=rx.transport)
        try:
            await until(lambda: hook(db, job)[0] == "sent")
            await asyncio.sleep(0.2)
        finally:
            await rn.stop()
        return job, rx

    job, rx = run_app(db, scenario)
    assert hook(db, job)[:2] == ("sent", 1)
    path, headers, body = rx.seen[0]
    assert len(rx.seen) == 1, "a sent webhook is posted once"
    assert path == "/in" and headers["user-agent"] == "relay/1" and headers["x-relay-event"] == "job.failed"
    assert set(body) == {"event", "event_id", "job_id", "status"}, "a thin body: %r" % body
    assert body["job_id"] == str(job)
    assert headers["x-relay-event-id"] == body["event_id"]


@mutation(RUNNERS, "return len(await webhooks.deliver_due(pool, resolver=resolver, transport=transport))"
          " >= webhooks.BATCH", "return False", expect="a backlog is drained without waiting for the next tick")
def test_a_backlog_is_drained_without_waiting_for_the_next_tick(db):
    async def scenario(env):
        jobs = [failed_hook(db) for _ in range(7)]            # more than one batch
        rx = Receiver(204)
        rn = runners.start_runners(env.pool, env.settings, env.store, **{**SLOW, "webhook_every_s": 3600},
                                   resolver=resolver, transport=rx.transport)
        try:
            done = await until(lambda: all(hook(db, j)[0] == "sent" for j in jobs), 6)
        finally:
            await rn.stop()
        return done

    assert run_app(db, scenario), "a backlog is drained without waiting for the next tick"


@mutation(WEBHOOKS, "MAX_ATTEMPTS = 3", "MAX_ATTEMPTS = 4", expect="no fourth attempt")
def test_at_most_three_attempts_with_one_stable_event_id(db):
    async def scenario(env):
        job = failed_hook(db)
        rx = Receiver(500)
        rn = runners.start_runners(env.pool, env.settings, env.store, **{**SLOW, "webhook_every_s": 0.03},
                                   resolver=resolver, transport=rx.transport)
        try:
            for n in (1, 2, 3):
                assert await until(lambda n=n: len(rx.seen) >= n), "attempt %d was never made" % n
                if n < 3:
                    await until(lambda n=n: db.one("SELECT count(*) FROM job_events WHERE job_id = %s AND "
                                                   "stage = 'webhook'", job)[0] == n)      # the mark is written
                    make_due(db, job)
            await until(lambda: hook(db, job)[0] == "failed")
            for _ in range(3):                                # it is due again and again; nothing may send
                make_due(db, job)
                await asyncio.sleep(0.15)
        finally:
            await rn.stop()
        return job, rx

    job, rx = run_app(db, scenario)
    assert len(rx.seen) == 3, "no fourth attempt"
    assert hook(db, job)[:2] == ("failed", 3)
    assert len({headers["x-relay-event-id"] for _, headers, _ in rx.seen}) == 1


@mutation(NET, 'max_redirects = min(max_redirects, MAX_REDIRECTS) if method == "GET" else 0',
          "max_redirects = MAX_REDIRECTS", expect="the Location was not followed")
def test_a_redirect_is_never_followed_and_a_private_address_is_never_posted_to(db):
    async def scenario(env):
        redirected = failed_hook(db)
        private = failed_hook(db, url="https://evil.test/in")            # resolves to 10.0.0.7
        rx = Receiver(302, location="https://hooks.example.test/elsewhere")
        rn = runners.start_runners(env.pool, env.settings, env.store, **{**SLOW, "webhook_every_s": 0.03},
                                   resolver=resolver, transport=rx.transport)
        try:
            await until(lambda: hook(db, private)[0] == "failed" and len(rx.seen) >= 1)
            await asyncio.sleep(0.2)
        finally:
            await rn.stop()
        return redirected, private, rx

    redirected, private, rx = run_app(db, scenario)
    assert {p for p, _, _ in rx.seen} == {"/in"}, "the Location was not followed"
    assert hook(db, private)[0] == "failed" and "refused" in hook(db, private)[2]
    assert DNS["evil.test"] == ["10.0.0.7"] and PUBLIC not in str(hook(db, private))


@mutation(WEBHOOKS, "WHERE id = %(id)s AND callback_for = %(for)s", "WHERE id = %(id)s",
          expect="the newer event is delivered")
def test_a_newer_terminal_event_is_the_one_delivered(db):
    """A Re-check during the send restarts the event: the receiver ends up with the newer event id."""
    async def scenario(env):
        job = failed_hook(db)
        seen_ids = []

        def recheck(_headers):
            if not seen_ids:
                db.run("UPDATE jobs SET callback_for = clock_timestamp(), callback_status = 'pending', "
                       "callback_attempts = 0, callback_next_at = clock_timestamp() WHERE id = %s", job)

        class Rx(Receiver):
            async def hook(self, request):
                recheck(request.headers)
                seen_ids.append(request.headers["x-relay-event-id"])
                return await super().hook(request)

        rx = Rx(204)
        rn = runners.start_runners(env.pool, env.settings, env.store, **{**SLOW, "webhook_every_s": 0.03},
                                   resolver=resolver, transport=rx.transport)
        try:
            await until(lambda: len(seen_ids) >= 2 and hook(db, job)[0] == "sent")
        finally:
            await rn.stop()
        return seen_ids

    ids = run_app(db, scenario)
    assert len(ids) >= 2, "the newer event is delivered: %r" % ids
    assert ids[0] != ids[1], "the newer event has its own id"


# ---- the loops themselves -------------------------------------------------------------------------------------------
@mutation(RUNNERS, 'log.exception("runner %s: step failed", name)', "raise",
          expect="a failing step does not end the loop")
def test_a_failing_step_is_logged_and_the_loop_goes_on(db, monkeypatch):
    from relay_core.jobs import webhooks

    real, calls = webhooks.deliver_due, []

    async def flaky(pool, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the database blinked")
        return await real(pool, **kw)

    monkeypatch.setattr(webhooks, "deliver_due", flaky)

    async def scenario(env):
        job = failed_hook(db)
        rx = Receiver(204)
        rn = runners.start_runners(env.pool, env.settings, env.store, **{**SLOW, "webhook_every_s": 0.03},
                                   resolver=resolver, transport=rx.transport)
        try:
            return job, await until(lambda: hook(db, job)[0] == "sent")
        finally:
            await rn.stop()

    job, sent = run_app(db, scenario)
    assert sent and len(calls) >= 2, "a failing step does not end the loop"


@mutation(RUNNERS, "self._stopping.set()", "pass", expect="TimeoutError")
def test_stop_ends_every_loop_even_with_long_intervals(db):
    async def scenario(env):
        rn = runners.start_runners(env.pool, env.settings, env.store, **SLOW)
        await asyncio.sleep(0.1)
        await asyncio.wait_for(rn.stop(), 3)
        return rn._tasks

    tasks = run_app(db, scenario)
    assert len(tasks) == 3 and all(t.done() and not t.cancelled() for t in tasks), "stop ends every loop"


# ---- housekeeping and the ledger -----------------------------------------------------------------------------------
def a_day_later(monkeypatch):
    """The sweeps' clock, two days on: lets a freshly uploaded object be old enough to count as an orphan."""
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr(sweeps, "datetime", Later)


@mutation(RUNNERS, 'orphans_enabled=settings.get("media_orphan_sweep")', "orphans_enabled=True",
          expect="an orphan survives while the sweep is switched off")
@mutation(RUNNERS, 'orphans_enabled=settings.get("media_orphan_sweep")', "orphans_enabled=False",
          expect="the orphan is swept once the sweep is switched on")
def test_the_orphan_sweep_follows_the_setting_and_the_other_sweeps_run_regardless(db, monkeypatch):
    a_day_later(monkeypatch)

    async def scenario(env):
        orphan = storage.media_key(str(uuid.uuid4()), "output", 0, "video/mp4")
        await env.store.put_bytes(orphan, b"nobody owns me", "video/mp4")
        await users.create_user(env.pool, "ghost", "correct horse battery")
        db.run("INSERT INTO admin_sessions (token_hash, user_id, expires_at) "
               "SELECT 'stale', id, clock_timestamp() - interval '1 hour' FROM admin_users")
        await env.settings.set("media_orphan_sweep", False)
        rn = runners.start_runners(env.pool, env.settings, env.store, **FAST, resolver=resolver)
        try:
            purged = await until(lambda: db.one("SELECT count(*) FROM admin_sessions")[0] == 0)
            await asyncio.sleep(0.4)                                   # several more passes
            kept = await env.store.stat(orphan)
            await env.settings.set("media_orphan_sweep", True)
            swept = False
            for _ in range(250):
                if await env.store.stat(orphan) is None:
                    swept = True
                    break
                await asyncio.sleep(0.02)
        finally:
            await rn.stop()
        return purged, kept, swept

    purged, kept, swept = run_app(db, scenario)
    assert purged, "an expired admin session is purged whatever the orphan setting says"
    assert kept is not None, "an orphan survives while the sweep is switched off"
    assert swept, "the orphan is swept once the sweep is switched on"


@mutation(RUNNERS, 'retention_days=settings.get("log_retention_days")', "retention_days=10_000",
          expect="old request logs are removed by the housekeeping pass")
def test_housekeeping_applies_the_retention_setting_and_expires_input_links(db):
    async def scenario(env):
        key = await env.new_key()
        db.run("INSERT INTO request_logs (key_id, route, status_code, duration_ms, at) VALUES "
               "(%s, 'GET /old', 200, 1, clock_timestamp() - interval '40 days'), "
               "(%s, 'GET /new', 200, 1, clock_timestamp())", key.id, key.id)
        job = db.job(key.id)
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size, share_token, share_expires_at) "
               "VALUES (%s, 'input', 0, 'k/input/0.png', 'image/png', 1, 'tok-expired-0123456789ab', "
               "clock_timestamp() - interval '1 hour')", job)
        rn = runners.start_runners(env.pool, env.settings, env.store, **FAST, resolver=resolver)
        try:
            await until(lambda: db.one("SELECT count(*) FROM request_logs WHERE route = 'GET /old'")[0] == 0, 4)
            await until(lambda: db.one("SELECT share_token FROM job_media")[0] is None, 4)
        finally:
            await rn.stop()

    run_app(db, scenario)
    assert db.all("SELECT route FROM request_logs") == [("GET /new",)], \
        "old request logs are removed by the housekeeping pass"
    assert db.one("SELECT share_token FROM job_media")[0] is None


@mutation(RUNNERS, '("ledger", ledger_every_s, ledger))', '("ledger", ledger_every_s, housekeeping))',
          expect="the ledger loop stored the drift alert")
def test_the_ledger_loop_stores_a_drift_alert(db):
    async def scenario(env):
        key = await env.new_key()
        db.run("UPDATE key_wallets SET used_total = used_total + 3 WHERE key_id = %s AND scope = '*'", key.id)
        rn = runners.start_runners(env.pool, env.settings, env.store, **{**SLOW, "ledger_every_s": 3600},
                                   resolver=resolver)
        try:
            found = await until(lambda: db.one("SELECT count(*) FROM billing_alerts WHERE alert = 'drift' "
                                               "AND key_id = %s", key.id)[0])
        finally:
            await rn.stop()
            db.run("UPDATE key_wallets SET used_total = used_total - 3 WHERE key_id = %s AND scope = '*'",
                   key.id)
        return found

    assert run_app(db, scenario) == 1, "the ledger loop stored the drift alert"


@mutation(RUNNERS, "        for task in pending:\n            task.cancel()\n", "        pass\n",
          expect="stop returned only after the hung step")
def test_stop_cancels_a_step_that_outlasts_the_wait(monkeypatch):
    monkeypatch.setattr(runners, "STOP_WAIT_S", 0.2)
    released = []

    async def scenario():
        stopping = asyncio.Event()
        entered = asyncio.Event()

        async def hung() -> None:
            entered.set()
            try:
                await asyncio.sleep(3600)
            finally:
                released.append(True)

        loop = asyncio.create_task(runners._loop("hung", 0.01, hung, stopping))
        stopper = runners.Runners([loop], stopping)
        await entered.wait()
        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(stopper.stop(), 5)
        return asyncio.get_running_loop().time() - started

    try:
        took = asyncio.run(scenario())
    except TimeoutError:
        took = None
    assert took is not None and took < 2, "stop returned only after the hung step: %s" % took
    assert released == [True], "the cancelled step was cleaned up"
