"""`open_session`: the one way to open a browser for one piece of work on one account.

Proven 30/09. What it keeps and why:
- ONE persistent profile per (account id, credential generation), and NO Playwright route anywhere: any route makes
  Playwright switch Chrome's HTTP cache off, so every send downloaded the site's 10 MB of scripts again through the
  account's proxy. Telemetry blocking and response patches therefore go through CDP (`Network.setBlockedURLs`,
  `Fetch`), never through a route. Serving the site's own scripts through a route (`route.fulfill`) broke Send 5 of 5.
- The profile exists to keep the CACHE, not a session. `reset_profile_state` runs at open AND at close and keeps only
  `Default/Cache`, `Default/Code Cache` and `Default/Local Storage`: the site keeps drafts (text and attached images)
  in IndexedDB, and cookies, session storage and login data would carry one run into the next. The site's declared
  token keys are stripped from Local Storage with the site's scripts stopped first (a site that writes its token back
  on a timer would otherwise restore it); when that cannot be done the whole directory goes. A crash leaves a marker,
  and the next open then drops Local Storage whole because nothing could strip it.
- A proxy goes through the local `ProxyForwarder` (see forwarder.py), and WebRTC then sends no UDP outside it
  (`seed_proxy_preferences`): a page script cannot learn the box's address from an ICE candidate.
- A non-blocking flock on the profile; a run that finds it held gets a throwaway profile (slower, never a second Chrome
  inside the first one's profile).
- A launch that fails wipes the profile and tries once more; a failure after that is a `BrowserLaunchError`.
- Chrome gets an allow-list of environment variables (`chrome_env`), never the worker's whole environment: the worker
  holds the credential private key. A claim that fails or is cancelled releases its lock (`claim_profile_in_thread`).
- Chrome's own temp files go to a scratch directory made for the session and removed at its end (Linux; macOS Chrome
  ignores TMPDIR). Its background calls
  to Google (safe browsing keys, account list, updates, push check-in) are kept off the account's proxy: safe browsing
  by a seeded preference, the rest by the forwarder refusing those hosts (no launch flag does it, measured).
- It refuses to launch without the core's browser token: a caller waits for a token, it never opens a browser
  without one. The token is checked by what it carries (`released`, `purpose`), so the kit does not import the core.

patchright is imported only inside this package, and a browser starts only in this file (tests/guards.py).
"""

import asyncio
import base64
import contextlib
import fcntl
import fnmatch
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .evidence import EvidenceWriter
from .forwarder import ProxyForwarder, proxy_settings
from .net import NetRecorder
from .ui import keep_single_tab

log = logging.getLogger("relay_browser.session")

# The only things kept between runs, inside `Default/`: the HTTP cache (the site's scripts), compiled JS, and Local
# Storage (where the site keeps a 1.1 MB UI string table that the cache cannot hold: the server sends no-store).
KEEP_IN_DEFAULT = ("Cache", "Code Cache", "Local Storage")
OPEN_MARK = ".relay-open"
_ID_OK = re.compile(r"[A-Za-z0-9_-]{1,64}")      # used with fullmatch: `$` would let a trailing newline in
STRIP_TIMEOUT_S = 5.0
VERSION_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 30.0       # a Chrome that does not close in this time is killed


class BrowserRefused(RuntimeError):
    """open_session refused to launch (no usable browser token)."""


class BrowserLaunchError(RuntimeError):
    """Chrome would not start, also after the profile was wiped and the launch tried once more. `__cause__` holds
    the browser driver's own error."""


# Chrome's own services that no flag switches off (Google Chrome only): account list, component updates, push check-in,
# time and extension checks, safe browsing and optimization hints. The forwarder answers them 502 instead of passing
# them to the account's proxy. A site that really uses one of these hosts lists its own `refuse_hosts`.
CHROME_BACKGROUND_HOSTS = ("accounts.google.com", "update.googleapis.com", "android.clients.google.com",
                           "clients2.google.com", "clients4.google.com", "clientservices.googleapis.com",
                           "safebrowsing.googleapis.com", "optimizationguide-pa.googleapis.com")
# Seeded into the reset profile (Chrome reads it at start). Safe browsing otherwise fetches its keys from Google; search
# suggestions and network prediction otherwise open connections to the default search provider (www.google.com) at once.
SEED_PREFERENCES = {"safebrowsing": {"enabled": False, "enhanced": False}, "search": {"suggest_enabled": False},
                    "net": {"network_prediction_options": 2}}
