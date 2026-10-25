"""Account check: one page load of gemini with the account's session, over its proxy. It spends nothing.

The run first rotates the session when it is due (`Site`), which is how an idle account stays signed in: the check
runs every 5 min. Signed in ⇔ the page carries `SNlM0e` (FINDINGS 1); the email on the page is the account's identity.
A page without it is a doubt, never a verdict by itself: one spaced strike and a rest that asks for a recheck, and only
the strike threshold, reached by separate reads, ends the account. Any other answer (not 200) is an unknown read with
no strike; a network failure raises, so the check is recorded as failed (no strike, no word of the proxy's login).

A session can be signed in to several Google accounts (`/u/0`, `/u/1`, ...; FINDINGS 6). When the credential names an
email and the page at the account's index is another account, the check looks through the indexes for it and keeps
the one found in `provider_state.authuser`; none found is an unknown read and a rest with a recheck (no strike: the
session is alive, it holds no such account). The account's default mode (the modes differ by plan) is kept in
`provider_state.mode`, its "Pro" mode with that mode's kind in `provider_state.pro_mode` (None on a plan without one;
the pro-tier image model is sent with it). Every order of the account goes to that index with its mode. A mode list
that cannot be read clears the kept mode (no send without it: `gemini.account_unresolved`) and is an unknown read with
a rest and a recheck.

An alive check also reads the account's usage (`jSf9Qc`, FINDINGS 5): the plan it names becomes the account's tier, and
a usage window with nothing left rests both pools until that window resets (the windows are shared: a video took 453
of the 5-hour window's 2400 units). A read that fails changes neither: the stored tier and pools stay.
"""

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import Balance, Cooldown, Health, Suspect, Tier, epoch_time
from relay_gemini.constants import NETWORK_REST_S, SIGNED_OUT_GAP_S, SIGNED_OUT_REST_S
from relay_gemini.engine.http import Sessions, Site
from relay_gemini.engine.reply import usage_of

USAGE = "jSf9Qc"
USAGE_PATH = "/usage"
# jSf9Qc payload[0]: 1 on free accounts, 2 on the AI Pro one (measured 24/10 and 25/10). Ultra's code is not measured:
# any other value is not read as a tier, the stored one stays.
PLAN_TIERS = {1: "free", 2: "pro"}
POOLS = ("image", "image_pro", "video", "music")
# jSf9Qc window code -> the `gemini.pools` window its reset ends (FINDINGS 5: 1 = the 5-hour block, 2 = the week)
WINDOWS = {1: "5h", 2: "week"}
# The mode menu's "Pro" (AI Pro: `e6fa609c3fa255c0`, kind 3). With the image tool it draws with "Nano Banana 2.1"
# (25/10; the default Flash-Lite draws "Nano Banana 2 Lite"), so a `gemini-nano-banana-2.1` job is sent with it. Kept
# with its kind in `provider_state.pro_mode` as `[id, kind]`; None on a plan without it.
PRO_MODE_NAME = "Pro"
SHORT_WINDOW = 1           # jSf9Qc's code of the 5-hour window (2 is the week)


def pool_windows(settings, pool: str) -> tuple[str, ...]:
    """The names of the pool's own windows (`gemini.pools`): the only usage windows that can empty it."""
    return tuple(w["name"] for w in settings.get("gemini.pools").get(pool, {}).get("windows", []))


def account_read(text: str, settings) -> tuple[Balance | Tier, ...]:
    """What a jSf9Qc answer says of the account: its tier, and for each pool a zero balance when a window of the
    pool's own is used up (`spent_balance`: a used-up 5-hour block leaves a pool that counts only a day alone).
    Nothing when the answer cannot be read."""
    usage = usage_of(text)
    if usage is None:
        return ()
    out: list[Balance | Tier] = [Tier(PLAN_TIERS[usage[0]])] if usage[0] in PLAN_TIERS else []
    out.extend(b for pool in POOLS if (b := spent_balance(text, pool, pool_windows(settings, pool))) is not None)
    return tuple(out)


