"""meta spec constants: the site facts and the numbers that are not live settings.

The site facts (hosts, the gateway query, the answer's field paths, cookie names) come from the 05/11 HTTP spike of
meta.ai and are kept here, in one place, so a change on the site is one edit. Pure: imported by the spec, the
credential reader and the engine alike, and it imports no curl_cffi.
"""

from relay_core.providers import FixedLimit

PROVIDER = "meta"

# ---- the site: hosts and paths ----------------------------------------------------------------------------------
SITE_URL = "https://www.meta.ai"
GATEWAY_URL = "wss://gateway.meta.ai/ws/clippy"   # one WebSocket per job; the token rides in its query: never logged
UPLOAD_URL = "https://rupload.meta.ai/gen_ai_document_gen_ai_tenant/"   # POST .../<uuid4> answers `{media_id}`
CHAT_PATH = "/prompt/"                            # `/prompt/<conversation uuid>`: the reconcile page
SITE_HOST = "meta.ai"                             # a page whose host is not this one (or a subdomain) is not signed in
COOKIE_DOMAIN = ".meta.ai"                        # for a bare `Cookie:` header string, which carries no domain
SESSION_COOKIES = ("ecto_1_sess", "abra_sess")    # a jar needs one of them; real jars carry `ecto_1_sess` alone
MEDIA_HOSTS = ("fbcdn.net",)                      # the final images: `scontent.xx.fbcdn.net/v/t39.105495-1/...`

# ---- the gateway ------------------------------------------------------------------------------------------------
# The query of the WebSocket URL, less `Authorization` (the token) and `x-dgw-app-clippy-request-id` (a uuid per job).
GATEWAY_QUERY = {"x-dgw-appid": "1522763855472543", "x-dgw-appversion": "1.0.0", "x-dgw-authtype": "15:0",
                 "x-dgw-version": "5", "x-dgw-uuid": "0", "x-dgw-tier": "prod", "x-dgw-app-origin": "meta.ai"}
# The fixed leaves of the Request's `product_context`, as (field path, value): to be copied from the `const` rows of
# fixtures/meta/http-261105/request_skeleton.json (Task 0). Empty until that fixture is merged.
PRODUCT_CONTEXT: tuple[tuple[str, str], ...] = ()
TIME_ZONE = "Europe/Berlin"                       # the proxies are German; the browser era sent GMT+7
LOCALE = "en-US"
# The newest Chrome profile of the locked curl_cffi 0.16.3 (the spike's chrome152 is 0.17-only), and a Linux UA of it.
IMPERSONATE = "chrome150"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

# ---- what the page and the answer look like ---------------------------------------------------------------------
LOGGED_IN_MARK = '\\"isLoggedIn\\":true'          # the page's embedded JSON, escaped once
VERCEL_MARK = "Vercel Security Checkpoint"
RD_MARK = "?challenge="                           # the 403 `rd` page names the POST that passes it
FINAL_IMAGE_MARK = "/t39.105495-1/"               # a generated image
PREVIEW_MARK = "/t58."                            # the low-res preview that comes first; never delivered
INPUT_MARK = "/t0.71334-"                         # an input image of a chat; never a result
# Protobuf field paths of a gateway `Response` (the embedded schema, 05/11).
FINAL_PATH = (1, 4, 6, 2, 3, 1)                   # `cdn_url_original`
MIME_PATH = (1, 4, 6, 2, 6)
TOOL_RESULT_PATHS = ((1, 4, 6, 4), (1, 2, 4))     # the `media_subagent` tool result JSON
TEXT_PATH = (1, 4, 2)                             # the answer text
FINAL_CHUNK_FIELD = 3                             # the top-level `is_final_chunk`
# A refusal's structured kind (`failure_kind`) or code (`error_code`) in the tool result. No limit kind is seen yet.
POLICY_KINDS = ("policy_denied",)
POLICY_CODES = ("integrity_check_failed",)
LIMIT_KINDS: tuple[str, ...] = ()

