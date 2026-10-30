"""The side chat of one job: what is said to muse, the tunnel's setup before it, and how its history is read.

Pure, but for `setup` and `deny_approvals`, which talk through a `Tunnel`. Shapes from the spike of 26/10
(fixtures/muse/spike-261026): the history (`GET /chat/history?...&session_id=<chat>`) holds the user's message, the
assistant's messages (`message.assistant`, `payload.status` "completed" once whole, `metadata.message_kind`
"commentary" for a progress line, "message" for an answer) and the result cards (`delta.presentation`, `payload.kind`
image or video, `payload.data.images[]` / `videos[]`, each with `variants.original` =
`https://<vm>.metaaivm.com/media/raw/workspace/imagine_media/<file>`, a video `.../workspace/your_files/<file>` since
30/10, fetched as `/fs/raw/workspace/...`). Several images may come in one card or one card each (2 images: two cards,
26/10). No live event is needed: the history is the record.
"""

import base64
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from relay_core.providers import Balance, Output
from relay_muse.constants import MEDIA_DIRS
from relay_muse.engine import noise
from relay_muse.engine.tunnel import Reply, Tunnel

log = logging.getLogger("relay_muse.chat")
CAPABILITIES = ["chat_cancel", "delta_stream", "custom_reactions", "custom_reactions_facebook_thumbs_up_v1"]
PRESENTATIONS = ["file", "image", "video", "audio", "text_with_button", "generic_list", "image_options",
                 "external_link"]
TIMEZONE = "UTC"
HISTORY_LIMIT = 40
_REF = {kind: re.compile(r"(?:%s)[A-Za-z0-9][A-Za-z0-9._-]{0,200}" % "|".join(map(re.escape, dirs)))
        for kind, dirs in MEDIA_DIRS.items()}
_SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_APPROVAL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_EXT_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".mp4": "video/mp4"}
_COUNT = {1: "one", 2: "two", 3: "three", 4: "four"}
_TAG = re.compile(r"(?i)<\s*/?\s*description\s*>")
FILE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class SetupError(Exception):
    """A setup call of the tunnel was answered with an error: nothing was sent."""


def is_session(value: object) -> bool:
    return isinstance(value, str) and bool(_SESSION.fullmatch(value))


def is_media_ref(ref: object, mime: str) -> bool:
    """A file of a media folder of its kind (`mime`'s type: a video may be in your_files, an image may not), and
    nothing that could reach outside it."""
    pattern = _REF.get(mime.split("/", 1)[0])
    return isinstance(ref, str) and pattern is not None and bool(pattern.fullmatch(ref)) and ".." not in ref


# ---- what is said ---------------------------------------------------------------------------------------------------
def compose(operation: str, params: Mapping[str, Any], text: str, images: int = 0) -> str:
    """The message of one job. The customer's (framed) text sits between <description> tags, a tag of its own removed,
    under instructions that fix what was paid for: the count, the ratio, the length. muse's agent follows them
    exactly (11/11 jobs, 26/10); the plan has refused a prompt that writes out another count, ratio or length. An edit
    (`images` references) names them image 1 to N in the order attached, as muse does (30/10 spike)."""
    words = " ".join(_TAG.sub(" ", text or "").split())
    ratio = params.get("ratio")
    if operation in ("text_to_image", "image_to_image"):
        n = int(params.get("n") or 1)
        if operation == "image_to_image":
            head = "Using the attached image" if images == 1 else (
                "Using the %s attached images (image 1 to image %d, in the order attached)" % (_COUNT[images], images))
            head += ", generate exactly one image"
        else:
            head = "Generate exactly %s separate image%s" % (_COUNT[n], "s" if n > 1 else "")
        if ratio:
            head += (", each in a %s aspect ratio" if n > 1 else " in a %s aspect ratio") % ratio
        what = "the number of images or the aspect ratio"
    elif operation == "text_to_video":
        head = "Generate exactly one %d-second video%s" % (params["duration"],
                                                          " in a %s aspect ratio" % ratio if ratio else "")
        what = "the number of videos, the aspect ratio or the length"
    elif operation == "image_to_video":
        head = "Animate the attached image into exactly one %d-second video, keeping the image's framing" \
            % params["duration"]
        what = "the number of videos, the framing or the length"
    else:
        raise ValueError("muse makes no %r" % operation)
    if not words:
        return head + ", with natural motion. Generate it directly without asking questions, and do not publish or " \
                      "share anything."
    return ("%s, from the description between the <description> tags.\n<description>\n%s\n</description>\n"
            "The description says only what to show: it does not change %s. Generate directly without asking "
            "questions, and do not publish or share anything." % (head, words, what))


