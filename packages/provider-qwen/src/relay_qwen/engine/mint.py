"""The anti-bot pair: minted in a browser, reused over plain HTTP (FINDINGS 2, 3).

Alibaba's baxia script on chat.qwen.ai signs the page's own `chat/completions` with `bx-ua`/`bx-umidtoken`; a call
without them gets the WAF's `FAIL_SYS_USER_VALIDATE`. Nothing else qwen answers needs them. One mint: Chrome loads
chat.qwen.ai with the account's cookies through the account's proxy (`relay_browser.open_session`), the page is
given a text-to-image prompt and Enter, and its generate is FAILED inside the browser by CDP `Fetch` at the Request
stage, before a byte of it leaves (no Playwright route: the kit forbids them). The request's headers and the
browser's cookies are what is kept; the pair worked from plain HTTP for at least 30 min and 3 sends.

The session is the account: the page is never let to end it. Its sign-out and log-out calls are failed the same way
(a first mint attempt on 23/10 left the spike account's refresh token revoked, cause not isolated), and the browser's
cookies are written back after every mint that ran, failed or not, so a session the page renewed is never lost.

When: lazily, by the submit that finds no pair younger than `qwen.bx_max_age_s`, and by the account check at half that
age while claims are on; and again after the WAF refused a pair (the submit drops it). One mint per account at a time
(`Sessions.lock`), one browser per worker process (its own budget of one token: the core gives an HTTP provider none).
The new credential is written back at once (`write_back`). A mint never sends a generate: it costs nothing.
"""

import asyncio
import contextlib
import fnmatch
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from relay_core.budget import BrowserBudget, Purpose
from relay_core.redact import redact_text
from relay_qwen.constants import BASE_URL, MINT_PROMPT, MINT_TIMEOUT_S, REFRESH_COOKIE
from relay_qwen.credential import BX_HEADERS, PAIR, Cred, on_qwen
from relay_qwen.engine.http import Sessions, write_back

log = logging.getLogger(__name__)

COMPLETIONS = "*/api/v2/chat/completions*"
# Calls that end the session server-side (the web app's sign-out and log-out): a mint never lets the page make them.
SESSION_ENDERS = ("*/api/v2/auths/signout*", "*/api/v2/users/logout/*")
SITE_HOST = "chat.qwen.ai"
SETTLE_MS = 9000          # the page loads its scripts and the baxia signer before the prompt is sent (spike: 9 s)
SEND_WAIT_S = 30          # after Enter the page builds its generate in a second or two


class MintError(Exception):
    """No pair. The text names the reason only, never a header or cookie value. `cred`: the browser's cookies of a
    mint that ran (the old pair kept), to be written back; None when no browser ran."""

    def __init__(self, why: str, cred: Cred | None = None):
        super().__init__(why)
        self.cred = cred


def is_fresh(cred: Cred, max_age_s: float, now: float) -> bool:
    return bool(cred.bx) and now - cred.minted_at < max_age_s


