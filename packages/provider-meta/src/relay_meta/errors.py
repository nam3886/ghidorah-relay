"""meta's error codes and warning codes, as rules the core reads.

Registered with the platform registry when this module loads (`relay_core.errors.register`), so a bad rule fails at
import; the registry refuses a code nobody assigns. The codes the platform already owns (`pool.*`, `render.*`,
`input.*`) are not repeated here. Warning codes are not rules: a warning never ends a job, it rides on a result as
`Warning(code, text)`.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

# Vercel's browser checkpoint stood in front of meta.ai: nothing was sent. The account's state, not the order's: the
# job goes back to the queue with no attempt used and no charge, like a `pool.*` code; it never fails a job.
CHECKPOINT = "meta.checkpoint"
UI_PAGE_NOT_READY = "meta.ui_page_not_ready"   # no sign-in sign, or the composer never hydrated: nothing was sent
UI_SEND_FAILED = "meta.ui_send_failed"         # the prompt did not go in, or no conversation id came: nothing was sent
NO_IMAGE = "meta.no_image"                     # meta finished its answer with no image in it
NO_VIDEO = "meta.no_video"                     # meta finished its answer with no video in it

# Warnings (not rules).

def _ui(code: str, message: str) -> Rule:
    """Our browser could not drive meta's page before the send: one attempt is used, the client sees a generic error."""
    return Rule(code, 500, "api_error", True, False, True, True, message, public_code=core_errors.INTERNAL_ERROR)


CODES: dict[str, Rule] = {r.code: r for r in (
    Rule(CHECKPOINT, 503, "api_error", False, False, False, True,
         "the meta account met a browser checkpoint; the job is tried again",
         public_code=core_errors.TEMPORARILY_UNAVAILABLE),
    _ui(UI_PAGE_NOT_READY, "meta's page showed no sign-in sign or no ready composer"),
    _ui(UI_SEND_FAILED, "the prompt could not be sent on meta's page"),
    Rule(NO_IMAGE, 422, "invalid_request_error", True, True, False, True,
         "meta made no image for this order; the charge is returned", public_code=core_errors.GENERATION_FAILED,
         public_text="meta made no image for this request. Sending the request again may work; if it keeps "
                     "failing, change the prompt."),
    Rule(NO_VIDEO, 422, "invalid_request_error", True, True, False, True,
         "meta made no video for this order; the charge is returned", public_code=core_errors.GENERATION_FAILED,
         public_text="meta made no video for this request. Sending the request again may work; if it keeps "
                     "failing, change the prompt."),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
