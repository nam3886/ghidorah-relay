"""Submit over plain HTTP (`dola.send_path` = http): one signed `POST /chat/completion`, no browser.

What dola's page would send, built from the 23/10 spike (FINDINGS.md "HTTP send spike 23/10", fixtures under
`fixtures/dola/api-spike-261023/`): the query in its exact order, the headers, the body (`client_meta`, `messages`,
`option`, `chat_ability`, `ext`), signed with `a_bogus` (`relay_dola.signing`). Everything after the send is the
browser path's own code (`submit._Run`): the reply is read over HTTP and judged by `judge()`, images finish in the
session or by the poll, a video by the poll.

Money rules that shape this file (each has a test and a mutation):
- Every client id (`local_conversation_id`, `local_message_id`, `unique_key`, `collect_id`) is made and written to the
  job (`ctx.report_ref`, `provider_state`) BEFORE `ctx.mark_sent()`: an answer that is lost is looked up by them.
- `ctx.mark_sent()` runs immediately before the ONE POST. Nothing here sends it again, ever: there is no re-send of a
  gated reply over HTTP (a NEEDS_CONFIRM stays unclear and the poll decides).
- `SSE_ACK` with a `conversation_id` = dola has the order (the conversation is kept; the reply is judged as on the
  browser path). `STREAM_ERROR` before any `SSE_ACK` = not taken (dola made no conversation: checked on every spike
  refusal). Anything else after `mark_sent` (a drop, a timeout, an HTTP error, a stream that says nothing) is
  `Ambiguous`: reconcile looks for the job's `local_message_id`, never a second POST.
- dola reports a BAD SIGNATURE with its throttle codes (710022002, 710022004 + a `verify` decision). `SignerAlarm`
  tells that apart from a real refusal: a `verify` decision, or several accounts refused within a window, blames our
  signer: no account rests, no job fails as refused, and HTTP sends pause for the window (loud: an ERROR log line,
  the job's timeline, the code `dola.signer_suspect`).

Before `mark_sent` a failure is an ordinary retry under the same setting (the job is never moved to the browser path
here): a proxy that cannot reach dola `pool.proxy_failed`, a guest login read `pool.account_logged_out`, the rest
(bootstrap, signer) `dola.http_setup_failed`.
"""

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import urlencode

from relay_core.providers import Accepted, NotTaken, Retry, Success
from relay_dola import credential, errors
from relay_dola.catalog import DEFAULT_RATIO, IMAGE_MODEL_LABELS
from relay_dola.engine import resolver
from relay_dola.engine.http import BASE, DolaHttp, query_params
from relay_dola.engine.submit import (
    COMPLETION_PATH,
    POOL_LOGGED_OUT,
    POOL_PROXY_FAILED,
    REFUSED_CODE,
    REGION_CODE,
    THROTTLE_CODES,
    StreamError,
    _error_envelope,
    _outcome,
    _Run,
    sse_events,
    stream_error,
)
from relay_dola.engine.verify import login_doubt_effects

log = logging.getLogger("relay_dola.send_http")

SEND_PATH_SETTING = "dola.send_path"
SEND_PATH_KEY = "send_path"                 # provider_state: "http" on a job sent this way
ID_KEYS = ("local_conversation_id", "local_message_id", "unique_key", "collect_id")
REPLAY_CODE = 710020702                     # dola saw these client ids before ("is_replay"); we never re-send
SEND_TIMEOUT_S = 180                        # the whole stream; images end in-stream at ~20 s, a video's at ~10 s
SESSION_TTL_S = 6 * 3600                    # a cached web id + bot id is fetched again after this
FP_COOKIE = "s_v_web_id"                    # `ext.fp` of the body

# The browser the signature claims to be: the box's Chrome 154 on Linux. `Fingerprint.user_agent` IS this header.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 "
              "Safari/537.36")
SEC_CH_UA = '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"'
IMAGE_REPLY_PREFIX = "Generate image: "
LAUNCH_BODY = {"select": {"launch_config": True, "assistant_bot_info": True, "landing_config": True,
                          "user_info": True}}


