"""grok spec constants: the site facts and the numbers that are not live settings.

Grok Imagine (grok.com) is reached over plain HTTP with Chrome's TLS fingerprint (curl_cffi) and one WebSocket
(aiohttp), through the account's proxy. The account is a cookie jar whose `sso` cookie is all the site needs; nothing
here refreshes it (plans/grok-spike-261106.md). Pure: imported by the spec, the credential reader and the engine
alike, and it imports no curl_cffi.
"""

from relay_core.providers import FixedLimit

PROVIDER = "grok"

# ---- the site: hosts and paths ---------------------------------------------------------------------------------------
SITE_URL = "https://grok.com"
SITE_HOST = "grok.com"
ASSETS_HOST = "assets.grok.com"                   # an edit's image and a video: fetched with the account's cookie
PUBLIC_HOST = "imagine-public.x.ai"               # a text-to-image result: public, fetched with no cookie
COOKIE_HOSTS = (SITE_HOST, ASSETS_HOST)           # the only hosts the account's cookies are ever sent to
COOKIE_DOMAIN = ".grok.com"                       # for a bare `Cookie:` header, which carries no domain
SSO_COOKIE = "sso"                                # the cookie a jar must hold

SESSION_PATH = "/api/auth/session"                # GET: {"status": "authenticated", "session": {userId, ...}}
QUOTA_PATH = "/rest/media/imagine/quota_info"     # POST {}: the image and video uses left (spends nothing)
UPLOAD_PATH = "/http/upload-file-v2/direct"       # POST multipart: one input image -> fileMetadataId
NEW_CONVERSATION_PATH = "/rest/app-chat/conversations/new"   # POST: the one send of an edit or a video (NDJSON)
IMAGINE_WS = "wss://grok.com/ws/imagine/listen"   # the one send of a text-to-image job (one item.create frame)
# The read-back paths of the reconcile (step-0 probe, plans/grok-design-261106/step0.md): GET only. A path is matched
# whole (the template with one id filled in), never as a prefix: NEW_CONVERSATION_PATH starts like the list.
LIST_CONVERSATIONS_PATH = "/rest/app-chat/conversations"          # recent conversations (twice: no kind, and IMAGINE)
RESPONSES_PATH = "/rest/app-chat/conversations/%s/responses"      # one conversation's turns and its outputs
READ_PATHS = (LIST_CONVERSATIONS_PATH, RESPONSES_PATH)

UPLOAD_SOURCE = "IMAGINE_SELF_UPLOAD_FILE_SOURCE"
EDIT_MODEL = "imagine-image-edit"
VIDEO_MODEL = "imagine-video-gen"
VIDEO_KIND = "CONVERSATION_KIND_IMAGINE"
VIDEO_MODE_TAIL = " --mode=custom"                # appended to a video's message, as the page does
VIDEO_DURATION_S = 6                              # the only length a free account makes
VIDEO_RESOLUTION = "720p"

# The newest Chrome profile of the locked curl_cffi 0.16.3 (the spike's 0.17 `chrome` is not locked), and a Linux UA of
# it: the worker runs on Linux. Checked again when the engine is wired.
IMPERSONATE = "chrome150"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

# ---- what an answer says (case-folded phrase matches; chenyme's words, not yet seen live) ----------------------------
LIMIT_PHRASES = ("usage limit", "usage quota", "usage_limit")
# Seen live 06/11 (fixture imagine_rate_limit_frame): an Imagine `error` frame "Image rate limit exceeded", with uses
# still left in `quota_info`: a throttle of the account, not its daily quota.
RATE_LIMIT_PHRASES = ("rate limit",)
# The Imagine socket's error codes, as the 06/11 page bundle handles them (its imagine-websocket onError): a throttle of
# the account (`rate_limit_exceeded`, seen live; `concurrency_limit`), and its use pools spent (`usage_limit_reached`,
# `usage_pool_exhausted`). `image_query_rejected` is a refusal of the prompt (age verification, copyright), not listed.
IMAGINE_RATE_CODES = ("rate_limit_exceeded", "concurrency_limit")
IMAGINE_LIMIT_CODES = ("usage_limit_reached", "usage_pool_exhausted")
SAFETY_PHRASES = ("content violates usage guidelines", "safety_check_type_", "content policy", "content moderation")
# A Cloudflare challenge page. Never `/cdn-cgi/challenge-platform/` alone: every normal grok.com page loads that script.
CF_MARKS = ("just a moment", "__cf_chl", "cf-chl-")
BLOCKED_PHRASES = ("blocked-user", "user is blocked")
ANTIBOT_CODE = 7                                  # 403 {"error": {"code": 7, ...}}: anti-bot rules
SIGNED_OUT_CODE = 16                              # 401 {"code": 16, "message": "... [WKE=unauthenticated:...]"}
BAD_BODY_CODE = 3                                 # 400 {"error": {"code": 3, ...}}: our own body was wrong

