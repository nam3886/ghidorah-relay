"""vibes spec constants: the site facts and the numbers that are not live settings.

vibes.ai is Meta's video and image product behind a plain JSON API (`https://vibes.ai/api/*`): no browser and no
captcha. The site facts (URL, paths, cookie names, headers) are kept here, in one place, so a change on the site is one
edit. Pure: imported by the spec, the credential reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "vibes"

# ---- the site ---------------------------------------------------------------------------------------------------
BASE_URL = "https://vibes.ai"
SITE_HOST = "vibes.ai"                 # the session cookie must be for this host (or a subdomain)
SESSION_COOKIE = "meta_session"        # the one cookie that signs the account in
ACK_COOKIE = "cookie_ack=true"         # the consent cookie the web app always sends next to the session
# A desktop Chrome, as the web app is used; the API answers the same to any client, this only keeps the traffic plain.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
ME_PATH = "/api/auth/me"               # {user: {id, username, accountStatus}}; 401/403 when the session is gone
QUOTA_PATH = "/api/quota/upsell"       # {upsell: null} while quota is left; a banner with `bannerStatus` when used up
ACTIVE_STATUS = "ACTIVE"
QUOTA_USED_BANNER = "enabled"          # `bannerStatus` (any case) of the banner shown once the quota is used up

# ---- timing ---------------------------------------------------------------------------------------------------
API_TIMEOUT_S = 30              # one API read of the account check, over a proxy
PROXY_REST_S = 1800             # a failed proxy probe rests every pool
CHECK_INTERVAL_S = 6 * 3600     # account check cadence
SIGNED_OUT_REST_S = 600         # a signed-out read rests the account this long, then it is checked again
SIGNED_OUT_GAP_S = 540          # a signed-out read less than this long after the last counted strike adds none;
#                                 shorter than the rest, so the recheck after a rest always counts
SUBMIT_TIMEOUT_IMAGE_S = 180    # an image is drawn inside the request (about 10 s)
SUBMIT_TIMEOUT_VIDEO_S = 120    # a video submit only starts the render (a few seconds); the render is polled

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails
SUSPECT_THRESHOLD = 3           # strikes before the account is dead
STRIKE_DEAD_REASON = "signed_out"

# ---- money ----------------------------------------------------------------------------------------------------
FALLBACK_PRICE = 1              # a model missing from `vibes.prices`, charged with a warning
ACCOUNT_COST = 1                # vibes' quota is not measured yet: each job counts one

# ---- requests -------------------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000         # not measured on vibes yet: a cap well inside what a generate request takes

# ---- the Fixed limits card ------------------------------------------------------------------------------------
# The numbers `Caps` already carries (proxy rest, check interval, attempts, strikes, prompt size) are listed by the
# core from the spec, so they are not repeated here.
LIMITS = (
    FixedLimit("relay_vibes.constants.API_TIMEOUT_S", API_TIMEOUT_S,
               "one API read of the account check over a proxy; none in time is a failed check", "s"),
    FixedLimit("relay_vibes.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_vibes.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_vibes.constants.SUBMIT_TIMEOUT_IMAGE_S", SUBMIT_TIMEOUT_IMAGE_S,
               "hard bound on one image submit: vibes draws the image inside the request (about 10 s)", "s"),
    FixedLimit("relay_vibes.constants.SUBMIT_TIMEOUT_VIDEO_S", SUBMIT_TIMEOUT_VIDEO_S,
               "hard bound on one video submit: it only starts the render, which is polled afterwards", "s"),
    FixedLimit("relay_vibes.constants.FALLBACK_PRICE", FALLBACK_PRICE,
               "charged for a model missing from vibes.prices", "points"),
    FixedLimit("relay_vibes.constants.ACCOUNT_COST", ACCOUNT_COST,
               "vibes' quota is not measured yet: each job counts one against the daily budget", "per job"),
)
