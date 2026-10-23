"""gemini ProviderSpec: pure data the API imports. No engine imports."""

import logging
from collections.abc import Mapping
from typing import Any

from relay_core import frames
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
    Warning,
)
from relay_core.settings import Rule, Spec, disabled_models
from relay_gemini import catalog as C
from relay_gemini import errors as codes
from relay_gemini.constants import (
    ACCOUNT_COST,
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


def _prices(v: dict) -> None:
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
    _setting("prices", dict, dict(C.DEFAULT_PRICES),
             "Points charged per image, keyed by model. A model missing here is charged the fallback (1) with a "
             "warning.", rule=PRICES_RULE, basic=True),
    _setting("image_budget", int, 100, "Images each gemini account may make per day (not measured yet).", min=1,
             basic=True),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame rendered verbatim. gemini draws what the prompt says, and a tracking code typed
# into the prompt was drawn into the image (23/10: "[7134]" painted on the duck, FINDINGS 4), so the code renders as
# nothing and a lost send is found by its exact text and its start time instead.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class GeminiSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", wallet_kinds=("image",), pools=(PoolSpec("image", "UTC", "gemini.image_budget"),),
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
        """Validate and normalise. Price and timeout come from the settings and the constants, NEVER from
        `req.options`. Raises `InputError`. One image per job; with one input image it is edited (its shape kept, so
        no ratio), without one it is drawn from the text (a ratio of `RATIO_IDS`, or none: gemini picks)."""
        if req.model != C.IMAGE_MODEL:
            raise _refuse("input.bad_model", "gemini's model is %s" % C.IMAGE_MODEL, "model")
        if req.duration is not None:
            raise _refuse("input.bad_duration", "an image has no duration", "duration")
        if len(req.images) > 1:
            raise _refuse("input.too_many_images", "%s takes at most one input image" % req.model, "images")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "gemini makes one image per request", "n")
        if req.ratio is not None and (req.images or req.ratio not in C.RATIO_IDS):
            raise _refuse("input.bad_ratio", "%s takes the ratios %s from text alone (or none); an edited image keeps "
                          "its shape" % (req.model, ", ".join(C.RATIO_IDS)), "ratio")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        price, warnings = self._price(settings, req.model)
        return Plan(
            operation="image_to_image" if req.images else "text_to_image", media_type="image", model=req.model,
            params={"ratio": req.ratio} if req.ratio else {}, completion=Completion.HOLD,
            after_sent=AfterSent.RECONCILE, wallet_kind="image", price=price, pool="image",
            account_cost=ACCOUNT_COST, submit_timeout_s=SUBMIT_TIMEOUT_S, warnings=tuple(warnings))

    @staticmethod
    def _price(settings: Any, model: str) -> tuple[int, list[Warning]]:
        price, missing = C.lookup_price(settings.get("gemini.prices"), model)
        if missing is None:
            return price, []
        log.warning("gemini price: no entry for %r in gemini.prices, charging the fallback of %d point(s)", missing,
                    price)
        return price, [Warning(codes.WARN_PRICE_FALLBACK,
                               "no price is set for %s: charged the fallback of %d point(s)" % (missing, price))]


SPEC = GeminiSpec()