QUOTA_IMAGE_KEY = "imagePro"                      # a text-to-image and an edit both spend it, one use per image
QUOTA_VIDEO_KEY = "video720p"
QUOTA_WINDOW = "quota"                            # the `grok.pools` window a `Balance` refill anchors

# ---- attempts, strikes, rests ----------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                  # counted attempts before a job fails
SUSPECT_THRESHOLD = 3             # strikes before the account is dead (signed out)
STRIKE_DEAD_REASON = "signed_out"
PROXY_REST_S = 300                # a failed proxy rests every pool
CHECK_INTERVAL_S = 3600           # the account check: the session, then quota_info (both spend nothing)

# ---- requests and results --------------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000           # grok's own cap is not measured
MAX_COOKIES = 300                 # far above a real grok.com jar; a longer list is not a cookie export
MEDIA_MAX_BYTES = 64 * 2**20      # one result file (a 6 s video was 4.4 MB)

# ---- timing ----------------------------------------------------------------------------------------------------------
REST_TIMEOUT_S = 30               # one plain call over a proxy
WS_OPEN_S = 30                    # opening the Imagine WebSocket
UPLOAD_TIMEOUT_S = 60             # one input image (3.0 s measured)
QUOTA_READ_S = 5                  # the quota read after a job (best effort)
HOLD_MARGIN_S = 15                # a submit gives its verdict this long before its timeout
MEDIA_DEADLINE_S = 120            # one result download
STATSIG_SEED_MAX_AGE_S = 3600     # the x-statsig-id seed is read from the page again after this long

# ---- provider_state keys (no secret goes in them) --------------------------------------------------------------------
STATE_OP = "op"                       # t2i, edit or t2v
STATE_REQUEST = "request"             # the run's own request id (the admin job drawer reads `request`)
STATE_CONVERSATION = "conversation_id"   # the key the admin job drawer reads (`_upstream_ids`)
STATE_VIDEO = "video_id"
STATE_POST = "video_post_id"
STATE_SENT_TEXT = "sent_text"         # the exact text sent, for a reconcile that has only the request id
STATE_INPUTS = "inputs"               # an edit's uploaded file ids, in order
STATE_SENT_AT = "sent_at"             # unix seconds of the send

# ---- the Fixed limits card -------------------------------------------------------------------------------------------
LIMITS = (
    FixedLimit("relay_grok.constants.MAX_COOKIES", MAX_COOKIES,
               "far above a real grok.com jar; a longer list is not a cookie export", "cookies"),
    FixedLimit("relay_grok.constants.MEDIA_MAX_BYTES", MEDIA_MAX_BYTES, "one result file downloaded", "bytes"),
    FixedLimit("relay_grok.constants.REST_TIMEOUT_S", REST_TIMEOUT_S,
               "one plain call over a proxy; none in time is a failed step", "s"),
    FixedLimit("relay_grok.constants.WS_OPEN_S", WS_OPEN_S,
               "opening the Imagine WebSocket; nothing is sent yet, so a timeout is a retry", "s"),
    FixedLimit("relay_grok.constants.UPLOAD_TIMEOUT_S", UPLOAD_TIMEOUT_S,
               "one input image uploaded; nothing is sent yet, so a timeout is a retry", "s"),
    FixedLimit("relay_grok.constants.QUOTA_READ_S", QUOTA_READ_S,
               "the quota read after a job; it never changes the job's result", "s"),
    FixedLimit("relay_grok.constants.HOLD_MARGIN_S", HOLD_MARGIN_S,
               "a submit gives its verdict this long before its timeout ends", "s"),
    FixedLimit("relay_grok.constants.MEDIA_DEADLINE_S", MEDIA_DEADLINE_S, "one result download", "s"),
    FixedLimit("relay_grok.constants.STATSIG_SEED_MAX_AGE_S", STATSIG_SEED_MAX_AGE_S,
               "the anti-bot header seed is read from grok.com's page again after this long", "s"),
)
