"""Account check and proxy probe ("probe_proxy").

`check_account` opens the account in a browser and reads, from INSIDE the tab (a login read over HTTP from another
machine
once said "5 conversations" for a cookie the browser could not use): the region URL, the login probe, the passport
identity (`user_id_str`: the stable account id; `uid_tt` is a SESSION id and changes per login), a screenshot, and which
composer UI the account has (the acceptance verdict of a send depends on it). It spends no credit and sends nothing.

`probe_proxy` goes over HTTP through the proxy: the exit IP, then dola's chat page, looking for the region page and 5xx.
"""

import asyncio
import logging
from datetime import timedelta

from relay_core.providers import Cooldown, Health, Proxy, ProxyBad, ProxyVerdict
from relay_core.redact import redact_text
from relay_dola import constants
from relay_dola.engine.http import CurlTransport, Transport, proxies_for
from relay_dola.engine.site import DOLA_SITE, REGION_BLOCK_MARK
from relay_dola.engine.submit import Deps, network_failure
from relay_dola.engine.verify import cookies_lost_effects, login_doubt_effects, login_state, lost_identity_cookies

log = logging.getLogger("relay_dola.check")

# What dola's chat page says when it blocks the exit's region.
REGION_BLOCKED_MARKERS = ("region-restricted", "region_restricted", "not available in your region", "/security/region")
IPIFY = "https://api.ipify.org"
CHAT_PAGE = "https://www.dola.com/chat"


def identity_of(passport) -> tuple[str | None, str]:
    """(user id, name) from the passport answer. A guest (`message` not `success`) or a failed call gives no
    identity: the
    `account_info_error` of a guest tab is an error text, not a name."""
    if not isinstance(passport, dict) or passport.get("message") != "success" or not passport.get("user_id"):
        return None, ""
    return str(passport["user_id"]), str(passport.get("name") or "")


async def check_account(ctx, settings, *, deps: Deps | None = None) -> Health:
    """The state of one account. A login doubt (a guest read, a login dialog, no conversation and no name) never kills
    an account by itself: the result is `unknown` with one spaced strike and a rest. A failed read and cookies
    lost or unreadable in the browser are no strike. Never a dead account from one read."""
    deps = deps or Deps()
    driver = deps.driver
    try:
        async with deps.open_session(DOLA_SITE, ctx.account, purpose="check", token=ctx.browser_token,
                                     redact=redact_text) as session:
            page = session.page
            injected = await driver.inject(session, ctx.account)
            await driver.open_chat(page)
            if REGION_BLOCK_MARK in await driver.page_url(page):
                return _proxy_trouble("dola moved the page to its region page", deps)
            state = login_state(await driver.probe(page))
            shot = await driver.screenshot(page)
            if not state.logged_in:
                present = await driver.identity_cookie_names(session)
                if present is None:
                    # The browser could not be asked: a browser fault, like a failed read. No strike.
                    return Health("unknown", screenshot=shot, effects=(_browser_fault("cookies not readable", deps),))
                if lost := lost_identity_cookies(present, injected):
                    # The browser lost the cookies it was given: a browser fault, not a guest verdict and not a strike.
                    log.warning("cookies lost in the browser during a check: %s", ", ".join(lost))
                    return Health("unknown", screenshot=shot, effects=cookies_lost_effects(deps.now()))
                if state.signed_out or state.cells >= 0 or state.modal_visible:
                    # A guest read or a dialog: a doubt, never a verdict. The third spaced one ends in `signed_out`
                    # through the strike threshold. A failed read (cells < 0) is no strike: the call did not answer.
                    return Health("unknown", screenshot=shot, effects=login_doubt_effects(deps.now(), state.reason))
                return Health("unknown", screenshot=shot, effects=())
            identity, name = identity_of(await driver.passport(page))
            patch = {}
            if (kind := await driver.composer_kind(page)) is not None:
                patch["composer"] = kind
            return Health("alive", identity=identity, display_name=name or state.account_name or None,
                          screenshot=shot, state_patch=patch)
    except Exception as exc:      # noqa: BLE001
        if network_failure(str(exc)):
            return _proxy_trouble(str(exc)[:100], deps)
        raise


def _browser_fault(why: str, deps: Deps) -> Cooldown:
    """A short rest for a browser fault that is not the account's: no strike."""
    return Cooldown(why, deps.now() + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S))


def _proxy_trouble(why: str, deps: Deps) -> Health:
    """The browser could not reach dola, or dola refused this exit: the proxy's fault, never the account's. It rests."""
    until = deps.now() + timedelta(seconds=constants.THROTTLE_COOLDOWN_S)
    return Health("unknown", effects=(ProxyBad(why), Cooldown("proxy trouble during a check", until)))


# ---- the proxy probe
# ----------------------------------------------------------------------------------------------------
def _scrub(text: str, proxy: Proxy) -> str:
    """An error text without the proxy's login (a transport may echo the URL it was given)."""
    for secret in filter(None, (proxy.auth, (proxy.auth or "").split(":", 1)[-1])):
        text = text.replace(secret, "***")
    return text


def _probe_blocking(transport: Transport, proxy: Proxy) -> tuple[int, str, str]:
    proxies = proxies_for(proxy)
    exit_ip = ""
    try:
        exit_ip = transport.get(IPIFY, headers=None, timeout=20, proxies=proxies).content.decode(
            "utf-8", "replace").strip()[:45]
    except Exception:      # noqa: BLE001 - the exit IP is a bonus; dola's answer decides
        pass
    resp = transport.get(CHAT_PAGE, headers=None, timeout=25, proxies=proxies)
    return resp.status_code, resp.content[:2000].decode("utf-8", "replace"), exit_ip


async def probe_proxy(proxy: Proxy, *, transport: Transport | None = None) -> ProxyVerdict:
    """Can this proxy reach dola, and does dola accept its region? One GET through the proxy, no browser, no credit. Not
    a guarantee (an exit can change later): a send still has to survive a proxy that is suddenly blocked."""
    transport = transport or CurlTransport()
    try:
        status, body, exit_ip = await asyncio.to_thread(_probe_blocking, transport, proxy)
    except Exception as exc:      # noqa: BLE001
        return ProxyVerdict(False, "proxy could not connect: %s" % _scrub(str(exc), proxy)[:80])
    low = body.lower()
    if any(mark in low for mark in REGION_BLOCKED_MARKERS):
        return ProxyVerdict(False, "dola blocked by region (exit %s)" % (exit_ip or "unknown"))
    if status >= 500:
        return ProxyVerdict(False, "dola returned %d via this proxy (exit %s)" % (status, exit_ip or "unknown"))
    return ProxyVerdict(True, "usable (exit %s)" % (exit_ip or "unknown"))
