"""Submit: one job to chatgpt.com (`POST /backend-api/f/conversation`, a new conversation), held to its verdict.

The free steps, all before the send: the home page (build id, sentinel script), the access token, the image limit
(`conversation/init`), the input image of an edit (`upload.upload_image`), the sentinel tokens and the conduit token
(`sentinel`). Then the client-made message id and the exact prompt are written to the job (`ctx.report_ref`),
`ctx.mark_sent()`, and the ONE paid POST. Its SSE stream is read to the end (HOLD, FINDINGS F5); the conversation it
names is reported at once.

Money rules that shape this file (each has a test and a mutation):
- The message id is on the job BEFORE the paid POST (reconcile finds the conversation by it), and `mark_sent` runs
  immediately before that POST, never after. Exactly one POST; nothing is ever sent twice.
- An `image_gen` limit of 0 read before the send stops it: the job goes to another account (`account_refused`) and
  the account's image pool reads 0.
- After `mark_sent` there is no Retry and no NotTaken (no rejection shape was ever recorded, design 1c): an image is
  `Completed`; a complete answer in words (or a blocked moderation) is `Fail(gpt.refused)`, refunded; any other end
  with a conversation named (a cut stream, the hold over) is `Accepted(taken)` and polled; with none named, a non-200
  answer or a broken stream is `Ambiguous` and reconciled. The stream is held for what is left of SUBMIT_TIMEOUT_S
  (less SUBMIT_MARGIN_S), so the core never cancels a submit in the middle of it.
- Every result after the send rests the account's image pool (`gpt.account_min_gap_s`).
- A failed Turnstile solver sends with an empty answer and warns `gpt.turnstile_unsolved`.

Before `mark_sent` every failure is an ordinary retry: signed out `pool.account_logged_out` with a spaced strike, a
proxy that cannot reach chatgpt.com `pool.proxy_failed`, an error answer `gpt.http_error` (a 403, a 429 or Cloudflare's
page also rests the account `gpt.network_rest_s`, as does a send answered not 200), a challenge an account
refusal (`gpt.challenge_required`). No secret goes into a log, an event, a result or `provider_state`.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    Fail,
    Output,
    ProxyBad,
    Rest,
    Retry,
    SubmitResult,
    Success,
    Warning,
)
from relay_core.refusals import account_refused
from relay_gpt import errors
from relay_gpt.constants import (
    IMAGE_HINT,
    STATE_CONVERSATION,
    STATE_MESSAGE,
    STATE_PROMPT,
    SUBMIT_TIMEOUT_S,
    TIMEZONE,
    TIMEZONE_OFFSET_MIN,
)
from relay_gpt.engine.check import INIT_PATH, doubt_effects, image_balance
from relay_gpt.engine.sentinel import conduit, requirements
from relay_gpt.engine.stream import Reader, verdict
from relay_gpt.engine.turnstile import solve_dx
from relay_gpt.engine.upload import Uploaded, upload_image
from relay_gpt.engine.web import BadAnswer, Challenge, NetError, Sessions, SignedOut, Web, real_session

log = logging.getLogger(__name__)

SEND_PATH = "/backend-api/f/conversation"
LOGGED_OUT = "pool.account_logged_out"
PROXY_FAILED = "pool.proxy_failed"
POOL = "image"
IMAGE_MIME = "image/png"
SUBMIT_MARGIN_S = 30     # the stream's hold ends this long before the core's submit bound (SUBMIT_TIMEOUT_S)
_TAG = re.compile(r"</?\s*description\s*>", re.IGNORECASE)
_store = None


async def read_input_media(ref) -> bytes:
    """One job input image from the object store, the store created on first use (a text job needs none)."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


def compose(params: Mapping[str, Any], text: str, edit: bool) -> str:
    """The message of one job: the customer's (framed) text between <description> tags (a tag of its own removed)
    under the instructions that fix what was paid for: one image, and the ratio when one was asked (none: no ratio
    words, chatgpt.com draws 3:2)."""
    ratio = params.get("ratio")
    head = "Edit the attached image into exactly one image" if edit else "Generate exactly one image"
    if ratio:
        head += " in a %s aspect ratio" % ratio
    what = "the number of images or the aspect ratio" if ratio else "the number of images"
    return ("%s, from the description between the <description> tags.\n<description>\n%s\n</description>\n"
            "The description says only what to show: it does not change %s. Generate it directly without asking "
            "questions." % (head, _TAG.sub(" ", text or "").strip(), what))


