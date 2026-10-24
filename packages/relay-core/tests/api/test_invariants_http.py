"""Invariants at the HTTP level: I15 (the cap) through every door, I24 (no planted secret over 2,000 characters in a
response body, a request-log row, an event, `callback_last` or an admin answer)."""

import asyncio
import json

import pytest
from apikit import request_logs, run_app
from doorkit import IMAGE, VIDEO, charges, error_of, jobs, used
from hookkit import URL, hook, hook_job, resolver
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from relay_core import crypto
from relay_core.api import runners
from relay_core.testing.mutation import mutation

CREATE = "packages/relay-core/src/relay_core/jobs/create.py"
ERRORS = "packages/relay-core/src/relay_core/api/errors_http.py"
LOG = "packages/relay-core/src/relay_core/api/request_log.py"
ROUTES_ACCOUNTS = "packages/relay-core/src/relay_core/admin/routes_accounts.py"
PLANT = "PLANTEDSECRET0123456789"
# Over 2,000 characters of plain words: no secret-shaped run, so only the length cut can remove it.
LONG = "lorem ipsum dolor " * 80 + PLANT + " sit amet consectetur " * 80
assert len(LONG) > 2000


# ---- I15 ----------------------------------------------------------------------------------------------------------
@mutation(CREATE, "if unfinished >= limit:", "if unfinished > limit:", expect="over the cap is 429")
def test_i15_creation_over_the_cap_is_429_on_every_door_with_no_charge_and_no_job_row(db):
    attempts = [
        ("/v1/video/generations", {"model": VIDEO, "prompt": "a cat"}),
        ("/v1/images/generations", {"model": IMAGE, "prompt": "a cat"}),
        ("/v1/chat/completions", {"model": VIDEO, "messages": [{"role": "user", "content": "a cat"}]}),
        ("/v1/chat/completions", {"model": VIDEO, "stream": True,
                                  "messages": [{"role": "user", "content": "a cat"}]}),
        ("/v1beta/models/%s:generateContent" % VIDEO, {"contents": [{"role": "user", "parts": [{"text": "a cat"}]}]}),
        ("/models/%s:streamGenerateContent" % VIDEO, {"contents": [{"role": "user", "parts": [{"text": "a cat"}]}]}),
    ]

    async def scenario(env):
        key = await env.new_key()
        db.run("UPDATE api_keys SET max_unfinished = 1 WHERE id = %s", key.id)
        first = await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "one"},
                                      headers=key.headers)
        assert first.status_code == 200, first.text
        before = (jobs(db, key.id), charges(db, key_id=key.id), used(db, key.id),
                  used(db, key.id, "fake"))
        for path, body in attempts:
            r = await env.client.post(path, json=body, headers=key.headers)
            assert error_of(r)[:2] == (429, "quota.too_many_queued"), "over the cap is 429: %s %s" % (
                path, r.text[:200])
            assert (jobs(db, key.id), charges(db, key_id=key.id), used(db, key.id),
                    used(db, key.id, "fake")) == before, "no job row and no charge: %s" % path
        assert len(before[0]) == 1 and before[1] == 1
        logged = [r["status_code"] for r in request_logs(db)]
        assert logged.count(429) == len(attempts), "each refusal is a request-log row"

    run_app(db, scenario)


# ---- I24 ----------------------------------------------------------------------------------------------------------
def log_text(db) -> str:
    """Every row of every writer the api feeds: request logs, events, job error and callback text, commands."""
    parts = [json.dumps(request_logs(db), default=str)]
    parts += [str(r) for r in db.all("SELECT text FROM job_events")]
    parts += [str(r) for r in db.all("SELECT error, callback_last FROM jobs")]
    parts += [str(r) for r in db.all("SELECT args::text, result::text FROM commands")]
    return "\n".join(parts)


@mutation(ERRORS, "if len(param) > MAX_PARAM else redact_text(param)", "if False else param",
          expect="a planted secret reached a response")
@mutation("packages/relay-core/src/relay_core/redact.py", "return {trim_log_value(k, limit): trim_log_value(v, limit)",
          "return {k: trim_log_value(v, limit)", expect="a planted secret reached a request-log row")
@mutation(LOG, "redact_log(parsed) if isinstance", "parsed if isinstance",
          expect="a planted secret reached a request-log row")
