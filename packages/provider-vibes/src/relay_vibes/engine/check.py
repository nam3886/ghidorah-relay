"""Account check: two reads of vibes' API with the account's session, over its proxy. It spends nothing.

`GET /api/auth/me` says who the session is: `{user: {id, username, accountStatus}}`. The user id is the account's
identity, the username its display name. A 401 or 403 means the session is gone; a status other than `ACTIVE` is a
doubt too. Neither is a verdict by itself (one bad read must not end an account): it is an `unknown` read with one
spaced strike and a rest that asks for a recheck, and only the strike threshold of the caps, reached by separate reads,
ends the account. Any other answer (a 5xx, a body that is not a user) is an unknown read with no strike. A network
failure raises, so the check is recorded as failed (no strike, no word of the proxy's login).

`GET /api/quota/upsell` then says whether quota is left: `{upsell: null}` while it is, a banner whose `bannerStatus` is
`enabled` once it is used up. Used up reports 0 left on every pool; the pools fill again at their day's reset. A failed
quota read changes nothing. `/api/auth/check-token` is throttled by vibes and is never called.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import Balance, Cooldown, Health, Suspect
from relay_vibes.constants import (
    ACTIVE_STATUS,
    ME_PATH,
    QUOTA_PATH,
    QUOTA_USED_BANNER,
    SIGNED_OUT_GAP_S,
    SIGNED_OUT_REST_S,
)
from relay_vibes.engine.http import client_for

POOLS = ("video", "image")
SIGNED_OUT_STATUSES = (401, 403)


def doubt_effects(why: str, now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect(why, min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


def _json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def quota_used(body: dict) -> bool:
    """Does the upsell answer show the used-up banner?"""
    upsell = body.get("upsell")
    return isinstance(upsell, dict) and str(upsell.get("bannerStatus") or "").lower() == QUOTA_USED_BANNER


async def check_account(ctx, settings, *, transport: httpx.AsyncBaseTransport | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Health:
    """The state of one account. `transport` and `now` are seams for tests."""
    try:
        async with client_for(ctx.account, transport) as client:
            me = await client.get(ME_PATH)
            if me.status_code in SIGNED_OUT_STATUSES:
                return Health("unknown", effects=doubt_effects(
                    "not signed in: vibes answered %d to the session" % me.status_code, now()))
            user = _json(me).get("user") if me.status_code == 200 else None
            if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
                return Health("unknown")
            status = user.get("accountStatus")
            if status != ACTIVE_STATUS:
                return Health("unknown", effects=doubt_effects("vibes says the account is %s" % str(status)[:40],
                                                               now()))
            try:
                quota = await client.get(QUOTA_PATH)
            except httpx.HTTPError:
                quota = None
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("vibes.ai could not be reached (%s)" % type(e).__name__) from None
    used = quota is not None and quota.status_code == 200 and quota_used(_json(quota))
    effects = tuple(Balance(pool, 0) for pool in POOLS) if used else ()
    name = user.get("username")
    return Health("alive", identity=user["id"], display_name=name if isinstance(name, str) and name else None,
                  effects=effects)
