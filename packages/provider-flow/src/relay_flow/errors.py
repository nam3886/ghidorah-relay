"""flow's error codes and warning codes.

Registered with the platform registry when this module loads. The codes the platform already owns are not repeated:
a signed-out page before the send is `pool.account_logged_out`, a proxy that cannot reach flow `pool.proxy_failed`,
an order that never shows up `render.deadline`. A warning never ends a job.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "flow.http_error"              # before the send: flow answered a step with an error; tried again
CAPTCHA_FAILED = "flow.captcha_failed"      # before the send: the reCAPTCHA token source gave no token
CAPTCHA_KEY_INVALID = "flow.captcha_key_invalid"    # before the send: the solver refused its key (or none is set)
CAPTCHA_NO_BALANCE = "flow.captcha_no_balance"      # before the send: the solver account has no balance left
CAPTCHA_TIMEOUT = "flow.captcha_timeout"            # before the send: the solver gave no token in time
CAPTCHA_REFUSED = "flow.captcha_refused"    # flow refused every try at its reCAPTCHA check; nothing was made
TOO_MUCH_TRAFFIC = "flow.too_much_traffic"  # flow refused a try with code 8 (too much traffic); nothing was made
MODEL_ACCESS_DENIED = "flow.model_access_denied"    # flow refused every try: the account may not use the model
UPLOAD_FAILED = "flow.upload_failed"        # before the send: flow did not take an input image
TIER_REQUIRED = "flow.tier_required"        # before the send: the account's plan has no such option

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "flow answered with an error before the order was sent"),
    Rule(CAPTCHA_FAILED, 502, "api_error", True, False, True, True, True,
         "the security token for flow could not be obtained; the order was not sent"),
    Rule(UPLOAD_FAILED, 502, "api_error", True, False, True, True, True,
         "flow did not take an input image; the order was not sent"),
    # the plan read right before the send (a backstop: the option's pool already holds nothing on a plan without
    # it): not the order's fault, so it uses no attempt until the job has met it 5 times; another account may take it
    Rule(TIER_REQUIRED, 503, "api_error", False, False, False, True, True,
         "no flow account of a plan that offers this option took the order; it was not sent", free_retries=5),
    # The solver's own faults: ours to fix (a key, a top-up), never the flow account's; nothing was sent.
    Rule(CAPTCHA_KEY_INVALID, 502, "api_error", True, False, True, False, True,
         "the reCAPTCHA solver refused its key, or none is set (Settings, flow.<source>_key)",
         "the security token for flow could not be obtained; the order was not sent"),
    Rule(CAPTCHA_NO_BALANCE, 502, "api_error", True, False, True, False, True,
         "the reCAPTCHA solver account has no balance left",
         "the security token for flow could not be obtained; the order was not sent"),
    Rule(CAPTCHA_TIMEOUT, 504, "api_error", True, False, True, True, True,
         "the security token for flow did not come in time; the order was not sent"),
    # Sent, and refused before anything was made (an immediate 7 or 8 from flow, nothing listed, nothing charged): the
    # job goes back to the queue as a new attempt.
    Rule(CAPTCHA_REFUSED, 503, "api_error", True, False, True, True, True,
         "flow refused the request at its security check; nothing was made"),
    Rule(TOO_MUCH_TRAFFIC, 503, "api_error", True, False, True, True, True,
         "flow said it had too much traffic from the account; nothing was made"),
    Rule(MODEL_ACCESS_DENIED, 503, "api_error", True, False, True, True, True,
         "flow refused the model for the account; nothing was made"),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

