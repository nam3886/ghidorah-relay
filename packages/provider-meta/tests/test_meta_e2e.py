"""relay e2e --provider meta: it refuses to spend a job unasked, always disables its key, and judges the file it got.

No network and no database: the pool, the transaction and the key calls are fakes, and so is the API run."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from relay_core.testing.mutation import mutation
from relay_meta import e2e

E2E = "packages/provider-meta/src/relay_meta/e2e.py"
ENV = {"RELAY_E2E_SPEND": "yes", "DATABASE_URL": "postgresql://nowhere/x"}


@pytest.fixture
def fake_db(monkeypatch):
    seen = SimpleNamespace(made=[], disabled=[])

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

    monkeypatch.setattr(e2e, "open_pool", open_pool)
    monkeypatch.setattr(e2e, "tx", tx)
    monkeypatch.setattr(e2e, "keys", SimpleNamespace(create_key=create_key, set_enabled=set_enabled))
    return seen


@mutation(E2E, 'if env.get("RELAY_E2E_SPEND") != "yes":', "if False:", expect="== 2")
def test_it_refuses_to_spend_a_job_unless_asked(fake_db, monkeypatch, capsys):
    async def generate(base, raw):
        return 0
    monkeypatch.setattr(e2e, "_generate", generate)
    assert asyncio.run(e2e.run({"DATABASE_URL": "postgresql://nowhere/x"})) == 2
    assert asyncio.run(e2e.run({"RELAY_E2E_SPEND": "no", "DATABASE_URL": "postgresql://nowhere/x"})) == 2
    assert "RELAY_E2E_SPEND=yes" in capsys.readouterr().err
    assert fake_db.made == []


@mutation(E2E, "await keys.set_enabled(conn, made.id, False)", "pass", expect="the key was left enabled")
def test_the_key_is_disabled_when_the_run_fails(fake_db, monkeypatch):
    async def fails(base, raw):
        return 1

    async def breaks(base, raw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(e2e, "_generate", fails)
    assert asyncio.run(e2e.run(ENV)) == 1
    monkeypatch.setattr(e2e, "_generate", breaks)
    with pytest.raises(RuntimeError):
        asyncio.run(e2e.run(ENV))
    assert fake_db.disabled == [(7, False), (7, False)], "the key was left enabled"
    assert fake_db.made[0]["wallets"] == {("meta", "image"): (5, 5)} and fake_db.made[0]["models"] == ["meta-image"]


WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20_000


@mutation(E2E, "return signed and len(data)", "return True or signed and len(data)", expect="not e2e.image_ok")
def test_only_a_real_image_of_some_size_passes():
    assert e2e.image_ok(WEBP)
    assert e2e.image_ok(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20_000) and e2e.image_ok(b"\xff\xd8\xff" + b"\x00" * 20_000)
    assert not e2e.image_ok(b"<html>" + b"\x00" * 20_000) and not e2e.image_ok(WEBP[:500])
