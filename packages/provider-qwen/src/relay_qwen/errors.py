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

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
         "qwen answered with an error before the order was sent"),
    Rule(MINT_FAILED, 502, "api_error", True, False, True, True, True,
         "the security headers for qwen could not be made; the order was not sent"),
    # Sent, refused at the firewall before qwen saw it (and the chat proven empty): the stale pair is dropped and the
    # job goes back to the queue. Not the order's fault: the first two such requeues use no attempt.
    Rule(WAF_REFUSED, 503, "api_error", False, False, False, True, True,
         "qwen's firewall refused the request; nothing was made, it is sent again", free_retries=2),
    # qwen said no for good: the job dies now and the charge is returned (the order is never sent twice).
    Rule(REFUSED, 422, "invalid_request_error", True, True, False, True, True,
         "qwen refused to make this; try a different prompt; the charge is returned"),
    Rule(OUT_OF_CREDITS, 503, "api_error", True, True, False, True, True,
         "the qwen account had too few credits for this; the charge is returned"),
    Rule(RENDER_FAILED, 502, "api_error", True, True, False, True, True,
         "qwen ended the render with no result; the charge is returned"),
    Rule(IMAGE_LOST, 502, "api_error", True, True, False, True, True,
         "qwen lost the image while it was drawn; the charge is returned"),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))

WARN_PRICE_FALLBACK = "qwen.price_fallback"
