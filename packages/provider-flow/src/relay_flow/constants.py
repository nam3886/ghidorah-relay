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
# The token sources (solver services). Each key is a secret of the worker's environment, never a setting. All three
# speak the same createTask / getTaskResult JSON API.
CAPTCHA_PROVIDERS = ("yescaptcha", "capsolver", "anticaptcha")
CAPTCHA_URLS = {"yescaptcha": "https://api.yescaptcha.com", "capsolver": "https://api.capsolver.com",
                "anticaptcha": "https://api.anti-captcha.com"}
CAPTCHA_KEY_VARS = {"yescaptcha": "FLOW_YESCAPTCHA_KEY", "capsolver": "FLOW_CAPSOLVER_KEY",
                    "anticaptcha": "FLOW_ANTICAPTCHA_KEY"}
YESCAPTCHA_TASK = "RecaptchaV3TaskProxylessM1"
# CapSolver (docs.capsolver.com, ReCaptchaV3): the Enterprise task solved through the proxy given in it, or proxyless
CAPSOLVER_TASK = "ReCaptchaV3EnterpriseTask"
CAPSOLVER_TASK_PROXYLESS = "ReCaptchaV3EnterpriseTaskProxyLess"
# Anti-Captcha (anti-captcha.com/apidoc, RecaptchaV3Enterprise): the v3 task with isEnterprise; it documents no proxy
# variant for v3, so this one is always minted from the solver's own network
ANTICAPTCHA_TASK = "RecaptchaV3TaskProxyless"
ANTICAPTCHA_SCORES = (0.3, 0.7, 0.9)        # its minScore queues


def captcha_sources(value) -> tuple[str, ...]:
    """`flow.captcha_provider`: one source, or a fallback order such as "capsolver,yescaptcha" (each tried in turn
    while the token's time lasts). Raises ValueError for anything else."""
    order = tuple(s.strip() for s in str(value).split(","))
    if any(s not in CAPTCHA_PROVIDERS for s in order) or len(set(order)) != len(order):
        raise ValueError("the token source is one of %s, or several of them in order, comma-separated, each once"
                         % ", ".join(CAPTCHA_PROVIDERS))
    return order
# A desktop Chrome, as the web app is used.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")

# ---- timing ---------------------------------------------------------------------------------------------------
API_TIMEOUT_S = 30              # one page load, read or cookie rotation over a proxy (reads took 0.3-1.5 s)
GENERATE_TIMEOUT_S = 150        # the image generate holds its reply until drawn (13-17 s, a 1:8 one 89 s); a video's
                                # answers in 2.6 s; an upload took 6-7 s
CAPTCHA_MIN_LEFT_S = 5          # a fallback token source is not started with less of the token's time left
SUBMIT_TIMEOUT_S = 900         # one submit (image or video), its spaced reCAPTCHA retries included
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
    FixedLimit("relay_flow.constants.CAPTCHA_MIN_LEFT_S", CAPTCHA_MIN_LEFT_S,
               "the next source of a captcha order is not started with less of the token's time left", "s"),
    FixedLimit("relay_flow.constants.SUBMIT_TIMEOUT_S", SUBMIT_TIMEOUT_S,
               "hard bound on one submit; no reCAPTCHA retry starts that would not end inside it", "s"),
    FixedLimit("relay_flow.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_flow.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_flow.constants.NETWORK_REST_S", NETWORK_REST_S,
               "a submit whose proxy could not reach flow rests the account this long", "s"),
)
