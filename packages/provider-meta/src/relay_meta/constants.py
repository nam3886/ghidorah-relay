"""meta spec constants: the site facts and the numbers that are not live settings.

The site facts (URL, selectors, cookie names) come from a reference study of meta.ai and are kept here, in one place,
so a change on the site is one edit. Pure: imported by the spec, the credential reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "meta"

# ---- the site ---------------------------------------------------------------------------------------------------
SITE_URL = "https://www.meta.ai/"
SITE_HOST = "meta.ai"                              # a page whose host is not this one (or a subdomain) is not signed in
COOKIE_DOMAIN = ".meta.ai"                         # for a bare `Cookie:` header string, which carries no domain
SESSION_COOKIES = ("ecto_1_sess", "abra_sess")     # a jar needs one of them; real jars carry `ecto_1_sess` alone

SEL_USER_MENU = '[data-testid="user-menu-button"]'           # visible only when signed in; carries the display name
SEL_LOGGED_OUT = ('[data-testid="login-button"]', '[data-testid="logged-out-signin-card-dismiss-button"]')
# The composer; the prehydration copy rendered before the app hydrates takes no input.
SEL_COMPOSER = ('[data-testid="composer-input"][contenteditable="true"]'
                ':not([data-ecto-composer-prehydration-input])')
LOGGED_OUT_URL_MARKS = ("/login", "checkpoint")   # meta sends a signed-out or blocked session to these URLs
# Vercel's browser checkpoint keeps the URL `https://www.meta.ai/`: only the page tells it. While the challenge runs the
# title is already this one (every cold load answers 403 first and passes a second later), so the title alone is not
# a verdict until the read gives up; the failure sentence is one at once.
CHECKPOINT_TITLE = "Vercel Security Checkpoint"
CHECKPOINT_FAILED_TEXT = "Failed to verify your browser"
CHECKPOINT_DEAD_REASON = "checkpoint"
SEL_ASSISTANT = '[data-testid="assistant-message"]'      # `data-streaming-state` INIT -> STREAMING -> DONE
SEL_IMAGE_TILE = "ur-image-tile"                         # the `data-testid` of a generated image in the answer
SEL_FILE_INPUT = 'input[type="file"]'                    # the composer's one file input (the image to animate)
# one per image attached in the composer (a blob: thumbnail inside the Lexical editor)
SEL_ATTACHED = SEL_COMPOSER + " span[data-inline-image-drag-handle]"
UPLOAD_PATH = "/gen_ai_document_gen_ai_tenant/"          # rupload.meta.ai: the POST answers `{media_id}` once uploaded
CHAT_PATH = "/prompt/"                                   # `/prompt/<conversation uuid>` after Enter
GRAPHQL_PATH = "/api/graphql"
MEDIA_HOSTS = ("fbcdn.net",)                             # the final images: `scontent.xx.fbcdn.net/v/t39.105495-1/...`
PREVIEW_MARK = "/t58.90637-"                             # the low-res preview that comes first; never delivered

# ---- timing ---------------------------------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT_S = 60        # the first load of the app over a proxy
CHECK_SIGNAL_TIMEOUT_S = 30     # how long an account check waits for a signed-in or signed-out sign after the load
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
CHECKPOINTS_TO_OWNER = 3        # checkpoint reads in a row (rechecks after each rest) before the account is dead

# ---- money ----------------------------------------------------------------------------------------------------
FALLBACK_PRICE = 1              # a model missing from `meta.prices`, charged with a warning
ACCOUNT_COST = 1                # meta's daily limits count jobs (one chat each), whatever the model

# ---- requests -------------------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000         # not measured on meta's composer yet: a cap well inside what a chat message takes
BROWSER_SLOTS_MIN, BROWSER_SLOTS_MAX = 1, 6       # sized to the meta container: 4 CPUs, 6 GiB, ~0.5 GiB a session

# ---- the Fixed limits card ------------------------------------------------------------------------------------
# The numbers `Caps` already carries (proxy rest, check interval, attempts, strikes, prompt size) are listed by the
# core from the spec, so they are not repeated here.
LIMITS = (
    FixedLimit("relay_meta.constants.PAGE_LOAD_TIMEOUT_S", PAGE_LOAD_TIMEOUT_S,
               "the first load of meta's app over a proxy", "s"),
    FixedLimit("relay_meta.constants.CHECK_SIGNAL_TIMEOUT_S", CHECK_SIGNAL_TIMEOUT_S,
               "an account check waits this long for a signed-in or signed-out sign; none is an unknown read", "s"),
    FixedLimit("relay_meta.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_meta.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_meta.constants.CHECKPOINTS_TO_OWNER", CHECKPOINTS_TO_OWNER,
               "browser checkpoint reads in a row, each after a rest, before the account is dead (reason checkpoint)",
               "reads"),
    FixedLimit("relay_meta.constants.SUBMIT_TIMEOUT_MAX_S", SUBMIT_TIMEOUT_MAX_S,
               "the most a submit timeout setting may hold: the worker's stop grace (780 s) covers it", "s"),
    FixedLimit("relay_meta.constants.FALLBACK_PRICE", FALLBACK_PRICE,
               "charged for a model missing from meta.prices", "points"),
    FixedLimit("relay_meta.constants.ACCOUNT_COST", ACCOUNT_COST,
               "meta counts its daily limits per job, whatever the model", "per job"),
    FixedLimit("relay_meta.constants.BROWSER_SLOTS_MIN", BROWSER_SLOTS_MIN,
               "least browser sessions meta.browser_slots may hold", "sessions"),
    FixedLimit("relay_meta.constants.BROWSER_SLOTS_MAX", BROWSER_SLOTS_MAX,
               "most browser sessions meta.browser_slots may hold: sized to the meta container (4 CPUs, 6 GiB)",
               "sessions"),
)
