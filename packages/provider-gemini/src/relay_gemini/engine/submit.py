"""Submit: one job to gemini (`StreamGenerate` with the image, the video or the music tool), and what gemini said as a
`SubmitResult`.

The steps: load gemini's page at the account's `/u/N` (session rotated first when due); check it is the Google account
the credential names; for an edit or an image to video, upload the one input image (free); write the job's request id
to the job (`ctx.report_ref`); `ctx.mark_sent()`; the ONE send. An image send streams its answer until the image is
drawn (25-28 s measured), so a good answer is `Completed` with the image's `/gg-dl/` link (HOLD); a song's the same
way once it is made (58-80 s measured), with its MP4 (HOLD). A video send answers in 3-33 s that it is "generating
your video" (FINDINGS 7): `Accepted` with the chat, which the poll then reads until the video is there (1-3 min
measured, DETACH).

Money rules that shape this file (each has a test and a mutation):
- The job is on record as sent BEFORE its send leaves: `report_ref` then `mark_sent` immediately before the one POST.
  The write-ahead key of a lost send is the job's exact sent text and its send time (`sent_at`): gemini lists each chat
  with the time its send started and gives the sent text back (FINDINGS 4). No id we send is echoed, and a tracking
  code typed into the prompt is drawn into the image, so the text goes verbatim and the reconciler matches it.
- One send per job, never a second: nothing after `mark_sent` returns `Retry`, nor `NotTaken` but for gemini's own
  "something went wrong, try again" final answer (below).
- Only a complete answer is a verdict: clean (every line read) and closed by gemini's end item (`Answer.ended`).
  Complete with the job's media: `Completed`. A video send complete with the "generating" item and a chat: `Accepted`.
  Complete with a final text and no media: gemini answered for good, the job fails with its refund
  (`gemini.not_entitled` for the "upgrade your subscription" answer, `gemini.video_limit` for "I can't create more
  videos for you today", `gemini.refused` otherwise, the text kept in the detail). The one exception: a final text
  saying something went wrong on gemini's side and asking to try again (`is_temporary_failure`) is `NotTaken`
  (`gemini.upstream_failed`): a complete answer with no media and no "generating" item can deliver nothing later, so the
  core re-queues the job with one attempt used and refunds it only when the attempts run out. A video limit answer (that
  one, or a video refusal that reads like a quota) also sets the video pool to a zero balance until the reset the usage
  read then names (`jSf9Qc`), else `gemini.video_limit_rest_s` from now; an image refusal empties the image pool only
  when that read shows a used-up window (no image limit wording is measured). Anything else (no answer, a timeout, a cut
  or unreadable answer, a status that is not 200, an error item, an empty or media-promising final text with no media, a
  "generating" answer with no chat) is `Ambiguous`: the job becomes unknown and the reconciler looks for it; it is never
  sent again.
- After every send the account's pools rest `gemini.account_min_gap_s`.

Before `mark_sent` every failure is an ordinary retry: a signed-out page `pool.account_logged_out` with a spaced strike,
a page of another Google account than the credential names (or an account the check has not resolved yet: no mode
known) `gemini.account_unresolved` with a rest and a recheck, a proxy that cannot reach gemini `pool.proxy_failed`, an
error answer `gemini.http_error`, an upload not taken `gemini.upload_failed`, and a video whose account's 5-hour usage
window holds less than one video (`gemini.video_usage_units`) `pool.account_out_of_points` with the video pool at 0
until that window resets (such a video is taken and never rendered, measured 25/10).
"""

