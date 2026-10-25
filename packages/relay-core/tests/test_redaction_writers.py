"""Redaction at the real writers (I24): an event, a `jobs.error`, an account error and a `callback_last`
value over 2,000 characters, each carrying a planted long secret, are stored as `[omitted len=N]`, and the secret is in
no column of any table afterwards. The writers are the production ones: the job store, the account check and the
webhook sender, running under the worker role."""

import asyncio
import uuid

import httpx
from accountkit import KitEngine, beating, command, make_env, new_account, new_command, run_as
from hookkit import Receiver, hook, hook_job, resolver
from jobkit import CAPS, POOLS, account, claim, create, key, run
from test_alerts import Reporting, applied, expired, queued

from relay_core.db import tx
from relay_core.jobs import store
from relay_core.jobs.checks import check_account
from relay_core.jobs.commands import run_one
from relay_core.providers import (
    Accepted,
    Done,
    Facts,
    Health,
    Output,
    Pending,
    PollResult,
    ProxyVerdict,
    SubmitResult,
)
from relay_core.testing.mutation import mutation

EVENTS = "packages/relay-core/src/relay_core/jobs/events.py"
FAIL = "packages/relay-core/src/relay_core/jobs/fail.py"
CHECKS = "packages/relay-core/src/relay_core/jobs/checks.py"
WEBHOOKS = "packages/relay-core/src/relay_core/jobs/webhooks.py"
COMMANDS = "packages/relay-core/src/relay_core/jobs/commands.py"
APPLY = "packages/relay-core/src/relay_core/jobs/apply.py"
ALERTS = "packages/relay-core/src/relay_core/alerts.py"
UNCLEAR_REDACTED = "    stored = redact_text(cleaned)[:REPLY_LIMIT]\n"
UNCLEAR_RAW = "    stored = cleaned[:REPLY_LIMIT]\n"

EVENT_REDACTED = 'redact_log(text or "")'
EVENT_RAW = '(text or "")'
ERROR_REDACTED = '"error": redact_log(detail) if detail else r.message'
ERROR_RAW = '"error": detail if detail else r.message'
ACCOUNT_REDACTED = "    return redact_log(str(e) or type(e).__name__)"
ACCOUNT_RAW = "    return str(e) or type(e).__name__"
ANSWER_REDACTED = 'text = "HTTP %d: %s" % (r.status_code, redact_log(r.content.decode("utf-8", "replace")))'
ANSWER_RAW = 'text = "HTTP %d: %s" % (r.status_code, r.content.decode("utf-8", "replace"))'


GONE = "[redacted] [redacted]"      # the 2,400 character run and the token after it, each replaced
RESULT_REDACTED ="Jsonb(redact(dict(result)))"
RESULT_RAW = "Jsonb(dict(result))"
SNAPSHOT_REDACTED = "(env.provider, str(name), Jsonb(redact(data)))"
SNAPSHOT_RAW = "(env.provider, str(name), Jsonb(data))"
PATCH_REDACTED = '"p": Jsonb(redact(dict(health.state_patch))),\n         "read"'
PATCH_RAW = '"p": Jsonb(dict(health.state_patch)),\n         "read"'
STATE_REDACTED = "return Jsonb(redact(dict(patch)))"
STATE_RAW = "return Jsonb(dict(patch))"
META_REDACTED = '"meta": redact(dict(o.meta))'
META_RAW = '"meta": dict(o.meta)'


def long_value(tag: str) -> tuple[str, str]:
    """(the 2,500 character value, the secret planted in it): the secret is a token a pattern alone would catch."""
    secret = "sk-" + (tag * 8 + "A1b2C3d4" * 8)
    return "x" * 2400 + " " + secret, secret


def where_it_is(db, secret: str) -> list[str]:
    """Every table whose rows mention the secret anywhere, in any column."""
    tables = [r[0] for r in db.all("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")]
    return [t for t in tables if db.one('SELECT count(*) FROM relay."%s" x WHERE to_jsonb(x)::text LIKE %%s' % t,
                                        "%" + secret + "%")[0]]


