"""gpt spec constants: the site facts and the numbers that are not live settings.

ChatGPT (chatgpt.com) is reached over plain HTTP with Chrome's TLS fingerprint (curl_cffi). The account is a cookie jar
whose session cookie (`__Secure-next-auth.session-token`, often split `.0`/`.1`, 90 days, re-set on use) buys a 10-day
access token from `GET /api/auth/session`, the call the page itself makes; nothing else mints or refreshes a token
(plans/gpt-spike-261104.md). Pure: imported by the spec, the credential reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "gpt"

# ---- the site ---------------------------------------------------------------------------------------------------
BASE_URL = "https://chatgpt.com"
SITE_HOST = "chatgpt.com"                         # every cookie the account needs is for it or a subdomain
SESSION_COOKIE = "__Secure-next-auth.session-token"   # the bare name, or its parts `.0`, `.1`, ...
UPLOAD_HOST_SUFFIX = ".oaiusercontent.com"        # where an input image is PUT (the host comes from files create)
ESTUARY_PATH = "/backend-api/estuary/content"     # a result's bytes: fetched with the bearer, chatgpt.com only
SENTINEL_LOADER_PATH = "/backend-api/sentinel/sdk.js"   # names /sentinel/<id>/sdk.js when the home page does not
                                                        # (live 05/11/2026)
# The fingerprint and the headers of macOS Chrome 150: the newest Chrome profile of the locked curl_cffi 0.16.3 (its
# `chrome150` is macOS Tahoe; the spike's `chrome152_macos` is 0.17-only and curl_cffi is not bumped). The UA and the
# client hints are the ones that profile sends itself, so the headers match the TLS fingerprint.
IMPERSONATE = "chrome150"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/150.0.0.0 Safari/537.36")
SEC_CH_UA = '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"'
SEC_CH_UA_PLATFORM = '"macOS"'
LANGUAGE = "en-US"
TIMEZONE = "Asia/Ho_Chi_Minh"
TIMEZONE_OFFSET_MIN = -420        # the page's `new Date().getTimezoneOffset()` in TIMEZONE
IMAGE_HINT = "picture_v2"         # the send's `system_hints`: draw an image

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                  # counted attempts before a job fails
SUSPECT_THRESHOLD = 3             # strikes before the account is dead
STRIKE_DEAD_REASON = "signed_out"
PROXY_REST_S = 1800               # a failed proxy rests every pool
CHECK_INTERVAL_S = 3600           # the account check: the session read, the identity and the plan

# ---- requests and results ----------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000           # well inside what the web composer takes
INPUT_MAX_BYTES = 20 * 2**20      # one input image uploaded
MEDIA_MAX_BYTES = 64 * 2**20      # one result file (a 1536x1024 PNG is a few MB)

# ---- timing ---------------------------------------------------------------------------------------------------
API_TIMEOUT_S = 30                # one plain call over a proxy
UPLOAD_TIMEOUT_S = 120            # one input image to the upload host
MEDIA_DEADLINE_S = 120            # one result download
SUBMIT_TIMEOUT_S = 480            # a submit: the session, the sentinel, an upload and the held stream (the stream
                                  # is held for what is left of it; no end in time: polled instead)
TOKEN_MARGIN_S = 600              # an access token this close to its `exp` is read again before a call

# ---- provider_state keys (no secret goes in them) ---------------------------------------------------------------
STATE_MESSAGE = "message"         # the client-made id of the sent user message (written ahead)
STATE_CONVERSATION = "conversation_id"   # the key the admin job drawer reads (`_upstream_ids`)
STATE_PROMPT = "sent_prompt"      # the exact text sent, for a reconcile that cannot read the message id

# ---- the Fixed limits card ------------------------------------------------------------------------------------
LIMITS = (
    FixedLimit("relay_gpt.constants.TIMEZONE_OFFSET_MIN", TIMEZONE_OFFSET_MIN,
               "the browser time zone offset the anti-bot config reports; it matches the time zone sent", "min"),
    FixedLimit("relay_gpt.constants.INPUT_MAX_BYTES", INPUT_MAX_BYTES, "one input image sent to chatgpt.com",
               "bytes"),
    FixedLimit("relay_gpt.constants.MEDIA_MAX_BYTES", MEDIA_MAX_BYTES, "one result file downloaded", "bytes"),
    FixedLimit("relay_gpt.constants.API_TIMEOUT_S", API_TIMEOUT_S,
               "one plain call over a proxy; none in time is a failed step", "s"),
    FixedLimit("relay_gpt.constants.UPLOAD_TIMEOUT_S", UPLOAD_TIMEOUT_S,
               "one input image to the upload host; nothing is sent yet, so a timeout is a retry", "s"),
    FixedLimit("relay_gpt.constants.MEDIA_DEADLINE_S", MEDIA_DEADLINE_S, "one result download", "s"),
    FixedLimit("relay_gpt.constants.SUBMIT_TIMEOUT_S", SUBMIT_TIMEOUT_S,
               "hard bound on one submit: the session, the anti-bot steps, an upload and the held stream", "s"),
    FixedLimit("relay_gpt.constants.TOKEN_MARGIN_S", TOKEN_MARGIN_S,
               "an access token lives 10 days; one this close to its end is read again before a call", "s"),
)
