"""Admin accounts and commands: the import seals and never logs, the list shows
no secret, a patch is refused while a worker holds the account, and every account action is only a command row."""

import logging
import re
import uuid

import psycopg
import pytest
from accountkit import KitEngine, make_env, new_account, new_command, object_store, run_as
from apikit import run_app
from psycopg.types.json import Jsonb

from relay_core import crypto
from relay_core.admin.routes_common import ADMIN_COMMANDS, enqueue, parse_proxy
from relay_core.db import tx
from relay_core.jobs.checks import check_account
from relay_core.providers import Health
from relay_core.testing.fake_provider import FakeSpec
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_accounts.py"
ADMIN_ROUTER = "packages/relay-core/src/relay_core/admin/router.py"
MIG_NO_DELETE = "packages/relay-core/src/relay_core/db/migrations/0010_account_delete_worker_only.sql"
LOG_LINE = 'rejected=%d", body.provider, len(imported), len(rejected))'
LOG_LINE_WITH_BODY = 'rejected=%d %s", body.provider, len(imported), len(rejected), body)'
IN_USE_CHECK = 'if fields & {"proxy", "enabled"} and (await'
WAKE_ON_ENABLE = '            if body.enabled and was == "disabled":\n'
SECRET = "sessionid=PLANTED-cookie-9f8e7d6c5b4a39281706"
PROXY_PASSWORD = "hunter2pw-planted"
WORKER_OWNED = ("check_due_at", "cooldown_until", "cooldown_reason", "strikes", "proxy_ok", "proxy_checked_at")
PARAM = re.compile(r"\{(\w+)\}")
NIL = "00000000-0000-0000-0000-000000000001"

# every route this lane serves: a mistyped lane module name would drop its routes without a sound (create_app skips an
# unknown module), so the set is pinned here
EXPECTED = {
    ("POST", "/admin/accounts/import"), ("GET", "/admin/accounts"), ("PATCH", "/admin/accounts/{account_id}"),
    ("GET", "/admin/accounts/{account_id}/screenshot"), ("DELETE", "/admin/accounts/{account_id}"),
    ("POST", "/admin/accounts/{account_id}/wake"), ("POST", "/admin/accounts/{account_id}/check"),
    ("POST", "/admin/accounts/{account_id}/proxy-recheck"), ("POST", "/admin/providers/{provider}/check-all"),
    ("POST", "/admin/providers/{provider}/refresh-catalog"), ("GET", "/admin/commands/{command_id}"),
    ("POST", "/admin/keys"), ("GET", "/admin/keys"), ("GET", "/admin/keys/{key_id}"),
    ("PATCH", "/admin/keys/{key_id}"), ("POST", "/admin/keys/{key_id}/enable"),
    ("POST", "/admin/keys/{key_id}/disable"), ("DELETE", "/admin/keys/{key_id}"),
    ("GET", "/admin/jobs"), ("GET", "/admin/jobs/{job_id}"), ("GET", "/admin/jobs/{job_id}/debug/{idx}"),
    ("PATCH", "/admin/jobs/{job_id}"), ("POST", "/admin/jobs/{job_id}/recheck"),
    ("POST", "/admin/jobs/{job_id}/recover"), ("POST", "/admin/jobs/{job_id}/fetch-title"),
    ("POST", "/admin/jobs/{job_id}/regenerate"),
    ("GET", "/admin/settings"), ("PUT", "/admin/settings/{key}"), ("DELETE", "/admin/settings/{key}"),
    ("GET", "/admin/alerts"), ("POST", "/admin/alerts/{alert_id}/acknowledge"), ("GET", "/admin/models"),
}


def code_of(r) -> str:
    return r.json()["error"]["code"]


def fill(match: re.Match) -> str:
    return "1" if match.group(1) in ("alert_id", "idx") else NIL if match.group(1).endswith("_id") else "x"


@pytest.fixture
def pair(monkeypatch):
    keys = crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)
    return keys


def account_dump(db, account_id) -> str:
    return db.one("SELECT a::text FROM accounts a WHERE id = %s", account_id)[0]


def live_lease(db, account_id) -> None:
    db.run("INSERT INTO leases (account_id, purpose, owner, until, hard_until) VALUES (%s, 'check', 'w1', "
           "clock_timestamp() + interval '1 hour', clock_timestamp() + interval '2 hours')", account_id)


