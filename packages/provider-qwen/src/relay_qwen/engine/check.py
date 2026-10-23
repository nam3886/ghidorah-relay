"""Account check: a session refresh, the credit balance, and (while qwen claims jobs) a fresh anti-bot pair. It spends
nothing.

Signed in ⇔ the refresh gives an access token; the user it names (email) is the account's identity. A refresh that
says the session is gone is a doubt, never a verdict by itself: one spaced strike and a rest that asks for a recheck,
and only the strike threshold, reached by separate reads, ends the account. Any other unusable answer is an unknown
read with no strike; a network failure raises, so the check is recorded as failed. The credits (`totalCredits`) are
the account's `video` balance. With claims on, a pair older than half `qwen.bx_max_age_s` is minted anew, so a submit
seldom waits for a browser; a mint that fails is logged and the account stays alive (the submit tries again).
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import Balance, Cooldown, Health, Suspect
from relay_qwen.constants import SIGNED_OUT_GAP_S, SIGNED_OUT_REST_S
from relay_qwen.engine.http import BadAnswer, Sessions, SignedOut, Site

log = logging.getLogger(__name__)


def doubt_effects(why: str, now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect(why, min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


async def check_account(ctx, settings, sessions: Sessions, minter, *, transport: httpx.AsyncBaseTransport | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC),
                        clock: Callable[[], float] = time.time) -> Health:
    """The state of one account. `transport`, `now` and `clock` are seams for tests."""
    try:
        async with Site(ctx, sessions, transport=transport, clock=clock) as site:
            try:
                user = await site.refresh()
            except SignedOut as e:
                return Health("unknown", effects=doubt_effects("not signed in: %s" % e, now()))
            except BadAnswer:
                return Health("unknown")
            credits = await site.credits()
            effects = () if credits is None else (Balance("video", credits),)
            if settings.get("qwen.claims_enabled"):
                try:
                    await minter.fresh(ctx, sessions, int(settings.get("qwen.bx_max_age_s")) / 2)
                except Exception as e:  # noqa: BLE001 - a mint is a nicety here: the submit mints when it must
                    log.warning("qwen account %s: the check could not mint a pair (%s)", ctx.account.id, e)
            name = user.get("email") or user.get("id")
            return Health("alive", identity=name, display_name=user.get("email") or user.get("name"),
                          effects=effects)
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("qwen could not be reached (%s)" % type(e).__name__) from None
