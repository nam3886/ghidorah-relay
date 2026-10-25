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
`provider_state.mode`. Every order of the account goes to that index with that mode. A mode list that cannot be read
clears the kept mode (no send without it: `gemini.account_unresolved`) and is an unknown read with a rest and a recheck.

An alive check also reads the account's usage (`jSf9Qc`, FINDINGS 5): the plan it names becomes the account's tier, and
a usage window with nothing left rests both pools until that window resets (the windows are shared: a video took 453
of the 5-hour window's 2400 units). A read that fails changes neither: the stored tier and pools stay.
"""

import time
from collections.abc import Callable
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
POOLS = ("image", "video")


def account_read(text: str) -> tuple[Balance | Tier, ...]:
    """What a jSf9Qc answer says of the account: its tier, and a zero balance in every pool until the latest reset of
    an exhausted window. Nothing when the answer cannot be read."""
    usage = usage_of(text)
    if usage is None:
        return ()
    plan, windows = usage
    out: list[Balance | Tier] = []
    if plan in PLAN_TIERS:
        out.append(Tier(PLAN_TIERS[plan]))
    spent = [reset for left, reset in windows if left <= 0]
    if spent:
        resets = [r for r in spent if r is not None]
        refill = epoch_time(max(resets)) if len(resets) == len(spent) else None
        out.extend(Balance(pool, 0, refill) for pool in POOLS)
    return tuple(out)


async def read_account(site: Site) -> tuple[Balance | Tier, ...]:
    """The account's plan and usage; nothing when gemini does not answer them (the stored values stay)."""
    try:
        status, body = await site.rpc(USAGE, [], path=USAGE_PATH)
    except httpx.HTTPError:
        return ()
    return account_read(body) if status == 200 else ()


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
            mode = await site.mode()
            patch = {"authuser": site.authuser, "mode": mode}
            if mode is None:        # no send without the account's own mode: the stale one is cleared, rest, recheck
                rest = Cooldown("gemini's mode list could not be read", now() + timedelta(seconds=NETWORK_REST_S),
                                recheck=True)
                return Health("unknown", identity=page.email, display_name=page.email, state_patch=patch,
                              effects=(rest,))
            return Health("alive", identity=page.email, display_name=page.email, state_patch=patch,
                          effects=await read_account(site))
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("gemini could not be reached (%s)" % type(e).__name__) from None
