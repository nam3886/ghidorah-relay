"""The real `PageDriver.identity_cookie_names` on a real browser context: after the identity cookies are in, it names
them; after they are cleared, none. Every other dola test answers this call from a scripted driver. Browser tier."""

import asyncio
import functools
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from relay_browser.session import SiteProfile, open_session
from relay_core.budget import BrowserBudget, Purpose
from relay_core.redact import redact_text
from relay_core.testing.mutation import mutation
from relay_dola.engine.submit import PageDriver

SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
READ = "            return {c[\"name\"] for c in cookies} & set(IDENTITY_COOKIE_NAMES)"


@dataclass(frozen=True)
class Account:
    id: str = "acct-1"
    credential_gen: int = 1
    proxy: object = None


def sync(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


@pytest.mark.browser
@mutation(SUB, READ, "            return set()", expect="the real cookie read returned nothing for injected cookies")
@mutation(SUB, "session.ctx.cookies(BASE), COOKIES_TIMEOUT_S)", "session.ctx.cookies('https://example.invalid'), "
          "COOKIES_TIMEOUT_S)", expect="the real cookie read returned nothing for injected cookies")
@sync
async def test_the_real_cookie_read_names_the_injected_identity_cookies_and_none_once_they_are_cleared(tmp_path):
    token = BrowserBudget(1).try_acquire(Purpose.SUBMIT)
    site = SiteProfile(name="dola-test", host="dola.com", profile_root=str(tmp_path / "profiles"), headless=True)
    try:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact_text) as s:
            driver, session = PageDriver(), SimpleNamespace(ctx=s.ctx)
            jar = [{"name": n, "value": "v-" + n, "domain": ".dola.com", "path": "/", "secure": True}
                   for n in ("sessionid", "uid_tt", "ttwid")]
            await s.ctx.add_cookies(jar)
            got = await driver.identity_cookie_names(session)
            assert got == {"sessionid", "uid_tt"}, \
                "the real cookie read returned nothing for injected cookies: %r" % got
            await s.ctx.clear_cookies()
            assert await driver.identity_cookie_names(session) == set(), "cleared cookies were still named"
    finally:
        token.release()


INJECT_RETURN = ("        return await inject_cookies(session.ctx, jar_from_blob(account.credential), "
                 "IDENTITY_COOKIE_NAMES, BASE)")
INJECT_DROPPED = ("        await inject_cookies(session.ctx, jar_from_blob(account.credential), "
                  "IDENTITY_COOKIE_NAMES, BASE)")


@pytest.mark.browser
@mutation(SUB, INJECT_RETURN, INJECT_DROPPED,
          expect="the real inject did not return the verified identity names")
@sync
async def test_the_real_inject_returns_the_identity_names_it_verified_in_the_browser(tmp_path):
    import json
    token = BrowserBudget(1).try_acquire(Purpose.SUBMIT)
    site = SiteProfile(name="dola-test", host="dola.com", profile_root=str(tmp_path / "profiles"), headless=True)

    def jar(*names):
        return json.dumps({"cookies": [{"name": n, "value": "v-" + n, "domain": ".dola.com", "path": "/",
                                        "secure": True, "httpOnly": True} for n in names]}).encode()

    try:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact_text) as s:
            driver, session = PageDriver(), SimpleNamespace(ctx=s.ctx)
            both = await driver.inject(session, SimpleNamespace(credential=jar("sessionid", "uid_tt", "ttwid")))
            assert both == ["sessionid", "uid_tt"], \
                "the real inject did not return the verified identity names: %r" % both
            only = await driver.inject(session, SimpleNamespace(credential=jar("sessionid", "ttwid")))
            assert only == ["sessionid"], \
                "a jar with only a session cookie must return only that name: %r" % only
    finally:
        token.release()