# meta has no ratio control: it reads the ratio from the words. Typed after the final text as ", <ratio>", these came
# out at exactly that ratio from text alone (FINDINGS.md, 23/10); any other ratio, or a job from an input image, is
# refused rather than drawn at meta's own size. A video asked ", 16:9" or ", 1:1" came back with no video at all, but
# a plain text-to-video comes out 1280x720: a 16:9 video is sent with no tail (`RATIO_UNTYPED`).
RATIO_TAIL = ", %s"
RATIOS = {"image": ("16:9", "9:16", "1:1", "4:3", "3:4"), "video": ("16:9", "9:16")}
RATIO_UNTYPED = (("video", "16:9"),)
# meta's limit or quota answer (an answer with no media), lowercased, straight apostrophes. NOT measured: no relay
# capture holds one yet; these are the wordings the earlier meta tool (meta2api `limit_phrases`) watched for.
LIMIT_PHRASES = ("reached your limit", "reached the limit", "hit your limit", "limit for today", "daily limit",
                 "out of quota", "too many requests", "come back later", "come back tomorrow")

# ---- timing ---------------------------------------------------------------------------------------------------
PROXY_REST_S = 1800             # a failed proxy probe rests every pool
CHECK_INTERVAL_S = 6 * 3600     # account check cadence
SUBMIT_TIMEOUT_MAX_S = 720      # the most a submit timeout setting may hold: the worker's stop grace is 780 s

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails
SUSPECT_THRESHOLD = 3           # strikes before the account is dead
STRIKE_DEAD_REASON = "signed_out"
SIGNED_OUT_REST_S = 600         # a signed-out read rests the account this long, then it is checked again
SIGNED_OUT_GAP_S = 540          # a signed-out read less than this long after the last counted strike adds none;
#                                 shorter than the rest, so the recheck after a rest always counts

# ---- requests -------------------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000         # not measured on meta's composer yet: a cap well inside what a chat message takes

# ---- one run's HTTP ---------------------------------------------------------------------------------------------
SOCKET_OPEN_S = 30              # the WebSocket handshake
ESTAB_WAIT_S = 15               # the EstabStream answer
UPLOAD_TIMEOUT_S = 60           # one input image POSTed to rupload
PAGE_TIMEOUT_S = 45             # one www.meta.ai page read
UPLOAD_MAX_BYTES = 1 << 20      # an input over this, or not JPEG/PNG/WebP, is re-encoded to a JPEG
UPLOAD_MAX_SIDE = 2048          # the longest side of that JPEG
FAULT_REST_S = 300              # the rest after a proxy fault

# ---- the Fixed limits card ------------------------------------------------------------------------------------
# The numbers `Caps` already carries (proxy rest, check interval, attempts, strikes, prompt size) are listed by the
# core from the spec, so they are not repeated here.
LIMITS = (
    FixedLimit("relay_meta.constants.SOCKET_OPEN_S", SOCKET_OPEN_S,
               "the WebSocket to meta's gateway must open within this", "s"),
    FixedLimit("relay_meta.constants.ESTAB_WAIT_S", ESTAB_WAIT_S,
               "the gateway must answer the EstabStream within this", "s"),
    FixedLimit("relay_meta.constants.UPLOAD_TIMEOUT_S", UPLOAD_TIMEOUT_S, "one input image upload", "s"),
    FixedLimit("relay_meta.constants.PAGE_TIMEOUT_S", PAGE_TIMEOUT_S, "one www.meta.ai page read", "s"),
    FixedLimit("relay_meta.constants.UPLOAD_MAX_BYTES", UPLOAD_MAX_BYTES,
               "an input image over this size is re-encoded to a JPEG before it is uploaded", "bytes"),
    FixedLimit("relay_meta.constants.UPLOAD_MAX_SIDE", UPLOAD_MAX_SIDE,
               "the longest side of that re-encoded JPEG", "px"),
    FixedLimit("relay_meta.constants.FAULT_REST_S", FAULT_REST_S, "the rest after a proxy fault", "s"),
    FixedLimit("relay_meta.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_meta.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_meta.constants.SUBMIT_TIMEOUT_MAX_S", SUBMIT_TIMEOUT_MAX_S,
               "the most a submit timeout setting may hold: the worker's stop grace (780 s) covers it", "s"),
)
