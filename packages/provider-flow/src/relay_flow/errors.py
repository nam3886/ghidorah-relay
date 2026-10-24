"""flow's error codes and warning codes.

Registered with the platform registry when this module loads. The codes the platform already owns are not repeated:
a signed-out page before the send is `pool.account_logged_out`, a proxy that cannot reach flow `pool.proxy_failed`,
an order that never shows up `render.deadline`. A warning never ends a job.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "flow.http_error"              # before the send: flow answered a step with an error; tried again
CAPTCHA_FAILED = "flow.captcha_failed"      # before the send: the reCAPTCHA token source gave no token
CAPTCHA_REFUSED = "flow.captcha_refused"    # flow refused every try at its reCAPTCHA check; nothing was made

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "flow answered with an error before the order was sent"),
    Rule(CAPTCHA_FAILED, 502, "api_error", True, False, True, True, True,
         "the security token for flow could not be obtained; the order was not sent"),
    # Sent, and refused before anything was made (an immediate 7 or 8 from flow, nothing listed, nothing charged): the
    # job goes back to the queue as a new attempt.
    Rule(CAPTCHA_REFUSED, 503, "api_error", True, False, True, True, True,
         "flow refused the request at its security check; nothing was made"),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