# ---- the door ---------------------------------------------------------------------------------------------------
@mutation(ADMIN_ROUTER, "behind = APIRouter(dependencies=[Depends(auth.require_admin)])", "behind = APIRouter()",
          expect="without a session")
def test_every_admin_route_refuses_a_request_without_a_session_and_none_is_missing(db):
    async def scenario(env):
        # the schema lists every route, nested routers included (`app.routes` hides them behind one entry)
        served = {(m.upper(), path) for path, item in env.app.openapi()["paths"].items() for m in item
                  if path.startswith("/admin/")}
        assert EXPECTED <= served, "a route is not mounted: %s" % sorted(EXPECTED - served)
        for method, path in sorted(served - {("POST", "/admin/login")}):
            for headers in ({}, {"X-Admin-Session": "nonsense"}):
                r = await env.client.request(method, PARAM.sub(fill, path), headers=headers, json={}
                                             if method in ("POST", "PUT", "PATCH") else None)
                assert (r.status_code, code_of(r)) == (401, "auth.invalid_session"), "without a session: %s %s" % (
                    method, path)
        assert db.one("SELECT count(*) FROM commands")[0] == 0

    run_app(db, scenario)


def test_the_admin_queues_exactly_the_command_kinds_of_the_contract():
    assert ADMIN_COMMANDS == ("delete_account", "wake", "check", "check_all", "proxy_recheck", "recover",
                              "refresh_catalog", "titles")


def test_a_kind_outside_the_list_is_never_queued(db):
    async def scenario(env):
        async with tx(env.pool) as conn:
            with pytest.raises(ValueError):
                await enqueue(conn, "fake", "merge_credential")

    run_app(db, scenario)
    assert db.one("SELECT count(*) FROM commands")[0] == 0


# ---- import -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, want", [
    ("10.0.0.5:3128", ("10.0.0.5", 3128, None)),
    ("10.0.0.5:3128:bob:p%40ss", ("10.0.0.5", 3128, "bob:p%40ss")),
    ("bob:pw@proxy.example:8080", ("proxy.example", 8080, "bob:pw")),
    ("http://bob:p%40ss@proxy.example:8080", ("proxy.example", 8080, "bob:p@ss")),
    ("socks5://proxy.example:1080", ("proxy.example", 1080, None)),
])
def test_a_proxy_is_read_in_the_usual_spellings(text, want):
    assert parse_proxy(text) == want


@pytest.mark.parametrize("text", ["nonsense", "host:notaport", "host:0", "host:70000", "ftp://h:21",
                                  "a:b:" + PROXY_PASSWORD, "http://:80", "bob:%s@:80" % PROXY_PASSWORD])
def test_a_bad_proxy_is_refused_without_repeating_it(text):
    with pytest.raises(ValueError) as e:
        parse_proxy(text)
    assert PROXY_PASSWORD not in str(e.value)


