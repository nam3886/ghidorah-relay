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

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "vibes answered with an error before the order was sent"),
    # vibes said no for good: the job dies now and the charge is returned (the order is never sent twice).
    Rule(GENERATE_REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "vibes refused to make this; the charge is returned"),
    Rule(RENDER_FAILED, 502, "api_error", True, True, False, True, True,
         "vibes ended the render with an error and no result; the charge is returned"),
    Rule(CONTENT_REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "vibes could not make this content; try a different prompt; the charge is returned"),
    Rule(OUTPUT_MISSING, 502, "api_error", True, True, False, True, True,
         "vibes has no file for this result; the charge is returned"),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

# Warnings (not rules).
