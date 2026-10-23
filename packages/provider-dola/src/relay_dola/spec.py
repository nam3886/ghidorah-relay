"""dola ProviderSpec: pure data the API imports. No engine, no browser imports."""

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
    FrameRules,
    InputError,
    ModelSpec,
    ParsedCredential,
    Plan,
    PoolSpec,
    RateAlert,
    Warning,
)
from relay_core.settings import Rule, Spec
from relay_dola import catalog as C
from relay_dola import errors as codes
from relay_dola.classify import no_result_cause, order_accepted
from relay_dola.conflicts import find_conflict
from relay_dola.constants import (
    BROWSER_SLOTS_MAX,
    BROWSER_SLOTS_MIN,
    CATALOG_REFRESH_S,
    CHECK_INTERVAL_S,
    FRAME_MAX_SENT_CHARS,
    IMAGE_ACCOUNT_COST,
    LIMITS,
    MAX_ATTEMPTS,
    MAX_REFERENCE_IMAGES,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_IMAGE_S,
    SUBMIT_TIMEOUT_VIDEO_S,
    SUSPECT_THRESHOLD,
)
from relay_dola.credential import MAX_COOKIES
from relay_dola.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)

NATIVE_NAME_HELP = ("the native door takes the model name alone: send video `duration` and `ratio` as fields, and "
                    "image `ratio` and `options.style` as fields (the suffix names such as "
                    "`seedance-2.5-30s-16:9` belong to the OpenAI and Gemini doors)")

DEFAULT_VIDEO_HINT = (
    "Generate it now as a single video in one go — do not ask questions, do not offer options, "
    "and do not split it into segments. Always reply to me in English; keep any dialogue and "
    "on-screen text in the video exactly as written in the prompt.")


# The shipped default frames: byte for byte the text relay sent before frames existed, the customer's
# words, then (video only) the sentence above, then the tracking code. A default is never updated by code after it is
# seeded; the admin resets it to this text when a fix of it should reach a running database.
DEFAULT_VIDEO_FRAME = "{{client_prompt}} " + DEFAULT_VIDEO_HINT + " {{tracking_code}}"
DEFAULT_IMAGE_FRAME = "{{client_prompt}} {{tracking_code}}"
SINGLE_VIDEO_WARNING = ("This frame no longer asks for a single video: dola may ask questions, offer options or split "
                        "the order. Keep the words \"single video\" in it.")


def _frame_warnings(kind: str, text: str) -> tuple[str, ...]:
    """A video frame whose text lacks \"single video\" (case ignored) gets one warning; it is never a refusal."""
    return (SINGLE_VIDEO_WARNING,) if kind == "video" and "single video" not in text.casefold() else ()


FRAMES = FrameRules(defaults={"video": DEFAULT_VIDEO_FRAME, "image": DEFAULT_IMAGE_FRAME}, warnings=_frame_warnings,
                    max_sent_chars=FRAME_MAX_SENT_CHARS)


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    """An `InputError` for the core, with the human text kept on `.detail` (the core answers with the rule's own
    message; the text is for tests, logs and a later core that surfaces it)."""
    err = InputError(code, param)
    err.detail = detail
    return err


# ---- settings: bounds are structured on the core Spec (min, max, rule), so one text drives the check and the page ----
def _prices(v: dict) -> None:
    """The price table is the way customers are charged: name the exact fault."""
    for tier, price in v.items():
        if not isinstance(tier, str) or not tier.strip():
            raise ValueError("a price tier has an empty name")
        if type(price) is not int:
            raise ValueError("the price for %s is not a whole number: %r" % (tier, price))
        if price < 0:
            raise ValueError("the price for %s is negative" % tier)


def _send_path(v: str) -> None:
    if v not in SEND_PATHS:
        raise ValueError("must be one of %s" % ", ".join(SEND_PATHS))


SEND_PATHS = ("browser", "http")
SEND_PATH_RULE = Rule("browser or http", _send_path)
PRICES_RULE = Rule("each tier: a non-empty name and a whole number of points, 0 or more", _prices)


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


