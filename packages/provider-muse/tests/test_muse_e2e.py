"""relay e2e --provider muse: it refuses to spend quota unasked or with an unknown case, and always disables its key.

No network and no database: the pool, the transaction, the key calls and the Usage reads are fakes."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from relay_core.testing.mutation import mutation
from relay_muse import e2e

E2E = "packages/provider-muse/src/relay_muse/e2e.py"
ENV = {"RELAY_E2E_SPEND": "yes", "DATABASE_URL": "postgresql://nowhere/x"}


@pytest.fixture
def fake_db(monkeypatch):
    seen = SimpleNamespace(made=[], disabled=[], usage=0)

    class Pool:
        async def close(self):
            pass

    async def open_pool(dsn, **kw):
        return Pool()

    @asynccontextmanager
    async def tx(pool):
        yield object()

    async def create_key(conn, **kw):
        seen.made.append(kw)
        return SimpleNamespace(id=7, raw="rk_secret")

    async def set_enabled(conn, key_id, enabled):
        seen.disabled.append((key_id, enabled))

    async def usage(pool):
        seen.usage += 1
        return {"muse-1": (9, True, None)}

    monkeypatch.setattr(e2e, "open_pool", open_pool)
    monkeypatch.setattr(e2e, "tx", tx)
    monkeypatch.setattr(e2e, "usage", usage)
    monkeypatch.setattr(e2e, "keys", SimpleNamespace(create_key=create_key, set_enabled=set_enabled))
    return seen


@mutation(E2E, 'if env.get("RELAY_E2E_SPEND") != "yes":', "if False:", expect="== 2")
def test_it_refuses_to_spend_unless_asked_or_with_an_unknown_case(fake_db, capsys):
    assert asyncio.run(e2e.run({"DATABASE_URL": "postgresql://nowhere/x"})) == 2
    assert asyncio.run(e2e.run({**ENV, "RELAY_E2E_CASES": "img-n1-1:1,nope"})) == 2
    assert "RELAY_E2E_SPEND=yes" in capsys.readouterr().err
    assert fake_db.made == [] and fake_db.usage == 0


@mutation(E2E, "await keys.set_enabled(conn, made.id, False)", "pass", expect="the key was left enabled")
def test_the_key_is_disabled_whatever_the_cases_did_and_usage_is_read_around_them(fake_db, monkeypatch):
    async def fails(pool, base, raw, names):
        return 1

    async def breaks(pool, base, raw, names):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(e2e, "_cases", fails)
    assert asyncio.run(e2e.run({**ENV, "RELAY_E2E_CASES": "t2v-5s"})) == 1
    assert fake_db.usage == 2
    monkeypatch.setattr(e2e, "_cases", breaks)
    with pytest.raises(RuntimeError):
        asyncio.run(e2e.run(ENV))
    assert fake_db.disabled == [(7, False), (7, False)], "the key was left enabled"
    assert fake_db.made[0]["models"] == ["muse-image", "muse-video"] and fake_db.made[0]["declared"] == ["muse"]