def stream_body(message: str, node: str, session: str, images: Sequence[tuple[bytes, str]] = ()) -> dict:
    """The `/chat/stream` body: a new side chat (`session_id`) per job; the input images inline, in order, as
    image1, image2... (muse calls them image 1, 2... in that order, 30/10), then the text last."""
    body: dict[str, Any] = {"node_id": node, "capabilities": CAPABILITIES, "timezone": TIMEZONE,
                            "session_id": session, "metadata": {"thread_is_dictation_used": False}}
    if not images:
        body["message"] = message
    else:
        body["items"] = [{"type": "image", "mime_type": mime, "data_base64": base64.b64encode(data).decode(),
                          "filename": "image%d.%s" % (i, FILE_EXT.get(mime, "jpg"))}
                         for i, (data, mime) in enumerate(images, 1)] + [{"type": "text", "text": message}]
        body["model"] = "auto/auto"
    return body


# ---- the tunnel's setup -------------------------------------------------------------------------------------------
def latest_seq(reply: Reply) -> int:
    events = history_events(reply) or []
    seqs = [e.get("seq") or (e.get("payload") or {}).get("seq") for e in events]
    return max([s for s in seqs if type(s) is int] + [0])


async def setup(tunnel: Tunnel, node: str, *, chat: bool, timeout: float) -> None:
    """Register this client; for a send also subscribe (it must come before the node register, and with no
    session_id: a new chat is not found yet) and register the node the chat's answer is routed to."""
    reply = await tunnel.call("POST", "/client/register-capabilities", {
        "client_id": node, "platform": "web", "display_name": "Muse Web", "version": "0.0.0",
        "capabilities": {"data_sources": {}, "device_commands": {}, "hatch_app_commands": {},
                         "rendering": {"supported_presentations": PRESENTATIONS,
                                       "supported_inline_presentations": ["option"], "supported_text_entities": []}}},
        timeout=timeout)
    if reply.status != 200:
        raise SetupError("muse answered %d to the client register" % reply.status)
    if not chat:
        return
    seq = latest_seq(await tunnel.call("GET", "/chat/history?limit=1&transcript_mode=messages", timeout=timeout))
    status = await tunnel.subscribe({"after_stream_seq": 0, "after_chat_event_seq": seq,
                                     "capabilities": CAPABILITIES}, timeout)
    if status != 200:
        raise SetupError("muse answered %d to the chat subscribe" % status)
    reply = await tunnel.call("POST", "/api/nodes/register", {
        "node_id": node, "display_name": "Chrome (Linux)", "platform": "linux",
        "commands_v2": {"ping": {"description": "Connection liveness check"}}}, timeout=timeout)
    if reply.status != 200:
        raise SetupError("muse answered %d to the node register" % reply.status)


# ---- approvals ------------------------------------------------------------------------------------------------------
def pending_approvals(events: Sequence[Mapping]) -> list[tuple[str, Any]]:
    """(approval id, message revision) of every pending approval named by an approval event (live or in a history).
    Not seen live yet: the shapes are the web client's (`approvals.snapshot`)."""
    found: dict[str, Any] = {}
    for e in events:
        name = str(e.get("event") or e.get("event_name") or "")
        payload = e.get("payload")
        if "approval" not in name or not isinstance(payload, Mapping):
            continue
        items = [payload, *(payload.get("approvals") or ()), *(payload.get("pending") or ())]
        for a in items:
            if not isinstance(a, Mapping):
                continue
            aid = a.get("approval_id") or (a.get("id") if a is not payload else None)
            if isinstance(aid, str) and _APPROVAL_ID.fullmatch(aid) and a.get("status") in (None, "pending"):
                found.setdefault(aid, a.get("message_revision"))
    return list(found.items())


async def deny_approvals(tunnel: Tunnel, events: Sequence[Mapping], denied: set[str], timeout: float) -> list[str]:
    """Deny every pending approval not denied yet (an approval or a publish request is never granted). The ids
    denied now; a refused deny is tried again on the next read."""
    done = []
    for aid, revision in pending_approvals(events):
        if aid in denied:
            continue
        reply = await tunnel.call("POST", "/approvals/%s/decide" % aid, {
            "approval_id": aid, "decision": "deny", "expected_message_revision": revision},
            service=noise.SENTINEL, timeout=timeout)
        if reply.status == 200:
            denied.add(aid)
            done.append(aid)
    return done


