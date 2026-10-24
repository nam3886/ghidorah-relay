"""vibes ProviderSpec: pure data the API imports. No engine imports."""

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
from relay_vibes import catalog as C
from relay_vibes import errors as codes
from relay_vibes.constants import (
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
from relay_vibes.credential import MAX_COOKIES
from relay_vibes.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)

_SUBMIT_TIMEOUT = {"image": SUBMIT_TIMEOUT_IMAGE_S, "video": SUBMIT_TIMEOUT_VIDEO_S}


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    """An `InputError` for the core, with the human text kept on `.detail` (for tests and logs)."""
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# vibes' daily quota is not measured yet: the budgets start far above any real day, and the account check reads the
# used-up banner instead (a used-up account reports 0 and rests until the pool's next day).
POOLS = (PoolSpec("video", "jobs"), PoolSpec("image", "jobs"))
POOL_DEFAULTS = {"video": day_pool({"free": 1000}), "image": day_pool({"free": 1000})}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false vibes claims no new job. Off until the owner turns vibes on.", basic=True),
    _setting("render_deadline_s", int, 1800,
             "Seconds after the send before a vibes job that has not delivered is given up (a video takes about "
             "5 min).", min=60),
    _setting("poll_interval_s", int, 20,
             "Seconds between two reads of a rendering vibes job; each wait adds up to half again at random, so the "
             "reads never come faster. vibes finishes a render only when it is read, and reads every 2-6 s brought "
             "its security checkpoint.", min=10, max=300),
    _setting("checkpoint_rest_s", int, 600,
             "Seconds a vibes account makes no call at all after vibes' security checkpoint (a 403 challenge) "
             "answered it. Its jobs keep waiting and never fail on it; the checkpoint cleared after about 7 min "
             "without calls.", min=60, max=3600),
    _setting("account_min_gap_s", int, 8,
             "Minimum seconds between two calls of one vibes account, whatever they are (submit, poll, reconcile, "
             "download, check); Vercel blocked ~45 calls in 5.5 min.", min=2, max=60),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per job, keyed by its option. An option missing here is refused "
        "(model.unpriced), never charged a guess.", C.PRICE_KEYS, C.DEFAULT_PRICES, unit="points", basic=True,
        renamed={"vibes-video": ("vibes-video@480p", "vibes-video@720p")})),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the vibes account: one job of its daily budget (vibes shows only "
        "a used-up flag). An option missing here is refused (model.uncosted).",
        C.PRICE_KEYS, C.DEFAULT_COSTS, unit="jobs")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame, rendered verbatim (the text goes in a request field, and a job is found by
# its batch id): `{{tracking_code}}` renders as nothing and the line breaks stay, so it sends the customer's words
# byte for byte, as vibes was sent before it had frames. The longest sent text is the prompt cap plus the longest
# frame: no prompt vibes takes today is refused for length.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME},
                    warnings=lambda kind, text: (), max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class VibesSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_vibes.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real vibes.ai export; a longer list is not a cookie export", "cookies"),
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
        """Validate and normalise. Price and timeout come from the settings and the constants, NEVER from
        `req.options`. Raises `InputError`. The ratio is the request field only (no prompt flag is read); vibes sets
        the length itself, so a duration is refused rather than ignored."""
        kind = C.KIND.get(req.model)
        if kind is None:
            raise _refuse("input.bad_model", "vibes' models are %s" % ", ".join(C.KIND), "model")
        if req.duration is not None:
            raise _refuse("input.bad_duration", "vibes sets the length itself: send no duration", "duration")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "vibes makes one result per request", "n")
        if req.ratio is not None and req.ratio not in C.RATIOS[kind]:
            allowed = ", ".join(C.RATIOS[kind])
            detail = ("vibes video comes out 9:16; send no ratio or 9:16" if kind == "video"
                      else "%s takes the ratios %s (or none)" % (req.model, allowed))
            raise _refuse("input.bad_ratio", detail, "ratio")
        resolution = req.options.get("resolution")
        params: dict[str, Any] = {"kind": kind, "model": C.UPSTREAM[req.model], "ratio": req.ratio}
        if kind == "image":
            if req.images:
                raise _refuse("input.too_many_images", "vibes-image takes no input image", "images")
            if resolution is not None:
                raise _refuse("input.invalid", "vibes-image takes no resolution", "options")
            operation = "text_to_image"
        else:
            if len(req.images) > 1:
                raise _refuse("input.too_many_images", "vibes-video animates at most one image", "images")
            resolution = C.DEFAULT_RESOLUTION if resolution is None else resolution
            if resolution not in C.RESOLUTIONS:
                raise _refuse("input.invalid", "the resolution is one of %s" % ", ".join(C.RESOLUTIONS), "options")
            params["resolution"] = resolution
            operation = "image_to_video" if req.images else "text_to_video"
        if operation.startswith("text_to_") and C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        return Plan(
            operation=operation, media_type=kind, model=req.model, params=params,
            completion=Completion.HOLD if kind == "image" else Completion.DETACH, after_sent=AfterSent.RECONCILE,
            price_key=C.price_key(req.model, resolution), pool=kind, submit_timeout_s=_SUBMIT_TIMEOUT[kind])


SPEC = VibesSpec()
