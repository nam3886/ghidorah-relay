"""muse spec constants: the site facts and the numbers that are not live settings.

The site facts come from the live spike of 26/10 (plans/muse-spike-261026.md, fixtures/muse/): kept here, in one
place, so a change on the site is one edit. Pure: imported by the spec, the credential reader and the engine alike.
"""

from relay_core.providers import FixedLimit

PROVIDER = "muse"

# ---- the site ---------------------------------------------------------------------------------------------------
SITE_URL = "https://muse.ai"
SITE_HOST = "muse.ai"
SESSION_COOKIE = "hatch_sess"         # the signed-in session; a jar without it is not an account
SESSION_PATH = "/api/session"         # {status: assigned, vm_id, endpoint_url, vm_state}; rotates hatch_vml
# A new account has no workspace yet: the session read says `none` (the site then leases one with {vmType:
# standard}, answered {status: assigned, gatewayUrl, vmName}, or 403 once the account has one). `unavailable`
# ({vm_resolution_issue: {kind: retryable | explicit_unavailable}}) is NOT that: muse cannot resolve the account's
# workspace from where the call comes from. Measured 27/10: the same session read `assigned` (SUSPENDED) through a
# Berlin proxy and `unavailable`/retryable through a Toronto one, while the owner's browser showed it connected; the
# site never leases on it (it retries a retryable one later). A lease there is refused (503 lease_rejected).
LEASE_PATH = "/api/hatch/lease-vm"
LEASE_BODY = {"vmType": "standard"}
NO_VM = ("none",)
UNAVAILABLE = "unavailable"
WAKE_PATH = "/api/hatch/vm/wake"      # {vm_id, retry_count, connect_attempt_id}: only when vm_state is not RUNNING
TOKEN_PATH = "/api/hatch/token"       # {vmAddress, vmName} -> {token, notary_token}; 403 without the Sec-Fetch-*
ASSIGNED = "assigned"
RUNNING = "RUNNING"
# What the web app sends on its own API calls: /api/hatch/token answers 403 {"error":"Forbidden"} without them.
SEC_FETCH = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
# The account's cloud VM is reached only through this load balancer, in a Noise_XX tunnel (HTTP-over-Noise).
NOISE_URL = "wss://hatch.metaaivm.com/v1/noise"
APP_ID = "hatch-web"
VM_HOST_SUFFIX = ".metaaivm.com"      # endpoint_url is wss://<vm_id>.metaaivm.com/ (no public DNS: never fetched)
# A result is fetched as GET /fs/raw/<dir><file> in the tunnel; the folders a result of each kind may come from.
# Images land in imagine_media; videos landed there on 26/10 and land in your_files since 30/10 (live job fba992d9: a
# your_files video judged refused and refunded). your_files holds a video only: an image there is not a result.
MEDIA_DIRS = {"image": ("workspace/imagine_media/",),
              "video": ("workspace/imagine_media/", "workspace/your_files/")}
# The Usage figure: a Next.js server action found in the site's JS by its export name (its id changes per deploy).
USAGE_ACTION = "fetchSubscriptionAction"
QUOTA_OK = "SUFFICIENT"

# ---- requests ---------------------------------------------------------------------------------------------------
PROMPT_MAX_CHARS = 4000            # not measured on muse: a cap well inside what a chat message takes
INPUT_MAX_BYTES = 8 * 2**20        # an input image is sent inline (base64) in the chat body; 0.5 MB measured
INPUT_MIMES = ("image/jpeg", "image/png", "image/webp")

# ---- timing -----------------------------------------------------------------------------------------------------
PROXY_REST_S = 1800                # a failed proxy probe rests every pool
CHECK_INTERVAL_S = 3600            # account check cadence: it reads the session (rotating hatch_vml) and the Usage
SUBMIT_TIMEOUT_MAX_S = 720         # the most a submit timeout setting may hold: the worker's stop grace is 780 s

# ---- attempts and strikes ---------------------------------------------------------------------------------------
MAX_ATTEMPTS = 3
SUSPECT_THRESHOLD = 3              # strikes before the account is dead (signed out)
STRIKE_DEAD_REASON = "signed_out"
SIGNED_OUT_REST_S = 600            # a signed-out read (/api/session 401) rests the account this long, then a recheck
SIGNED_OUT_GAP_S = 540             # a signed-out read this soon after the last counted strike adds none

LIMITS = (
    FixedLimit("relay_muse.constants.INPUT_MAX_BYTES", INPUT_MAX_BYTES,
               "the largest input image a muse video takes: it travels inline in the chat body (not measured "
               "above 0.5 MB)", "bytes"),
    FixedLimit("relay_muse.constants.SIGNED_OUT_REST_S", SIGNED_OUT_REST_S,
               "a signed-out read rests the account this long, then it is checked again", "s"),
    FixedLimit("relay_muse.constants.SIGNED_OUT_GAP_S", SIGNED_OUT_GAP_S,
               "a signed-out read this soon after the last counted strike adds none; shorter than the rest", "s"),
    FixedLimit("relay_muse.constants.SUBMIT_TIMEOUT_MAX_S", SUBMIT_TIMEOUT_MAX_S,
               "the most a submit timeout setting may hold: the worker's stop grace (780 s) covers it", "s"),
)