@mutation(ROUTES, "crypto.seal(public, parsed.blob)", "parsed.blob", expect="plaintext credential stored")
@mutation(ROUTES, LOG_LINE, LOG_LINE_WITH_BODY, expect="the credential reached a log")
def test_import_seals_the_credential_and_the_proxy_password_and_the_body_reaches_no_log_row_or_answer(
        db, pair, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(env):
        h = await env.admin_headers()
        body = {"provider": "fake", "items": [
            {"credential": SECRET + " one", "label": "main", "proxy": "10.0.0.5:3128:bob:" + PROXY_PASSWORD},
            {"credential": SECRET + " two"}]}
        r = await env.client.post("/admin/accounts/import", json=body, headers=h)
        assert r.status_code == 200, r.text
        got = r.json()
        assert [i["label"] for i in got["imported"]] == ["main", "fake"] and got["rejected"] == []
        assert SECRET not in r.text and PROXY_PASSWORD not in r.text
        return got

    got = run_app(db, scenario)
    first = uuid.UUID(got["imported"][0]["id"])
    row = db.one("SELECT provider, state, credential_enc, proxy_host, proxy_port, proxy_auth_enc, identity, "
                 "last_checked_at, check_due_at IS NOT NULL FROM accounts WHERE id = %s", first)
    assert bytes(row[2]).startswith(b"rs1"), "plaintext credential stored"
    assert crypto.open_blob(bytes(row[2]), [pair.private]) == (SECRET + " one").encode()
    assert (row[0], row[1], row[3], row[4]) == ("fake", "available", "10.0.0.5", 3128)
    assert crypto.open_blob(bytes(row[5]), [pair.private]) == ("bob:" + PROXY_PASSWORD).encode()
    assert (row[6], row[7], row[8]) == (None, None, True), "never checked, so not claimable; due at once"
    assert db.one("SELECT count(*) FROM accounts")[0] == 2
    assert not [r for r in caplog.records if SECRET in r.getMessage() or PROXY_PASSWORD in r.getMessage()
                ], "the credential reached a log"
    assert SECRET not in caplog.text and PROXY_PASSWORD not in caplog.text
    tables = [t[0] for t in db.all("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")]
    for table in tables:
        for (text,) in db.all('SELECT x::text FROM "%s" x' % table):
            assert SECRET not in text and PROXY_PASSWORD not in text, "a secret is stored in %s" % table
            assert SECRET.encode().hex() not in text, "a plaintext secret is stored in %s" % table


def test_an_unparseable_credential_or_proxy_is_rejected_with_a_fixed_reason_and_nothing_of_its_text(
        db, pair, monkeypatch):
    def picky(self, raw: bytes):
        if raw.startswith(b"BAD"):
            raise ValueError("cookie %s is not valid" % raw.decode())
        return type("P", (), {"blob": raw, "hint": ""})()

    monkeypatch.setattr(FakeSpec, "parse_credential", picky)

    async def scenario(env):
        h = await env.admin_headers()
        body = {"provider": "fake", "items": [
            {"credential": "BAD " + SECRET}, {"credential": "good"},
            {"credential": "good too", "proxy": "not a proxy " + PROXY_PASSWORD}]}
        return await env.client.post("/admin/accounts/import", json=body, headers=h)

    r = run_app(db, scenario)
    assert r.status_code == 200
    assert r.json()["rejected"] == [{"index": 0, "reason": "credential_unparseable"},
                                    {"index": 2, "reason": "proxy_invalid"}]
    assert [i["index"] for i in r.json()["imported"]] == [1]
    assert SECRET not in r.text and PROXY_PASSWORD not in r.text
    assert db.one("SELECT count(*) FROM accounts")[0] == 1, "only the good one"


def test_an_import_for_an_unknown_provider_or_with_an_extra_field_or_no_items_is_refused(db, pair):
    async def scenario(env):
        h = await env.admin_headers()
        post = lambda body: env.client.post("/admin/accounts/import", json=body, headers=h)  # noqa: E731
        ghost = await post({"provider": "ghost", "items": [{"credential": SECRET}]})
        extra = await post({"provider": "fake", "items": [{"credential": SECRET, "cookie": "x"}]})
        empty = await post({"provider": "fake", "items": []})
        return ghost, extra, empty

    ghost, extra, empty = run_app(db, scenario)
    assert (ghost.status_code, code_of(ghost)) == (404, "resource.not_found")
    assert (extra.status_code, code_of(extra), extra.json()["error"]["param"]) == (
        400, "input.invalid", "items.0.cookie")
    assert (empty.status_code, code_of(empty)) == (400, "input.invalid")
    assert SECRET not in extra.text
    assert db.one("SELECT count(*) FROM accounts")[0] == 0


def test_a_credential_for_a_known_identity_is_inserted_and_the_first_check_merges_it_into_the_old_row(db, pair):
    """The api never learns an identity: the import adds a row, the worker's check merges it."""
    old = new_account(db, identity="who@x", checked=True, credential=b"old-cred", proxy=("10.0.0.9", 1))

    async def scenario(env):
        h = await env.admin_headers()
        r = await env.client.post("/admin/accounts/import", headers=h,
                                  json={"provider": "fake", "items": [{"credential": "fresh-cookie"}]})
        return uuid.UUID(r.json()["imported"][0]["id"])

    fresh = run_app(db, scenario)
    assert db.one("SELECT count(*) FROM accounts")[0] == 2, "the import itself leaves the old row alone"
    assert db.one("SELECT credential_gen, credential_enc FROM accounts WHERE id = %s", old)[0] == 1
    command = new_command(db, "check", account_id=fresh)

    async def check(p, store):
        engine = KitEngine()
        engine.health = Health("alive", identity="who@x", display_name="Who")
        return await check_account(make_env(p, engine, store=store), fresh, command_id=command)

    with object_store() as store:
        result = run_as(db.worker_dsn, lambda p: check(p, store))
    assert result.outcome == "merged" and result.account_id == old
    assert db.one("SELECT count(*) FROM accounts")[0] == 1
    gen, enc, host = db.one("SELECT credential_gen, credential_enc, proxy_host FROM accounts WHERE id = %s", old)
    assert gen == 2 and host == "10.0.0.9", "the credential moves, the proxy stays"
    assert crypto.open_blob(bytes(enc), [pair.private]) == b"fresh-cookie", "the sealed credential moved"


# ---- list -------------------------------------------------------------------------------------------------------
def test_the_list_shows_state_capacity_and_use_and_no_secret(db, pair):
    a = new_account(db, label="alpha", checked=True, proxy=("10.0.0.1", 80), identity="a@x")
    b = new_account(db, label="beta", checked=False, provider="other")
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at) VALUES "
           "(%s, 'video', 7, clock_timestamp(), clock_timestamp() + interval '1 day')", a)
    db.run("UPDATE accounts SET proxy_auth_enc = %s WHERE id = %s", b"sealed-proxy-auth", a)
    live_lease(db, a)

    async def scenario(env):
        h = await env.admin_headers()
        everything = await env.client.get("/admin/accounts", headers=h)
        fake = await env.client.get("/admin/accounts?provider=fake", headers=h)
        paged = await env.client.get("/admin/accounts?limit=1&offset=1", headers=h)
        bad = await env.client.get("/admin/accounts?limit=0", headers=h)
        return everything, fake, paged, bad

    everything, fake, paged, bad = run_app(db, scenario)
    assert everything.json()["total"] == 2 and {i["label"] for i in everything.json()["items"]} == {"alpha", "beta"}
    [item] = fake.json()["items"]
    assert item["id"] == str(a) and item["in_use"] is True and item["has_proxy_auth"] is True
    assert item["capacity"][0]["pool"] == "video" and item["capacity"][0]["left_n"] == 7
    assert (item["capacity"][0]["left_today"], item["capacity"][0]["source"]) == (7, "reported")
    assert (item["tier"], item["tier_read"], item["cycle_start"]) == (None, False, None)
    assert (item["proxy_host"], item["proxy_port"], item["state"], item["identity"]) == ("10.0.0.1", 80, "available",
                                                                                         "a@x")
    assert isinstance(item["created_ms"], int) and item["check_due_ms"] is not None
    assert all(not i["in_use"] for i in everything.json()["items"] if i["id"] == str(b))
    assert len(paged.json()["items"]) == 1 and paged.json()["total"] == 2
    assert bad.status_code == 400
    for name in ("credential_enc", "sealed-proxy-auth", "proxy_auth_enc"):
        assert name not in everything.text


