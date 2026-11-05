"""Submit for `meta-image` (text to image, or an edit from one to three reference images) and `meta-video` (text to
video, or one image animated) over plain HTTP, held in the session until meta's answer is done.

One chat per job: read the input images (each shrunk by ffmpeg when meta would not take it as it is), take the
account's token (cached per process, the page read again when it is older than `meta.token_reread_s` and the proxy
IP's page gate allows it), upload the inputs to rupload, open the gateway WebSocket, open the stream on a fresh
conversation uuid (EstabStream), then `report_ref`, `mark_sent` and ONE `0x0d` data frame, and read the answer's frames
until its final chunk. The results are the final fbcdn links of the answer (`wire.Reader`); the fetcher downloads
them (fetch.py).

The words sent: an image job from text alone leads with the count it paid for (`count_lead`, n = 1 too; none for a job
queued before n), a video from text alone with `VIDEO_FROM_TEXT` (meta has no video mode), then the core's `sent_text`
on one line, then `RATIO_TAIL` unless the pair is in `RATIO_UNTYPED` (meta reads the ratio from the words).

Money rules (each has a test and a mutation):
- a fresh conversation uuid per run, persisted with `report_ref` BEFORE `mark_sent`, so an answer lost after the send
  is found again (reconcile) without sending anything;
- every input is uploaded and answered with a media id before `mark_sent`: never the text without its images;
- `mark_sent` runs immediately before the single data frame and nothing sends after it: after `mark_sent` the engine
  never returns `Retry`, whatever is unclear is `Ambiguous` (unknown), never a re-send. The EstabStream may be
  repeated (once, after a page read, on a new socket); the send never is;
- a page that cannot be had (the IP gate closed, a Vercel page) with no usable token sends nothing: `Retry` with no
  attempt and no charge (`meta.page_wait`, `meta.checkpoint`) and an account `Cooldown` with a recheck, never a
  strike;
- meta's safety refusal fails at once, refunded (`refusal_outcome`); meta's limit on the account in an answer with
  no media moves the job to another account (`account_refused`) up to `meta.refusal_max_accounts`, the pool empty for
  `meta.limit_rest_s`; a done answer with no media and no reason fails refunded;
- an answer is complete with n finals of the job's kind (previews left then are a draft, never delivered); fewer
  finals with previews left, or only the other kind, is `Ambiguous`; at most n results, the first in order; fewer
  finals with no preview left are delivered at the full charge (any output is a full charge), and more or fewer
  than n is noted on the timeline and in the log (`wire.made_note`);
- the run up to its verdict is bounded by the submit timeout less `HOLD_MARGIN_S`.

Evidence: a run that read frames and is not `Completed` keeps the redacted frame summary (`wire.summary`), cut at
`EVIDENCE_WAIT_S`, never failing the run. Nothing logs or keeps the token, the viewerId or the WebSocket URL.
"""

import asyncio
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from relay_core.providers import (
    REFUSED_BY,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Fail,
    NotTaken,
    ProxyBad,
    Retry,
    SubmitResult,
    Success,
)
from relay_core.redact import redact_text
from relay_core.refusals import account_refused
from relay_meta import errors
from relay_meta.constants import (
    ESTAB_WAIT_S,
    FAULT_REST_S,
    LIMIT_KINDS,
    LIMIT_PHRASES,
    POLICY_CODES,
    POLICY_KINDS,
    RATIO_TAIL,
    RATIO_UNTYPED,
)
from relay_meta.engine import wire
from relay_meta.engine.shrink import shrink as ffmpeg_shrink
from relay_meta.engine.web import (
    GateClosed,
    NetError,
    Sessions,
    UploadFailed,
    Web,
    quiet_close,
    real_session,
    signed_out_effects,
)

log = logging.getLogger("relay_meta.submit")

# The platform's pool codes (the platform owns them; the engine only names them).
POOL_LOGGED_OUT = "pool.account_logged_out"
POOL_PROXY_FAILED = "pool.proxy_failed"
POOL_OUT_OF_POINTS = "pool.account_out_of_points"

