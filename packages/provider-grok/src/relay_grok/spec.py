"""grok ProviderSpec: pure data the API imports. No engine imports, no curl_cffi."""

import re
from collections.abc import Mapping
from typing import Any

from relay_core import frames
from relay_core.pool import pools_setting
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
from relay_core.settings import Rule, Spec, disabled_models
from relay_grok import catalog as C
from relay_grok import errors as codes
from relay_grok.constants import (
    CHECK_INTERVAL_S,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    QUOTA_WINDOW,
    STRIKE_DEAD_REASON,
    SUSPECT_THRESHOLD,
    VIDEO_DURATION_S,
)
from relay_grok.credential import parse_credential as _parse_credential

# A mode flag typed into the prompt (`--mode=extremely-spicy`): the customer never picks grok's mode.
_MODE_FLAG = re.compile(r"--mode\s*=", re.IGNORECASE)


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    """An `InputError` for the core, with the human text kept on `.detail` (for tests and logs)."""
    err = InputError(code, param)
    err.detail = detail
    return err


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


STATSIG_MODES = ("off", "generate")
T2I_TRANSPORTS = ("browser", "curl")


def _one_of(choices: tuple[str, ...]):
    def check(value: Any) -> None:
        if value not in choices:
            raise ValueError("must be one of %s" % ", ".join(choices))
    return check


def _rolling(hours: int, free: int) -> dict[str, Any]:
    """One `grok.pools` entry: a rolling window of `hours`, its blocks placed by the refill grok reports."""
    return {"combine": "sum", "windows": [{"name": QUOTA_WINDOW, "kind": "rolling", "hours": hours, "anchor": "reset",
                                           "budget": {"free": free}}]}


# Uses of a free account's `imagePro` (a text-to-image and an edit, one per image; about 8 a day) and of its
# `video720p` (one a day; the next one 25 h after the send). grok's own `quota_info` count wins over these budgets.
POOLS = (PoolSpec("image", "uses"), PoolSpec("video", "uses"))
POOL_DEFAULTS = {
    "image": _rolling(24, 8),
    "video": _rolling(25, 1),
}

# What a send that may have been taken does when its answer is unclear. Step 0 (plans/grok-design-261106/step0.md)
# found every operation readable back by its conversation id (GET .../conversations/<id>/responses), so all three are
# reconciled; the owner ruled it for text-to-image too on 06/11 (replacing Q5's refund at once): it fails with its
# refund only when the read-back finds nothing by the deadline. One line per operation; `Caps.has_reconcile` follows.
AFTER_SENT = {
    "text_to_image": AfterSent.RECONCILE,
    "image_to_image": AfterSent.RECONCILE,
    "text_to_video": AfterSent.RECONCILE,
}
# The submit timeout setting of each operation.
_TIMEOUT = {"text_to_image": "image_submit_timeout_s", "image_to_image": "edit_submit_timeout_s",
            "text_to_video": "video_submit_timeout_s"}

