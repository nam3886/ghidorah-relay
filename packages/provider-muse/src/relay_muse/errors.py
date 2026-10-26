"""muse's error codes, registered with the platform registry when this module loads.

The codes the platform owns are not repeated: a signed-out session before the send is `pool.account_logged_out`, a
proxy that cannot reach muse `pool.proxy_failed`, an order that never shows up `render.deadline`. Each rule carries the
sentence the public API reference shows a customer (`public_text`).
"""

from relay_core import errors as core_errors
from relay_core.errors import Rule

HTTP_ERROR = "muse.http_error"          # muse answered a step before (or the send itself) with an error: not taken
VM_NOT_READY = "muse.vm_not_ready"      # the account's cloud VM did not wake (or has none): nothing was sent
REFUSED = "muse.refused"                # muse answered the order in words with no image or video
OUTPUT_MISSING = "muse.output_missing"  # muse has no file for a result it showed

def _rule(*args, text: str, **kw) -> Rule:
    return Rule(*args, **kw, public_text=text)


CODES: dict[str, Rule] = {r.code: r for r in (
    # A failure that may pass: one attempt is used, and the job fails (refunded) when they run out.
    _rule(HTTP_ERROR, 502, "api_error", True, False, True, True, True,
          "muse answered with an error before the order was taken",
          text="muse kept answering with errors and nothing was made. Sending the request again later may work."),
    # The account's state, not the order's: back to the queue with no attempt used for the first three.
    _rule(VM_NOT_READY, 503, "api_error", False, False, False, True, True,
          "the muse workspace did not wake; the order was not sent", free_retries=3,
          text="muse could not start the work in time and nothing was made. Sending the request again later may "
               "work."),
    # muse answered for good with no media (a refused prompt, a question): the job dies now, the charge is returned.
    _rule(REFUSED, 422, "invalid_request_error", True, True, False, True, True,
          "muse answered in words without making the image or video; the charge is returned",
          text="muse refused to make this. Sending the same request again will not help; change the prompt or the "
               "image."),
    _rule(OUTPUT_MISSING, 502, "api_error", True, True, False, True, True,
          "muse has no file for this result; the charge is returned",
          text="muse had no file for the result. Sending the request again may work."),
)}

# A module loaded twice (a test reloading it) keeps its first registration.
core_errors.register(*(r for code, r in CODES.items() if code not in core_errors.RULES))
