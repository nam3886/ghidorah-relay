"""flow spec constants: the site facts and the numbers that are not live settings.

Google Flow (flow.google.com) answers plain HTTP: an Angular page that carries the request tokens, and Google's
`batchexecute` RPC transport (fixtures/flow/FINDINGS.md). The account is a Google cookie session whose
`__Secure-1PSIDTS` ages out in about 20 min unless the provider rotates it. Pure: imported by the spec, the credential
reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "flow"

# ---- the site ---------------------------------------------------------------------------------------------------
BASE_URL = "https://flow.google.com"
GOOGLE_DOMAIN = "google.com"          # every cookie of the session is for google.com or a subdomain of it
SESSION_COOKIE = "__Secure-1PSID"     # without it there is no Google session to keep
ROTATE_URL = "https://accounts.google.com/RotateCookies"     # Chrome's own __Secure-1PSIDTS refresh
ROTATE_BODY = '[000,"-0000000000000000000"]'
RPC_PATH = "/_/AiSandboxAngularFrontend/data/batchexecute"
MEDIA_HOSTS = ("flow-content.google",)                      # the signed result links; no cookie is sent there
OUTPUT_MIME = "image/jpeg"
VIDEO_MIME = "video/mp4"          # H.264 1280x720 8 s with AAC (FINDINGS 5)
# reCAPTCHA Enterprise of flow.google.com: the token of a generate is minted for its page action
RECAPTCHA_SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
IMAGE_ACTION = "IMAGE_GENERATION"
VIDEO_ACTION = "VIDEO_GENERATION"
# The token source. Only YesCaptcha is built; its key is a secret of the worker's environment, never a setting.
CAPTCHA_PROVIDERS = ("yescaptcha",)
YESCAPTCHA_URL = "https://api.yescaptcha.com"
YESCAPTCHA_TASK = "RecaptchaV3TaskProxylessM1"
YESCAPTCHA_KEY_VAR = "FLOW_YESCAPTCHA_KEY"
# A desktop Chrome, as the web app is used.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")

# ---- timing ---------------------------------------------------------------------------------------------------
API_TIMEOUT_S = 30              # one page load, read or cookie rotation over a proxy (reads took 0.3-1.5 s)
GENERATE_TIMEOUT_S = 90         # the image generate holds its reply until drawn (13-17 s); a video's answers in 2.6 s
CAPTCHA_TIMEOUT_S = 120         # one reCAPTCHA token from the solver (10-17 s measured)
CAPTCHA_POLL_S = 3              # between two reads of the solver's task
SUBMIT_TIMEOUT_S = 900          # one submit (image or video), its spaced reCAPTCHA retries included
PROXY_REST_S = 1800             # a failed proxy probe rests every pool
CHECK_INTERVAL_S = 300          # the account check: it also rotates the session, which ages out in about 20 min
SIGNED_OUT_REST_S = 600         # a signed-out read rests the account this long, then it is checked again
SIGNED_OUT_GAP_S = 540          # a signed-out read this soon after the last counted strike adds none
NETWORK_REST_S = 300            # a submit whose proxy could not reach flow rests the account this long

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails
SUSPECT_THRESHOLD = 3           # strikes before the account is dead
STRIKE_DEAD_REASON = "signed_out"

# ---- requests -------------------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000         # not measured on flow yet: a cap well inside what a generate request takes

# ---- the Fixed limits card ------------------------------------------------------------------------------------
LIMITS = (
    FixedLimit("relay_flow.constants.API_TIMEOUT_S", API_TIMEOUT_S,
               "one page load, read or cookie rotation over a proxy; none in time is a failed step", "s"),
    FixedLimit("relay_flow.constants.GENERATE_TIMEOUT_S", GENERATE_TIMEOUT_S,
               "the generate holds its reply until the image is drawn; no answer in time makes the job unknown", "s"),
    FixedLimit("relay_flow.constants.CAPTCHA_TIMEOUT_S", CAPTCHA_TIMEOUT_S,
               "one reCAPTCHA token from the solver; none in time is a failed try", "s"),
    FixedLimit("relay_flow.constants.CAPTCHA_POLL_S", CAPTCHA_POLL_S, "between two reads of the solver's task", "s"),
    FixedLimit("relay_flow.constants.SUBMIT_TIMEOUT_S", SUBMIT_TIMEOUT_S,
               "hard bound on one submit; no reCAPTCHA retry starts that would not end inside it", "s"),
    FixedLimit("relay_flow.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_flow.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_flow.constants.NETWORK_REST_S", NETWORK_REST_S,
               "a submit whose proxy could not reach flow rests the account this long", "s"),
)