HOLD_MARGIN_S = 15        # the run's verdict is out this long before the core's submit timeout: the write-back (5 s)
#                           and the evidence (5 s) fit after it
EVIDENCE_WAIT_S = 5.0
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
KINDS = ("image", "video")
# the job's operation (the plan's) -> its kind; the two from an image upload input images
OPERATIONS = {"text_to_image": "image", "image_to_image": "image", "text_to_video": "video", "image_to_video": "video"}
FROM_IMAGE = ("image_to_image", "image_to_video")
# sent before the core's text of a video from text alone: without it meta draws an image (FINDINGS.md)
VIDEO_FROM_TEXT = "Create a short video: "
# sent before the core's text of an image job from text alone, n = 1 included: "Create 4 images of ..." gave 4; the job
# delivers at most n, whatever meta draws. A job queued before n existed gets no count and keeps every image.
IMAGES_FROM_TEXT = "Create %d images: "
IMAGE_FROM_TEXT = "Create 1 image: "

Verdict = Literal["done", "policy", "limit", "empty", "unknown"]


# ---- pure helpers ------------------------------------------------------------------------------------------------
def count_lead(n: int | None) -> str:
    """What an image job from text sends before its text: the count it paid for; nothing for a job from before n."""
    if n is None:
        return ""
    return IMAGE_FROM_TEXT if n == 1 else IMAGES_FROM_TEXT % n


def is_limit(text: str) -> bool:
    """Does an answer with no media read like meta's limit or quota refusal (`LIMIT_PHRASES`, not measured)?"""
    t = " ".join(text.lower().replace("’", "'").split())
    return any(p in t for p in LIMIT_PHRASES)


def limit_balance(settings, now: datetime, kind: str) -> Balance:
    """meta said the account makes no more of this kind for now: the pool's balance is 0 for `meta.limit_rest_s`
    (meta names no reset time). The core rests the pool until then."""
    return Balance(kind, 0, now + timedelta(seconds=int(settings.get("meta.limit_rest_s"))))


def judge(answer: wire.Answer, kind: str, n: int | None = None) -> Verdict:
    """What meta's answer means for the job. `n`: the images asked (None: a video or a job from before n).
    - not done, or no final with previews or the other kind left: `unknown`;
    - finals: `done` with n of them (previews left then are a draft) or with no preview left, else `unknown`;
    - no media at all: `policy` (meta's safety check), `limit` (a limit kind, or limit words with no structured
      error), else `empty`."""
    if not answer.done:
        return "unknown"
    if answer.finals:
        return "done" if answer.previews == 0 or (n is not None and len(answer.finals) >= n) else "unknown"
    if answer.previews or answer.other:
        return "unknown"
    error = answer.error or {}
    if error.get("failure_kind") in POLICY_KINDS or error.get("error_code") in POLICY_CODES:
        return "policy"
    if error.get("failure_kind") in LIMIT_KINDS or (answer.error is None and is_limit(answer.text)):
        return "limit"
    return "empty"


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def refusal_outcome(job, ctx, settings, kind: str) -> SubmitResult:
    """meta's safety check refused the prompt (`policy_denied`): the check is on the prompt, so another account gives
    the same answer. The job fails at once, refunded. The one place to change for dola-style account moves."""
    return _result(Fail(errors.CONTENT_REFUSED, "meta's safety check refused this %s" % kind))


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
    shrink: Callable[[bytes, str], Awaitable[tuple[bytes, str] | None]] = ffmpeg_shrink
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    uuid4: Callable[[], str] = lambda: str(uuid.uuid4())
    rand22: Callable[[], int] = lambda: secrets.randbits(22)
    hold_margin_s: float = HOLD_MARGIN_S