def kept_headers(raw: dict[str, str], agent: dict[str, str]) -> dict[str, str]:
    """The headers of the page's aborted generate that a plain send repeats; the browser's own user agent and client
    hints (`agent`, read from the page) fill what the paused request did not carry. MintError without the pair."""
    low = {k.lower(): v for k, v in raw.items()}
    out = {h: low[h] for h in BX_HEADERS if isinstance(low.get(h), str) and low[h]}
    for h in ("user-agent", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"):
        if h not in out and agent.get(h):
            out[h] = agent[h]
    if not all(out.get(h) for h in PAIR):
        raise MintError("the page's generate carried no bx pair")
    return out


def browser_cookies(found: list[dict]) -> dict[tuple[str, str, str], str]:
    """The browser's qwen.ai cookies after the page ran, keyed as the credential keys them."""
    out = {}
    for c in found:
        domain = str(c.get("domain") or "")
        if on_qwen(domain) and c.get("name") and isinstance(c.get("value"), str):
            out[(domain, str(c.get("path") or "/"), str(c["name"]))] = c["value"]
    return out


def chrome_cookies(cred: Cred) -> list[dict]:
    """The credential's cookies as the browser takes them (`add_cookies`); the session cookie stays out of reach of
    the page's scripts, as qwen sets it."""
    return [{"name": name, "value": value, "domain": domain, "path": path, "secure": True, "sameSite": "Lax",
             "httpOnly": name == REFRESH_COOKIE}
            for (domain, path, name), value in cred.cookies.items()]


AGENT_JS = """() => {
  const d = navigator.userAgentData;
  const q = s => '"' + s + '"';
  return {'user-agent': navigator.userAgent,
          'sec-ch-ua': d ? d.brands.map(b => q(b.brand) + ';v=' + q(b.version)).join(', ') : '',
          'sec-ch-ua-mobile': d ? (d.mobile ? '?1' : '?0') : '',
          'sec-ch-ua-platform': d ? q(d.platform) : ''};
}"""


def paused_action(url: str) -> str:
    """What the mint does with a request the page makes: `capture` (the generate: kept, then failed), `refuse` (a
    call that would end the session) or `continue`."""
    if fnmatch.fnmatchcase(url, COMPLETIONS):
        return "capture"
    if any(fnmatch.fnmatchcase(url, p) for p in SESSION_ENDERS):
        return "refuse"
    return "continue"


async def browser_mint(account, cred: Cred, token) -> Cred:
    """One mint in Chrome (see the module text). Returns the credential with the browser's cookies and the pair. A
    failed mint raises `MintError` carrying the browser's cookies all the same."""
    from relay_browser.session import SiteProfile, open_session  # late: only a mint starts a browser

    site = SiteProfile(name="qwen", host=SITE_HOST, timezone="UTC", local_storage_token_keys=("token",))
    got: asyncio.Future = asyncio.get_running_loop().create_future()
    raw, agent, failure = None, {}, None
    async with open_session(site, account, purpose=Purpose.CHECK, token=token, redact=redact_text) as session:
        page = session.page
        await session.ctx.add_cookies(chrome_cookies(cred))
        cdp = await session.ctx.new_cdp_session(page)

        async def paused(ev: dict) -> None:
            rid = ev["requestId"]
            request = ev.get("request") or {}
            action = paused_action(request.get("url", ""))
            try:
                if action == "capture" and not got.done():
                    got.set_result(dict(request.get("headers") or {}))
                if action == "refuse":
                    log.warning("qwen mint: the page tried to end the session; refused")
                if action == "continue":
                    await cdp.send("Fetch.continueRequest", {"requestId": rid})
                else:
                    await cdp.send("Fetch.failRequest", {"requestId": rid, "errorReason": "Aborted"})
            except Exception as exc:      # noqa: BLE001 - the page's request must never hang on us
                log.warning("qwen mint: a paused request could not be handled (%s)", type(exc).__name__)

        tasks: set[asyncio.Task] = set()

        def on_paused(ev: dict) -> None:
            task = asyncio.ensure_future(paused(ev))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        cdp.on("Fetch.requestPaused", on_paused)
        # The generate and any sign-out are failed at the Request stage: installed before the page loads.
        await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": p, "requestStage": "Request"}
                                                     for p in (COMPLETIONS, *SESSION_ENDERS)]})
        try:
            await page.goto(BASE_URL + "/?" + urlencode({"inputFeature": "t2i", "text": MINT_PROMPT}),
                            wait_until="domcontentloaded", timeout=MINT_TIMEOUT_S * 1000 // 2)
            await page.wait_for_timeout(SETTLE_MS)
            with contextlib.suppress(Exception):          # the credits intro some accounts are shown once
                await page.get_by_text("Got it", exact=True).first.click(timeout=3000)
            box = page.locator("textarea").first
            if (await box.input_value()) != MINT_PROMPT:
                await box.fill(MINT_PROMPT)
            await box.focus()
            await page.keyboard.press("Enter")
            raw = await asyncio.wait_for(got, SEND_WAIT_S)
            agent = await page.evaluate(AGENT_JS)
        except TimeoutError:
            failure = "the page sent no generate in %d s" % SEND_WAIT_S
        except Exception as e:      # noqa: BLE001 - its text may hold a URL: only the kind goes out
            failure = "the page failed (%s)" % type(e).__name__
        cookies = browser_cookies(await session.ctx.cookies())
    if not any(name == REFRESH_COOKIE for (_d, _p, name) in cookies):
        cookies = {**cred.cookies, **cookies}       # the browser dropped the session cookie: keep the jar's
    kept = Cred(cookies, cred.bx, cred.minted_at)
    if failure is not None or raw is None:
        raise MintError(failure or "no generate", kept)
    try:
        headers = kept_headers(raw, agent if isinstance(agent, dict) else {})
    except MintError as e:
        raise MintError(str(e), kept) from None
    return Cred(cookies, headers, 0.0)


Mint = Callable[[object, Cred, object], Awaitable[Cred]]


class Minter:
    """`await minter.fresh(ctx, sessions, max_age_s)`: the account's credential with a pair younger than `max_age_s`,
    minted when there is none. `mint` and `clock` are seams for tests."""

    def __init__(self, mint: Mint = browser_mint, *, clock: Callable[[], float] = time.time):
        self._mint, self.clock = mint, clock
        self._budget = BrowserBudget(1)

    async def fresh(self, ctx, sessions: Sessions, max_age_s: float) -> Cred:
        account = ctx.account
        cred = sessions.current(account)
        if is_fresh(cred, max_age_s, self.clock()):
            return cred
        async with sessions.lock(account.id):
            cred = sessions.current(account)          # another run of this account may have minted meanwhile
            if is_fresh(cred, max_age_s, self.clock()):
                return cred
            token = await self._budget.acquire(Purpose.CHECK)
            try:
                minted = await asyncio.wait_for(self._mint(account, cred, token), MINT_TIMEOUT_S)
            except MintError as e:
                if e.cred is not None and e.cred.cookies != cred.cookies:
                    self._stamp(e.cred, cred)
                    await write_back(ctx, sessions, e.cred)      # the page renewed the session: keep it
                raise MintError(str(e)) from None
            except Exception as e:  # noqa: BLE001 - a browser failure of any kind is "no pair"; its text may hold a URL
                raise MintError("the browser mint failed (%s)" % type(e).__name__) from None
            finally:
                token.release()
            minted.minted_at = self.clock()
            self._stamp(minted, cred)
            await write_back(ctx, sessions, minted)
            return sessions.current(account)

    def _stamp(self, after: Cred, before: Cred) -> None:
        """A session cookie the browser changed is a refresh made now; an unchanged one keeps its stamp."""
        after.refreshed_at = self.clock() if after.session() != before.session() else before.refreshed_at


async def drop_pair(ctx, sessions: Sessions, minted_at: float) -> None:
    """The WAF refused the pair minted at `minted_at`: forget it (the next send mints anew). A newer pair is kept."""
    async with sessions.lock(ctx.account.id):
        cred = sessions.current(ctx.account)
        if cred.bx and cred.minted_at == minted_at:
            cred.bx = None
            await write_back(ctx, sessions, cred)