class Unreachable(Exception):
    """dola could not be reached (the transport raised). Its text is the error's type only: a proxy URL never leaks."""


class SetupError(Exception):
    """dola answered, but not what a send needs (a bad bootstrap answer, no fingerprint cookie, a signer error)."""


class SignerOutdated(Exception):
    """dola's page loads another `bdms` SDK than the one the signer was written from: its format may have changed."""


# ---- what goes on the wire -------------------------------------------------------------------------------------------
def base_query(web_id: str) -> list[tuple[str, str]]:
    """dola's web query in the page's exact order (the signature binds the query string as sent)."""
    return [("version_code", "20800"), ("language", "en"), ("device_platform", "web"),
            ("doubao_device_platform", "web"), ("aid", "495671"), ("real_aid", "495671"),
            ("pkg_type", "release_version"), ("device_id", ""), ("pc_version", "3.42.0"),
            ("doubao_pc_version", "3.42.0"), ("web_id", web_id), ("tea_uuid", web_id), ("region", "VN"),
            ("sys_region", "VN"), ("samantha_web", "1"), ("web_platform", "browser"), ("use-olympus-account", "1"),
            ("web_tab_id", str(uuid.uuid4()))]


def send_query(web_id: str) -> list[tuple[str, str]]:
    return base_query(web_id) + [("tz_name", "Asia/Ho_Chi_Minh")]


def anon_query() -> list[tuple[str, str]]:
    """`get_web_anon_id` is asked before there is a web id: no web id, empty regions (as the page asks it)."""
    drop = ("web_id", "tea_uuid", "region", "sys_region")
    return [(k, v) for k, v in base_query("") if k not in drop] + [("region", ""), ("sys_region", "")]


