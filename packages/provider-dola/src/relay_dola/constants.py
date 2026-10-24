"""dola spec constants: the numbers that are not live settings.

Each one is fixed for a measured reason, named in its comment. Pure: imported by the spec, the classifier and the
engine alike.
"""

from relay_core.providers import FixedLimit
from relay_core.redact import LOG_VALUE_LIMIT

PROVIDER = "dola"

# ---- timing ---------------------------------------------------------------------------------------------------
POLL_INTERVAL_S = 30            # next poll after a Pending; a render takes minutes, so a faster loop only adds reads
DURATION_TOLERANCE_S = 2        # a video within this many seconds of the asked length is on target
PENDING_GRACE_S = 20 * 60       # a conversation with several videos: wait this long after the first finished one
QUIET_GRACE_S = 2700            # dola silent this long with no order and nothing pending: nothing is coming
THROTTLE_COOLDOWN_S = 1800      # throttle (710022004), a 710022002 refusal, region/proxy failures: the account rests
PROXY_REST_S = 1800             # a failed proxy probe rests every pool
BROWSER_FAULT_FREE_RETRIES = 3   # browser-fault retries of one job that use no attempt; the next ones count one (so
#                                  max_attempts ends a job that keeps meeting the fault)
BROWSER_FAULT_COOLDOWN_S = 60   # the account rests this long after the browser lost its cookies (not a guest read)
CHECK_INTERVAL_S = 6 * 3600     # account check cadence
CATALOG_REFRESH_S = 3600        # the worker refreshes the templates and styles snapshots this often
SUBMIT_TIMEOUT_VIDEO_S = 720    # hard bound on one video submit; re-measured on the box
SUBMIT_TIMEOUT_IMAGE_S = 300    # hard bound on one image submit (the session waits for the image URLs)

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails
SUSPECT_THRESHOLD = 3           # strikes before the account is dead
LOGIN_DOUBT_REST_S = 600        # a login doubt rests the account this long, then it is checked again
LOGIN_DOUBT_GAP_S = 540         # a doubt less than this long after the last counted strike adds none; shorter than
#                                 the rest, so a slow close or a clock skew cannot put the next read inside the gap
STRIKE_DEAD_REASON = "signed_out"

# ---- requests -------------------------------------------------------------------------------------------------
MAX_REFERENCE_IMAGES = 10       # the composer takes 10 and silently drops the rest (spike 17/09)
PROMPT_MAX_CHARS = 200_000      # longest prompt a customer may send; edge measured 12/10: 250,000 kept, 265,625 dropped
FRAME_MAX_SENT_CHARS = 200_000  # longest rendered text dola is sent (same measured edge)
BROWSER_SLOTS_MIN, BROWSER_SLOTS_MAX = 1, 12       # sized to the dola container: 8 CPUs, 12 GiB, ~0.5 GiB a session

# ---- events ---------------------------------------------------------------------------------------------------
# The core stores event text through `relay_core.redact.trim_log_value`, which turns any string longer than its
# `LOG_VALUE_LIMIT` into "[omitted len=N]". The upstream text a poll reports stays within it, or the reaper would read
# the marker instead of dola's last words: the cap is the core's own constant, not a copy of it.
EVENT_TEXT_MAX = LOG_VALUE_LIMIT

# ---- credential -----------------------------------------------------------------------------------------------
IDENTITY_COOKIES = ("sessionid", "uid_tt")         # `sessionid` is the session, `uid_tt` the user
SESSION_COOKIE = "sessionid"                       # a credential without it is refused at import
COOKIE_DOMAIN = ".dola.com"                        # for a bare `Cookie:` header string, which carries no domain

# ---- the Fixed limits card ------------------------------------------------------------------------------------
# What the admin sees for the numbers above that are not live settings. Each value is the constant itself. The numbers
# `Caps` already carries (proxy rest, check interval, catalog refresh, attempts, strikes, prompt and frame size) are
# listed by the core from the spec, so they are not repeated here.
LIMITS = (
    FixedLimit("relay_dola.constants.POLL_INTERVAL_S", POLL_INTERVAL_S,
               "a render takes minutes, so a faster loop only adds reads", "s"),
    FixedLimit("relay_dola.constants.DURATION_TOLERANCE_S", DURATION_TOLERANCE_S,
               "a video this close to the asked length counts as on target", "s"),
    FixedLimit("relay_dola.constants.PENDING_GRACE_S", PENDING_GRACE_S,
               "with several videos in one conversation, wait this long after the first one finished", "s"),
    FixedLimit("relay_dola.constants.QUIET_GRACE_S", QUIET_GRACE_S,
               "dola silent this long with no order and nothing pending: nothing is coming", "s"),
    FixedLimit("relay_dola.constants.THROTTLE_COOLDOWN_S", THROTTLE_COOLDOWN_S,
               "after a throttle or a region failure the account rests this long", "s"),
    FixedLimit("relay_dola.constants.BROWSER_FAULT_FREE_RETRIES", BROWSER_FAULT_FREE_RETRIES,
               "browser-fault retries of one job that use no attempt; the next ones count", "retries"),
    FixedLimit("relay_dola.constants.BROWSER_FAULT_COOLDOWN_S", BROWSER_FAULT_COOLDOWN_S,
               "the account rests this long after the browser lost its cookies", "s"),
    FixedLimit("relay_dola.constants.SUBMIT_TIMEOUT_VIDEO_S", SUBMIT_TIMEOUT_VIDEO_S,
               "hard bound on one video submit", "s"),
    FixedLimit("relay_dola.constants.SUBMIT_TIMEOUT_IMAGE_S", SUBMIT_TIMEOUT_IMAGE_S,
               "hard bound on one image submit", "s"),
    FixedLimit("relay_dola.constants.LOGIN_DOUBT_REST_S", LOGIN_DOUBT_REST_S,
               "a login doubt rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_dola.constants.LOGIN_DOUBT_GAP_S", LOGIN_DOUBT_GAP_S,
               "a doubt this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_dola.constants.MAX_REFERENCE_IMAGES", MAX_REFERENCE_IMAGES,
               "the composer takes this many and silently drops the rest", "images"),
    FixedLimit("relay_dola.constants.BROWSER_SLOTS_MIN", BROWSER_SLOTS_MIN,
               "least browser sessions dola.browser_slots may hold", "sessions"),
    FixedLimit("relay_dola.constants.BROWSER_SLOTS_MAX", BROWSER_SLOTS_MAX,
               "most browser sessions dola.browser_slots may hold: sized to the dola container (8 CPUs, 12 GiB)",
               "sessions"),
)