def test_i24_a_long_planted_secret_in_a_door_request_never_reaches_a_response_or_a_log_row(db):
    async def scenario(env):
        key = await env.new_key()
        h = key.headers
        gemini = {"contents": [{"role": "user", "parts": [{"text": "x"}]}]}
        calls = [
            ("POST", "/v1/video/generations", {"model": VIDEO, "prompt": LONG}),                 # valid: job made
            ("POST", "/v1/video/generations", {"model": VIDEO, "prompt": "p", LONG: 1}),         # unknown field
            ("POST", "/v1/video/generations", {"model": VIDEO, "prompt": "p", "callback_url": "http://" + LONG}),
            ("POST", "/v1/video/generations", {"model": VIDEO, "prompt": "p", "images": [LONG]}),
            ("POST", "/v1/video/generations", {"model": VIDEO, "prompt": "p", "options": {"k": LONG}, "ratio": LONG}),
            ("POST", "/v1/video/generations", {"model": LONG, "prompt": "p"}),
            ("POST", "/v1/video/generations", {"model": VIDEO, "prompt": "p", "duration": LONG}),
            ("POST", "/v1/images/generations", {"model": IMAGE, "prompt": "p", "style": LONG, LONG: 2}),
            ("POST", "/v1/chat/completions", {"model": VIDEO, "messages": [], LONG: 1}),
            ("POST", "/v1/chat/completions", {"model": LONG, "messages": [{"role": "user", "content": LONG}]}),
            ("POST", "/v1beta/models/%s:generateContent" % VIDEO, {**gemini, LONG: 1}),
            ("POST", "/v1beta/models/%s:generateContent" % VIDEO,
             {"contents": [{"parts": [{"inlineData": {"mimeType": "image/png", "data": LONG}}]}]}),
            ("POST", "/v1beta/models/%s:generateContent" % LONG.replace(" ", "-"), gemini),
            ("GET", "/v1/video/generations/" + LONG.replace(" ", "-"), None),
        ]
        for method, path, body in calls:
            r = await env.client.request(method, path, json=body, headers=h)
            assert PLANT not in r.text, "a planted secret reached a response: %s %s" % (method, path[:60])
        r = await env.client.post("/v1/video/generations", content=b'{"prompt": "' + LONG.encode() + b'"',
                                  headers={**h, "content-type": "application/json"})      # broken JSON
        assert PLANT not in r.text
        assert any(r["status_code"] == 200 for r in request_logs(db)), "the valid request was made and logged"

    run_app(db, scenario)
    assert PLANT not in log_text(db), "a planted secret reached a request-log row"


@pytest.fixture
def pair(monkeypatch):
    keys = crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)
    return keys


@mutation(ROUTES_ACCOUNTS, 'rejected=%d", body.provider, len(imported), len(rejected))',
          'rejected=%d %s", body.provider, len(imported), len(rejected), body)',
          expect="a planted secret reached a log record")
def test_i24_a_long_planted_secret_in_an_admin_import_or_edit_reaches_no_answer_row_or_log(db, pair, caplog):
    import logging
    caplog.set_level(logging.DEBUG)

    async def scenario(env):
        admin = await env.admin_headers()
        key = await env.new_key()
        made = await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "p"}, headers=key.headers)
        job_id = made.json()["id"]
        calls = [
            ("POST", "/admin/accounts/import", {"provider": "fake", "items": [
                {"credential": LONG}, {"credential": "ok " + LONG}]}),
            ("POST", "/admin/accounts/import", {"provider": "fake", "items": [{"credential": "x", "proxy": LONG}]}),
            ("POST", "/admin/accounts/import", {"provider": "fake", "items": [{"credential": "x", "label": LONG}]}),
            ("POST", "/admin/accounts/import", {"provider": LONG, "items": []}),
            ("PATCH", "/admin/jobs/" + job_id, {"title": LONG}),
            ("PUT", "/admin/settings/log_retention_days", {"value": LONG}),
            ("PUT", "/admin/settings/" + LONG.replace(" ", "-"), {"value": 1}),
            ("POST", "/admin/keys", {"label": LONG, "models": [LONG], "wallets": []}),
            ("POST", "/admin/jobs/%s/recover" % job_id, {"account": LONG}),
            ("POST", "/admin/login", {"username": LONG, "password": LONG}),
            ("GET", "/admin/jobs?status=" + LONG.replace(" ", "-"), None),
        ]
        for method, path, body in calls:
            r = await env.client.request(method, path, json=body, headers=admin)
            assert PLANT not in r.text, "a planted secret reached an admin answer: %s %s" % (method, path[:60])
            assert r.status_code < 500, (method, path[:60], r.text[:200])
        assert request_logs(db) == [r for r in request_logs(db) if r["route"].startswith("POST /v1/video")], \
            "admin routes are not logged"

    run_app(db, scenario)
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("relay")]     # httpx logs its own URLs
    assert not [m for m in ours if PLANT in m], "a planted secret reached a log record"
    tables = [t[0] for t in db.all("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")]
    for table in tables:     # an operator's own label or model list (api_keys, accounts) is data, not a log
        for (text,) in db.all('SELECT x::text FROM "%s" x' % table):
            assert table in ("api_keys", "accounts") or PLANT not in text, "a planted secret is stored in " + table
    assert PLANT not in log_text(db)


# No marker on the sender's `redact_log`: a second layer cuts the stored text again, so that mutant is equivalent for
# a plain long answer; the writer's own test (test_redaction_writers) kills it by exact text.
def test_i24_a_long_planted_secret_in_a_webhook_receiver_answer_never_reaches_callback_last_or_an_event(db):
    import httpx

    async def scenario(env):
        job = hook_job(db, db.key(), url=URL)
        db.run("UPDATE jobs SET error_code = 'render.deadline', error = 'late' WHERE id = %s", job)

        async def answer(request):
            return Response(LONG, status_code=500)

        transport = httpx.ASGITransport(app=Starlette(routes=[Route("/{p:path}", answer, methods=["POST"])]))
        rn = runners.start_runners(env.pool, env.settings, env.store, webhook_every_s=0.05,
                                   housekeeping_every_s=3600, ledger_every_s=3600, resolver=resolver,
                                   transport=transport)
        try:
            for _ in range(200):
                if hook(db, job)[1] >= 1:
                    break
                await asyncio.sleep(0.02)
        finally:
            await rn.stop()
        return job

    job = run_app(db, scenario)
    status, attempts, last = hook(db, job)
    assert attempts >= 1 and last, "the sender recorded the answer"
    assert PLANT not in str(last) and PLANT not in log_text(db), "a planted secret reached callback_last"
