"""Account check over HTTP: `GET /api/auth/session`, then `POST /rest/media/imagine/quota_info {}`. Both spend nothing;
nothing is sent, uploaded or opened.

Signed in is `alive`: identity = the session's `userId` (stable: a re-import of the same user merges), display name
its email (else its given name), `Tier("free")` (a paid `xSubscriptionType` still runs as free: the quota grok reports
decides; its name is logged), a `Balance` per pool grok reported, and `Success`. A signed-out answer (or a blocked
account) is never a verdict by itself: `unknown` with one spaced strike and a rest that asks for a recheck
(`doubt_effects`), to `SUSPECT_THRESHOLD` and `Dead("signed_out")`. Cloudflare or the anti-bot rules rest the account
with a recheck and no strike; a failed proxy is `ProxyBad` and a rest of `PROXY_REST_S`.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from relay_core.providers import Balance, Cooldown, Health, ProxyBad, Success, Suspect, Tier
from relay_grok.constants import PROXY_REST_S, QUOTA_WINDOW
from relay_grok.engine.web import NetError, Quota, Refused, SignedOut, Web, real_session

log = logging.getLogger("relay_grok.check")
SIGNED_OUT_REST_S = 600         # a signed-out (or blocked) read rests the account this long, then it is checked again
SIGNED_OUT_GAP_S = 540          # such a read this soon after the last counted strike adds none (meta's and qwen's)


def doubt_effects(why: str, settings, now: datetime) -> tuple[Suspect, Cooldown]:
    """A signed-out or blocked read: one spaced strike, and a rest after which the account is checked again."""
    return (Suspect(why, min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown(why + ", to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


def balances(q: Quota, now: datetime, settings) -> tuple[Balance, ...]:
    """A `Balance` per pool grok reported (none for a pool it did not). Its refill is `nextAvailableAt`; an empty pool
    grok gave no time for rests `grok.limit_rest_s`; a pool with uses left and no time refills at its window."""
    out = []
    for pool, left, refill in (("image", q.image_left, q.image_refill), ("video", q.video_left, q.video_refill)):
        if left is None:
            continue
        if refill is None and left == 0:
            refill = now + timedelta(seconds=settings.get("grok.limit_rest_s"))
        out.append(Balance(pool, left, refill, window=QUOTA_WINDOW))
    return tuple(out)


async def check_account(ctx, settings, *, make_session=real_session,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Health:
    """The state of one account. `make_session` and `now` are seams for tests."""
    account = ctx.account
    async with Web(account, settings, ctx, make_session=make_session) as web:
        try:
            session = await web.session()
            quota = await web.quota()
        except SignedOut as e:
            return Health("unknown", effects=doubt_effects("not signed in: %s" % e, settings, now()))
        except Refused as e:
            log.warning("grok check of %s: %s", account.id, e)
            if e.kind == "blocked":
                return Health("unknown", effects=doubt_effects("blocked: grok.com says the user is blocked",
                                                               settings, now()))
            if e.kind in ("cloudflare", "antibot"):
                rest = settings.get("grok.cf_rest_s" if e.kind == "cloudflare" else "grok.antibot_rest_s")
                return Health("unknown", effects=(Cooldown(
                    "grok.com answered the check with %s, to be checked again" % e.kind,
                    now() + timedelta(seconds=rest), recheck=True),))
            return Health("unknown")
        except NetError as e:
            log.warning("grok check of %s: grok.com could not be reached (%s)", account.id, e)
            if e.proxy:
                rest = Cooldown("the proxy failed", now() + timedelta(seconds=PROXY_REST_S))
                return Health("unknown", effects=(ProxyBad("the proxy failed (%s)" % e), rest))
            return Health("unknown")
        user = session.get("userId")
        if not isinstance(user, str) or not user:
            log.warning("grok check of %s: the session names no userId", account.id)
            return Health("unknown")
        if web.jar.get("x-userid") not in (None, user):
            log.warning("grok check of %s: the x-userid cookie is not the session's userId", account.id)
        plan = session.get("xSubscriptionType")
        if isinstance(plan, str) and plan:
            log.info("grok account %s: subscription %r checked as free", account.id, plan[:40])
        name = next((v for v in (session.get("email"), session.get("givenName")) if isinstance(v, str) and v), None)
        return Health("alive", identity=user, display_name=name[:200] if name else None,
                      effects=(Tier("free"), *balances(quota, now(), settings), Success()))