def headers(accept: str) -> dict[str, str]:
    """The page's headers. The cookie is added by `DolaHttp`; the content type is plain JSON (not the `/im/*` one)."""
    return {"accept": accept, "accept-language": "en-US", "agw-js-conv": "str", "content-type": "application/json",
            "origin": BASE, "referer": BASE + "/chat", "sec-ch-ua": SEC_CH_UA, "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"', "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin", "user-agent": USER_AGENT}


def new_ids(rng: random.Random | None = None) -> dict[str, str]:
    """The client ids of one send, made before anything is sent (the write-ahead)."""
    rng = rng or random.SystemRandom()
    return {"local_conversation_id": "local_%d" % rng.randint(10 ** 15, 10 ** 16 - 1),
            "local_message_id": str(uuid.uuid4()), "unique_key": str(uuid.uuid4()), "collect_id": str(uuid.uuid4())}


def _compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def chat_ability(spec) -> tuple[dict, str]:
    """(`chat_ability`, the message text) of an order. Each shape is the one a live send delivered (23/10, d23-05):

    - Image: the page's own form, `reply_message_format` filled with the typed text, then the ratio:
      "Generate image: <text>, 16:9", and the page config's selector key `image_creation_ratio` beside the model.
      Delivered: Seedream 4.5 no ratio 2048x2048, 4.5 16:9 2848x1600, 5.0 Pro 9:16 1600x2848. Without the text tail
      the key alone was ignored (16:9 asked, 2048x2048 made). A style is not built (`browser_only`).
    - Video: the bare text and the parameters in `ability_param` (spike and live check: Seedance 2.0, 5 s, 16:9).
      The ratio is NOT honoured this way (9:16 asked: dola stored it, replied "16:9", made 1280x720), so a video in
      another ratio goes by browser (`browser_only`). The browser's own form ("Generated video: <text>, 9:16" plus
      `reply_message_format`, fixtures/dola/chains) was sent twice on Seedance 2.5 (9:16, 21:9) and both times dola
      answered "I can't generate the requested content." (the account then had no video credit left that day: the
      cause, form, model or credit, is not known), so it is not used. UNPROVEN: `duration` other than 5,
      `seedance_v1.0`."""
    text = spec.sent_text
    if spec.kind == "image":
        inner = {"model": IMAGE_MODEL_LABELS[spec.model]}
        if spec.ratio:
            inner["image_creation_ratio"] = spec.ratio
        inner["input_box_content"] = {"user_input_content": text,
                                      "reply_message_format": IMAGE_REPLY_PREFIX + "%s"}
        said = IMAGE_REPLY_PREFIX + text + (", " + spec.ratio if spec.ratio else "")
        return {"ability_type": 3, "ability_param": _compact({"ability_param": inner, "ability_type": 1})}, said
    param = {"ratio": spec.ratio, "model": spec.model, "duration": spec.duration,
             "input_box_content": {"user_input_content": text}}
    return {"ability_type": 17, "ability_param": _compact(param)}, text


def build_body(spec, ids: dict[str, str], bot_id: str, fp: str, now_ms: int) -> bytes:
    """The exact bytes of the send (compact JSON: what the signature hashes)."""
    ability, text = chat_ability(spec)
    body = {
        "client_meta": {"local_conversation_id": ids["local_conversation_id"], "conversation_id": "",
                        "bot_id": bot_id, "last_section_id": "", "last_message_index": None,
                        "local_permissions": []},
        "messages": [{"local_message_id": ids["local_message_id"], "content_block": [{
            "block_type": 10000, "content": {"text_block": {"text": text, "icon_url": "", "icon_url_dark": "",
                                                            "summary": ""}, "pc_event_block": ""},
            "block_id": str(uuid.uuid4()), "parent_id": "", "meta_info": [], "append_fields": []}],
            "message_status": 0}],
        "option": {"send_message_scene": "", "create_time_ms": now_ms, "collect_id": ids["collect_id"],
                   "is_audio": False, "answer_with_suggest": False, "tts_switch": False, "need_deep_think": 0,
                   "click_clear_context": False, "from_suggest": False, "is_regen": False, "is_replace": False,
                   "is_from_click_option": False, "is_from_click_softlink": False, "disable_sse_cache": False,
                   "select_text_action": "", "is_select_text": False, "resend_for_regen": False, "scene_type": 0,
                   "unique_key": ids["unique_key"], "start_seq": 0, "need_create_conversation": True,
                   "conversation_init_option": {"need_ack_conversation": True}, "regen_query_id": [],
                   "edit_query_id": [], "regen_instruction": "", "no_replace_for_regen": False, "message_from": 0,
                   "shared_app_name": "", "shared_app_id": "",
                   "sse_recv_event_options": {"support_chunk_delta": True}, "is_ai_playground": False,
                   "is_old_user": False,
                   "recovery_option": {"is_recovery": False, "req_create_time_sec": now_ms // 1000,
                                       "append_sse_event_scene": 0},
                   "message_storage_type": 0, "related_deleted_message_ids": {}, "connector_info_list": [],
                   "model_config": {"model_item_key": "", "model_extra_params": {}},
                   "aggregate_params": {"conversation_mode": "", "mode_id": "", "model_item_key": "",
                                        "agent_mode": "", "reasoning_effort": "", "provider_id": ""}},
        "chat_ability": ability,
        "user_context": [],
        "ext": {"fp": fp, "collection_id": ids["collect_id"], "commerce_credit_config_enable": "0",
                "use_deep_think": "0", "sub_conv_firstmet_type": "1",
                "conversation_init_option": '{"need_ack_conversation":true}', "is_finish": "1"},
    }
    return _compact(body).encode("utf-8")


# ---- the stream ------------------------------------------------------------------------------------------------------
class SendStream(NamedTuple):
    conversation: str | None        # `SSE_ACK`'s `ack_client_meta.conversation_id`: dola has the order
    early: StreamError | None       # the first `STREAM_ERROR` BEFORE any `SSE_ACK`
    verify: bool                    # that error carried dola's `verify` decision (its captcha: see SignerAlarm)


def read_send_stream(lines: list[str]) -> SendStream:
    """What the stream of the ONE send says, read event by event. Unreadable data proves nothing either way."""
    conversation, early, verify = None, None, False
    for name, data in sse_events("\n".join(lines)):
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if name == "SSE_ACK" and isinstance(obj, dict):
            cid = str((obj.get("ack_client_meta") or {}).get("conversation_id") or "")
            if cid.isdigit() and cid != "0":
                return SendStream(cid, early, verify)
            continue
        env = _error_envelope(obj)
        if env is not None and early is None:
            said = env.get("error_msg")
            early = StreamError(int(env["error_code"]), " ".join(said.split())[:200] if isinstance(said, str) else "")
            verify = _decision(env) == "verify"
    return SendStream(conversation, early, verify)


def _decision(env: dict) -> str:
    """The `type` of dola's `extra.decision` (a JSON string), "" when there is none."""
    extra = env.get("extra")
    raw = extra.get("decision") if isinstance(extra, dict) else None
    try:
        decision = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return ""
    return str(decision.get("type") or "") if isinstance(decision, dict) else ""


# ---- the signer alarm ------------------------------------------------------------------------------------------------
class SignerAlarm:
    """Is OUR signature what dola refuses? A bad `a_bogus` comes back as 710022002 or 710022004 (measured 23/10), so on
    its own a refusal cannot tell. It trips on dola's `verify` decision (what a missing signature got), or when
    `accounts` different accounts were refused before an acknowledgement within `window_s` with no send acknowledged
    in between. Tripped, it pauses every HTTP send of this worker for `window_s`.

    ponytail: process-local (one dola worker); a second worker process would keep its own count."""

    def __init__(self) -> None:
        self.refused: dict[str, float] = {}     # account id -> when it was last refused before an ack
        self.paused_until = 0.0

    def paused(self, now: float) -> bool:
        return now < self.paused_until

    def acknowledged(self) -> None:
        """A send went through: the signer works, the refusals so far were dola's own."""
        self.refused.clear()

    def note(self, account: str, verify: bool, now: float, accounts: int, window_s: float) -> str | None:
        """Record one refusal before an ack; the reason when it trips (the caller blames the signer), else None."""
        self.refused = {a: t for a, t in self.refused.items() if now - t < window_s}
        self.refused[account] = now
        if verify:
            why = "dola asked for a captcha (710022004 with a verify decision)"
        elif len(self.refused) >= accounts:
            why = "%d accounts refused before an acknowledgement within %d s" % (len(self.refused), window_s)
        else:
            return None
        self.trip(now, window_s)
        return why

    def trip(self, now: float, window_s: float) -> None:
        self.refused.clear()
        self.paused_until = now + window_s


ALARM = SignerAlarm()


def _alarm_bounds(settings) -> tuple[int, int]:
    accounts = settings.get("dola.signer_alarm_accounts") if settings is not None else None
    window = settings.get("dola.signer_alarm_window_s") if settings is not None else None
    return (accounts if isinstance(accounts, int) else 3), (window if isinstance(window, int) else 900)


# ---- the session: web id, bot id, and the account's ONE signer -------------------------------------------------
@dataclass
class Session:
    web_id: str
    bot_id: str
    signer: object          # relay_dola.signing.Signer: kept per account, so the paid send is never its first signature
    at: float


_SESSIONS: dict[tuple[str, int], Session] = {}
_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}     # one bootstrap at a time per account


