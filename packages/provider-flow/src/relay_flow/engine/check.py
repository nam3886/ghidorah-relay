"""Account check: one page load of flow with the account's session, over its proxy. It spends nothing.

The run first rotates the session when it is due (`Site`), which is how an idle account stays signed in: the check
runs every 5 min. Signed in ⇔ the page carries `SNlM0e` (FINDINGS 1); the email on the page is the account's identity.
A page without it is a doubt, never a verdict by itself: one spaced strike and a rest that asks for a recheck, and only
the strike threshold, reached by separate reads, ends the account. Any other answer (not 200) is an unknown read with
no strike; a network failure raises, so the check is recorded as failed (no strike, no word of the proxy's login).

A signed-in account without a project gets one (`jHPbke`, free): every job of the account is made in it, and its
listing is how a lost reply is reconciled. Its id is kept in the account's `provider_state.project`.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from relay_core.providers import Cooldown, Health, Suspect
from relay_flow.constants import SIGNED_OUT_GAP_S, SIGNED_OUT_REST_S
from relay_flow.engine.http import CLIENT_TOOL, Sessions, Site

PROJECT_KEY = "project"
CREATE_PROJECT = "jHPbke"
PROJECT_TITLE = "relay"


def doubt_effects(why: str, now: datetime) -> tuple[Suspect, Cooldown]:
    """What a signed-out read does to an account: one spaced strike, and a rest after which it is checked again."""
    return (Suspect(why, min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


def signed_out_why(site: Site) -> str:
    rotated = " (RotateCookies answered %d)" % site.rotation if site.rotation not in (None, 200) else ""
    return "not signed in: flow's page carries no session token%s" % rotated


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
            return Health("alive", identity=page.email, display_name=page.email, state_patch=patch)
    except httpx.HTTPError as e:
        # the exception text may carry the proxy URL: only its kind goes out
        raise RuntimeError("flow could not be reached (%s)" % type(e).__name__) from None