# Added when the account has a proxy: WebRTC may send UDP only through a proxy, which an HTTP proxy never carries.
# Measured 23/10 on Chrome 154: the switch --force-webrtc-ip-handling-policy is ignored (a host candidate still came
# out); this preference is the one Chrome reads.
PROXY_PREFERENCES = {"webrtc": {"ip_handling_policy": "disable_non_proxied_udp"}}


# The Chrome the box runs: `CHROME_VERSION` in deploy/Dockerfile without its package revision (a test keeps them equal).
# A Mac run sets RELAY_CHROME_PATH to the same version: Chrome 155.0.8059.40 restarts its network service during a dola
# load, and the restart empties the cookie store, so the page reads as a guest.
PINNED_CHROME_VERSION = "154.0.8037.92"
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)\.(\d+)")


def chrome_version_warning(running: str, pinned: str = PINNED_CHROME_VERSION) -> str | None:
    """The text of a log warning when the running Chrome is not the pinned one, else None. Only the major version and
    the build number count: a patch level apart is the same build. A string that holds no version is a warning too."""
    got, want = _VERSION.search(running or ""), _VERSION.search(pinned)
    if want is None:
        raise ValueError("the pinned version %r holds no version" % pinned)
    if got is None:
        return "the browser version could not be read (%r); the pinned Chrome is %s" % ((running or "")[:60], pinned)
    if got.group(1) != want.group(1) or got.group(3) != want.group(3):
        return "the browser is Chrome %s but the pinned Chrome is %s (major or build differs)" % (got.group(0), pinned)
    return None


async def log_browser_version(ctx, page) -> None:
    """Log the running browser's version once per launch, and warn when it is not the pinned build. Never raises: a
    browser that cannot answer is no reason to fail the run. The CDP session is detached whatever happened."""
    cdp = None
    try:
        cdp = await asyncio.wait_for(ctx.new_cdp_session(page), VERSION_TIMEOUT_S)
        product = str((await asyncio.wait_for(cdp.send("Browser.getVersion"), VERSION_TIMEOUT_S)).get("product", ""))
    except Exception as exc:      # noqa: BLE001 - the version is information, not a precondition
        log.warning("could not read the browser version: %s", str(exc)[:80])
        return
    finally:
        if cdp is not None:
            with contextlib.suppress(Exception):
                await cdp.detach()
    log.info("browser %s", product)
    warning = chrome_version_warning(product)
    if warning:
        log.warning(warning)


@dataclass(frozen=True)
class SiteProfile:
    """What differs per site. A provider declares one; the kit holds no site knowledge."""
    name: str
    host: str                                  # a tab whose URL holds it is the site's tab (`keep_single_tab`)
    locale: str = "en-US"                      # pinned so phrase lists stay stable
    timezone: str = "Asia/Ho_Chi_Minh"
    viewport: tuple[int, int] | None = None    # (width, height); None = the browser default
    block_images: bool = True                  # engine flags, not requests: nothing for a request signature to see
    block_fonts: bool = True                   # `images=True` on open_session turns images back on
    telemetry_url_patterns: tuple[str, ...] = ()   # blocked through CDP `Network.setBlockedURLs`
    disk_cache_bytes: int = 150 * 1024 * 1024
    profile_max_age_s: float = 3 * 86400.0     # a profile nobody opened for this long is swept
    local_storage_token_keys: tuple[str, ...] = ()  # stripped from Local Storage at close
    capture_paths: tuple[str, ...] = ()        # NetRecorder: regexes over the URL path whose bodies are kept
    capture_ws_hosts: tuple[str, ...] = ()     # NetRecorder: WebSocket hosts whose frames are kept
    launch_timeout_ms: int = 60_000            # Chrome starts in seconds; Playwright's default 180 s is a hang
    channel: str = "chrome"                    # the pinned Google Chrome
    executable_path: str | None = None         # wins over `channel`; env RELAY_CHROME_PATH wins over both
    headless: bool | None = None               # None: env RELAY_BROWSER_HEADLESS=1 means headless, else headful (Xvfb)
    profile_root: str | None = None            # the profiles volume; env RELAY_PROFILE_ROOT; else a temp directory
    refuse_hosts: tuple[str, ...] = CHROME_BACKGROUND_HOSTS   # the proxy forwarder answers 502 for these hosts

    def __post_init__(self):
        if not self.name or not self.host:
            raise ValueError("a SiteProfile needs a name and a host")


