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
PAGE_WAIT = "meta.page_wait"                   # a page was needed and the proxy IP's page gate is closed: nothing sent
HTTP_ERROR = "meta.http_error"                 # an HTTP step before the send failed (page, upload, EstabStream)
INPUT_UNUSABLE = "meta.input_unusable"         # an input image cannot be made small enough to upload: nothing sent
CONTENT_REFUSED = "meta.content_refused"       # meta's safety check refused the prompt (`policy_denied`)
NO_IMAGE = "meta.no_image"                     # meta finished its answer with no image in it
NO_VIDEO = "meta.no_video"                     # meta finished its answer with no video in it

# Warnings (not rules).

def _ui(code: str, message: str) -> Rule:
    """A step toward meta.ai failed before the send: one attempt is used, the client sees a generic error."""
    return Rule(code, 500, "api_error", True, False, True, True, message, public_code=core_errors.INTERNAL_ERROR)


CODES: dict[str, Rule] = {r.code: r for r in (
    Rule(CHECKPOINT, 503, "api_error", False, False, False, True,
         "the meta account met a browser checkpoint; the job is tried again",
         public_code=core_errors.TEMPORARILY_UNAVAILABLE),
    Rule(PAGE_WAIT, 503, "api_error", False, False, False, True,
         "the meta account needs a page read and its proxy IP's page gate is closed; the job is tried again",
         public_code=core_errors.TEMPORARILY_UNAVAILABLE),
    _ui(HTTP_ERROR, "an HTTP step to meta.ai failed before the send"),
    Rule(INPUT_UNUSABLE, 422, "invalid_request_error", True, True, False, True,
         "an input image could not be made small enough to upload; nothing was sent and the charge is returned",
         public_code=core_errors.INVALID_INPUT,
         public_text="An input image could not be read or made small enough. Sending the same image again will "
                     "not help; use another one."),
    Rule(CONTENT_REFUSED, 422, "invalid_request_error", True, True, False, True,
         "meta's safety check refused this prompt; the charge is returned",
         public_code=core_errors.CONTENT_REFUSED,
         public_text="meta refused to make this. Sending the same request again will not help; change the prompt "
                     "or the images."),
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