# ---- the history ----------------------------------------------------------------------------------------------------
def history_path(session: str) -> str:
    return "/chat/history?limit=%d&transcript_mode=messages&session_id=%s" % (HISTORY_LIMIT, session)


def history_events(reply: Reply) -> list[dict] | None:
    """The chat events of a history answer; None when it is not one (an error, a body that is not a history)."""
    body = reply.json() if reply.status == 200 else None
    result = body.get("result") if isinstance(body, dict) else None
    events = result.get("chat_events") if isinstance(result, dict) else None
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else None


class Media(NamedTuple):
    ref: str        # workspace/imagine_media/<file> or workspace/your_files/<file>
    mime: str


class Reading(NamedTuple):
    media: tuple[Media, ...]    # the results of the job's kind, in order, each once
    other: int                  # results of the other kind (never delivered)
    answered: bool              # an assistant answer (message_kind "message") is complete
    busy: bool                  # an assistant message is not complete yet
    text: str                   # the last complete answer's words
    events: int                 # how many events the chat holds
    user: bool                  # the job's own message is in the chat (the order is there)


def _media(item: Mapping) -> Media | None:
    original = (item.get("variants") or {}).get("original") if isinstance(item.get("variants"), Mapping) else None
    if not isinstance(original, str) or "/raw/" not in original or item.get("missing"):
        return None
    ref = original.split("?", 1)[0].split("/raw/", 1)[1]
    ext = ref[ref.rfind("."):].lower()
    mime = item.get("mime") if isinstance(item.get("mime"), str) and "/" in item["mime"] else _EXT_MIME.get(ext, "")
    return Media(ref, mime) if is_media_ref(ref, mime) else None


def read_history(events: Sequence[Mapping], kind: str) -> Reading:
    media: dict[str, Media] = {}
    other, answered, busy, text, user = 0, False, False, "", False
    for e in events:
        name, payload = e.get("event_name"), e.get("payload") if isinstance(e.get("payload"), Mapping) else {}
        if name == "message.user":
            user = True
        elif name == "message.assistant":
            if payload.get("status") != "completed":
                busy = True
            elif (payload.get("metadata") or {}).get("message_kind") == "message":
                answered, text = True, str(e.get("display_text") or payload.get("content") or "")
        elif name == "delta.presentation":
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
            for key, of in (("images", "image"), ("videos", "video")):
                for item in data.get(key) or ():
                    found = _media(item) if isinstance(item, Mapping) else None
                    if found is None:
                        continue
                    if of == kind and found.mime.startswith(kind + "/"):
                        media.setdefault(found.ref, found)
                    else:
                        other += 1
    return Reading(tuple(media.values()), other, answered, busy, text, len(events), user)


def signature(r: Reading) -> list:
    """What must stay the same for a reading to be settled (stored in a poll's state: JSON)."""
    return [len(r.media), r.other, r.answered, r.busy, r.events]


def verdict(r: Reading, want: int, settled: bool) -> str | None:
    """`done` once the `want` results are there, or fewer once the chat is settled with every message complete
    (any output is a full charge); `refused` once a settled chat holds a complete answer and no result of the kind
    (words, a question, the other kind); None while it may still change."""
    if len(r.media) >= want:
        return "done"
    if not settled or r.busy:
        return None
    if r.media:
        return "done"
    return "refused" if r.answered else None


def outputs(r: Reading, want: int, session: str, **paid: Any) -> tuple[Output, ...]:
    """The first `want` results (what was paid for: more are never delivered). `paid` (the job id, a video's
    duration and ratio) rides on each output's meta, so the fetch can compare the file with it; the last one is
    marked `LAST`: once its file is read the side chat is deleted (the core fetches the outputs in order)."""
    kept = r.media[:want]
    return tuple(Output(m.ref, m.mime, i, meta={"session_id": session, **paid,
                                                **({LAST: True} if i == len(kept) - 1 else {})})
                 for i, m in enumerate(kept))


