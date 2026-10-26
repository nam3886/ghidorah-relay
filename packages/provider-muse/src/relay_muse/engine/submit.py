"""Submit for `muse-image` (text to image) and `muse-video` (text to video, or one image animated), held in the run
until muse's answer, or handed to the poll when it is slower than the hold.

One side chat per job: open the tunnel (session, waking the workspace if needed, tokens, handshake, client register,
subscribe, node register), persist the new chat's id with `report_ref`, then `mark_sent` and ONE `/chat/stream` (an
input image inline in it). Then read the chat's history every `muse.history_poll_s`: the job is done once its results
are there (`chat.verdict`), refused once a settled chat holds an answer in words and no result.

Money rules (each has a test and a mutation):
- the chat id is persisted BEFORE `mark_sent`, so a send whose answer was lost is looked up, never sent again;
- after `mark_sent` the engine never returns `Retry`: no answer to the send is `Ambiguous` (reconciled), a 4xx answer
  is `NotTaken` (muse refused the request itself, nothing was made), and once muse acknowledged the send the order is
  taken whatever follows: past the hold, or on a broken tunnel, it is `Accepted` and the poll reads the chat;
- results: any is a full charge (Q4); the first n are delivered and never more; none is a failure with its refund;
- an approval or a publish request the agent raises is denied, never granted.
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import timedelta

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    Fail,
    NotTaken,
    ProxyBad,
    Retry,
    SubmitResult,
    Success,
    Suspect,
)
from relay_core.redact import redact_text
from relay_muse import errors
from relay_muse.constants import INPUT_MIMES, SIGNED_OUT_GAP_S, SIGNED_OUT_REST_S
from relay_muse.engine.chat import (
    SetupError,
    compose,
    deny_approvals,
    history_events,
    history_path,
    outputs,
    read_history,
    signature,
    stream_body,
    verdict,
    want_of,
)
from relay_muse.engine.link import Deps, opened
from relay_muse.engine.tunnel import TunnelError
from relay_muse.engine.web import NetError, SignedOut, SiteError, VmNotReady

log = logging.getLogger("relay_muse.submit")

POOL_LOGGED_OUT = "pool.account_logged_out"
POOL_PROXY_FAILED = "pool.proxy_failed"
HOLD_MARGIN_S = 15        # the run ends this long before the core's submit timeout, so its verdict is always written
FAULT_REST_S = 300        # a proxy failure before the send rests the account this long
VM_REST_S = 300           # a workspace that did not wake rests the account this long
OPERATIONS = {"text_to_image": "image", "text_to_video": "video", "image_to_video": "video"}


def signed_out_effects(now) -> tuple[Suspect, Cooldown]:
    """A 401 to the session read: one spaced strike, and a rest after which the account is checked again."""
    return (Suspect("not signed in: muse answered 401 to the session read", min_gap_s=SIGNED_OUT_GAP_S),
            Cooldown("signed-out read, to be checked again", now + timedelta(seconds=SIGNED_OUT_REST_S), recheck=True))


class _CoreVerdict(Exception):
    """An exception from a `ctx` gate (`report_ref`, `mark_sent`): the core's own verdict, passed on unchanged."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


