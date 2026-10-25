"""A `meta_session` that vibes rotates in a Set-Cookie is written back to the account (none was seen on 25/10 over
reads, a batch create, a generate and its polls; the session is kept the day vibes starts rotating it): only a
site-wide one, never over a newer rotation another run wrote, cut short when it hangs. Over a fake HTTP transport: no
network."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx

from relay_core.testing.mutation import mutation
from relay_vibes.engine import http
from relay_vibes.engine.check import check_account
from relay_vibes.engine.http import keep_rotated_session, rotated_session

HTTP = "packages/provider-vibes/src/relay_vibes/engine/http.py"
ME = {"user": {"id": "u1", "username": "nam", "accountStatus": "ACTIVE"}}
OLD = "sess-old"


def vibes(set_cookie=None, me_status=200):
    def handle(request):
        headers = [("set-cookie", set_cookie)] if set_cookie and request.url.path == "/api/auth/me" else []
        body = ME if request.url.path == "/api/auth/me" else {"upsell": None}
        return httpx.Response(me_status if request.url.path == "/api/auth/me" else 200, json=body, headers=headers)
    return httpx.MockTransport(handle)


def check(transport, error=None):
    writes = []

    async def renew_credential(blob, merge=None):
        if error:
            raise error
        writes.append(json.loads(blob))
        return blob
    blob = json.dumps({"meta_session": OLD, "cookies": [{"name": "meta_session", "value": OLD, "domain": "vibes.ai"},
                                                        {"name": "cookie_ack", "value": "true", "domain": "vibes.ai"}]})
    ctx = SimpleNamespace(account=SimpleNamespace(id="a1", credential=blob.encode(), proxy=None),
                          renew_credential=renew_credential)
    return asyncio.run(check_account(ctx, None, transport=transport)), writes


ROTATED = "meta_session=sess-new; Path=/; Domain=vibes.ai; Secure; HttpOnly; SameSite=Lax"


@mutation(HTTP, "            if self.ctx is not None and exc[0] is None:", "            if False:",
          expect="the rotated session")
def test_a_rotated_meta_session_is_written_back_in_the_sealed_shape():
    health, writes = check(vibes(ROTATED))
    assert health.state == "alive"
    assert len(writes) == 1, "the rotated session was not written back"
    assert writes[0]["meta_session"] == "sess-new"
    assert writes[0]["cookies"] == [{"name": "meta_session", "value": "sess-new", "domain": "vibes.ai"},
                                    {"name": "cookie_ack", "value": "true", "domain": "vibes.ai"}]


@mutation(HTTP, "        if signed_out or not new or not renewable(new) or new == started:",
          "        if not new:", expect="nothing new")
def test_nothing_is_written_for_the_same_session_an_empty_one_or_a_signed_out_answer():
    for cookie in (None, "meta_session=%s; Path=/" % OLD, 'meta_session=""; Path=/', "other=x; Path=/"):
        _, writes = check(vibes(cookie))
        assert writes == [], "nothing new was written back: %r" % cookie
    health, writes = check(vibes(ROTATED, me_status=401))
    assert health.state == "unknown" and writes == [], "a signed-out answer was written back"


def test_a_failed_write_never_fails_the_check():
    health, writes = check(vibes(ROTATED), error=RuntimeError("database down"))
    assert health.state == "alive" and writes == []


SCOPE = ('              and (c.path == "/" or not c.path_specified) and (c.domain or "").lstrip(".").lower() '
         '== SITE_HOST}')


@mutation(HTTP, SCOPE, "              }", expect="scope")
def test_only_a_site_wide_meta_session_for_vibes_ai_is_taken():
    for cookie in ("meta_session=sess-new; Path=/api; Domain=vibes.ai", "meta_session=sess-new; Path=/x"):
        _, writes = check(vibes(cookie))
        assert writes == [], "a cookie of another scope was taken: %r" % cookie
    jar = httpx.Cookies()
    jar.set("meta_session", "sess-new", domain="api.vibes.ai", path="/")
    assert rotated_session(jar) is None, "a subdomain's cookie was taken (scope)"
    jar.set("meta_session", "sess-other", domain="vibes.ai", path="/")
    assert rotated_session(jar) == "sess-other"
    jar.set("meta_session", "sess-third", domain=".vibes.ai", path="/")
    assert rotated_session(jar) is None, "two site-wide values at once are no rotation (scope)"
    _, writes = check(vibes("meta_session=sess-new"))          # host-only, default path: the site-wide cookie
    assert [w["meta_session"] for w in writes] == ["sess-new"]


class Db:
    """The core's renewal: compare-and-swap on what this run read, a lost swap merged onto a fresh read."""

    def __init__(self, blob):
        self.stored = blob

    def ctx(self):
        db, seen = self, {"plain": self.stored}

        async def renew_credential(blob, merge=None):
            if db.stored != seen["plain"]:
                seen["plain"] = db.stored
            db.stored = seen["plain"] = merge(seen["plain"], blob)
            return db.stored
        return SimpleNamespace(account=SimpleNamespace(id="a1", credential=self.stored),
                               renew_credential=renew_credential)


def jar_with(value):
    jar = httpx.Cookies()
    jar.set("meta_session", value, domain="vibes.ai", path="/")
    return jar


@mutation(HTTP, "            return with_session(current, new) if session_of(current) == started else current",
          "            return with_session(current, new)", expect="S3")
def test_two_runs_from_one_session_the_one_that_writes_second_never_overwrites_a_newer_rotation():
    db = Db(json.dumps({"meta_session": "S1-sess", "cookies": []}).encode())
    a, b = db.ctx(), db.ctx()                  # both runs loaded S1
    assert asyncio.run(keep_rotated_session(b, jar_with("S3-sess"), False)) is True
    assert asyncio.run(keep_rotated_session(a, jar_with("S2-sess"), False)) is True
    assert json.loads(db.stored)["meta_session"] == "S3-sess", "run A laid its older S2 over run B's S3"


@mutation(HTTP, "        return await asyncio.wait_for(renew(), RENEW_TIMEOUT_S)", "        return await renew()",
          expect="late")
def test_a_renewal_that_hangs_on_the_database_is_cut_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(http, "RENEW_TIMEOUT_S", 0.1)

    async def hung(blob, merge=None):
        await asyncio.sleep(2)
        return blob
    ctx = SimpleNamespace(account=SimpleNamespace(id="a1", credential=json.dumps({"meta_session": OLD}).encode()),
                          renew_credential=hung)
    t0 = time.monotonic()
    assert asyncio.run(keep_rotated_session(ctx, jar_with("sess-new"), False)) is False, "a late renewal was written"
    assert time.monotonic() - t0 < 1, "the renewal was late: it held the run back"
