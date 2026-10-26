"""qwen's error codes and warning codes.

Registered with the platform registry when this module loads. The codes the platform already owns are not repeated:
a signed-out refresh before the send is `pool.account_logged_out`, too few qwen credits before the send
`pool.account_out_of_points`, a proxy that cannot reach qwen `pool.proxy_failed`, a render that never ends
`render.deadline`. A warning never ends a job.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "qwen.http_error"            # before the send: qwen answered a step with an error; tried again
MINT_FAILED = "qwen.mint_failed"          # before the send: the browser could not mint the anti-bot pair
WAF_REFUSED = "qwen.waf_refused"          # qwen's firewall refused the pair; the chat stayed empty: re-mint, re-queue
REFUSED = "qwen.refused"                  # qwen answered the order with words and no image or task
OUT_OF_CREDITS = "qwen.out_of_credits"    # qwen answered "Insufficient Credits" to the order
RENDER_FAILED = "qwen.render_failed"      # the video task ended without a video
IMAGE_LOST = "qwen.image_lost"            # the image stream died and the chat shows no image long after
IMAGE_LIMIT = "qwen.image_limit"          # qwen answered the account's daily image quota is used up: nothing made

_CHECK_TEXT = "qwen's security check could not be passed and nothing was made. Sending the request again later " \
              "may work."

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "qwen answered with an error before the order was sent",
         public_text="qwen kept answering with errors and nothing was made. Sending the request again later may "
                     "work."),
    Rule(MINT_FAILED, 502, "api_error", True, False, True, True, True,
         "the security headers for qwen could not be made; the order was not sent",
         public_text=_CHECK_TEXT),
    # Sent, refused at the firewall before qwen saw it (and the chat proven empty): the stale pair is dropped and the
    # job goes back to the queue. Not the order's fault: the first two such requeues use no attempt.
    Rule(WAF_REFUSED, 503, "api_error", False, False, False, True, True,
         "qwen's firewall refused the request; nothing was made, it is sent again", free_retries=2,
         public_text=_CHECK_TEXT),
    # The account's daily image quota is used up (its image pool is set to 0 until the day turns): nothing was made,
    # the job goes back to the queue for another account and uses no attempt.
    Rule(IMAGE_LIMIT, 503, "api_error", False, False, False, True, True,
         "the qwen account used up its daily images; nothing was made, it is sent to another account"),
    # qwen said no for good: the job dies now and the charge is returned (the order is never sent twice).
    Rule(REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "qwen refused to make this; try a different prompt; the charge is returned",
         public_text="qwen refused to make this. Sending the same prompt again will not help; change it."),
    Rule(OUT_OF_CREDITS, 503, "api_error", True, True, False, True, True,
         "the qwen account had too few credits for this; the charge is returned",
         public_text="qwen's capacity for this was used up for now. Sending the request again later may work."),
    Rule(RENDER_FAILED, 502, "api_error", True, True, False, True, True,
         "qwen ended the render with no result; the charge is returned",
         public_text="qwen ended the render with no result. Sending the request again may work."),
    Rule(IMAGE_LOST, 502, "api_error", True, True, False, True, True,
         "qwen lost the image while it was drawn; the charge is returned",
         public_text="qwen lost the image while it was drawn. Sending the request again may work."),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