class _Run:
    def __init__(self, ctx, job, settings, deps: Deps):
        self.kind = job.params.get("kind")
        if OPERATIONS.get(job.operation) != self.kind:
            raise ValueError("a muse %s job cannot be %r" % (self.kind, job.operation))
        if job.sent_text is None:
            raise ValueError("the job has no sent_text: the core composes it")
        self.ctx, self.job, self.settings, self.deps = ctx, job, settings, deps
        self.want = want_of(job.params)
        self.session = str(uuid.uuid4())      # the new side chat: one per job
        self.node = str(uuid.uuid4())
        self.started = deps.clock()
        self.deadline = self.started + settings.get("muse.%s_submit_timeout_s" % self.kind) - HOLD_MARGIN_S
        self.signed_in = False    # the session read worked: the strike count is cleared
        self.sent = False         # from the moment `mark_sent` returned: muse may have the order
        self.acked = False        # muse acknowledged the send: the order is taken

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

    async def _input(self) -> tuple[bytes, str] | None:
        if self.job.operation != "image_to_video":
            return None
        refs = await self.job.inputs()
        if len(refs) != 1 or refs[0].mime not in INPUT_MIMES:
            raise ValueError("a muse video from an image takes one JPEG, PNG or WebP")
        return await self.deps.read_media(refs[0]), refs[0].mime

    async def run(self) -> SubmitResult:
        image = await self._input()       # read before anything is opened
        bound = asyncio.timeout(max(0.0, self.deadline - self.deps.clock()) + HOLD_MARGIN_S / 2)
        try:
            async with bound:
                return await self._steps(image)
        except _CoreVerdict:
            raise
        except TimeoutError:
            if not bound.expired():
                return await self._failed(TimeoutError())
            return await self._failed(TunnelError("no verdict within the submit's bound"))
        except Exception as exc:      # noqa: BLE001 - mapped by `_failed`; cancellation is not an Exception
            return await self._failed(exc)

    async def _steps(self, image: tuple[bytes, str] | None) -> SubmitResult:
        timeout = self.deps.call_timeout_s
        async with opened(self.ctx, self.settings, self.deps, chat=True, node=self.node) as (_web, tunnel):
            self.signed_in = True
            message = compose(self.job.operation, self.job.params, self.job.sent_text)
            body = stream_body(message, self.node, self.session, image)
            await self._core(lambda: self.ctx.report_ref(self.session, {"session_id": self.session}))
            await self._core(self.ctx.mark_sent)
            self.sent = True
            try:
                reply = await tunnel.call("POST", "/chat/stream", body,
                                          timeout=self.settings.get("muse.send_ack_timeout_s"))
            except TunnelError as e:
                return await self._unknown("no answer to the send: %s" % e)
            ack = reply.json()
            if 400 <= reply.status < 500:
                why = "muse refused the send itself (%d %s): nothing was made" % (reply.status, _error_code(ack))
                await self._say(why, "submit_error")
                return _result(NotTaken(errors.HTTP_ERROR))
            if reply.status != 200 or not isinstance(ack, dict) or ack.get("session_id") not in (None, self.session):
                return await self._unknown("muse answered %d to the send, not its acknowledgement of this chat"
                                           % reply.status)
            self.acked = True
            await self._say("muse took the order in side chat %s" % self.session)
            return await self._hold(tunnel, timeout)

    async def _hold(self, tunnel, timeout: float) -> SubmitResult:
        """Read the chat until a verdict or the hold's end. Nothing here sends the order again."""
        denied: set[str] = set()
        seen, since = None, self.deps.clock()
        settle_s, poll_s = self.settings.get("muse.settle_s"), self.settings.get("muse.history_poll_s")
        while True:
            events = history_events(await tunnel.call("GET", history_path(self.session), timeout=timeout))
            if events is not None:
                reading = read_history(events, self.kind)
                if signature(reading) != seen:
                    seen, since = signature(reading), self.deps.clock()
                found = verdict(reading, self.want, self.deps.clock() - since >= settle_s)
                if found == "done":
                    if len(reading.media) < self.want:
                        await self._say("muse made %d of the %d %ss asked; all are charged (any output is a full "
                                        "charge)" % (len(reading.media), self.want, self.kind))
                    return _result(Completed(outputs(reading, self.want, self.session)), Success())
                if found == "refused":
                    why = "muse answered with no %s: %s" % (self.kind, " ".join(reading.text.split())[:300])
                    await self._say(why, "submit_error")
                    return _result(Fail(errors.REFUSED, why), Success())
                for aid in await deny_approvals(tunnel, [*tunnel.events, *events], denied, timeout):
                    await self._say("muse asked an approval (%s): denied" % aid)
            left = self.deadline - self.deps.clock()
            if left <= 0:
                return self._accepted()
            await tunnel.listen(min(poll_s, left))

    def _accepted(self) -> SubmitResult:
        """Taken and not finished: the poll reads the chat from here."""
        return _result(Accepted(self.session, {"session_id": self.session}), Success())

    async def _unknown(self, why: str) -> SubmitResult:
        await self._say(why, "submit_error")
        return _result(Ambiguous(why[:500]))

    async def _failed(self, exc: BaseException) -> SubmitResult:
        """The run raised. Taken: the poll follows it. Sent: unknown. Before the send: back to the queue."""
        cause = redact_text("%s: %s" % (type(exc).__name__, str(exc)[:300]))
        if self.acked:
            await self._say("the run broke after muse took the order (%s): the poll follows it" % cause)
            return self._accepted()
        if self.sent:
            return await self._unknown("the run broke after the send: %s" % cause)
        now = self.deps.now()
        await self._say("nothing was sent: %s" % cause)
        if isinstance(exc, SignedOut):
            return _result(Retry(POOL_LOGGED_OUT), *signed_out_effects(now))
        if isinstance(exc, VmNotReady):
            return _result(Retry(errors.VM_NOT_READY), Cooldown("muse workspace not ready",
                                                                now + timedelta(seconds=VM_REST_S)))
        if isinstance(exc, NetError) and exc.proxy:
            return _result(Retry(POOL_PROXY_FAILED), ProxyBad("the proxy could not reach muse"),
                           Cooldown("proxy failed", now + timedelta(seconds=FAULT_REST_S)))
        if not isinstance(exc, NetError | SiteError | TunnelError | SetupError | TimeoutError):
            log.warning("muse submit broke before the send: %s", type(exc).__name__)
        return _result(Retry(errors.HTTP_ERROR))

    def finish(self, outcome: SubmitResult) -> SubmitResult:
        """A run that read the session signed in clears the account's strikes, whatever it ends as."""
        if not self.signed_in or any(isinstance(e, Success) for e in outcome.account):
            return outcome
        return replace(outcome, account=(Success(), *outcome.account))


def _error_code(body) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code)[:60] if code else ""


async def submit(ctx, job, settings, *, deps: Deps | None = None) -> SubmitResult:
    """Send the job's order and return what happened. A refused `report_ref` or `mark_sent` propagates unchanged."""
    run = _Run(ctx, job, settings, deps or Deps())
    try:
        return run.finish(await run.run())
    except _CoreVerdict as verdict_:
        raise verdict_.cause from None
