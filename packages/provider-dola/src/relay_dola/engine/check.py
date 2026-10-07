"""Account check and proxy probe (contracts 5.4.1 "check_account", "probe_proxy"; v4 `alive.py`, `proxy_manager.py`).

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

from relay_core.providers import Cooldown, Dead, Health, Proxy, ProxyBad, ProxyVerdict, Suspect
from relay_core.redact import redact_text
from relay_dola import constants
from relay_dola.engine.http import CurlTransport, Transport, proxies_for
from relay_dola.engine.site import DOLA_SITE, REGION_BLOCK_MARK
from relay_dola.engine.submit import Deps, network_failure
from relay_dola.engine.verify import login_state

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
    """The state of one account. `signed_out` only on positive proof (passport says guest AND no conversation): a
    doubt is
    `unknown` (and a strike when dola showed a login dialog), never a dead account."""
    deps = deps or Deps()
    driver = deps.driver
    try:
        async with deps.open_session(DOLA_SITE, ctx.account, purpose="check", token=ctx.browser_token,
                                     redact=redact_text) as session:
            page = session.page
            await driver.inject(session, ctx.account)
            await driver.open_chat(page)
            if REGION_BLOCK_MARK in await driver.page_url(page):
                return _proxy_trouble("dola moved the page to its region page", deps)
            state = login_state(await driver.probe(page))
            shot = await driver.screenshot(page)
            if state.signed_out:
                return Health("signed_out", screenshot=shot, effects=(Dead("signed_out"),))
            if not state.logged_in:
                # A dialog, or no conversation and no readable name: a doubt, not a verdict. A failed read (cells < 0)
                # is not even a strike: the call did not answer.
                doubt = state.cells >= 0 or state.modal_visible
                strike = (Suspect("not logged in: %s" % state.reason),) if doubt else ()
                return Health("unknown", screenshot=shot, effects=strike)
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
