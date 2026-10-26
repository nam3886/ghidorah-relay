"""vibes' error codes and warning codes.

Registered with the platform registry when this module loads (`relay_core.errors.register`), so a bad rule fails at
import; the registry refuses a code nobody assigns. The codes the platform already owns (`pool.*`, `render.*`) are not
repeated here: a checkpoint before the send is `pool.account_throttled`, a signed-out answer `pool.account_logged_out`,
a proxy that cannot reach vibes `pool.proxy_failed`, and a render that never finishes `render.deadline`.
Warning codes are not rules: a warning never ends a job, it rides on a result as `Warning(code, text)`.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "vibes.http_error"                # before the send: vibes answered a step with an error; tried again
GENERATE_REFUSED = "vibes.generate_refused"    # the generate call itself answered no: the job ends, refunded
RENDER_FAILED = "vibes.render_failed"          # vibes finished the batch with a batch-level error
CONTENT_REFUSED = "vibes.content_refused"      # vibes finished the batch but its item has an error or no media
OUTPUT_MISSING = "vibes.output_missing"        # the download answered that the finished item's file does not exist
SOURCE_MISSING = "vibes.source_missing"        # a pinned job's source output is no longer one vibes can work on

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "vibes answered with an error before the order was sent",
         public_text="vibes kept answering with errors and nothing was made. Sending the request again later may "
                     "work."),
    # vibes said no for good: the job dies now and the charge is returned (the order is never sent twice).
    Rule(GENERATE_REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "vibes refused to make this; the charge is returned",
         public_text="vibes refused to make this. Sending the same request again will not help; change the prompt "
                     "or the images."),
    Rule(RENDER_FAILED, 502, "api_error", True, True, False, True, True,
         "vibes ended the render with an error and no result; the charge is returned",
         public_text="vibes ended the render with an error and no result. Sending the request again may work."),
    Rule(CONTENT_REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "vibes could not make this content; try a different prompt; the charge is returned",
         public_text="vibes could not make this content. Sending the same prompt again will not help; change it."),
    Rule(OUTPUT_MISSING, 502, "api_error", True, True, False, True, True,
         "vibes has no file for this result; the charge is returned",
         public_text="vibes had no file for the result. Sending the request again may work."),
    # Found before the send: nothing is sent, the job ends and the charge is returned.
    Rule(SOURCE_MISSING, 422, "invalid_request_error", True, True, False, True, True,
         "vibes no longer has the source job's result to work on; the charge is returned",
         public_text="vibes no longer has the source job's result to work on. Sending the request again with this "
                     "source job will not help."),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

# Warnings (not rules).
