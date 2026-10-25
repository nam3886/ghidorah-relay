"""qwen ProviderSpec: pure data the API imports. No engine imports."""

import logging
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
from relay_qwen import catalog as C
from relay_qwen import errors as codes
from relay_qwen.constants import (
    CHECK_INTERVAL_S,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_IMAGE_S,
    SUBMIT_TIMEOUT_VIDEO_S,
    SUSPECT_THRESHOLD,
)
from relay_qwen.credential import MAX_COOKIES
from relay_qwen.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# Video counts qwen credits (a 5 s HappyHorse 1.1 video takes 30; a video that costs more than any tier holds is
# refused, input.too_costly): a free account is granted 40 that expire 24 h later, and the next grant shows on the
# following read, so the window rolls 24 h from the grant (measured 25/10). Images count jobs: 10 a day per account
# (measured 25/10; whether the day is a fixed hour or rolling is not settled: a UTC day). The pro-only options
# (`qwen-image-3.0-pro`, HappyHorse 1080P) have their own pools with nothing on a free account, which is what keeps
# their jobs off free accounts (a tier with no budget of its own takes the free one). Not measured on a paid account:
# the pro plan's 10500 credits a month (its plan page) and 100 pro images a day (a guess).
POOLS = (PoolSpec("video", "credits"), PoolSpec("image", "jobs"), PoolSpec("video_pro", "credits"),
         PoolSpec("image_pro", "jobs"))
