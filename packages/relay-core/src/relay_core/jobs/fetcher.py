"""The fetcher (contracts 7.1 "Fetching", 9; invariants I11, I21, I23).

`fetching` jobs are claimed like polls (a token and a 900 s visibility), with no account lease. Each output goes
`engine.fetch` -> a temporary file -> `ffprobe` -> the object store under `<job>/output/<n>.<ext>` (size verified by
`storage`). All of that I/O runs with no database connection held. Only when every output is stored does one
transaction (`store.apply_fetched`) write the media rows and move the job to `done`; a job is never `done` without
its files (I11), and a result whose token is no longer current is dropped.

A download that fails (an error, no bytes, a store error, out of time) is retried every `retry_s` until the render
deadline, then fails `render.download_failed`; a file that does not probe fails `render.file_unreadable` at once.
Both fail through the one failing transaction, so the key and the account are refunded with the status (I21).
"""

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from relay_core import storage
from relay_core.jobs import store
from relay_core.jobs.apply import apply_fetch_failed, apply_fetched
from relay_core.jobs.claim import Visible, claim_visible
from relay_core.jobs.poller import ERROR_RETRY_S, JobLoop, LoadAccount, ReadCtx
from relay_core.jobs.recheck import StoredOutput
from relay_core.providers import Output
from relay_core.redact import redact_log

log = logging.getLogger(__name__)

FETCH_VISIBILITY_S = 900.0        # contracts 7.1
FETCH_BOUND_S = 600.0
PROBE_BOUND_S = 30.0
DOWNLOAD_FAILED = "render.download_failed"
FILE_UNREADABLE = "render.file_unreadable"


class FetchFailure(Exception):
    """One output could not be fetched; `code` is the registry code the job fails or retries with."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Probed:
    width: int | None
    height: int | None
    duration_s: float | None


async def probe(path: str, mime: str, *, bound_s: float = PROBE_BOUND_S) -> Probed:
    """Dimensions (and the length of a video) from `ffprobe`. Raises FetchFailure(FILE_UNREADABLE) when the file is
    not what its mime says: no video stream or no length for a video, no picture for an image."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), bound_s)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise
        info = json.loads(out or b"{}") if proc.returncode == 0 else {}
    except (OSError, ValueError, TimeoutError) as e:
        raise FetchFailure(FILE_UNREADABLE, "the file could not be probed: %s" % type(e).__name__) from None
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None or not video.get("width") or not video.get("height"):
        raise FetchFailure(FILE_UNREADABLE, "the file has no picture")
    duration = None
    if mime.startswith("video/"):
        try:
            duration = float(info.get("format", {}).get("duration") or video.get("duration"))
        except (TypeError, ValueError):
            raise FetchFailure(FILE_UNREADABLE, "the video has no length") from None
        if duration <= 0:
            raise FetchFailure(FILE_UNREADABLE, "the video has no length")
    return Probed(int(video["width"]), int(video["height"]), duration)


def outputs_of(state: Mapping[str, Any]) -> list[Output]:
    """The outputs a result left in `provider_state.outputs`, in index order."""
    rows = sorted(state.get("outputs") or [], key=lambda d: d["idx"])
    return [Output(d["ref"], d["mime"], d["idx"], d.get("upstream_id"), d.get("meta") or {}) for d in rows]


async def store_output(engine, objects: storage.ObjectStore, ctx, job_id, i: int, out: Output) -> StoredOutput:
    """One output: `engine.fetch` streamed to a temporary file, size checked, probed (`FILE_UNREADABLE` when it is not
    what its mime says), then `objects.put_file` under `<job>/output/<i>.<ext>`. Shared by the fetcher and Recover so
    both refuse the same bad files. Raises FetchFailure; holds no database connection."""
    key = storage.media_key(str(job_id), "output", i, out.mime)
    with tempfile.TemporaryDirectory(prefix="relay-fetch-") as tmp:
        path = os.path.join(tmp, "output")
        size = 0
        sink = await asyncio.to_thread(open, path, "wb")
        try:
            async with contextlib.aclosing(engine.fetch(ctx, out)) as chunks:
                async for chunk in chunks:
                    size += len(chunk)
                    await asyncio.to_thread(sink.write, chunk)
        except Exception as e:
            raise FetchFailure(DOWNLOAD_FAILED, "download failed: %s" % type(e).__name__) from e
        finally:
            await asyncio.to_thread(sink.close)
        if size == 0:
            raise FetchFailure(DOWNLOAD_FAILED, "the download was empty")
        probed = await probe(path, out.mime)
        try:
            info = await objects.put_file(key, path, out.mime)
        except (storage.StorageError, OSError) as e:
            raise FetchFailure(DOWNLOAD_FAILED, "the result could not be stored: %s" % e) from e
    return StoredOutput(key, out.mime, info.size, out.upstream_id, probed.width, probed.height, probed.duration_s)


class Fetcher(JobLoop):
    def __init__(self, db, spec, engine, settings, *, owner: str, load_account: LoadAccount,
                 objects: storage.ObjectStore, visibility_s: float = FETCH_VISIBILITY_S,
                 bound_s: float = FETCH_BOUND_S, retry_s: float = ERROR_RETRY_S, **kw):
        super().__init__(db, spec, engine, settings, owner=owner, load_account=load_account, **kw)
        self.objects, self.visibility_s, self.bound_s, self.retry_s = objects, visibility_s, bound_s, retry_s

    async def _claim(self, n: int) -> list[Visible]:
        return await claim_visible(self.db, provider=self.spec.name, status="fetching",
                                   visibility_s=self.visibility_s, limit=n)

    async def _store_one(self, ctx: ReadCtx, job_id, i: int, out: Output) -> StoredOutput:
        return await store_output(self.engine, self.objects, ctx, job_id, i, out)

    async def _fetch_all(self, c: Visible) -> list[StoredOutput]:
        view = await store.job_view(self.db, c.job_id)
        outs = outputs_of(view.provider_state)
        if not outs:
            raise FetchFailure(FILE_UNREADABLE, "the job has no outputs to fetch")
        try:
            async with asyncio.timeout(self.bound_s):
                ctx = ReadCtx(self.db, c.job_id, account=await self.load_account(c.account_id),
                              settings=self.settings, provider=self.spec.name, evidence_sink=self.evidence_sink)
                rows: list[StoredOutput] = []
                for i, out in enumerate(outs):
                    rows.append(await self._store_one(ctx, c.job_id, i, out))
                return rows
        except TimeoutError:
            raise FetchFailure(DOWNLOAD_FAILED, "the download ran past %.0f s" % self.bound_s) from None

    async def _work(self, c: Visible) -> None:
        try:
            rows = await self._fetch_all(c)
        except FetchFailure as e:
            await self._failed(c, e)
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("fetch of job %s broke", c.job_id)
            await self._failed(c, FetchFailure(DOWNLOAD_FAILED, "fetch error: %s" % type(e).__name__))
            return
        if not await self._shielded(apply_fetched(self.db, c.job_id, c.token, rows)):
            log.info("fetch result of job %s dropped: its token is no longer current", c.job_id)

    async def _failed(self, c: Visible, e: FetchFailure) -> None:
        log.warning("fetch of job %s failed: %s %s", c.job_id, e.code, redact_log(e.detail))
        await self._shielded(apply_fetch_failed(
            self.db, c.job_id, c.token, code=e.code, detail=e.detail, retry_in_s=self.retry_s,
            deadline_s=self.settings.get(self.spec.name + ".render_deadline_s"), pools=self._pools()))