@mutation(EVENTS, EVENT_REDACTED, EVENT_RAW,
           expect="db.one(\"SELECT text FROM job_events WHERE job_id = %s AND stage = 'probe'\", event_job) == (")
@mutation(FAIL, ERROR_REDACTED, ERROR_RAW,
           expect="db.one(\"SELECT error FROM jobs WHERE id = %s\", failed_job) == (marker % len(error_text),)")
@mutation(CHECKS, ACCOUNT_REDACTED, ACCOUNT_RAW,
           expect="db.one(\"SELECT last_error FROM accounts WHERE id = %s\", account) == (marker % len(account_text),)")
@mutation(WEBHOOKS, ANSWER_REDACTED, ANSWER_RAW, expect="'[omitted len=2486]' == 'HTTP 500: [omitted len=2476]'")
def test_a_long_value_is_stored_as_an_omitted_marker_by_every_writer(db):
    k = db.key(("fake", None, None))
    event_job, failed_job, hook_target = db.job(k, provider="fake"), db.job(k, provider="fake"), hook_job(db, k)
    account = new_account(db, checked=True)
    event_text, event_secret = long_value("e")
    error_text, error_secret = long_value("f")
    account_text, account_secret = long_value("a")
    answer_text, answer_secret = long_value("w")
    engine = KitEngine()

    async def hang_up(ctx):
        raise RuntimeError(account_text)

    engine.on_check = hang_up
    receiver = Receiver(httpx.Response(500, content=answer_text.encode()))

    async def scenario(p):
        async with tx(p) as conn:
            await store.add_event(conn, event_job, "engine", "probe", event_text)
        assert await store.fail_job(p, failed_job, expect=("queued",), code="account.prompt_mismatch",
                                    detail=error_text, pools={})
        env = make_env(p, engine)
        async with beating(env):
            assert (await check_account(env, account)).outcome == "failed"
        await store.deliver_due(p, resolver=resolver, transport=receiver.transport)
        await asyncio.sleep(0)

    run_as(db.worker_dsn, scenario)

    marker = "[omitted len=%d]"
    assert db.one("SELECT text FROM job_events WHERE job_id = %s AND stage = 'probe'", event_job) == (
        marker % len(event_text),)
    assert db.one("SELECT error FROM jobs WHERE id = %s", failed_job) == (marker % len(error_text),)
    assert db.one("SELECT last_error FROM accounts WHERE id = %s", account) == (marker % len(account_text),)
    assert hook(db, hook_target)[2] == "HTTP 500: " + marker % len(answer_text)
    for secret in (event_secret, error_secret, account_secret, answer_secret):
        assert where_it_is(db, secret) == [], "the secret is stored in " + ", ".join(where_it_is(db, secret))


@mutation(COMMANDS, RESULT_REDACTED, RESULT_RAW, expect="AssertionError: the secret is stored in commands")
@mutation(COMMANDS, SNAPSHOT_REDACTED, SNAPSHOT_RAW,
           expect="AssertionError: the secret is stored in provider_snapshots")
@mutation(CHECKS, PATCH_REDACTED, PATCH_RAW,
           expect="db.one(\"SELECT provider_state FROM accounts WHERE id = %s\", account_id)[0] == {\"note\": GONE}")
def test_a_command_result_a_snapshot_and_a_check_state_patch_are_redacted_before_they_are_stored(db):
    account_id = new_account(db, checked=True, due_s=None)
    result_text, result_secret = long_value("r")
    snap_text, snap_secret = long_value("s")
    patch_text, patch_secret = long_value("p")
    proxied = new_account(db, checked=True, proxy=("10.0.0.3", 8080))
    listing = new_command(db, "proxy_recheck", account_id=proxied)
    refresh = new_command(db, "refresh_catalog")

    async def scenario(p):
        engine = KitEngine()
        engine.verdict = ProxyVerdict(False, result_text)      # the probe's reason is stored in the command result
        engine.results["refresh_catalog"] = {"snapshots": {"templates": {"items": [{"url": snap_text}]}}}
        engine.health = Health("alive", state_patch={"note": patch_text})
        env = make_env(p, engine)
        assert await run_one(env) and await run_one(env)
        async with beating(env):
            assert (await check_account(env, account_id)).outcome == "checked"

    run_as(db.worker_dsn, scenario)
    assert command(db, listing)["status"] == "done" and command(db, refresh)["status"] == "done"
    assert db.one("SELECT provider_state FROM accounts WHERE id = %s", account_id)[0] == {"note": GONE}
    for secret in (result_secret, snap_secret, patch_secret):
        assert where_it_is(db, secret) == [], "the secret is stored in " + ", ".join(where_it_is(db, secret))