@dataclass(frozen=True)
class ResponsePatch:
    """Change a response the page receives, through CDP `Fetch`, limited to `url_pattern`.

    `apply(body_text) -> (new_body_text, changed)`; only `changed` counts as a patch. The site signs requests over
    their body, so the response is what gets changed: the page then builds and signs its own request from it."""
    url_pattern: str
    apply: Callable[[str], tuple[str, bool]]


def check_token(token, purpose) -> None:
    """Refuse without a live browser token, and one for another purpose."""
    if token is None or getattr(token, "released", True):
        raise BrowserRefused("no live browser token: a browser opens only with one from the core's budget")
    held = getattr(token, "purpose", None)
    if held is not None and str(held) != str(purpose):
        raise BrowserRefused("the browser token is for %s, not %s" % (held, purpose))


# ---- profile directory ---------------------------------------------------------------------------------------------
def profile_root(site: SiteProfile) -> str:
    return site.profile_root or os.environ.get("RELAY_PROFILE_ROOT") or os.path.join(tempfile.gettempdir(),
                                                                                       "relay-profiles")


def profile_name(account) -> str:
    """One profile per (account id, credential generation): a new credential never inherits the old page state."""
    account_id = str(account.id)
    if not _ID_OK.fullmatch(account_id):
        raise ValueError("account id %r cannot name a profile directory" % account_id)
    return "%s-g%d" % (account_id, int(account.credential_gen))


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def reset_profile_state(profile: str, *, drop_local_storage: bool = False) -> None:
    """Remove every piece of page state from the profile; keep only the caches (`KEEP_IN_DEFAULT`).

    Call it while holding the profile's lock and with no Chrome on it. Also removes the `Singleton*` links a killed
    Chrome leaves (after a restart their pid may be another Chrome) and the open marker."""
    keep = tuple(k for k in KEEP_IN_DEFAULT if not (drop_local_storage and k == "Local Storage"))
    try:
        names = os.listdir(profile)
    except OSError:
        return
    for name in names:
        path = os.path.join(profile, name)
        if name == OPEN_MARK:
            continue
        if name == "Default" and os.path.isdir(path) and not os.path.islink(path):
            for sub in os.listdir(path):
                if sub not in keep:
                    _remove(os.path.join(path, sub))
        else:
            _remove(path)
    # Last: while the marker stands, a run that dies in the middle of this reset is still known to have left a
    # Local Storage that nobody stripped.
    _remove(os.path.join(profile, OPEN_MARK))


def lock_profile(path: str) -> int | None:
    """Take the profile exclusively (flock on `path.lock`, no waiting). The fd, or None when another run holds it.

    Our own lock, not Chrome's: two overlapping runs on one account would make the second Chrome hand its command to
    the first, and a stray window would appear in the middle of a send. Closing the fd releases it, also on a crash."""
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def unlock_profile(fd: int) -> None:
    with contextlib.suppress(OSError):
        os.close(fd)


def sweep_profiles(root: str, keep: str, max_age_s: float, now: float | None = None) -> int:
    """Delete profiles nobody opened for `max_age_s` (credential replaced, account deleted). Returns how many.
    Never `keep`, never a profile another run holds."""
    now = time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(root, name)
        if path == keep or not os.path.isdir(path):
            continue
        try:
            if now - os.path.getmtime(path) <= max_age_s:
                continue
        except OSError:
            continue
        fd = lock_profile(path)
        if fd is None:
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
            with contextlib.suppress(OSError):
                os.remove(path + ".lock")
            removed += 1
        finally:
            unlock_profile(fd)
    return removed


def seed_preferences(profile: str) -> None:
    """Write the preferences Chrome starts with (`SEED_PREFERENCES`); the next reset removes them with the rest."""
    os.makedirs(os.path.join(profile, "Default"), exist_ok=True)
    with open(os.path.join(profile, "Default", "Preferences"), "w", encoding="utf-8") as f:
        json.dump(SEED_PREFERENCES, f)


