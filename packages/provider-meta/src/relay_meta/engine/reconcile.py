"""Reconcile: what became of a job whose send outcome is unknown (`Ambiguous` after `mark_sent`), and the poll that
delivers what a reconcile found.

The submit persisted the conversation id (`external_ref`, also `provider_state.conversation_id`) before its one
Enter. Reconcile reopens `/prompt/<uuid>` in the account's session and reads the thread; it never types, attaches or
presses a key, so nothing can be sent again:
- the newest assistant answer is DONE and every result of the job's kind shows its final link: `Found` (taken), with
  the links in `provider_state.outputs`;
- anything else (an answer still streaming, a preview left, no assistant answer, an answer with no result, a page
  that is not the thread or not signed in): `StillUnknown`. It is `answered` when the thread itself was read (meta's
  `conversation` query for this id came back): the read worked and proved nothing. `NotSent` is never returned: a
  warmed-up chat with no answer may still be one meta is working on, and a re-send would charge twice. The render
  deadline fails such a job with its refund.

`Found` moves the job to `waiting`; the poll then hands the recorded links to the fetcher without opening a browser
(the core gives a poll none).
"""

import json

from relay_browser.ui import inject_cookies
from relay_core.providers import Done, Found, Output, Pending, PollResult, StillUnknown
from relay_core.redact import redact_text
from relay_meta.constants import GRAPHQL_PATH, PAGE_LOAD_TIMEOUT_S, SEL_ASSISTANT, SESSION_COOKIES, SITE_URL
from relay_meta.engine.check import read_signals
from relay_meta.engine.site import META_SITE
from relay_meta.engine.submit import (
    ANSWER_JS,
    KINDS,
    STEP_S,
    Deps,
    chat_id,
    is_final_image,
    is_final_video,
    media_outputs,
    read_answer,
)

READ_WAIT_S = 30.0       # how long the reopened thread may take to show a done answer (it renders in seconds)


def thread_read(ev, conversation: str) -> bool:
    """meta's answer to the `conversation(conversationId, count)` query for this chat: the thread was read (a null
    conversation is an answer too)."""
    if ev.path != GRAPHQL_PATH or conversation not in (ev.req or "") or '"count"' not in (ev.req or ""):
        return False
    try:
        return "conversation" in (json.loads(ev.body).get("data") or {})
    except (ValueError, AttributeError):
        return False


async def reconcile(ctx, job, settings, *, deps: Deps | None = None, read_wait_s: float = READ_WAIT_S):
    """See the module text. `deps` and `read_wait_s` are seams for tests."""
    deps = deps or Deps()
    kind = job.params.get("kind")
    conversation = job.external_ref or (job.provider_state or {}).get("conversation_id")
    if kind not in KINDS or not conversation or chat_id(SITE_URL + "prompt/" + conversation) != conversation:
        return StillUnknown()
    async with deps.open_session(META_SITE, ctx.account, purpose="reconcile", token=ctx.browser_token, images=True,
                                 redact=redact_text) as session:
        page, recorder = session.page, session.recorder
        await inject_cookies(session.ctx, ctx.account.credential, SESSION_COOKIES, SITE_URL)
        await page.goto(SITE_URL + "prompt/" + conversation, wait_until="domcontentloaded",
                        timeout=PAGE_LOAD_TIMEOUT_S * 1000)
        state, _ = await read_signals(page, META_SITE.host, deps.signal_wait_s)
        if state != "alive" or chat_id(page.url) != conversation:
            return StillUnknown()
        end = deps.clock() + read_wait_s
        while True:
            try:
                html = await page.evaluate(ANSWER_JS, SEL_ASSISTANT)
            except Exception:      # noqa: BLE001 - the page is busy: read again
                html = ""
            answer = read_answer(html if isinstance(html, str) else "", kind)
            if answer.done and answer.media and not answer.previews:
                outputs = [{"ref": o.ref, "mime": o.mime}
                           for o in media_outputs(answer.media, conversation, kind, job.params.get("n"))]
                return Found(conversation, {"conversation_id": conversation, "outputs": outputs})
            if deps.clock() >= end:
                read = any(thread_read(e, conversation) for e in recorder.events())
                return StillUnknown(answered=read or bool(html))
            await deps.sleep(STEP_S)


async def poll(ctx, job, settings) -> PollResult:
    """The links a reconcile found, handed to the fetcher; reads nothing upstream. A job with none recorded (it was
    never found) is a poll that did not answer: the render deadline bounds it."""
    state = job.provider_state or {}
    final = is_final_image if job.params.get("kind") == "image" else is_final_video
    outs = list(state.get("outputs") or ())
    if not outs or not all(isinstance(o, dict) and final(str(o.get("ref") or "")) for o in outs):
        return PollResult(Pending("no result was recorded for this meta job", answered=False))
    conversation = state.get("conversation_id") or job.external_ref
    return PollResult(Done(tuple(Output(o["ref"], str(o.get("mime") or ""), i, meta={"conversation_id": conversation})
                                 for i, o in enumerate(outs))))
