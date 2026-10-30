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
# Account refusals (`relay_core.refusals`): the job goes to another account with the code (no attempt used) until
# `flow.refusal_max_accounts` accounts refused it; the last refusal fails it with the same code, refunded.
TOO_MUCH_TRAFFIC = "flow.too_much_traffic"  # flow refused the send with code 8 (too much traffic); nothing was made
MODEL_ACCESS_DENIED = "flow.model_access_denied"    # flow refused the model for the account; nothing was made
UPLOAD_FAILED = "flow.upload_failed"        # before the send: flow did not take an input image
TIER_REQUIRED = "flow.tier_required"        # before the send: the account's plan has no such option

_CHECK_TEXT = "flow could not take the order for now and nothing was made. Sending the request again later may work."
_BUSY = core_errors.TEMPORARILY_UNAVAILABLE

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True,
         "flow answered with an error before the order was sent", public_code=_BUSY,
         public_text="flow kept answering with errors and nothing was made. Sending the request again later may "
                     "work."),
    Rule(CAPTCHA_FAILED, 502, "api_error", True, False, True, True,
         "the security token for flow could not be obtained; the order was not sent", public_code=_BUSY,
         public_text=_CHECK_TEXT),
    Rule(UPLOAD_FAILED, 502, "api_error", True, False, True, True,
         "flow did not take an input image; the order was not sent", public_code=core_errors.GENERATION_FAILED,
         public_text="flow did not take an input image and nothing was made. Sending the request again may work; "
                     "if it keeps failing, use another image."),
    # the plan read right before the send (a backstop: the option's pool already holds nothing on a plan without
    # it): not the order's fault, so it uses no attempt until the job has met it 5 times; another account may take it
    Rule(TIER_REQUIRED, 503, "api_error", False, False, False, True,
         "no flow account of a plan that offers this option took the order; it was not sent", free_retries=5,
         public_code=_BUSY,
         public_text="This option of the model could not be ordered from flow when the job ran and nothing was "
                     "made. Sending the request again later, or with another option, may work."),
    # The solver's own faults: ours to fix (a key, a top-up), never the flow account's; nothing was sent.
    Rule(CAPTCHA_KEY_INVALID, 502, "api_error", True, False, True, True,
         "the reCAPTCHA solver refused its key, or none is set (Settings, flow.<source>_key)",
         public_code=core_errors.INTERNAL_ERROR),
    Rule(CAPTCHA_NO_BALANCE, 502, "api_error", True, False, True, True,
         "the reCAPTCHA solver account has no balance left", public_code=core_errors.INTERNAL_ERROR),
    Rule(CAPTCHA_TIMEOUT, 504, "api_error", True, False, True, True,
         "the security token for flow did not come in time; the order was not sent", public_code=_BUSY,
         public_text=_CHECK_TEXT),
    # Sent, and refused before anything was made (an immediate 7 from flow, nothing listed, nothing charged): the
    # job goes back to the queue as a new attempt.
    Rule(CAPTCHA_REFUSED, 503, "api_error", True, False, True, True,
         "flow refused the request at its security check; nothing was made", public_code=_BUSY,
         public_text=_CHECK_TEXT),
    # Account refusals: requeued with no attempt used; the refusal on the last account flow.refusal_max_accounts
    # allows (or of a pinned job) ends the job with its full refund.
    Rule(TOO_MUCH_TRAFFIC, 503, "api_error", True, True, False, True,
         "flow said it had too much traffic from the accounts the job tried; nothing was made", public_code=_BUSY,
         public_text="flow was too busy to take the order and nothing was made. Sending the request again later "
                     "may work."),
    Rule(MODEL_ACCESS_DENIED, 503, "api_error", True, True, False, True,
         "flow refused the model for the accounts the job tried; nothing was made", public_code=_BUSY,
         public_text="flow refused this model for now and nothing was made. Sending the request again later may "
                     "work."),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