def seed_proxy_preferences(profile: str) -> None:
    """Add `PROXY_PREFERENCES` to the seeded preferences of an account with a proxy, so that everything goes through the
    proxy (the owner's rule): a page's RTCPeerConnection gathers no host or srflx candidate and STUN never leaves the
    box directly. Fingerprint effect: a page that probes WebRTC sees no local or public address at all, as with
    Chrome's own WebRtcIPHandling policy; the alternative is an address that differs from the proxy's exit, which gives
    the proxy away. An account with no proxy keeps the plain seed."""
    path = os.path.join(profile, "Default", "Preferences")
    with open(path, encoding="utf-8") as f:
        prefs = json.load(f)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prefs | PROXY_PREFERENCES, f)


def mark_open(profile: str) -> None:
    with open(os.path.join(profile, OPEN_MARK), "w", encoding="utf-8"):
        pass


@dataclass
class ProfileClaim:
    path: str
    lock: int | None          # None: the profile was held elsewhere and this one is a throwaway
    throwaway: bool


def claim_profile(root: str, name: str, site: SiteProfile) -> ProfileClaim:
    """Lock the account's profile and reset it, or (when it is held) make a throwaway one inside `root`. Whatever
    fails after the lock was taken releases it: a lock nobody holds a claim for would last as long as the worker."""
    os.makedirs(root, exist_ok=True)
    profile = os.path.join(root, name)
    lock = lock_profile(profile)
    try:
        if lock is None:
            log.info("another run holds this account's browser profile: using a throwaway profile")
            # a crash leaves the throwaway for the sweep
            claim = ProfileClaim(tempfile.mkdtemp(prefix="tmp-", dir=root), None, True)
            seed_preferences(claim.path)
        else:
            os.makedirs(profile, exist_ok=True)
            os.utime(profile)
            # An open marker still here means the last run died before its close-time reset, so tokens may still be in
            # Local Storage and nothing could strip them: drop it whole.
            crashed = os.path.exists(os.path.join(profile, OPEN_MARK))
            reset_profile_state(profile, drop_local_storage=crashed and bool(site.local_storage_token_keys))
            seed_preferences(profile)
            mark_open(profile)
            claim = ProfileClaim(profile, lock, False)
    except BaseException:
        if lock is not None:
            unlock_profile(lock)
        raise
    try:
        sweep_profiles(root, profile, site.profile_max_age_s)
    except Exception as exc:      # noqa: BLE001 - old profiles staying a day longer is not a reason to fail the run
        log.warning("profile sweep failed: %s", str(exc)[:100])
    return claim


async def claim_profile_in_thread(root: str, name: str, site: SiteProfile) -> ProfileClaim:
    """`claim_profile` off the loop, cancel-safe. The thread cannot be stopped, so when the caller is cancelled while
    it runs, the claim it returns is released the moment it finishes: nobody else holds it."""
    work = asyncio.ensure_future(asyncio.to_thread(claim_profile, root, name, site))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        work.add_done_callback(_release_unclaimed)
        raise


def _release_unclaimed(work: asyncio.Future) -> None:
    if work.cancelled() or work.exception() is not None:
        return
    try:
        release_profile(work.result())
    except Exception as exc:      # noqa: BLE001 - a callback has nobody to raise to
        log.warning("could not release a profile claimed by a cancelled run: %s", str(exc)[:100])


def release_profile(claim: ProfileClaim, *, drop_local_storage: bool = False) -> None:
    """After Chrome has exited: reset the kept profile again (a throwaway is deleted), then release the lock."""
    try:
        if claim.throwaway:
            shutil.rmtree(claim.path, ignore_errors=True)
        else:
            reset_profile_state(claim.path, drop_local_storage=drop_local_storage)
    finally:
        if claim.lock is not None:
            unlock_profile(claim.lock)


# ---- launch --------------------------------------------------------------------------------------------------------
def launch_args(site: SiteProfile, images: bool = False) -> list[str]:
    """Chrome flags for one run. `images=True` (attaching or reading images) turns raster images back on: the site's
    decode and upload gate runs only with images enabled (verified 16/09: images off, an attached image dies
    silently)."""
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run",
            "--disable-background-networking"]
    if site.block_images and not images:
        args.append("--blink-settings=imagesEnabled=false")
    if site.block_fonts:
        args.append("--disable-remote-fonts")
    return args + ["--disk-cache-size=%d" % site.disk_cache_bytes]


