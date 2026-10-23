"""The RunCtx an engine gets for one claimed job (invariant I2) and the account it works on.

`fence()`, `mark_sent()` and `report_ref()` act on the job's lease by its ID. Once the worker has been told to stop,
`fence()` and `mark_sent()` refuse with `Stopping`: no new irreversible action may start after the signal, so a job
that was not sent goes back to `queued` and one that was sent ends `unknown`. `report_ref()` and `event()` stay
open: they record what already happened.
"""

import functools
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
EvidenceSink = Callable[[str, str, bytes, str], Awaitable[None]]        # (job id, name, data, mime)
Merge = Callable[[bytes, bytes], bytes]      # (the credential stored now, mine) -> the merged credential


class Stopping(Exception):
    """The worker was told to stop: no new irreversible action may start."""


class LeaseCtx:
    """The platform as one claimed job's engine sees it. Engines never write the database."""

    def __init__(self, db: AsyncConnectionPool, claim: Claim, *, account: AccountView, settings: Any,
                 stopping: Callable[[], bool], evidence_sink: EvidenceSink | None = None):
        self._db, self._claim, self._stopping, self._evidence = db, claim, stopping, evidence_sink
        self.account, self.browser_token, self.settings = account, claim.token, settings
        self.log = logging.getLogger("relay.engine.%s" % claim.provider)
        self._renewal = Renewal(db, account)

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
        await self._evidence(str(self._claim.job_id), name, data, mime)

    async def renew_credential(self, blob: bytes, merge: Merge | None = None) -> bytes | None:
        return await self._renewal(blob, merge)


async def load_account_view(db: AsyncConnectionPool, account_id, private_keys: Iterable[str]) -> AccountView:
    """The decrypted credential, proxy and capacity of an account, for a worker (the only process that holds the
    private key). A blob that cannot be opened raises `crypto.CryptoError`. The view keeps the ciphertext it read
    (the compare-and-swap of a renewal) and how to read the account again (`reload`, for a renewal that lost it)."""
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
    sealed = bytes(credential)
    return AccountView(str(account_id), label, crypto.open_blob(sealed, keys), gen, proxy, state,
                       {pool: left for pool, left in caps}, crypto.sealing_key(sealed, keys), sealed,
                       functools.partial(load_account_view, db, account_id, keys))


class Renewal:
    """RunCtx.renew_credential for one run: a renewed copy of the account's session replaces the sealed credential,
    sealed with the key that sealed the one it replaces, by compare-and-swap on the exact ciphertext this run last
    read or wrote, and only at the credential generation the run read (a re-import bumps it and is never
    overwritten). The generation stays: it is the same session. Given `merge(stored, mine)`, EVERY write is the merge of
    what this run knows to be stored with the caller's copy, so a stale copy can never undo a newer session. A lost
    swap (another run renewed meanwhile) reads the account again and writes the merge once more. Returns the credential
    now stored (the caller keeps exactly that); None when a second loss, a re-import or a loss with no `merge` left
    nothing written."""

    def __init__(self, db: AsyncConnectionPool, account: AccountView | None):
        self.db, self.account = db, account
        self.sealed, self.key = getattr(account, "sealed", None), getattr(account, "seal_key", None)
        self.plain = getattr(account, "credential", None)        # the plaintext of `sealed`

    async def __call__(self, blob: bytes, merge: Merge | None = None) -> bytes | None:
        account = self.account
        if account is None or self.key is None or self.sealed is None:
            raise crypto.CryptoError("the account view has no sealed credential: it cannot be renewed")
        for attempt in range(2):
            if merge is not None and self.plain is not None:
                blob = merge(self.plain, blob)
            sealed = crypto.seal(self.key, blob)
            async with tx(self.db) as conn:
                cur = await conn.execute(
                    "UPDATE accounts SET credential_enc = %s, updated_at = clock_timestamp() "
                    "WHERE id = %s AND credential_gen = %s AND credential_enc = %s",
                    (sealed, account.id, account.credential_gen, self.sealed))
            if cur.rowcount == 1:
                self.sealed, self.plain = sealed, blob
                return blob
            if attempt or merge is None or account.reload is None:  # the last try (no reload), or no merge
                return None
            fresh = await account.reload()
            if fresh.credential_gen != account.credential_gen or fresh.sealed is None or fresh.seal_key is None:
                return None
            self.sealed, self.key, self.plain = fresh.sealed, fresh.seal_key, fresh.credential
        return None
