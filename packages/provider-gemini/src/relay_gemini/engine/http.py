"""HTTP to gemini.google.com over httpx, bound to one account's Google cookie jar and its proxy.

gemini answers plain HTTP (no TLS fingerprint and no anti-bot token needed for a send, measured 23/10), so the async
httpx client relay-core already carries is enough. The transport is a seam: tests pass an `httpx.MockTransport`. With a
proxy on the account every call goes through it and nothing goes direct; no call follows a redirect (the cookies must
not travel to wherever an answer points). The cookies live in the client's cookie jar, so each `Set-Cookie` lands
where a browser would put it, and a cookie goes only to the hosts its domain covers.

Session keeping (FINDINGS 1): `__Secure-1PSIDTS` ages out in about 20 min. Every run of an account (a check, a submit,
a reconcile, a poll, a fetch) first rotates the session through `RotateCookies` when `gemini.rotate_interval_s` has
passed since the last rotation, and writes the rotated jar back at once (`RunCtx.renew_credential`, sealed,
compare-and-swap; a write that lost to another run is merged with `merge_blobs` and tried once more); at the end of the
run every cookie that changed (a page load refreshes the SIDCC trio) is written back too. `Sessions` keeps each
account's newest jar in this process and makes the rotation of one account one at a time, so two runs never rotate the
same session together. The session is gemini's own (owner, 24/10): no other provider rotates it.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit

import httpx

from relay_core.providers import Proxy
from relay_gemini.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    BYTES_HOST,
    FULL_SIZE,
    LINK_HOPS,
    LINK_HOSTS,
    MAX_ACCOUNTS,
    ROTATE_BODY,
    ROTATE_URL,
    RPC_PATH,
    STREAM_PATH,
    UPLOAD_PUSH_ID,
    UPLOAD_TENANT,
    UPLOAD_URL,
    USER_AGENT,
    VIDEO_BYTES_HOST,
)
from relay_gemini.credential import dump, email_of, jar_of
from relay_gemini.engine.reply import modes as read_modes

log = logging.getLogger(__name__)
_AT = re.compile(r'"SNlM0e":"([^"]+)"')
_FSID = re.compile(r'"FdrFJe":"(-?\d+)"')
_BL = re.compile(r'"cfb2h":"([^"]+)"')
_EMAIL = re.compile(r'"oPEP7c":"([^"@]+@[^"]+)"')
SESSION_LINK_HOSTS = tuple(h for h in LINK_HOSTS if h != BYTES_HOST)   # the link hops that read the session


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port` for httpx; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


# ---- the jar ------------------------------------------------------------------------------------------------------
@dataclass
class Jar:
    """One account's cookies, keyed (domain, path, name), when the session was last rotated (unix s), and the Google
    account it is meant for (the credential's optional email)."""
    cookies: dict[tuple[str, str, str], str]
    rotated_at: float
    email: str | None = None

    @classmethod
    def of(cls, blob: bytes) -> "Jar":
        cookies, rotated_at = jar_of(blob)
        return cls({(c["domain"], c["path"], c["name"]): c["value"] for c in cookies}, rotated_at, email_of(blob))

    @classmethod
    def of_client(cls, jar: httpx.Cookies, rotated_at: float, email: str | None = None) -> "Jar":
        return cls({(c.domain, c.path or "/", c.name): c.value or "" for c in jar.jar}, rotated_at, email)

    def copy(self) -> "Jar":
        return Jar(dict(self.cookies), self.rotated_at, self.email)

    def blob(self) -> bytes:
        return dump([{"name": n, "value": v, "domain": d, "path": p} for (d, p, n), v in self.cookies.items()],
                    self.rotated_at, self.email)

    def client_cookies(self) -> httpx.Cookies:
        out = httpx.Cookies()
        for (domain, path, name), value in self.cookies.items():
            out.set(name, value, domain=domain, path=path)
        return out


class Sessions:
    """Each account's newest jar in this worker process, and one lock per account for its rotation."""
    # ponytail: per process, one worker process per provider. A second process would rotate on its own; the jar it
    # writes back is still a valid rotation of the same session.

    def __init__(self) -> None:
        self._jars: dict[str, tuple[int, Jar]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    def current(self, account) -> Jar:
        """The newer of the jar this process holds and the one the account was loaded with. A held jar of another
        credential generation (the account was re-imported) is dropped."""
        loaded = Jar.of(account.credential)
        held = self._jars.get(account.id)
        if held is not None and held[0] == account.credential_gen and held[1].rotated_at >= loaded.rotated_at:
            return held[1].copy()
        return loaded

    def merge(self, account, before: Jar, after: Jar) -> Jar:
        """Lay what changed in one run (`after` against `before`) on the newest jar, keep it, return it."""
        base = self.current(account)
        for key, value in after.cookies.items():
            if before.cookies.get(key) != value:
                base.cookies[key] = value
        base.rotated_at = max(base.rotated_at, after.rotated_at)
        self._jars[account.id] = (account.credential_gen, base)
        return base.copy()

    def keep(self, account, jar: Jar) -> None:
        """Hold exactly `jar` (what the database now stores) for the account."""
        self._jars[account.id] = (account.credential_gen, jar.copy())

    def forget(self, account_id: str) -> None:
        self._jars.pop(account_id, None)


def merge_blobs(stored: bytes, mine: bytes) -> bytes:
    """`RunCtx.renew_credential`'s merge: another run renewed the session first. Cookie by cookie, the jar rotated
    later wins (a tie: the stored one); a cookie only one of them has is kept."""
    a, b = Jar.of(stored), Jar.of(mine)
    old, new = (a, b) if a.rotated_at < b.rotated_at else (b, a)
    return Jar({**old.cookies, **new.cookies}, new.rotated_at, a.email).blob()      # the stored account stays


# ---- the page ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Page:
    """What a page load says: signed in ⇔ `at` (SNlM0e) is there. `fsid` and `bl` go on every call."""
    status: int
    at: str | None
    fsid: str | None
    bl: str | None
    email: str | None

    @property
    def signed_in(self) -> bool:
        return self.status == 200 and bool(self.at)


def read_page(status: int, html: str) -> Page:
    def first(rx):
        m = rx.search(html)
        return m.group(1) if m else None
    return Page(status, first(_AT), first(_FSID), first(_BL), first(_EMAIL))


# ---- one run of one account ---------------------------------------------------------------------------------------
def authuser_of(account) -> int:
    """The `/u/N` index of the account's Google account in its session: the one the check resolved from the
    credential's email (`provider_state.authuser`), else 0."""
    n = (account.provider_state or {}).get("authuser")
    return n if type(n) is int and 0 <= n < MAX_ACCOUNTS else 0


class Site:
    """One run of one account against gemini: its client (cookies, proxy, no redirects), the session rotation when
    due, the page load and the RPCs. Use as `async with Site(...) as site:`; leaving it writes the changed cookies back.
    Every gemini call goes to the account's `/u/N` (`authuser`; none for 0, FINDINGS 6)."""

    def __init__(self, ctx, sessions: Sessions, settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.time):
        self.ctx, self.sessions, self.settings, self.clock = ctx, sessions, settings, clock
        self.account = ctx.account
        self.transport = transport
        self.authuser = authuser_of(self.account)
        self.email: str | None = None          # the Google account the credential names (None: none named)
        self.page: Page | None = None
        self.rotation: int | None = None      # the status of this run's RotateCookies; None: none was due
        self._before: Jar | None = None
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Site":
        inner = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(self.account.proxy), trust_env=False)
        async with self.sessions.lock(self.account.id):
            jar = self.sessions.current(self.account)
            self._before, self.email = jar, jar.email
            if not jar.email:
                self.authuser = 0            # no account named: `/u/0`, whatever an older credential resolved
            self.client = httpx.AsyncClient(
                cookies=jar.client_cookies(), transport=inner, timeout=API_TIMEOUT_S, follow_redirects=False,
                trust_env=False, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
            try:
                if self.clock() - jar.rotated_at >= int(self.settings.get("gemini.rotate_interval_s")):
                    await self._rotate()
            except BaseException:
                await self.client.aclose()
                raise
        return self

    async def _rotate(self) -> None:
        """RotateCookies (FINDINGS 1): 200 sets a new __Secure-1PSIDTS pair and the SIDCC trio. The rotated jar is
        written back before anything else happens, so a crash later cannot lose it."""
        resp = await self.client.post(ROTATE_URL, content=ROTATE_BODY, headers={
            "Content-Type": "application/json", "Origin": "https://accounts.google.com"})
        self.rotation = resp.status_code
        if resp.status_code != 200:
            return
        rotated = Jar.of_client(self.client.cookies, self.clock())
        kept = self.sessions.merge(self.account, self._before, rotated)
        self._before = rotated
        await self._write_back(kept)

    async def _write_back(self, jar: Jar) -> None:
        """Every write is merged with the stored session (`merge_blobs`); this process then holds exactly what was
        stored, never its own pre-merge copy, so a later write of this run cannot bring an older rotation back."""
        try:
            stored = await self.ctx.renew_credential(jar.blob(), merge_blobs)
            if stored is None:
                self.sessions.forget(self.account.id)      # re-imported meanwhile: the new credential wins
            else:
                self.sessions.keep(self.account, Jar.of(stored))
        except Exception as e:      # noqa: BLE001 - the jar stays held here; the next run writes it again
            log.warning("gemini account %s: the renewed session was not written back (%s)", self.account.id,
                        type(e).__name__)

    async def __aexit__(self, *exc) -> None:
        try:
            after = Jar.of_client(self.client.cookies, self._before.rotated_at)
            if after.cookies != self._before.cookies:
                async with self.sessions.lock(self.account.id):
                    await self._write_back(self.sessions.merge(self.account, self._before, after))
        finally:
            await self.client.aclose()

    def _url(self, path: str) -> str:
        return BASE_URL + ("/u/%d" % self.authuser if self.authuser else "") + path

    async def load(self, path: str = "/app") -> Page:
        resp = await self.client.get(self._url(path), headers={"Accept": "text/html,application/xhtml+xml"})
        self.page = read_page(resp.status_code, resp.text if resp.status_code == 200 else "")
        return self.page

    async def find_account(self, email: str) -> int | None:
        """Load `/u/0`, `/u/1`, ... until the page names `email`; that index becomes this run's `authuser`. None when
        no signed-in account of the session has it (the first index that is not a signed-in page ends the list:
        measured, `/u/5` of a 5-account session answers 302)."""
        for n in range(MAX_ACCOUNTS):
            self.authuser = n
            page = await self.load()
            if not page.signed_in:
                return None
            if (page.email or "").lower() == email:
                return n
        return None

    async def open_account(self) -> Page | None:
        """Load the page and make sure it is the Google account the credential names before any account-scoped
        read: at the kept index, or, when the session's account order moved, at the index `find_account` finds now
        (for this run). None when the page is signed out or the account cannot be found: the caller reads nothing."""
        page = await self.load()
        if not page.signed_in:
            return None
        if self.email and (page.email or "").lower() != self.email:
            if await self.find_account(self.email) is None:
                return None
            page = self.page
        return page

    async def modes(self) -> list[tuple[str, str | None, int | None]]:
        """The account's modes `(id, name, kind)`, the default first (otAQ7b); one is sent with every order: the modes
        differ by plan (FINDINGS 6). Empty when they cannot be read."""
        status, body = await self.rpc("otAQ7b", [])
        return read_modes(body) if status == 200 else []

    def _query(self, **extra) -> str:
        page = self.page
        return urlencode({**extra, "bl": page.bl or "", "f.sid": page.fsid or "", "hl": "en",
                          "_reqid": random.randint(10000, 99999), "rt": "c"})

    def _form(self, freq) -> str:
        return urlencode({"f.req": json.dumps(freq, separators=(",", ":")), "at": self.page.at})

    async def rpc(self, rpcid: str, args, path: str = "/app") -> tuple[int, str]:
        """One batchexecute read; the page must be loaded (signed in). HTTP errors propagate."""
        resp = await self.client.post(
            "%s?%s" % (self._url(RPC_PATH), self._query(rpcids=rpcid, **{"source-path": path})),
            content=self._form([[[rpcid, json.dumps(args, separators=(",", ":")), None, "generic"]]]),
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "Origin": BASE_URL,
                     "Referer": BASE_URL + path, "X-Same-Domain": "1", "Accept": "*/*"})
        return resp.status_code, resp.text

    async def send(self, inner: list, headers: dict, timeout: float) -> tuple[int, str]:
        """The ONE StreamGenerate POST of a job, its whole answer read (it ends when the image is drawn, or when a video
        is taken). HTTP errors and timeouts propagate: the caller decides what they mean."""
        resp = await self.client.post(
            "%s?%s" % (self._url(STREAM_PATH), self._query()),
            content=self._form([None, json.dumps(inner, separators=(",", ":"))]), timeout=timeout,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "Origin": BASE_URL,
                     "Referer": BASE_URL + "/app", "X-Same-Domain": "1", "Accept": "*/*", **headers})
        return resp.status_code, resp.text

    async def upload(self, name: str, data: bytes) -> str | None:
        """The web app's resumable upload of one input image (free): `start`, then the bytes with `upload,
        finalize`. The storage path the send names the image by, or None when a step was not answered with one. On
        `/u/N` the upload names the account (`?authuser=N`) as the web app does: a file uploaded without it was
        refused by that account's send (FINDINGS 7)."""
        base = {"Origin": BASE_URL, "Referer": BASE_URL + "/", "Push-ID": UPLOAD_PUSH_ID, "X-Tenant-Id": UPLOAD_TENANT}
        url = UPLOAD_URL + ("?authuser=%d" % self.authuser if self.authuser else "")
        start = await self.client.post(url, content="File name: " + name, headers={
            **base, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)), "X-Goog-Upload-Protocol": "resumable"})
        target = start.headers.get("x-goog-upload-url") if start.status_code == 200 else None
        to = urlsplit(target or "")
        if not target or to.scheme != "https" or to.hostname != urlsplit(UPLOAD_URL).hostname:
            return None
        done = await self.client.post(target, content=data, headers={
            **base, "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0"})
        path = done.text.strip() if done.status_code == 200 else ""
        return path if path.startswith("/contrib_service/") else None

    async def resolve(self, link: str, size: str = FULL_SIZE) -> str | None:
        """A result link (`/gg-dl/` of a reply, `/gg/` of a chat read) to the link of the file's bytes: the link with
        `size` (`=d-I` an image's original, `=m22` a video's 720p MP4) and `alr=yes` answers the next link as text, the
        session's cookies going only where their domain covers the host (FINDINGS 3, 7). On `/u/N` the first link and
        every hop on a host that reads the session (`SESSION_LINK_HOSTS`) name the account (`authuser=N`): without it
        the last hop is 403. The bytes host (lh3, googlevideo.com) gets neither the account nor a cookie. Every
        hop must be https on `LINK_HOSTS` or, for a video's bytes, a googlevideo.com host. None when a hop is not, or
        the chain does not end, inside `LINK_HOPS`, in an answer that is not text."""
        au = "&authuser=%d" % self.authuser if self.authuser else ""
        current = re.sub(r"=[A-Za-z0-9-]+$", "", link) + size + "?alr=yes" + au
        for _ in range(LINK_HOPS):
            parts = urlsplit(current)
            host = parts.hostname or ""
            if parts.scheme != "https" or (host not in LINK_HOSTS and not host.endswith("." + VIDEO_BYTES_HOST)):
                return None
            async with self.client.stream("GET", current, headers={"Referer": BASE_URL + "/"}) as resp:
                if resp.status_code != 200:
                    return None
                if not resp.headers.get("content-type", "").startswith("text/"):
                    return current           # the bytes: the fetcher downloads them through the proxy
                text = (await resp.aread()).decode("utf-8", "replace").strip()
            if not text.startswith("https://"):
                return None
            current = text + ("&" if "?" in text else "?") + au[1:] \
                if au and urlsplit(text).hostname in SESSION_LINK_HOSTS else text
        return None