@mutation(APPLY, STATE_REDACTED, STATE_RAW, expect="state[\"debug\"] == state[\"conv\"] == state[\"pct\"] == GONE")
@mutation(APPLY, META_REDACTED, META_RAW, expect="state[\"outputs\"][0][\"meta\"] == {\"signed\": GONE}")
def test_a_long_value_in_a_job_state_patch_or_an_output_meta_is_redacted_before_it_is_stored(db):
    k, _ = key(db), account(db)
    planted = {tag: long_value(tag) for tag in ("a", "b", "c", "d")}
    token = uuid.uuid4()

    async def scenario(p):
        await create(p, k, "x")
        got = await claim(p)
        await store.report_ref(p, got, "ref-1", {"debug": planted["a"][0]})
        accepted = SubmitResult(Accepted("ref-1", {"conv": planted["b"][0]}))
        assert await store.apply_submit(p, got, accepted, caps=CAPS, pools=POOLS) == "waiting"
        db.run("UPDATE jobs SET poll_token = %s", token)
        assert await store.apply_poll(p, got.job_id, token, PollResult(Pending("rendering", 30, {
            "pct": planted["c"][0]})), caps=CAPS, pools=POOLS) == "waiting"
        db.run("UPDATE jobs SET poll_token = %s", token)
        outputs = (Output("https://cdn/x.mp4", "video/mp4", 0, "up-1", {"signed": planted["d"][0]}),)
        assert await store.apply_poll(p, got.job_id, token, PollResult(Done(outputs)), caps=CAPS,
                                      pools=POOLS) == "fetching"

    run(db, scenario)
    state = db.one("SELECT provider_state FROM jobs")[0]
    assert state["debug"] == state["conv"] == state["pct"] == GONE
    assert state["outputs"][0]["meta"] == {"signed": GONE}
    for _, secret in planted.values():
        assert where_it_is(db, secret) == [], "the secret is stored in " + ", ".join(where_it_is(db, secret))


def test_a_long_upstream_model_fact_is_redacted_before_it_is_stored(db):
    k, _ = key(db), account(db)
    text, secret = long_value("m")
    token = uuid.uuid4()

    async def scenario(p):
        await create(p, k, "x")
        got = await claim(p)
        assert await store.apply_submit(p, got, SubmitResult(Accepted("ref-1")), caps=CAPS, pools=POOLS) == "waiting"
        db.run("UPDATE jobs SET poll_token = %s", token)
        done = PollResult(Done((Output("u", "video/mp4", 0, "up-1"),), Facts(5, text, "reply")))
        await store.apply_poll(p, got.job_id, token, done, caps=CAPS, pools=POOLS)

    run(db, scenario)
    assert where_it_is(db, secret) == []


@mutation(ALERTS, UNCLEAR_REDACTED, UNCLEAR_RAW, expect="an unclear reply writer stored a planted secret")
def test_both_unclear_reply_writers_store_the_text_redacted(db):
    """The submit result and the render-deadline failure each store a reply carrying a planted secret; the secret is
    in no column of any table afterwards."""
    secret = "rk-" + "a1B2" * 8
    reply = "Sorry, try again. Authorization: Bearer %s" % secret
    applied(db, queued(db), [Accepted("ref-1", taken=False, unclear=reply)])
    expired(db, Reporting(reply), prompt="deadline")
    rows = db.all("SELECT text FROM unclear_replies")
    assert len(rows) == 2, "premise: both writers stored a row"
    with db.connect() as c:
        tables = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'relay'")]
        for table in tables:
            dump = [r[0] for r in c.execute("SELECT t::text FROM %s t" % table)]
            assert not any(secret in line for line in dump), "an unclear reply writer stored a planted secret: " + table
