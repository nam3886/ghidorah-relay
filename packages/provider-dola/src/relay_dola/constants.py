"""dola spec constants (contracts 5.4.1): the numbers that were v4 module constants and are not live settings.

Each one is fixed for a measured reason, named in its comment. Pure: imported by the spec, the classifier and the
engine alike.
"""

PROVIDER = "dola"

# ---- timing ---------------------------------------------------------------------------------------------------
POLL_INTERVAL_S = 30            # next poll after a Pending; a render takes minutes, so a faster loop only adds reads
DURATION_TOLERANCE_S = 2        # a video within this many seconds of the asked length is on target (v4 runner)
PENDING_GRACE_S = 20 * 60       # a conversation with several videos: wait this long after the first finished one
QUIET_GRACE_S = 2700            # dola silent this long with no order and nothing pending: nothing is coming
THROTTLE_COOLDOWN_S = 1800      # throttle (710022002/4) and region/proxy failures: the account rests this long
PROXY_REST_S = 1800             # a failed proxy probe rests every pool (D44; v4 PROXY_DOWN_COOLDOWN_S)
CHECK_INTERVAL_S = 6 * 3600     # account check cadence (v4 CHECK_EVERY_S)
CATALOG_REFRESH_S = 3600        # the worker refreshes the templates and styles snapshots this often
SUBMIT_TIMEOUT_VIDEO_S = 720    # hard bound on one video submit; re-measured on the box in phase 7
SUBMIT_TIMEOUT_IMAGE_S = 300    # hard bound on one image submit (the session waits for the image URLs)

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3                # counted attempts before a job fails (v4 job attempts)
SUSPECT_THRESHOLD = 3           # strikes before the account is dead (v4 DEAD_AFTER_FAILURES)
STRIKE_DEAD_REASON = "signed_out"

# ---- money ----------------------------------------------------------------------------------------------------
FALLBACK_VIDEO_PRICE = 3        # a video tier missing from `dola.prices`: dearest measured plain tier, + a warning
FALLBACK_IMAGE_PRICE = 1        # an image model missing from `dola.prices`
IMAGE_ACCOUNT_COST = 1          # dola counts the image ceiling per JOB, whatever the model (v4 wallet_cost, R20)

# ---- requests -------------------------------------------------------------------------------------------------
MAX_REFERENCE_IMAGES = 10       # the composer takes 10 and silently drops the rest (spike 17/09)
PROMPT_HINT_MAX = 500           # `dola.video_prompt_hint` length cap
BROWSER_SLOTS_MIN, BROWSER_SLOTS_MAX = 1, 30       # 30 sessions at once peaked ~25 of 32 cores (measured 25/09)

# ---- events ---------------------------------------------------------------------------------------------------
# The core stores event text through `relay_core.redact.trim_log_value`, which turns any string longer than its
# `LOG_VALUE_LIMIT` (2000) into "[omitted len=N]". The upstream text a poll reports stays within it, or the reaper
# would read the marker instead of dola's last words.
EVENT_TEXT_MAX = 2000

# ---- credential -----------------------------------------------------------------------------------------------
IDENTITY_COOKIES = ("sessionid", "uid_tt")         # `sessionid` is the session, `uid_tt` the user
SESSION_COOKIE = "sessionid"                       # a credential without it is refused at import
COOKIE_DOMAIN = ".dola.com"                        # for a bare `Cookie:` header string, which carries no domain
