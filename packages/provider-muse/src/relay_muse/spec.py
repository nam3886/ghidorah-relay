"""muse ProviderSpec: pure data the API imports. No engine imports."""

import re
from collections.abc import Mapping
from typing import Any

from relay_core import frames
from relay_core.pool import pools_setting
from relay_core.pricing import table_setting
from relay_core.providers import (
    AfterSent,
    Caps,
    Completion,
    CreateRequest,
    Dead,
    FixedLimit,
    FrameRules,
    InputError,
    ModelSpec,
    ParsedCredential,
    Plan,
    PoolSpec,
)
from relay_core.refusals import refusal_max_accounts_setting
from relay_core.settings import Spec, disabled_models
from relay_muse import catalog as C
from relay_muse import errors as codes
from relay_muse.constants import (
    CHECK_INTERVAL_S,
    INPUT_MAX_BYTES,
    INPUT_MIMES,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_MAX_S,
    SUSPECT_THRESHOLD,
)
from relay_muse.credential import MAX_COOKIES
from relay_muse.credential import parse_credential as _parse_credential

# What a prompt may not say against the paid options: muse's agent follows the words, so a prompt asking another
# ratio, length or count would get it (only the paid count is delivered and charged; the rest is waste). Numbers and
# the plain word forms are caught; anything subtler is left to the engine, which delivers at most n and notes a video
# that is not what was paid.
_PROMPT_RATIO = re.compile(r"(?<![\d.:])(21:9|16:9|9:16|4:3|3:4|3:2|2:3|1:1)(?![\d:])")
# shape words: always for "widescreen"; an orientation only when it names the frame ("portrait orientation", "vertical
# video"), so "a portrait of a woman" or "a town square" stay free
_SHAPE_WORDS = {"widescreen": ("16:9",), "wide-screen": ("16:9",), "cinemascope": ("16:9", "21:9"),
                "ultrawide": ("21:9",), "ultra-wide": ("21:9",), "portrait": ("9:16", "3:4", "2:3"),
                "vertical": ("9:16", "3:4", "2:3"), "landscape": ("16:9", "4:3", "3:2", "21:9"),
                "horizontal": ("16:9", "4:3", "3:2", "21:9"), "square": ("1:1",)}
_PROMPT_SHAPE = re.compile(
    r"\b(widescreen|wide-screen|cinemascope|ultra-?wide)\b"
    r"|\b(portrait)\s+(?:orientation|format|mode|aspect|ratio)\b"
    r"|\b(vertical|landscape|horizontal|square)\s+(?:orientation|format|mode|aspect|ratio|frame|video|image|shot)\b",
    re.IGNORECASE)
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
          "seventeen eighteen nineteen twenty").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}
_NUMBERS = {**{w: i for i, w in enumerate(_UNITS)}, **_TENS, "a dozen": 12}
_NUMBER = r"\d+|(?:%s)(?:[ -](?:%s))?|%s" % ("|".join(_TENS), "|".join(_UNITS[1:10]), "|".join(
    sorted(_NUMBERS, key=len, reverse=True)))
# a length: "10 seconds", "twenty seconds", "5-sec", "10s", "half a minute", "a minute"; a bare "s" only after 1 to 15
# ("the 1990s", "50s style" are eras)
_PROMPT_SECONDS = re.compile(r"(?<![\w.])(?:(%s)\s*-?\s*(?:secs?|seconds?)|([1-9]|1[0-5])\s*-?\s*s"
                             r"|(half a|an?|one|two|1|2)\s*-?\s*min(?:ute)?s?)\b" % _NUMBER, re.IGNORECASE)
_MINUTES = {"half a": 30, "a": 60, "an": 60, "one": 60, "1": 60, "two": 120, "2": 120}
# a number, at most one word ("a 10 second clip" is a length, not a count), then the noun; or "a pair of", "a couple
# of", "a trio of"; or a diptych or triptych (images only)
_COUNT = r"(?<![\w.])(%s)\s+(?:(?!(?:seconds?|secs?)\b)[a-z-]+\s+)?(?:%s)\b"
_IMAGE_NOUNS, _VIDEO_NOUNS = "images?|pictures?|photos?|pics?|shots?|versions?|variations?", "videos?|clips?"
_PROMPT_IMAGES = re.compile(_COUNT % (_NUMBER, _IMAGE_NOUNS), re.IGNORECASE)
_PROMPT_VIDEOS = re.compile(_COUNT % (_NUMBER, _VIDEO_NOUNS), re.IGNORECASE)
_GROUP = r"(?<![\w.])(?:an?\s+)?(pair|couple|trio)\s+of\s+(?:[a-z-]+\s+)?(?:%s)\b"
_GROUPS = {"pair": 2, "couple": 2, "trio": 3}
_PROMPT_GROUP_IMAGES = re.compile(_GROUP % _IMAGE_NOUNS, re.IGNORECASE)
_PROMPT_GROUP_VIDEOS = re.compile(_GROUP % _VIDEO_NOUNS, re.IGNORECASE)
_PANELS = re.compile(r"\b(diptych|triptych)\b", re.IGNORECASE)