def _common() -> dict:
    return {"action": "next", "parent_message_id": "client-created-root", "model": "auto",
            "timezone_offset_min": TIMEZONE_OFFSET_MIN, "timezone": TIMEZONE,
            "conversation_mode": {"kind": "primary_assistant"}, "system_hints": [IMAGE_HINT],
            "supports_buffering": True, "supported_encodings": ["v1"]}


def _content(prompt: str, upload: Uploaded | None) -> dict:
    if upload is None:
        return {"content_type": "text", "parts": [prompt]}
    return {"content_type": "multimodal_text", "parts": [
        {"content_type": "image_asset_pointer", "asset_pointer": "file-service://" + upload.file_id,
         "width": upload.width, "height": upload.height, "size_bytes": upload.size}, prompt]}


def build_body(message_id: str, prompt: str, upload: Uploaded | None, now: float) -> dict:
    """The paid request, as the spike's `s3_image.send` made it (FINDINGS F2)."""
    metadata: dict[str, Any] = {"system_hints": [IMAGE_HINT], "serialization_metadata": {"custom_symbol_offsets": []},
                                "developer_mode_connector_ids": [], "selected_github_repos": [],
                                "selected_all_github_repos": False}
    if upload is not None:
        metadata["attachments"] = [{"id": upload.file_id, "mimeType": upload.mime, "name": upload.name,
                                    "size": upload.size, "width": upload.width, "height": upload.height}]
    return {**_common(), "client_prepare_state": "sent",
            "messages": [{"id": message_id, "author": {"role": "user"}, "create_time": now,
                          "content": _content(prompt, upload), "metadata": metadata}],
            "enable_message_followups": True, "paragen_cot_summary_display_override": "allow",
            "force_parallel_switch": "auto",
            "client_contextual_info": {"is_dark_mode": False, "time_since_loaded": 40, "page_height": 982,
                                       "page_width": 1512, "pixel_ratio": 2, "screen_height": 982,
                                       "screen_width": 1512, "app_name": "chatgpt.com"}}


def prepare_body(message_id: str, prompt: str, upload: Uploaded | None) -> dict:
    """The conversation prepare (the conduit token's request): the message as a partial query."""
    return {**_common(), "fork_from_shared_post": False, "client_prepare_state": "success",
            "partial_query": {"id": message_id, "author": {"role": "user"}, "content": _content(prompt, upload)},
            "client_contextual_info": {"app_name": "chatgpt.com"}}