def make_signer(user_agent: str):
    from relay_dola import signing
    return signing.Signer(signing.Fingerprint(user_agent=user_agent))


def _json(resp) -> dict:
    if resp.status_code != 200:
        raise SetupError("HTTP %s" % resp.status_code)
    try:
        body = resp.json()
    except (ValueError, TypeError):
        raise SetupError("not JSON") from None
    if not isinstance(body, dict) or body.get("code") not in (0, None):
        raise SetupError("dola code %s" % (body.get("code") if isinstance(body, dict) else "?"))
    return body


async def _call(coro):
    try:
        return await coro
    except Exception as exc:      # noqa: BLE001 - the transport: the proxy or the network, never named beyond its type
        raise Unreachable(type(exc).__name__) from exc


async def bootstrap(http: DolaHttp, signer) -> tuple[str, str]:
    """(web id, bot id) of a new session: `get_web_anon_id` (unsigned), then `/alice/user/launch` SIGNED with the
    account's signer. That read is not checked by dola (spike: no/bad/good signature all `code 0`) and takes the
    signer's first signature (detect 14, the page's first call): the paid send is then signed like a page's later call
    (detect 8, the only value proven on `/chat/completion`)."""
    anon = _json(await _call(http.post_data("/alice/user/get_web_anon_id", urlencode(anon_query()), b"{}",
                                            headers("application/json, text/plain, */*"))))
    web_id = str(anon.get("web_id") or "")
    if not web_id.isdigit():
        raise SetupError("get_web_anon_id gave no web id")
    body = _compact(LAUNCH_BODY).encode()
    query = signer.signed_query(base_query(web_id), body)
    launch = _json(await _call(http.post_data("/alice/user/launch", query, body,
                                              headers("application/json, text/plain, */*"))))
    bot_id = str((launch.get("data") or {}).get("assistant_bot_id") or "")
    if not bot_id.isdigit():
        raise SetupError("launch gave no assistant bot id")
    return web_id, bot_id


