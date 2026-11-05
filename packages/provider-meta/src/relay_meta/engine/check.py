"""Account check over HTTP: read the www.meta.ai page (gate permitting), then open one gateway socket and send one
EstabStream with no prompt. It spends nothing and sends no data frame.

Signed in and an accepted estab is `alive`; its identity is `viewer_hash(viewerId)` (also kept as
`provider_state["viewer"]`, which is what the cookie write-back compares). A signed-out page is never a verdict by
itself: an `unknown` read with one spaced strike and a rest that asks for a recheck (`signed_out_effects`). A Vercel
page is the proxy IP's problem, never the account's: the account rests (no strike) only when it has no token to
check with. A refused estab (401) forgets the token and is `unknown`.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from relay_core.providers import Cooldown, Health
from relay_meta.constants import ESTAB_WAIT_S
from relay_meta.credential import viewer_hash
from relay_meta.engine.web import GateClosed, NetError, Sessions, Web, quiet_close, real_session, signed_out_effects
from relay_meta.engine.wire import ESTAB, FrameError, estab, estab_answer, parse_frame

log = logging.getLogger("relay_meta.check")
UNKNOWN = Health("unknown")


async def _estab(web: Web, token: str) -> str:
    """The gateway's answer to one EstabStream (`ok`, `unauthorized`, `other`); a socket that says nothing in
    `ESTAB_WAIT_S` or sends a frame that does not decode is `other`."""
    sock = await web.socket(token)
    try:
        await sock.send(estab(str(uuid.uuid4())))
        async with asyncio.timeout(ESTAB_WAIT_S):
            while True:
                t, _stream, payload = parse_frame(await sock.recv())
                if t == ESTAB:
                    return estab_answer(payload)
    except (TimeoutError, FrameError):
        return "other"
    finally:
        await quiet_close(sock)


async def check_account(ctx, settings, sessions: Sessions, *, make_session=real_session,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Health:
    """The state of one account. `make_session` and `now` are seams for tests."""
    account = ctx.account
    held = sessions.token(account.id, account.credential_gen)
    async with Web(account, settings, sessions, ctx, make_session=make_session) as web:
        try:
            page = await web.page()
        except GateClosed as gate:
            if not gate.marked:
                return UNKNOWN
            page = None
        except NetError as e:      # a dead proxy or an unreachable site says nothing about the account
            log.warning("meta check of %s: %s", account.id, e)
            return UNKNOWN
        if page is not None and page.state == "out":
            return Health("unknown", effects=signed_out_effects(now()))
        if page is not None and page.state == "in":
            sessions.keep(account.id, account.credential_gen, page)
            token, viewer, name = page.token, page.viewer, page.name
        elif page is None or page.state == "vercel":
            if held is None:
                until = sessions.gate.until(web.ip)
                end = now() + timedelta(seconds=(until - sessions.clock()) if until is not None
                                        else settings.get("meta.checkpoint_rest_s"))
                return Health("unknown", effects=(Cooldown(
                    "the proxy IP rests after a Vercel page, to be checked again", end, recheck=True),))
            token, viewer, name = held.token, held.viewer, None
        else:
            return UNKNOWN
        try:
            answer = await _estab(web, token)
        except NetError as e:
            log.warning("meta check of %s: %s", account.id, e)
            return UNKNOWN
        if answer == "unauthorized":
            sessions.forget(account.id)
        if answer != "ok":
            return UNKNOWN
        identity = viewer_hash(viewer)
        return Health("alive", identity=identity, display_name=name, state_patch={"viewer": identity})
