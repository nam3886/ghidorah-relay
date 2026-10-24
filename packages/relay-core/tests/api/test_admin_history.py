"""What the admin serves of the account history and the settings change history: the list's jobs today, jobs in use,
exit and failed-check stamps; the detail's hours and errors; an import that names the account's identity; and who
changed which setting from what to what (a secret only "changed")."""

import asyncio
import uuid

import pytest
from apikit import ADMIN_USER, run_app

from relay_core import crypto
from relay_core.db import open_pool
from relay_core.settings import Registry, Settings, Spec
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_accounts.py"
SETTINGS_ROUTES = "packages/relay-core/src/relay_core/admin/routes_settings.py"
SETTINGS = "packages/relay-core/src/relay_core/settings.py"


@pytest.fixture
def pair(monkeypatch):
    keys = crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)
    return keys


def account(db, label="a", identity=None) -> uuid.UUID:
    a = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, label, identity, credential_enc, proxy_host, proxy_port, "
           "proxy_exit_ip, proxy_latency_ms, proxy_exit_at, check_failed_at) VALUES (%s, 'fake', %s, %s, '\\x01', "
           "'10.0.0.1', 80, '203.0.113.5', 182, now(), now())", a, label, identity)
    return a


def job(db, account_id, status="done", created="now()") -> uuid.UUID:
    j = db.job(db.key(("fake", None, None)), provider="fake")
    db.run("UPDATE jobs SET status = %s, account_id = %s, created_at = " + created + ", finished_at = now(), "
           "error_code = 'x.code', error = 'the job text' WHERE id = %s", status, account_id, j)
    return j


@mutation(ROUTES, "\"AND created_at >= date_trunc('day'", "\"AND true OR created_at >= date_trunc('day'",
          expect="yesterday's job is not today's")
@mutation(ROUTES, "l.purpose = 'job' AND l.until > clock_timestamp()), ",
          "l.until > clock_timestamp()), ", expect="a check lease is not a job")
def test_the_list_counts_todays_jobs_and_failures_and_the_jobs_in_use_and_carries_the_exit_and_failed_stamps(db):
    a = account(db)
    job(db, a)
    job(db, a, status="failed")
    job(db, a, created="now() - interval '2 days'")
    for purpose in ("job", "job", "check"):
        db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, %s, 'w', "
               "now() + interval '1 h', now() + interval '2 h')", a, purpose)

    async def scenario(env):
        return (await env.client.get("/admin/accounts", headers=await env.admin_headers())).json()["items"][0]

    got = run_app(db, scenario)
    assert got["jobs_today"] == {"total": 2, "failed": 1}, "yesterday's job is not today's: %r" % got["jobs_today"]
    assert got["jobs_in_use"] == 2, "a check lease is not a job"
    assert (got["proxy_exit_ip"], got["proxy_latency_ms"]) == ("203.0.113.5", 182)
    assert isinstance(got["proxy_exit_ms"], int) and isinstance(got["check_failed_ms"], int)
    assert got["screenshot_ms"] is None
    db.run("UPDATE jobs SET status = 'failed' WHERE status = 'done'")      # a done job has outputs (I11)
    db.run("DELETE FROM leases")                                         # a job lease names a live job (I14)


@mutation(ROUTES, "AND hour >= date_trunc('hour', clock_timestamp(), 'UTC') - make_interval(days => %s) ORDER BY hour",
          "ORDER BY hour", expect="only the last 14 days")
def test_the_history_serves_14_days_of_hours_oldest_first_and_the_newest_errors_with_their_jobs(db):
    a = account(db)
    j = job(db, a, status="failed")
    db.run("INSERT INTO account_health (account_id, hour, worst) VALUES (%s, date_trunc('hour', now()), 1), "
           "(%s, date_trunc('hour', now()) - interval '1 day', 0), (%s, now() - interval '15 days', 2)", a, a, a)
    db.run("INSERT INTO account_errors (account_id, code, job_id) VALUES (%s, 'proxy.timeout', NULL)", a)
    db.run("INSERT INTO account_errors (account_id, code, job_id) VALUES (%s, 'dola.content_refused', %s)", a, j)

    async def scenario(env):
        h = await env.admin_headers()
        return (await env.client.get("/admin/accounts/%s/history" % a, headers=h),
                await env.client.get("/admin/accounts/%s/history" % uuid.uuid4(), headers=h))

    found, missing = run_app(db, scenario)
    assert missing.status_code == 404
    body = found.json()
    assert [w for _, w in body["hours"]] == [0, 1], "only the last 14 days, oldest first"
    assert body["hours"][0][0] < body["hours"][1][0]
    assert [(e["code"], e["job_id"], e["job_error_code"]) for e in body["errors"]] == [
        ("dola.content_refused", str(j), "x.code"), ("proxy.timeout", None, None)]
    assert all(isinstance(e["at_ms"], int) for e in body["errors"])


