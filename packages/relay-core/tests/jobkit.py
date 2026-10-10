"""Shared helpers for the job store tests: a fake provider, accounts, jobs, and a driver for one submit."""

import asyncio
import dataclasses
import uuid

from relay_core import pool
from relay_core.db import open_pool
from relay_core.frames import CORE_FRAME
from relay_core.jobs import store
from relay_core.providers import CreateRequest, FrameUse
from relay_core.testing.fake_provider import FakeCtx, FakeEngine, FakeSettings, FakeSpec

SPEC = FakeSpec()
CAPS = SPEC.caps
PROVIDER = SPEC.name
SETTINGS = FakeSettings(SPEC)
POOLS = pool.pool_configs(CAPS, SETTINGS.get)
WALLETS = ((PROVIDER, "video", None, None), (PROVIDER, "image", None, None))


def run(db, scenario, max_size=10):
    async def main():
        p = await open_pool(db.dsn, max_size=max_size)
        try:
            return await scenario(p)
        finally:
            await p.close()

    return asyncio.run(main())


def key(db, *, wallets=WALLETS, max_unfinished=None) -> uuid.UUID:
    key_id = db.key(*wallets)
    if max_unfinished is not None:
        db.run("UPDATE api_keys SET max_unfinished = %s WHERE id = %s", max_unfinished, key_id)
    return key_id


def account(db, *, left=None, pool_name="video", state="available", checked=True, used_ago_s=None,
            cooldown_s=None) -> uuid.UUID:
    """An account of the fake provider. `left` pins the pool's remaining capacity (None = no capacity row yet, so
    the claim creates it from the budget); `used_ago_s` sets last_used_at for the LRU order."""
    account_id = uuid.uuid4()
    db.run("INSERT INTO accounts (id, provider, credential_enc, state, last_checked_at, last_used_at, cooldown_until) "
           "VALUES (%s, %s, '\\x01', %s, CASE WHEN %s THEN clock_timestamp() END, "
           "clock_timestamp() - make_interval(secs => %s), clock_timestamp() + make_interval(secs => %s))",
           account_id, PROVIDER, state, checked, used_ago_s, cooldown_s)
    if left is not None:
        db.run("INSERT INTO account_capacity (account_id, pool, left_n, source, day_anchor) "
               "VALUES (%s, %s, %s, 'reported', (clock_timestamp() AT TIME ZONE 'UTC')::date)",
               account_id, pool_name, left)
    return account_id


def new_job(key_id, prompt="a prompt", *, model=PROVIDER + "-video", idem=None, body=None, inputs=(), frame=None,
            **options):
    """A NewJob planned by the fake spec; `options` steer the plan (account_cost, completion, price, ...). The plan
    carries the core frame, as the plan step gives a provider without frames; `frame` passes another."""
    plan = dataclasses.replace(SPEC.plan(CreateRequest(model=model, prompt=prompt, options=options), SETTINGS, {}),
                               frame=frame or FrameUse(None, CORE_FRAME))
    sent = {"endpoint": "video", "model": model, "prompt": prompt, "duration": None, "ratio": None, "n": None,
            "options": {}, "title": None, "callback_url": None, "images": [{"input": m.idx} for m in inputs]}
    return store.NewJob(PROVIDER, key_id, plan, prompt, idem_key=idem,
                        idem_body_hash=None if idem is None else store.body_hash(body or {"prompt": prompt}),
                        inputs=tuple(inputs), request=sent)


async def create(p, key_id, prompt="a prompt", *, settings=SETTINGS, **kw) -> store.Created:
    return await store.create_job(p, new_job(key_id, prompt, **kw), settings)


async def claim(p, *, owner="w1", settings=SETTINGS, caps=CAPS, **kw):
    return await store.claim_one(p, provider=PROVIDER, caps=caps, settings=settings, owner=owner, **kw)


async def submit(p, claimed, engine: FakeEngine):
    """What the engine loop does for one claimed job: run the engine, then apply its result."""
    view = await store.job_view(p, claimed.job_id)
    ctx = FakeCtx(mark_sent=lambda: store.mark_sent(p, claimed))
    result = await engine.submit(ctx, view)
    return await store.apply_submit(p, claimed, result, caps=CAPS, pools=POOLS)


def status(db, job_id) -> str:
    return db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0]


def events(db, job_id) -> list[str]:
    return [r[0] for r in db.all("SELECT stage FROM job_events WHERE job_id = %s ORDER BY id", job_id)]