import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

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
from relay_gemini import catalog as C
from relay_gemini import errors
from relay_gemini.constants import (
    CAPABILITIES,
    GENERATE_TIMEOUT_S,
    IMAGE_TOOL,
    MUSIC_TOOL,
    NETWORK_REST_S,
    NOT_ENTITLED_LINK,
    QUOTA_WORDS,
    REFUSAL_WORDS,
    TEMPORARY_FAILURE_PHRASES,
    TRY_AGAIN_PHRASES,
    VIDEO_LIMIT_PHRASES,
    VIDEO_TOOL,
)
from relay_gemini.engine.check import (
    POOLS,
    SHORT_WINDOW,
    WINDOWS,
    doubt_effects,
    pool_windows,
    read_short_window,
    read_spent,
    signed_out_why,
)
from relay_gemini.engine.http import Sessions, Site
from relay_gemini.engine.reply import Answer, Image, Video, read_answer
from relay_gemini.templates import ASSET_BASE, MUSIC_TEMPLATES, VIDEO_TEMPLATES

REQUEST_KEY = "request"      # provider_state: the request id of the send (also the job's external_ref until the chat)
CHAT_KEY = "chat"            # provider_state: gemini's conversation id, once known
LOGGED_OUT = "pool.account_logged_out"
PROXY_FAILED = "pool.proxy_failed"
OUT_OF_POINTS = "pool.account_out_of_points"
IMAGE_PLACEHOLDER = "googleusercontent.com/image_generation_content/"   # the text of an answer that holds an image
VIDEO_PLACEHOLDER = "googleusercontent.com/generated_video_content/"    # ... and of one that holds a video
MUSIC_PLACEHOLDER = "googleusercontent.com/generated_music_content/"    # ... and of one that holds a song (25/10)
_MODE = re.compile(r"[0-9a-f]{16}")
DEFAULT_MODE_KIND = 6        # the header's [14] for the default mode (Auto and Flash-Lite: 6, otAQ7b and the captures)

_store = None


async def read_input_media(ref) -> bytes:
    """One job input image from the object store, the store created on first use (a text job needs none). The one
    place the engine reaches past `RunCtx` for a file: the protocol hands out only a `MediaRef`."""
    global _store
    if _store is None:
        from relay_core.storage import ObjectStore
        _store = ObjectStore.from_env()
    return await _store.read(ref.object_key)


def _result(job, *effects) -> SubmitResult:
    return SubmitResult(job, account=tuple(effects))


def pacing(settings, now: datetime) -> tuple[Rest, ...]:
    """After a send the account's pools rest `gemini.account_min_gap_s` (0: no rest)."""
    gap = int(settings.get("gemini.account_min_gap_s"))
    return tuple(Rest(pool, now + timedelta(seconds=gap), "gemini pacing between two sends")
                 for pool in POOLS) if gap > 0 else ()


def limit_balance(settings, now: datetime, refill: datetime | None = None) -> Balance:
    """gemini said the account makes no more videos for now: the video pool's balance is 0 until the reset the usage
    read named (`refill`), else `gemini.video_limit_rest_s` from now. The core rests the pool until then."""
    return Balance("video", 0, refill or now + timedelta(seconds=int(settings.get("gemini.video_limit_rest_s"))))


def is_video(job) -> bool:
    return job.operation in ("text_to_video", "image_to_video")


def is_music(job) -> bool:
    return job.operation == "text_to_music"


def mode_of(account, model: str | None = None) -> tuple[str, int] | None:
    """The mode the job is sent with, `(id, kind)`, as the check read it (otAQ7b): the account's "Pro" mode for the
    pro-tier image model (it draws "Nano Banana 2.1"; the default Flash-Lite draws "Nano Banana 2 Lite", 25/10), its
    default mode otherwise (kind 6: Auto and Flash-Lite both, measured). None until a check has read it, or for the
    pro-tier model on a plan with no "Pro"."""
    state = account.provider_state or {}
    if model == C.PRO_IMAGE_MODEL:
        pro = state.get("pro_mode")
        ok = isinstance(pro, list) and len(pro) == 2 and isinstance(pro[0], str) and _MODE.fullmatch(pro[0]) \
            and type(pro[1]) is int
        return (pro[0], pro[1]) if ok else None
    mode = state.get("mode")
    return (mode, DEFAULT_MODE_KIND) if isinstance(mode, str) and _MODE.fullmatch(mode) else None


