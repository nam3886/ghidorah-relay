"""Submit for grok: text to image over the Imagine WebSocket, an image edit and a text to video over one streamed
`POST /rest/app-chat/conversations/new`, each held until grok's answer is done (`Completion.HOLD`).

The free steps first, in order: read `quota_info` (enough uses left? still signed in?), upload every input image of
an edit, open the Imagine socket and send its `reset` frame (a text to image). Then a fresh request id, `report_ref`,
`mark_sent` and THE one send: one `conversation.item.create` frame, or one `conversations/new` POST. The answer is read
until it is settled; the conversation id (and a video's ids) are reported the moment they appear, so a lost answer is
read back by them (results.py) without sending anything.

Money rules (each has a test and a mutation):
- a fresh request id per run, persisted with `report_ref` BEFORE `mark_sent`; the ids grok gives are persisted as soon
  as they are read, before reading on;
- `mark_sent` runs immediately before the single send and nothing sends after it: after `mark_sent` the engine never
  returns `Retry`; whatever is unclear is `Ambiguous` (all three operations reconcile), never a re-send;
- too few uses left in the pool (grok's own count, read before the send) sends nothing: `Retry` with the pool's
  `Balance`; every input image is uploaded and answered with its id before anything is sent;
- `NotTaken` after `mark_sent` only on an exact refusal of the send itself before any answer: a 401 (`code 16`), a 429
  (grok's limit: `code 8`, the out-of-quota send, makes no conversation), a Cloudflare page or a 403 `code 7`; or an
  Imagine limit frame before any job started. A limit moves the job to another account (`account_refused`) up to
  `grok.refusal_max_accounts`, the pool at 0 until `grok.limit_rest_s`;
- at most n images, the first in order; fewer (but at least one) are delivered at the full charge with a timeline
  note; none, moderated or grok's safety words: `Fail` refunded;
- a video with no `rRated` key, or done faster than `grok.decoy_min_s`, is a stock clip: `Fail(grok.decoy_video)`
  refunded, the account's video pool rests `grok.decoy_rest_s`, never re-sent;
- the run up to its verdict is bounded by the submit timeout less `HOLD_MARGIN_S`.

After a `Completed` or a `Fail` the quota is read again (best effort, cut at `QUOTA_READ_S`): the pool's `Balance`,
and the uses spent as `Facts(balance_delta)`. A run that read an answer and is not `Completed` keeps its redacted
summary as evidence. Nothing logs or keeps a cookie, the user id or an answer's URL path.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from relay_core.providers import (
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Facts,
    Fail,
    NotTaken,
    Output,
    ProxyBad,
    Rest,
    Retry,
    SubmitResult,
    Success,
)
from relay_core.refusals import account_refused
from relay_grok import errors
from relay_grok.constants import (
    EDIT_MODEL,
    HOLD_MARGIN_S,
    PROXY_REST_S,
    QUOTA_READ_S,
    QUOTA_WINDOW,
    STATE_CONVERSATION,
    STATE_INPUTS,
    STATE_OP,
    STATE_REQUEST,
    STATE_SENT_AT,
    STATE_SENT_TEXT,
    VIDEO_DURATION_S,
    VIDEO_KIND,
    VIDEO_MODE_TAIL,
    VIDEO_MODEL,
    VIDEO_RESOLUTION,
)
from relay_grok.engine import wire
from relay_grok.engine.check import balances, doubt_effects
from relay_grok.engine.web import NetError, Refused, SignedOut, Web, real_session

log = logging.getLogger("relay_grok.submit")

# The platform's pool codes (the platform owns them; the engine only names them).
POOL_LOGGED_OUT = "pool.account_logged_out"
POOL_PROXY_FAILED = "pool.proxy_failed"
POOL_OUT_OF_POINTS = "pool.account_out_of_points"

EVIDENCE_WAIT_S = 5.0
CLOSE_WAIT_S = 2.0        # the socket's close after the verdict, so a hung close cannot eat the hold margin
OPS = {"text_to_image": "t2i", "image_to_image": "edit", "text_to_video": "t2v"}
_TIMEOUT = {"t2i": "grok.image_submit_timeout_s", "edit": "grok.edit_submit_timeout_s",
            "t2v": "grok.video_submit_timeout_s"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

Verdict = Literal["done", "refused", "empty", "limit", "decoy", "unknown"]


# ---- pure verdicts -----------------------------------------------------------------------------------------------
def judge_imagine(a: wire.ImagineAnswer) -> Verdict:
    """An Imagine answer so far. Settled (every started job completed): `done` with a deliverable final, else
    `refused` (moderated, or the safety words) or `empty`. Not settled: `limit` only for a limit frame before any job
    started, `refused` for the safety words with nothing to deliver, else `unknown` (read on)."""
    if a.jobs and set(a.jobs) <= a.completed:
        if a.deliverable():
            return "done"
        return "refused" if a.moderated or a.safety else "empty"
    if a.limit and not a.jobs:
        return "limit"
    if a.safety and not a.deliverable():
        return "refused"
    return "unknown"


def judge_media(a: wire.MediaAnswer, kind: str, decoy_min_s: float) -> Verdict:
    """An edit's or a video's NDJSON answer. Moderated or the safety words: `refused`. A final at 100: `done`, but a
    video without the `rRated` key (its value does not matter) or done faster than `decoy_min_s` after the send is a
    `decoy`; a final that is not a relative assets path is `unknown`. Ended with no final: `empty`. Else `unknown`."""
    if a.moderated or a.safety:
        return "refused"
    if a.final_url is not None:
        if wire.asset_url(a.final_url) is None:
            return "unknown"
        if kind == "video" and (not a.rrated_present or a.progress_at.get(100, 0.0) < decoy_min_s):
            return "decoy"
        return "done"
    return "empty" if a.ended else "unknown"


def reset_frame(now_ms: int) -> dict:
    """The Imagine `reset` (it makes nothing and spends nothing)."""
    return {"type": "conversation.item.create", "timestamp": now_ms,
            "item": {"type": "message", "content": [{"type": "reset"}]}}


def item_create(request_id: str, text: str, ratio: str, n: int, now_ms: int) -> dict:
    """THE generating frame of a text to image (F3, as the page sends it): n images, not pro, not nsfw."""
    return {"type": "conversation.item.create", "timestamp": now_ms, "item": {"type": "message", "content": [{
        "requestId": request_id, "text": text, "type": "input_text",
        "properties": {"section_count": 0, "is_kids_mode": False, "enable_nsfw": False, "skip_upsampler": False,
                       "enable_side_by_side": True, "is_initial": False, "aspect_ratio": ratio, "enable_pro": False,
                       "num_generations": n}}]}}


def edit_body(text: str, inputs: list[str]) -> dict:
    """THE POST of an image edit (F5)."""
    return {"modelName": EDIT_MODEL, "message": text, "enableImageStreaming": True, "enableSideBySide": True,
            "sendFinalMetadata": True, "mediaGenInput": {"imageToImage": {"prompt": text, "inputAssets": inputs}}}


def video_body(text: str, ratio: str) -> dict:
    """THE POST of a text to video (F6): 6 s, 720p, the page's custom mode appended to the message."""
    return {"modelName": VIDEO_MODEL, "message": text + VIDEO_MODE_TAIL, "kind": VIDEO_KIND,
            "mediaGenInput": {"textToVideo": {"prompt": text, "aspectRatio": ratio, "duration": VIDEO_DURATION_S,
                                              "resolutionName": VIDEO_RESOLUTION}}}