_SETTINGS = dict((
    _setting("claims_enabled", bool, False,
             "Drain mode off: when false grok claims no new job; its account checks go on. Off until the owner turns "
             "grok on.", basic=True),
    _setting("render_deadline_s", int, 900,
             "Seconds after the send before a grok job that has not delivered is given up and refunded.", min=60),
    _setting("image_submit_timeout_s", int, 90,
             "Hard bound on one text-to-image submit (2 images took 4.5 s).", min=30, max=600),
    _setting("edit_submit_timeout_s", int, 120,
             "Hard bound on one image edit submit, uploads included (6.2 s, the upload 3.0 s).", min=30, max=600),
    _setting("video_submit_timeout_s", int, 240,
             "Hard bound on one text-to-video submit (a 6 s video took 34.5 s).", min=60, max=720),
    _setting("send_gap_s", int, 30,
             "Least seconds between two jobs one grok account takes. 0 = no gap; never more than half of "
             "unsent_deadline_s.", min=0, max=3600),
    _setting("limit_rest_s", int, 86400,
             "Seconds an account's image or video pool reads 0 (and rests) when grok answers its limit without "
             "naming when the uses come back. The job goes to another account (see grok.refusal_max_accounts).",
             min=600, max=7 * 86400),
    _setting("rate_limit_rest_s", int, 3600,
             "Seconds an account's image pool rests when grok answers its image rate limit before anything is made "
             "(its uses stay as grok counts them). The job goes to another account (see grok.refusal_max_accounts).",
             min=60, max=86400),
    refusal_max_accounts_setting(
        PROVIDER, "How many accounts one job may try when grok answers its limit to the send before anything is made "
        "(HTTP 429, its usage-limit words before any progress, or its image rate limit before any conversation). Each "
        "such answer empties that account's pool until its refill (a rate limit rests it grok.rate_limit_rest_s) and "
        "sends the job to another account; the answer on the last one ends the job failed (grok.limit), with its full "
        "refund. 1 ends it at the first."),
    _setting("decoy_min_s", int, 15,
             "A video finished faster than this many seconds after the send is a stock clip, not the one asked for: "
             "the job fails refunded and the account's video pool rests grok.decoy_rest_s. 0 = no time check (a final "
             "frame without rRated is still one).", min=0, max=120),
    _setting("decoy_rest_s", int, 86400,
             "Seconds an account's video pool rests after it answered a stock clip.", min=0, max=7 * 86400),
    _setting("signed_out_rest_s", int, 600,
             "Seconds a grok account rests after a signed-out (or blocked) read, then it is checked again.",
             min=60, max=86400),
    _setting("signed_out_gap_s", int, 540,
             "A signed-out (or blocked) read this soon after the last counted strike adds none (shorter than the "
             "rest).", min=0, max=86400),
    _setting("cf_rest_s", int, 600,
             "Seconds a grok account rests after a Cloudflare challenge page (nothing sent, no attempt used).",
             min=60, max=86400),
    _setting("antibot_rest_s", int, 1800,
             "Seconds a grok account rests after grok's anti-bot refusal (403 code 7; nothing sent, no attempt used).",
             min=60, max=86400),
    (PROVIDER + ".statsig_mode", Spec(
        PROVIDER + ".statsig_mode", str, "off",
        "off: send no x-statsig-id header (what works today). generate: send one made from the seed on grok.com's "
        "page; with no seed found it sends none and logs it.",
        rule=Rule("off or generate", _one_of(STATSIG_MODES), STATSIG_MODES))),
    (PROVIDER + ".t2i_transport", Spec(
        PROVIDER + ".t2i_transport", str, "browser",
        "How a text to image is sent. browser: from a Chrome page on the account's proxy (grok.com refused the plain "
        "socket of most accounts with its rate limit on 06/11). curl: the plain socket, no browser. Everything else "
        "(quota, read-back, downloads, edit, video) is plain HTTP either way.",
        rule=Rule("browser or curl", _one_of(T2I_TRANSPORTS), T2I_TRANSPORTS))),
    _setting("browsers", int, 2,
             "How many Chromes one grok worker opens at once for text to image (grok.t2i_transport = browser). A job "
             "that finds none free waits, inside its submit timeout. Each is a Chrome: more at once, more memory.",
             min=1, max=4),
    _setting("alert_antibot_rate", float, 0.1,
             "Drift alert: share of the last 24 h grok jobs that ended on grok's anti-bot layer (a Cloudflare page or "
             "403 code 7). A jump means the anti-bot layer is back.", min=0.0, max=1.0),
    _setting("alert_decoy_rate", float, 0.2,
             "Drift alert: share of the last 24 h grok jobs that ended with a stock clip instead of the video "
             "(grok.decoy_video).", min=0.0, max=1.0),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per grok job, keyed by its option. An option missing here is refused "
        "(model.unpriced), never charged a guess.", C.PRICE_KEYS, C.DEFAULT_PRICES, unit="points", basic=True)),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the grok account: one use of its pool per image, one per video. An "
        "option missing here is refused (model.uncosted).", C.PRICE_KEYS, C.DEFAULT_COSTS, unit="uses")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame, rendered verbatim (a job is found again by its ids, never by a code typed into
# the prompt, which grok may draw into the image): `{{tracking_code}}` renders as nothing.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class GrokSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", pools=POOLS,
        account_slots=1, max_attempts=MAX_ATTEMPTS, suspect_threshold=SUSPECT_THRESHOLD,
        strike_effect=Dead(STRIKE_DEAD_REASON), has_poll=True,
        has_reconcile=AfterSent.RECONCILE in AFTER_SENT.values(), has_probe_proxy=False,
        proxy_rest_s=PROXY_REST_S, check_interval_s=CHECK_INTERVAL_S)
    limits = LIMITS + (
        FixedLimit("relay_grok.catalog.MAX_N", C.MAX_N,
                   "most images (n) one grok-image job from text draws in its one send, priced per image", "images"),
        FixedLimit("relay_grok.catalog.MAX_REFS", C.MAX_REFS, "most input images one grok-image edit takes",
                   "images"),
    )
    rate_alerts = (RateAlert("grok.alert_antibot_rate", (codes.ANTIBOT, codes.CLOUDFLARE), "error_code"),
                   RateAlert("grok.alert_decoy_rate", codes.DECOY_VIDEO, "error_code"))

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
        """Validate and normalise; the plan names its price key and the core prices it (`grok.prices`, `grok.costs`).
        Timeouts come from the settings, NEVER from `req.options`. Raises `InputError`. grok-image draws n = 1..MAX_N
        images from text (a ratio of IMAGE_RATIOS, 1:1 without one), or edits one image from 1..MAX_REFS inputs (no
        ratio: it keeps the input's shape; n = 1). grok-video makes one 6 s video from text only (a ratio of
        VIDEO_RATIOS, 16:9 without one). A prompt may not carry a `--mode=` flag."""
        kind = C.KIND.get(req.model)
        if kind is None:
            raise _refuse("input.bad_model", "grok's models are %s" % ", ".join(C.KIND), "model")
        prompt = req.prompt or ""
        if C.is_blank_prompt(prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        if len(prompt) > PROMPT_MAX_CHARS:
            raise _refuse("input.prompt_too_long", "the prompt is at most %d characters" % PROMPT_MAX_CHARS, "prompt")
        if _MODE_FLAG.search(prompt):
            raise _refuse("input.invalid", "the prompt may not set grok's mode (--mode=)", "prompt")
        if req.duration is not None and (kind != "video" or req.duration != VIDEO_DURATION_S):
            raise _refuse("input.bad_duration", "grok-video is %d s long (send %d or no duration); an image has none"
                          % (VIDEO_DURATION_S, VIDEO_DURATION_S), "duration")
        most = C.MAX_REFS if kind == "image" else 0
        if len(req.images) > most:
            raise _refuse("input.too_many_images", "%s takes at most %d input image(s)" % (req.model, most), "images")
        n = 1 if req.n is None else req.n
        if n != 1 and (kind != "image" or req.images or not 1 <= n <= C.MAX_N):
            raise _refuse("input.invalid", "n is 1 to %d images from text alone; an edit or a video makes one"
                          % C.MAX_N, "n")
        if kind == "image" and req.images:
            if req.ratio is not None:
                raise _refuse("input.bad_ratio", "an edit keeps the input image's shape: send no ratio", "ratio")
            operation, params, price_key = "image_to_image", {"kind": "image", "n": 1}, C.image_key(1)
        elif kind == "image":
            ratio = C.DEFAULT_IMAGE_RATIO if req.ratio is None else req.ratio
            if ratio not in C.IMAGE_RATIOS:
                raise _refuse("input.bad_ratio", "%s takes the ratios %s" % (req.model, ", ".join(C.IMAGE_RATIOS)),
                              "ratio")
            operation, params, price_key = "text_to_image", {"kind": "image", "n": n, "ratio": ratio}, C.image_key(n)
        else:
            ratio = C.DEFAULT_VIDEO_RATIO if req.ratio is None else req.ratio
            if ratio not in C.VIDEO_RATIOS:
                raise _refuse("input.bad_ratio", "%s takes the ratios %s" % (req.model, ", ".join(C.VIDEO_RATIOS)),
                              "ratio")
            operation, params, price_key = "text_to_video", {"kind": "video", "ratio": ratio,
                                                             "duration": VIDEO_DURATION_S}, C.VIDEO_MODEL
        return Plan(
            operation=operation, media_type=kind, model=req.model, params=params, completion=Completion.HOLD,
            after_sent=AFTER_SENT[operation], price_key=price_key, pool=kind,
            submit_timeout_s=settings.get("%s.%s" % (PROVIDER, _TIMEOUT[operation])))


SPEC = GrokSpec()
