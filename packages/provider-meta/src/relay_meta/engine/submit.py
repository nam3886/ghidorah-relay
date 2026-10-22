"""Submit for `meta-image` (text to image) and `meta-video` (one image to video), held in the session until meta's
answer is done.

One chat per job: load meta.ai (a new chat), read the sign-in state, wait for the composer to hydrate, type the core's
`sent_text`, for a video attach the one input image and wait for meta's upload answer, take the conversation id meta
names before Enter, then `mark_sent`, ONE Enter, and wait in the session for the answer to finish
(`data-streaming-state="DONE"` with `data-streaming-complete="true"`; `thinking-status` stays "in-progress" after done
and says nothing). The results are the final fbcdn links of the answer: the image tiles, or the `<video>` (a direct
https `.mp4`, never `blob:`); the fetcher downloads them (fetch.py). The prompt asks for the count and the ratio; the
plan takes neither.

The input image goes through the composer's single file input. meta ignores Enter while the upload is pending, and a
5 MB PNG never got an answer (FINDINGS.md), so an image over `UPLOAD_MAX_BYTES` (or not a JPEG, PNG or WebP) is first
re-encoded to a JPEG of at most `UPLOAD_MAX_SIDE` px a side by the browser itself, in memory, before meta's page loads.

Money rules (each has a test and a mutation):
- the conversation id (`warmupConversation`, the uuid of `/prompt/<uuid>`) is persisted with `report_ref` BEFORE
  `mark_sent`, so an answer lost after the send can be found again without sending anything;
- a video is sent only once meta answered the upload (`{media_id}`) and the composer shows exactly one attached image:
  never the motion text without its image;
- `mark_sent` runs immediately before the single Enter, and nothing presses Enter again: after `mark_sent` the engine
  never returns `Retry`, whatever is unclear is `Ambiguous` (unknown), never a re-send;
- only the hydrated composer (the Lexical contenteditable) is typed into, never the placeholder textarea rendered
  before hydration (it ignores input for 7-12 s on a cold load);
- Vercel's browser checkpoint sends nothing: `Retry(meta.checkpoint)` with a rest and a recheck, no strike, never a
  failed or charged job.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import unquote_plus, urlsplit

from relay_browser.session import BrowserLaunchError, BrowserRefused, open_session
from relay_browser.ui import inject_cookies
from relay_core.net import host_allowed
from relay_core.providers import Ambiguous, Completed, Cooldown, Fail, Output, ProxyBad, Retry, SubmitResult, Success
from relay_core.redact import redact_text
from relay_meta import errors
from relay_meta.constants import (
    CHAT_PATH,
    CHECK_SIGNAL_TIMEOUT_S,
    GRAPHQL_PATH,
    MEDIA_HOSTS,
    PAGE_LOAD_TIMEOUT_S,
    PREVIEW_MARK,
    SEL_ASSISTANT,
    SEL_ATTACHED,
    SEL_COMPOSER,
    SEL_FILE_INPUT,
    SEL_IMAGE_TILE,
    SESSION_COOKIES,
    SITE_URL,
    UPLOAD_PATH,
)
from relay_meta.engine.check import checkpoint_rest, read_signals, signed_out_effects
from relay_meta.engine.site import META_SITE

log = logging.getLogger("relay_meta.submit")

# The platform's pool codes (the platform owns them; the engine only names them).
POOL_LOGGED_OUT = "pool.account_logged_out"
POOL_PROXY_FAILED = "pool.proxy_failed"
NETWORK_MARK = "net::err"        # Playwright's network failures: the browser could not reach meta (the proxy)

COMPOSER_WAIT_S = 30.0    # the contenteditable hydrates 7-12 s after a cold load (0.1 s warm)
WARMUP_WAIT_S = 10.0      # `warmupConversation` came 1.2-1.5 s after the typing in every capture
RECEIPT_WAIT_S = 15.0     # the URL became `/prompt/<uuid>` 0.1 s after Enter
STEP_S = 0.5              # between two reads of the page
TILES_GRACE_S = 10.0      # after DONE, how long the tiles may take to show their final links
HOLD_MARGIN_S = 30        # the hold ends this long before the core's submit timeout (`meta.<kind>_submit_timeout_s`)
FAULT_REST_S = 300        # a proxy failure before the send rests the account this long
UPLOAD_WAIT_S = 60.0      # a 560 KB webp was answered in 7.3 s through a proxy; a 5 MB PNG never was
ATTACHED_WAIT_S = 5.0     # after the upload answer, how long the composer may take to show the thumbnail
UPLOAD_MAX_BYTES = 1 << 20    # an input image above this is re-encoded before the upload
UPLOAD_MAX_SIDE = 2048        # ... to a JPEG at most this many pixels a side (meta's own images are 1920x1280)
UPLOAD_AS_IS = ("image/jpeg", "image/png", "image/webp")     # sent unchanged when small enough

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_WARMUP_ID = re.compile(r'"conversationId"\s*:\s*"(%s)"' % _UUID)
_CHAT_ID = re.compile(r"^%s(%s)/?$" % (re.escape(CHAT_PATH), _UUID))
_MIMES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
KINDS = ("image", "video")

# The newest assistant message, whole (its attributes, its image tiles, its video); "" when there is none yet.
ANSWER_JS = """(sel) => {
  const all = document.querySelectorAll(sel);
  return all.length ? all[all.length - 1].outerHTML : '';
}"""

# [base64, mime, max bytes, max side] -> base64 of a JPEG within both, or "" when the image cannot be made that small
# (or cannot be decoded). Runs in the browser before meta's page loads: no image library in the worker.
SHRINK_JS = """async ([b64, mime, maxBytes, maxSide]) => {
  const raw = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const bmp = await createImageBitmap(new Blob([raw], {type: mime}));
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const canvas = new OffscreenCanvas(Math.max(1, Math.round(bmp.width * scale)),
                                     Math.max(1, Math.round(bmp.height * scale)));
  canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
  for (const q of [0.9, 0.8, 0.7, 0.6, 0.5]) {
    const blob = await canvas.convertToBlob({type: 'image/jpeg', quality: q});
    if (blob.size > maxBytes) continue;
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  return '';
}"""


# ---- pure helpers ------------------------------------------------------------------------------------------------
def warmup_id(ev) -> str | None:
    """The conversation a recorded `warmupConversation` call names (in its request), None for any other event."""
    if ev.path != GRAPHQL_PATH or '"warmupConversation"' not in (ev.body or ""):
        return None
    m = _WARMUP_ID.search(unquote_plus(ev.req or ""))
    return m.group(1) if m else None


def chat_id(url: str) -> str | None:
    """The uuid of a `https://www.meta.ai/prompt/<uuid>` address."""
    try:
        m = _CHAT_ID.match(urlsplit(url or "").path)
    except ValueError:
        return None
    return m.group(1) if m else None


def upload_done(ev) -> bool:
    """meta's answer to the image upload: the `rupload.meta.ai` POST with a `media_id`."""
    if ev.kind != "http" or ev.method != "POST" or ev.status != 200 or not ev.path.startswith(UPLOAD_PATH):
        return False
    try:
        return bool(json.loads(ev.body).get("media_id"))
    except (ValueError, AttributeError):
        return False


class Answer(NamedTuple):
    done: bool                 # DONE and complete
    media: tuple[str, ...]     # the final links of the job's kind, in page order
    previews: int              # tiles or videos of that kind that do not show a final link (yet)


class _AnswerParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.state: tuple[str, str] | None = None
        self.tiles: list[str] = []
        self.videos: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        if self.state is None and a.get("data-testid") == "assistant-message":
            self.state = (a.get("data-streaming-state") or "", a.get("data-streaming-complete") or "")
        elif tag == "img" and a.get("data-testid") == SEL_IMAGE_TILE:
            self.tiles.append(a.get("src") or "")
        elif tag == "video":
            self.videos.append(a.get("src") or "")


def is_final_image(url: str) -> bool:
    """An https fbcdn link that is not the low-res preview meta shows first."""
    return host_allowed(url, MEDIA_HOSTS, default_port_only=True) and PREVIEW_MARK not in urlsplit(url).path


def is_final_video(url: str) -> bool:
    """A direct https fbcdn `.mp4` (a `blob:` source is the player's, not a link the fetcher can take)."""
    return host_allowed(url, MEDIA_HOSTS, default_port_only=True) and urlsplit(url).path.lower().endswith(".mp4")


def read_answer(html: str, kind: str = "image") -> Answer:
    """What the newest assistant message (its outerHTML) says about the media of `kind`."""
    parser = _AnswerParser()
    parser.feed(html or "")
    found, final = (parser.tiles, is_final_image) if kind == "image" else (parser.videos, is_final_video)
    finals = [u for u in found if final(u)]
    return Answer(parser.state == ("DONE", "true"), tuple(finals), len(found) - len(finals))


def media_outputs(urls, conversation: str, kind: str = "image") -> tuple[Output, ...]:
    def mime(url: str) -> str:
        if kind == "video":
            return "video/mp4"
        return _MIMES.get(os.path.splitext(urlsplit(url).path)[1].lower(), "image/webp")
    return tuple(Output(url, mime(url), i, meta={"conversation_id": conversation}) for i, url in enumerate(urls))


_store = None


async def read_input_media(ref) -> bytes:
    """The job's input image from the object store, the store made on first use (an image job needs none). The one
    place the engine reaches past `RunCtx` for a file, as dola's does: the protocol hands out only a `MediaRef`."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


# ---- the run -----------------------------------------------------------------------------------------------------
@dataclass
class Deps:
    """What a run needs from outside; every field has the real default."""
    open_session: Callable = open_session
    read_media: Callable[..., Awaitable[bytes]] = read_input_media
    sleep: Callable[[float], Awaitable] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    signal_wait_s: float = CHECK_SIGNAL_TIMEOUT_S
    composer_wait_s: float = COMPOSER_WAIT_S
    warmup_wait_s: float = WARMUP_WAIT_S
    receipt_wait_s: float = RECEIPT_WAIT_S
    tiles_grace_s: float = TILES_GRACE_S
    upload_wait_s: float = UPLOAD_WAIT_S
    attached_wait_s: float = ATTACHED_WAIT_S


class _CoreVerdict(Exception):
    """An exception from a `ctx` gate (`report_ref`, `mark_sent`: a lost lease, a stopping worker): the core's own
    verdict, passed on unchanged, never turned into a result."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


class _Run:
    def __init__(self, ctx, job, settings, deps: Deps):
        self.kind = job.params.get("kind")
        if self.kind not in KINDS:
            raise ValueError("a meta job is an image or a video job, not %r" % (self.kind,))
        if not job.sent_text:
            raise ValueError("the job has no sent_text: the core composes it")
        self.ctx, self.job, self.settings, self.deps = ctx, job, settings, deps
        # One line: the composer sends on Enter, so a newline would send part of the prompt.
        self.text = " ".join(job.sent_text.split())
        self.started = deps.clock()
        self.signed_in = False      # the sign-in read said signed in: the strike count is cleared
        self.sent = False           # True from the moment `mark_sent` returned: meta may have the order
        self.conversation: str | None = None
        self.session = None
        self.page = None

    async def _say(self, text: str, stage: str, source: str = "engine") -> None:
        try:
            await self.ctx.event(text, stage, source)
        except Exception as exc:      # noqa: BLE001 - diagnostics never fail the order
            log.warning("event %s not recorded: %s", stage, type(exc).__name__)

    async def _core(self, fn) -> None:
        try:
            await fn()
        except Exception as exc:
            raise _CoreVerdict(exc) from exc

    async def run(self) -> SubmitResult:
        image = await self._input_image() if self.kind == "video" else None     # read before the browser opens
        async with self.deps.open_session(META_SITE, self.ctx.account, purpose="submit", token=self.ctx.browser_token,
                                          images=True, redact=redact_text) as session:
            self.session, self.page = session, session.page
            if image is not None:
                image = await self._shrink(*image)
                if image is None:
                    return await self._unsent(errors.UI_SEND_FAILED, "the input image could not be made smaller than "
                                              "%d bytes for meta's upload" % UPLOAD_MAX_BYTES)
            return await self._order(image)

    async def _input_image(self) -> tuple[bytes, str]:
        refs = await self.job.inputs()
        if len(refs) != 1:
            raise ValueError("a meta video animates exactly one image, the job holds %d" % len(refs))
        return await self.deps.read_media(refs[0]), refs[0].mime

    async def _shrink(self, data: bytes, mime: str) -> tuple[bytes, str] | None:
        """The image as it is uploaded: unchanged when small enough, else a JPEG the browser re-encodes in memory (on
        the blank page, before meta's page loads). None when it cannot be made small enough."""
        if len(data) <= UPLOAD_MAX_BYTES and mime in UPLOAD_AS_IS:
            return data, mime
        b64 = await self.page.evaluate(SHRINK_JS, [base64.b64encode(data).decode(), mime, UPLOAD_MAX_BYTES,
                                                   UPLOAD_MAX_SIDE])
        if not b64:
            return None
        small = base64.b64decode(b64)
        await self._say("the input image (%d bytes, %s) was re-encoded to a %d-byte JPEG for meta's upload"
                        % (len(data), mime, len(small)), "submit")
        return small, "image/jpeg"

    async def _order(self, image: tuple[bytes, str] | None) -> SubmitResult:
        ctx, page = self.ctx, self.page
        await inject_cookies(self.session.ctx, ctx.account.credential, SESSION_COOKIES, SITE_URL)
        await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_S * 1000)
        state, _ = await read_signals(page, META_SITE.host, self.deps.signal_wait_s)
        if state == "checkpoint":
            rest = checkpoint_rest(self.deps.now(), self.settings.get("meta.checkpoint_rest_s"), "seen by a submit")
            return await self._unsent(errors.CHECKPOINT, "Vercel's browser checkpoint stood in front of meta.ai", rest)
        if state == "signed_out":
            return await self._unsent(POOL_LOGGED_OUT, "meta.ai showed a signed-out sign",
                                      *signed_out_effects(self.deps.now()))
        if state != "alive":
            return await self._unsent(errors.UI_PAGE_NOT_READY, "no sign-in sign within %g s"
                                      % self.deps.signal_wait_s)
        self.signed_in = True

        box = page.locator(SEL_COMPOSER)
        try:
            await box.wait_for(state="visible", timeout=self.deps.composer_wait_s * 1000)
        except Exception:      # noqa: BLE001 - Playwright's timeout: the composer never hydrated
            return await self._unsent(errors.UI_PAGE_NOT_READY, "the composer did not hydrate within %g s"
                                      % self.deps.composer_wait_s)
        await box.click()
        await page.keyboard.insert_text(self.text)
        if image is not None:
            why = await self._attach(*image)
            if why:
                return await self._unsent(errors.UI_SEND_FAILED, why)
        if " ".join((await box.inner_text()).split()) != self.text:
            return await self._unsent(errors.UI_SEND_FAILED, "the prompt did not go into the composer as typed")
        conversation = await self._warmup_id()
        if conversation is None:
            return await self._unsent(errors.UI_SEND_FAILED, "meta named no conversation before Enter")
        self.conversation = conversation
        await self._core(lambda: ctx.report_ref(conversation, {"conversation_id": conversation}))
        # the thumbnail is read again right before the send: meta may have dropped or doubled it since the attach;
        # nothing but mark_sent is awaited between this read and the one Enter
        if image is not None and (shown := await page.locator(SEL_ATTACHED).count()) != 1:
            return await self._unsent(errors.UI_SEND_FAILED, "just before the send the composer shows %d attached "
                                      "images, not 1" % shown)
        await self._core(self.ctx.mark_sent)
        self.sent = True
        await box.press("Enter")
        return await self._answer()

    async def _attach(self, data: bytes, mime: str) -> str:
        """Choose the image in the composer's file input and wait until meta answered its upload AND the composer
        shows it: meta ignores Enter while an upload is pending, so an Enter before that would send nothing (or, worse,
        the motion text alone). "" when attached, else why not."""
        page, recorder = self.page, self.session.recorder
        files = page.locator(SEL_FILE_INPUT)
        if await files.count() != 1:
            return "the composer has no single file input"
        since = recorder.now()
        await files.first.set_input_files({"name": "upload" + _EXT.get(mime, ".jpg"), "mimeType": mime,
                                           "buffer": data})
        t0 = self.deps.clock()
        if await recorder.wait_for(upload_done, self.deps.upload_wait_s, since=since) is None:
            return "meta did not answer the image upload within %g s" % self.deps.upload_wait_s
        await self._say("meta took the %d-byte image upload after %.1f s" % (len(data), self.deps.clock() - t0),
                        "submit")
        # give the thumbnail a moment to show; the count that decides is read right before the send (`_order`)
        attached = self.page.locator(SEL_ATTACHED)
        grace = self.deps.clock() + self.deps.attached_wait_s
        while await attached.count() != 1 and self.deps.clock() < grace:
            await self.deps.sleep(STEP_S)
        return ""

    async def _warmup_id(self) -> str | None:
        """The conversation the page warmed up for this draft: the newest `warmupConversation` call."""
        recorder = self.session.recorder
        if await recorder.wait_for(lambda e: warmup_id(e) is not None, self.deps.warmup_wait_s) is None:
            return None
        return [cid for e in recorder.events() if (cid := warmup_id(e))][-1]

    # ---- after the Enter: nothing here sends ----
    async def _answer(self) -> SubmitResult:
        ctx, deps = self.ctx, self.deps
        url_id = await self._receipt()
        if url_id is None:
            return await self._unknown("the page showed no %s address within %g s of Enter"
                                       % (CHAT_PATH, deps.receipt_wait_s))
        if url_id != self.conversation:
            await self._say("meta opened %s, not the warmed-up %s: keeping meta's" % (url_id, self.conversation),
                            "submit")
            self.conversation = url_id
            await self._core(lambda: ctx.report_ref(url_id, {"conversation_id": url_id}))
        hold_s = self.settings.get("meta.%s_submit_timeout_s" % self.kind) - HOLD_MARGIN_S
        answer = await self._read_answer()
        while not answer.done:
            if deps.clock() - self.started >= hold_s:
                return await self._unknown("meta's answer was not done within %d s" % hold_s)
            await deps.sleep(STEP_S)
            answer = await self._read_answer()
        grace = deps.clock() + deps.tiles_grace_s
        while (answer.previews or not answer.media) and deps.clock() < grace:
            await deps.sleep(STEP_S)
            answer = await self._read_answer()
        if answer.previews:
            # a part delivery would lose the media still drawing: unknown, the conversation is read again later
            return await self._unknown("meta's answer is done but %d of its %ss show no final link yet"
                                       % (answer.previews, self.kind))
        if answer.media:
            return _result(Completed(media_outputs(answer.media, self.conversation, self.kind)), Success())
        why = "meta finished its answer with no %s" % self.kind
        await self._say(why, "submit")
        return _result(Fail(errors.NO_IMAGE if self.kind == "image" else errors.NO_VIDEO, why))

    async def _receipt(self) -> str | None:
        waited = 0.0
        while True:
            found = chat_id(self.page.url)
            if found or waited >= self.deps.receipt_wait_s:
                return found
            await self.deps.sleep(STEP_S)
            waited += STEP_S

    async def _read_answer(self) -> Answer:
        try:
            html = await self.page.evaluate(ANSWER_JS, SEL_ASSISTANT)
        except Exception:      # noqa: BLE001 - the page is busy: read again
            html = ""
        return read_answer(html if isinstance(html, str) else "", self.kind)

    # ---- results ----
    async def _unsent(self, code: str, why: str, *effects) -> SubmitResult:
        """Nothing was sent: the order goes back to the queue (`Retry` is legal only here)."""
        await self._say(why, "submit")
        return _result(Retry(code), *effects)

    async def _unknown(self, why: str) -> SubmitResult:
        """After `mark_sent`: the outcome is unknown, never a re-send."""
        await self._say(why, "submit_error")
        return _result(Ambiguous(why[:500]))

    async def failed(self, exc: BaseException) -> SubmitResult:
        """The run raised. Before `mark_sent`: a retry (a network error is the proxy's). After it: unknown."""
        cause = redact_text("%s: %s" % (type(exc).__name__, str(exc)[:300]))
        if self.sent:
            return await self._unknown("the browser failed after sending: %s" % cause)
        if NETWORK_MARK in cause.lower():
            until = self.deps.now() + timedelta(seconds=FAULT_REST_S)
            return await self._unsent(POOL_PROXY_FAILED, cause, ProxyBad("browser could not reach meta.ai"),
                                      Cooldown("proxy failed", until))
        return await self._unsent(errors.UI_PAGE_NOT_READY, cause)

    def finish(self, outcome: SubmitResult) -> SubmitResult:
        """A run that read the account signed in clears its strikes, whatever it ends as."""
        if not self.signed_in or any(isinstance(e, Success) for e in outcome.account):
            return outcome
        return replace(outcome, account=(Success(), *outcome.account))


async def submit(ctx, job, settings, *, deps: Deps | None = None) -> SubmitResult:
    """Send the job's order and return what happened. Exceptions the core must see (a refused `report_ref` or
    `mark_sent`, a browser that would not start) propagate unchanged; every other failure becomes a result."""
    run = _Run(ctx, job, settings, deps or Deps())
    try:
        return run.finish(await run.run())
    except _CoreVerdict as verdict:
        raise verdict.cause from None
    except (BrowserLaunchError, BrowserRefused):
        raise
    except Exception as exc:      # noqa: BLE001 - mapped by `failed`; cancellation is not an Exception and passes
        log.warning("submit failed (sent=%s): %s", run.sent, type(exc).__name__)
        return run.finish(await run.failed(exc))
