"""gemini's error codes and warning codes.

Registered with the platform registry when this module loads. The codes the platform already owns are not repeated:
a signed-out page before the send is `pool.account_logged_out`, a proxy that cannot reach gemini `pool.proxy_failed`,
an order that never shows up `render.deadline`. A warning never ends a job.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "gemini.http_error"          # before the send: gemini answered a step with an error; tried again
UPLOAD_FAILED = "gemini.upload_failed"    # before the send: the input image was not taken; tried again
REFUSED = "gemini.refused"                # gemini finished its answer with text and no image
NOT_ENTITLED = "gemini.not_entitled"      # gemini answered that the account's plan does not include this
VIDEO_LIMIT = "gemini.video_limit"        # gemini answered that the account makes no more videos today
UNRESOLVED = "gemini.account_unresolved"  # before the send: the Google account of the session is not the one meant

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "gemini answered with an error before the order was sent"),
    Rule(UNRESOLVED, 502, "api_error", True, False, True, True, True,
         "the gemini account's Google account is not resolved yet; the order was not sent"),
    Rule(UPLOAD_FAILED, 502, "api_error", True, False, True, True, True,
         "the input image could not be handed to gemini; the order was not sent"),
    # gemini answered for good with no image: the job dies now and the charge is returned (never sent twice).
    Rule(REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "gemini answered without a result (a refused prompt or a reached limit); the charge is returned"),
    Rule(NOT_ENTITLED, 502, "api_error", True, True, False, True, True,
         "the gemini account cannot make this; the charge is returned"),
    Rule(VIDEO_LIMIT, 502, "api_error", True, True, False, True, True,
         "the gemini account reached its video limit for now; the charge is returned"),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

WARN_PRICE_FALLBACK = "gemini.price_fallback"
