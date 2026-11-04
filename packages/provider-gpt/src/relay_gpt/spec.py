"""gpt ProviderSpec: pure data the API imports. No engine imports."""

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
    RateAlert,
)
from relay_core.refusals import refusal_max_accounts_setting
from relay_core.settings import Spec, disabled_models
from relay_gpt import catalog as C
from relay_gpt import errors as codes
from relay_gpt.constants import (
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
from relay_gpt.credential import MAX_COOKIES
from relay_gpt.credential import parse_credential as _parse_credential


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# Images count jobs. Only Plus was measured (its remaining quota is not readable): the budgets per tier are guesses,
# kept low. The check maps the account's plan to a tier: free/go -> free, plus/team/enterprise/edu -> pro,
# pro -> ultra.
POOLS = (PoolSpec("image", "jobs"),)
POOL_DEFAULTS = {"image": day_pool({"free": 3, "pro": 40, "ultra": 100})}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false gpt claims no new job; its account checks go on. Off until the owner turns "
             "gpt on.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a gpt job that has not delivered is given up and refunded (an image took "
             "21-26 s).", min=300),
    _setting("poll_interval_s", int, 15,
             "Seconds between two reads of the conversation of a gpt job whose stream broke after the send.",
             min=5, max=120),
    _setting("account_min_gap_s", int, 30,
             "Minimum seconds between two sends of one gpt account: after a send the account's pool rests this "
             "long.", min=0, max=3600),
    _setting("image_lost_after_s", int, 600,
             "Seconds after the send before a job whose conversation still shows no image is failed and refunded.",
             min=120, max=3600),
    _setting("page_max_age_s", int, 3600,
             "Seconds the build id and the anti-bot script address read from chatgpt.com's home page are reused "
             "before the page is read again.", min=300, max=86400),
    _setting("signed_out_rest_s", int, 600,
             "Seconds a gpt account rests after a signed-out session read, then it is checked again.",
             min=60, max=86400),
    _setting("signed_out_gap_s", int, 540,
             "A signed-out read this soon after the last counted strike adds none (shorter than the rest).",
             min=0, max=86400),
    _setting("network_rest_s", int, 300,
             "Seconds a gpt account rests after its proxy could not reach chatgpt.com in a submit.",
             min=30, max=3600),
    _setting("challenge_rest_s", int, 3600,
             "Seconds a gpt account rests after chatgpt.com asked it for a challenge or a new login before a send.",
             min=300, max=86400),
    _setting("alert_turnstile_rate", float, 0.2,
             "Drift alert: share of the last 24 h gpt jobs sent with an empty Turnstile answer (the solver failed).",
             min=0.0, max=1.0),
    refusal_max_accounts_setting(
        PROVIDER, "How many accounts one job may try when chatgpt.com refuses the account before anything is made "
        "(an image limit or a challenge answered to the send). Each such answer sends the job to another account; "
        "the answer on the last one ends the job failed, with its full refund. 1 ends it at the first."),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per gpt job, keyed by model. An option missing here is refused "
        "(model.unpriced), never charged a guess.", C.PRICE_KEYS, C.DEFAULT_PRICES, unit="points", basic=True)),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the gpt account: one job of its image budget. An option missing "
        "here is refused (model.uncosted).", C.PRICE_KEYS, C.DEFAULT_COSTS, unit="jobs")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame, rendered verbatim (the text is the chat message, and a job is found by its own
# message id): `{{tracking_code}}` renders as nothing and the line breaks stay.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class GptSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True, has_reconcile=True, has_probe_proxy=False,
        proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    ref_is_conversation = True
    limits = LIMITS + (
        FixedLimit("relay_gpt.credential.MAX_COOKIES", MAX_COOKIES,
                   "far above a real chatgpt.com export; a longer list is not a cookie export", "cookies"),
    )
    rate_alerts = (RateAlert("gpt.alert_turnstile_rate", codes.WARN_TURNSTILE, "warning"),)

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
        """Validate and normalise; the plan names its price key and the core prices it (`gpt.prices`, `gpt.costs`).
        Timeouts come from the constants, NEVER from `req.options`. Raises `InputError`. One image per job."""
        if req.model != C.IMAGE_MODEL:
            raise _refuse("input.bad_model", "gpt's model is %s" % C.IMAGE_MODEL, "model")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "gpt makes one image per request", "n")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        if len(req.images) > C.MAX_IMAGE_INPUTS:
            raise _refuse("input.too_many_images", "%s takes at most %d input image" % (
                req.model, C.MAX_IMAGE_INPUTS), "images")
        if req.ratio is not None and req.ratio not in C.RATIOS:
            raise _refuse("input.bad_ratio", "%s takes the ratios %s (or none: 3:2)" % (
                req.model, ", ".join(C.RATIOS)), "ratio")
        if req.duration is not None:
            raise _refuse("input.bad_duration", "an image has no duration", "duration")
        return Plan(
            operation="image_to_image" if req.images else "text_to_image", media_type="image", model=req.model,
            params={"kind": "image", "ratio": req.ratio}, completion=Completion.HOLD, after_sent=AfterSent.RECONCILE,
            price_key=C.price_key(req.model), pool="image", submit_timeout_s=SUBMIT_TIMEOUT_S)


SPEC = GptSpec()