async def read_account(site: Site, settings) -> tuple[Balance | Tier, ...]:
    """The account's plan and usage; nothing when gemini does not answer them (the stored values stay)."""
    try:
        status, body = await site.rpc(USAGE, [], path=USAGE_PATH)
    except httpx.HTTPError:
        return ()
    return account_read(body, settings) if status == 200 else ()


def spent_balance(text: str, pool: str, windows: Iterable[str]) -> Balance | None:
    """A zero balance for `pool` when a usage window it counts is used up: `windows` are the names of the pool's own
    `gemini.pools` windows, and only a jSf9Qc window of one of those names (`WINDOWS`) counts. Until the latest reset
    among them, naming that window (with no window named when a reset cannot be read). None when no window of the
    pool is used up or the answer cannot be read."""
    usage = usage_of(text)
    names = set(windows)
    spent = [(reset, WINDOWS[code]) for left, reset, code in (usage[1] if usage else ())
             if left <= 0 and WINDOWS.get(code) in names]
    if not spent:
        return None
    known = [s for s in spent if s[0] is not None and epoch_time(s[0]) is not None]
    if len(known) < len(spent):
        return Balance(pool, 0)
    reset, name = max(known)
    return Balance(pool, 0, epoch_time(reset), name)


async def read_spent(site: Site, pool: str, windows: Iterable[str]) -> Balance | None:
    """`spent_balance` of the account's usage now (a read: it spends nothing); None when gemini does not answer."""
    try:
        status, body = await site.rpc(USAGE, [], path=USAGE_PATH)
    except httpx.HTTPError:
        return None
    return spent_balance(body, pool, windows) if status == 200 else None


def short_window(text: str) -> tuple[int, datetime | None] | None:
    """The 5-hour usage window of a jSf9Qc answer: its units left and its reset; None when it cannot be read."""
    usage = usage_of(text)
    found = [(left, epoch_time(reset)) for left, reset, w in (usage[1] if usage else ()) if w == SHORT_WINDOW]
    return found[0] if found else None


async def read_short_window(site: Site) -> tuple[int, datetime | None] | None:
    """The account's 5-hour usage window now (a read: it spends nothing); None when gemini does not answer it."""
    try:
        status, body = await site.rpc(USAGE, [], path=USAGE_PATH)
    except httpx.HTTPError:
        return None
    return short_window(body) if status == 200 else None


def doubt_effects(why: str, now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect(why, min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


def signed_out_why(site: Site) -> str:
    rotated = " (RotateCookies answered %d)" % site.rotation if site.rotation not in (None, 200) else ""
    return "not signed in: gemini's page carries no session token%s" % rotated


async def check_account(ctx, settings, sessions: Sessions, *, transport: httpx.AsyncBaseTransport | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC),
                        clock: Callable[[], float] = time.time) -> Health:
    """The state of one account. `transport`, `now` and `clock` are seams for tests."""
    try:
        async with Site(ctx, sessions, settings, transport=transport, clock=clock) as site:
            page = await site.load()
            if page.status != 200:
                return Health("unknown")
            if not page.signed_in:
                return Health("unknown", effects=doubt_effects(signed_out_why(site), now()))
            if site.email and (page.email or "").lower() != site.email:
                if await site.find_account(site.email) is None:
                    why = "the session holds no signed-in Google account with the credential's email"
                    return Health("unknown", effects=(
                        Cooldown(why, now() + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True),))
                page = site.page
            menu = await site.modes()
            mode = menu[0][0] if menu else None
            pro = next(([m, kind] for m, name, kind in menu if name == PRO_MODE_NAME and kind is not None), None)
            patch = {"authuser": site.authuser, "mode": mode, "pro_mode": pro}
            if mode is None:        # no send without the account's own mode: the stale one is cleared, rest, recheck
                rest = Cooldown("gemini's mode list could not be read", now() + timedelta(seconds=NETWORK_REST_S),
                                recheck=True)
                return Health("unknown", identity=page.email, display_name=page.email, state_patch=patch,
                              effects=(rest,))
            return Health("alive", identity=page.email, display_name=page.email, state_patch=patch,
                          effects=await read_account(site, settings))
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("gemini could not be reached (%s)" % type(e).__name__) from None