def sent_text(job) -> str:
    """The core's framed text (verbatim: the customer's words); a job queued before gemini had frames carries none."""
    return job.prompt if job.frame_id is None or job.sent_text is None else job.sent_text


def gallery_entry(templates: dict, name: str) -> list:
    """A template as the send names it: its id, then the gallery's entry `[id, name, [[asset link, kind], ...], ""]`
    (captured 25/10). The web app adds the gallery tab ("all") and the card's place, which gemini does not need (a
    template video sent with no place was made, 25/10)."""
    tid, assets = templates[name]
    return [tid, [tid, name, [[ASSET_BASE + path, k] for path, k in assets], ""], None, "all", None]


def music_message(params) -> tuple[list | None, list]:
    """A song's message part (`[9][6]`, None: none) and option ids (`[55]`), as the web app sends them (captured 25/10):
    the length, vocals and genre ids in that order at `[55]`; the length and vocals codes at `[9][6][2]` as `[length,
    vocals]` (a trailing one not chosen left out), `[]` there when only a genre is chosen, no `[9]` when nothing is. A
    template goes after the codes (`[]` then)."""
    length, vocals = C.MUSIC_LENGTHS.get(params.get("length")), C.MUSIC_VOCALS.get(params.get("vocals"))
    genre = C.MUSIC_GENRES.get(params.get("genre"))
    ids = [x for x in (length and length[0], vocals and vocals[0], genre) if x is not None]
    codes = [length and length[1], vocals and vocals[1]]
    while codes and codes[-1] is None:
        codes.pop()
    if params.get("template"):
        return [None, None, codes, *gallery_entry(MUSIC_TEMPLATES, params["template"])], [ids] if ids else []
    if codes:
        return [None, None, codes], [ids]
    return ([], [ids]) if ids else (None, [])


def request(text: str, ratio: str | None, image: tuple[str, str, str] | None, rid: str, mode: str, *,
            video: bool = False, kind: int = DEFAULT_MODE_KIND, template: str | None = None,
            music: dict | None = None) -> tuple[list, dict]:
    """The send's `inner` and headers, as the web app builds them with the image tool chosen (captured 23/10,
    fixtures/gemini/live-261023/web_request_*.json), the video tool (24/10, live-261024/web_request_i2v.json, and 25/10)
    or the music tool (25/10). The anti-bot token the page puts at inner[3] is not needed. An image ratio goes in the
    message (`[9][6]`) and as its option id (`[55]`); an edit names the uploaded file at `[3]`. A video names its ratio
    option (`[55]`) and its orientation code in the message (`[[null, null, null, 1 | 2]]`), then its gallery
    template if one was asked for; an image to video names the file at `[3]` in the shorter entry the video tool sends.
    `music`: a song's params (`music_message`). The mode goes in the first header with its `kind` at [14]: the kind,
    not the id alone, picks the image model (25/10: the "Pro" id with kind 6 drew "Nano Banana 2 Lite", with its own
    kind 3 "Nano Banana 2.1")."""
    content: list = [text, 0, None, None, None, None, 0]
    if image is not None:
        path, mime, name = image
        entry = [path, 1, None, mime, str(uuid.uuid4())]
        content[3] = [[entry, name]] if video else [[entry, name, None, None, None, None, None, None, [0]]]
    message, option = None, [[C.RATIO_IDS[ratio]]] if ratio else []
    if video:
        message = [[None, None, None, C.VIDEO_ORIENTATION[ratio]]]
        if template:
            message += [None, None, *gallery_entry(VIDEO_TEMPLATES, template)]
        option = [[C.VIDEO_RATIO_IDS[ratio]]]
    elif music is not None:
        message, option = music_message(music)
    elif ratio:
        message = [None, [None, ratio]]
    if message is not None:
        content += [None, None, [None, None, None, None, None, None, message]]
    tool = VIDEO_TOOL if video else MUSIC_TOOL if music is not None else IMAGE_TOOL
    inner: list = [None] * 99
    for i, v in {0: content, 1: ["en"], 2: ["", "", "", None, None, None, None, None, None, ""], 4: uuid.uuid4().hex,
                 6: [0], 7: 1, 10: 1, 11: 0, 17: [[0]], 18: 0, 27: 1, 30: [4, 16, 19], 41: [1],
                 49: tool, 53: 0, 54: [], 55: option, 59: rid, 61: [], 67: 0, 68: 1,
                 79: 6, 80: 1, 91: 1, 96: 0, 98: 1}.items():
        inner[i] = v
    headers = {
        "x-goog-ext-525001261-jspb": json.dumps([1, None, None, None, mode, None, None, 0, CAPABILITIES, None,
                                                 None, 1, None, None, kind, 1, rid]),
        "x-goog-ext-525005358-jspb": json.dumps([rid, 1]),
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0,0,0]",
    }
    return inner, headers


