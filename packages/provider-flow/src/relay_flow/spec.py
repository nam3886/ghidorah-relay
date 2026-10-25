"""flow ProviderSpec: pure data the API imports. No engine imports."""

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
from relay_core.settings import Rule, Spec, disabled_models
from relay_flow import catalog as C
from relay_flow import errors as codes
from relay_flow.constants import (
    ANTICAPTCHA_SCORES,
    CAPTCHA_PROVIDERS,
    CHECK_INTERVAL_S,
    LIMITS,
    MAX_ATTEMPTS,
    PROMPT_MAX_CHARS,
    PROVIDER,
    PROXY_REST_S,
    STRIKE_DEAD_REASON,
    SUBMIT_TIMEOUT_S,
    SUSPECT_THRESHOLD,
    captcha_sources,
)
from relay_flow.credential import MAX_COOKIES
from relay_flow.credential import parse_credential as _parse_credential

log = logging.getLogger(__name__)


def _refuse(code: str, detail: str = "", param: str | None = None) -> InputError:
    err = InputError(code, param)
    err.detail = detail
    return err


def _anticaptcha_score(v: float) -> None:
    if v not in ANTICAPTCHA_SCORES:
        raise ValueError("Anti-Captcha's score queues are %s" % ", ".join(map(str, ANTICAPTCHA_SCORES)))


CAPTCHA_RULE = Rule("one of: %s; or several, comma-separated, tried in that order" % ", ".join(CAPTCHA_PROVIDERS),
                    captcha_sources)
SCORE_RULE = Rule("one of: %s" % ", ".join(map(str, ANTICAPTCHA_SCORES)), _anticaptcha_score)


def _setting(key: str, kind: type, default: Any, description: str, **bounds: Any) -> tuple[str, Spec]:
    full = "%s.%s" % (PROVIDER, key)
    return full, Spec(full, kind, default, description, **bounds)


# Images count jobs: a day window of 40 per account, every tier alike (an image takes no Flow credit and no daily cap
# is measured yet: a guard the admin can see and edit, FINDINGS "Catalog 25/10"). Video counts Flow credits (a Veo 3.1
# Lite video takes 10, Fast 20): 50 a day on every plan, the day opened by the first use and refilled 24 h later, and
# on AI Pro 1000 more a month from its renewal (measured 25/10: 1050 on the AI Pro account with its refill a month
# ahead, 50 on the free ones with theirs within a day; fixtures/flow/live-261025). The daily credits go first. Ultra is
# not measured: it takes the free budget (50 a day, no month). Before each video the account's own balance is read
# too: too few credits and the job goes to another account, nothing sent.
POOLS = (PoolSpec("image", "jobs"), PoolSpec("video", "credits"))
POOL_DEFAULTS = {"image": day_pool({"free": 40}),
                 "video": {"combine": "sum", "windows": [
                     {"name": "24h", "kind": "rolling", "hours": 24, "budget": {"free": 50, "pro": 50}},
                     {"name": "month", "kind": "month", "tz": "UTC", "budget": {"pro": 1000}}]}}

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
             "Where the reCAPTCHA token of a generate comes from: yescaptcha, capsolver or anticaptcha, or several "
             "comma-separated (e.g. 'capsolver,yescaptcha': the next is tried when one gives no token). Their keys "
             "are the worker's FLOW_YESCAPTCHA_KEY, FLOW_CAPSOLVER_KEY and FLOW_ANTICAPTCHA_KEY. Every call to a "
             "solver goes through the account's proxy.", rule=CAPTCHA_RULE, basic=True),
    _setting("captcha_timeout_s", int, 120,
             "Seconds one reCAPTCHA token may take, all sources of the order together (10-17 s measured); none in "
             "time is a failed try.", min=30, max=300),
    _setting("captcha_poll_s", int, 3,
             "Seconds between two reads of the solver's task.", min=1, max=30),
    _setting("capsolver_proxy_task", bool, True,
             "CapSolver solves through the account's own proxy, so the token is minted from the IP that sends the "
             "generate (the proxy's address and login are given to CapSolver). Off, or an account with no proxy: "
             "CapSolver's proxyless task, whose token is minted from CapSolver's own network, as YesCaptcha's and "
             "Anti-Captcha's are; the calls to its API still go through the account's proxy."),
    _setting("anticaptcha_min_score", float, 0.9,
             "The reCAPTCHA score queue Anti-Captcha solves in (0.3 has the most workers, 0.9 the fewest). "
             "Anti-Captcha has no proxy task for this captcha: its tokens come from its own network.",
             rule=SCORE_RULE),
    _setting("captcha_max_tries", int, 5,
             "Tries of one job against flow's reCAPTCHA check, each with a new token. A refused try made nothing; "
             "after the last the job goes back to the queue.", min=1, max=10),
    _setting("captcha_retry_gap_s", int, 60,
             "Seconds between two tries of one job after flow refused a token (back-to-back tries kept being "
             "refused).", min=0, max=600),
    _setting("traffic_rest_s", int, 1800,
             "Seconds a flow account rests after flow refused it with code 8 (too much traffic): an image goes back "
             "to the queue at once instead of trying the account again. Seen once in 20 rapid tries (23/10), where "
             "a 12-min pause was then accepted.", min=0, max=86400),
    _setting("account_min_gap_s", int, 300,
             "Minimum seconds between two jobs of one flow account: after a generate the account's pools rest this "
             "long (a 12-min pause was accepted on the first try, rapid tries were refused).", min=0, max=3600),
    (PROVIDER + ".prices", table_setting(
        PROVIDER + ".prices", "Points charged per job: an image by its model, a video as '<model>@8s'. An option "
        "missing here is refused (model.unpriced), never charged a guess.", C.PRICE_KEYS, C.DEFAULT_PRICES,
        unit="points", basic=True, renamed={"flow-veo-3.1-lite": ("flow-veo-3.1-lite@8s",)})),
    (PROVIDER + ".costs", table_setting(
        PROVIDER + ".costs", "What one job costs the flow account: an image counts one job of the image budget, a "
        "video its Flow credits. An option missing here is refused (model.uncosted).", C.PRICE_KEYS, C.DEFAULT_COSTS,
        unit="jobs or Flow credits")),
    (PROVIDER + ".pools", pools_setting(PROVIDER, POOLS, POOL_DEFAULTS)),
    (PROVIDER + ".disabled_models", disabled_models(PROVIDER, (m.id for m in C.catalog()))),
))