async def sdk_check(http: DolaHttp) -> None:
    """dola's `/chat` page through the account's proxy: raises `SignerOutdated` when it loads another `bdms` SDK than
    the one the signer was written from (`signing.bdms_changed`). Run at each session bootstrap, not per send."""
    from relay_dola import signing
    resp = await _call(http.get_with_cookie(BASE + "/chat", 60))
    if resp.status_code != 200:
        raise SetupError("the chat page answered HTTP %s" % resp.status_code)
    changed = signing.bdms_changed(bytes(resp.content or b"").decode("utf-8", "replace"))
    if changed is not None:
        raise SignerOutdated("dola's page now loads %s, the signer knows %s" % (changed, signing.BDMS_SDK_FILE))


async def session_for(account, http: DolaHttp, now: float, signer_factory=make_signer) -> Session:
    """The account's cached session, made again after SESSION_TTL_S or a new credential. A new one first checks the
    page's `bdms` SDK. The account keeps ONE signer for as long as its credential: its first signature goes to the
    bootstrap read, never to a send. Signing runs on the event loop only (never in a thread), so two jobs of one
    account cannot share its first signature."""
    key = (account.id, account.credential_gen)
    async with _LOCKS.setdefault(key, asyncio.Lock()):
        got = _SESSIONS.get(key)
        if got is not None and now - got.at < SESSION_TTL_S:
            return got
        await sdk_check(http)
        signer = got.signer if got is not None else signer_factory(USER_AGENT)
        web_id, bot_id = await bootstrap(http, signer)
        got = _SESSIONS[key] = Session(web_id, bot_id, signer, now)
        return got


async def login_state(http: DolaHttp) -> str:
    """`user`, `guest`, or raises: dola's passport answer for this cookie (the read the browser path trusts too)."""
    resp = await _call(http.get_with_cookie(BASE + "/passport/account/info/v2/?" + urlencode(query_params()), 30))
    if resp.status_code != 200:
        raise SetupError("passport HTTP %s" % resp.status_code)
    try:
        body = resp.json()
    except (ValueError, TypeError):
        raise SetupError("passport answer is not JSON") from None
    if not isinstance(body, dict) or "message" not in body:
        raise SetupError("passport answer has no message")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    return "user" if body.get("message") == "success" and (data.get("user_id_str") or data.get("user_id")) \
        else "guest"


class _HttpReads:
    """The one page action the shared code calls after the send, `read_chain`, done over HTTP."""

    def __init__(self, http: DolaHttp):
        self.http = http

    async def read_chain(self, _page, conversation: str) -> dict | None:
        try:
            return await resolver.fetch_chain(self.http, conversation)
        except resolver.ChainError:
            return None