_store = None


async def read_input_media(ref) -> bytes:
    """The job's input image from the object store, the store made on first use (a job from text needs none)."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


# ---- the run -----------------------------------------------------------------------------------------------------
@dataclass
class Deps:
    """What a run needs from outside; every field has the real default."""
    make_session: Callable = real_session
    read_media: Callable[..., Awaitable[bytes]] = read_input_media
    statsig: Callable | None = None
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    uuid4: Callable[[], str] = lambda: str(uuid.uuid4())
    hold_margin_s: float = HOLD_MARGIN_S


class _CoreVerdict(Exception):
    """An exception from a `ctx` gate (`report_ref`, `mark_sent`): the core's own verdict, passed on unchanged."""

    def __init__(self, cause: Exception):
        super().__init__(type(cause).__name__)
        self.cause = cause


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


class _Run:
    def __init__(self, ctx, job, settings, deps: Deps):
        self.op = OPS.get(job.operation)
        if self.op is None:
            raise ValueError("a grok job is t2i, edit or t2v, not %r" % (job.operation,))
        if not job.sent_text:
            raise ValueError("the job has no sent_text: the core composes it")
        self.ctx, self.job, self.settings, self.deps = ctx, job, settings, deps
        self.pool = "video" if self.op == "t2v" else "image"
        self.n = int(job.params.get("n") or 1) if self.op == "t2i" else 1
        self.text = job.sent_text
        self.started = deps.clock()
        self.deadline = self.started + settings.get(_TIMEOUT[self.op]) - deps.hold_margin_s
        self.sent = False                   # True from the moment `mark_sent` returned: grok may have the order
        self.before: int | None = None      # the pool's uses left before the send
        self.state: dict = {}
        self.items: list = []               # the frames or lines read, for the evidence
        self.outcome: SubmitResult | None = None

    # ---- small steps ----
    async def _say(self, text: str, stage: str = "submit") -> None:
        try:
            await self.ctx.event(text, stage, "engine")
        except Exception as exc:      # noqa: BLE001 - diagnostics never fail the order
            log.warning("event %s not recorded: %s", stage, type(exc).__name__)

    async def _core(self, fn) -> None:
        try:
            await fn()
        except Exception as exc:
            raise _CoreVerdict(exc) from exc

    def _later(self, key: str) -> datetime:
        return self.deps.now() + timedelta(seconds=int(self.settings.get(key)))

    async def _keep(self, result: SubmitResult, line: str | None = None, stage: str = "submit") -> SubmitResult:
        """The verdict, kept BEFORE its timeline line is written: a deadline hit during the write keeps it."""
        self.outcome = result
        if line is not None:
            await self._say(line, stage)
        return result

    async def _unknown(self, why: str) -> SubmitResult:
        """After `mark_sent`: the outcome is unknown (the reconcile reads it back), never a re-send."""
        return await self._keep(_result(Ambiguous(why[:500])), why, "submit_error")

    def _limit(self, why: str) -> SubmitResult:
        """grok's limit on this account before anything was made: the job goes to another account (no attempt)
        until `grok.refusal_max_accounts` refused it, then fails refunded (`grok.limit`); the pool reads 0 until its
        refill."""
        outcome = account_refused(self.job, self.ctx.account.id, self.settings, not_taken=POOL_OUT_OF_POINTS,
                                  final=errors.LIMIT, detail=why)
        return _result(outcome, Balance(self.pool, 0, self._later("grok.limit_rest_s"), window=QUOTA_WINDOW))

    def _refusal(self, kind: str) -> SubmitResult:
        """An exact refusal of a call (`Refused.kind`), before the send or of the send itself before any answer."""
        if kind == "cloudflare":
            return _result(NotTaken(errors.CLOUDFLARE), Cooldown("grok.com answered a Cloudflare page",
                                                                 self._later("grok.cf_rest_s"), recheck=True))
        if kind == "antibot":
            return _result(NotTaken(errors.ANTIBOT), Cooldown("grok.com's anti-bot rules refused (code 7)",
                                                              self._later("grok.antibot_rest_s")))
        if kind == "limit":
            if self.sent:
                return self._limit("grok answered its limit to the send before anything was made")
            return _result(Retry(POOL_OUT_OF_POINTS), Balance(self.pool, 0, self._later("grok.limit_rest_s"),
                                                              window=QUOTA_WINDOW))
        if kind in ("signed_out", "blocked"):
            effects = doubt_effects("blocked" if kind == "blocked" else "signed out", self.settings, self.deps.now())
            if not self.sent:
                return _result(Retry(POOL_LOGGED_OUT), *effects)
            if kind == "signed_out":
                return _result(NotTaken(POOL_LOGGED_OUT), *effects)
            return _result(Ambiguous("grok refused the send: the user is blocked"), *effects)
        if kind == "input" and not self.sent:
            return _result(Fail(errors.INPUT_UNUSABLE, "grok.com refused an input image at upload"))
        if kind == "bad_body":
            return _result(Fail(errors.HTTP_ERROR, "grok.com refused the request body (400 code 3)"))
        if self.sent:
            return _result(Ambiguous("grok refused the send (%s)" % kind))
        return _result(Retry(errors.HTTP_ERROR))

    def _left(self, q) -> tuple[int | None, datetime | None]:
        return (q.video_left, q.video_refill) if self.pool == "video" else (q.image_left, q.image_refill)

    async def _mark(self, request_id: str, **more) -> None:
        """Persist the run's ids, then `mark_sent`: the very next step is THE send."""
        self.state = {STATE_OP: self.op, STATE_REQUEST: request_id,
                      STATE_SENT_AT: int(self.deps.now().timestamp()), **more}
        await self._core(lambda: self.ctx.report_ref(request_id, dict(self.state)))
        await self._core(self.ctx.mark_sent)
        self.sent = True

    async def _ids(self, ids: dict[str, str]) -> None:
        """Ids grok just gave: persisted before reading on (the ref is the conversation, else the request)."""
        self.state.update(ids)
        ref = self.state.get(STATE_CONVERSATION) or self.state[STATE_REQUEST]
        await self._core(lambda: self.ctx.report_ref(ref, dict(self.state)))

    # ---- the run ----
    async def bounded(self, web: Web) -> SubmitResult:
        """Everything up to the verdict runs before the deadline, whatever hangs. Past it: nothing sent is a `Retry`,
        a sent order `Ambiguous`, never a re-send."""
        bound = asyncio.timeout(max(0.0, self.deadline - self.deps.clock()))
        try:
            async with bound:
                return await self._steps(web)
        except TimeoutError:
            if not bound.expired():
                raise
            if self.outcome is not None:
                return self.outcome
        why = "no verdict within %d s (the submit timeout less %d s)" % (
            self.deadline - self.started, self.deps.hold_margin_s)
        if self.sent:
            return await self._unknown(why)
        await self._say(why)
        return _result(Retry(errors.HTTP_ERROR))

    async def _steps(self, web: Web) -> SubmitResult:
        left, refill = self._left(await web.quota())
        if left is None:
            raise Refused("other", 200)       # grok named no such pool: a step that failed, nothing sent
        self.before = left
        if left < self.n:
            await self._say("grok says %d %s use(s) left, the job needs %d: nothing sent" % (left, self.pool, self.n))
            return _result(Retry(POOL_OUT_OF_POINTS),
                           Balance(self.pool, left, refill or self._later("grok.limit_rest_s"), window=QUOTA_WINDOW))
        if self.op == "t2i":
            return await self._imagine(web)
        if self.op == "t2v":
            return await self._media(web, None)
        # every input image, in the client's order, answered with its file id before anything is sent
        refs = await self.job.inputs()
        if not refs:
            raise ValueError("an edit has no input image")
        ids = []
        for i, ref in enumerate(refs):
            ids.append(await web.upload(await self.deps.read_media(ref), ref.mime,
                                        "image%d%s" % (i, _EXT.get(ref.mime, ".jpg"))))
        if len(ids) != len(refs):
            return _result(Retry(errors.HTTP_ERROR))
        return await self._media(web, ids)

    # ---- text to image ----
    async def _imagine(self, web: Web) -> SubmitResult:
        sock = await web.socket()
        try:
            await sock.send_json(reset_frame(int(self.deps.now().timestamp() * 1000)))
            request_id = "img_" + self.deps.uuid4().replace("-", "")
            reader = wire.ImagineReader(request_id)
            frame = item_create(request_id, self.text, self.job.params.get("ratio") or "1:1", self.n,
                                int(self.deps.now().timestamp() * 1000))
            await self._mark(request_id, **{STATE_SENT_TEXT: self.text})
            await sock.send_json(frame)
            while True:
                raw = await sock.recv_text()
                self.items.append(raw)
                reader.feed(raw)
                a = reader.answer()
                if a.conversation_id is not None and STATE_CONVERSATION not in self.state:
                    await self._ids({STATE_CONVERSATION: a.conversation_id})
                verdict = judge_imagine(a)
                if verdict != "unknown" or a.error_text is not None:
                    break
            return await self._imagine_verdict(a, verdict)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(sock.close(), CLOSE_WAIT_S)

    async def _imagine_verdict(self, a: wire.ImagineAnswer, verdict: Verdict) -> SubmitResult:
        if verdict == "done":
            finals = a.deliverable()
            outputs = tuple(Output(f.url, "image/jpeg", i, f.job_id) for i, f in enumerate(finals[:self.n]))
            note = wire.made_note(len(finals), self.n, "image")
            if note is not None:
                log.warning("grok job %s: %s", self.job.id, note)
            return await self._keep(_result(Completed(outputs), Success()), note)
        if verdict == "refused":
            return await self._keep(_result(Fail(errors.CONTENT_REFUSED, "grok's moderation refused every image")))
        if verdict == "empty":
            return await self._keep(_result(Fail(errors.NO_IMAGE, "grok finished with no image and no reason")))
        if verdict == "limit":
            return await self._keep(self._limit("grok answered its limit before any image started"),
                                    "grok's limit on this account; the job goes to another account")
        return await self._unknown("grok's Imagine answer is not settled: %d job(s), %d completed, error %s" % (
            len(a.jobs), len(a.completed), a.error_text))

    # ---- an edit or a video ----
    async def _media(self, web: Web, inputs: list[str] | None) -> SubmitResult:
        kind = "video" if self.op == "t2v" else "image"
        ratio = self.job.params.get("ratio") or "16:9"
        body = video_body(self.text, ratio) if kind == "video" else edit_body(self.text, inputs)
        more = {STATE_SENT_TEXT: body["message"]}
        if inputs is not None:
            more[STATE_INPUTS] = list(inputs)
        await self._mark(self.deps.uuid4(), **more)
        reader = wire.NdjsonReader(kind, self.deps.clock)
        try:
            async with contextlib.aclosing(web.send_new(body)) as lines:
                async for line in lines:
                    self.items.append(line)
                    reader.feed(line)
                    new = reader.ids_new()
                    if new:
                        await self._ids(new)
                    if reader.answer().ended:
                        break
        except Refused as refused:
            # send_new raises it only for a non-200 answer to the POST itself, before any line
            result = self._refusal(refused.kind)
            return await self._keep(result, "grok refused the send (%s, %d)" % (refused.kind, refused.status))
        return await self._media_verdict(reader.answer(), kind)

    async def _media_verdict(self, a: wire.MediaAnswer, kind: str) -> SubmitResult:
        verdict = judge_media(a, kind, self.settings.get("grok.decoy_min_s"))
        if verdict == "done":
            mime = "video/mp4" if kind == "video" else "image/jpeg"
            upstream = a.video_id if kind == "video" else a.asset_id
            return await self._keep(_result(Completed((Output(wire.asset_url(a.final_url), mime, 0, upstream),)),
                                            Success()))
        if verdict == "decoy":
            why = "grok answered a stock clip (rRated %s, 100 %% at %.1f s)" % (
                "present" if a.rrated_present else "absent", a.progress_at.get(100, 0.0))
            rest = Rest("video", self._later("grok.decoy_rest_s"), "decoy")
            return await self._keep(_result(Fail(errors.DECOY_VIDEO, why), rest), why)
        if verdict == "refused":
            return await self._keep(_result(Fail(errors.CONTENT_REFUSED, "grok's moderation refused the %s" % kind)))
        if verdict == "empty":
            code = errors.NO_VIDEO if kind == "video" else errors.NO_IMAGE
            return await self._keep(_result(Fail(code, "grok finished with no %s and no reason" % kind)))
        return await self._unknown("grok's answer is not settled: progress %d, ended %s, error %s" % (
            a.progress, a.ended, a.error_text))

    # ---- after ----
    async def failed(self, exc: BaseException) -> SubmitResult:
        """The run raised. Before `mark_sent`: a retry (an exact refusal maps by its kind). After it: unknown. Only
        the exception's type is kept: a curl text may hold the proxy's login."""
        cause = type(exc).__name__
        if self.sent:
            return await self._unknown("the run failed after the send: %s" % cause)
        if isinstance(exc, SignedOut):
            return self._refusal("signed_out")
        if isinstance(exc, NetError) and exc.proxy:
            return _result(Retry(POOL_PROXY_FAILED), ProxyBad("grok.com could not be reached through the proxy"),
                           Cooldown("proxy failed", self.deps.now() + timedelta(seconds=PROXY_REST_S)))
        if isinstance(exc, Refused):
            return self._refusal(exc.kind)
        await self._say("an HTTP step to grok.com failed before the send: %s" % cause)
        return _result(Retry(errors.HTTP_ERROR))

    async def after(self, web: Web, result: SubmitResult) -> SubmitResult:
        """The quota read after a `Completed` or a `Fail`: the pool's `Balance`, and on `Completed` the uses spent as
        `Facts`. Best effort: it never changes the result."""
        if not isinstance(result.job, Completed | Fail) or self.before is None:
            return result
        try:
            async with asyncio.timeout(QUOTA_READ_S):
                q = await web.quota()
        except Exception as exc:      # noqa: BLE001 - the read after the job never fails it
            log.info("grok quota read after job %s skipped: %s", self.job.id, type(exc).__name__)
            return result
        mine = tuple(b for b in balances(q, self.deps.now(), self.settings) if b.pool == self.pool)
        job = result.job
        if isinstance(job, Completed) and mine and self.before - mine[0].left_n > 0:
            job = replace(job, facts=Facts(upstream_price=self.before - mine[0].left_n, cost_source="balance_delta"))
        return replace(result, job=job, account=(*result.account, *mine))

    async def evidence(self) -> None:
        """The redacted answer summary, diagnostics only: cut at `EVIDENCE_WAIT_S`, a failure logged and skipped."""
        try:
            async with asyncio.timeout(EVIDENCE_WAIT_S):
                await self.ctx.evidence("grok-answer.json", wire.summary(self.items), "application/json")
        except Exception as exc:      # noqa: BLE001 - diagnostics never fail the order
            log.warning("evidence not kept: %s", type(exc).__name__)


async def submit(ctx, job, settings, *, deps: Deps | None = None) -> SubmitResult:
    """Send the job's order once and return what happened. Exceptions the core must see (a refused `report_ref` or
    `mark_sent`) propagate unchanged; every other failure becomes a result."""
    deps = deps or Deps()
    run = _Run(ctx, job, settings, deps)
    try:
        async with Web(ctx.account, settings, ctx, make_session=deps.make_session, statsig=deps.statsig,
                       clock=deps.clock) as web:
            try:
                outcome = await run.bounded(web)
            except _CoreVerdict:
                raise
            except Exception as exc:      # noqa: BLE001 - mapped by `failed`; cancellation is not an Exception
                log.warning("grok submit failed (sent=%s): %s", run.sent, type(exc).__name__)
                outcome = await run.failed(exc)
            outcome = await run.after(web, outcome)
    except _CoreVerdict as verdict:
        raise verdict.cause from None
    if not isinstance(outcome.job, Completed) and run.items:
        await run.evidence()
    return outcome
