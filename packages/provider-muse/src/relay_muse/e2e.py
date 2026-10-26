"""muse end-to-end run against the live service: `relay e2e --provider muse`.

It makes a temporary API key with a small points wallet, runs the cases named in `RELAY_E2E_CASES` (default: all of
`CASES`) through the running API at once, waits for each, probes every delivered file (ffprobe: its size, and the
length of a video), and disables the key afterwards. The image-to-video case animates the first file of the first image
case. Before and after, it queues an account check of every muse account and prints the weekly Usage each one read.
One line per job: id, account, status, seconds from create to done, the files. It SPENDS muse quota and so refuses
unless `RELAY_E2E_SPEND=yes`. Environment: `DATABASE_URL` (a role that may create keys and queue commands),
`RELAY_E2E_URL` (the API, default http://127.0.0.1:8100). Never prints a key or a cookie.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping

import httpx

from relay_core import keys
from relay_core.admin.routes_common import enqueue
from relay_core.db import open_pool, tx
from relay_muse.catalog import IMAGE_MODEL, VIDEO_MODEL

WAIT_S = 1200            # a video took 37-60 s live; jobs of one account run one at a time
POLL_S = 5
CHECK_WAIT_S = 180       # an account check reads the session and the Usage
# name -> (kind, body); "i2v" takes its image from the first image case
CASES = {
    "img-n1-1:1": ("image", {"model": IMAGE_MODEL, "prompt": "A red fox sitting in fresh snow at sunrise, photo",
                             "n": 1, "ratio": "1:1"}),
    "img-n1-9:16": ("image", {"model": IMAGE_MODEL, "prompt": "A tall waterfall in a green jungle", "n": 1,
                              "ratio": "9:16"}),
    "img-n2-16:9": ("image", {"model": IMAGE_MODEL, "prompt": "A lighthouse on a rocky coast in a storm", "n": 2,
                              "ratio": "16:9"}),
    "img-n2-3:4": ("image", {"model": IMAGE_MODEL, "prompt": "A cozy reading nook with a window seat", "n": 2,
                             "ratio": "3:4"}),
    "t2v-5s": ("video", {"model": VIDEO_MODEL, "prompt": "Ocean waves crashing on black volcanic rocks at golden "
                         "hour", "duration": 5, "ratio": "16:9"}),
    "t2v-10s": ("video", {"model": VIDEO_MODEL, "prompt": "A hot air balloon rising over a valley at dawn",
                          "duration": 10, "ratio": "9:16"}),
    "i2v-5s": ("video", {"model": VIDEO_MODEL, "prompt": "The animal slowly turns its head toward the camera",
                         "duration": 5}),
}
WALLET = 40


def probe(data: bytes) -> str:
    """`WxH` (and the length of a video) of a file, or why it could not be read."""
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                                  f.name], capture_output=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            return "unprobed (%s)" % type(e).__name__
    info = json.loads(out or b"{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    length = (info.get("format") or {}).get("duration")
    return "%sx%s%s" % (video.get("width"), video.get("height"), " %.3f s" % float(length) if length else "")


async def usage(pool) -> dict[str, tuple]:
    """Check every enabled muse account now and return label -> (usage %, quota ok, resets at) as they read it."""
    async with tx(pool) as conn:
        accounts = await (await conn.execute(
            "SELECT id, label FROM accounts WHERE provider = 'muse' AND state <> 'disabled' ORDER BY label")).fetchall()
        ids = [await enqueue(conn, "muse", "check", account_id=a, args={"account_id": str(a)}) for a, _ in accounts]
    end = time.monotonic() + CHECK_WAIT_S
    while time.monotonic() < end:
        async with tx(pool) as conn:
            left = (await (await conn.execute(
                "SELECT count(*) FROM commands WHERE id = ANY(%s) AND status IN ('queued', 'running')",
                (ids,))).fetchone())[0]
        if not left:
            break
        await asyncio.sleep(POLL_S)
    async with tx(pool) as conn:
        rows = await (await conn.execute(
            "SELECT label, provider_state->'usage_percent', provider_state->'quota_ok', "
            "provider_state->>'usage_resets_at' FROM accounts WHERE id = ANY(%s) ORDER BY label",
            ([a for a, _ in accounts],))).fetchall()
    return {label: (pct, ok, resets) for label, pct, ok, resets in rows}


async def run(env: Mapping[str, str]) -> int:
    if env.get("RELAY_E2E_SPEND") != "yes":
        print("relay e2e muse: this run spends muse quota; set RELAY_E2E_SPEND=yes to go on", file=sys.stderr)
        return 2
    dsn, base = env.get("DATABASE_URL", ""), env.get("RELAY_E2E_URL", "http://127.0.0.1:8100")
    if not dsn:
        print("relay e2e muse: DATABASE_URL is not set", file=sys.stderr)
        return 2
    names = [n for n in (env.get("RELAY_E2E_CASES") or ",".join(CASES)).split(",") if n]
    unknown = [n for n in names if n not in CASES]
    if unknown:
        print("relay e2e muse: unknown cases %s (known: %s)" % (unknown, ", ".join(CASES)), file=sys.stderr)
        return 2
    pool = await open_pool(dsn, max_size=4, name="relay-e2e")
    try:
        print("usage before: %s" % await usage(pool))
        async with tx(pool) as conn:
            made = await keys.create_key(conn, label="e2e-muse", models=[IMAGE_MODEL, VIDEO_MODEL],
                                         max_unfinished=len(names), declared=["muse"],
                                         wallets={"*": (WALLET, WALLET), "muse": (WALLET, WALLET)})
        try:
            code = await _cases(pool, base, made.raw, names)
        finally:
            async with tx(pool) as conn:
                await keys.set_enabled(conn, made.id, False)       # disabled afterwards, never left open
        print("usage after: %s" % await usage(pool))
        return code
    finally:
        await pool.close()


async def _cases(pool, base: str, raw_key: str, names: list[str]) -> int:
    headers = {"Authorization": "Bearer " + raw_key}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120) as http:
        first_image: asyncio.Future = asyncio.get_running_loop().create_future()

        async def one(name: str) -> bool:
            kind, body = CASES[name]
            body = dict(body)
            if name.startswith("i2v"):
                image = await first_image if any(n.startswith("img") for n in names) else None
                if image is None:
                    print("%s: no image case ran to animate" % name)
                    return False
                body["image"] = "data:image/webp;base64," + base64.b64encode(image).decode()
            path = "/v1/%s/generations" % ("images" if kind == "image" else "video")
            started = time.monotonic()
            made = await http.post(path, json=body)
            if made.status_code >= 300:
                print("%s: create answered %d %s" % (name, made.status_code, made.text[:300]))
                return False
            job = made.json()["id"]
            state: dict = {}
            while time.monotonic() - started < WAIT_S:
                state = (await http.get("%s/%s" % (path, job))).json()
                if state["status"] in ("done", "failed"):
                    break
                await asyncio.sleep(POLL_S)
            took = time.monotonic() - started
            async with tx(pool) as conn:
                row = await (await conn.execute(
                    "SELECT a.label, j.external_ref, extract(epoch FROM j.sent_at - j.created_at), "
                    "extract(epoch FROM j.finished_at - j.sent_at) FROM jobs j LEFT JOIN accounts a ON a.id = "
                    "j.account_id WHERE j.id = %s", (job,))).fetchone()
            files = []
            if state.get("status") == "done":
                count = body.get("n") or 1
                for idx in range(count if kind == "image" else 1):
                    got = await http.get("%s/%s/content/%d" % (path, job, idx))
                    if got.status_code != 200:
                        break
                    files.append("%d B %s" % (len(got.content), probe(got.content)))
                    if kind == "image" and idx == 0 and not first_image.done():
                        first_image.set_result(got.content)
            print("%s: job %s account %s chat %s status %s %s in %.1f s (queued %.1f s, sent to done %.1f s) "
                  "files [%s]" % (name, job, row[0] if row else None, row[1] if row else None, state.get("status"),
                                  state.get("error_code") or "", took, float(row[2] or 0) if row else 0,
                                  float(row[3] or 0) if row else 0, "; ".join(files)), flush=True)
            if kind == "image" and not first_image.done() and name == next(n for n in names if n.startswith("img")):
                first_image.set_result(None)
            return state.get("status") == "done"

        results = await asyncio.gather(*(one(n) for n in names))
    print("relay e2e muse: %d of %d cases done" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run(os.environ)))