def outputs_of(media: tuple[Image | Video, ...], chat: str | None) -> tuple[Output, ...]:
    def meta(m):
        extra = {"duration_s": m.duration_s} if isinstance(m, Video) else {}
        return {"chat": chat, "width": m.width, "height": m.height, **extra}
    return tuple(Output(m.link, m.mime, i, m.name, meta(m)) for i, m in enumerate(media))


def judge(status: int, answer: Answer | None, *, video: bool = False, music: bool = False) -> tuple[str, object]:
    """What a send's (or a chat read's) answer means: ("done", media) / ("taken", None) / ("refused", (code, text)) /
    ("unknown", why). Only a complete answer (200, clean, ended) is ever "done", "taken" or "refused". `video`: the
    job's media is a video, and a "generating your video" answer is "taken" (sent and accepted, not done). `music`:
    the job is a song, whose media is the answer's MP4 (its MP3 is not a video record); judged like an image
    otherwise."""
    if status != 200 or answer is None:
        return "unknown", "gemini answered %d" % status
    if not answer.clean or not answer.ended:
        return "unknown", "the answer was cut or could not be read"
    if answer.errors:
        return "unknown", "the answer carried an error item"
    media = answer.videos if video or music else answer.images
    if media:
        return "done", media
    final = answer.texts[-1].strip() if answer.texts else ""
    # the proven refusals first: the free plan's "upgrade" answer carries the "generating" item too (measured 23/10)
    if NOT_ENTITLED_LINK in final:
        return "refused", (errors.NOT_ENTITLED, final)
    if video and is_video_limit(final):
        return "refused", (errors.VIDEO_LIMIT, final)
    if video and answer.generating:     # taken, whatever other text comes with it: never a refusal while it renders
        return "taken", None
    if not final or any(p in final for p in (IMAGE_PLACEHOLDER, VIDEO_PLACEHOLDER, MUSIC_PLACEHOLDER)):
        return "unknown", "the answer named no %s record" % ("video" if video else "song" if music else "image")
    if is_temporary_failure(final):
        return "refused", (errors.UPSTREAM_FAILED, final)
    return "refused", (errors.REFUSED, final)


def plain(text: str) -> str:
    """The text lowercased, its apostrophes made straight, "cannot" made "can't", every other punctuation a space."""
    t = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
    t = t.replace("cannot", "can't").replace("can not", "can't")
    return " ".join(re.sub(r"[^\w']+", " ", t).split())


def is_temporary_failure(text: str) -> bool:
    """Is it gemini's "something went wrong on our end, try again" answer: a failure on its side, not a refusal? A
    refusal word (a policy, another prompt, "can't create", ...) wins over both phrases."""
    t = plain(text)
    if any(w in t for w in REFUSAL_WORDS):
        return False
    return any(p in t for p in TEMPORARY_FAILURE_PHRASES) and any(p in t for p in TRY_AGAIN_PHRASES)