@mutation("packages/relay-core/src/relay_core/admin/routes_common.py", '"left_today": st.left,', '"left_today": None,',
          expect="gives way to the budget")
def test_the_list_shows_what_the_next_claim_will_see_a_report_until_its_refill_and_the_budget_after_it(db, pair):
    a = new_account(db, label="alpha", checked=True)
    db.run("INSERT INTO account_pools (account_id, pool, reported_left, reported_at, refill_at) VALUES "
           "(%s, 'video', 1, clock_timestamp() - interval '30 hours', clock_timestamp() - interval '1 hour'), "
           "(%s, 'image', 3, clock_timestamp(), clock_timestamp() + interval '1 hour')", a, a)

    async def scenario(env):
        return await env.client.get("/admin/accounts?provider=fake", headers=await env.admin_headers())

    [item] = run_app(db, scenario).json()["items"]
    caps = {c["pool"]: c for c in item["capacity"]}
    assert (caps["video"]["left_n"], caps["video"]["left_today"], caps["video"]["source"]) == (1, 4, "computed"), (
        "a report whose refill has passed gives way to the budget (4 a day), as the next claim reads it")
    assert (caps["image"]["left_n"], caps["image"]["left_today"], caps["image"]["source"]) == (3, 3, "reported"), (
        "a report whose refill is ahead is shown as it is")


