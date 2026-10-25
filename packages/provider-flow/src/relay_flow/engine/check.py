"""Account check: one page load of flow with the account's session, over its proxy. It spends nothing.

The run first rotates the session when it is due (`Site`), which is how an idle account stays signed in: the check
runs every 5 min. Signed in ⇔ the page carries `SNlM0e` (FINDINGS 1); the email on the page is the account's identity.
A page without it is a doubt, never a verdict by itself: one spaced strike and a rest that asks for a recheck, and only
the strike threshold, reached by separate reads, ends the account. Any other answer (not 200) is an unknown read with
no strike; a network failure raises, so the check is recorded as failed (no strike, no word of the proxy's login).

A signed-in account without a project gets one (`jHPbke`, free): every job of the account is made in it, and its
listing is how a lost reply is reconciled. Its id is kept in the account's `provider_state.project`.

Every alive check also reads the account's Flow credits (`nzlxg`, free; FINDINGS 2): the balance and its refill become
the video pool's reported balance, and the plan the answer names becomes the account's tier. A read that fails changes
neither: the account stays alive with what was stored.
"""

import calendar
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx

from relay_core.providers import Balance, Cooldown, Health, Suspect, Tier
from relay_flow.constants import SIGNED_OUT_GAP_S, SIGNED_OUT_REST_S
from relay_flow.engine.http import CLIENT_TOOL, Sessions, Site

PROJECT_KEY = "project"
CREATE_PROJECT = "jHPbke"
PROJECT_TITLE = "relay"
CREDITS = "nzlxg"
# `nzlxg` payload[1], flow's PaygateTier (its JS): 1 PAYGATE_TIER_ONE = AI Pro, 2 PAYGATE_TIER_TWO = AI Ultra,
# 3 PAYGATE_TIER_NOT_PAID = free (measured 25/10: 1 on the AI Pro account, 3 on two free ones). Any other value is not
# read as a tier: the stored one stays.
PAYGATE_TIERS = {1: "pro", 2: "ultra", 3: "free"}


def doubt_effects(why: str, now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect(why, min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


def signed_out_why(site: Site) -> str:
    rotated = " (RotateCookies answered %d)" % site.rotation if site.rotation not in (None, 200) else ""
    return "not signed in: flow's page carries no session token%s" % rotated


def credits_of(payload) -> int | None:
    """The balance of a `nzlxg` answer `[balance, tier, _, _, null, balance, [refill s, ns]]` (FINDINGS 2)."""
    if isinstance(payload, list) and payload and type(payload[0]) is int and payload[0] >= 0:
        return payload[0]
    return None


def month_before(day: date) -> date:
    """The same day one month earlier, clamped to that month's length (31 Mar gives the last day of February)."""
    y, m = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def account_read(payload) -> tuple[Balance | Tier, ...]:
    """What a `nzlxg` answer says of the account: the video pool's balance with its refill and, for a plan flow names,
    the tier. A paid plan's refill is its monthly renewal (measured: ~2 weeks ahead on AI Pro, ~24 h on free), so its
    cycle started a month before it. Nothing when the balance cannot be read."""
    credits = credits_of(payload)
    if credits is None:
        return ()
    stamp = payload[6] if len(payload) > 6 else None
    refill = (datetime.fromtimestamp(stamp[0], UTC) if isinstance(stamp, list) and stamp and type(stamp[0]) is int
              and stamp[0] > 0 else None)
    out: list[Balance | Tier] = [Balance("video", credits, refill)]
    tier = PAYGATE_TIERS.get(payload[1]) if len(payload) > 1 and type(payload[1]) is int else None
    if tier is not None:
        out.append(Tier(tier, month_before(refill.date()) if refill is not None and tier != "free" else None))
    return tuple(out)


async def read_account(site: Site) -> tuple[Balance | Tier, ...]:
    """The account's credits and plan; nothing when flow does not answer them (the stored values stay)."""
    try:
        reply = await site.rpc(CREDITS, [])
    except httpx.HTTPError:
        return ()
    return account_read(reply.payload) if reply.status == 200 else ()


async def create_project(site: Site) -> str | None:
    """A new empty project (free); its id, or None when flow did not give one."""
    reply = await site.rpc(CREATE_PROJECT, ["projects/*", [None, [PROJECT_TITLE]], [None, CLIENT_TOOL]])
    payload = reply.payload
    if isinstance(payload, list) and payload and isinstance(payload[0], str) and payload[0]:
        return payload[0]
    return None


async def check_account(ctx, settings, sessions: Sessions, *, transport: httpx.AsyncBaseTransport | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC),
                        clock: Callable[[], float] = time.time) -> Health:
    """The state of one account. `transport`, `now` and `clock` are seams for tests."""
    try:
        async with Site(ctx, sessions, settings, transport=transport, clock=clock) as site:
            page = await site.load("/")
            if page.status != 200:
                return Health("unknown")
            if not page.signed_in:
                return Health("unknown", effects=doubt_effects(signed_out_why(site), now()))
            patch = {}
            if not (ctx.account.provider_state or {}).get(PROJECT_KEY):
                project = await create_project(site)
                if project:
                    patch[PROJECT_KEY] = project
            return Health("alive", identity=page.email, display_name=page.email, state_patch=patch,
                          effects=await read_account(site))
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("flow could not be reached (%s)" % type(e).__name__) from None
