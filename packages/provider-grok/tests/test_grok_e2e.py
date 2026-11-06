"""relay e2e --provider grok: it refuses to spend a job unasked, always disables its key, and judges the file it got.

No network and no database: the pool, the transaction and the key calls are fakes, and so is the API run."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from relay_core.testing.mutation import mutation
from relay_grok import e2e

E2E = "packages/provider-grok/src/relay_grok/e2e.py"
ENV = {"RELAY_E2E_SPEND": "yes", "DATABASE_URL": "postgresql://nowhere/x"}
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20_000


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
def test_it_refuses_to_spend_unless_asked(fake_db, monkeypatch, capsys):
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
    assert fake_db.made[0]["wallets"] == {"*": (5, 5), "grok": (5, 5)} and fake_db.made[0]["models"] == ["grok-image"]


@mutation(E2E, "return data[:3] ==", "return len(data) > 0 or data[:3] ==", expect="not e2e.jpeg_ok")
def test_only_a_jpeg_of_some_size_passes():
    assert e2e.jpeg_ok(JPEG)
    assert not e2e.jpeg_ok(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20_000) and not e2e.jpeg_ok(b"<html>" + b"\x00" * 20_000)
    assert not e2e.jpeg_ok(JPEG[:500])


def test_the_run_draws_one_1_1_image_from_text(monkeypatch, capsys):
    posts: list[dict] = []

    def site(request):
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "job-1"})
        if request.url.path.endswith("/content/0"):
            return httpx.Response(200, content=JPEG)
        return httpx.Response(200, json={"status": "done"})
    real = httpx.AsyncClient
    monkeypatch.setattr(e2e.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(site), **kw))
    assert asyncio.run(e2e._generate("http://api.invalid", "rk_x")) == 0
    assert posts == [{"model": "grok-image", "ratio": "1:1", "n": 1,
                      "prompt": "A paper boat drifting on a calm pond at sunrise"}]
    assert "rk_x" not in capsys.readouterr().out