POOL_DEFAULTS = {"video": {"combine": "sum", "windows": [
                     {"name": "24h", "kind": "rolling", "hours": 24, "budget": {"free": 40}}]},
                 "image": day_pool({"free": 10}),
                 "video_pro": {"combine": "sum", "windows": [
                     {"name": "month", "kind": "month", "tz": "UTC", "budget": {"free": 0, "pro": 10500}}]},
                 "image_pro": day_pool({"free": 0, "pro": 100})}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false qwen claims no new job; its account checks go on. Off until the owner turns "
             "qwen on.", basic=True),
    _setting("render_deadline_s", int, 2700,
             "Seconds after the send before a qwen job that has not delivered is given up and refunded (a video "
             "took 9.5-10.5 min).", min=600),
    _setting("poll_interval_s", int, 30,
             "Seconds between two reads of a rendering qwen video (each read is about 0.5 s; a video renders for "
             "about 10 min).", min=10, max=300),
    _setting("account_min_gap_s", int, 60,
             "Minimum seconds between two generates of one qwen account: after a generate the account's pools rest "
             "this long.", min=0, max=3600),
    _setting("bx_max_age_s", int, 1500,
             "Seconds one minted anti-bot pair (bx-ua, bx-umidtoken) is reused before the browser mints a new one. "
             "A pair worked at 30 min and 3 sends; the account check mints again at half this age.", min=300,
             max=7200),
    _setting("image_lost_after_s", int, 900,
             "Seconds after the send before an image job whose chat still shows no image is failed and refunded "
             "(an image is drawn only while its stream is open: a lost stream never delivered later).", min=300,
             max=3600),
    _setting("refusal_grace_s", int, 300,
             "Seconds after the send before a video whose answer was lost, and whose chat holds a reply in words with "
             "no task, is failed as a refusal and refunded (a task named within this time is followed).", min=60,
             max=3600),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per job, keyed by model for an image and by model@<seconds>s for a "
        "video. An option missing here is refused (model.unpriced), never charged a guess.", C.PRICE_KEYS,
        C.DEFAULT_PRICES, unit="points", basic=True)),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the qwen account: an image counts one job of the image budget, a "
        "video its qwen credits (qwen's price list). An option missing here is refused (model.uncosted).",
        C.PRICE_KEYS, C.DEFAULT_COSTS, unit="jobs or qwen credits")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame, rendered verbatim (the text is the chat message, and a job is found by its own
# chat id): `{{tracking_code}}` renders as nothing and the line breaks stay.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME},
                    warnings=lambda kind, text: (), max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class QwenSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_qwen.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real qwen.ai export; a longer list is not a cookie export", "cookies"),
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
        """Validate and normalise; the plan names its price key and the core prices it (`qwen.prices`,
        `qwen.costs`). Timeouts come from the constants, NEVER from `req.options`. Raises `InputError`. One result per
        job."""
        video = C.VIDEO_MODELS.get(req.model)
        image = C.IMAGE_MODELS.get(req.model)
        if video is None and image is None:
            raise _refuse("input.bad_model", "qwen's models are %s" % ", ".join((*C.IMAGE_MODELS, *C.VIDEO_MODELS)),
                          "model")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "qwen makes one result per request", "n")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        resolution = req.options.get("resolution")
        if image is not None:
            if len(req.images) > C.MAX_IMAGE_INPUTS:
                raise _refuse("input.too_many_images", "%s takes at most %d input images" % (
                    req.model, C.MAX_IMAGE_INPUTS), "images")
            pro = image[2] == "image_pro"
            ratios = C.PRO_IMAGE_RATIOS if pro else C.IMAGE_RATIOS
            ratio = ("auto" if pro else C.DEFAULT_RATIO) if req.ratio is None else req.ratio
            if ratio not in ratios:
                raise _refuse("input.bad_ratio", "%s takes the ratios %s (or none)" % (req.model, ", ".join(ratios)),
                              "ratio")
            if req.duration is not None:
                raise _refuse("input.bad_duration", "an image has no duration", "duration")
            if resolution is not None:
                raise _refuse("input.invalid", "an image takes no resolution", "options")
            kind, pool, resolution = "image", image[2], C.DEFAULT_RESOLUTION
            operation = "image_to_image" if req.images else "text_to_image"
            params = {"kind": kind, "model": image[0], "ratio": ratio}
        else:
            # one start image is `standard`; more go by the model's other mode (reference images, or first and last)
            mode = "standard" if len(req.images) <= 1 else video[2]
            if len(req.images) > C.MODE_MAX_IMAGES[video[2]]:
                raise _refuse("input.too_many_images", "%s takes %s" % (req.model, (
                    "one start image or up to 9 reference images" if video[2] == "multi-reference"
                    else "one start image or a first and a last frame")), "images")
            ratio = C.DEFAULT_RATIO if req.ratio is None else req.ratio
            if ratio not in C.VIDEO_RATIOS:
                raise _refuse("input.bad_ratio", "%s takes the ratios %s (or none)" % (
                    req.model, ", ".join(C.VIDEO_RATIOS)), "ratio")
            duration = C.DEFAULT_DURATION if req.duration is None else req.duration
            if duration not in C.DURATIONS:
                raise _refuse("input.bad_duration", "%s makes %s s clips" % (
                    req.model, ", ".join(map(str, C.DURATIONS))), "duration")
            resolution = C.DEFAULT_RESOLUTION if resolution is None else resolution
            if resolution not in C.RESOLUTIONS:
                raise _refuse("input.invalid", "options.resolution is one of %s" % ", ".join(C.RESOLUTIONS),
                              "options")
            kind, pool = "video", C.video_pool(req.model, resolution)
            # what qwen's own credit check is asked about: the option's cost (`qwen.costs`); a missing one is refused
            # by the core's pricing, so this job never reaches a submit without it
            credits = settings.get("qwen.costs").get(C.price_key(req.model, duration, resolution))
            operation = "image_to_video" if req.images else "text_to_video"
            params = {"kind": kind, "model": video[0], "ratio": ratio, "duration": duration, "credits": credits,
                      "resolution": resolution.upper(), "mode": mode}
        return Plan(
            operation=operation, media_type=kind, model=req.model, params=params,
            completion=Completion.HOLD if kind == "image" else Completion.DETACH, after_sent=AfterSent.RECONCILE,
            price_key=C.price_key(req.model, params.get("duration"), resolution), pool=pool,
            submit_timeout_s=SUBMIT_TIMEOUT_IMAGE_S if kind == "image" else SUBMIT_TIMEOUT_VIDEO_S)


SPEC = QwenSpec()
