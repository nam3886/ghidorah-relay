"""dola ProviderSpec: pure data the API imports (contracts §5.1, §5.4.1). No engine, no browser imports."""

import logging
from collections.abc import Mapping
from typing import Any

from relay_core.providers import (
    AfterSent,
    Caps,
    Completion,
    CreateRequest,
    Dead,
    InputError,
    ModelSpec,
    ParsedCredential,
    Plan,
    PoolSpec,
    Warning,
)
from relay_core.settings import Spec
from relay_dola import catalog as C
from relay_dola import errors as codes
from relay_dola.classify import no_result_cause
from relay_dola.conflicts import find_conflict
from relay_dola.constants import (
    BROWSER_SLOTS_MAX,
    BROWSER_SLOTS_MIN,
    CATALOG_REFRESH_S,
    CHECK_INTERVAL_S,
    IMAGE_ACCOUNT_COST,
    MAX_ATTEMPTS,
    MAX_REFERENCE_IMAGES,
    PROMPT_HINT_MAX,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_IMAGE_S,
    SUBMIT_TIMEOUT_VIDEO_S,
    SUSPECT_THRESHOLD,
)
from relay_dola.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)

NATIVE_NAME_HELP = ("the native door takes the model name alone: send video `duration` and `ratio` as fields, and "
                    "image `ratio` and `options.style` as fields (the suffix names such as "
                    "`seedance-2.5-30s-16:9` belong to the OpenAI and Gemini doors)")

DEFAULT_VIDEO_HINT = (
    "Generate it now as a single video in one go — do not ask questions, do not offer options, "
    "and do not split it into segments. Always reply to me in English; keep any dialogue and "
    "on-screen text in the video exactly as written in the prompt.")


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    """An `InputError` for the core, with the human text kept on `.detail` (the core answers with the rule's own
    message; the text is for tests, logs and a later core that surfaces it)."""
    err = InputError(code, param)
    err.detail = detail
    return err


# ---- settings validators (the core's Spec.check raises ValueError for a value of the right type that is out) ----
def _at_least(n: int):
    def check(v) -> None:
        if v < n:
            raise ValueError("must be at least %d" % n)
    return check


def _rate(v: float) -> None:
    if not 0.0 <= v <= 1.0:
        raise ValueError("must be between 0 and 1")


def _slots(v: int) -> None:
    if not BROWSER_SLOTS_MIN <= v <= BROWSER_SLOTS_MAX:
        raise ValueError("must be between %d and %d" % (BROWSER_SLOTS_MIN, BROWSER_SLOTS_MAX))


def _hint(v: str) -> None:
    if "\n" in v or "\r" in v:
        raise ValueError("must be one line: dola sends a message at each Enter, so a line break would split the order")
    if len(v) > PROMPT_HINT_MAX:
        raise ValueError("must be at most %d characters (got %d)" % (PROMPT_HINT_MAX, len(v)))


def _prices(v: dict) -> None:
    """The price table is the way customers are charged: name the exact fault."""
    for tier, price in v.items():
        if not isinstance(tier, str) or not tier.strip():
            raise ValueError("a price tier has an empty name")
        if type(price) is not int:
            raise ValueError("the price for %s is not a whole number: %r" % (tier, price))
        if price < 0:
            raise ValueError("the price for %s is negative" % tier)


def _setting(key: str, kind: type, default: Any, description: str, check=None) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, check)