def is_video_limit(text: str) -> bool:
    """Is it gemini's "no more videos for now" answer (measured: "I can't create more videos for you today, ...")?"""
    t = plain(text)
    return any(p in t for p in VIDEO_LIMIT_PHRASES)


def is_video_limit_refusal(code: str, text: str) -> bool:
    """A video refusal that is the limit answer, or reads like a quota (a quota word in any of the languages listed:
    the video rule from before limits became balances)."""
    return code == errors.VIDEO_LIMIT or (code == errors.REFUSED and any(w in plain(text) for w in QUOTA_WORDS))


def job_pool(job) -> str:
    """The pool the job spends (as the spec plans it): `video`, `music`, `image_pro` for the pro-tier image model,
    else `image`."""
    if is_video(job) or is_music(job):
        return "video" if is_video(job) else "music"
    return "image_pro" if job.model == C.PRO_IMAGE_MODEL else "image"


def limit_read_pool(code: str, text: str, pool: str) -> str | None:
    """The pool whose usage is read right after a refusal of a job of `pool`, or None: a video's limit refusal (for
    its reset), and any image or song refusal (no image or music limit wording is measured, so only a used-up usage
    window says they are out)."""
    if pool == "video":
        return pool if is_video_limit_refusal(code, text) else None
    return pool if code == errors.REFUSED else None


def limit_effects(settings, now: datetime, code: str, text: str, video: bool,
                  spent: Balance | None = None) -> tuple[Balance, ...]:
    """What a refusal does to the account's pool; the job's own outcome (failed, refunded) does not change.
    - a video limit refusal: the video pool is 0 until the reset the usage read named (`spent`, with its window),
      else `gemini.video_limit_rest_s` from now (`limit_balance`);
    - an image refusal: the job's image pool is 0 only when the usage read then showed a used-up window (`spent`);
      its words alone never empty the pool (an ordinary refusal would close the account's images for hours)."""
    if video:
        if not is_video_limit_refusal(code, text):
            return ()
        return (spent,) if spent is not None and spent.refill_at is not None else (limit_balance(settings, now),)
    return (spent,) if spent is not None else ()


async def usage_spent(site: Site, pool: str, settings) -> Balance | None:
    """The zero balance the usage read gives the pool when a usage window of the pool's own is used up (until its
    reset, naming it), read right after a refusal (a read: it spends nothing); None when none is used up, the pool has
    no such window (a used-up 5-hour block never empties a pool that counts only a day) or the read failed."""
    return await read_spent(site, pool, pool_windows(settings, pool))


def short_balance(room: tuple[int, datetime | None]) -> Balance:
    """The video pool at 0 until the 5-hour window resets (naming that window when its reset is known)."""
    return Balance("video", 0, room[1], WINDOWS[SHORT_WINDOW] if room[1] is not None else None)


def video_room_short(settings, room: tuple[int, datetime | None] | None) -> bool:
    """Does the 5-hour usage window hold less than one video (`gemini.video_usage_units`)? Unknown is not short."""
    return room is not None and room[0] < int(settings.get("gemini.video_usage_units"))


def answer_result(status: int, answer: Answer | None, settings, now: datetime, *, video: bool = False,
                  music: bool = False, rid: str | None = None, spent: Balance | None = None) -> SubmitResult:
    """A send's verdict. Never `Retry`, and `NotTaken` only for a complete "something went wrong, try again" answer.
    `spent`: what the usage read right after a refusal gave the job's pool (`usage_spent`)."""
    rest = pacing(settings, now)
    verdict, what = judge(status, answer, video=video, music=music)
    if verdict == "done":
        return _result(Completed(outputs_of(what, answer.cid)), Success(), *rest)
    if verdict == "taken":
        if not answer.cid:
            return _result(Ambiguous("gemini took the video but named no chat"), *rest)
        return _result(Accepted(answer.cid, {REQUEST_KEY: rid, CHAT_KEY: answer.cid}), Success(), *rest)
    if verdict == "refused" and what[0] == errors.UPSTREAM_FAILED:
        # a complete final answer with no media and no "generating" item: nothing is made and nothing can still come,
        # so a new send (this account or another) is no second order. No chat kept: a later send's lost answer must
        # not be reconciled to this one (the requeue event names it, the dropped reference)
        return _result(NotTaken(errors.UPSTREAM_FAILED), *rest)
    if verdict == "refused":
        code, text = what
        return _result(Fail(code, "gemini answered: %s" % text[:300], {CHAT_KEY: answer.cid}), Success(), *rest,
                       *limit_effects(settings, now, code, text, video, spent))
    return _result(Ambiguous(what), *rest)


