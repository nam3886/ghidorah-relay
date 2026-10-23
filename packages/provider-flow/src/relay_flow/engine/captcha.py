"""The reCAPTCHA token of a generate: a seam chosen by `flow.captcha_provider`. Only YesCaptcha is built.

YesCaptcha (`RecaptchaV3TaskProxylessM1`, enterprise, the generate's page action) solved in 10-17 s; flow accepted a
token about 1 time in 5 at a fast rate, and on the first try after a 12-min pause (FINDINGS 3). The task is
"proxyless": the solver mints the token from its own network. The calls to the solver's API go through the account's
proxy all the same (owner, 23/10), direct only for an account without one, like every other call. The key comes
from the worker's environment (`FLOW_YESCAPTCHA_KEY`); neither the key nor a token is ever logged or put in an error
text.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from relay_core.providers import Proxy
from relay_flow.constants import (
    API_TIMEOUT_S,
    CAPTCHA_POLL_S,
    CAPTCHA_TIMEOUT_S,
    RECAPTCHA_SITE_KEY,
    YESCAPTCHA_KEY_VAR,
    YESCAPTCHA_TASK,
    YESCAPTCHA_URL,
)
from relay_flow.engine.http import proxy_url


class CaptchaError(Exception):
    """No token. The text names the reason only, never the key or a token."""


class Tokens:
    """`await tokens(action, page_url, proxy)` -> a token from the source `flow.captcha_provider` names, its calls
    through `proxy` (the account's; None: direct)."""

    def __init__(self, settings, yescaptcha_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, clock: Callable[[], float] = time.monotonic):
        self.settings, self._key, self.transport, self.sleep, self.clock = (
            settings, yescaptcha_key, transport, sleep, clock)

    def __repr__(self) -> str:          # the key never shows in a traceback's locals or a log line
        return "Tokens(%s)" % ("key set" if self._key else "no key")

    async def __call__(self, action: str, page_url: str, proxy: Proxy | None = None) -> str:
        source = self.settings.get("flow.captcha_provider")
        if source != "yescaptcha":
            raise CaptchaError("no token source %r is built" % str(source)[:40])
        if not self._key:
            raise CaptchaError("%s is not set on the flow worker" % YESCAPTCHA_KEY_VAR)
        try:
            return await self._yescaptcha(action, page_url, proxy)
        except httpx.HTTPError as e:
            raise CaptchaError("the solver could not be reached (%s)" % type(e).__name__) from None
        except ValueError:
            raise CaptchaError("the solver answered something that is not JSON") from None

    async def _yescaptcha(self, action: str, page_url: str, proxy: Proxy | None) -> str:
        task = {"type": YESCAPTCHA_TASK, "websiteURL": page_url, "websiteKey": RECAPTCHA_SITE_KEY,
                "pageAction": action, "isEnterprise": True}
        transport = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(proxy), trust_env=False)
        async with httpx.AsyncClient(base_url=YESCAPTCHA_URL, transport=transport, timeout=API_TIMEOUT_S,
                                     trust_env=False, follow_redirects=False) as client:
            made = (await client.post("/createTask", json={"clientKey": self._key, "task": task})).json()
            if not isinstance(made, dict) or made.get("errorId") or not made.get("taskId"):
                raise CaptchaError("the solver refused the task (%s)" % _code(made))
            end = self.clock() + CAPTCHA_TIMEOUT_S
            while self.clock() < end:
                await self.sleep(CAPTCHA_POLL_S)
                got = (await client.post("/getTaskResult", json={"clientKey": self._key,
                                                                 "taskId": made["taskId"]})).json()
                if not isinstance(got, dict) or got.get("errorId"):
                    raise CaptchaError("the solver failed the task (%s)" % _code(got))
                if got.get("status") == "ready":
                    token = (got.get("solution") or {}).get("gRecaptchaResponse")
                    if isinstance(token, str) and token:
                        return token
                    raise CaptchaError("the solver answered ready with no token")
        raise CaptchaError("no token in %d s" % CAPTCHA_TIMEOUT_S)


def _code(body) -> str:
    return str(body.get("errorCode") if isinstance(body, dict) else "not an object")[:60]