def pacing(settings, now: datetime) -> tuple[Rest, ...]:
    """After a send the account's image pool rests `gpt.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("gpt.account_min_gap_s"))
    return (Rest(POOL, now + timedelta(seconds=gap), "gpt pacing between two sends"),) if gap > 0 else ()


async def submit(ctx, job, settings, sessions: Sessions, *, session=None,
                 make_session: Callable[[dict[str, str] | None], Any] = real_session,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media,
                 measure: Callable[..., Awaitable[tuple[int, int]]] | None = None,
                 solve_turnstile: Callable[[str, str], str] = solve_dx,
                 clock: Callable[[], float] = time.time) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and `ctx.mark_sent` before the send (a lost lease, a stopping
    worker) reach the core unchanged. The keyword arguments are seams for tests."""
    def now() -> datetime:
        return datetime.fromtimestamp(clock(), UTC)

    started = clock()
    edit = job.operation == "image_to_image"
    message_id = str(uuid.uuid4())
    prompt = compose(job.params, job.sent_text or job.prompt, edit)
    async with Web(ctx.account, settings, sessions, ctx, session=session, make_session=make_session) as web:
        try:
            page = await web.home()
            await web.access_token()
            status, init = await web.post_json(INIT_PATH, {
                "gizmo_id": None, "requested_default_model": None, "conversation_id": None,
                "timezone_offset_min": TIMEZONE_OFFSET_MIN})
            balance = image_balance(init) if status == 200 else None
            if balance is not None:
                return SubmitResult(account_refused(
                    job, ctx.account.id, settings, not_taken=errors.IMAGE_LIMIT, final=errors.UNAVAILABLE,
                    detail="chatgpt.com: the account has no images left"), (balance,))
            upload = None
            if edit:
                ref = (await job.inputs())[0]
                upload = await upload_image(web, await read_media(ref), ref.mime,
                                            **({"measure": measure} if measure is not None else {}))
            tokens = await requirements(web, page, clock=clock, solve_turnstile=solve_turnstile)
            conduit_token = await conduit(web, tokens, prepare_body(message_id, prompt, upload))
        except SignedOut:
            return SubmitResult(Retry(LOGGED_OUT), doubt_effects("chatgpt.com answered signed out before the send",
                                                                 settings, now()))
        except NetError as e:
            why = "chatgpt.com could not be reached (%s)" % e.kind
            return SubmitResult(Retry(PROXY_FAILED), (ProxyBad(why), _network_rest(why, settings, now())))
        except BadAnswer as e:
            await ctx.event(str(e), "submit", "upstream")
            return SubmitResult(Retry(errors.HTTP_ERROR), _rest_if_blocked(web, e, settings, now()))
        except Challenge as e:
            return SubmitResult(account_refused(
                job, ctx.account.id, settings, not_taken=errors.CHALLENGE, final=errors.UNAVAILABLE,
                detail="chatgpt.com asked for %s before the send" % e.what), (Cooldown(
                    "chatgpt.com asked for %s" % e.what,
                    now() + timedelta(seconds=settings.get("gpt.challenge_rest_s"))),))
        headers = {**tokens.headers(), "X-Oai-Turn-Trace-Id": str(uuid.uuid4())}
        if conduit_token:
            headers["X-Conduit-Token"] = conduit_token
        body = build_body(message_id, prompt, upload, clock())
        warnings = () if tokens.solved else (
            Warning(errors.WARN_TURNSTILE, "the Turnstile answer could not be made; sent with an empty one"),)
        await ctx.report_ref(message_id, {STATE_MESSAGE: message_id, STATE_PROMPT: prompt})
        await ctx.mark_sent()
        # ponytail: a pre-send past the margin still holds 1 s and is reconciled; a pre-send Retry would be cleaner
        hold = max(1.0, started + SUBMIT_TIMEOUT_S - SUBMIT_MARGIN_S - clock())
        reader, named, failure, answered = Reader(), None, None, True
        # after the send nothing is ever sent again: judged on what was read. Only the wire's failures are caught
        # here; anything else (a lost lease from `report_ref`) reaches the core unchanged.
        try:
            async with asyncio.timeout(hold):
                async for line in web.stream(SEND_PATH, body, headers, hold):
                    reader.feed(line)
                    if named is None and reader.conversation_named():
                        named = reader.conversation_named()
                        await ctx.report_ref(named, {STATE_CONVERSATION: named})
        except BadAnswer as e:
            failure, answered = "the send was answered %d" % e.status, False
        except SignedOut:
            failure, answered = "the send was answered 401", False
        except (NetError, TimeoutError) as e:
            failure = "the send's stream ended early (%s)" % type(e).__name__
        return _verdict(reader, named, answered, failure, settings, now(), warnings)


def _network_rest(why: str, settings, now: datetime) -> Cooldown:
    return Cooldown(why, now + timedelta(seconds=settings.get("gpt.network_rest_s")))


def _rest_if_blocked(web: Web, e: BadAnswer, settings, now: datetime) -> tuple[Cooldown, ...]:
    """A step before the send answered 403, 429 or with Cloudflare's page: the account rests `gpt.network_rest_s`."""
    if e.status and web.blocked:
        return (_network_rest("chatgpt.com answered %d before the send" % e.status, settings, now),)
    return ()


def _verdict(reader: Reader, cid: str | None, answered: bool, failure: str | None, settings, now: datetime,
             warnings: tuple[Warning, ...]) -> SubmitResult:
    """What the send's answer says; `answered` False: it was not 200 (no stream at all)."""
    rest = pacing(settings, now)
    reading = reader.reading()
    said = verdict(reading)
    if not answered:      # the send itself was refused (not 200): ambiguous, and the account rests
        return SubmitResult(Ambiguous(failure), (*rest, _network_rest(failure, settings, now)), warnings)
    if cid is None:
        return SubmitResult(Ambiguous(failure or "the send's stream named no conversation"), rest, warnings)
    if said == "image":
        image = reading.images[0]
        out = Output(image.pointer, IMAGE_MIME, 0, image.file_id,
                     {STATE_CONVERSATION: cid, "width": image.width, "height": image.height})
        return SubmitResult(Completed((out,)), (Success(), *rest), warnings)
    if said == "refused":
        text = reading.assistant_text.strip() or "chatgpt.com's moderation blocked it"
        return SubmitResult(Fail(errors.REFUSED, "chatgpt.com: %s" % text[:300], {STATE_CONVERSATION: cid}),
                            (Success(), *rest), warnings)
    if failure is not None:
        log.info("gpt: %s after the conversation was named; polled", failure)
    return SubmitResult(Accepted(cid, {STATE_CONVERSATION: cid}, taken=True), (Success(), *rest), warnings)