def is_headless(site: SiteProfile) -> bool:
    """The site's own `headless` wins; else `RELAY_BROWSER_HEADLESS=1`; else headful (the box runs Xvfb)."""
    if site.headless is not None:
        return site.headless
    return os.environ.get("RELAY_BROWSER_HEADLESS") == "1"


# What Chrome is given of the worker's environment: a name list, never the whole of it. The worker holds the credential
# private key, the database URL and the storage keys, and Chrome runs with `--no-sandbox` on third-party pages, so a
# renderer that is taken over could read them out of its own environment.
CHROME_ENV_NAMES = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TZ", "DISPLAY", "XAUTHORITY")
CHROME_ENV_PREFIXES = ("LC_", "XDG_", "FONTCONFIG_")


def chrome_env(tmpdir: str | None = None) -> dict[str, str]:
    """The environment Chrome starts with: the allow-listed names the worker has, and `TMPDIR` = the session's own
    directory (Chrome puts its component and singleton directories there on Linux; on macOS it ignores TMPDIR)."""
    env = {k: v for k, v in os.environ.items() if k in CHROME_ENV_NAMES or k.startswith(CHROME_ENV_PREFIXES)}
    if tmpdir:
        env["TMPDIR"] = tmpdir
    return env


def launch_kwargs(site: SiteProfile, proxy: dict | None, images: bool = False, tmpdir: str | None = None) -> dict:
    kw: dict = {"headless": is_headless(site), "env": chrome_env(tmpdir),
                "proxy": proxy, "args": launch_args(site, images), "locale": site.locale,
                "timezone_id": site.timezone, "timeout": site.launch_timeout_ms}
    exe = os.environ.get("RELAY_CHROME_PATH") or site.executable_path
    if exe:
        kw["executable_path"] = exe
    else:
        kw["channel"] = site.channel
    if site.viewport:
        kw["viewport"] = {"width": site.viewport[0], "height": site.viewport[1]}
    return kw


async def launch_with_retry(launch: Callable[[str], Awaitable], claim: ProfileClaim):
    """Launch on the claimed profile. A kept profile that will not open is wiped (we hold its lock) and opened once
    more from empty; a throwaway has nothing to wipe, so its failure is final. A final failure is a
    `BrowserLaunchError` (the driver's error is its `__cause__`), so callers catch the kit's type."""
    try:
        return await launch(claim.path)
    except Exception as exc:      # noqa: BLE001 - a damaged profile: we hold the lock, so it can be removed
        if claim.throwaway:
            raise BrowserLaunchError("the browser would not start: %s" % str(exc)[:300]) from exc
        log.warning("browser profile would not open (%s): starting it fresh", str(exc)[:100])
        await asyncio.to_thread(shutil.rmtree, claim.path, True)
        os.makedirs(claim.path, exist_ok=True)
        seed_preferences(claim.path)
        mark_open(claim.path)
        try:
            return await launch(claim.path)
        except Exception as again:      # noqa: BLE001
            raise BrowserLaunchError("the browser would not start: %s" % str(again)[:300]) from again


