"""One run's way into the account's VM: muse.ai's session and tokens over HTTP, then the tunnel, set up.

Every run (submit, poll, reconcile, fetch) opens its own: the tunnel's auth token is short-lived, and the session read
that starts it keeps the account's cookies fresh. `Deps` holds the seams tests replace.
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from relay_muse.engine.chat import setup
from relay_muse.engine.tunnel import Tunnel
from relay_muse.engine.web import Web, bootstrap, real_session

HANDSHAKE_TIMEOUT_S = 20.0     # the Noise handshake's answer
CALL_TIMEOUT_S = 30.0          # one call in the tunnel (a history read, a setup call)

_store = None


async def read_input_media(ref) -> bytes:
    """The job's input image from the object store, the store made on first use. The one place the engine reaches past
    `RunCtx` for a file, as dola's and meta's do: the protocol hands out only a `MediaRef`."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


@dataclass
class Deps:
    """What a run needs from outside; every field has the real default."""
    make_session: Callable[[dict[str, str] | None], Any] = real_session
    read_media: Callable[..., Awaitable[bytes]] = read_input_media
    sleep: Callable[[float], Awaitable] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    handshake_timeout_s: float = HANDSHAKE_TIMEOUT_S
    call_timeout_s: float = CALL_TIMEOUT_S


@contextlib.asynccontextmanager
async def opened(ctx, settings, deps: Deps, *, chat: bool,
                 node: str | None = None) -> AsyncIterator[tuple[Web, Tunnel]]:
    """(web, tunnel) of one run, set up for a send (`chat`) or for reads; both closed after. Raises what the steps
    raise (web.SignedOut, VmNotReady, SiteError, NetError; tunnel.TunnelError; chat.SetupError)."""
    async with Web(ctx.account, ctx, make_session=deps.make_session) as web:
        boot = await bootstrap(web, settings, sleep=deps.sleep, clock=deps.clock)
        tunnel = await Tunnel.open(await web.socket(boot.ws_url), deps.handshake_timeout_s, deps.clock)
        try:
            await setup(tunnel, node or str(uuid.uuid4()), chat=chat, timeout=deps.call_timeout_s)
            yield web, tunnel
        finally:
            await tunnel.close()
