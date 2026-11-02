"""The anti-bot pair: minted in a browser, reused over plain HTTP (FINDINGS 2, 3).

Alibaba's baxia script on chat.qwen.ai signs the page's own `chat/completions` with `bx-ua`/`bx-umidtoken`; a call
without them gets the WAF's `FAIL_SYS_USER_VALIDATE`. Nothing else qwen answers needs them. One mint: Chrome loads
chat.qwen.ai with the account's cookies through the account's proxy (`relay_browser.open_session`), then:
- the baxia way first (spike 01/11, FINDINGS "Baxia mint"): the page's own signer, `window.__baxia__.getFYModule`, is
  asked for the pair in the page's MAIN world (CDP `Runtime.evaluate`: patchright's `page.evaluate` runs in an
  isolated world that sees no page global): `getFYToken()` is `bx-ua`, `getUidToken()` `bx-umidtoken`. Nothing is
  typed and nothing is sent. It was ready 3.5-4 s after the load; it worked on accounts whose Enter sends nothing.
- when the module gives no pair within `qwen.baxia_wait_s`, the typed way: the page is given a text-to-image prompt
  and Enter, and its generate is FAILED inside the browser by CDP `Fetch` at the Request stage, before a byte of it
  leaves (no Playwright route: the kit forbids them). That request's headers are kept.
The browser's cookies are kept too; a pair worked from plain HTTP for at least 30 min and 3 sends.

The session is the account: the page is never let to end it. Its sign-out and log-out calls are failed the same way
(a first mint attempt on 23/10 left the spike account's refresh token revoked, cause not isolated), and the browser's
cookies are written back after every mint that ran, failed or not, so a session the page renewed is never lost.

When: lazily, by the submit that finds no pair younger than `qwen.bx_max_age_s`, and by the account check at half that
age while claims are on; and again after the WAF refused a pair (the submit drops it). One mint per account at a time
(`Sessions.lock`), at most `qwen.mint_browsers` browsers at once per worker process (the core gives an HTTP provider
no browser budget).
The new credential is written back at once (`write_back`). A mint never sends a generate: it costs nothing.
"""

import asyncio
import contextlib
import fnmatch
import logging
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from relay_core.budget import BrowserBudget, Purpose
from relay_core.redact import redact_text
from relay_qwen.constants import BASE_URL, BX_VERSION, MINT_PROMPT, MINT_TIMEOUT_S, REFRESH_COOKIE, WEB_VERSION
from relay_qwen.credential import BX_HEADERS, PAIR, Cred, on_qwen
from relay_qwen.engine.http import Sessions, write_back
from relay_qwen.spec import SPEC

log = logging.getLogger(__name__)

COMPLETIONS = "*/api/v2/chat/completions*"
# Calls that end the session server-side (the web app's sign-out and log-out): a mint never lets the page make them.
SESSION_ENDERS = ("*/api/v2/auths/signout*", "*/api/v2/users/logout/*")
SITE_HOST = "chat.qwen.ai"
SETTLE_MS = 9000          # the page loads its scripts and the baxia signer before the prompt is sent (spike: 9 s)
SEND_WAIT_S = 30          # after Enter the page builds its generate in a second or two
BAXIA_POLL_S = 0.5        # between two asks for the baxia module while it loads
SLOT_POLL_S = 0.5         # between two looks for a free mint browser
# NOTE: the live web app was 0.3.32 on 01/11 while WEB_VERSION is 0.3.22: the baxia mint reads the version the page
# loads from its bundle's path (`.../qwen-chat-fe/<version>/js/main.js`), the constant only when none is found.
ASSET_VERSION = re.compile(r"/qwen-chat-fe/(\d+(?:\.\d+)+)/")


class MintError(Exception):
    """No pair. The text names the reason only, never a header or cookie value. `cred`: the browser's cookies of a
    mint that ran (the old pair kept), to be written back; None when no browser ran. `screenshot`: the page as the
    mint gave up on it (PNG), for the account's check screenshot; None when none was taken."""

    def __init__(self, why: str, cred: Cred | None = None, screenshot: bytes | None = None):
        super().__init__(why)
        self.cred = cred
        self.screenshot = screenshot


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


# Run in the page's main world: the pair from the page's own baxia signer, or null while it is not loaded.
BAXIA_JS = """(() => {
  const fm = (window.__baxia__ || {}).getFYModule;
  if (!fm || !fm.fyObj) return null;
  return {'bx-ua': String(fm.getFYToken() || ''), 'bx-umidtoken': String(fm.getUidToken() || ''),
          assets: Array.from(document.scripts, s => s.src).filter(u => u.includes('/qwen-chat-fe/'))};
})()"""


def page_version(assets) -> str:
    """The web app version the page runs, from its bundle's path; WEB_VERSION when none is found."""
    for url in assets if isinstance(assets, list) else ():
        found = ASSET_VERSION.search(url) if isinstance(url, str) else None
        if found:
            return found.group(1)
    return WEB_VERSION


async def main_world(cdp, js: str):
    """`js` evaluated in the page's main world (where `window.__baxia__` lives), its value; None on any failure."""
    with contextlib.suppress(Exception):
        ev = await cdp.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        return (ev.get("result") or {}).get("value")
    return None