@mutation(ROUTES, '(item.identity or "").strip() or None', "None", expect="the email is the identity")
def test_an_import_stores_the_given_identity_unless_another_account_already_has_it(db, pair):
    account(db, label="old", identity="taken@example.com")

    async def scenario(env):
        body = {"provider": "fake", "items": [{"credential": "c1", "identity": " me@example.com "},
                                              {"credential": "c2", "identity": "taken@example.com"},
                                              {"credential": "c3"}]}
        r = await env.client.post("/admin/accounts/import", json=body, headers=await env.admin_headers())
        assert r.status_code == 200, r.text
        return [uuid.UUID(i["id"]) for i in r.json()["imported"]]

    ids = run_app(db, scenario)
    got = [db.one("SELECT identity FROM accounts WHERE id = %s", i)[0] for i in ids]
    assert got == ["me@example.com", None, None], "the email is the identity: %r" % got


@mutation(SETTINGS, "            if changed:\n                await self._history(", "            if False:\n"
          "                await self._history(", expect="each change is a row")
@mutation(SETTINGS_ROUTES, "stored = await settings.delete(key, by=request.state.admin_user.username)",
          "stored = await settings.delete(key)", expect="the reset names who made it")
def test_each_change_and_reset_is_a_history_row_and_the_list_says_when_and_by_whom_it_was_last_saved(db):
    async def scenario(env):
        h = await env.admin_headers()
        empty = (await env.client.get("/admin/settings", headers=h)).json()["last_saved"]
        for value in (7, 7, 9):                     # the second put changes nothing: no row
            assert (await env.client.put("/admin/settings/log_retention_days", json={"value": value},
                                         headers=h)).status_code == 200
        assert (await env.client.delete("/admin/settings/log_retention_days", headers=h)).status_code == 200
        page = (await env.client.get("/admin/settings/history?limit=2", headers=h)).json()
        rest = (await env.client.get("/admin/settings/history?before=%d" % page["next"], headers=h)).json()
        saved = (await env.client.get("/admin/settings", headers=h)).json()["last_saved"]
        bad = await env.client.get("/admin/settings/history?limit=0", headers=h)
        return empty, page, rest, saved, bad.status_code

    empty, page, rest, saved, bad = run_app(db, scenario)
    assert empty is None and bad == 400
    rows = page["items"] + rest["items"]
    assert [(r["old"], r["new"]) for r in rows] == [(9, None), (7, 9), (None, 7)], "each change is a row"
    assert [r["by"] for r in rows] == [ADMIN_USER] * 3, "the reset names who made it"
    assert {r["key"] for r in rows} == {"log_retention_days"} and rest["next"] is None
    assert saved == {"at_ms": rows[0]["at_ms"], "by": ADMIN_USER}


@mutation(SETTINGS, "        if secret:\n            old = new = None\n", "", expect="a secret value was kept")
def test_a_secret_setting_keeps_only_that_it_changed(db):
    registry = Registry(Spec("relay_token", str, "", "A token.", secret=True), Spec("plain_word", str, "", "A word."))

    async def scenario():
        pool = await open_pool(db.api_dsn, max_size=2)
        try:
            settings = Settings(pool, db.api_dsn, registry=registry)
            await settings.set("relay_token", "s3cret-value", by="admin")
            await settings.set("relay_token", "s3cret-other", by="admin")
            await settings.delete("relay_token", by="admin")
            await settings.set("plain_word", "hello", by="admin")
        finally:
            await pool.close()

    asyncio.run(scenario())
    rows = db.all("SELECT key, old_value, new_value, secret FROM settings_history ORDER BY id")
    assert rows[:3] == [("relay_token", None, None, True)] * 3, "a secret value was kept: %r" % rows
    assert rows[3] == ("plain_word", None, "hello", False)
    assert "s3cret" not in db.one("SELECT string_agg(h::text, ' ') FROM settings_history h")[0]
