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
LIMIT_REACHED = "gemini.limit_reached"    # such a refusal that is the account's limit (it empties the pool)
NOT_ENTITLED = "gemini.not_entitled"      # gemini answered that the account's plan does not include this
VIDEO_LIMIT = "gemini.video_limit"        # gemini answered that the account makes no more videos today
UNRESOLVED = "gemini.account_unresolved"  # before the send: the Google account of the session is not the one meant
UPSTREAM_FAILED = "gemini.upstream_failed"  # gemini said it failed on its side, "try again": re-queued

_BUSY = core_errors.TEMPORARILY_UNAVAILABLE

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True,
         "gemini answered with an error before the order was sent", public_code=_BUSY,
         public_text="gemini kept answering with errors and nothing was made. Sending the request again later may "
                     "work."),
    Rule(UNRESOLVED, 502, "api_error", True, False, True, True,
         "the gemini account's Google account is not resolved yet; the order was not sent", public_code=_BUSY,
         public_text="gemini could not be reached for this order and nothing was made. Sending the request again "
                     "later may work."),
    Rule(UPLOAD_FAILED, 502, "api_error", True, False, True, True,
         "the input image could not be handed to gemini; the order was not sent",
         public_code=core_errors.GENERATION_FAILED,
         public_text="The input image could not be handed to gemini and nothing was made. Sending the request "
                     "again may work; if it keeps failing, use another image."),
    # gemini's final answer said it failed on its side and asked to try again ("something went wrong on our end",
    # 25/10): nothing was made, the job goes back to the queue (this account or another) and uses one attempt; at the
    # last attempt it fails with its refund. Only from a send's own answer: a found chat's read cannot re-queue.
    Rule(UPSTREAM_FAILED, 503, "api_error", True, False, True, True,
         "gemini said something went wrong on its side; nothing was made, it is sent again",
         public_code=core_errors.GENERATION_FAILED,
         public_text="gemini kept failing on its side and nothing was made. Sending the request again later may "
                     "work."),
    # gemini answered for good with no image: the job dies now and the charge is returned (never sent twice).
    Rule(REFUSED, 422, "invalid_request_error", True, True, False, True,
         "gemini answered without a result (a refused prompt); the charge is returned",
         public_code=core_errors.CONTENT_REFUSED,
         public_text="gemini refused to make this. Sending the same request again will not help; change the prompt "
                     "or the images."),
    # The same answer when it is the account's limit (`submit.refusal_code`: a quota-like video refusal, or an image
    # refusal while the usage read shows a used-up window): the same refund and no retry, but the customer is told it
    # is capacity, not their prompt.
    Rule(LIMIT_REACHED, 502, "api_error", True, True, False, True,
         "gemini answered without a result and the account's limit is reached; the charge is returned",
         public_code=core_errors.TEMPORARILY_UNAVAILABLE,
         public_text="gemini's capacity for this was used up for now. Sending the request again later may work."),
    Rule(NOT_ENTITLED, 502, "api_error", True, True, False, True,
         "the gemini account cannot make this; the charge is returned", public_code=core_errors.INVALID_INPUT,
         public_text="gemini could not make this option. Sending the same request again is unlikely to help; try "
                     "another option or model."),
    Rule(VIDEO_LIMIT, 502, "api_error", True, True, False, True,
         "the gemini account reached its video limit for now; the charge is returned", public_code=_BUSY,
         public_text="gemini's video capacity was used up for now. Sending the request again later may work."),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