# ---- patch ------------------------------------------------------------------------------------------------------
@mutation(ROUTES, IN_USE_CHECK, "if False and (await", expect="while a worker holds it")
def test_a_proxy_or_enable_change_is_refused_while_the_account_is_in_use_but_a_label_is_not(db, pair):
    a = new_account(db, label="busy", checked=True)
    live_lease(db, a)

    async def scenario(env):
        h = await env.admin_headers()
        patch = lambda body: env.client.patch("/admin/accounts/%s" % a, json=body, headers=h)  # noqa: E731
        return [await patch({"enabled": False}), await patch({"proxy": "10.0.0.2:80"}), await patch({"label": "new"})]

    disabled, proxied, labelled = run_app(db, scenario)
    for r in (disabled, proxied):
        assert (r.status_code, code_of(r)) == (409, "pool.account_busy"), "refused while a worker holds it"
    assert labelled.status_code == 200 and labelled.json()["label"] == "new"
    assert db.one("SELECT state, proxy_host FROM accounts WHERE id = %s", a) == ("available", None)
    assert db.one("SELECT count(*) FROM commands")[0] == 0


@mutation(ROUTES, WAKE_ON_ENABLE, "            if False:\n", expect="enabling a disabled account wakes it")
def test_a_patch_sets_the_label_the_proxy_and_the_state_and_a_new_proxy_queues_the_recheck(db, pair):
    a = new_account(db, label="old", checked=True, proxy=("10.0.0.1", 80))
    before = db.one("SELECT proxy_ok, proxy_checked_at, check_due_at, strikes FROM accounts WHERE id = %s", a)

    async def scenario(env):
        h = await env.admin_headers()
        patch = lambda body: env.client.patch("/admin/accounts/%s" % a, json=body, headers=h)  # noqa: E731
        out = {}
        out["label"] = await patch({"label": "renamed"})
        out["proxy"] = await patch({"proxy": "bob:pw@10.9.9.9:3128"})
        out["off"] = await patch({"enabled": False})
        out["on"] = await patch({"enabled": True})
        out["on_again"] = await patch({"enabled": True})
        out["unproxy"] = await patch({"proxy": None})
        out["bad"] = await patch({"proxy": "nonsense"})
        out["empty"] = await patch({})
        out["extra"] = await patch({"state": "dead"})
        out["ghost"] = await env.client.patch("/admin/accounts/%s" % uuid.uuid4(), json={"label": "x"}, headers=h)
        return out

    out = run_app(db, scenario)
    assert out["label"].json()["command_id"] is None and out["label"].json()["label"] == "renamed"
    queued = sorted(db.all("SELECT provider, kind, account_id, args FROM commands"), key=lambda q: q[1])
    args = {"account_id": str(a)}
    assert queued == [("fake", "proxy_recheck", a, args), ("fake", "wake", a, args)], (
        "only the new proxy queues the probe, and enabling a disabled account wakes it: %r" % queued)
    assert out["on"].json()["wake_command_id"] is not None, "enabling a disabled account wakes it"
    assert [out[n].json()["wake_command_id"] for n in ("label", "proxy", "off", "on_again")] == [None] * 4
    assert out["proxy"].json()["command_id"] is not None and out["proxy"].json()["proxy_host"] == "10.9.9.9"
    assert out["off"].json()["state"] == "disabled" and out["on"].json()["state"] == "available"
    assert out["unproxy"].json()["proxy_host"] is None
    assert db.one("SELECT proxy_host, proxy_port, proxy_auth_enc, state_reason FROM accounts WHERE id = %s", a) == (
        None, None, None, None)
    assert out["unproxy"].json()["command_id"] is None
    for name in ("bad", "empty", "extra"):
        assert (out[name].status_code, code_of(out[name])) == (400, "input.invalid")
    assert out["bad"].json()["error"]["param"] == "proxy" and "nonsense" not in out["bad"].text
    assert (out["ghost"].status_code, code_of(out["ghost"])) == (404, "resource.not_found")
    assert db.one("SELECT proxy_ok, proxy_checked_at, check_due_at, strikes FROM accounts WHERE id = %s", a) == before


def test_the_proxy_password_of_a_patch_is_sealed(db, pair):
    a = new_account(db, label="x", checked=True)

    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.patch("/admin/accounts/%s" % a, json={"proxy": "h.example:80:bob:" + PROXY_PASSWORD},
                                      headers=h)

    r = run_app(db, scenario)
    assert r.status_code == 200 and PROXY_PASSWORD not in r.text
    sealed = bytes(db.one("SELECT proxy_auth_enc FROM accounts WHERE id = %s", a)[0])
    assert sealed.startswith(b"rs1") and crypto.open_blob(sealed, [pair.private]) == ("bob:" + PROXY_PASSWORD).encode()


