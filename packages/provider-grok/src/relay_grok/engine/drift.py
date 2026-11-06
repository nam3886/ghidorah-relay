"""The on-demand page check: `python -m relay_grok.engine.drift --account <label>`.

grok.com's deploys can rename the Imagine frame keys, the NDJSON keys of `conversations/new`, the `quota_info` keys or
the body keys the submit sends, which `engine/wire.py`, `engine/web.py` and the submit rely on (fixtures/grok/FINDINGS
F2–F6). This reads the `/imagine` page and the scripts it names and says which pinned name no script holds any more.
It also says whether the dormant `x-statsig-id` generator could work: the page's `grok-site-verification` meta, a
`"curves"` table, the salt in the scripts. On 06/11 none of the three was there (F1, design "Chống bot"): those lines
are notes, not drift, and never make the exit code 1.

It prints `OK` (exit 0) or one line per missing pinned name (exit 1), after the statsig notes: names only, never a
value from the page. It does one page read with the account's cookies through its proxy (it counts as a page load: run
it with grok drained or on a rested account) and fetches each `/_next/static/…js` script the page names on a fresh
session through the same proxy with no cookie. It sends nothing: no `conversations/new`, no socket, no upload.
"""

import argparse
import asyncio
import os
import re
import sys
from types import SimpleNamespace

from relay_grok.constants import QUOTA_IMAGE_KEY, QUOTA_VIDEO_KEY, SITE_URL
from relay_grok.engine.statsig import _CURVES, _META, SALT

PAGE_PATH = "/imagine"
STATSIG = "statsig "            # a note's prefix: never drift
# where -> the names the code reads or sends there (F2–F6, 06/11/2026). Distinctive names only: a key as short as
# `progress` is in every bundle and would prove nothing.
PINNED: dict[str, tuple[str, ...]] = {
    "imagine frame": ("current_status", "start_stage", "percentage_complete", "job_id", "request_id",
                      "conversation_id", "num_generations", "aspect_ratio", "enable_pro", "input_text"),
    "media stream": ("conversationId", "streamingImageGenerationResponse", "streamingVideoGenerationResponse",
                     "videoId", "videoPostId", "videoUrl", "imageUrl", "assetId", "rRated", "modelResponse"),
    "media send": ("mediaGenInput", "imageToImage", "inputAssets", "textToVideo", "resolutionName"),
    "quota": (QUOTA_IMAGE_KEY, QUOTA_VIDEO_KEY, "remainingQueries", "nextAvailableAt"),
}
_SCRIPT = re.compile(r'(?:src|href)="(/_next/static/[^"?#]+\.js)')


def check_page(html: str, chunks: list[str]) -> list[str]:
    """The statsig notes (what `statsig.find_seed` looks for: the meta with a content, `"curves":`, the salt), then one
    line per pinned name no chunk holds. Names only."""
    js = "\n".join(chunks)
    notes = [] if any(m.search(html) for m in _META) else [STATSIG + "grok-site-verification: not on the page"]
    if _CURVES not in (html + js).replace('\\"', '"'):
        notes.append(STATSIG + "curves: not on the page or its scripts")
    if SALT not in js:
        notes.append(STATSIG + "salt: not in the scripts")
    return notes + ["%s %s: not in the scripts" % (where, name)
                    for where, names in PINNED.items() for name in names if name not in js]


def exit_code(lines: list[str]) -> int:
    """1 when a pinned name is missing; the statsig notes alone are 0."""
    return 1 if any(not line.startswith(STATSIG) for line in lines) else 0


def scripts_of(html: str) -> list[str]:
    """The `/_next/static/…js` paths a page names, once each, in order."""
    return list(dict.fromkeys(_SCRIPT.findall(html)))


class Unreadable(Exception):
    """The page or its scripts could not be read; the text is safe to print (no value from the page, no proxy login)."""


async def fetch_page(account, settings, **web_kw) -> tuple[str, list[str]]:
    """The `/imagine` page (the account's cookies, its proxy, no redirect) and the text of each script it names, read
    on a second session with no cookie through the same proxy. `Unreadable` otherwise."""
    # imported here: the pure part above needs no curl_cffi
    from relay_grok.engine.web import Web

    try:
        async with Web(account, settings, **web_kw) as web:      # no ctx: no event, nothing written back
            status, html = await web.get_text(SITE_URL + PAGE_PATH)
        if status != 200:
            raise Unreadable("the page answered %d" % status)
        names = scripts_of(html)
        if not names:
            raise Unreadable("the page names no scripts")
        chunks = []
        # a fresh session: the page's Set-Cookie (a rotated sso) sits in the first one's jar and must not follow
        async with Web(account, settings, **web_kw) as bare:
            for name in names:
                status, text = await bare.get_text(SITE_URL + name, cookies=False)
                if status == 200:
                    chunks.append(text)
    except Unreadable:
        raise
    except Exception as e:      # noqa: BLE001 - only the kind is kept: curl's text may hold the proxy's login
        raise Unreadable("could not read the page (%s)" % type(e).__name__) from None
    if not chunks:
        raise Unreadable("no script of the page answered 200")
    return html, chunks


async def _run(label: str) -> int:
    # imported here: the pure part above needs no database
    from relay_core import crypto, pool
    from relay_core.db.pool import open_pool, tx
    from relay_core.jobs.runctx import load_account_view
    from relay_core.jobs.worker import provider_settings
    from relay_grok.spec import SPEC

    defaults = {s.key: s.default for s in provider_settings(SPEC).specs.values()}   # stored overrides are not read
    keys = crypto.private_keys_from_env(os.environ)
    db = await open_pool(os.environ["DATABASE_URL"], name="drift-grok")
    try:
        async with tx(db) as conn:
            row = await (await conn.execute("SELECT id FROM accounts WHERE provider = 'grok' AND label = %s",
                                            (label,))).fetchone()
        if row is None:
            print("no grok account with that label")
            return 2
        account = await load_account_view(db, row[0], keys, pool.pool_configs(SPEC.name, SPEC.caps, defaults.get))
    finally:
        await db.close()
    try:
        html, chunks = await fetch_page(account, SimpleNamespace(get=defaults.__getitem__))
    except Unreadable as e:
        print(e)
        return 2
    lines = check_page(html, chunks)
    code = exit_code(lines)
    print("\n".join(lines + ([] if code else ["OK"])))
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m relay_grok.engine.drift", description=__doc__.splitlines()[0])
    ap.add_argument("--account", required=True, help="the label of a grok account (its proxy is used)")
    return asyncio.run(_run(ap.parse_args(argv).account))


if __name__ == "__main__":
    sys.exit(main())