# ---- CDP response patches ------------------------------------------------------------------------------------------
class ResponsePatcher:
    """CDP `Fetch` at the Response stage for the declared URL patterns, with a counter of real changes.

    The page's request must never hang on us: on any error the response goes on unchanged (`continueRequest`)."""

    def __init__(self, patches: Sequence[ResponsePatch]):
        self._patches = tuple(patches)
        self.count = 0
        self._tasks: set[asyncio.Task] = set()

    async def install(self, cdp) -> None:
        """Never raises: without the patch the count stays 0 and the caller's own check stops the send."""
        if not self._patches:
            return
        try:
            cdp.on("Fetch.requestPaused", lambda ev: self._spawn(self._handle(cdp, ev)))
            await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": p.url_pattern, "requestStage": "Response"}
                                                         for p in self._patches]})
        except Exception as exc:      # noqa: BLE001
            log.warning("response patch not installed: %s", str(exc)[:80])

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)       # a task nobody holds can be collected midway
        task.add_done_callback(self._tasks.discard)

    def _match(self, url: str) -> ResponsePatch | None:
        return next((p for p in self._patches if fnmatch.fnmatchcase(url, p.url_pattern)), None)

    async def _handle(self, cdp, ev: dict) -> None:
        rid = ev["requestId"]
        try:
            patch = self._match((ev.get("request") or {}).get("url", ""))
            if patch is None or ev.get("responseErrorReason"):
                await cdp.send("Fetch.continueRequest", {"requestId": rid})
                return
            got = await cdp.send("Fetch.getResponseBody", {"requestId": rid})
            raw = base64.b64decode(got["body"]) if got.get("base64Encoded") else got["body"].encode("utf-8")
            body = raw.decode("utf-8")
            new_body, changed = patch.apply(body)
            # The body is now plain text: a stale length or encoding header would make the page misread it.
            headers = [h for h in ev.get("responseHeaders") or []
                       if h["name"].lower() not in ("content-length", "content-encoding")]
            await cdp.send("Fetch.fulfillRequest", {
                "requestId": rid, "responseCode": ev.get("responseStatusCode") or 200,
                "responseHeaders": headers, "body": base64.b64encode(new_body.encode("utf-8")).decode()})
            if changed:      # only now: a response the page never got is not a patch
                self.count += 1
        except Exception as exc:      # noqa: BLE001
            log.warning("response patch error: %s", str(exc)[:120])
            with contextlib.suppress(Exception):
                await cdp.send("Fetch.continueRequest", {"requestId": rid})


# ---- the session ---------------------------------------------------------------------------------------------------
class BrowserSession:
    """One open browser: `ctx`, the current `page`, the `recorder` and the `evidence` writer."""

    def __init__(self, site: SiteProfile, ctx, recorder: NetRecorder, evidence: EvidenceWriter,
                 patcher: ResponsePatcher, claim: ProfileClaim):
        self.site, self.ctx, self.recorder, self.evidence = site, ctx, recorder, evidence
        self._patcher, self._claim = patcher, claim
        self.page = None
        # False until the strip has succeeded: a close that is cut short (a cancel, an error) must drop Local Storage
        self.local_storage_clean = not site.local_storage_token_keys

    @property
    def patched(self) -> int:
        """How many responses were really changed (0 = the patch never applied)."""
        return self._patcher.count

    @property
    def throwaway(self) -> bool:
        return self._claim.throwaway

    @property
    def profile(self) -> str:
        return self._claim.path

    async def open_page(self):
        """The site's single tab: new tab first, old ones after (see ui.py), recorder attached, telemetry blocked and
        patches installed BEFORE the caller's `goto`, since the config the patch needs arrives while the page loads."""
        page = await keep_single_tab(self.ctx, self.site.host)
        self.recorder.attach(page)
        await self._prepare(page)
        self.page = page
        return page

    async def _prepare(self, page) -> None:
        try:
            cdp = await self.ctx.new_cdp_session(page)
        except Exception as exc:      # noqa: BLE001 - no patch then: the count stays 0 and the caller stops
            log.warning("devtools session unavailable: %s", str(exc)[:80])
            return
        if self.site.telemetry_url_patterns:
            try:
                await cdp.send("Network.enable")
                await cdp.send("Network.setBlockedURLs", {"urls": list(self.site.telemetry_url_patterns)})
            except Exception as exc:      # noqa: BLE001 - blocking telemetry is a nicety, never a reason to fail
                log.warning("telemetry block not installed: %s", str(exc)[:80])
        await self._patcher.install(cdp)

    async def _strip_local_storage(self) -> None:
        """Remove the declared token keys from the site's Local Storage, for good. First every tab of the site is
        frozen (CDP `Emulation.setScriptExecutionDisabled`: no timer, handler or promise of the site runs again), only
        then are the keys removed through the browser's own storage API; a site that writes its token back on a timer
        would otherwise put it on disk again before the close. When any step fails, or no tab of the site is open,
        `local_storage_clean` stays False and the close drops the whole directory."""
        keys = list(self.site.local_storage_token_keys)
        if not keys:
            return
        self.local_storage_clean = False
        pages = [p for p in list(self.ctx.pages) if self.site.host in p.url]
        if not pages:
            return
        try:
            frozen = []
            for page in pages:
                cdp = await asyncio.wait_for(self.ctx.new_cdp_session(page), STRIP_TIMEOUT_S)
                await asyncio.wait_for(cdp.send("Emulation.setScriptExecutionDisabled", {"value": True}),
                                       STRIP_TIMEOUT_S)
                frozen.append((page, cdp))
            for page, cdp in frozen:
                parts = urlsplit(page.url)
                storage = {"securityOrigin": "%s://%s" % (parts.scheme, parts.netloc), "isLocalStorage": True}
                for key in keys:
                    await asyncio.wait_for(cdp.send("DOMStorage.removeDOMStorageItem",
                                                    {"storageId": storage, "key": key}), STRIP_TIMEOUT_S)
        except Exception as exc:      # noqa: BLE001 - the default applies: Local Storage goes whole
            log.warning("could not strip local storage keys: %s", str(exc)[:80])
            return
        self.local_storage_clean = True

    async def _shutdown(self) -> None:
        try:
            await self._strip_local_storage()
        finally:
            self.recorder.close()
            try:
                await asyncio.wait_for(self.ctx.close(), CLOSE_TIMEOUT_S)
            except Exception as exc:      # noqa: BLE001 - a hung or failed close must not stop the rest of the cleanup
                log.warning("browser did not close (%s): killing it", type(exc).__name__)
                # a killed Chrome may not have written the strip to disk: the close reset drops Local Storage whole
                self.local_storage_clean = False
                _kill_browser(self._claim.path)