async def baxia_headers(cdp, wait_s: float) -> dict[str, str] | None:
    """The pair from the page's baxia module, asked every BAXIA_POLL_S until it answers; None after `wait_s`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while True:
        got = await main_world(cdp, BAXIA_JS)
        if isinstance(got, dict) and all(isinstance(got.get(h), str) and got[h] for h in PAIR):
            return {"bx-ua": got["bx-ua"], "bx-umidtoken": got["bx-umidtoken"], "bx-v": BX_VERSION,
                    "version": page_version(got.get("assets"))}
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(BAXIA_POLL_S)


async def typed_send(page, got: asyncio.Future) -> dict[str, str]:
    """The typed way: a t2i prompt and Enter; the headers of the generate the guard captured. TimeoutError when the
    page sent none in SEND_WAIT_S."""
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
    return await asyncio.wait_for(got, SEND_WAIT_S)


def paused_action(url: str) -> str:
    """What the mint does with a request the page makes: `capture` (the generate: kept, then failed), `refuse` (a
    call that would end the session) or `continue`."""
    if fnmatch.fnmatchcase(url, COMPLETIONS):
        return "capture"
    if any(fnmatch.fnmatchcase(url, p) for p in SESSION_ENDERS):
        return "refuse"
    return "continue"


async def browser_mint(account, cred: Cred, token, baxia_wait_s: float) -> Cred:
    """One mint in Chrome (see the module text): the baxia way, the typed way when the module gives no pair within
    `baxia_wait_s`. Returns the credential with the browser's cookies and the pair. A failed mint raises `MintError`
    carrying the browser's cookies all the same."""
    from relay_browser.session import SiteProfile, open_session  # late: only a mint starts a browser

    site = SiteProfile(name="qwen", host=SITE_HOST, timezone="UTC", local_storage_token_keys=("token",))
    got: asyncio.Future = asyncio.get_running_loop().create_future()
    raw, agent, failure, shot = None, {}, None, None
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
            await page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=MINT_TIMEOUT_S * 1000 // 2)
            raw = await baxia_headers(cdp, baxia_wait_s)
            if raw is None:
                log.info("qwen mint: the baxia module gave no pair in %d s; the typed way", baxia_wait_s)
                raw = await typed_send(page, got)
            agent = await page.evaluate(AGENT_JS)
        except TimeoutError:
            failure = "the page sent no generate in %d s" % SEND_WAIT_S
        except Exception as e:      # noqa: BLE001 - its text may hold a URL: only the kind goes out
            failure = "the page failed (%s)" % type(e).__name__
        if failure is not None or raw is None:
            with contextlib.suppress(Exception):          # what kept the page from sending, shown in the admin
                shot = await page.screenshot(full_page=False)
        cookies = browser_cookies(await session.ctx.cookies())
    if not any(name == REFRESH_COOKIE for (_d, _p, name) in cookies):
        cookies = {**cred.cookies, **cookies}       # the browser dropped the session cookie: keep the jar's
    kept = Cred(cookies, cred.bx, cred.minted_at)
    if failure is not None or raw is None:
        raise MintError(failure or "no generate", kept, shot)
    try:
        headers = kept_headers(raw, agent if isinstance(agent, dict) else {})
    except MintError as e:
        raise MintError(str(e), kept) from None
    return Cred(cookies, headers, 0.0)


Mint = Callable[[object, Cred, object, float], Awaitable[Cred]]


class Minter:
    """`await minter.fresh(ctx, sessions, max_age_s)`: the account's credential with a pair younger than `max_age_s`,
    minted when there is none. At most `qwen.mint_browsers` mints run at once (read live). The bound is kept here: a
    browser budget gives check tokens one at a time, so each mint takes its token from a budget of its own.
    `settings` (None: the spec's defaults), `mint` and `clock` are seams for tests."""

    def __init__(self, mint: Mint = browser_mint, *, settings=None, clock: Callable[[], float] = time.time):
        self._mint, self.clock = mint, clock
        self._setting = settings.get if settings is not None else (lambda key: SPEC.settings()[key].default)
        self._busy = 0

    async def _browser(self):
        """A browser token, once fewer than `qwen.mint_browsers` mints run (looked at every SLOT_POLL_S: the setting
        is live). Give it back with `_release`."""
        while self._busy >= int(self._setting("qwen.mint_browsers")):
            await asyncio.sleep(SLOT_POLL_S)
        self._busy += 1         # no await between the look and the count: two waiters never take one slot
        return BrowserBudget(1).try_acquire(Purpose.CHECK)

    def _release(self, token) -> None:
        token.release()
        self._busy -= 1

    async def fresh(self, ctx, sessions: Sessions, max_age_s: float) -> Cred:
        account = ctx.account
        cred = sessions.current(account)
        if is_fresh(cred, max_age_s, self.clock()):
            return cred
        async with sessions.lock(account.id):
            cred = sessions.current(account)          # another run of this account may have minted meanwhile
            if is_fresh(cred, max_age_s, self.clock()):
                return cred
            token = await self._browser()
            try:
                minted = await asyncio.wait_for(
                    self._mint(account, cred, token, int(self._setting("qwen.baxia_wait_s"))), MINT_TIMEOUT_S)
            except MintError as e:
                if e.cred is not None and e.cred.cookies != cred.cookies:
                    self._stamp(e.cred, cred)
                    await write_back(ctx, sessions, e.cred)      # the page renewed the session: keep it
                raise MintError(str(e), screenshot=e.screenshot) from None
            except Exception as e:  # noqa: BLE001 - a browser failure of any kind is "no pair"; its text may hold a URL
                raise MintError("the browser mint failed (%s)" % type(e).__name__) from None
            finally:
                self._release(token)
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
