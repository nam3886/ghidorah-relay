"""Submit for `meta-image` (text to image), held in the session until meta's answer is done.

One chat per job: load meta.ai (a new chat), read the sign-in state, wait for the composer to hydrate, type the core's
`sent_text`, take the conversation id meta names before Enter, then `mark_sent`, ONE Enter, and wait in the session for
the answer to finish (`data-streaming-state="DONE"` with `data-streaming-complete="true"`; `thinking-status` stays
"in-progress" after done and says nothing). The images are the final fbcdn links of the answer's image tiles; the
fetcher downloads them (fetch.py). The prompt asks for the count and the ratio; the plan takes neither.

Money rules (each has a test and a mutation):
- the conversation id (`warmupConversation`, the uuid of `/prompt/<uuid>`) is persisted with `report_ref` BEFORE
  `mark_sent`, so an answer lost after the send can be found again without sending anything;
- `mark_sent` runs immediately before the single Enter, and nothing presses Enter again: after `mark_sent` the engine
  never returns `Retry`, whatever is unclear is `Ambiguous` (unknown), never a re-send;
- only the hydrated composer (the Lexical contenteditable) is typed into, never the placeholder textarea rendered
  before hydration (it ignores input for 7-12 s on a cold load);
- Vercel's browser checkpoint sends nothing: `Retry(meta.checkpoint)` with a rest and a recheck, no strike, never a
  failed or charged job.
"""

import asyncio
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
    SEL_COMPOSER,
    SEL_IMAGE_TILE,
    SESSION_COOKIES,
    SITE_URL,
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
HOLD_MARGIN_S = 30        # the hold ends this long before the core's submit timeout (`meta.image_submit_timeout_s`)
FAULT_REST_S = 300        # a proxy failure before the send rests the account this long

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_WARMUP_ID = re.compile(r'"conversationId"\s*:\s*"(%s)"' % _UUID)
_CHAT_ID = re.compile(r"^%s(%s)/?$" % (re.escape(CHAT_PATH), _UUID))
_MIMES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

# The newest assistant message, whole (its attributes and its image tiles); "" when there is none yet.
ANSWER_JS = """(sel) => {
  const all = document.querySelectorAll(sel);
  return all.length ? all[all.length - 1].outerHTML : '';
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


class Answer(NamedTuple):
    done: bool                 # DONE and complete
    images: tuple[str, ...]    # the final links, in page order (one per tile)
    previews: int              # tiles that do not show a final link (yet)


class _AnswerParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.state: tuple[str, str] | None = None
        self.tiles: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        a = dict(attrs)
        if self.state is None and a.get("data-testid") == "assistant-message":
            self.state = (a.get("data-streaming-state") or "", a.get("data-streaming-complete") or "")
        elif tag == "img" and a.get("data-testid") == SEL_IMAGE_TILE:
            self.tiles.append(a.get("src") or "")


def is_final_image(url: str) -> bool:
    """An https fbcdn link that is not the low-res preview meta shows first."""
    return host_allowed(url, MEDIA_HOSTS, default_port_only=True) and PREVIEW_MARK not in urlsplit(url).path


def read_answer(html: str) -> Answer:
    """What the newest assistant message (its outerHTML) says."""
    parser = _AnswerParser()
    parser.feed(html or "")
    finals = [u for u in parser.tiles if is_final_image(u)]
    return Answer(parser.state == ("DONE", "true"), tuple(finals), len(parser.tiles) - len(finals))


def image_outputs(urls, conversation: str) -> tuple[Output, ...]:
    return tuple(Output(url, _MIMES.get(os.path.splitext(urlsplit(url).path)[1].lower(), "image/webp"), i,
                        meta={"conversation_id": conversation}) for i, url in enumerate(urls))


# ---- the run -----------------------------------------------------------------------------------------------------
@dataclass
class Deps:
    """What a run needs from outside; every field has the real default."""
    open_session: Callable = open_session
    sleep: Callable[[float], Awaitable] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    signal_wait_s: float = CHECK_SIGNAL_TIMEOUT_S
    composer_wait_s: float = COMPOSER_WAIT_S
    warmup_wait_s: float = WARMUP_WAIT_S
    receipt_wait_s: float = RECEIPT_WAIT_S
    tiles_grace_s: float = TILES_GRACE_S


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
        if job.params.get("kind") != "image":
            raise NotImplementedError("meta video submit is not built yet")
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
        async with self.deps.open_session(META_SITE, self.ctx.account, purpose="submit", token=self.ctx.browser_token,
                                          images=True, redact=redact_text) as session:
            self.session, self.page = session, session.page
            return await self._order()

    async def _order(self) -> SubmitResult:
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
        if " ".join((await box.inner_text()).split()) != self.text:
            return await self._unsent(errors.UI_SEND_FAILED, "the prompt did not go into the composer as typed")
        conversation = await self._warmup_id()
        if conversation is None:
            return await self._unsent(errors.UI_SEND_FAILED, "meta named no conversation before Enter")
        self.conversation = conversation
        await self._core(lambda: ctx.report_ref(conversation, {"conversation_id": conversation}))
        await self._core(self.ctx.mark_sent)
        self.sent = True
        await box.press("Enter")
        return await self._answer()

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
        hold_s = self.settings.get("meta.image_submit_timeout_s") - HOLD_MARGIN_S
        answer = await self._read_answer()
        while not answer.done:
            if deps.clock() - self.started >= hold_s:
                return await self._unknown("meta's answer was not done within %d s" % hold_s)
            await deps.sleep(STEP_S)
            answer = await self._read_answer()
        grace = deps.clock() + deps.tiles_grace_s
        while (answer.previews or not answer.images) and deps.clock() < grace:
            await deps.sleep(STEP_S)
            answer = await self._read_answer()
        if answer.previews:
            # a part delivery would lose the image still drawing: unknown, the conversation is read again later
            return await self._unknown("meta's answer is done but %d of its images still show a preview"
                                       % answer.previews)
        if answer.images:
            return _result(Completed(image_outputs(answer.images, self.conversation)), Success())
        await self._say("meta finished its answer with no image", "submit")
        return _result(Fail(errors.NO_IMAGE, "meta finished its answer with no image"))

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
        return read_answer(html if isinstance(html, str) else "")

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
