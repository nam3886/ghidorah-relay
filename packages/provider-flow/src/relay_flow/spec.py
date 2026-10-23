"""flow ProviderSpec: pure data the API imports. No engine imports."""

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
from relay_core.settings import Rule, Spec, disabled_models
from relay_flow import catalog as C
from relay_flow import errors as codes
from relay_flow.constants import (
    ACCOUNT_COST,
    CAPTCHA_PROVIDERS,
    CHECK_INTERVAL_S,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_IMAGE_S,
    SUSPECT_THRESHOLD,
)
from relay_flow.credential import MAX_COOKIES
from relay_flow.credential import parse_credential as _parse_credential

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


def _captcha_provider(v: str) -> None:
    if v not in CAPTCHA_PROVIDERS:
        raise ValueError("the token source is one of %s" % ", ".join(CAPTCHA_PROVIDERS))


PRICES_RULE = Rule("each model: a non-empty name and a whole number of points, 0 or more", _prices)
CAPTCHA_RULE = Rule("one of: %s" % ", ".join(CAPTCHA_PROVIDERS), _captcha_provider)


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false flow claims no new job; its account checks go on and keep the Google "
             "sessions rotated. Off until the owner turns flow on.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a flow job that has not delivered is given up and refunded (an image "
             "the reply lost shows in the project about a minute later).", min=120),
    _setting("poll_interval_s", int, 30,
             "Seconds between two reads of a flow job whose reply was lost, while it is looked up in the project.",
             min=10, max=300),
    _setting("rotate_interval_s", int, 600,
             "Seconds between two rotations of an account's Google session (RotateCookies); the session ages out in "
             "about 20 min without one. The account check (every 5 min) and each job rotate when this is due.",
             min=120, max=900),
    _setting("captcha_provider", str, "yescaptcha",
             "Where the reCAPTCHA token of a generate comes from. Its key is the worker's FLOW_YESCAPTCHA_KEY.",
             rule=CAPTCHA_RULE),
    _setting("captcha_max_tries", int, 5,
             "Tries of one job against flow's reCAPTCHA check, each with a new token. A refused try made nothing; "
             "after the last the job goes back to the queue.", min=1, max=10),
    _setting("captcha_retry_gap_s", int, 60,
             "Seconds between two tries of one job after flow refused a token (back-to-back tries kept being "
             "refused).", min=0, max=600),
    _setting("account_min_gap_s", int, 300,
             "Minimum seconds between two jobs of one flow account: after a generate the account's pools rest this "
             "long (a 12-min pause was accepted on the first try, rapid tries were refused).", min=0, max=3600),
    _setting("prices", dict, dict(C.DEFAULT_PRICES),
             "Points charged per image, keyed by model. A model missing here is charged the fallback (1) with a "
             "warning.", rule=PRICES_RULE, basic=True),
    _setting("image_budget", int, 1000, "Images each flow account may make per day (not measured yet).", min=1,
             basic=True),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


class FlowSpec:
    name = PROVIDER
    frames = None
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", wallet_kinds=("image",), pools=(PoolSpec("image", "UTC", "flow.image_budget"),),
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_flow.credential.MAX_COOKIES", MAX_COOKIES,
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
        `req.options`. Raises `InputError`. One image per job (n > 1 later)."""
        model = C.MODELS.get(req.model)
        if model is None:
            raise _refuse("input.bad_model", "flow's models are %s" % ", ".join(C.MODELS), "model")
        if req.duration is not None:
            raise _refuse("input.bad_duration", "an image has no duration", "duration")
        if req.images:
            raise _refuse("input.too_many_images", "%s takes no input image" % req.model, "images")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "flow makes one image per request", "n")
        ratio = C.DEFAULT_RATIO if req.ratio is None else req.ratio
        if ratio not in C.ASPECTS:
            raise _refuse("input.bad_ratio", "%s takes the ratios %s (or none)" % (req.model, ", ".join(C.ASPECTS)),
                          "ratio")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        price, warnings = self._price(settings, req.model)
        return Plan(
            operation="text_to_image", media_type="image", model=req.model,
            params={"model": model[0], "aspect": C.ASPECTS[ratio], "ratio": ratio},
            completion=Completion.HOLD, after_sent=AfterSent.RECONCILE, wallet_kind="image", price=price,
            pool="image", account_cost=ACCOUNT_COST, submit_timeout_s=SUBMIT_TIMEOUT_IMAGE_S,
            warnings=tuple(warnings))

    @staticmethod
    def _price(settings: Any, model: str) -> tuple[int, list[Warning]]:
        price, missing = C.lookup_price(settings.get("flow.prices"), model)
        if missing is None:
            return price, []
        log.warning("flow price: no entry for %r in flow.prices, charging the fallback of %d point(s)", missing, price)
        return price, [Warning(codes.WARN_PRICE_FALLBACK,
                               "no price is set for %s: charged the fallback of %d point(s)" % (missing, price))]


SPEC = FlowSpec()