def _number(word: str) -> int:
    word = " ".join(word.lower().replace("-", " ").split())
    if word.isdigit():
        return int(word)
    if word in _NUMBERS:
        return _NUMBERS[word]
    tens, _, unit = word.partition(" ")
    return _TENS[tens] + _NUMBERS[unit]


def prompt_counts(text: str, kind: str) -> set[int]:
    """The counts of images (or videos) a prompt writes out ("4 images", "two cat photos", "a pair of photos",
    "a triptych", "3 clips")."""
    text = text or ""
    if kind == "image":
        found = {_number(w) for w in _PROMPT_IMAGES.findall(text)}
        found |= {_GROUPS[w.lower()] for w in _PROMPT_GROUP_IMAGES.findall(text)}
        return found | {2 if w.lower() == "diptych" else 3 for w in _PANELS.findall(text)}
    return {_number(w) for w in _PROMPT_VIDEOS.findall(text)} | {
        _GROUPS[w.lower()] for w in _PROMPT_GROUP_VIDEOS.findall(text)}


def prompt_shapes(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """(what the prompt says, the ratios it allows) for every ratio or shape word it writes out."""
    found = [(r, (r,)) for r in _PROMPT_RATIO.findall(text or "")]
    for groups in _PROMPT_SHAPE.findall(text or ""):
        word = next(g for g in groups if g).lower()
        found.append((word, _SHAPE_WORDS["ultrawide" if word.startswith("ultra") else word]))
    return found


def prompt_seconds(text: str) -> set[int]:
    """The lengths, in seconds, a prompt writes out."""
    return {_number(a) if a else int(b) if b else _MINUTES[" ".join(c.lower().split())]
            for a, b, c in _PROMPT_SECONDS.findall(text or "")}


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# muse has ONE meter for everything, a weekly Usage percentage that resets at a time it names per account (resetsAt).
# The relay counts it in two pools of outputs, each a 168 h window placed by that reset: measured 26/10 on a free
# account, an image took about 0.15-0.2 % (about 500 a week) and a 5 s video about 2 % (10 s: 3 %, about 50 or 33 a
# week). The budgets are the low end of those ranges. The account check reads the percentage and reports what is left
# of BOTH budgets in proportion, so videos made on an account also lower the images it is sent, and the reverse; at
# `muse.usage_stop_percent` (or a quota status other than SUFFICIENT) both read 0 until the reset.
POOLS = (PoolSpec("image", "uses"), PoolSpec("video", "uses"))
WEEK = "week"
POOL_DEFAULTS = {
    pool: {"combine": "all", "windows": [
        {"name": WEEK, "kind": "rolling", "hours": 168, "anchor": "reset", "budget": {"free": budget}}]}
    for pool, budget in (("image", 400), ("video", 40))}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false muse claims no new job; its account checks go on and keep the sessions "
             "alive. Off until the owner turns muse on.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a muse job that has not delivered is given up and refunded.", min=120),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per job: an image job per image ('muse-image', 'muse-image@n<k>'), a "
        "video by its length ('muse-video@<s>s'). An option missing here is refused (model.unpriced).",
        C.PRICE_KEYS, C.DEFAULT_PRICES, unit="points", basic=True)),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the muse account: one use of its image or video budget per output "
        "(muse's own meter is a weekly percentage). An option missing here is refused (model.uncosted).",
        C.PRICE_KEYS, C.DEFAULT_COSTS, unit="uses")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    _setting("image_submit_timeout_s", int, 240,
             "Hard bound on one image submit, held until muse's answer (15-34 s measured). Past it a taken order is "
             "followed by polls.", min=60, max=SUBMIT_TIMEOUT_MAX_S),
    _setting("video_submit_timeout_s", int, 420,
             "Hard bound on one video submit, held until muse's answer (37-60 s measured). Past it a taken order is "
             "followed by polls.", min=60, max=SUBMIT_TIMEOUT_MAX_S),
    _setting("history_poll_s", int, 10,
             "Seconds between two reads of the job's side chat while a submit holds it.", min=3, max=60),
    _setting("settle_s", int, 45,
             "Seconds an answer must stay unchanged, with every assistant message complete, before fewer outputs "
             "than asked are delivered, or an answer in words with no media is failed as refused (refunded).",
             min=10, max=600),
    _setting("poll_interval_s", int, 30,
             "Seconds between two reads of a muse job that outlived its submit.", min=10, max=300),
    _setting("send_ack_timeout_s", int, 60,
             "Seconds muse may take to acknowledge the send; no answer by then makes the job unknown (it is looked "
             "up, never sent again).", min=10, max=300),
    _setting("lease_timeout_s", int, 30,
             "Seconds muse may take to answer the lease of a new account's first workspace (asked once, only when the "
             "session has none).", min=5, max=120),
    _setting("unavailable_rest_s", int, 3600,
             "Seconds an account rests when muse says its workspace is unavailable from where the call comes from "
             "(seen through a proxy in a region muse does not serve); it is read again at the check after the rest. "
             "Nothing is leased for it.", min=300, max=86400),
    _setting("lease_retry_s", int, 3600,
             "Seconds a new account rests after muse refused or did not answer the lease of its first workspace for "
             "now (a 5xx such as 503 lease_rejected, a timeout: capacity or a waitlist); the lease is asked again at "
             "the check after the rest. A 4xx other than 403 ends the account instead.", min=300, max=86400),
    _setting("wake_timeout_s", int, 90,
             "Seconds a job waits for the account's sleeping workspace to wake before it goes back to the queue.",
             min=10, max=600),
    _setting("wake_poll_s", int, 3,
             "Seconds between two reads of the session while the workspace wakes.", min=1, max=30),
    _setting("http_timeout_s", int, 30, "Seconds one HTTP call to muse.ai may take (through the account's proxy).",
             min=5, max=120),
    _setting("ws_timeout_s", int, 30, "Seconds the WebSocket to the account's workspace may take to open, and each "
             "frame sent on it or its close.", min=5, max=120),
    _setting("handshake_timeout_s", int, 20, "Seconds the workspace may take to answer the tunnel's handshake.",
             min=5, max=120),
    _setting("call_timeout_s", int, 30, "Seconds one call in the tunnel may take to answer (a setup call, a history "
             "read).", min=5, max=120),
    _setting("fetch_timeout_s", int, 120, "Seconds one result file may take to come through the tunnel.",
             min=30, max=600),
    _setting("media_max_mb", int, 64, "The largest result file taken, in MiB (a 10 s video was under 6 MiB).",
             min=1, max=512),
    _setting("usage_budget_s", int, 90, "Seconds an account check may spend on the weekly Usage figure, the hunt "
             "for its action in the site's scripts included (the hunt goes on at the next check). The check itself "
             "is cut at 144 s.", min=10, max=130),
    _setting("scan_max_scripts", int, 400, "The most reads of the site's scripts one deployment's hunt for the "
             "Usage action may spend (the site loads about 190).", min=50, max=2000),
    _setting("scan_batch", int, 12, "Site scripts read at once while hunting the Usage action.", min=1, max=32),
    _setting("video_region_rest_s", int, 7 * 86400,
             "Seconds an account's video pool rests when muse answers a video job that video generation is not "
             "available for the account in its country or region (a standing restriction, live 30/10); its image "
             "pool goes on. The job goes to another account.", min=3600, max=30 * 86400),
    refusal_max_accounts_setting(
        PROVIDER, "How many accounts one muse job may be refused on before it fails with its refund. A refusal of "
        "the account is muse's answer that the account makes no video in its country or region (the last one ends "
        "the job with muse.video_unavailable_on_accounts); a refusal of the prompt ends the job at once "
        "(muse.refused)."),
    _setting("usage_stop_percent", int, 95,
             "The weekly Usage percentage at which an account is sent no more jobs until its reset (both pools read "
             "0). Below it, what is left of each pool's budget follows the percentage.", min=1, max=100),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))

