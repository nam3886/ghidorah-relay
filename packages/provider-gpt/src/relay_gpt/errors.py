"""gpt's error codes and warning codes.

Registered with the platform registry when this module loads. The codes the platform already owns are not repeated:
a signed-out session read before the send is `pool.account_logged_out`, a proxy that cannot reach chatgpt.com
`pool.proxy_failed`, an image that never arrives `render.deadline`. A warning never ends a job.
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "gpt.http_error"                # before the send: chatgpt.com answered a step with an error; tried again
IMAGE_LIMIT = "gpt.image_limit"              # before the send: the account's image quota read 0; another account
CHALLENGE = "gpt.challenge_required"         # before the send: the anti-bot step asked for a challenge or a login
UNAVAILABLE = "gpt.unavailable"              # the account refusals ran out (`account_refused`'s final): nothing made
REFUSED = "gpt.refused"                      # the answer ended in words with no image
IMAGE_LOST = "gpt.image_lost"                # the stream broke and the conversation shows no image long after
OUTPUT_MISSING = "gpt.output_missing"        # the image a result names could not be downloaded

WARN_TURNSTILE = "gpt.turnstile_unsolved"    # the Turnstile answer could not be made: sent with an empty one

_BUSY = core_errors.TEMPORARILY_UNAVAILABLE

CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    Rule(HTTP_ERROR, 502, "api_error", True, False, True, True,
         "chatgpt.com answered with an error before the order was sent", public_code=_BUSY,
         public_text="GPT Image kept answering with errors and nothing was made. Sending the request again later "
                     "may work."),
    # The account's state, not the order's: nothing was sent, the job goes back to the queue for another account and
    # uses no attempt.
    Rule(IMAGE_LIMIT, 503, "api_error", False, False, False, True,
         "the chatgpt.com account used up its images for now; nothing was sent, it goes to another account",
         public_code=_BUSY),
    Rule(CHALLENGE, 503, "api_error", False, False, False, True,
         "chatgpt.com asked the account for a challenge or a new login before the send; nothing was sent, it goes to "
         "another account", public_code=_BUSY),
    # Accounts kept refusing the order before anything was made: the job ends with its charge returned.
    Rule(UNAVAILABLE, 503, "api_error", True, True, False, True,
         "chatgpt.com refused the order on every account tried; nothing was made, the charge is returned",
         public_code=_BUSY,
         public_text="GPT Image could not take the order for now and nothing was made. Sending the request again "
                     "later may work."),
    # chatgpt.com said no for good: the job dies now and the charge is returned (the order is never sent twice).
    Rule(REFUSED, 422, "invalid_request_error", True, True, False, True,
         "chatgpt.com answered in words without an image; try a different prompt; the charge is returned",
         public_code=core_errors.CONTENT_REFUSED,
         public_text="GPT Image refused to make this. Sending the same prompt again will not help; change it."),
    Rule(IMAGE_LOST, 502, "api_error", True, True, False, True,
         "chatgpt.com lost the image while it was drawn; the charge is returned",
         public_code=core_errors.GENERATION_FAILED,
         public_text="GPT Image lost the image while it was drawn. Sending the request again may work."),
    Rule(OUTPUT_MISSING, 502, "api_error", True, True, False, True,
         "the image chatgpt.com made could not be downloaded; the charge is returned",
         public_code=core_errors.GENERATION_FAILED,
         public_text="The image was made but could not be delivered. Sending the request again may work."),
)}

core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