_SETTINGS = dict((
    _setting("claims_enabled", bool, True, "Drain mode off: when false dola claims no new job."),
    _setting("render_deadline_s", int, 9000,
             "Seconds to wait for dola to render one submission before giving up on it (the new UI promises up to 2 "
             "hours).", min=60),
    _setting("prices", dict, dict(C.DEFAULT_PRICES),
             "Points charged per generation, keyed '<model>@<N>s' for video and '<model>' for image. A tier missing "
             "here is charged the fallback (video 3, image 1) with a warning.", rule=PRICES_RULE),
    _setting("video_budget", int, 4, "Video points each dola account gets per day (dola reports its own number and "
             "that wins).", min=1),
    _setting("image_budget", int, 40, "Image jobs each dola account may take per day (measured about 40; dola never "
             "reports it).", min=1),
    _setting("master_quality", bool, True,
             "Deliver the H.264 master (720p, about 12 Mbps, no watermark) instead of the light HEVC rendition; falls "
             "back to the rendition when the master cannot be fetched."),
    _setting("alert_duration_mismatch_rate", float, 0.2,
             "Drift alert: share of the last 24 h jobs delivered with the wrong length.", min=0.0, max=1.0),
    _setting("alert_watermark_only_rate", float, 0.2,
             "Drift alert: share of the last 24 h jobs that failed as watermark-only.", min=0.0, max=1.0),
    _setting("alert_content_refused_rate", float, 0.5,
             "Drift alert: share of the last 24 h submits refused for content.", min=0.0, max=1.0),
    _setting("send_path", str, "browser",
             "How an order is sent to dola: 'browser' drives dola.com in Chrome; 'http' posts it signed, with no "
             "browser. Read per job at the send; polls, reconcile and downloads are HTTP either way. A job with "
             "reference images, an image with a style, or any video but Seedance 2.0 5 s 16:9 goes by browser.",
             rule=SEND_PATH_RULE),
    _setting("signer_alarm_accounts", int, 3,
             "HTTP send only: when this many different accounts get dola's 710022002/710022004 before any "
             "acknowledgement within dola.signer_alarm_window_s, relay blames its own signature: those refusals rest "
             "no account and fail no job, and HTTP sends pause for the window.", min=2),
    _setting("signer_alarm_window_s", int, 900,
             "HTTP send only: the window of dola.signer_alarm_accounts, and how long HTTP sends pause after it trips.",
             min=60),
    _setting("browser_slots", int, 4,
             "Browser sessions at once (live). Lowering retires idle slots.",
             min=BROWSER_SLOTS_MIN, max=BROWSER_SLOTS_MAX),
))


class DolaSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="browser", wallet_kinds=("video", "image"),
        pools=(PoolSpec("video", "UTC", "dola.video_budget"), PoolSpec("image", "UTC", "dola.image_budget")),
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=True, titles=True,
        catalog_refresh_s=CATALOG_REFRESH_S, proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_dola.catalog.MODEL_2_0_MAX_SECONDS", C.MODEL_2_0_MAX_SECONDS,
                   "dola always cuts a 2.0 video to this length, so no longer one is offered", "s"),
        FixedLimit("relay_dola.credential.MAX_COOKIES", MAX_COOKIES,
                   "a real dola jar holds about 20; a longer list is not a cookie export", "cookies"),
    )
    rate_alerts = (
        RateAlert("dola.alert_duration_mismatch_rate", codes.WARN_DURATION_DRIFT, "warning"),
        RateAlert("dola.alert_watermark_only_rate", codes.RENDER_WATERMARKED_ONLY, "error_code"),
        RateAlert("dola.alert_content_refused_rate", (codes.CONTENT_REFUSED, codes.LIKENESS_REFUSED), "error_code"),
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
        return C.extras(dict(snapshots))

    def deadline_cause(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str:
        """The code of a render deadline. A session that died after the order (`signed_out`, set by the poll on
        712012001) is `account.dead_session` whatever dola last said: the account, not dola's words, is why nothing
        came (R81). Otherwise dola's last words (refusal, likeness, question), else `render.deadline`."""
        if provider_state.get("signed_out"):
            return "account.dead_session"
        cause = no_result_cause(upstream_text)
        if cause:
            return cause[0]
        # the poll saw only watermarked links all along: that is the cause, not a generic timeout
        return "render.watermarked_only" if provider_state.get("watermarked_only") else "render.deadline"

    def unclear_at_deadline(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str | None:
        """What to report when a job (video or image) reaches its render deadline and dola's last words explain
        nothing: its newest paragraph. None when `deadline_cause` has a reason of its own (a dead session, watermarked
        links only, a refusal, a likeness refusal or a question), when the last words hold an acceptance (a real
        render timeout) or when there is no text. The newest paragraph is split here the way the engine splits it
        (`resolver.newest_reply`): the spec imports no engine module."""
        if not upstream_text.strip() or self.deadline_cause(upstream_text, provider_state) != "render.deadline":
            return None
        if order_accepted(upstream_text):
            return None
        return upstream_text.split("\n\n", 1)[0].strip() or None

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
