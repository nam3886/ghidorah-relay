"""The RunCtx an engine gets for one claimed job (contracts 5.2, 7.7; invariant I2) and the account it works on.

`fence()`, `mark_sent()` and `report_ref()` act on the job's lease by its ID. Once the worker has been told to stop,
`fence()` and `mark_sent()` refuse with `Stopping`: no new irreversible action may start after the signal, so a job
that was not sent goes back to `queued` and one that was sent ends `unknown`. `report_ref()` and `event()` stay
open: they record what already happened.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from psycopg_pool import AsyncConnectionPool

from relay_core import crypto, leases
from relay_core.db import tx
from relay_core.jobs.claim import Claim
from relay_core.jobs.events import add_event
from relay_core.jobs.store import mark_sent, report_ref
from relay_core.providers import AccountView, Proxy

log = logging.getLogger(__name__)

EngineSource = ("upstream", "engine")
EvidenceSink = Callable[[str, bytes, str], Awaitable[None]]


class Stopping(Exception):
    """The worker was told to stop: no new irreversible action may start."""


class LeaseCtx:
    """The platform as one claimed job's engine sees it. Engines never write the database."""

    def __init__(self, db: AsyncConnectionPool, claim: Claim, *, account: AccountView, settings: Any,
                 stopping: Callable[[], bool], evidence_sink: EvidenceSink | None = None):
        self._db, self._claim, self._stopping, self._evidence = db, claim, stopping, evidence_sink
        self.account, self.browser_token, self.settings = account, claim.token, settings
        self.log = logging.getLogger("relay.engine.%s" % claim.provider)

    async def fence(self) -> None:
        if self._stopping():
            raise Stopping("the worker is stopping")
        await leases.fence_once(self._db, self._claim.lease_id)

    async def mark_sent(self) -> None:
        if self._stopping():
            raise Stopping("the worker is stopping")
        await mark_sent(self._db, self._claim)

    async def report_ref(self, ref: str, state: Mapping[str, Any]) -> None:
        await report_ref(self._db, self._claim, ref, state)

    async def event(self, text: str, stage: str, source: str) -> None:
        if source not in EngineSource:
            raise ValueError("an engine event source is one of %s" % (EngineSource,))
        async with tx(self._db) as conn:
            await add_event(conn, self._claim.job_id, source, stage, text)

    async def evidence(self, name: str, data: bytes, mime: str) -> None:
        """Debug evidence needs the object store; the process wires a sink for it. Without one it is dropped."""
        if self._evidence is None:
            self.log.warning("evidence %r dropped: no evidence sink is wired", name)
            return
        await self._evidence(name, data, mime)


async def load_account_view(db: AsyncConnectionPool, account_id, private_keys: Iterable[str]) -> AccountView:
    """The decrypted credential, proxy and capacity of an account, for a worker (the only process that holds the
    private key). A blob that cannot be opened raises `crypto.CryptoError`."""
    async with tx(db) as conn:
        row = await (await conn.execute(
            "SELECT label, credential_enc, credential_gen, proxy_host, proxy_port, proxy_auth_enc, provider_state "
            "FROM accounts WHERE id = %s", (account_id,))).fetchone()
        if row is None:
            raise LookupError("no account %s" % account_id)
        caps = await (await conn.execute("SELECT pool, left_n FROM account_capacity WHERE account_id = %s",
                                         (account_id,))).fetchall()
    keys = list(private_keys)
    label, credential, gen, host, port, auth, state = row
    proxy = None
    if host is not None:
        proxy = Proxy(host, port, crypto.open_blob(bytes(auth), keys).decode() if auth is not None else None)
    return AccountView(str(account_id), label, crypto.open_blob(bytes(credential), keys), gen, proxy, state,
                       {pool: left for pool, left in caps})
