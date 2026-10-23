"""The core title sweep.

For a provider with `Caps.titles`, jobs with an empty title, an `external_ref`, created in the last 24 hours and
sent at least 60 seconds ago (`done` jobs included) are grouped by account, 50 refs per call, and the engine's
`run_command("titles", {"refs": [...]})` answers `{"titles": {ref: title}}`. A title is set only while the job's title
is still empty: a title the client or the admin set is never overwritten. The sweep is read-only HTTP: it takes no
account lease and no browser token, so it never waits behind or blocks a check. Drain mode does not stop it.
"""

import asyncio
import logging
import uuid
from collections.abc import Mapping

from psycopg import AsyncConnection

from relay_core.db import tx
from relay_core.jobs.checks import TIMEOUT_SHARE, AccountCtx, Env, shielded
from relay_core.redact import redact_log

log = logging.getLogger(__name__)

TITLE_LIMIT = 200
MIN_SENT_S = 60
MAX_AGE_S = 24 * 3600
BATCH = 50
SWEEP_EVERY_S = 60.0

# The title every reader shows (the API status, the admin's jobs, logs and alerts), as SQL over `jobs j`: the job's
# own title (the client's, the admin's or the provider's native name), else the prompt's first 8 words on one line,
# cut to 60 characters on a word boundary. It is computed on read and never stored, so the column stays empty until a
# native name lands (this sweep, or a submit or poll result's title): the native name always wins. The stored prompt
# is the client's own words (the frame is in `sent_text`), so nothing has to be skipped at its start.
SHOWN_TITLE = (
    "COALESCE(NULLIF(j.title, ''), (SELECT NULLIF(rtrim(COALESCE(substring(w FROM '^.{1,60}(?= |$)'), left(w, 60)), "
    "' ,.;:-'), '') FROM (SELECT substring(btrim(regexp_replace(left(j.prompt, 2000), '\\s+', ' ', 'g')) "
    "FROM '^\\S+(?: \\S+){0,7}') AS w) AS s))")


def clean_title(raw: object) -> str | None:
    """One line of at most 200 characters, through the redactor; None when nothing is left."""
    text = " ".join(str(raw or "").split())[:TITLE_LIMIT]
    return redact_log(text) if text else None


async def apply_titles(conn: AsyncConnection, provider: str, titles: Mapping[str, object]) -> int:
    """Set each title only where the job still has none. Returns how many jobs got one."""
    done = 0
    for ref, raw in titles.items():
        title = clean_title(raw)
        if title is None:
            continue
        done += (await conn.execute(
            "UPDATE jobs SET title = %s, updated_at = clock_timestamp() WHERE provider = %s AND external_ref = %s "
            "AND (title IS NULL OR title = '')", (title, provider, str(ref)))).rowcount
    return done


_CANDIDATES = (
    "SELECT account_id, external_ref FROM jobs WHERE provider = %(p)s AND (title IS NULL OR title = '') "
    "AND external_ref IS NOT NULL AND account_id IS NOT NULL "
    "AND created_at >= clock_timestamp() - make_interval(secs => %(age)s) "
    "AND sent_at <= clock_timestamp() - make_interval(secs => %(sent)s) ORDER BY account_id, created_at, id")


async def sweep_titles(env: Env, *, min_sent_s: float = MIN_SENT_S, max_age_s: float = MAX_AGE_S,
                       batch: int = BATCH) -> int:
    """One round of the sweep; returns the number of titles set."""
    if not env.caps.titles:
        return 0
    async with tx(env.db) as conn:
        rows = await (await conn.execute(_CANDIDATES, {"p": env.provider, "age": max_age_s,
                                                       "sent": min_sent_s})).fetchall()
    by_account: dict[uuid.UUID, list[str]] = {}
    for account_id, ref in rows:
        by_account.setdefault(account_id, []).append(ref)
    set_n = 0
    for account_id, refs in by_account.items():
        for i in range(0, len(refs), batch):
            got = await _one_call(env, account_id, refs[i:i + batch])
            if got is None:
                break                    # busy, no token or a failing account: the next round tries again
            set_n += got
    return set_n


async def _one_call(env: Env, account_id, refs: list[str]) -> int | None:
    """One read-only HTTP call: no account lease and no browser token (it must not hold the account's only slot or
    the shared check-and-command token). The write is conditional on the job still having no title."""
    try:
        async with asyncio.timeout(env.command_hard_s("titles") * TIMEOUT_SHARE):
            view = await env.load_account(account_id)
            raw = await env.engine.run_command(AccountCtx(env, None, view, None), "titles", {"refs": refs})
    except Exception as e:      # the titles are only for convenience: one bad account must not stop the sweep
        log.warning("title sweep: account %s failed: %s", account_id, redact_log(str(e) or type(e).__name__))
        return None
    return await shielded(_apply(env, raw))


async def _apply(env: Env, raw: object) -> int | None:
    titles = raw.get("titles") if isinstance(raw, Mapping) else None
    if not isinstance(titles, Mapping):
        log.warning("title sweep: the engine returned no titles mapping")
        return None
    async with tx(env.db) as conn:
        return await apply_titles(conn, env.provider, titles)