# ---- commands ---------------------------------------------------------------------------------------------------
@mutation(ROUTES, 'account_id=None if kind == "delete_account" else account_id,', "account_id=account_id,",
          expect="its own row names no account")
def test_every_account_action_only_queues_a_command_and_answers_its_id_and_touches_no_account_column(db):
    a = new_account(db, label="used", checked=True, proxy=("10.0.0.1", 80))
    db.run("UPDATE accounts SET strikes = 2, cooldown_until = now() + interval '1 hour', cooldown_reason = 'rl', "
           "proxy_ok = false, proxy_checked_at = now() WHERE id = %s", a)
    before = account_dump(db, a)

    async def scenario(env):
        h = await env.admin_headers()
        calls = [("DELETE", "/admin/accounts/%s" % a), ("POST", "/admin/accounts/%s/wake" % a),
                 ("POST", "/admin/accounts/%s/check" % a), ("POST", "/admin/accounts/%s/proxy-recheck" % a),
                 ("POST", "/admin/providers/fake/check-all"), ("POST", "/admin/providers/fake/refresh-catalog")]
        return [await env.client.request(m, p, headers=h) for m, p in calls]

    answers = run_app(db, scenario)
    ids = []
    for r in answers:
        assert r.status_code == 202 and set(r.json()) == {"command_id"}, r.text
        ids.append(uuid.UUID(r.json()["command_id"]))
    rows = {k: (acct, args, status, provider) for k, acct, args, status, provider in db.all(
        "SELECT kind, account_id, args, status, provider FROM commands")}
    assert set(rows) == {"delete_account", "wake", "check", "proxy_recheck", "check_all", "refresh_catalog"}
    assert len(set(ids)) == 6 and all(v[2:] == ("queued", "fake") for v in rows.values())
    assert rows["delete_account"][0] is None and rows["delete_account"][1] == {"account_id": str(a)}, (
        "its own row names no account")
    for kind in ("wake", "check", "proxy_recheck"):
        assert rows[kind][0] == a and rows[kind][1] == {"account_id": str(a)}
    assert rows["check_all"][1] == {} and rows["check_all"][0] is None
    assert account_dump(db, a) == before, "the api wrote the account row"


def test_an_action_on_a_missing_account_or_unknown_provider_queues_nothing(db):
    async def scenario(env):
        h = await env.admin_headers()
        ghost = uuid.uuid4()
        out = [await env.client.request(m, p, headers=h) for m, p in (
            ("DELETE", "/admin/accounts/%s" % ghost), ("POST", "/admin/accounts/%s/wake" % ghost),
            ("POST", "/admin/accounts/%s/check" % ghost), ("POST", "/admin/accounts/%s/proxy-recheck" % ghost),
            ("POST", "/admin/providers/ghost/check-all"), ("POST", "/admin/providers/ghost/refresh-catalog"))]
        return out

    for r in run_app(db, scenario):
        assert (r.status_code, code_of(r)) == (404, "resource.not_found")
    assert db.one("SELECT count(*) FROM commands")[0] == 0


def test_the_page_polls_a_command_by_id_and_sees_the_workers_result_but_never_the_args(db):
    a = new_account(db, label="x", checked=True)
    queued = new_command(db, "wake", account_id=a, args={"account_id": str(a), "sealed": "c2VjcmV0"})

    async def scenario(env):
        h = await env.admin_headers()
        first = await env.client.get("/admin/commands/%s" % queued, headers=h)
        db.run("UPDATE commands SET status = 'done', result = %s, attempts = 1, finished_at = now() WHERE id = %s",
               Jsonb({"woke": str(a)}), queued)
        second = await env.client.get("/admin/commands/%s" % queued, headers=h)
        ghost = await env.client.get("/admin/commands/%s" % uuid.uuid4(), headers=h)
        return first, second, ghost

    first, second, ghost = run_app(db, scenario)
    assert (first.json()["status"], first.json()["result"], first.json()["finished_ms"]) == ("queued", None, None)
    assert second.json()["status"] == "done" and second.json()["result"] == {"woke": str(a)}
    assert second.json()["kind"] == "wake" and second.json()["account_id"] == str(a)
    assert "args" not in second.json() and "c2VjcmV0" not in second.text
    assert ghost.status_code == 404


