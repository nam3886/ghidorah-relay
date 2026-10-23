"""meta ProviderSpec: pure data the API imports. No engine, no browser imports."""

import logging
import re
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
from relay_meta import catalog as C
from relay_meta import errors as codes
from relay_meta.constants import (
    ACCOUNT_COST,
    BROWSER_SLOTS_MAX,
    BROWSER_SLOTS_MIN,
    CHECK_INTERVAL_S,
    CHECKPOINTS_TO_OWNER,
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
             "Drain mode off: when false meta claims no new job. Off until meta can submit.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a meta job that has not delivered is given up.", min=60),
    _setting("browser_slots", int, 2, "Browser sessions at once (live). Lowering retires idle slots.",
             min=BROWSER_SLOTS_MIN, max=BROWSER_SLOTS_MAX),
    _setting("prices", dict, dict(C.DEFAULT_PRICES),
             "Points charged per generation, keyed by model. A model missing here is charged the fallback (1) with a "
             "warning.", rule=PRICES_RULE, basic=True),
    _setting("image_budget", int, 40, "Image jobs each meta account may take per day.", min=1, basic=True),
    _setting("video_budget", int, 20, "Videos each meta account may make per day.", min=1, basic=True),
    _setting("image_submit_timeout_s", int, 240,
             "Hard bound on one image submit (meta takes 20 s to 3 min to draw).", min=60, max=SUBMIT_TIMEOUT_MAX_S),
    _setting("video_submit_timeout_s", int, 420,
             "Hard bound on one video submit (meta takes 1 to 2 min to render).", min=60, max=SUBMIT_TIMEOUT_MAX_S),
    _setting("checkpoint_rest_s", int, 1800,
             "Seconds a meta account rests after Vercel's browser checkpoint (\"Failed to verify your browser\") "
             "before it is checked again. Nothing is sent and no job is charged; %d checkpoint reads in a row end "
             "the account as dead: checkpoint, for the owner." % CHECKPOINTS_TO_OWNER, min=300, max=86400),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


class MetaSpec:
    name = PROVIDER
    frames = None
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="browser", wallet_kinds=("image", "video"),
        pools=(PoolSpec("image", "UTC", "meta.image_budget"), PoolSpec("video", "UTC", "meta.video_budget")),
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        titles=False, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_meta.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real meta.ai jar; a longer list is not a cookie export", "cookies"),
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
        `RATIOS` (the engine types it after the text); a prompt naming another ratio is refused, as dola does. Each
        model takes at most one input image: with one it edits (image) or animates (video) it, without one it draws
        from the text, which must then show something (a video from an image may come with no text)."""
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
        if len(req.images) > 1:
            raise _refuse("input.too_many_images", "%s takes at most one input image" % req.model, "images")
        if kind == "image":
            if C.is_blank_prompt(req.prompt):
                raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
            operation = "image_to_image" if req.images else "text_to_image"
        else:
            if not req.images and C.is_blank_prompt(req.prompt):
                raise _refuse("input.empty_prompt", "a video from text alone needs a prompt that shows something",
                              "prompt")
            operation = "image_to_video" if req.images else "text_to_video"
        price, warnings = self._price(settings, req.model)
        return Plan(
            operation=operation, media_type=kind, model=req.model,
            params={"kind": kind, **({"ratio": req.ratio} if req.ratio else {})}, completion=Completion.HOLD,
            after_sent=AfterSent.RECONCILE, wallet_kind=kind, price=price, pool=kind, account_cost=ACCOUNT_COST,
            submit_timeout_s=settings.get("meta.%s_submit_timeout_s" % kind), warnings=tuple(warnings))

    @staticmethod
    def _price(settings: Any, model: str) -> tuple[int, list[Warning]]:
        price, missing = C.lookup_price(settings.get("meta.prices"), model)
        if missing is None:
            return price, []
        log.warning("meta price: no entry for %r in meta.prices, charging the fallback of %d point(s)", missing, price)
        return price, [Warning(codes.WARN_PRICE_FALLBACK,
                               "no price is set for %s: charged the fallback of %d point(s)" % (missing, price))]


SPEC = MetaSpec()
