"""dola keeps the cookies it rotates (measured 25/10 on the box: `ttwid` rotates on every load, the session cookies
`sessionid`, `sid_guard`, `sid_tt`, `uid_tt` and `msToken` never did): a check or a submit that read the account
logged in writes them back to the sealed credential; a run that did not read it logged in, or whose browser carries
another session, writes nothing. On a scripted page: no Chrome."""

import json

from kit_c import ACCEPTED, FakeCtx, FakeDriver, FakeSession, Scene, deps, sync

from relay_core.jobs.runctx import AccountView
from relay_core.providers import Accepted
from relay_core.testing.mutation import mutation
from relay_dola.engine.check import check_account

CK = "packages/provider-dola/src/relay_dola/engine/check.py"
SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
GUEST = {"cells": 0, "ids": [], "name": "", "modal": False,
         "passport": {"message": "error", "uid": False, "name": "account_info_error"}}
JAR = [{"name": "sessionid", "value": "S-FAKE", "domain": ".dola.com", "path": "/", "session": True},
       {"name": "uid_tt", "value": "U-FAKE", "domain": ".dola.com", "path": "/", "session": True},
       {"name": "ttwid", "value": "W-OLD", "domain": ".dola.com", "path": "/", "session": True}]


def account() -> AccountView:
    return AccountView("acct-1", "acct", json.dumps({"cookies": JAR}).encode(), 1, None, {}, {"video": None,
                                                                                               "image": None})


class Browser:
    """The browser's cookies at the end of the run: the jar with `ttwid` rotated (and `sessionid` as given)."""

    def __init__(self, sessionid="S-FAKE"):
        self.jar = [{**c, "expires": -1} for c in JAR if c["name"] != "sessionid"]
        self.jar[-1]["value"] = "W-NEW"
        if sessionid is not None:
            self.jar.append({**JAR[0], "value": sessionid, "expires": -1})

    async def cookies(self, urls):
        return self.jar


def renewing(ctx):
    ctx.writes = []

    async def renew_credential(blob, merge=None):
        ctx.writes.append(json.loads(blob))
        return blob
    ctx.renew_credential = renew_credential
    return ctx


def ttwid(written) -> str:
    return next(c["value"] for c in written["cookies"] if c["name"] == "ttwid")


async def check(browser, probe=None):
    log = []
    session = FakeSession()
    session.ctx = browser
    ctx = renewing(FakeCtx(log, account()))
    health = await check_account(ctx, ctx.settings, deps=deps(session, FakeDriver(log, None, probe=probe), calls=[]))
    return health, ctx.writes


@mutation(CK, "            await renew_jar(session.ctx, ctx, (BASE,), stable=IDENTITY_COOKIE_NAMES, "
              "host=DOLA_SITE.host, log=log)\n", "",
          expect="the rotated ttwid")
@sync
async def test_a_logged_in_check_writes_back_the_rotated_ttwid():
    health, writes = await check(Browser())
    assert health.state == "alive"
    assert [ttwid(w) for w in writes] == ["W-NEW"], "the rotated ttwid was not written back"


@mutation(CK, "(BASE,), stable=IDENTITY_COOKIE_NAMES, host=DOLA_SITE.host, log=log)", "(BASE,), log=log)",
          expect="another session")
@sync
async def test_a_guest_check_or_a_browser_with_another_session_writes_nothing():
    _, writes = await check(Browser(), probe=GUEST)
    assert writes == [], "a guest read was written back"
    health, writes = await check(Browser(sessionid="S-OTHER"))
    assert health.state == "alive" and writes == [], "another session was written back"


def scene(browser):
    s = Scene(replies={1: [ACCEPTED]}, acct=account())
    s.session.ctx = browser
    renewing(s.ctx)
    return s


@mutation(SUB, "            if self.logged_in:", "            if False:",
          expect="the rotated ttwid")
@sync
async def test_a_logged_in_submit_writes_back_the_rotated_ttwid_after_its_verdict():
    s = scene(Browser())
    await s.run()
    assert [ttwid(w) for w in s.ctx.writes] == ["W-NEW"], "the rotated ttwid was not written back"


@mutation(SUB, "            if self.logged_in:", "            if True:",
          expect="a guest run")
@sync
async def test_a_submit_that_never_read_the_account_logged_in_writes_nothing():
    s = Scene(probe=GUEST, acct=account())
    s.session.ctx = Browser()
    renewing(s.ctx)
    await s.run()
    assert s.ctx.writes == [], "a guest run was written back"


@sync
async def test_a_write_that_fails_leaves_the_submit_verdict_as_it_was():
    s = scene(Browser())

    async def down(blob, merge=None):
        raise RuntimeError("database down")
    s.ctx.renew_credential = down
    res = await s.run()
    assert isinstance(res.job, Accepted) and res.job.taken, res.job