@mutation(MIG_NO_DELETE, "REVOKE DELETE ON accounts FROM relay_api;", "SELECT 1;", expect="InsufficientPrivilege")
def test_under_the_api_role_an_account_cannot_be_deleted_nor_a_worker_owned_or_secret_column_written(db):
    a = new_account(db, label="x", checked=True)
    owned = "SELECT %s FROM accounts WHERE id = %%s" % ", ".join(WORKER_OWNED)
    before = db.one(owned, a)

    async def scenario(env):
        refused = ["DELETE FROM accounts WHERE id = %s"] + [
            "UPDATE accounts SET %s WHERE id = %%s" % assignment for assignment in (
                "check_due_at = now()", "cooldown_until = now()", "cooldown_reason = 'x'", "strikes = 9",
                "proxy_ok = true", "proxy_checked_at = now()", "credential_enc = '\\x00'", "credential_gen = 9",
                "identity = 'stolen'")]
        for sql in refused:
            async with env.pool.connection() as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute(sql, (a,))
        async with env.pool.connection() as conn:         # what it may do
            await conn.execute("UPDATE accounts SET label = 'ok', proxy_host = 'h', updated_at = now() WHERE id = %s",
                               (a,))
            await conn.execute("INSERT INTO commands (provider, kind, account_id) VALUES ('fake', 'check', %s)", (a,))

    run_app(db, scenario)
    assert db.one("SELECT label FROM accounts WHERE id = %s", a) == ("ok",)
    assert db.one(owned, a) == before


# ---- screenshot -------------------------------------------------------------------------------------------------
def test_the_screenshot_of_an_account_is_served_as_a_png_and_a_missing_one_is_404(db):
    a, b = new_account(db, label="shot", checked=True), new_account(db, label="none", checked=True)
    png = b"\x89PNG\r\n\x1a\n-the-latest-shot"

    async def scenario(env):
        h = await env.admin_headers()
        key = "%s/debug/0.png" % a
        await env.store.put_bytes(key, png, "image/png")
        db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, %s)", a, key)
        ok = await env.client.get("/admin/accounts/%s/screenshot" % a, headers=h)
        none = await env.client.get("/admin/accounts/%s/screenshot" % b, headers=h)
        db.run("INSERT INTO account_shots (account_id, object_key) VALUES (%s, 'gone/debug/0.png')", b)
        gone = await env.client.get("/admin/accounts/%s/screenshot" % b, headers=h)
        await env.store.remove(key)
        return ok, none, gone

    ok, none, gone = run_app(db, scenario)
    assert ok.status_code == 200 and ok.content == png and ok.headers["content-type"] == "image/png"
    assert ok.headers["cache-control"] == "no-store" and ok.headers["x-content-type-options"] == "nosniff"
    assert none.status_code == 404 and gone.status_code == 404



LABEL_ONLY = "label_only = fields == {\"label\"}"


@mutation(ROUTES, LABEL_ONLY, "label_only = False", expect="a label edit is always allowed")
def test_a_label_edit_waits_for_a_worker_that_holds_the_account_row_and_is_never_refused(db, pair):
    """A worker transaction holds the row (FOR NO KEY UPDATE) for 4.5 s, past the 3 s lock_timeout of every session:
    the label edit waits for it and succeeds (the 409 for proxy and enable is tested above)."""
    import asyncio

    a = new_account(db, label="busy", checked=True)

    async def worker_holds(held: asyncio.Event, release: asyncio.Event) -> None:
        async with await psycopg.AsyncConnection.connect(db.dsn, autocommit=False) as conn:
            await conn.execute("SELECT 1 FROM accounts WHERE id = %s FOR NO KEY UPDATE", (a,))
            held.set()
            await release.wait()
            await conn.commit()

    async def scenario(env):
        h = await env.admin_headers()
        held, release = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(worker_holds(held, release))
        try:
            await asyncio.wait_for(held.wait(), 5)
            labelling = asyncio.create_task(
                env.client.patch("/admin/accounts/%s" % a, json={"label": "renamed"}, headers=h))
            await asyncio.sleep(4.5)
            assert not labelling.done(), "a label edit is always allowed: it waits for the row"
            release.set()
            await holder
            return await asyncio.wait_for(labelling, 5)
        finally:
            release.set()
            await asyncio.gather(holder, return_exceptions=True)

    r = run_app(db, scenario)
    assert r.status_code == 200 and r.json()["label"] == "renamed", "a label edit is always allowed: %s" % r.text[:200]
    assert db.one("SELECT label FROM accounts WHERE id = %s", a) == ("renamed",)