def _kill_browser(profile: str) -> None:
    """SIGKILL the Chrome that owns `profile`: its `SingletonLock` link is `host-pid`."""
    with contextlib.suppress(OSError, ValueError):
        pid = int(os.readlink(os.path.join(profile, "SingletonLock")).rsplit("-", 1)[1])
        os.kill(pid, signal.SIGKILL)


@contextlib.asynccontextmanager
async def open_session(site: SiteProfile, account, *, purpose, token, images: bool = False,
                       response_patches: Sequence[ResponsePatch] = (), redact: Callable[[str], str]):
    """Open Chrome for `account` and yield a `BrowserSession`; everything is undone on exit.

    `account` is read for `id`, `credential_gen` and `proxy` (`host`, `port`, `auth`), `token` is the core's browser
    token, `redact` is the caller's text redactor for evidence and network dumps (required: nothing unredacted is ever
    written). Order: token, profile (lock or throwaway, reset), proxy forwarder, launch; on exit: stop the site's
    scripts and strip the declared Local Storage keys, close the browser, stop the forwarder, reset the profile again,
    release the lock."""
    check_token(token, purpose)
    if not callable(redact):
        raise TypeError("open_session needs `redact`, a callable that redacts text: nothing unredacted is ever written")
    name = profile_name(account)
    from patchright.async_api import async_playwright  # late: the checks above need no browser

    claim = await claim_profile_in_thread(profile_root(site), name, site)
    fwd = None
    scratch = None
    # Until the strip has succeeded Local Storage is dropped whole, whatever cuts the close short.
    drop_storage = bool(site.local_storage_token_keys)
    try:
        scratch = tempfile.mkdtemp(prefix="relay-chrome-")      # short path: Chrome puts a unix socket in it
        # everything that can fail on a bad declaration (a capture regex) is built before Chrome starts
        recorder = NetRecorder(site.capture_paths, site.capture_ws_hosts)
        patcher = ResponsePatcher(response_patches)
        proxy = proxy_settings(getattr(account, "proxy", None))
        fwd = ProxyForwarder(proxy, site.refuse_hosts) if proxy else None
        kw = launch_kwargs(site, await fwd.start() if fwd else proxy, images, scratch)
        async with async_playwright() as pw:
            async def launch(path: str):
                if proxy:       # here, so that a profile wiped and seeded again by the retry gets it too
                    seed_proxy_preferences(path)
                return await pw.chromium.launch_persistent_context(path, **kw)

            ctx = await launch_with_retry(launch, claim)
            session = BrowserSession(site, ctx, recorder, EvidenceWriter(redact, recorder), patcher, claim)
            try:
                await session.open_page()
                await log_browser_version(ctx, session.page)
                yield session
            finally:
                await session._shutdown()
                drop_storage = not session.local_storage_clean
    finally:
        if fwd is not None:
            fwd.close()
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        await asyncio.to_thread(release_profile, claim, drop_local_storage=drop_storage)
