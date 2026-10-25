"""gemini spec constants: the site facts and the numbers that are not live settings.

Gemini web (gemini.google.com) answers plain HTTP: a page that carries the request tokens, the `StreamGenerate`
stream for a send, and Google's `batchexecute` RPCs for reads (fixtures/gemini/FINDINGS.md). The account is a Google
cookie session whose `__Secure-1PSIDTS` ages out in about 20 min unless the provider rotates it. Pure: imported by the
spec, the credential reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "gemini"

# ---- the site ---------------------------------------------------------------------------------------------------
BASE_URL = "https://gemini.google.com"
GOOGLE_DOMAIN = "google.com"          # every cookie of the session is for google.com or a subdomain of it
SESSION_COOKIE = "__Secure-1PSID"     # without it there is no Google session to keep
ROTATE_URL = "https://accounts.google.com/RotateCookies"     # Chrome's own __Secure-1PSIDTS refresh
ROTATE_BODY = '[000,"-0000000000000000000"]'
STREAM_PATH = "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
RPC_PATH = "/_/BardChatUi/data/batchexecute"
UPLOAD_URL = "https://push.clients6.google.com/upload/"      # the web app's resumable upload of an input image
UPLOAD_PUSH_ID = "feeds/mcudyrk2a4khkz"
UPLOAD_TENANT = "bard-storage"
# The image link of a reply (`/gg-dl/`) or of a chat read (`/gg/`) answers, with `=d-I` and `alr=yes`, the next link
# as text: lh3 -> work.fife.usercontent.google.com or lh3.google.com (these two need the session cookies) -> lh3
# with the bytes (no cookie). Every hop must be one of these hosts.
LINK_HOSTS = ("lh3.googleusercontent.com", "work.fife.usercontent.google.com", "lh3.google.com")
BYTES_HOST = "lh3.googleusercontent.com"
FULL_SIZE = "=d-I"                    # the original file (measured 23/10: 1408x768, the size the reply names)
# A video's link with `=m22` hops (the same way) to the 720p MP4 the web app plays, served by a *.googlevideo.com
# host with no cookie (measured 24/10: 1280x720 h264 + aac, 10.0 s). `=d-I` answers only its poster frame (JPEG).
VIDEO_SIZE = "=m22"
VIDEO_BYTES_HOST = "googlevideo.com"
MODE_AUTO = "cf41b0e0dd7d53e5"        # the mode menu's "Auto" (Gemini 3 models), the only one a free account has
IMAGE_TOOL = 14                       # inner[49] of a send with the image tool chosen
VIDEO_TOOL = 11                       # inner[49] of a send with the video tool ("Create video") chosen
MAX_ACCOUNTS = 10                     # a Google session holds at most 10 signed-in accounts: /u/0../u/9
GENERATING_MARK = "video_generation"  # the "I'm generating your video" item of a video send that was taken
# The video limit answer, in `submit.plain` form (lowercase, straight apostrophes, "cannot" as "can't", no other
# punctuation). Measured 23/10: "I can't create more videos for you today, ..."; "I can create more videos as soon as
# your limit resets" with its "out of video generation quota" status (zexadev/gemini-web2api-go, 24/10); the others
# are likely rewordings.
VIDEO_LIMIT_PHRASES = ("can't create more videos", "can't make more videos", "can't generate more videos",
                       "no more videos today", "reached your video limit", "reached the video limit",
                       "reached your daily video limit", "video generation limit", "as soon as your limit resets",
                       "out of video generation quota")
# Words of a quota-like answer (English, Vietnamese, Spanish, French, German, Japanese/Chinese): a video refusal that
# holds one also empties the account's video pool, since it may be the limit answer worded otherwise. Never used for
# images: no image limit wording is measured, and these words are common in an ordinary refusal.
QUOTA_WORDS = ("limit", "quota", "today", "tomorrow", "try again later", "giới hạn", "hôm nay", "ngày mai", "límite",
               "hoy", "limite", "aujourd'hui", "heute", "上限", "今日", "今天")
CAPABILITIES = [4, 5, 6, 8, 16, 19, 20, 4, 5, 6, 8, 16, 19, 20]     # the web app's capability list (23/10)
# A desktop Chrome, as the web app is used.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
NOT_ENTITLED_LINK = "gemini.google/subscriptions"     # the "upgrade your subscription" answer of a tool not in the plan

# ---- timing ---------------------------------------------------------------------------------------------------
API_TIMEOUT_S = 30              # one page load, read, upload step or cookie rotation over a proxy (0.3-2 s measured)
GENERATE_TIMEOUT_S = 180        # an image send streams until the image is drawn (25-28 s), a video send until it is
                                # taken (10-33 s measured); the video itself is read from the chat after
SUBMIT_TIMEOUT_S = 300          # one submit: page, upload, send
PROXY_REST_S = 1800             # a failed proxy probe rests every pool
CHECK_INTERVAL_S = 300          # the account check: it also rotates the session, which ages out in about 20 min
SIGNED_OUT_REST_S = 600         # a signed-out read rests the account this long, then it is checked again
SIGNED_OUT_GAP_S = 540          # a signed-out read this soon after the last counted strike adds none
NETWORK_REST_S = 300            # a submit whose proxy could not reach gemini rests the account this long
FIND_BEFORE_S = 60              # reconcile: a chat listed this long before the send started can still be the job's
FIND_AFTER_S = 300              # ... and this long after it (measured: listed 1 s after the send started)

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails
SUSPECT_THRESHOLD = 3           # strikes before the account is dead
STRIKE_DEAD_REASON = "signed_out"

# ---- requests and results -------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000         # not measured on gemini: a cap well inside what a send takes
MEDIA_MAX_BYTES = 64 * 2**20    # one result; a 1408x768 PNG was 1.2-1.9 MB, a 10 s 720p MP4 1.5-2.2 MB
MEDIA_DEADLINE_S = 120          # one result download, every hop included
LINK_HOPS = 6                   # the text hops of one result link (3 measured)
LIST_PAGE = 13                  # chats the web app lists per read (MaZiqc)

# ---- the Fixed limits card ------------------------------------------------------------------------------------
LIMITS = (
    FixedLimit("relay_gemini.constants.API_TIMEOUT_S", API_TIMEOUT_S,
               "one page load, read, upload step or cookie rotation over a proxy; none in time is a failed step", "s"),
    FixedLimit("relay_gemini.constants.GENERATE_TIMEOUT_S", GENERATE_TIMEOUT_S,
               "the send streams until the image is drawn; no end in time makes the job unknown", "s"),
    FixedLimit("relay_gemini.constants.SUBMIT_TIMEOUT_S", SUBMIT_TIMEOUT_S,
               "hard bound on one submit: page, upload and send", "s"),
    FixedLimit("relay_gemini.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_gemini.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_gemini.constants.NETWORK_REST_S", NETWORK_REST_S,
               "a submit whose proxy could not reach gemini rests the account this long", "s"),
    FixedLimit("relay_gemini.constants.FIND_BEFORE_S", FIND_BEFORE_S,
               "a lost send is looked for among chats started from this long before it", "s"),
    FixedLimit("relay_gemini.constants.FIND_AFTER_S", FIND_AFTER_S,
               "... up to this long after it (a chat is listed with the time its send started)", "s"),
    FixedLimit("relay_gemini.constants.MAX_ACCOUNTS", MAX_ACCOUNTS,
               "the /u/N indexes the check looks through for the account's email", "accounts"),
    FixedLimit("relay_gemini.constants.MEDIA_MAX_BYTES", MEDIA_MAX_BYTES, "one result image or video", "bytes"),
    FixedLimit("relay_gemini.constants.MEDIA_DEADLINE_S", MEDIA_DEADLINE_S, "one result download", "s"),
    FixedLimit("relay_gemini.constants.LINK_HOPS", LINK_HOPS, "text hops of one result link (3 measured)", "hops"),
)
