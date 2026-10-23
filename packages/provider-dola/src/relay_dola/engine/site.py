"""dola's `SiteProfile` for the browser kit, and the response patch that unlocks 30-second videos.

Everything the kit must know about the site lives here (the kit holds no site knowledge): the host, the pinned
locale, the viewport the image chip row needs, the telemetry to block, the paths the recorder keeps, and the one
response the provider patches.
"""

from relay_browser.session import CHROME_BACKGROUND_HOSTS, ResponsePatch, SiteProfile
from relay_dola.engine.config_patch import CONFIG_URL_PART, patch_duration_options

BASE = "https://www.dola.com"
CHAT_URL = BASE + "/chat"
# dola sends an account whose proxy is in the wrong region to this URL; the app never loads there. The URL is the only
# sign: the recent-conversations API keeps answering, so a login probe does not see it (measured 260914).
REGION_BLOCK_MARK = "region-restricted"

# ByteDance's monitoring and telemetry hosts, blocked by name. Never dola.com, its video store or its image store.
TELEMETRY_URL_PATTERNS = ("*mon-sg.byteintlapi.com*", "*mcs.snssdk.com*", "*toblog.ctobsnssdk.com*",
                          "*/monitor_browser/*", "*slardar*")

# The recorder keeps these bodies: `/im/chain/single` carries the conversation and its replies (and the request names
# the conversation id); `/chat/completion` carries no id and no reply, but its SSE body holds dola's `error_code`
# (710022002 a refused order, 710022003 region, 710022004 rate limit). Regular expressions over the path.
CAPTURE_PATHS = (r"^/im/chain/single$", r"^/chat/completion$")

# Which Local Storage keys of dola.com identify the account or hold a token (the kit strips them before the browser
# closes; the 1.1 MB UI string table stays cached). Measured live 05/10 on a signed-in account: one origin only
# (https://www.dola.com; no frame of another origin), the session itself is a cookie (`sessionid`, deleted at close),
# and these four are the account-bound values Local Storage holds: the analytics token cache, the sec user id
# (a token-like string of 76 chars), the web id and the numeric user id.
# The rest is UI state (theme, language, banner, string table).
LOCAL_STORAGE_TOKEN_KEYS: tuple[str, ...] = ("__tea_cache_tokens_495671", "for_ban_appeal_sec_user_id",
                                              "samantha_web_web_id", "flow_tea_user_id")

# Hosts dola's pages never use but Chrome may still reach, added to the kit's list of Chrome background hosts that the
# proxy forwarder refuses. Measured live 05/10 on three accounts: a /chat load contacts only dola.com, ciciai.com,
# bytedapm.com, ttwstatic.com, byteoversea.com and zijieapi.com hosts, never `www.google.com` (dola uses its own
# captcha), so the kit's default exception for reCAPTCHA sites is closed here.
DOLA_REFUSE_HOSTS: tuple[str, ...] = ("www.google.com",)

# The image composer's chip row (Create Images / Model / Ratio / Style / Templates) renders only at desktop width
# (verified 260917: 1440x900 shows it, narrower hides it). Video uses the same viewport so one profile serves both.
VIEWPORT = (1440, 900)

DOLA_SITE = SiteProfile(
    name="dola",
    host="dola.com",
    locale="en-US",
    timezone="Asia/Ho_Chi_Minh",
    viewport=VIEWPORT,
    telemetry_url_patterns=TELEMETRY_URL_PATTERNS,
    local_storage_token_keys=LOCAL_STORAGE_TOKEN_KEYS,
    capture_paths=CAPTURE_PATHS,
    refuse_hosts=(*CHROME_BACKGROUND_HOSTS, *DOLA_REFUSE_HOSTS),
)


def duration_response_patch(seconds: int) -> ResponsePatch:
    """The CDP `Fetch` patch for the action-bar config: add the `seconds` level to the duration selector.

    The kit counts a body as patched only when `changed` is true AND the patched body reached the page
    (`session.patched`): 0 patches after the composer is built means the level does not exist, and the send must
    not go on."""
    return ResponsePatch("*%s*" % CONFIG_URL_PART, lambda body: patch_duration_options(body, seconds))
