"""vibes ProviderSpec: pure data the API imports. No engine imports."""

import logging
from collections.abc import Mapping
from typing import Any

from relay_core.providers import (
    AfterSent,
    Caps,
    Completion,
    CreateRequest,
    Dead,
    FixedLimit,
    InputError,
    ModelSpec,
    ParsedCredential,
    Plan,
    PoolSpec,
    Warning,
)
from relay_core.settings import Rule, Spec
from relay_vibes import catalog as C
from relay_vibes import errors as codes
from relay_vibes.constants import (
    ACCOUNT_COST,
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


def _prices(v: dict) -> None:
    """The price table is the way customers are charged: name the exact fault."""
    for model, price in v.items():
        if not isinstance(model, str) or not model.strip():
            raise ValueError("a price has an empty model name")
        if type(price) is not int:
            raise ValueError("the price for %s is not a whole number: %r" % (model, price))
        if price < 0:
            raise ValueError("the price for %s is negative" % model)


PRICES_RULE = Rule("each model: a non-empty name and a whole number of points, 0 or more", _prices)


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false vibes claims no new job. Off until vibes can submit."),
    _setting("render_deadline_s", int, 1800,
             "Seconds after the send before a vibes job that has not delivered is given up (a video takes about "
             "5 min).", min=60),
    _setting("prices", dict, dict(C.DEFAULT_PRICES),
             "Points charged per generation, keyed by model. A model missing here is charged the fallback (1) with a "
             "warning.", rule=PRICES_RULE),
    # vibes' daily quota is not measured yet: the budgets start far above any real day, and the account check reads
    # the used-up banner instead.
    _setting("image_budget", int, 1000, "Image jobs each vibes account may take per day (not measured yet).", min=1),
    _setting("video_budget", int, 1000, "Videos each vibes account may make per day (not measured yet).", min=1),
))


class VibesSpec:
    name = PROVIDER
    frames = None
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", wallet_kinds=("video", "image"),
        pools=(PoolSpec("video", "UTC", "vibes.video_budget"), PoolSpec("image", "UTC", "vibes.image_budget")),
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=False, has_reconcile=False, has_probe_proxy=False,
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
        if req.ratio is not None and req.ratio not in C.RATIOS[kind]:
            allowed = ", ".join(C.RATIOS[kind])
            raise _refuse("input.bad_ratio", "%s takes the ratios %s (or none)" % (req.model, allowed), "ratio")
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
        price, warnings = self._price(settings, req.model)
        return Plan(
            operation=operation, media_type=kind, model=req.model, params=params,
            completion=Completion.HOLD if kind == "image" else Completion.DETACH, after_sent=AfterSent.RECONCILE,
            wallet_kind=kind, price=price, pool=kind, account_cost=ACCOUNT_COST, submit_timeout_s=_SUBMIT_TIMEOUT[kind],
            warnings=tuple(warnings))

    @staticmethod
    def _price(settings: Any, model: str) -> tuple[int, list[Warning]]:
        price, missing = C.lookup_price(settings.get("vibes.prices"), model)
        if missing is None:
            return price, []
        log.warning("vibes price: no entry for %r in vibes.prices, charging the fallback of %d point(s)", missing,
                    price)
        return price, [Warning(codes.WARN_PRICE_FALLBACK,
                               "no price is set for %s: charged the fallback of %d point(s)" % (missing, price))]


SPEC = VibesSpec()
