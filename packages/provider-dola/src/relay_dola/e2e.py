"""dola end-to-end run against the live service (contracts §14, item 10): `relay e2e --provider dola`.

It makes a temporary API key with a small video wallet, creates one 5-second video through the running API, waits for
it, checks the delivered length with ffprobe, and disables the key afterwards. It SPENDS dola credits (a real
generation) and so refuses unless `RELAY_E2E_SPEND=yes`. Environment: `DATABASE_URL` (a role that may create keys),
`RELAY_E2E_URL` (the API, default http://127.0.0.1:8100). Never prints a key.
"""

import asyncio
import os
import sys
import tempfile
import time
from collections.abc import Mapping

import httpx

from relay_core import keys
from relay_core.db import open_pool, tx

MODEL = "seedance-2.5"
SECONDS = 5
TOLERANCE_S = 2          # the same tolerance the poll uses for "duration drift"
WAIT_S = 1800


def measured_length_ok(measured: float, wanted: int, tolerance: float = TOLERANCE_S) -> bool:
    return abs(measured - wanted) <= tolerance


async def ffprobe_seconds(path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffprobe failed: %s" % err.decode(errors="replace")[:200])
    return float(out.decode().strip())


async def run(env: Mapping[str, str]) -> int:
    if env.get("RELAY_E2E_SPEND") != "yes":
        print("relay e2e dola: this run spends dola credits; set RELAY_E2E_SPEND=yes to go on", file=sys.stderr)
        return 2
    dsn, base = env.get("DATABASE_URL", ""), env.get("RELAY_E2E_URL", "http://127.0.0.1:8100")
    if not dsn:
        print("relay e2e dola: DATABASE_URL is not set", file=sys.stderr)
        return 2
    pool = await open_pool(dsn, max_size=2, name="relay-e2e")
    try:
        async with tx(pool) as conn:
            made = await keys.create_key(conn, label="e2e-dola", models=[MODEL], max_unfinished=1,
                                         declared=[("dola", "video"), ("dola", "image")],
                                         wallets={("dola", "video"): (10, 10)})
        try:
            return await _generate(base, made.raw)
        finally:
            async with tx(pool) as conn:
                await keys.set_enabled(conn, made.id, False)       # D31: disabled afterwards, never left open
    finally:
        await pool.close()


async def _generate(base: str, raw_key: str) -> int:
    headers = {"Authorization": "Bearer " + raw_key}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=60) as http:
        made = await http.post("/v1/video/generations", json={
            "model": MODEL, "prompt": "A paper boat drifting on a calm pond at sunrise", "duration": SECONDS})
        if made.status_code >= 300:
            print("relay e2e dola: create answered %d" % made.status_code, file=sys.stderr)
            return 1
        job = made.json()["id"]
        end = time.monotonic() + WAIT_S
        while time.monotonic() < end:
            state = (await http.get("/v1/video/generations/%s" % job)).json()
            if state["status"] == "failed":
                print("relay e2e dola: job failed: %s" % state.get("error_code"), file=sys.stderr)
                return 1
            if state["status"] == "done":
                break
            await asyncio.sleep(15)
        else:
            print("relay e2e dola: no result in %d s" % WAIT_S, file=sys.stderr)
            return 1
        video = await http.get("/v1/video/generations/%s/content" % job)
        video.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video.content)
        f.flush()
        seconds = await ffprobe_seconds(f.name)
    ok = measured_length_ok(seconds, SECONDS)
    print("relay e2e dola: %s, %d bytes, %.2f s (wanted %d)" % ("OK" if ok else "LENGTH OFF", len(video.content),
                                                                 seconds, SECONDS))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(os.environ)))
