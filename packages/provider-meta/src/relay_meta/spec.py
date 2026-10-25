"""meta ProviderSpec: pure data the API imports. No engine, no browser imports."""

import logging
import re
from collections.abc import Mapping
from typing import Any

from relay_core import frames
from relay_core.pool import day_pool, pools_setting
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
from relay_core.settings import Spec, disabled_models
from relay_meta import catalog as C
from relay_meta import errors as codes
from relay_meta.constants import (
    BROWSER_SLOTS_MAX,
    BROWSER_SLOTS_MIN,
    CHECK_INTERVAL_S,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    RATIOS,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_MAX_S,
    SUSPECT_THRESHOLD,
)
from relay_meta.credential import MAX_COOKIES
from relay_meta.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)

# A ratio written in the prompt (dola's list); not next to another digit or colon, so "10:30" (a time) is not one.
_PROMPT_RATIO = re.compile(r"(?<![\d.:])(21:9|16:9|9:16|4:3|3:4|3:2|2:3|1:1)(?![\d:])")
# A count of images written in the prompt ("4 images", "two cat photos"): a number, at most one word, then an image
# noun. Conservative on purpose: meta draws what the words ask, and only the paid n may be drawn.
_WORDS = "zero one two three four five six seven eight nine ten eleven twelve"
_NUMBERS = {**{w: i for i, w in enumerate(_WORDS.split())},
            "a dozen": 12}
_PROMPT_COUNT = re.compile(r"(?<![\w.])(\d+|%s)\s+(?:[a-z-]+\s+)?(?:images?|pictures?|photos?|pics?)\b"
                           % "|".join(_NUMBERS), re.IGNORECASE)


def prompt_counts(text: str) -> set[int]:
    """The image counts a prompt writes out (`_PROMPT_COUNT`)."""
    return {int(w) if w.isdigit() else _NUMBERS[" ".join(w.lower().split())]
            for w in _PROMPT_COUNT.findall(text or "")}


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    """An `InputError` for the core, with the human text kept on `.detail` (for tests and logs)."""
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# Image jobs each meta account may take per day, and videos it may make per day (not measured).
POOLS = (PoolSpec("image", "jobs"), PoolSpec("video", "jobs"))
POOL_DEFAULTS = {"image": day_pool({"free": 40}), "video": day_pool({"free": 20})}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false meta claims no new job. Off until meta can submit.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a meta job that has not delivered is given up.", min=60),
    _setting("browser_slots", int, 2, "Browser sessions at once (live). Lowering retires idle slots.",
             min=BROWSER_SLOTS_MIN, max=BROWSER_SLOTS_MAX, basic=True),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per job, keyed by its option. An option missing here is refused "
        "(model.unpriced), never charged a guess.", C.PRICE_KEYS, C.DEFAULT_PRICES, unit="points", basic=True)),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the meta account: one job of its daily budget (one chat each). "
        "An option missing here is refused (model.uncosted).",
        C.PRICE_KEYS, C.DEFAULT_COSTS, unit="jobs")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    _setting("image_submit_timeout_s", int, 240,
             "Hard bound on one image submit (meta takes 20 s to 3 min to draw).", min=60, max=SUBMIT_TIMEOUT_MAX_S),
    _setting("video_submit_timeout_s", int, 420,
             "Hard bound on one video submit (meta takes 1 to 2 min to render).", min=60, max=SUBMIT_TIMEOUT_MAX_S),
    _setting("checkpoint_rest_s", int, 1800,
             "Seconds a meta account rests after Vercel's browser checkpoint (\"Failed to verify your browser\") "
             "before it is checked again. Nothing is sent, no job is charged or uses an attempt, and the account is "
             "never ended for it; each repeat within a day doubles the rest, up to meta.checkpoint_rest_max_s.",
             min=300, max=86400),
    _setting("checkpoint_rest_max_s", int, 21600,
             "The longest rest a repeated browser checkpoint gives a meta account (the rest doubles per repeat); "
             "below checkpoint_rest_s it counts as checkpoint_rest_s.",
             min=300, max=86400),
    _setting("send_gap_s", int, 600,
             "Least seconds between two jobs one meta account takes. Every job loads meta.ai afresh and passes "
             "Vercel's browser challenge; the third load within about 2 minutes met the checkpoint (25/10). "
             "0 = no gap; "
             "never more than half of unsent_deadline_s.", min=0, max=3600),
    _setting("limit_rest_s", int, 4 * 3600,
             "Seconds an account's image or video pool reads 0 (and rests) after meta answers with its limit or quota "
             "words and no media (meta names no reset time). The job still fails and is refunded.",
             min=600, max=7 * 86400),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default frames: the core frame, byte for byte the text meta was sent before it had frames (the customer's
# words, then the tracking code). The engine types it on one line; a ratio tail, when the plan sets one, follows it.
# The longest sent text is the prompt cap plus the longest frame: no prompt meta takes today is refused for length
# (its composer's own cap is not measured).
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX)


class MetaSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="browser", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_meta.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real meta.ai jar; a longer list is not a cookie export", "cookies"),
        FixedLimit("relay_meta.catalog.MAX_N", C.MAX_N,
                   "most images (n) one meta-image job from text draws in its one chat, priced per image", "images"),
        FixedLimit("relay_meta.catalog.MAX_REFS", C.MAX_REFS,
                   "most reference images one meta-image edit attaches together (a video takes one)", "images"),
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

    # ---- plan -----------------------------------------------------------------------------------------------------
    def plan(self, req: CreateRequest, settings: Any, snapshots: Mapping[str, Mapping]) -> Plan:
        """Validate and normalise. Price and timeout come from the settings, NEVER from `req.options`. Raises
        `InputError`. meta takes no duration (refused rather than ignored) and a ratio only from text alone, one of
        `RATIOS` (the engine types it after the text); a prompt naming another ratio is refused, as dola does. With
        input images an image job makes one image from up to `MAX_REFS` of them (an edit) and a video animates its one;
        without one each draws from the text, which must then show something (a video from an image may come with no
        text). An image from text alone may ask `n` images (1 to `MAX_N`, one chat: the engine asks for the count in the
        words and delivers at most n), priced per image by its own key (`image_key`); anything else makes one."""
        kind = C.KIND.get(req.model)
        if kind is None:
            raise _refuse("input.bad_model", "meta's models are %s" % ", ".join(C.KIND), "model")
        if req.ratio is not None:
            if req.images or req.ratio not in RATIOS[kind]:
                raise _refuse("input.bad_ratio", "%s takes the ratios %s from text alone (or none); a job from an "
                              "image keeps the image's shape" % (req.model, ", ".join(RATIOS[kind])), "ratio")
            other = sorted({r for r in _PROMPT_RATIO.findall(req.prompt or "") if r != req.ratio})
            if other:
                raise _refuse("input.prompt_ratio_conflict", "the prompt says %s but the ratio is %s: make them match "
                              "or remove the ratio from the prompt" % (" / ".join(other), req.ratio))
        if req.duration is not None:
            raise _refuse("input.bad_duration", "meta sets the length itself: send no duration", "duration")
        most = C.MAX_REFS if kind == "image" else 1
        if len(req.images) > most:
            raise _refuse("input.too_many_images", "%s takes at most %d input image(s)" % (req.model, most), "images")
        n = 1 if req.n is None else req.n
        # several images come from one drawing from text; an edit and a video make one
        if n != 1 and (kind != "image" or req.images or not 1 <= n <= C.MAX_N):
            raise _refuse("input.invalid", "n is 1 to %d images from text alone; an edit or a video makes one"
                          % C.MAX_N, "n")
        if kind == "image":
            if C.is_blank_prompt(req.prompt):
                raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
            if prompt_counts(req.prompt) - {n}:
                raise _refuse("input.invalid", "the prompt asks for %s image(s) but n is %d: set n to the count you "
                              "want and leave it out of the prompt" % (
                                  " / ".join(map(str, sorted(prompt_counts(req.prompt) - {n}))), n), "prompt")
            operation = "image_to_image" if req.images else "text_to_image"
        else:
            if not req.images and C.is_blank_prompt(req.prompt):
                raise _refuse("input.empty_prompt", "a video from text alone needs a prompt that shows something",
                              "prompt")
            operation = "image_to_video" if req.images else "text_to_video"
        return Plan(
            operation=operation, media_type=kind, model=req.model,
            params={"kind": kind, **({"ratio": req.ratio} if req.ratio else {}),
                    **({"n": n} if kind == "image" else {})},
            completion=Completion.HOLD, after_sent=AfterSent.RECONCILE,
            price_key=C.image_key(n) if kind == "image" else req.model, pool=kind,
            submit_timeout_s=settings.get("meta.%s_submit_timeout_s" % kind))


SPEC = MetaSpec()
