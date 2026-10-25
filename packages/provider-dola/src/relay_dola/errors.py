"""dola's error codes: what dola said or what our browser did, as rules the core reads.

Registered with the platform registry when this module loads (`relay_core.errors.register`), so a bad rule fails at
import. The codes the platform already owns (`pool.*`, `account.*`, `render.*`, `input.*`) are not repeated here.

Warning codes are not rules: a warning never ends a job, it rides on a result as `Warning(code, text)`.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule
from relay_dola.constants import BROWSER_FAULT_FREE_RETRIES

CONTENT_REFUSED = "dola.content_refused"
# dola's reply stream ended with error code 710022002 (fixtures called it "high demand"): measured 23/10, 9 of 18 tries
# in 3 h, ALL on one prompt and on 7 different accounts, so it answers the prompt, not the account's load. Owner's
# ruling 25/10 (it replaces the 23/10 one, which ended the job at the first): like every refusal it moves the job to
# another account until `dola.refusal_max_accounts` accounts refused it, and the last one ends the job with this code,
# refunded. A requeue in between carries it too (it uses no attempt).
REFUSED_710022002 = "dola.refused_710022002"
# The same cap reached by dola's throttle (710022004): dola would not take the order on that many accounts. 22-23/10
# single jobs went round all 9 accounts in minutes and 6 were logged out.
REFUSED_ON_ACCOUNTS = "dola.refused_on_accounts"
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
# The plain-HTTP send (`dola.send_path` = http). Neither is dola's verdict on the order: both are ours to fix.
HTTP_SETUP_FAILED = "dola.http_setup_failed"     # the login read, the session bootstrap or the signer failed: not sent
SIGNER_SUSPECT = "dola.signer_suspect"           # dola's refusals look like OUR signature is rejected (see send_http)

# Warnings (not rules).
WARN_DURATION_DRIFT = "dola.duration_drift"
WARN_MODEL_SUBSTITUTED = "dola.model_substituted"
WARN_PARTLY_WATERMARKED = "dola.partly_watermarked"
WARN_DURATION_CAPPED = "dola.duration_capped"   # 2.0 / 1.0 at their longest length

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
    _refusal(REFUSED_710022002, "dola refused to take this order (its code 710022002, which it gives for prompts it "
                                "will not make); nothing was made and the charge is returned"),
    _refusal(REFUSED_ON_ACCOUNTS, "dola would not take this order on the most accounts a job may try (it was "
                                  "throttled); nothing was made and the charge is returned"),
    _refusal(LIKENESS_REFUSED,"dola refused: with a photo of a person it only makes videos of the account owner"),
    _refusal(NEEDS_CONFIRM, "dola asked a question instead of taking the order, and never delivered"),
    _refusal(NO_VIDEO, "dola made no video for this order"),
    _refusal(NO_IMAGE, "dola made no image for this order"),
    # The render-deadline cause of a job whose last word from dola is "something went wrong" (spec.deadline_cause):
    # the reply keeps the job waiting for the poll (Codex 25/10), and an empty conversation ends it with this.
    # A deadline cause ends the job with its refund and uses no attempt (as every deadline cause).
    Rule(TEMPORARY_ERROR, 502, "api_error", True, True, False, True, True,
         "dola reported a temporary error when it took the order, and never delivered; the charge is returned"),
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
    _ui(HTTP_SETUP_FAILED, "the plain-HTTP send could not be prepared (login read, session or signature); nothing "
                           "was sent"),
    _ui(SIGNER_SUSPECT, "dola refused the plain-HTTP send in a way that points at relay's request signature; nothing "
                        "was taken"),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
