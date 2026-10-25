"""The reCAPTCHA token of a generate: a seam chosen by `flow.captcha_provider`, one source or a fallback order.

Three solver services, one JSON API (`/createTask`, then `/getTaskResult` every `flow.captcha_poll_s` until ready or
`flow.captcha_timeout_s` is spent, all sources of the order together). Each solves reCAPTCHA v3 Enterprise for flow's
site key, the project page and the generate's page action:
- YesCaptcha `RecaptchaV3TaskProxylessM1` (isEnterprise): solved in 10-17 s; flow accepted a token about 1 time in 5
  at a fast rate, and on the first try after a 12-min pause (FINDINGS 3).
- CapSolver `ReCaptchaV3EnterpriseTask` with the account's proxy in the task, so the token is minted from the IP
  that sends the generate; `ReCaptchaV3EnterpriseTaskProxyLess` when `flow.capsolver_proxy_task` is off or the
  account has no proxy.
- Anti-Captcha `RecaptchaV3TaskProxyless` with isEnterprise and `flow.anticaptcha_min_score`: it documents no proxy
  task for v3.

The calls to every solver's API go through the account's proxy (owner, 23/10), direct only for an account without one,
like every other call. The keys are the secret settings `flow.<source>_key` (`constants.CAPTCHA_KEY_SETTINGS`), sealed
in the database and opened by the worker's settings at each reload, read at each solve. Neither a key, a
token nor the proxy login is ever logged or put in an error text. A solver's fault (a bad key, no balance, no token
in time) is a `CaptchaError` with its own code: the job is retried, the flow account is never blamed.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from relay_core.providers import Proxy
from relay_flow import errors
from relay_flow.constants import (
    ANTICAPTCHA_TASK,
    API_TIMEOUT_S,
    CAPSOLVER_TASK,
    CAPSOLVER_TASK_PROXYLESS,
    CAPTCHA_KEY_SETTINGS,
    CAPTCHA_MIN_LEFT_S,
    CAPTCHA_URLS,
    RECAPTCHA_SITE_KEY,
    YESCAPTCHA_TASK,
    captcha_sources,
)
from relay_flow.engine.http import proxy_url

# the solvers' errorCode -> our code (YesCaptcha and Anti-Captcha share their names; CapSolver has its own)
SOLVER_CODES = {
    "ERROR_KEY_DOES_NOT_EXIST": errors.CAPTCHA_KEY_INVALID,
    "ERROR_KEY_DENIED_ACCESS": errors.CAPTCHA_KEY_INVALID,
    "ERROR_ZERO_BALANCE": errors.CAPTCHA_NO_BALANCE,
    "ERROR_SETTLEMENT_FAILED": errors.CAPTCHA_NO_BALANCE,
    "ERROR_TASK_TIMEOUT": errors.CAPTCHA_TIMEOUT,
    "ERROR_RECAPTCHA_TIMEOUT": errors.CAPTCHA_TIMEOUT,
}


class CaptchaError(Exception):
    """No token. The text names the reason only, never a key, a token or a proxy login; `code` is the relay code."""

    def __init__(self, reason: str, code: str = errors.CAPTCHA_FAILED):
        super().__init__(reason)
        self.code = code


class Tokens:
    """`await tokens(action, page_url, proxy)` -> a token from the source(s) `flow.captcha_provider` names, its calls
    through `proxy` (the account's; None: direct), each source's key read from its secret setting at the solve ("" is
    not set)."""

    def __init__(self, settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, clock: Callable[[], float] = time.monotonic):
        self.settings, self.transport, self.sleep, self.clock = settings, transport, sleep, clock

    def __repr__(self) -> str:          # holds no key: the settings view opens them
        return "Tokens()"

    async def __call__(self, action: str, page_url: str, proxy: Proxy | None = None) -> str:
        chosen = self.settings.get("flow.captcha_provider")
        try:
            order = captcha_sources(chosen)
        except ValueError:
            raise CaptchaError("no token source %r is built" % str(chosen)[:40]) from None
        timeout = int(self.settings.get("flow.captcha_timeout_s"))
        end = self.clock() + timeout
        failed: list[tuple[str, CaptchaError]] = []
        for source in order:
            if failed and end - self.clock() < CAPTCHA_MIN_LEFT_S:
                break                       # too little of the token's time left to start another source
            try:
                return await self._solve(source, action, page_url, proxy, end, timeout)
            except CaptchaError as e:
                failed.append((source, e))
        raise CaptchaError("; ".join("%s: %s" % (s, e) for s, e in failed), failed[-1][1].code)

    async def _solve(self, source: str, action: str, page_url: str, proxy: Proxy | None, end: float,
                     timeout: int) -> str:
        key = self.settings.get(CAPTCHA_KEY_SETTINGS[source])
        if not key:
            raise CaptchaError("%s is not set (Settings, flow)" % CAPTCHA_KEY_SETTINGS[source],
                               errors.CAPTCHA_KEY_INVALID)
        task = self._task(source, action, page_url, proxy)
        transport = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(proxy), trust_env=False)
        def left() -> float:        # every wait and every request ends by the token's shared deadline
            return end - self.clock()

        try:
            async with httpx.AsyncClient(base_url=CAPTCHA_URLS[source], transport=transport, timeout=API_TIMEOUT_S,
                                         trust_env=False, follow_redirects=False) as client:
                made = await _post(client, "/createTask", {"clientKey": key, "task": task}, left())
                if made.get("errorId") or not made.get("taskId"):
                    raise _refused("the solver refused the task", made)
                poll = int(self.settings.get("flow.captcha_poll_s"))
                while (wait := left()) > 0:
                    await self.sleep(min(poll, wait))
                    if (remaining := left()) <= 0:
                        break
                    got = await _post(client, "/getTaskResult", {"clientKey": key, "taskId": made["taskId"]},
                                      remaining)
                    if got.get("errorId") or got.get("status") == "failed":
                        raise _refused("the solver failed the task", got)
                    if got.get("status") == "ready":
                        solution = got.get("solution")
                        token = solution.get("gRecaptchaResponse") if isinstance(solution, dict) else None
                        if isinstance(token, str) and token:
                            return token
                        raise CaptchaError("the solver answered ready with no token")
        except httpx.TimeoutException as e:
            raise CaptchaError("the solver did not answer in time (%s)" % type(e).__name__,
                               errors.CAPTCHA_TIMEOUT) from None
        except httpx.HTTPError as e:     # its text may carry the proxy URL: only the class is named
            raise CaptchaError("the solver could not be reached (%s)" % type(e).__name__) from None
        raise CaptchaError("no token in %d s" % timeout, errors.CAPTCHA_TIMEOUT)

    def _task(self, source: str, action: str, page_url: str, proxy: Proxy | None) -> dict:
        base = {"websiteURL": page_url, "websiteKey": RECAPTCHA_SITE_KEY, "pageAction": action}
        if source == "yescaptcha":
            return {"type": YESCAPTCHA_TASK, **base, "isEnterprise": True}
        if source == "anticaptcha":
            return {"type": ANTICAPTCHA_TASK, **base, "isEnterprise": True,
                    "minScore": self.settings.get("flow.anticaptcha_min_score")}
        if proxy is not None and self.settings.get("flow.capsolver_proxy_task"):
            return {"type": CAPSOLVER_TASK, **base, "proxy": capsolver_proxy(proxy)}
        return {"type": CAPSOLVER_TASK_PROXYLESS, **base}


def capsolver_proxy(proxy: Proxy) -> str:
    """CapSolver's proxy string `http:host:port[:user:pass]` (its documented form).
    ponytail: a password holding ':' cannot be told apart in this form; CapSolver would then refuse the proxy task
    (the job retries). Use its URL form if such a proxy ever appears."""
    user, _, secret = (proxy.auth or "").partition(":")
    return ":".join(["http", proxy.host, str(proxy.port)] + ([user, secret] if user else []))


async def _post(client: httpx.AsyncClient, path: str, body: dict, left: float) -> dict:
    """One call, its timeout cut to the token's time left (at least a moment, so a call is never sent with none)."""
    response = await client.post(path, json=body, timeout=max(0.5, min(API_TIMEOUT_S, left)))
    try:
        out = response.json()
    except ValueError:
        out = None
    if response.status_code == 401 and not (isinstance(out, dict) and out.get("errorCode")):
        raise CaptchaError("the solver refused its key (HTTP 401)", errors.CAPTCHA_KEY_INVALID)
    if not isinstance(out, dict):
        raise CaptchaError("the solver answered something that is not a JSON object (HTTP %d)" % response.status_code)
    return out


def _refused(what: str, body: dict) -> CaptchaError:
    code = str(body.get("errorCode") or body.get("status") or "no error code")[:60]
    return CaptchaError("%s (%s)" % (what, code), SOLVER_CODES.get(code, errors.CAPTCHA_FAILED))