_SETTINGS = dict((
    _setting("claims_enabled", bool, True, "Drain mode off: when false dola claims no new job."),
    _setting("render_deadline_s", int, 9000,
             "Seconds to wait for dola to render one submission before giving up on it (the new UI promises up to 2 "
             "hours).", _at_least(60)),
    _setting("video_prompt_hint", str, DEFAULT_VIDEO_HINT,
             "Sentence added after the customer's prompt when a VIDEO job is sent (images are left alone). Stops dola "
             "asking questions, splitting a 30 s order in two or drawing a storyboard. Empty = off. A job keeps the "
             "sentence it was created with.", _hint),
    _setting("prices", dict, dict(C.DEFAULT_PRICES),
             "Points charged per generation, keyed '<model>@<N>s' for video and '<model>' for image. A tier missing "
             "here is charged the fallback (video 3, image 1) with a warning.", _prices),
    _setting("video_budget", int, 4, "Video points each dola account gets per day (dola reports its own number and "
             "that wins).", _at_least(1)),
    _setting("image_budget", int, 40, "Image jobs each dola account may take per day (measured about 40; dola never "
             "reports it).", _at_least(1)),
    _setting("master_quality", bool, True,
             "Deliver the H.264 master (720p, about 12 Mbps, no watermark) instead of the light HEVC rendition; falls "
             "back to the rendition when the master cannot be fetched."),
    _setting("alert_duration_mismatch_rate", float, 0.2,
             "Drift alert: share of the last 24 h jobs delivered with the wrong length.", _rate),
    _setting("alert_watermark_only_rate", float, 0.2,
             "Drift alert: share of the last 24 h jobs that failed as watermark-only.", _rate),
    _setting("alert_content_refused_rate", float, 0.5,
             "Drift alert: share of the last 24 h submits refused for content.", _rate),
    _setting("browser_slots", int, 8,
             "Browser sessions at once (live; capped by the environment ceiling). Lowering retires idle slots.",
             _slots),
))


