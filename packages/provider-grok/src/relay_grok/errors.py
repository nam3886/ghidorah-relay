"""grok's error codes, as rules the core reads.

Registered with the platform registry when this module loads (`relay_core.errors.register`), so a bad rule fails at
import. The codes the platform already owns are not repeated: a signed-out account before the send is
`pool.account_logged_out`, too few uses left `pool.account_out_of_points`, a failed proxy `pool.proxy_failed`, a
text-to-image send that broke after it was made `render.unclear_send`.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "grok.http_error"             # a step before the send failed, or 400 code 3 (our body)
CLOUDFLARE = "grok.cloudflare"             # a Cloudflare challenge page (exact match): nothing sent, the account rests
ANTIBOT = "grok.antibot"                   # 403 {"error": {"code": 7}} (exact match): nothing sent, the account rests
INPUT_UNUSABLE = "grok.input_unusable"     # the upload answered a terminal error for an input image: nothing sent
CONTENT_REFUSED = "grok.content_refused"   # every image or the video came back moderated, or grok's safety words
NO_IMAGE = "grok.no_image"                 # the answer ended with no image and no reason
NO_VIDEO = "grok.no_video"                 # the answer ended with no video and no reason
DECOY_VIDEO = "grok.decoy_video"           # a stock clip instead of the video asked for (no rRated, or too fast)
LIMIT = "grok.limit"                       # `account_refused`'s final: every account tried answered its limit

_BUSY = core_errors.TEMPORARILY_UNAVAILABLE
_BUSY_TEXT = ("Grok Imagine could not take the order for now and nothing was made. Sending the request again later "
              "may work.")

CODES: dict[str, Rule] = {r.code: r for r in (
    # A step before the send failed: one attempt is used, the client sees a generic error.
    Rule(HTTP_ERROR, 500, "api_error", True, False, True, True,
         "an HTTP step to grok.com failed before the send", public_code=core_errors.INTERNAL_ERROR),
    # grok's anti-bot layer stood in front of the account: nothing was sent, the job goes back to the queue with no
    # attempt used (twice; after that a requeue uses one) and the account rests.
    Rule(CLOUDFLARE, 503, "api_error", False, False, False, True,
         "grok.com answered a Cloudflare challenge page; nothing was sent, the job is tried again",
         public_code=_BUSY, public_text=_BUSY_TEXT, free_retries=2),
    Rule(ANTIBOT, 503, "api_error", False, False, False, True,
         "grok.com's anti-bot rules refused the request (code 7); nothing was sent, the job is tried again",
         public_code=_BUSY, public_text=_BUSY_TEXT, free_retries=2),
    Rule(INPUT_UNUSABLE, 422, "invalid_request_error", True, True, False, True,
         "grok.com refused an input image at upload; nothing was sent and the charge is returned",
         public_code=core_errors.INVALID_INPUT,
         public_text="An input image could not be used. Sending the same image again will not help; use another "
                     "one."),
    Rule(CONTENT_REFUSED, 422, "invalid_request_error", True, True, False, True,
         "grok's moderation refused this order; the charge is returned",
         public_code=core_errors.CONTENT_REFUSED,
         public_text="Grok Imagine refused to make this. Sending the same request again will not help; change the "
                     "prompt or the images."),
    Rule(NO_IMAGE, 422, "invalid_request_error", True, True, False, True,
         "grok made no image for this order; the charge is returned", public_code=core_errors.GENERATION_FAILED,
         public_text="Grok Imagine made no image for this request. Sending the request again may work; if it keeps "
                     "failing, change the prompt."),
    Rule(NO_VIDEO, 422, "invalid_request_error", True, True, False, True,
         "grok made no video for this order; the charge is returned", public_code=core_errors.GENERATION_FAILED,
         public_text="Grok Imagine made no video for this request. Sending the request again may work; if it keeps "
                     "failing, change the prompt."),
    Rule(DECOY_VIDEO, 502, "api_error", True, True, False, True,
         "grok answered a stock clip instead of the video (no rRated, or done too fast); the charge is returned",
         public_code=core_errors.GENERATION_FAILED,
         public_text="Grok Imagine returned a video that does not match the request. Sending the request again "
                     "later may work."),
    # Accounts kept answering their limit to the send before anything was made: the job ends with its charge returned.
    Rule(LIMIT, 503, "api_error", True, True, False, True,
         "grok answered its limit on every account tried; nothing was made, the charge is returned",
         public_code=_BUSY, public_text=_BUSY_TEXT),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