# The shipped default: the core frame, rendered verbatim (the text goes in a request field, and a job is found by
# its batch id): `{{tracking_code}}` renders as nothing and the line breaks stay, so it sends the customer's words
# byte for byte, as flow was sent before it had frames. The longest sent text is the prompt cap plus the longest
# frame: no prompt flow takes today is refused for length.
FRAMES = FrameRules(defaults={"image": frames.CORE_FRAME, "video": frames.CORE_FRAME}, warnings=lambda kind, text: (),
                    max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)


class FlowSpec:
    name = PROVIDER
    frames = FRAMES
    max_prompt_chars = PROMPT_MAX_CHARS
    caps = Caps(
        execution="http", pools=POOLS,
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
        """Validate and normalise; the plan names its price key and the core prices it (`flow.prices`,
        `flow.costs`). Timeouts come from the constants, NEVER from `req.options`. Raises `InputError`. One result per
        job. An input image (image to image, image to video) is
        refused with `input.unsupported`: flow's upload was never captured, so it is not guessed."""
        image, video = C.MODELS.get(req.model), C.VIDEO_MODELS.get(req.model)
        if image is None and video is None:
            raise _refuse("input.bad_model", "flow's models are %s" % ", ".join((*C.MODELS, *C.VIDEO_MODELS)),
                          "model")
        if req.images:
            raise _refuse("input.unsupported", "flow takes no input image yet: its image upload is not proven",
                          "images")
        if req.n not in (None, 1):
            raise _refuse("input.invalid", "flow makes one result per request", "n")
        ratio = C.DEFAULT_RATIO if req.ratio is None else req.ratio
        aspects = video[0] if video else C.ASPECTS
        if ratio not in aspects:
            raise _refuse("input.bad_ratio", "%s takes the ratios %s (or none)" % (req.model, ", ".join(aspects)),
                          "ratio")
        if C.is_blank_prompt(req.prompt):
            raise _refuse("input.empty_prompt", "the prompt shows no visible characters", "prompt")
        if video is None:
            if req.duration is not None:
                raise _refuse("input.bad_duration", "an image has no duration", "duration")
            return Plan(
                operation="text_to_image", media_type="image", model=req.model,
                params={"model": image[0], "aspect": C.ASPECTS[ratio], "ratio": ratio},
                completion=Completion.HOLD, after_sent=AfterSent.RECONCILE, price_key=C.price_key(req.model),
                pool="image", submit_timeout_s=SUBMIT_TIMEOUT_S)
        if req.duration not in (None, C.VIDEO_DURATION):
            raise _refuse("input.bad_duration", "%s makes %d s clips" % (req.model, C.VIDEO_DURATION), "duration")
        # the credits the submit checks the account's balance against: the option's cost (`flow.costs`); a missing
        # one is refused by the core's pricing, so this job never reaches a submit without it
        credits = settings.get("flow.costs").get(C.price_key(req.model))
        return Plan(
            operation="text_to_video", media_type="video", model=req.model,
            params={"model": video[0][ratio][0], "aspect": video[0][ratio][1], "ratio": ratio,
                    "duration": C.VIDEO_DURATION, "credits": credits},
            completion=Completion.DETACH, after_sent=AfterSent.RECONCILE, price_key=C.price_key(req.model),
            pool="video", submit_timeout_s=SUBMIT_TIMEOUT_S)


SPEC = FlowSpec()
