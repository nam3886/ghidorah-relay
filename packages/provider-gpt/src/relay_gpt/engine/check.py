"""Account check: chatgpt.com's session read and the image limit, over plain HTTP through the account's proxy. It
spends nothing.

The session read (`GET /api/auth/session`, always read, never a cached token) is also what keeps the account alive:
chatgpt.com re-sets the session cookie there and the run writes it back (web.keep_rotated). Signed in <=> the read
gives an access token. Its claims name the identity, `<chatgpt_user_id>/<chatgpt_account_id>` (one person in one
workspace: two members of a Team never merge, one user's own and team workspaces stay apart), and the plan,
which becomes the tier (free/go -> free; plus/team/enterprise/edu -> pro; pro -> ultra; anything else -> free, logged
by name). A signed-out read (no token, or a 401) is a doubt, never a verdict by itself: one spaced strike and a rest
that asks for a recheck; only the strike threshold, reached by separate reads, ends the account. A network failure
raises, so the check is recorded as failed.

`conversation/init`'s `limits_progress` is read for the image limit: Plus never counts down there (FINDINGS F8), so
only a `remaining` of 0 is believed (the pool empty until its `reset_after`); any other figure reports nothing.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from relay_core.providers import Balance, Cooldown, Health, Suspect, Tier
from relay_gpt.constants import TIMEZONE_OFFSET_MIN
from relay_gpt.engine.web import BadAnswer, NetError, Sessions, SignedOut, Web, claims, real_session

log = logging.getLogger(__name__)

INIT_PATH = "/backend-api/conversation/init"
PLAN_TIERS = {"free": "free", "go": "free", "plus": "pro", "team": "pro", "enterprise": "pro", "edu": "pro",
              "pro": "ultra"}


def tier_of(plan: str | None) -> str:
    """The core tier of a chatgpt.com plan; an unknown plan is free (the smallest budget)."""
    return PLAN_TIERS.get(plan or "", "free")


def doubt_effects(why: str, settings, now: datetime) -> tuple[Suspect, Cooldown]:
    """A signed-out read: one strike (spaced by `gpt.signed_out_gap_s`) and a rest that asks for a recheck."""
    return (Suspect(why, min_gap_s=settings.get("gpt.signed_out_gap_s")),
            Cooldown(why, now + timedelta(seconds=settings.get("gpt.signed_out_rest_s")), recheck=True))


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def image_balance(init_body: Any) -> Balance | None:
    """`Balance("image", 0, refill_at=reset_after)` when the `image_gen` limit says none remain; None otherwise."""
    rows = init_body.get("limits_progress") if isinstance(init_body, dict) else None
    if not isinstance(rows, list):
        return None
    row = next((r for r in rows if isinstance(r, dict) and r.get("feature_name") == "image_gen"), None)
    remaining = row.get("remaining") if row is not None else None
    if type(remaining) is not int or remaining != 0:
        return None
    return Balance("image", 0, refill_at=_when(row.get("reset_after")))


async def check_account(ctx, settings, sessions: Sessions, *, session=None,
                        make_session: Callable[[dict[str, str] | None], Any] = real_session,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Health:
    try:
        async with Web(ctx.account, settings, sessions, ctx, session=session, make_session=make_session) as web:
            try:
                body = await web.session()
                status, init = await web.post_json(INIT_PATH, {
                    "gizmo_id": None, "requested_default_model": None, "conversation_id": None,
                    "timezone_offset_min": TIMEZONE_OFFSET_MIN})
            except SignedOut:
                return Health("unknown", effects=doubt_effects("chatgpt.com answered the session read signed out",
                                                               settings, now()))
    except (NetError, BadAnswer) as e:
        raise RuntimeError(str(e)) from None
    token = claims(web.token or "")
    user, workspace = token.get("chatgpt_user_id"), token.get("chatgpt_account_id")
    if not isinstance(user, str) or not user or not isinstance(workspace, str) or not workspace:
        raise RuntimeError("the access token names no user or no workspace")
    account = body.get("account") if isinstance(body.get("account"), dict) else {}
    plan = token.get("chatgpt_plan_type") or account.get("planType")
    plan = plan[:40] if isinstance(plan, str) else None
    if plan not in PLAN_TIERS:
        log.info("gpt account %s: unknown plan %r taken as free", ctx.account.id, plan)
    email = (body.get("user") or {}).get("email") if isinstance(body.get("user"), dict) else None
    balance = image_balance(init) if status == 200 else None
    return Health("alive", identity="%s/%s" % (user, workspace),
                  display_name=email[:200] if isinstance(email, str) and email else None,
                  state_patch={"plan": plan},
                  effects=(Tier(tier_of(plan)), *((balance,) if balance is not None else ())))