# ---- muse's answer that the ACCOUNT makes no video -----------------------------------------------------------------
# Live 30/10 (muse-acc1, five jobs): "Video generation isn't available for your account in your country or region",
# "Video bị giới hạn ở khu vực tài khoản này, nên mình không tạo được video nào", "tính năng tạo video hiện không khả
# dụng ở khu vực của bạn". muse answers in the prompt's language and its history carries no code for it, only these
# words. So an answer is that refusal only when it says all three, each in any of these languages: the video
# GENERATION (the feature, not a video's subject), the ACCOUNT or the user's own country or region, and that it is
# unavailable or restricted. "This video is blocked in your country" or "a video of that country's flag" is not it.
# ponytail: word lists; an answer they miss stays a plain muse.refused (failed and refunded, as before 30/10).
_GENERATION_WORDS = (
    "video generation", "generate video", "generate videos", "generating video", "video creation", "create video",
    "create videos", "creating video", "video feature", "tạo video", "tạo được video", "tính năng video", "视频生成",
    "生成视频", "影片生成", "生成影片", "視頻生成", "動画生成", "動画の生成", "動画を生成", "동영상 생성",
    "비디오 생성", "영상 생성", "generación de video", "generación de vídeo", "generar video", "generar vídeo",
    "creación de video", "geração de vídeo", "geração de video", "gerar vídeo", "génération de vidéo",
    "génération vidéo", "générer des vidéos", "générer une vidéo", "videoerstellung", "videogenerierung",
    "videos zu erstellen", "generazione video", "generazione di video", "pembuatan video", "membuat video",
    "создание видео", "генерация видео", "การสร้างวิดีโอ", "video oluşturma")
_SCOPE_WORDS = (
    "your account", "this account", "the account", "your country", "your region", "your location", "tài khoản",
    "khu vực của bạn", "quốc gia của bạn", "vùng của bạn", "你所在的国家", "你所在的地区", "您所在的国家",
    "您所在的地区", "所在国家", "所在地区", "所在地區", "账号", "账户", "帳號", "帳戶", "お住まいの国",
    "お住まいの地域", "アカウント", "해당 국가", "해당 지역", "귀하의 국가", "귀하의 지역", "계정", "tu país",
    "tu región", "su país", "su región", "tu cuenta", "su cuenta", "seu país", "sua região", "sua conta",
    "votre pays", "votre région", "votre compte", "ton pays", "deinem land", "deiner region", "ihrem land",
    "ihrer region", "dein konto", "ihr konto", "tuo paese", "tua regione", "tuo account", "negara anda",
    "wilayah anda", "akun anda", "вашей стране", "вашем регионе", "вашего региона", "вашей страны", "аккаунт",
    "ประเทศของคุณ", "ภูมิภาคของคุณ", "บัญชี", "ülkenizde", "bölgenizde", "hesabınız")
_UNAVAILABLE_WORDS = (
    "not available", "isn't available", "unavailable", "not supported", "isn't supported", "restricted",
    "restriction", "blocked", "không khả dụng", "giới hạn", "hạn chế", "không hỗ trợ", "chưa hỗ trợ", "không có sẵn",
    "不可用", "不支持", "不支援", "无法使用", "無法使用", "受限", "限制", "不提供", "利用できません", "利用できない",
    "ご利用いただけません", "対応していません", "提供されていません", "制限", "사용할 수 없", "이용할 수 없",
    "지원되지 않", "제공되지 않", "제한", "no está disponible", "no disponible", "restringid", "no es compatible",
    "não está disponível", "indisponível", "não disponível", "restrit", "pas disponible", "indisponible",
    "restreint", "pas pris en charge", "nicht verfügbar", "eingeschränkt", "nicht unterstützt", "gesperrt",
    "non è disponibile", "non disponibile", "limitat", "tidak tersedia", "dibatasi", "недоступ", "не поддерж",
    "ограничен", "ไม่พร้อมใช้งาน", "ไม่สามารถใช้", "ไม่รองรับ", "kullanılamıyor", "kısıtlı", "desteklenmiyor")


def _bounded(word: str) -> bool:
    """A word of a script with spaces between words (Latin, Cyrillic): matched only at the start of a word."""
    first = word[0]
    return first.isascii() or any(s in unicodedata.name(first, "") for s in ("LATIN", "CYRILLIC"))


def _any_of(words: Sequence[str]) -> re.Pattern:
    alts = []
    for word in (unicodedata.normalize("NFC", w) for w in words):
        spaced = r"\s+".join(map(re.escape, word.split()))
        alts.append(r"(?<!\w)" + spaced if _bounded(word) else spaced)
    return re.compile("|".join(alts))


_ACCOUNT_NO_VIDEO = tuple(map(_any_of, (_GENERATION_WORDS, _SCOPE_WORDS, _UNAVAILABLE_WORDS)))


