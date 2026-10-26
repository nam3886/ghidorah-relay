"""HTTP to muse.ai over curl_cffi (Chrome fingerprint), bound to one account's cookies and proxy, and the bootstrap
that opens the tunnel to its VM.

Every call of a run (the site's API, the WebSocket to the VM's load balancer, the Usage action) goes through the
account's proxy; with no proxy on the account it goes direct, and a failed proxy is a failed call, never a direct
retry. No call follows a redirect: the cookies must not travel to wherever an answer points. muse rotates `hatch_vml`
(and may rotate others) on `/api/session`; the run writes what it set back to the account (`keep_rotated`) unless a
call of the run was answered as signed out. The session seam is curl_cffi's `AsyncSession` (or a fake with the same
`get`, `post`, `ws_connect`, `cookies` and `close`).
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode, urlsplit

from relay_core.providers import Proxy, epoch_time
from relay_muse.constants import (
    APP_ID,
    ASSIGNED,
    NOISE_URL,
    QUOTA_OK,
    RUNNING,
    SEC_FETCH,
    SESSION_PATH,
    SITE_HOST,
    SITE_URL,
    TOKEN_PATH,
    USAGE_ACTION,
    VM_HOST_SUFFIX,
    WAKE_PATH,
)
from relay_muse.credential import jar_of, on_site, renewable, with_cookies

log = logging.getLogger("relay_muse.web")
API_TIMEOUT_S = 30          # one HTTP call
WS_TIMEOUT_S = 30           # the WebSocket upgrade
RENEW_TIMEOUT_S = 5.0       # writing rotated cookies back: past it nothing is written
USAGE_BUDGET_S = 60         # the whole Usage read, the hunt for its action id included
SCAN_MAX_SCRIPTS = 400      # site scripts read at most while hunting the action id (the site loads about 190)
SCAN_BATCH = 8              # scripts read at once
_VM_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


class NetError(Exception):
    """A call that got no answer. Only the error's kind is kept: curl's text may hold the proxy's login."""

    def __init__(self, kind: str, proxy: bool):
        super().__init__("muse.ai could not be reached (%s)" % kind)
        self.proxy = proxy


class SignedOut(Exception):
    """muse answered the session read 401: the account's session is gone."""


class SiteError(Exception):
    """muse answered a step before the tunnel with something else than its answer."""


