"""scripts/import-accounts.py against the real admin API (sealing, parsing, the rows), served on a loopback port by
uvicorn over the per-run Postgres. Every value here is generated; the awkward proxy passwords are the shapes that
used to be corrupted or refused on the way in."""

import asyncio
import importlib.util
import json
import secrets
from pathlib import Path

import pytest
import uvicorn
from apikit import ADMIN_PASSWORD, ADMIN_USER, serving

from relay_core import crypto

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "import-accounts.py"
_spec = importlib.util.spec_from_file_location("import_accounts_real", SCRIPT)
imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(imp)

PASSWORDS = ["plain1234", "has%41pct", "has#hash", "has/slash", "has?query", "has:colon", "has@at", "has space;,x"]


@pytest.fixture
def pair(monkeypatch):
    keys = crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)
    return keys


def test_the_script_imports_into_the_real_api_and_every_proxy_password_is_stored_as_typed(db, pair, tmp_path,
                                                                                         monkeypatch, capsys):
    cookies = ["ck" + secrets.token_hex(10) for _ in PASSWORDS]
    users = ["us" + secrets.token_hex(3) for _ in PASSWORDS]
    jars, proxies = tmp_path / "jars.jsonl", tmp_path / "proxies.txt"
    jars.write_text("".join(json.dumps({"cookies": [{"name": "sid", "value": v}]}) + "\n" for v in cookies))
    proxies.write_text("".join("203.0.113.%d:%d:%s:%s\n" % (i + 1, 8000 + i, u, p)
                               for i, (u, p) in enumerate(zip(users, PASSWORDS, strict=True))))
    jars.chmod(0o600)
    proxies.chmod(0o600)
    monkeypatch.setattr(imp, "ask_password", lambda: ADMIN_PASSWORD)

    async def scenario() -> int:
        async with serving(db) as env:
            await env.admin_headers()                                  # makes the admin user
            server = uvicorn.Server(uvicorn.Config(env.app, host="127.0.0.1", port=0, log_level="warning",
                                                   lifespan="off"))
            task = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.05)
            port = server.servers[0].sockets[0].getsockname()[1]
            try:
                return await asyncio.to_thread(imp.main, [
                    "--cookies", str(jars), "--proxies", str(proxies), "--provider", "fake", "--label-prefix", "n11",
                    "--base", "http://127.0.0.1:%d" % port, "--username", ADMIN_USER])
            finally:
                server.should_exit = True
                await task

    code = asyncio.run(scenario())
    shown = capsys.readouterr()
    out = shown.out + shown.err
    assert code == 0, out
    assert "imported: 8, rejected: 0" in out and "accounts before: 0" in out and "accounts after: 8" in out, out
    for secret in (*cookies, *users, *PASSWORDS, ADMIN_PASSWORD):
        assert secret not in out, "a secret was printed"
    rows = db.all("SELECT label, proxy_host, proxy_port, proxy_auth_enc, credential_enc FROM accounts ORDER BY label")
    assert [r[0] for r in rows] == ["n11-%02d" % i for i in range(8)]
    for i, row in enumerate(rows):
        assert (row[1], row[2]) == ("203.0.113.%d" % (i + 1), 8000 + i)
        assert crypto.open_blob(bytes(row[3]), [pair.private]) == ("%s:%s" % (users[i], PASSWORDS[i])).encode(), \
            "a proxy password was stored as something else: %r" % PASSWORDS[i]
        assert cookies[i].encode() in crypto.open_blob(bytes(row[4]), [pair.private])
