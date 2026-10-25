"""Account check: open the account in a browser, load meta.ai and read whether it is signed in.

Signed in: the user-menu button is visible (it carries the display name). Signed out: a login button or the signed-out
card is visible, or meta moved the tab to a login or checkpoint URL or off meta.ai. Neither within
`CHECK_SIGNAL_TIMEOUT_S` is an unknown read. It spends nothing and sends nothing.

A signed-out read is never a verdict by itself (a checkpoint can pass, a page can be read mid-redirect): it is an
`unknown` read with one spaced strike and a rest that asks for a recheck. Only the strike threshold of the caps, reached
by separate reads, ends the account.

Vercel's browser checkpoint (same URL; the page says "Failed to verify your browser") is neither signed in nor signed
out: the browser is in doubt, not the account. It rests `meta.checkpoint_rest_s` and is checked again, with no strike;
each repeat within a day, seen by a check or by a submit, doubles the rest up to `meta.checkpoint_rest_max_s`
(`CHECKPOINTS`). A checkpoint never ends the account.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from relay_browser.session import SiteProfile, open_session
from relay_browser.ui import inject_cookies
from relay_core.pool import RepeatRest
from relay_core.providers import Cooldown, Health, Suspect
from relay_core.redact import redact_text
from relay_meta.constants import (
    CHECK_SIGNAL_TIMEOUT_S,
    CHECKPOINT_FAILED_TEXT,
    CHECKPOINT_TITLE,
    LOGGED_OUT_URL_MARKS,
    PAGE_LOAD_TIMEOUT_S,
    SEL_LOGGED_OUT,
    SEL_USER_MENU,
    SESSION_COOKIES,
    SIGNED_OUT_GAP_S,
    SIGNED_OUT_REST_S,
    SITE_URL,
)
from relay_meta.engine.site import META_SITE

SIGNAL_POLL_S = 0.5

# {menu: user menu visible, out: a signed-out sign visible, name: the menu's text or label, title: the page title,
#  failed: the checkpoint's failure sentence is on the page}
SIGNALS_JS = """([menu, outs, failedText]) => {
  const shown = e => !!e && e.getClientRects().length > 0;
  const m = document.querySelector(menu);
  const name = shown(m) ? ((m.innerText || '').trim() || (m.getAttribute('aria-label') || '').trim()) : '';
  const text = (document.body && document.body.innerText) || '';
  return {menu: shown(m), out: outs.some(s => [...document.querySelectorAll(s)].some(shown)), name,
          title: document.title || '', failed: text.includes(failedText)};
}"""


def signed_out_effects(now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect("not signed in: meta.ai showed a signed-out sign or URL", min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


CHECKPOINTS = RepeatRest()      # the checkpoints of each account in this worker, for the growing rest


def checkpoint_rest(now: datetime, settings, account_id, why: str) -> Cooldown:
    """A checkpoint rests the account (longer on a repeat) and asks for a recheck at its end; it is never a strike."""
    rest_s = CHECKPOINTS.next_s(account_id, settings.get("meta.checkpoint_rest_s"),
                                settings.get("meta.checkpoint_rest_max_s"))
    return Cooldown("browser checkpoint (%s), to be checked again in %d s" % (why, rest_s),
                    now + timedelta(seconds=rest_s), recheck=True)


def verdict(url: str, host: str, seen: dict) -> str | None:
    """`alive`, `signed_out`, `checkpoint`, or None while nothing is certain. Only a web URL is judged by its address:
    an error page of the browser (a dead proxy) says nothing about the account."""
    parts = urlsplit(url)
    if parts.scheme in ("http", "https"):
        hostname = (parts.hostname or "").lower()
        if not (hostname == host or hostname.endswith("." + host)):
            return "signed_out"
        if any(mark in url for mark in LOGGED_OUT_URL_MARKS):
            return "signed_out"
    if seen.get("failed"):
        return "checkpoint"
    if seen.get("menu") and not seen.get("out"):
        return "alive"
    if seen.get("out") and not seen.get("menu"):
        return "signed_out"
    return None


async def read_signals(page, host: str, wait_s: float, clock: Callable[[], float] = time.monotonic) -> tuple[str, str]:
    """(state, display name), polled until a verdict or `wait_s`; then `checkpoint` when the page still carries the
    checkpoint's title (its challenge did not pass), else `unknown`."""
    deadline = clock() + wait_s
    while True:
        try:
            seen = await page.evaluate(SIGNALS_JS, [SEL_USER_MENU, list(SEL_LOGGED_OUT), CHECKPOINT_FAILED_TEXT])
        except Exception:      # noqa: BLE001 - the page is navigating: read again
            seen = {}
        seen = seen if isinstance(seen, dict) else {}
        state = verdict(page.url or "", host, seen)
        if state is not None:
            return state, str(seen.get("name") or "") if state == "alive" else ""
        if clock() >= deadline:
            return "checkpoint" if seen.get("title") == CHECKPOINT_TITLE else "unknown", ""
        await asyncio.sleep(SIGNAL_POLL_S)


async def check_account(ctx, settings, *, open_session=open_session, site: SiteProfile = META_SITE,
                        url: str = SITE_URL, wait_s: float = CHECK_SIGNAL_TIMEOUT_S,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Health:
    """The state of one account. `open_session`, `site`, `url`, `wait_s` and `now` are seams for tests."""
    async with open_session(site, ctx.account, purpose="check", token=ctx.browser_token,
                            redact=redact_text) as session:
        page = session.page
        # the cookies are for meta.ai whatever page the check loads; read back there
        await inject_cookies(session.ctx, ctx.account.credential, SESSION_COOKIES, SITE_URL)
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_S * 1000)
        state, name = await read_signals(page, site.host, wait_s)
        try:
            shot = await page.screenshot(full_page=False)
        except Exception:      # noqa: BLE001 - a picture is a bonus, never a reason to fail the check
            shot = None
        if state == "alive":
            return Health("alive", display_name=name or None, screenshot=shot)
        if state == "checkpoint":
            return Health("unknown", screenshot=shot,
                          effects=(checkpoint_rest(now(), settings, ctx.account.id, "seen by a check"),))
        if state == "signed_out":
            return Health("unknown", screenshot=shot, effects=signed_out_effects(now()))
        return Health("unknown", screenshot=shot)
