"""dola's error codes (contracts 11): what dola said or what our browser did, as rules the core reads.

Registered with the platform registry when this module loads (`relay_core.errors.register`), so a bad rule fails at
import. The codes the platform already owns (`pool.*`, `account.*`, `render.*`, `input.*`) are not repeated here.

Warning codes are not rules: a warning never ends a job, it rides on a result as `Warning(code, text)`.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule
from relay_dola.constants import BROWSER_FAULT_FREE_RETRIES

CONTENT_REFUSED = "dola.content_refused"
LIKENESS_REFUSED = "dola.likeness_refused"
RENDER_WATERMARKED_ONLY = "render.watermarked_only"      # a core code: the poll saw only watermarked links
NEEDS_CONFIRM = "dola.needs_confirm"
NO_VIDEO = "dola.no_video"
NO_IMAGE = "dola.no_image"
TEMPORARY_ERROR = "dola.temporary_error"
# The next three are read-time diagnostics: a conversation read that failed and is read again (`ChainError.transient`).
# They never end a job; they name the failure in the poll note and in a Recover failure text.
UNKNOWN_STATUS = "dola.unknown_status"
HTTP_ERROR = "dola.http_error"
BAD_RESPONSE = "dola.bad_response"

UI_MODE_NOT_READY = "dola.ui_mode_not_ready"
UI_MODEL_SELECT_FAILED = "dola.ui_model_select_failed"
UI_RATIO_SELECT_FAILED = "dola.ui_ratio_select_failed"
UI_DURATION_PATCH_FAILED = "dola.ui_duration_patch_failed"
UI_ATTACH_FAILED = "dola.ui_attach_failed"
UI_STYLE_SELECT_FAILED = "dola.ui_style_select_failed"
UI_SEND_FAILED = "dola.ui_send_failed"
PAGE_RELOADED = "dola.page_reloaded"   # the page navigated while the order was being composed: nothing was sent
BROWSER_COOKIES_LOST = "dola.browser_cookies_lost"   # the browser lost the account's identity cookies: a browser fault
UI_NO_CONVERSATION = "dola.ui_no_conversation"   # names the cause in the detail of an Ambiguous send; ends no job alone

# Warnings (not rules).
WARN_DURATION_DRIFT = "dola.duration_drift"
WARN_MODEL_SUBSTITUTED = "dola.model_substituted"
WARN_PARTLY_WATERMARKED = "dola.partly_watermarked"
WARN_PRICE_FALLBACK = "dola.price_fallback"
WARN_DURATION_CAPPED = "dola.duration_capped"   # 2.0 / 1.0 at their longest length (v4 `VideoRequest.warnings`)

_HIDDEN = "the request could not be submitted to dola"


def _refusal(code: str, message: str) -> Rule:
    """dola said no for good: the job dies now and the charge is returned (sending again only burns credit)."""
    return Rule(code, 422, "invalid_request_error", True, True, False, True, True, message)


def _transient(code: str, message: str) -> Rule:
    """A failure that may pass: one attempt is used, and the job fails (refunded) when they run out."""
    return Rule(code, 502, "api_error", True, False, True, True, True, message)


def _ui_retry(code: str, message: str) -> Rule:
    """The page changed under the browser before anything was sent: the run goes back to the queue and no attempt is
    used (the page, not the order, was at fault), for the job's first BROWSER_FAULT_FREE_RETRIES of them; after that
    each uses one, so `max_attempts` ends a job that keeps meeting the fault."""
    return Rule(code, 500, "api_error", False, False, False, False, True, message, _HIDDEN,
                BROWSER_FAULT_FREE_RETRIES)


def _ui(code: str, message: str) -> Rule:
    """Our browser could not drive dola's page: ours to fix, so the client sees a generic error."""
    return Rule(code, 500, "api_error", True, False, True, False, True, message, _HIDDEN)


CODES: dict[str, Rule] = {r.code: r for r in (
    _refusal(CONTENT_REFUSED, "dola refused to make this (copyright, safety or content rules); the charge is returned"),
    _refusal(LIKENESS_REFUSED, "dola refused: with a photo of a person it only makes videos of the account owner"),
    _refusal(NEEDS_CONFIRM, "dola asked a question instead of taking the order, and never delivered"),
    _refusal(NO_VIDEO, "dola made no video for this order"),
    _refusal(NO_IMAGE, "dola made no image for this order"),
    _transient(TEMPORARY_ERROR, "dola reported a temporary error when it took the order"),
    _transient(UNKNOWN_STATUS, "dola answered with a status that is not understood"),
    _transient(HTTP_ERROR, "dola answered with an HTTP error"),
    _transient(BAD_RESPONSE, "dola answered with something that is not readable"),
    _ui(UI_MODE_NOT_READY, "dola's creation mode did not become ready"),
    _ui_retry(PAGE_RELOADED, "dola's page reloaded while the order was being composed; the run is tried again"),
    _ui_retry(BROWSER_COOKIES_LOST, "the browser lost the account's cookies; the run is tried again"),
    _ui(UI_MODEL_SELECT_FAILED, "the model could not be selected on dola's page"),
    _ui(UI_RATIO_SELECT_FAILED, "the aspect ratio could not be selected on dola's page"),
    _ui(UI_DURATION_PATCH_FAILED, "the duration could not be set on dola's page"),
    _ui(UI_ATTACH_FAILED, "the reference images could not be attached on dola's page"),
    _ui(UI_STYLE_SELECT_FAILED, "the style could not be selected on dola's page"),
    _ui(UI_SEND_FAILED, "the order could not be sent on dola's page"),
    _ui(UI_NO_CONVERSATION, "dola's page showed no conversation after sending"),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
