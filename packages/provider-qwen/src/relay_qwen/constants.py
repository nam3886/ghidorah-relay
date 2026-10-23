"""qwen spec constants: the site facts and the numbers that are not live settings.

Qwen Chat (chat.qwen.ai) is a JSON API behind Alibaba's WAF (fixtures/qwen/FINDINGS.md). The account is a cookie jar
whose `refresh_token` (30 days) buys a 15-min access token over plain HTTP. Only the paid call, `chat/completions`,
needs the `bx-ua`/`bx-umidtoken` pair that the page's baxia script mints; a browser mints it, plain HTTP reuses it.
Pure: imported by the spec, the credential reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "qwen"

# ---- the site ---------------------------------------------------------------------------------------------------
BASE_URL = "https://chat.qwen.ai"
REFRESH_URL = "https://auth.qwen.ai/api/v2/auths/refresh"     # the jar -> a 15-min access token (and the user)
CREDITS_URL = "https://sg-entitlement.qwen.com/api/entitlement/credits/credits/history"
CREDITS_QUERY = {"pageNum": "1", "pageSize": "5", "biz_id": "ai_qwen", "chat_client": "h5", "device": "pc",
                 "fr": "pc", "pr": "qwen-i18n", "la": "en-US", "tz": "UTC", "wv": "0.3.22"}
QWEN_DOMAIN = "qwen.ai"               # every cookie the account needs is for qwen.ai or a subdomain of it
REFRESH_COOKIE = "refresh_token"      # without it there is no session
MEDIA_HOSTS = ("cdn.qwenlm.ai",)      # the result links (a `key` JWT in the query, no cookie)
OSS_HOST_SUFFIX = ".aliyuncs.com"     # where an input image is PUT (the bucket and endpoint come from getstsToken)
CHAT_MODEL = "qwen3.7-plus"           # the chat model every generate names; the media model goes in `extra.meta`
WEB_VERSION = "0.3.22"                # the web app's `Version` header until a mint reads the page's own
BX_VERSION = "2.5.37"
# Chrome 154 on Linux, as the worker image runs it: the headers of a call made before the first mint.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 "
              "Safari/537.36")
SEC_CH_UA = '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"'
SEC_CH_UA_PLATFORM = '"Linux"'
MINT_PROMPT = "a red apple"           # what the page is let to build; its send is aborted inside the browser

# ---- timing ---------------------------------------------------------------------------------------------------
API_TIMEOUT_S = 30              # one plain call over a proxy (0.5-2 s measured)
TOKEN_REUSE_S = 600             # an access token lives 15 min (measured); a run older than this refreshes it first
IMAGE_READ_TIMEOUT_S = 60       # the image stream sends a keep-alive every 10 s: a minute of silence is a dead stream
IMAGE_STREAM_TIMEOUT_S = 360    # the image stream ended in 60-108 s (measured); no end in time makes the job unknown
VIDEO_SEND_TIMEOUT_S = 90       # the video send answers with its task id in about 4 s
UPLOAD_TIMEOUT_S = 120          # one input image to the object store
MINT_TIMEOUT_S = 150            # one browser mint: Chrome start, the page load through the proxy, the aborted send
SUBMIT_TIMEOUT_IMAGE_S = 720    # an image submit: a mint, an upload and the held stream
SUBMIT_TIMEOUT_VIDEO_S = 480    # a video submit: a mint, an upload and the send; the render is polled
PROXY_REST_S = 1800             # a failed proxy probe rests every pool
CHECK_INTERVAL_S = 600          # the account check: it reads the credits and keeps the anti-bot pair fresh
SIGNED_OUT_REST_S = 600         # a signed-out read rests the account this long, then it is checked again
SIGNED_OUT_GAP_S = 540          # a signed-out read this soon after the last counted strike adds none
NETWORK_REST_S = 300            # a submit whose proxy could not reach qwen rests the account this long

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails
SUSPECT_THRESHOLD = 3           # strikes before the account is dead
STRIKE_DEAD_REASON = "signed_out"

# ---- money ----------------------------------------------------------------------------------------------------
FALLBACK_PRICE = 1              # a model missing from `qwen.prices`, charged with a warning
IMAGE_ACCOUNT_COST = 1          # an image took no qwen credit (8 of 8 measured): each counts one against the budget

# ---- requests and results ----------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000         # not measured on qwen yet: a cap well inside what a generate request takes
INPUT_MAX_BYTES = 20 * 2**20    # one input image sent to the object store
MEDIA_MAX_BYTES = 256 * 2**20   # one result file (a 1664x928 PNG was 10.7 MB, a 5 s 720p mp4 a few MB)
MEDIA_DEADLINE_S = 300          # one result download

# ---- the Fixed limits card ------------------------------------------------------------------------------------
LIMITS = (
    FixedLimit("relay_qwen.constants.API_TIMEOUT_S", API_TIMEOUT_S,
               "one plain call over a proxy; none in time is a failed step", "s"),
    FixedLimit("relay_qwen.constants.TOKEN_REUSE_S", TOKEN_REUSE_S,
               "an access token lives 15 min: a run older than this refreshes it before its calls", "s"),
    FixedLimit("relay_qwen.constants.IMAGE_READ_TIMEOUT_S", IMAGE_READ_TIMEOUT_S,
               "the image stream keeps itself alive every 10 s; this long without a line is a dead stream", "s"),
    FixedLimit("relay_qwen.constants.IMAGE_STREAM_TIMEOUT_S", IMAGE_STREAM_TIMEOUT_S,
               "the image stream ends in 60-108 s; no end in time makes the job unknown (reconciled)", "s"),
    FixedLimit("relay_qwen.constants.VIDEO_SEND_TIMEOUT_S", VIDEO_SEND_TIMEOUT_S,
               "the video send answers its task id in about 4 s; no answer in time makes the job unknown", "s"),
    FixedLimit("relay_qwen.constants.UPLOAD_TIMEOUT_S", UPLOAD_TIMEOUT_S,
               "one input image to the object store; nothing is sent yet, so a timeout is a retry", "s"),
    FixedLimit("relay_qwen.constants.MINT_TIMEOUT_S", MINT_TIMEOUT_S,
               "one browser mint of the anti-bot pair; none in time is a retry before the send", "s"),
    FixedLimit("relay_qwen.constants.SUBMIT_TIMEOUT_IMAGE_S", SUBMIT_TIMEOUT_IMAGE_S,
               "hard bound on one image submit: a mint, an upload and the held stream", "s"),
    FixedLimit("relay_qwen.constants.SUBMIT_TIMEOUT_VIDEO_S", SUBMIT_TIMEOUT_VIDEO_S,
               "hard bound on one video submit: a mint, an upload and the send; the render is polled", "s"),
    FixedLimit("relay_qwen.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_qwen.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_qwen.constants.NETWORK_REST_S", NETWORK_REST_S,
               "a submit whose proxy could not reach qwen rests the account this long", "s"),
    FixedLimit("relay_qwen.constants.FALLBACK_PRICE", FALLBACK_PRICE,
               "charged for a model missing from qwen.prices", "points"),
    FixedLimit("relay_qwen.constants.IMAGE_ACCOUNT_COST", IMAGE_ACCOUNT_COST,
               "an image costs no qwen credit: each job counts one against the daily image budget", "per job"),
    FixedLimit("relay_qwen.constants.INPUT_MAX_BYTES", INPUT_MAX_BYTES, "one input image sent to qwen", "bytes"),
    FixedLimit("relay_qwen.constants.MEDIA_MAX_BYTES", MEDIA_MAX_BYTES, "one result file downloaded", "bytes"),
    FixedLimit("relay_qwen.constants.MEDIA_DEADLINE_S", MEDIA_DEADLINE_S, "one result download", "s"),
)