# ---- the run ---------------------------------------------------------------------------------------------------------
class HttpRun(_Run):
    """The browser run's judgement and results, with the page replaced by HTTP. No re-send exists here."""

    async def run(self):
        ctx, spec, deps = self.ctx, self.spec, self.deps
        accounts, window = _alarm_bounds(self.settings)
        if ALARM.paused(deps.clock()):
            return await self._unsent(errors.SIGNER_SUSPECT, "HTTP sends are paused: dola's refusals pointed at "
                                      "relay's request signature (%s)" % errors.SIGNER_SUSPECT)
        http = deps.http(ctx.account)
        self.driver = _HttpReads(http)
        self.steps.append("send path: http")
        state = await login_state(http)
        self.steps.append("login (passport over HTTP): %s" % state)
        if state != "user":
            return _outcome(Retry(POOL_LOGGED_OUT), *login_doubt_effects(deps.now(), "passport read: guest"))
        self.logged_in = True
        session = await session_for(ctx.account, http, deps.clock(), deps.signer)
        cookies = {c["name"]: c["value"] for c in credential.jar_from_blob(ctx.account.credential)}
        if not cookies.get(FP_COOKIE):
            raise SetupError("the credential has no %s cookie (the body's fingerprint)" % FP_COOKIE)
        ids = new_ids()
        body = build_body(spec, ids, session.bot_id, cookies[FP_COOKIE], int(deps.now().timestamp() * 1000))
        try:
            query = session.signer.signed_query(send_query(session.web_id), body)
        except Exception as exc:      # noqa: BLE001 - a signer that cannot sign sends nothing
            raise SetupError("the signer failed: %s" % type(exc).__name__) from exc
        # Write-ahead: the ids are on the job before it is marked sent, so a lost answer is looked up by them.
        await self._core(lambda: ctx.report_ref(None, {SEND_PATH_KEY: "http", **ids}))
        await self._mark_sent()
        lines: list[str] = []
        status, failure = None, ""
        try:
            resp = await http.post_data(COMPLETION_PATH, query, body, headers("text/event-stream"), SEND_TIMEOUT_S,
                                        lines)
            status = resp.status_code
        except Exception as exc:      # noqa: BLE001 - after mark_sent: unknown, whatever it was
            failure = type(exc).__name__
        return await self._answered(lines, status, failure, accounts, window)

    async def _answered(self, lines: list[str], status: int | None, failure: str, accounts: int, window: int):
        text = "\n".join(lines)
        if status is not None and not 200 <= status < 300:
            # Only a 200 stream is read for dola's verdict: an error page may carry anything, even a code.
            return await self._after_send_failure("the send was answered HTTP %d: unknown" % status)
        stream = read_send_stream(lines)
        if stream.conversation is None:
            if stream.early is not None and status == 200:      # a whole 200 stream, not a cut one
                return await self._refused_before_ack(stream, text, accounts, window)
            why = ("the send got no acknowledgement (%s, %d line(s) read)"
                   % (failure or "HTTP %s" % status, len(lines)))
            result = await self._after_send_failure(why)
            if failure and not lines:      # nothing came back at all: the route may be dead
                result = _outcome(result.job, *self._proxy_effects(why))
            return result
        ALARM.acknowledged()
        conversation = self.conversation = stream.conversation
        await self.ctx.report_ref(conversation, {})
        if failure:
            # Acknowledged, then the stream was cut: the order is taken, and nothing in a partial stream completes
            # or fails the job. The poll reads the whole conversation and decides.
            await self._say("dola acknowledged the order (conversation %s), then the stream was cut (%s): the poll "
                            "decides" % (conversation, failure), "submit", "engine")
            return _outcome(Accepted(conversation, taken=True), Success())
        error = stream_error(text)
        if error is not None and error.code == REFUSED_CODE:     # acknowledged, then refused: as the browser path
            return await self._refused_by_code(text, conversation)
        return await self._answer(conversation, [])

    async def _refused_before_ack(self, stream: SendStream, text: str, accounts: int, window: int):
        """dola answered the ONE send with an error and no acknowledgement: it made no conversation."""
        code = stream.early.code
        if code == REPLAY_CODE:
            # These ids were seen before: the earlier copy may exist. Reconcile finds it by `local_message_id`.
            return await self._after_send_failure("dola called this send a replay (%d): reconcile decides" % code)
        if code == REFUSED_CODE or code in THROTTLE_CODES:
            why = ALARM.note(self.ctx.account.id, stream.verify, self.deps.clock(), accounts, window)
            if why is not None:
                log.error("dola HTTP send: our request signature is suspect, HTTP sends pause %d s: %s (code %d)",
                          window, why, code)
                await self._flush_steps()
                await self._say("dola answered %d (%s); %s: relay's request signature is suspect, no account "
                                "rests and HTTP sends pause %d s" % (code, stream.early.message or "no message", why,
                                                                     window), "submit_error", "engine")
                return _outcome(NotTaken(errors.SIGNER_SUSPECT))
            if code == REFUSED_CODE:
                return await self._refused_by_code(text, None)
            return self._throttled(code)
        if code == REGION_CODE:
            return self._region_blocked("dola answered %d" % code)
        return await self._after_send_failure("dola answered %d before acknowledging the send: unknown" % code)

    async def _chain(self, conversation: str, *, fresh: bool) -> dict | None:
        return await self.driver.read_chain(None, conversation)

    async def _gate(self, conversation, images, judged):
        """No re-send over HTTP: a question stays unclear and the poll decides (as after the browser's re-sends)."""
        if judged.verdict.outcome is not None and not judged.taken:
            self.steps.append("no re-send over HTTP: %s" % judged.verdict.detail)
        return judged, None

    async def _after_send_picture(self) -> None:
        return None

    async def failed(self, exc: BaseException):
        if self.sent:
            return await super().failed(exc)
        cause = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        await self._flush_steps()
        await self._say("not sent: %s" % cause, "submit_error", "engine")
        if isinstance(exc, SignerOutdated):
            _, window = _alarm_bounds(self.settings)
            ALARM.trip(self.deps.clock(), window)
            log.error("dola HTTP send: %s; HTTP sends pause %d s", exc, window)
            return _outcome(Retry(errors.SIGNER_SUSPECT))
        if isinstance(exc, Unreachable):
            return _outcome(Retry(POOL_PROXY_FAILED), *self._proxy_effects("dola unreachable over HTTP: %s" % exc))
        return _outcome(Retry(errors.HTTP_SETUP_FAILED))