class DolaSpec:
    name = PROVIDER
    caps = Caps(
        execution="browser", wallet_kinds=("video", "image"),
        pools=(PoolSpec("video", "UTC", "dola.video_budget"), PoolSpec("image", "UTC", "dola.image_budget")),
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=True, titles=True,
        catalog_refresh_s=CATALOG_REFRESH_S, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)

    def catalog(self) -> list[ModelSpec]:
        return C.catalog()

    def codes(self) -> dict:
        return dict(codes.CODES)

    def settings(self) -> dict[str, Spec]:
        return dict(_SETTINGS)

    def parse_credential(self, raw: bytes) -> ParsedCredential:
        return _parse_credential(raw)

    def extras(self, snapshots: Mapping[str, Mapping]) -> dict:
        return C.extras(dict(snapshots))

    def deadline_cause(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str:
        """The code of a render deadline. A session that died after the order (`signed_out`, set by the poll on
        712012001) is `account.dead_session` whatever dola last said: the account, not dola's words, is why nothing
        came (R81, v1.3 #28). Otherwise dola's last words (refusal, likeness, question), else `render.deadline`."""
        if provider_state.get("signed_out"):
            return "account.dead_session"
        cause = no_result_cause(upstream_text)
        if cause:
            return cause[0]
        # the poll saw only watermarked links all along: that is the cause, not a generic timeout
        return "render.watermarked_only" if provider_state.get("watermarked_only") else "render.deadline"

    # ---- plan -----------------------------------------------------------------------------------------------------
    def plan(self, req: CreateRequest, settings: Any, snapshots: Mapping[str, Mapping]) -> Plan:
        """Validate and normalise. Price, account cost and timeout come from the settings and the constants, NEVER from
        `req.options` (the native door passes options as the client sent them). Raises `InputError`."""
        if req.door == "native" and C.carries_parameters(req.model):
            raise _refuse("input.bad_model", NATIVE_NAME_HELP, "model")
        if req.options.get("template") is not None or req.options.get("template_category") is not None:
            raise _refuse("input.invalid", "image templates are chosen in the UI and are not a parameter: send the "
                          "template's prompt as `prompt` (see GET /v1/providers/dola/options)", "options")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        try:
            gen = C.resolve(req.model)
        except C.ModelNameError as e:
            raise _refuse(e.code, str(e)) from None
        if len(req.images) > MAX_REFERENCE_IMAGES:
            raise _refuse("input.too_many_images", "at most %d reference images per request" % MAX_REFERENCE_IMAGES)
        if gen.kind == "video":
            return self._plan_video(req, gen, settings)
        return self._plan_image(req, gen, settings)

    def _plan_video(self, req: CreateRequest, gen: C.GenSpec, settings: Any) -> Plan:
        duration = gen.duration if req.duration is None else req.duration
        if duration not in C.DURATIONS_BY_MODEL[gen.model]:
            raise _refuse("input.bad_duration", "%s supports only %s seconds"
                          % (gen.model, C.DURATIONS_BY_MODEL[gen.model]), "duration")
        ratio = gen.ratio if req.ratio is None else req.ratio
        if ratio not in C.VALID_RATIOS:
            raise _refuse("input.bad_ratio", "ratio must be one of %s" % (C.VALID_RATIOS,), "ratio")
        self._check_conflict(req.prompt, "video", ratio, duration)
        price, warnings = self._price(settings, gen.model, "video", duration)
        warnings += [Warning(codes.WARN_DURATION_CAPPED, w) for w in C.video_warnings(gen.model, duration)]
        params: dict[str, Any] = {"kind": "video", "model": gen.model, "duration": duration, "ratio": ratio}
        hint = " ".join(str(settings.get("dola.video_prompt_hint") or "").split())[:PROMPT_HINT_MAX]
        if hint:
            params["prompt_hint"] = hint        # captured now: a job keeps the sentence it was created with
        return Plan(
            operation="image_to_video" if req.images else "text_to_video", media_type="video",
            model=C.public_name("video", gen.model, duration, ratio), params=params, completion=Completion.DETACH,
            after_sent=AfterSent.RECONCILE, wallet_kind="video", price=price, pool="video", account_cost=price,
            submit_timeout_s=SUBMIT_TIMEOUT_VIDEO_S, warnings=tuple(warnings))

    def _plan_image(self, req: CreateRequest, gen: C.GenSpec, settings: Any) -> Plan:
        opts = req.options
        ratio = gen.ratio if req.ratio is None else req.ratio
        if ratio is not None and ratio not in C.VALID_IMAGE_RATIOS:
            raise _refuse("input.bad_ratio", "ratio must be one of %s (or omit)" % (C.VALID_IMAGE_RATIOS,), "ratio")
        style = gen.style if opts.get("style") is None else opts["style"]
        if style is not None and style not in C.VALID_STYLES:
            raise _refuse("input.bad_style", "style must be one of %s (or omit)" % (C.VALID_STYLES,), "style")
        params: dict[str, Any] = {"kind": "image", "model": gen.model, "ratio": ratio, "style": style}
        self._check_conflict(req.prompt, "image", ratio, None)
        price, warnings = self._price(settings, gen.model, "image", None)
        return Plan(
            operation="image_to_image" if req.images else "text_to_image", media_type="image",
            model=C.public_name("image", gen.model, None, ratio, style),
            params=params, completion=Completion.HOLD, after_sent=AfterSent.RECONCILE, wallet_kind="image", price=price,
            pool="image", account_cost=IMAGE_ACCOUNT_COST, submit_timeout_s=SUBMIT_TIMEOUT_IMAGE_S,
            warnings=tuple(warnings))

    @staticmethod
    def _check_conflict(prompt: str, kind: str, ratio: str | None, duration: int | None) -> None:
        found = find_conflict(prompt, kind, ratio, duration)
        if found:
            raise _refuse(found[0], found[1])

    @staticmethod
    def _price(settings: Any, model: str, kind: str, duration: int | None) -> tuple[int, list[Warning]]:
        price, missing = C.lookup_price(settings.get("dola.prices"), model, kind, duration)
        if missing is None:
            return price, []
        log.warning("dola price: no entry for %r in dola.prices, charging the fallback of %d point(s)", missing, price)
        return price, [Warning(codes.WARN_PRICE_FALLBACK,
                               "no price is set for %s: charged the fallback of %d point(s)" % (missing, price))]


SPEC = DolaSpec()