class VmNotReady(Exception):
    """The account has no running workspace (none assigned, or it did not wake in time)."""


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port`; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


def _proxy_failure(exc: BaseException) -> bool:
    return any(c.__name__ == "ProxyError" for c in type(exc).__mro__)


def real_session(proxies: dict[str, str] | None):
    from curl_cffi import AsyncSession  # imported on first real use: importing the engine stays offline
    return AsyncSession(impersonate="chrome", proxies=proxies, timeout=API_TIMEOUT_S)


class _Socket:
    """curl_cffi's async WebSocket as the tunnel's `Socket`."""

    def __init__(self, ws):
        self.ws = ws

    async def send(self, data: bytes) -> None:
        await self.ws.send(data)

    async def recv(self) -> bytes:
        data, _flags = await self.ws.recv()
        return data

    async def close(self) -> None:
        await self.ws.close()


class Web:
    """One run's HTTP to muse.ai. `async with`: on a clean exit with a run's `ctx`, rotated cookies are kept."""

    def __init__(self, account, ctx=None, session=None,
                 make_session: Callable[[dict[str, str] | None], Any] = real_session):
        self.ctx, self.start = ctx, jar_of(account.credential)
        url = proxy_url(account.proxy)
        self.proxies = {"http": url, "https": url} if url else None
        self.s = session if session is not None else make_session(self.proxies)
        for name, value in self.start.items():
            self.s.cookies.set(name, value, domain=SITE_HOST)
        self.signed_out = False

    async def __aenter__(self) -> "Web":
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.ctx is not None and exc[0] is None:
                await keep_rotated(self.ctx, self)
        finally:
            try:
                await self.s.close()
            except Exception:      # noqa: BLE001 - a session that will not close is dropped
                pass

    async def _call(self, method: str, path: str, headers: dict[str, str], **kw):
        try:
            return await getattr(self.s, method)(SITE_URL + path, headers={"Origin": SITE_URL,
                                                                          "Referer": SITE_URL + "/", **headers},
                                                 timeout=API_TIMEOUT_S, allow_redirects=False, **kw)
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None

    async def get(self, path: str, accept: str = "application/json"):
        return await self._call("get", path, {"Accept": accept})

    async def post_json(self, path: str, body: Any):
        return await self._call("post", path, {"Accept": "application/json", "Content-Type": "application/json",
                                               **SEC_FETCH}, data=json.dumps(body))

    async def post_action(self, action: str, deployment: str, body: Any):
        """A Next.js server action on the site's root (the Usage figure)."""
        return await self._call("post", "/", {"Accept": "text/x-component", "Next-Action": action,
                                              "Content-Type": "text/plain;charset=UTF-8",
                                              "x-deployment-id": deployment, **SEC_FETCH}, data=json.dumps(body))

    async def socket(self, url: str) -> _Socket:
        """The WebSocket to the VM's load balancer, through the same proxy."""
        try:
            ws = await self.s.ws_connect(url, headers={"Origin": SITE_URL}, timeout=WS_TIMEOUT_S,
                                         proxies=self.proxies, impersonate="chrome")
        except Exception as e:
            raise NetError(type(e).__name__, _proxy_failure(e)) from None
        return _Socket(ws)

    def cookies(self) -> dict[str, str]:
        """The muse.ai cookies the session holds now (what muse set included)."""
        return {c.name: c.value for c in self.s.cookies.jar if on_site(c.domain or "")}


def _json(resp) -> Any:
    try:
        return json.loads(resp.text)
    except (ValueError, TypeError):
        return None


async def keep_rotated(ctx, web: Web) -> bool:
    """Write the cookies muse set during the run back into the account's sealed jar (`ctx.renew_credential`: sealed,
    compare-and-swap, never over a re-import), each only while the stored value is still the one this run started
    from (another run's newer rotation is never overwritten). Nothing after a signed-out answer, and nothing for a
    value a `Cookie:` header cannot carry. Cut at `RENEW_TIMEOUT_S`. True when written. Never raises or logs a value."""
    async def renew() -> bool:
        if web.signed_out:
            return False
        changes = {n: v for n, v in web.cookies().items() if web.start.get(n) != v and renewable(n, v)}
        if not changes:
            return False
        started = dict(web.start)

        def merge(current: bytes, _mine: bytes) -> bytes:
            stored = jar_of(current)
            mine = {n: v for n, v in changes.items() if stored.get(n) == started.get(n)}
            return with_cookies(current, mine) if mine else current
        written = await ctx.renew_credential(with_cookies(ctx.account.credential, changes), merge)
        if written is not None:
            log.info("muse account %s: rotated cookies written back (%s)", ctx.account.id, ", ".join(sorted(changes)))
        return written is not None

    try:
        return await asyncio.wait_for(renew(), RENEW_TIMEOUT_S)
    except Exception as e:      # noqa: BLE001 - the credential stays as it was; never a reason to fail the run
        log.warning("muse account %s: rotated cookies not written back (%s)",
                    getattr(getattr(ctx, "account", None), "id", "?"), type(e).__name__)
        return False


# ---- bootstrap ----------------------------------------------------------------------------------------------------
class Boot(NamedTuple):
    vm_id: str
    ws_url: str          # carries the run's tokens: never logged


async def read_session(web: Web) -> dict:
    """`GET /api/session`: who the session is and its workspace. SignedOut on 401, SiteError on anything that is not
    a JSON object."""
    resp = await web.get(SESSION_PATH)
    if resp.status_code == 401:
        web.signed_out = True
        raise SignedOut("muse answered 401 to the session read")
    body = _json(resp) if resp.status_code == 200 else None
    if not isinstance(body, dict):
        raise SiteError("muse answered %d to the session read%s" % (
            resp.status_code, "" if resp.status_code != 200 else " with no JSON object"))
    return body


def workspace(info: dict) -> tuple[str, str]:
    """(vm_id, endpoint_url) of an assigned session; VmNotReady when none is assigned, SiteError when malformed."""
    if info.get("status") != ASSIGNED:
        raise VmNotReady("the session has no workspace assigned (%s)" % str(info.get("status"))[:40])
    vm, endpoint = info.get("vm_id"), info.get("endpoint_url")
    if not isinstance(endpoint, str) or not endpoint.startswith("wss://") or not isinstance(vm, str) \
            or not _VM_ID.fullmatch(vm) or not (urlsplit(endpoint).hostname or "").endswith(VM_HOST_SUFFIX):
        raise SiteError("the session names no usable workspace")
    return vm, endpoint


async def wake(web: Web, vm: str, settings, sleep: Callable[[float], Awaitable], clock: Callable[[], float]) -> None:
    """Ask a sleeping workspace to wake, then read the session until it says RUNNING (VmNotReady past
    `muse.wake_timeout_s`)."""
    await web.post_json(WAKE_PATH, {"vm_id": vm, "retry_count": 0, "connect_attempt_id": str(uuid.uuid4())})
    timeout = settings.get("muse.wake_timeout_s")
    end = clock() + timeout
    while clock() < end:
        await sleep(settings.get("muse.wake_poll_s"))
        if (await read_session(web)).get("vm_state") == RUNNING:
            return
    raise VmNotReady("the workspace did not wake within %d s" % timeout)


async def bootstrap(web: Web, settings, *, sleep: Callable[[float], Awaitable] = asyncio.sleep,
                    clock: Callable[[], float] = time.monotonic) -> Boot:
    """Session (waking the workspace when it is not RUNNING), then the tunnel's tokens."""
    info = await read_session(web)
    vm, endpoint = workspace(info)
    if info.get("vm_state") != RUNNING:
        await wake(web, vm, settings, sleep, clock)
    resp = await web.post_json(TOKEN_PATH, {"vmAddress": endpoint, "vmName": vm})
    body = _json(resp) if resp.status_code == 200 else None
    token = body.get("token") if isinstance(body, dict) else None
    notary = body.get("notary_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token or not isinstance(notary, str) or not notary.strip():
        raise SiteError("muse answered %d to the tunnel token call%s" % (
            resp.status_code, "" if resp.status_code != 200 else " with no tokens"))
    query = urlencode({"vm_id": vm, "auth_token": token, "notary_token": notary.strip(), "app_id": APP_ID,
                       "request_id": str(uuid.uuid4())})
    return Boot(vm, NOISE_URL + "?" + query)


# ---- the Usage figure -----------------------------------------------------------------------------------------------
class Usage(NamedTuple):
    percent: int                 # the weekly meter, a whole percentage
    quota_ok: bool               # quotaStatus is SUFFICIENT
    resets_at: datetime | None
    paid: bool | None            # the plan's isPaid; None when not said


_DPL = re.compile(r"dpl=(dpl_[A-Za-z0-9]+)")
_SCRIPT = re.compile(r"/_next/static/[^\"'\s\\]+?\.js")
_CHUNK = re.compile(r"static/chunks/[\w.-]+\.js")
# createServerReference("<id>", callServer, void 0, findSourceMapURL, "fetchSubscriptionAction")
_ACTION = re.compile(r"\"([0-9a-f]{40,44})\"(?:,[^,()\"]{1,80}){3},\"%s\"\)" % USAGE_ACTION)
ACTION_IDS: dict[str, str | None] = {}     # deployment id -> action id (None: not in that deployment's scripts)


def parse_usage(text: str) -> Usage | None:
    """The subscription line of the action's answer (React Server Components: `<n>:<json>` lines)."""
    for line in (text or "").splitlines():
        _, sep, rest = line.partition(":")
        if not sep or "percentUsed" not in rest:
            continue
        try:
            sub = json.loads(rest).get("subscription")
            usage = sub["usage"]
        except (ValueError, AttributeError, TypeError, KeyError):
            continue
        percent = usage.get("percentUsed") if isinstance(usage, dict) else None
        if type(percent) is not int or not 0 <= percent <= 100:
            continue
        paid = (sub.get("tier") or {}).get("isPaid") if isinstance(sub.get("tier"), dict) else None
        return Usage(percent, usage.get("quotaStatus") == QUOTA_OK, epoch_time(usage.get("resetsAt")),
                     paid if isinstance(paid, bool) else None)
    return None


def find_action(text: str) -> str | None:
    m = _ACTION.search(text or "")
    return m.group(1) if m else None


async def _hunt(web: Web, html: str) -> str | None:
    """Read the site's scripts (those the page names, then the chunks they name) until one declares the action."""
    queue = list(dict.fromkeys(_SCRIPT.findall(html)))
    seen: set[str] = set()
    while queue and len(seen) < SCAN_MAX_SCRIPTS:
        batch = [p for p in dict.fromkeys(queue) if p not in seen][:SCAN_BATCH]
        queue = [p for p in queue if p not in batch]
        if not batch:
            break
        seen.update(batch)
        for resp in await asyncio.gather(*(web.get(p, accept="*/*") for p in batch), return_exceptions=True):
            text = getattr(resp, "text", "") if getattr(resp, "status_code", 0) == 200 else ""
            found = find_action(text)
            if found:
                return found
            queue += ["/_next/" + c for c in _CHUNK.findall(text) if "/_next/" + c not in seen]
    return None


async def read_usage(web: Web) -> Usage | None:
    """The weekly Usage figure, or None when it cannot be read (the action id not found, any failure): a missing
    figure changes nothing. The action id is looked up once per site deployment in this process."""
    async def read() -> Usage | None:
        page = await web.get("/", accept="text/html")
        html = page.text if page.status_code == 200 else ""
        m = _DPL.search(html)
        if not m:
            log.warning("muse usage: the site page names no deployment")
            return None
        deployment = m.group(1)
        if deployment not in ACTION_IDS:
            ACTION_IDS[deployment] = await _hunt(web, html)
        action = ACTION_IDS[deployment]
        if action is None:
            log.warning("muse usage: no %s in the scripts of deployment %s", USAGE_ACTION, deployment)
            return None
        resp = await web.post_action(action, deployment, [{"includeAgreement": True}])
        usage = parse_usage(resp.text) if resp.status_code == 200 else None
        if usage is None:
            log.warning("muse usage: the action answered %d with no usage figure", resp.status_code)
        return usage

    try:
        return await asyncio.wait_for(read(), USAGE_BUDGET_S)
    except Exception as e:      # noqa: BLE001 - the figure is a bonus: the check goes on without it
        log.warning("muse usage: not read (%s)", type(e).__name__)
        return None