REFERENCE_OPERATIONS = ("image_to_video", "image_to_image")
# The only video (model, seconds) delivered over HTTP (spike and live check 23/10, 16:9). Seedance 2.5 was not
# testable (two sends answered "I can't generate the requested content."), 1.0 and other lengths were not tried.
PROVEN_VIDEO = frozenset({("seedance_v2.0", 5)})


def browser_only(job) -> str | None:
    """Why this job cannot go over HTTP (it is sent by browser whatever the setting), None when it can."""
    if job.operation in REFERENCE_OPERATIONS:
        return "the HTTP send cannot attach reference images yet"
    if job.params.get("kind") == "image" and job.params.get("style"):
        return "an image style is not proven over HTTP"
    if job.params.get("kind") == "video" and (job.params.get("ratio") or DEFAULT_RATIO) != DEFAULT_RATIO:
        return "dola ignored a video ratio sent over HTTP (live check 23/10: 9:16 asked, 1280x720 made)"
    if job.params.get("kind") == "video" and (job.params.get("model"), job.params.get("duration")) not in PROVEN_VIDEO:
        return "this video model and length are not proven over HTTP yet"
    return None


def wanted_path(settings) -> str:
    """`dola.send_path` as set (read per job at the send); anything but "http" is the browser."""
    return "http" if settings is not None and settings.get(SEND_PATH_SETTING) == "http" else "browser"
