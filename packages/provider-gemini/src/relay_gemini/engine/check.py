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
`provider_state.mode`. Every order of the account goes to that index with that mode.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import Cooldown, Health, Suspect
from relay_gemini.constants import SIGNED_OUT_GAP_S, SIGNED_OUT_REST_S
from relay_gemini.engine.http import Sessions, Site


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
            patch = {"authuser": site.authuser, **({"mode": mode} if mode else {})}
            return Health("alive", identity=page.email, display_name=page.email, state_patch=patch)
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("gemini could not be reached (%s)" % type(e).__name__) from None
