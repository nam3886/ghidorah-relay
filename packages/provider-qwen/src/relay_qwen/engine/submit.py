"""Submit: one job to qwen (`POST /api/v2/chat/completions`), and what qwen said as a `SubmitResult`.

The steps, all free and all before the send: the access token (refreshed when old); a minted anti-bot pair younger than
`qwen.bx_max_age_s` (`Minter.fresh`); for a video, qwen's own credit check (`credits/verify`); the input image, if any
(`upload.upload_image`); a new empty chat (`chats/new`). Then the chat id is written to the job (`ctx.report_ref`),
`ctx.mark_sent()`, and the ONE paid POST into that chat. An image is drawn inside that POST's stream (HOLD:
`Completed`); a video POST answers its task id at once (DETACH: `Accepted`, then polled).

Money rules that shape this file (each has a test and a mutation):
- The chat id is on the job BEFORE the paid POST: qwen keeps no id of ours, so the chat is how a lost answer is read
  back (reconcile). One chat per job, and exactly one POST into it.
- `ctx.mark_sent()` runs immediately before that POST, never after.
- Nothing is ever sent twice. After `mark_sent` only two answers end in something other than `Ambiguous`/`Completed`/
  `Accepted`: qwen's own refusal and its "Insufficient Credits" (`Fail`, refunded), and the Aliyun WAF's edge block
  of the pair, which never reached qwen's app. That one is `NotTaken` (the job goes back to the queue, the stale pair
  is dropped so the next send mints anew) ONLY when the answer is that block exactly, as measured (`is_waf_block`:
  status, the WAF's own headers and cookie, none of the app's, the exact body); a chat read is no proof and is not
  used. Anything that only looks like it is `Ambiguous`.
- Any other answer (no answer, a timeout, a cut stream with no image, a 5xx, a shape not seen) is `Ambiguous`: the
  job becomes unknown and the reconciler reads the chat; it is never sent again.

Before `mark_sent` every failure is an ordinary retry: a signed-out refresh `pool.account_logged_out` with a spaced
strike, no credits `pool.account_out_of_points`, a proxy that cannot reach qwen `pool.proxy_failed`, an error answer
`qwen.http_error`, no pair `qwen.mint_failed`.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Fail,
    NotTaken,
    Output,
    ProxyBad,
    Rest,
    Retry,
    SubmitResult,
    Success,
)
from relay_qwen import errors
from relay_qwen.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    CHAT_MODEL,
    IMAGE_READ_TIMEOUT_S,
    IMAGE_STREAM_TIMEOUT_S,
    NETWORK_REST_S,
    VIDEO_SEND_TIMEOUT_S,
)
from relay_qwen.engine.check import doubt_effects
from relay_qwen.engine.http import (
    BadAnswer,
    Sessions,
    SignedOut,
    Site,
    media_link,
    task_of,
    text_of,
)
from relay_qwen.engine.mint import Minter, MintError, drop_pair
from relay_qwen.engine.upload import upload_image

COMPLETIONS = "/api/v2/chat/completions?chat_id=%s"
CHAT_KEY = "chat"            # provider_state: the job's chat id (also its external_ref until a video has a task)
TASK_KEY = "task"            # provider_state: the video's task id
LOGGED_OUT = "pool.account_logged_out"
OUT_OF_POINTS = "pool.account_out_of_points"
PROXY_FAILED = "pool.proxy_failed"
POOLS = ("video", "image")
IMAGE_MIME = "image/png"
# The Aliyun WAF edge block (fixtures waf_plain_no_bx, waf_replayed_bx_other_body): never an answer of qwen's app.
WAF_RET = "FAIL_SYS_USER_VALIDATE"
WAF_RET_DETAIL = "RGV587_ERROR::"
WAF_PUNISH = "/_____tmd_____/punish?"
WAF_COOKIE = "x5secdata"
APP_HEADERS = ("x-request-id", "x-actual-status-code")     # qwen's app sets both on every answer it makes
FEATURES = {"thinking_enabled": False, "output_schema": "phase", "research_mode": "normal", "auto_thinking": False,
            "thinking_mode": "Fast", "auto_search": True}
_store = None


async def read_input_media(ref) -> bytes:
    """One job input image from the object store, the store created on first use (a text job needs none)."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def pacing(settings, now: datetime) -> tuple[Rest, ...]:
    """After a generate the account's pools rest `qwen.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("qwen.account_min_gap_s"))
    return tuple(Rest(pool, now + timedelta(seconds=gap), "qwen pacing between two generates")
                 for pool in POOLS) if gap > 0 else ()


def chat_type(job) -> str:
    """qwen's chat type: an image edit is a text-to-image chat with one file (the web app no longer says
    `image_edit`); a video is t2v or i2v."""
    return {"text_to_video": "t2v", "image_to_video": "i2v"}.get(job.operation, "t2i")


def _text(job) -> str:
    """The core's framed text; a job queued before qwen had frames carries the core frame's and goes as it went."""
    return job.prompt if job.frame_id is None else job.sent_text


def build_body(job, chat: str, prompt: str, files: list[dict], now_s: int) -> dict:
    """The paid request, as the web app builds it (FINDINGS 5)."""
    p, kind, ctype = job.params, job.params["kind"], chat_type(job)
    if kind == "image":
        sub = "t2i"
        meta: dict[str, Any] = {"subChatType": sub, "size": p["ratio"], "model": p["model"]}
    else:
        sub = "zaodian_generate_video"
        params: dict[str, Any] = {"resolution": p["resolution"], "size": p["ratio"], "duration": p["duration"],
                                  "audio": True}
        if files:
            params["attachments"] = [{"type": "image", "materialId": f["id"]} for f in files]
        meta = {"size": p["ratio"], "subChatType": sub, "videoGenerationMode": "standard",
                "biz_data": json.dumps({"bizScene": "genVideo", "req": {"rootModel": p["model"], "genMode": "vid_gen",
                                                                         "params": params}}, separators=(",", ":"))}
    message = {"id": None, "fid": str(uuid.uuid4()), "parentId": None, "childrenIds": [str(uuid.uuid4())],
               "role": "user", "content": prompt, "user_action": "chat", "files": files, "timestamp": now_s,
               "models": [CHAT_MODEL], "model": "", "chat_type": ctype, "feature_config": dict(FEATURES),
               "extra": {"meta": meta}, "sub_chat_type": sub, "parent_id": None}
    return {"stream": kind == "image", "version": "2.1", "incremental_output": True, "chatId": chat, "parentId": "",
            "chat_id": chat, "chat_mode": "normal", "model": CHAT_MODEL, "parent_id": None, "messages": [message],
            "timestamp": now_s, "size": p["ratio"]}


# ---- what an answer says (pure) --------------------------------------------------------------------------------------
def _obj(text: str) -> dict | None:
    try:
        body = json.loads(text)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def is_waf_block(status: int, headers: httpx.Headers, text: str) -> bool:
    """The Aliyun WAF's edge block of the request, matched exactly as measured twice (fixtures waf_*): HTTP 200,
    `bxpunish: 1` and a `bxuuid`, a JSON content type, an `x5secdata` cookie set, none of the headers qwen's app puts on
    its answers, and a body that is exactly `{"ret": ["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR::..."], "data": {"url":
    ".../_____tmd_____/punish?..."}}`. Anything else, however close, is not it."""
    if status != 200 or headers.get("bxpunish") != "1" or not headers.get("bxuuid"):
        return False
    if any(h in headers for h in APP_HEADERS) or not headers.get("content-type", "").startswith("application/json"):
        return False
    if WAF_COOKIE not in {c.split("=", 1)[0].strip() for c in headers.get_list("set-cookie")}:
        return False
    body = _obj(text.strip())
    if body is None or set(body) != {"ret", "data"}:
        return False
    ret, data = body["ret"], body["data"]
    return (isinstance(ret, list) and len(ret) == 2 and ret[0] == WAF_RET and isinstance(ret[1], str)
            and ret[1].startswith(WAF_RET_DETAIL) and isinstance(data, dict) and set(data) == {"url"}
            and isinstance(data["url"], str) and WAF_PUNISH in data["url"])


def _first_message(body: dict) -> dict:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    found = data.get("messages")
    return found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else {}


def json_verdict(status: int, body: dict | None) -> tuple[str, Any]:
    """A JSON answer to the paid POST: ("task", id) | ("no_credits", text) | ("refused", text) | ("unclear", note)."""
    if status != 200 or body is None or body.get("success") is not True:
        return "unclear", "qwen answered %d%s" % (status, "" if body is None else " (success %r, code %s)" % (
            body.get("success"), str((body.get("data") or {}).get("code") if isinstance(body.get("data"), dict)
                                     else None)[:40]))
    msg = _first_message(body)
    extra = msg.get("extra") if isinstance(msg.get("extra"), dict) else {}
    task, words = task_of(msg), text_of(msg)
    if task:
        return "task", task
    if extra.get("quota_limit") is True:
        return "no_credits", words
    if msg.get("role") == "assistant" and words and "wanx" not in extra:
        return "refused", words
    return "unclear", "qwen answered with no task and no reason"


def _events(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        if line.startswith("data:"):
            ev = _obj(line[5:].strip())
            if ev is not None:
                out.append(ev)
    return out


def stream_verdict(status: int, lines: list[str], cut: bool, headers: httpx.Headers) -> tuple[str, Any]:
    """The image POST's answer: ("images", [links]) | ("waf", None) | ("no_credits", text) | ("refused", text) |
    ("unclear", note). An image link anywhere counts, even in a stream cut after it; a stream that ended cleanly
    with words and no image is qwen refusing; nothing else is final."""
    text = "\n".join(lines)
    if not cut and is_waf_block(status, headers, text):
        return "waf", None
    if not any(line.startswith("data:") for line in lines):
        verdict = json_verdict(status, None if cut else _obj(text.strip()))
        return ("unclear", verdict[1]) if verdict[0] == "task" else verdict    # an image job never has a task
    links, words, finished = [], [], False
    for ev in _events(lines):
        for choice in ev.get("choices") or []:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if not isinstance(delta, dict):
                continue
            content = delta.get("content") if isinstance(delta.get("content"), str) else ""
            if delta.get("phase") == "image_gen" and media_link(content):
                links.append(content)
            elif content:
                words.append(content)
            if delta.get("status") == "finished":
                finished = True
    if links:
        return "images", links
    if status == 200 and finished and not cut:
        return "refused", "".join(words) or "qwen ended the image with no image"
    return "unclear", "the image stream %s with no image (HTTP %d)" % ("was cut" if cut else "ended", status)


# ---- the send ----------------------------------------------------------------------------------------------------
async def _post_image(site: Site, url: str, body: dict, headers: dict
                      ) -> tuple[int, list[str], bool, httpx.Headers]:
    """The image POST, read to its end: (status, lines, cut, answer headers). A stream that broke or ran out of time
    is `cut`."""
    status, lines, got = 0, [], httpx.Headers()
    try:
        async with asyncio.timeout(IMAGE_STREAM_TIMEOUT_S):
            async with site.client.stream("POST", url, json=body, headers=headers, timeout=httpx.Timeout(
                    API_TIMEOUT_S, read=IMAGE_READ_TIMEOUT_S)) as resp:
                status, got = resp.status_code, resp.headers
                async for line in resp.aiter_lines():
                    if line.strip():
                        lines.append(line)
    except (httpx.HTTPError, TimeoutError):
        return status, lines, True, got
    return status, lines, False, got


async def submit(ctx, job, settings, sessions: Sessions, minter: Minter, *, transport=None,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.time) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and `ctx.mark_sent` (a lost lease, a stopping worker) reach the
    core unchanged. The keyword arguments are seams for tests."""
    kind = job.params["kind"]
    pool = kind
    async with Site(ctx, sessions, transport=transport, clock=clock) as site:
        try:
            await site.token()
            cred = await minter.fresh(ctx, sessions, int(settings.get("qwen.bx_max_age_s")))
            site.adopt(cred)
            if kind == "video":
                status, body = await site.call("POST", "/api/v2/entitlement/credits/verify", {
                    "chat_type": chat_type(job), "task_info": {"create_credits": job.params["credits"]}})
                data = body.get("data") if isinstance(body.get("data"), dict) else {}
                if status != 200 or body.get("success") is not True:
                    return _result(Retry(errors.HTTP_ERROR))
                if data.get("access") is not True:
                    return _result(Retry(OUT_OF_POINTS), Balance(pool, 0))
            files = []
            if job.operation in ("image_to_image", "image_to_video"):
                ref = (await job.inputs())[0]
                files = [await upload_image(site, await read_media(ref), ref.mime)]
            chat = await site.new_chat(chat_type(job), CHAT_MODEL)
        except SignedOut as e:
            return _result(Retry(LOGGED_OUT), *doubt_effects("not signed in: %s" % e, now()))
        except MintError as e:
            await ctx.event("no anti-bot pair: %s" % e, "submit", "engine")
            return _result(Retry(errors.MINT_FAILED))
        except BadAnswer as e:
            await ctx.event(str(e), "submit", "upstream")
            return _result(Retry(errors.HTTP_ERROR))
        except httpx.HTTPError as e:
            why = "qwen could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
            return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
        body = build_body(job, chat, _text(job), files, int(clock()))
        url = BASE_URL + COMPLETIONS % chat
        headers = site.headers("/c/" + chat, bx=True)
        await ctx.report_ref(chat, {CHAT_KEY: chat})       # write-ahead: the chat is on the job first
        await ctx.mark_sent()                               # then marked sent, immediately before the POST
        if kind == "image":
            status, lines, cut, answered = await _post_image(site, url, body, headers)
            verdict, said = stream_verdict(status, lines, cut, answered)
        else:
            try:
                resp = await site.client.post(url, json=body, headers=headers, timeout=VIDEO_SEND_TIMEOUT_S)
            except httpx.HTTPError as e:
                return _result(Ambiguous("the video send got no answer (%s)" % type(e).__name__),
                               *pacing(settings, now()))
            if is_waf_block(resp.status_code, resp.headers, resp.text):
                verdict, said = "waf", None
            else:
                verdict, said = json_verdict(resp.status_code, _obj(resp.text))
        if verdict == "waf":
            return await _waf(ctx, sessions, chat, cred.minted_at)
        rest = pacing(settings, now())
        if verdict == "images":
            outs = tuple(Output(link, IMAGE_MIME, i, None, {CHAT_KEY: chat}) for i, link in enumerate(said))
            return _result(Completed(outs), Success(), *rest)
        if verdict == "task":
            return _result(Accepted(said, {CHAT_KEY: chat, TASK_KEY: said}, taken=True), Success(), *rest)
        if verdict == "no_credits":
            return _result(Fail(errors.OUT_OF_CREDITS, "qwen: %s" % str(said)[:300], {CHAT_KEY: chat}),
                           Balance(pool, 0), *rest)
        if verdict == "refused":
            return _result(Fail(errors.REFUSED, "qwen: %s" % str(said)[:300], {CHAT_KEY: chat}), *rest)
        return _result(Ambiguous(str(said)), *rest)


async def _waf(ctx, sessions: Sessions, chat: str, minted_at: float) -> SubmitResult:
    """The WAF's edge block: the request never reached qwen's app. The stale pair is dropped, the job requeued."""
    await drop_pair(ctx, sessions, minted_at)
    await ctx.event("qwen's firewall blocked the send at its edge (the anti-bot pair); a new pair will be minted",
                    "submit", "upstream")
    return _result(NotTaken(errors.WAF_REFUSED, {"waf_chat": chat}))