# The shipped default: the core frame rendered verbatim. The job is found by its own side chat id, so the tracking
# code renders as nothing; the engine wraps the framed text in its own instructions (engine.chat.compose).
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class MuseSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    ref_is_conversation = True       # the job's reference is its side chat
    caps = Caps(
        execution="http", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=True,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_muse.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real muse.ai jar; a longer list is not a cookie export", "cookies"),
        FixedLimit("relay_muse.catalog.MAX_N", C.MAX_N,
                   "most images (n) one muse-image job asks in its one chat, priced per image", "images"),
    )

    def catalog(self) -> list[ModelSpec]:
        return C.catalog()

    def codes(self) -> dict:
        return dict(codes.CODES)

    def settings(self) -> dict[str, Spec]:
        return dict(_SETTINGS)

    def parse_credential(self, raw: bytes) -> ParsedCredential:
        return _parse_credential(raw)

    def extras(self, snapshots: Mapping[str, Mapping]) -> dict:
        return {}

    def deadline_cause(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str:
        return "render.deadline"

    def unclear_at_deadline(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str | None:
        return None

    def plan(self, req: CreateRequest, settings: Any, snapshots: Mapping[str, Mapping]) -> Plan:
        """Validate and normalise. Raises `InputError`. An image job draws `n` images (1 to `MAX_N`) from text alone,
        in one of `IMAGE_RATIOS` or muse's own shape. A video is 5 or 10 s, from text (16:9 or 9:16, default 16:9) or
        from one input image (its own shape, no ratio; the text is optional). A prompt that writes out another ratio,
        length or count than the paid one is refused: muse's agent would follow the words."""
        kind = C.KIND.get(req.model)
        if kind is None:
            raise _refuse("input.bad_model", "muse's models are %s" % ", ".join(C.KIND), "model")
        if len(req.images) > (1 if kind == "video" else 0):
            raise _refuse("input.too_many_images", "%s takes %s" % (
                req.model, "at most one input image" if kind == "video" else "no input image"), "images")
        for image in req.images:
            if image.mime not in INPUT_MIMES or image.size > INPUT_MAX_BYTES:
                raise _refuse("input.invalid", "the input image must be a JPEG, PNG or WebP of at most %d bytes"
                              % INPUT_MAX_BYTES, "images")
        if (kind == "image" or not req.images) and C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        n = 1 if req.n is None else req.n
        if (kind == "video" and n != 1) or not 1 <= n <= C.MAX_N:
            raise _refuse("input.invalid", "n is 1 to %d images; a video job makes one video" % C.MAX_N, "n")
        if prompt_counts(req.prompt, kind) - {n}:
            raise _refuse("input.invalid", "the prompt asks for %s %s(s) but the job makes %d: set n and leave the "
                          "count out of the prompt" % (" / ".join(map(str, sorted(prompt_counts(req.prompt, kind)))),
                                                       kind, n), "prompt")
        if req.ratio is not None:
            allowed = C.IMAGE_RATIOS if kind == "image" else () if req.images else C.VIDEO_RATIOS
            if req.ratio not in allowed:
                raise _refuse("input.bad_ratio", "%s takes the ratios %s (a video from an image keeps its shape)"
                              % (req.model, ", ".join(allowed) or "none"), "ratio")
        ratio = req.ratio or (C.VIDEO_RATIOS[0] if kind == "video" and not req.images else None)
        other = sorted({said for said, allows in prompt_shapes(req.prompt) if ratio not in allows})
        if other:
            raise _refuse("input.prompt_ratio_conflict", "the prompt says %s but the job's ratio is %s: remove it "
                          "from the prompt" % (" / ".join(other), ratio or "the image's or muse's own"))
        params: dict[str, Any] = {"kind": kind, **({"ratio": ratio} if ratio else {})}
        if kind == "image":
            if req.duration is not None:
                raise _refuse("input.bad_duration", "an image has no duration", "duration")
            params["n"] = n
            price_key = C.image_key(n)
        else:
            duration = C.DEFAULT_DURATION if req.duration is None else req.duration
            if duration not in C.DURATIONS:
                raise _refuse("input.bad_duration", "%s makes %s s videos" % (
                    req.model, " or ".join(map(str, C.DURATIONS))), "duration")
            if prompt_seconds(req.prompt) - {duration}:
                raise _refuse("input.prompt_duration_conflict", "the prompt names another length than %d s: remove "
                              "it from the prompt" % duration)
            params["duration"] = duration
            price_key = C.video_key(duration)
        operation = {"image": "text_to_image", "video": "image_to_video" if req.images else "text_to_video"}[kind]
        return Plan(
            operation=operation, media_type=kind, model=req.model, params=params,
            completion=Completion.HOLD, after_sent=AfterSent.RECONCILE, price_key=price_key, pool=kind,
            submit_timeout_s=settings.get("muse.%s_submit_timeout_s" % kind))


SPEC = MuseSpec()