async def submit(ctx, job, settings, sessions: Sessions, *, transport=None,
                 read_media: Callable[..., Awaitable[bytes]] = read_input_media,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 wall: Callable[[], float] = time.time) -> SubmitResult:
    """Send the job. Exceptions of `ctx.report_ref` and `ctx.mark_sent` (a lost lease, a stopping worker) reach the
    core unchanged. The keyword arguments are seams for tests."""
    sent, video, music = False, is_video(job), is_music(job)
    try:
        async with Site(ctx, sessions, settings, transport=transport, clock=wall) as site:
            page = await site.load()
            if page.status != 200:
                return _result(Retry(errors.HTTP_ERROR))
            if not page.signed_in:
                return _result(Retry(LOGGED_OUT), *doubt_effects(signed_out_why(site), now()))
            mode = mode_of(ctx.account, job.model)
            if mode is None or (site.email and (page.email or "").lower() != site.email):
                why = "gemini's page is not the account's Google account, or the account was not checked yet"
                return _result(Retry(errors.UNRESOLVED),
                               Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S), recheck=True))
            if video:
                # a video sent into a 5-hour window that cannot hold one is taken and never rendered (measured 25/10):
                # it is not sent, the video pool is empty until the window resets, another account takes the job
                room = await read_short_window(site)
                if video_room_short(settings, room):
                    return _result(Retry(OUT_OF_POINTS), short_balance(room))
            image = None
            if job.operation in ("image_to_image", "image_to_video"):
                ref = (await job.inputs())[0]
                name = "input.%s" % (ref.mime.split("/")[-1] or "png")
                path = await site.upload(name, await read_media(ref))
                if path is None:
                    return _result(Retry(errors.UPLOAD_FAILED))
                image = (path, ref.mime, name)
            rid = str(uuid.uuid4()).upper()
            ratio = job.params.get("ratio") or (C.VIDEO_RATIO if video else None)
            inner, headers = request(sent_text(job), ratio, image, rid, mode[0], video=video, kind=mode[1],
                                     template=job.params.get("template"), music=dict(job.params) if music else None)
            await ctx.report_ref(rid, {REQUEST_KEY: rid})
            await ctx.mark_sent()
            sent = True
            try:
                status, body = await site.send(inner, headers, GENERATE_TIMEOUT_S)
            except httpx.HTTPError as e:
                return _result(Ambiguous("the send got no full answer (%s)" % type(e).__name__),
                               *pacing(settings, now()))
            answer = read_answer(body)          # judged with its status, never instead of it
            if status == 200 and answer.cid:
                await ctx.report_ref(answer.cid, {REQUEST_KEY: rid, CHAT_KEY: answer.cid})
            verdict, what = judge(status, answer, video=video, music=music)
            pool = limit_read_pool(*what, job_pool(job)) if verdict == "refused" else None
            spent = await usage_spent(site, pool, settings) if pool else None
            return answer_result(status, answer, settings, now(), video=video, music=music, rid=rid, spent=spent)
    except httpx.HTTPError as e:
        why = "gemini could not be reached (%s)" % type(e).__name__     # the text may carry the proxy URL
        if sent:       # never reached: the send's own errors are caught above. Kept as the money guard.
            return _result(Ambiguous(why))
        return _result(Retry(PROXY_FAILED), ProxyBad(why), Cooldown(why, now() + timedelta(seconds=NETWORK_REST_S)))
