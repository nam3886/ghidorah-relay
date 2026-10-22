"""meta end-to-end run against the live service: `relay e2e --provider meta`.

It makes a temporary API key with a small image wallet, draws one `meta-image` from a text prompt through the running
API, waits for it, checks the first delivered file is a real image (WebP, PNG or JPEG), and disables the key
afterwards. It SPENDS a meta account's daily image job (a real generation) and so refuses unless
`RELAY_E2E_SPEND=yes`. Environment: `DATABASE_URL` (a role that may create keys), `RELAY_E2E_URL` (the API, default
http://127.0.0.1:8100). Never prints a key.
"""

import asyncio
import os
import sys
import time
from collections.abc import Mapping

import httpx

from relay_core import keys
from relay_core.db import open_pool, tx
from relay_meta.catalog import IMAGE_MODEL

MIN_BYTES = 10_000       # a real drawing is hundreds of KB; an error page or an empty file is far below this
WAIT_S = 600             # done 16-27 s after Enter (FINDINGS); the submit bound is 240 s, plus a queue and a cold load
POLL_S = 15              # the same cadence as dola's e2e


def image_ok(data: bytes) -> bool:
    """A WebP (what meta delivers), PNG or JPEG of a non-trivial size."""
    signed = (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or data[:8] == b"\x89PNG\r\n\x1a\n" \
        or data[:3] == b"\xff\xd8\xff"
    return signed and len(data) >= MIN_BYTES


async def run(env: Mapping[str, str]) -> int:
    if env.get("RELAY_E2E_SPEND") != "yes":
        print("relay e2e meta: this run spends a meta image job; set RELAY_E2E_SPEND=yes to go on", file=sys.stderr)
        return 2
    dsn, base = env.get("DATABASE_URL", ""), env.get("RELAY_E2E_URL", "http://127.0.0.1:8100")
    if not dsn:
        print("relay e2e meta: DATABASE_URL is not set", file=sys.stderr)
        return 2
    pool = await open_pool(dsn, max_size=2, name="relay-e2e")
    try:
        async with tx(pool) as conn:
            made = await keys.create_key(conn, label="e2e-meta", models=[IMAGE_MODEL], max_unfinished=1,
                                         declared=[("meta", "image"), ("meta", "video")],
                                         wallets={("meta", "image"): (5, 5)})
        try:
            return await _generate(base, made.raw)
        finally:
            async with tx(pool) as conn:
                await keys.set_enabled(conn, made.id, False)       # disabled afterwards, never left open
    finally:
        await pool.close()


async def _generate(base: str, raw_key: str) -> int:
    headers = {"Authorization": "Bearer " + raw_key}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as http:
        made = await http.post("/v1/images/generations", json={
            "model": IMAGE_MODEL, "prompt": "A paper boat drifting on a calm pond at sunrise"})
        if made.status_code >= 300:
            print("relay e2e meta: create answered %d" % made.status_code, file=sys.stderr)
            return 1
        job = made.json()["id"]
        end = time.monotonic() + WAIT_S
        while time.monotonic() < end:
            state = (await http.get("/v1/images/generations/%s" % job)).json()
            if state["status"] == "failed":
                print("relay e2e meta: job failed: %s" % state.get("error_code"), file=sys.stderr)
                return 1
            if state["status"] == "done":
                break
            await asyncio.sleep(POLL_S)
        else:
            print("relay e2e meta: no result in %d s" % WAIT_S, file=sys.stderr)
            return 1
        image = await http.get("/v1/images/generations/%s/content/0" % job)
        image.raise_for_status()
    ok = image_ok(image.content)
    print("relay e2e meta: %s, %d bytes" % ("OK" if ok else "NOT AN IMAGE", len(image.content)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(os.environ)))