class _CoreVerdict(Exception):
    """An exception from a `ctx` gate (`report_ref`, `mark_sent`: a lost lease, a stopping worker): the core's own
    verdict, passed on unchanged, never turned into a result."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


class _Done(Exception):
    """A result decided before the send (a page that cannot be had, a signed-out page, an unreadable one)."""

    def __init__(self, result: SubmitResult):
        super().__init__()
        self.result = result


class _Run:
    def __init__(self, ctx, job, settings, sessions: Sessions, deps: Deps):
        self.kind = job.params.get("kind")
        if self.kind not in KINDS:
            raise ValueError("a meta job is an image or a video job, not %r" % (self.kind,))
        self.operation = job.operation
        if OPERATIONS.get(self.operation) != self.kind:
            raise ValueError("a meta %s job cannot be %r" % (self.kind, self.operation))
        if not job.sent_text:
            raise ValueError("the job has no sent_text: the core composes it")
        self.ctx, self.job, self.settings, self.sessions, self.deps = ctx, job, settings, sessions, deps
        self.n = job.params.get("n")       # the images asked and paid for; None: a video or a job from before n
        lead = VIDEO_FROM_TEXT if self.operation == "text_to_video" else (
            count_lead(self.n) if self.operation == "text_to_image" else "")
        self.text = " ".join((lead + job.sent_text).split())
        # meta reads the ratio from the words, after the final (framed) text; a 16:9 video is its own default
        if job.params.get("ratio") and (self.kind, job.params["ratio"]) not in RATIO_UNTYPED:
            self.text += RATIO_TAIL % job.params["ratio"]
        self.started = deps.clock()
        self.deadline = self.started + settings.get("meta.%s_submit_timeout_s" % self.kind) - deps.hold_margin_s
        self.account = ctx.account
        self.signed_in = False      # a page read this run said signed in: the strike count is cleared
        self.sent = False           # True from the moment `mark_sent` returned: meta may have the order
        self.conversation: str | None = None
        self.reader = wire.Reader()
        self.outcome: SubmitResult | None = None    # the verdict of the send, kept if the deadline hits the close

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

    async def _unsent(self, code: str, why: str, *effects) -> SubmitResult:
        """Nothing was sent: the order goes back to the queue (`Retry` is legal only here)."""
        await self._say(why)
        return _result(Retry(code), *effects)

    async def _unknown(self, why: str) -> SubmitResult:
        """After `mark_sent`: the outcome is unknown, never a re-send."""
        await self._say(why, "submit_error")
        return _result(Ambiguous(why[:500]))

    async def run(self) -> SubmitResult:
        images = await self._inputs() if self.operation in FROM_IMAGE else ()
        for i, (data, mime) in enumerate(images):
            if mime is None:
                why = "input image %d (%d bytes) could not be decoded or made small enough for meta's upload" % (
                    i, len(data))
                await self._say(why)
                return _result(Fail(errors.INPUT_UNUSABLE, why))
        async with Web(self.account, self.settings, self.sessions, self.ctx,
                       make_session=self.deps.make_session) as web:
            return await self._bounded(web, images)

    async def _inputs(self) -> tuple[tuple[bytes, str | None], ...]:
        """The job's input images, in the client's order, each as meta takes it; `(data, None)`: one that cannot be."""
        refs = await self.job.inputs()
        if not refs or (self.operation == "image_to_video" and len(refs) != 1):
            raise ValueError("a meta %s cannot take %d input images" % (self.operation, len(refs)))
        out = []
        for ref in refs:
            data = await self.deps.read_media(ref)
            small = await self.deps.shrink(data, ref.mime)
            out.append(small if small is not None else (data, None))
        return tuple(out)

    async def _bounded(self, web: Web, images) -> SubmitResult:
        """Everything up to the verdict runs before the deadline, whatever hangs. Past it: nothing sent is a `Retry`,
        a sent order is `Ambiguous` (the reconcile decides), never a re-send."""
        bound = asyncio.timeout(max(0.0, self.deadline - self.deps.clock()))
        try:
            async with bound:
                return await self._steps(web, images)
        except TimeoutError:
            if not bound.expired():
                raise
            if self.outcome is not None:
                return self.outcome
        why = "no verdict within %d s of the start (the submit timeout less %d s)" % (
            self.deadline - self.started, self.deps.hold_margin_s)
        if self.sent:
            return await self._unknown(why)
        return await self._unsent(errors.HTTP_ERROR, why)

    # ---- before the send ----
    def _wait(self, until: float | None, marked: bool) -> SubmitResult:
        """A page is needed and cannot be had now: no attempt, no charge, the account rests until the IP may load."""
        left = max(0.0, (until if until is not None else self.sessions.clock()) - self.sessions.clock())
        rest = Cooldown("a page read is needed and the proxy IP rests" if marked else
                        "a page read is needed and the proxy IP loaded one lately",
                        self.deps.now() + timedelta(seconds=left), recheck=True)
        return _result(Retry(errors.CHECKPOINT if marked else errors.PAGE_WAIT), rest)

    async def _page(self, web: Web, held):
        """One page read for the token. -> a `Held` (the new token, or `held` when the page cannot be had); raises
        `_Done` with the result when the job goes back."""
        acc = self.account
        try:
            page = await web.page()
        except GateClosed as closed:
            if held is not None:
                return held
            await self._say(str(closed))
            raise _Done(self._wait(closed.until, closed.marked)) from None
        if page.state == "in":
            self.signed_in = True
            self.sessions.keep(acc.id, acc.credential_gen, page)
            return self.sessions.token(acc.id, acc.credential_gen)
        if page.state == "out":
            self.sessions.forget(acc.id)
            raise _Done(await self._unsent(POOL_LOGGED_OUT, "meta.ai showed a signed-out page",
                                           *signed_out_effects(self.deps.now())))
        if page.state == "vercel":
            if held is not None:
                return held
            await self._say("Vercel's checkpoint stood in front of meta.ai and no token is held")
            raise _Done(self._wait(self.sessions.gate.until(web.ip), True))
        raise _Done(await self._unsent(errors.HTTP_ERROR, "meta.ai's page could not be read"))

    async def _token(self, web: Web):
        acc = self.account
        async with self.sessions.lock(acc.id):
            held = self.sessions.token(acc.id, acc.credential_gen)
            if held is None or self.sessions.stale(held, self.settings.get("meta.token_reread_s")):
                held = await self._page(web, held)
            return held

    async def _estab(self, web: Web, token: str):
        """A socket with the stream open on `self.conversation`: (socket, "ok" | "unauthorized" | "other")."""
        sock = await web.socket(token)
        try:
            await sock.send(wire.estab(self.conversation))
            async with asyncio.timeout(ESTAB_WAIT_S):
                while True:
                    raw = await sock.recv()
                    self.reader.feed(raw)
                    t, _, payload = wire.parse_frame(raw)
                    if t == wire.ESTAB:
                        return sock, wire.estab_answer(payload)
        except TimeoutError:
            return sock, "other"
        except BaseException:
            await quiet_close(sock)
            raise

    async def _steps(self, web: Web, images) -> SubmitResult:
        try:
            held = await self._token(web)
        except _Done as done:
            return done.result
        # every input, in the client's order, answered with its media id before anything is sent (an upload not
        # answered raises: `Retry`)
        attachments = []
        for i, (data, mime) in enumerate(images):
            name = "upload%s%s" % (i or "", _EXT.get(mime, ".jpg"))
            try:
                media_id = await web.upload(held.token, data, mime, name)
            except UploadFailed as refused:
                if refused.status in (401, 403):     # the token is dead: the next job reads the page again
                    self.sessions.forget(self.account.id)
                raise
            attachments.append(wire.Attachment(media_id, mime, name))
        self.conversation = self.deps.uuid4()
        sock, said = await self._estab(web, held.token)
        if said == "unauthorized":
            await quiet_close(sock)
            self.sessions.forget(self.account.id)
            try:
                held = await self._page(web, None)
            except _Done as done:
                return done.result
            sock, said = await self._estab(web, held.token)
        if said != "ok":
            await quiet_close(sock)
            return await self._unsent(errors.HTTP_ERROR, "meta's gateway did not open the stream (%s)" % said)
        try:
            self.outcome = await self._send(sock, held, attachments)
            return self.outcome
        finally:
            await quiet_close(sock)

    # ---- the send: nothing after it sends ----
    async def _send(self, sock, held, attachments) -> SubmitResult:
        conversation, deps = self.conversation, self.deps
        data = wire.send_frame(deps.uuid4(), wire.build_request(
            text=self.text, conversation=conversation, viewer=held.viewer, attachments=attachments,
            now_ms=int(deps.now().timestamp() * 1000), ids=deps.uuid4, rand22=deps.rand22()))
        await self._core(lambda: self.ctx.report_ref(conversation, {"conversation_id": conversation}))
        await self._core(self.ctx.mark_sent)
        self.sent = True
        await sock.send(data)
        while not self.reader.answer(self.kind).done:
            raw = await sock.recv()
            self.reader.feed(raw)
            if raw[:1] == bytes([wire.EOD]):
                break
        return await self._verdict(self.reader.answer(self.kind))

    async def _verdict(self, answer: wire.Answer) -> SubmitResult:
        """The result of meta's answer, kept in `self.outcome` BEFORE its timeline line is written: a deadline hit
        during that write keeps the verdict."""
        result, line, stage = self._judged(answer)
        self.outcome = result
        if line is not None:
            await self._say(line, stage)
        return result

    def _judged(self, answer: wire.Answer) -> tuple[SubmitResult, str | None, str]:
        """-> (the result, its timeline line or None, the line's stage)."""
        verdict = judge(answer, self.kind, self.n)
        if verdict == "done":
            note = wire.made_note(len(answer.finals), self.n, self.kind)
            if note is not None:
                log.warning("meta job %s: %s", self.job.id, note)
            return _result(Completed(wire.media_outputs(answer.finals, self.conversation, self.kind, self.n)),
                           Success()), note, "submit"
        if verdict == "unknown":
            why = ("meta's answer is not settled: done=%s, %d final %s(s), %d preview(s) left, %d of the other kind"
                   % (answer.done, len(answer.finals), self.kind, answer.previews, answer.other))
            return _result(Ambiguous(why[:500])), why, "submit_error"
        if verdict == "policy":
            return (refusal_outcome(self.job, self.ctx, self.settings, self.kind),
                    "meta's safety check refused the prompt (%s)" % (answer.error or {}).get("failure_kind"), "submit")
        why = "meta finished its answer with no %s" % self.kind
        code = errors.NO_IMAGE if self.kind == "image" else errors.NO_VIDEO
        if verdict == "limit":
            # meta's limit on THIS account, with no media: nothing was made, so the job goes to another account in a
            # new chat (no attempt used) until `meta.refusal_max_accounts` refused it, then fails with `code`
            outcome = account_refused(self.job, self.account.id, self.settings, not_taken=POOL_OUT_OF_POINTS,
                                      final=code, detail=why)
            line = ("meta's limit on %d account(s); the job goes to another account"
                    % len(outcome.state_patch[REFUSED_BY])) if isinstance(outcome, NotTaken) else None
            return _result(outcome, limit_balance(self.settings, self.deps.now(), self.kind)), line, "submit"
        return (_result(Fail(code, why)), "%s (failure kind: %s)" % (why, (answer.error or {}).get("failure_kind")),
                "submit")

    async def _evidence(self) -> None:
        """The redacted frame summary, diagnostics only: cut at `EVIDENCE_WAIT_S`, a failure logged and skipped."""
        try:
            data = redact_text(wire.summary(self.reader.frames).decode()).encode()
            async with asyncio.timeout(EVIDENCE_WAIT_S):
                await self.ctx.evidence("gateway-frames.json", data, "application/json")
        except Exception as exc:      # noqa: BLE001 - diagnostics never fail the order
            log.warning("evidence not kept: %s", type(exc).__name__)

    async def failed(self, exc: BaseException) -> SubmitResult:
        """The run raised. Before `mark_sent`: a retry (a proxy failure rests the account). After it: unknown. Only the
        exception's type is kept: a curl text may hold the proxy's login or the socket's URL."""
        cause = type(exc).__name__
        if isinstance(exc, wire.FrameError):
            cause = "unreadable gateway frame"
        if self.sent:
            return await self._unknown("the run failed after the send: %s" % cause)
        if isinstance(exc, NetError) and exc.proxy:
            until = self.deps.now() + timedelta(seconds=FAULT_REST_S)
            return await self._unsent(POOL_PROXY_FAILED, "the proxy failed: %s" % exc.kind,
                                      ProxyBad("meta.ai could not be reached through the proxy"),
                                      Cooldown("proxy failed", until))
        return await self._unsent(errors.HTTP_ERROR, "an HTTP step to meta.ai failed before the send: %s" % cause)

    def finish(self, outcome: SubmitResult) -> SubmitResult:
        """A run that read the account signed in clears its strikes, whatever it ends as."""
        if not self.signed_in or any(isinstance(e, Success) for e in outcome.account):
            return outcome
        return replace(outcome, account=(Success(), *outcome.account))


async def submit(ctx, job, settings, sessions: Sessions, *, deps: Deps | None = None) -> SubmitResult:
    """Send the job's order and return what happened. Exceptions the core must see (a refused `report_ref` or
    `mark_sent`) propagate unchanged; every other failure becomes a result."""
    run = _Run(ctx, job, settings, sessions, deps or Deps())
    try:
        outcome = await run.run()
    except _CoreVerdict as verdict:
        raise verdict.cause from None
    except Exception as exc:      # noqa: BLE001 - mapped by `failed`; cancellation is not an Exception and passes
        log.warning("submit failed (sent=%s): %s", run.sent, type(exc).__name__)
        outcome = await run.failed(exc)
    if not isinstance(outcome.job, Completed) and run.reader.frames:
        await run._evidence()
    return run.finish(outcome)