def video_unavailable_here(text: str) -> bool:
    """muse's answer says video GENERATION is not available for the account or where it is (the user's own country or
    region): a standing restriction of the ACCOUNT, not a refusal of the prompt."""
    said = unicodedata.normalize("NFC", text or "").lower().replace("’", "'")
    return all(p.search(said) for p in _ACCOUNT_NO_VIDEO)


# ---- the side chat's end --------------------------------------------------------------------------------------------
LAST = "last"       # the output meta key of a job's last output: its fetch deletes the side chat
DELETE_PATH = "/session/delete"


async def delete_chat(tunnel: Tunnel, session: str, timeout: float, retry_after: float = 0.0) -> bool:
    """Delete a job's side chat once its verdict is in and its outputs are read (measured 30/10: 200 {ok, result:
    {deleted}}; the files stay readable, the history is gone). A chat still working answers 409 ("session has an
    active message"): tried once more after `retry_after` seconds (0: not), then left. Best effort: never raises, and
    a chat that stays changes nothing for the job. True when muse said it is deleted."""
    try:
        for final in (not retry_after, True):
            reply = await tunnel.call("POST", DELETE_PATH, {"method": "/api/session/delete", "session_id": session},
                                      timeout=timeout)
            body = reply.json() if reply.status == 200 else None
            result = body.get("result") if isinstance(body, dict) and body.get("ok") else None
            if isinstance(result, dict) and result.get("deleted"):
                log.info("muse side chat %s deleted", session)
                return True
            if reply.status != 409 or final:
                log.warning("muse side chat %s not deleted: muse answered %d", session, reply.status)
                return False
            log.info("muse side chat %s still has an active message: deleted again in %.0f s", session, retry_after)
            await tunnel.listen(retry_after)
    except Exception as e:      # noqa: BLE001 - whatever muse answers, the delete never changes the job
        log.warning("muse side chat %s not deleted: %s", session, type(e).__name__)
    return False


def paid_of(job) -> dict[str, Any]:
    """What `outputs` carries of a job: its id, and a video's paid length and ratio."""
    params = job.params or {}
    keep = ("duration", "ratio") if params.get("kind") == "video" else ()
    return {"job_id": str(job.id), **{k: params[k] for k in keep if params.get(k) is not None}}


def budgets(settings) -> dict[str, int]:
    """Each pool's weekly budget for a free account (`<p>.pools`), the scale of muse's one meter."""
    out = {}
    for name, cfg in (settings.get("muse.pools") or {}).items():
        week = next((w for w in (cfg or {}).get("windows") or () if w.get("name") == "week"), None)
        value = ((week or {}).get("budget") or {}).get("free")
        if type(value) is int and value > 0:
            out[name] = value
    return out


def meter_effects(account, settings, kind: str, made: int) -> tuple:
    """muse has ONE weekly meter for images and videos; the relay keeps two pools. When a job is done, both pools are
    lowered by its share of that meter, counted by what muse MADE (extras it drew beyond n included, though never
    delivered): `made` outputs of `kind` are made/budget[kind] of the week, which is that share of every pool's
    budget (with the defaults a video is 10 images' worth). Reported as each pool's balance from what it held when
    the run started; the core charged the job's own pool already, and the report replaces that figure. A pool whose
    balance is unknown is left to the next check, which reads the real meter."""
    scale = budgets(settings)
    if kind not in scale:
        return ()
    effects = []
    for pool, total in scale.items():
        left = (getattr(account, "capacity", None) or {}).get(pool)
        if left is None:
            continue
        used = made if pool == kind else -(-made * total // scale[kind])
        refill = (getattr(account, "capacity_until", None) or {}).get(pool)
        effects.append(Balance(pool, max(0, left - used), refill))     # the check's report anchors the week
    return tuple(effects)


def made_note(r: Reading, want: int, kind: str, job_id) -> str:
    """The timeline line of a done job: how many results muse made against the paid n. More or fewer than paid is
    also a warning in the log (with the job id), to measure how often the words change the count."""
    made = len(r.media)
    if made == want:
        return "muse made the %d %s(s) asked" % (want, kind)
    if made > want:
        text = "muse made %d %ss, %d were paid: the first %d are delivered, the rest are not" % (made, kind, want, want)
    else:
        text = "muse made %d of the %d %ss asked; all are charged (any output is a full charge)" % (made, want, kind)
    log.warning("muse job %s: %s", job_id, text)
    return text


def want_of(params: Mapping[str, Any]) -> int:
    return int(params.get("n") or 1) if params.get("kind") == "image" else 1
