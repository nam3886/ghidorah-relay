"""gemini ProviderSpec: pure data the API imports. No engine imports."""

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
from relay_gemini import catalog as C
from relay_gemini import errors as codes
from relay_gemini.constants import (
    CHECK_INTERVAL_S,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_S,
    SUSPECT_THRESHOLD,
)
from relay_gemini.credential import MAX_COOKIES
from relay_gemini.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# Gemini's quota is two windows of units that must both hold, read with their resets in `jSf9Qc` (FINDINGS 5): fixed
# 5 h blocks and a week, placed per account (measured 25/10 on AI Pro: blocks reset at 05:07:45 and 10:07:45 UTC, the
# week at 06:07:45), so both start from the reset gemini reports. AI Pro holds 2400 units per block and 48384 per
# week; a 10 s video takes about 470 (5 per block, 102 per week) and an image about 3.2 (750 per block, 15120 per
# week: no practical cap). A free account is not measured: a day guard holds it (image 100, video 15, as before),
# which does not bind AI Pro (null). Ultra takes the free budgets. Nano Banana 2.1 (`image_pro`): none on a free
# account, which is what keeps its jobs off free accounts (a tier with no budget of its own takes the free one); 100
# a day on AI Pro and 1000 on Ultra (not measured: Google's own figures for its pro image model).
POOLS = (PoolSpec("image", "uses"), PoolSpec("image_pro", "uses"), PoolSpec("video", "uses"))
POOL_DEFAULTS = {
    "image": {"combine": "all", "windows": [
        {"name": "day", "kind": "day", "tz": "UTC", "budget": {"free": 100, "pro": None}},
        {"name": "5h", "kind": "rolling", "hours": 5, "anchor": "reset", "budget": {"pro": 750}},
        {"name": "week", "kind": "rolling", "hours": 168, "anchor": "reset", "budget": {"pro": 15120}}]},
    "image_pro": day_pool({"free": 0, "pro": 100, "ultra": 1000}),
    "video": {"combine": "all", "windows": [
        {"name": "day", "kind": "day", "tz": "UTC", "budget": {"free": 15, "pro": None}},
        {"name": "5h", "kind": "rolling", "hours": 5, "anchor": "reset", "budget": {"pro": 5}},
        {"name": "week", "kind": "rolling", "hours": 168, "anchor": "reset", "budget": {"pro": 102}}]}}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false gemini claims no new job; its account checks go on and keep the Google "
             "sessions rotated. Off until the owner turns gemini on.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a gemini job that has not delivered is given up and refunded (a send "
             "whose answer was lost is looked up in the account's chats meanwhile).", min=120),
    _setting("poll_interval_s", int, 30,
             "Seconds between two reads of a gemini job whose answer was lost, while it is looked up in the chats.",
             min=10, max=300),
    _setting("rotate_interval_s", int, 600,
             "Seconds between two rotations of an account's Google session (RotateCookies); the session ages out in "
             "about 20 min without one. The account check (every 5 min) and each job rotate when this is due.",
             min=120, max=900),
    _setting("account_min_gap_s", int, 30,
             "Minimum seconds between two sends of one gemini account: after a send the account's pool rests this "
             "long (not measured: gemini's own rate limit is unknown).", min=0, max=3600),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per job: an image by its model, a video as '<model>@10s'. An option "
        "missing here is refused (model.unpriced), never charged a guess.", C.PRICE_KEYS, C.DEFAULT_PRICES,
        unit="points", basic=True, renamed={"gemini-omni-video": ("gemini-omni-video@10s",)})),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the gemini account: one use of its image or its video budget "
        "(the quota is relative; no per-job figure exists). An option missing here is refused (model.uncosted).",
        C.PRICE_KEYS, C.DEFAULT_COSTS, unit="uses")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    _setting("video_limit_rest_s", int, 18000,
             "Seconds an account's video pool rests after gemini answers it can make no more videos for now (the "
             "short usage window is 5 h).", min=600, max=7 * 86400),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame rendered verbatim. gemini draws what the prompt says, and a tracking code typed
# into the prompt was drawn into the image (23/10: "[7134]" painted on the duck, FINDINGS 4), so the code renders as
# nothing and a lost send is found by its exact text and its start time instead.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class GeminiSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_gemini.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real Google export; a longer list is not a cookie export", "cookies"),
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
        """Validate and normalise; the plan names its price key and the core prices it (`gemini.prices`,
        `gemini.costs`). Timeouts come from the constants, NEVER from `req.options`. Raises `InputError`. One result
        per job. An image: with one input image it is edited (its shape kept, so no ratio), without one it is drawn
        from the text (a ratio of `RATIO_IDS`, or none: gemini picks); Nano Banana 2.1 takes the `image_pro` pool. A
        video: 10 s, 16:9, from the text or from one input image and the text."""
        if req.model not in (*C.IMAGE_MODELS, C.VIDEO_MODEL):
            raise _refuse("input.bad_model", "gemini's models are %s" % ", ".join((*C.IMAGE_MODELS, C.VIDEO_MODEL)),
                          "model")
        if len(req.images) > 1:
            raise _refuse("input.too_many_images", "%s takes at most one input image" % req.model, "images")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "gemini makes one result per request", "n")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        if req.model == C.VIDEO_MODEL:
            return self._video(req, settings)
        if req.duration is not None:
            raise _refuse("input.bad_duration", "an image has no duration", "duration")
        if req.ratio is not None and (req.images or req.ratio not in C.RATIO_IDS):
            raise _refuse("input.bad_ratio", "%s takes the ratios %s from text alone (or none); an edited image keeps "
                          "its shape" % (req.model, ", ".join(C.RATIO_IDS)), "ratio")
        return Plan(
            operation="image_to_image" if req.images else "text_to_image", media_type="image", model=req.model,
            params={"ratio": req.ratio} if req.ratio else {}, completion=Completion.HOLD,
            after_sent=AfterSent.RECONCILE, price_key=C.price_key(req.model),
            pool="image_pro" if req.model == C.PRO_IMAGE_MODEL else "image",
            submit_timeout_s=SUBMIT_TIMEOUT_S)

    def _video(self, req: CreateRequest, settings: Any) -> Plan:
        if req.duration not in (None, C.VIDEO_DURATION):
            raise _refuse("input.bad_duration", "%s makes %d s clips" % (req.model, C.VIDEO_DURATION), "duration")
        ratio = C.VIDEO_RATIO if req.ratio is None else req.ratio
        if ratio not in C.VIDEO_RATIO_IDS:
            raise _refuse("input.bad_ratio", "%s takes the ratio %s (or none)"
                          % (req.model, ", ".join(C.VIDEO_RATIO_IDS)), "ratio")
        return Plan(
            operation="image_to_video" if req.images else "text_to_video", media_type="video", model=req.model,
            params={"ratio": ratio, "duration": C.VIDEO_DURATION}, completion=Completion.DETACH,
            after_sent=AfterSent.RECONCILE, price_key=C.price_key(req.model), pool="video",
            submit_timeout_s=SUBMIT_TIMEOUT_S)


SPEC = GeminiSpec()
