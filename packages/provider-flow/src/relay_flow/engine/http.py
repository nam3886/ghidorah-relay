"""HTTP to flow.google.com over httpx, bound to one account's Google cookie jar and its proxy.

flow answers plain HTTP (no TLS fingerprint check measured), so the async httpx client relay-core already carries is
enough. The transport is a seam: tests pass an `httpx.MockTransport`. With a proxy on the account every call goes
through it and nothing goes direct; no call follows a redirect (the cookies must not travel to wherever an answer
points). The cookies live in the client's cookie jar, so each `Set-Cookie` lands where a browser would put it.

Session keeping (FINDINGS 1): `__Secure-1PSIDTS` ages out in about 20 min. Every run of an account (a check, a submit,
a reconcile, a poll) first rotates the session through `RotateCookies` when `flow.rotate_interval_s` has passed since
the last rotation, and writes the rotated jar back at once (`RunCtx.renew_credential`, sealed, compare-and-swap; a
write that lost to another run is merged with `merge_blobs` and tried once more); at the end of the run
every cookie that changed (a page load refreshes the SIDCC trio) is written back too. `Sessions` keeps each account's
newest jar in this process and makes the rotation of one account one at a time, so two runs never rotate the same
session together.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import httpx

from relay_core.providers import Proxy
from relay_flow.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    ROTATE_BODY,
    ROTATE_URL,
    RPC_PATH,
    USER_AGENT,
)
from relay_flow.credential import dump, jar_of

log = logging.getLogger(__name__)

REFUSED_CODES = (7, 8)     # PERMISSION_DENIED (unusual activity), RESOURCE_EXHAUSTED (too much traffic): nothing made
ERROR_TYPE_PREFIX = "type.googleapis.com/"     # each detail of a refusal's error envelope
CLIENT_TOOL = 22           # the client id flow's web app sends in every request context
_AT = re.compile(r'"SNlM0e":"([^"]+)"')
_FSID = re.compile(r'"FdrFJe":"(-?\d+)"')
_BL = re.compile(r'"cfb2h":"([^"]+)"')
_EMAIL = re.compile(r'"oPEP7c":"([^"@]+@[^"]+)"')


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
    """One account's cookies, keyed (domain, path, name), and when the session was last rotated (unix s)."""
    cookies: dict[tuple[str, str, str], str]
    rotated_at: float

    @classmethod
    def of(cls, blob: bytes) -> "Jar":
        cookies, rotated_at = jar_of(blob)
        return cls({(c["domain"], c["path"], c["name"]): c["value"] for c in cookies}, rotated_at)

    @classmethod
    def of_client(cls, jar: httpx.Cookies, rotated_at: float) -> "Jar":
        return cls({(c.domain, c.path or "/", c.name): c.value or "" for c in jar.jar}, rotated_at)

    def copy(self) -> "Jar":
        return Jar(dict(self.cookies), self.rotated_at)

    def blob(self) -> bytes:
        return dump([{"name": n, "value": v, "domain": d, "path": p} for (d, p, n), v in self.cookies.items()],
                    self.rotated_at)

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
    return Jar({**old.cookies, **new.cookies}, new.rotated_at).blob()


# ---- the page and the RPC reply ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Page:
    """What a page load says: signed in ⇔ `at` (SNlM0e) is there. `fsid` and `bl` go on every RPC."""
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


@dataclass(frozen=True)
class Item:
    """One `wrb.fr` item of the rpc id: `raw` is its result slot as sent (None: no result), `payload` that slot
    decoded (None when absent or not JSON), `code` its envelope's error code (`item[5][0]`), `refusal_shape` whether
    it is exactly the recorded refusal item (`refusal_shape()`), `reason` the first ErrorInfo reason of the envelope
    (`item[5][2][0][1][0]`, e.g. PUBLIC_ERROR_UNUSUAL_ACTIVITY), None without one."""
    raw: object
    payload: object
    code: int | None
    refusal_shape: bool
    reason: str | None = None


@dataclass(frozen=True)
class Reply:
    """One batchexecute answer for one rpc id: EVERY matching item of every frame (`items`), and `clean`: every line
    of the answer was a frame, a length line or the `)]}'` guard (nothing cut or unreadable)."""
    status: int
    items: tuple[Item, ...] = ()
    clean: bool = True

    @property
    def found(self) -> bool:
        return bool(self.items)

    @property
    def payload(self) -> object:
        """The decoded result when the answer holds exactly one matching item; None otherwise (none, or several)."""
        return self.items[0].payload if len(self.items) == 1 else None

    @property
    def code(self) -> int | None:
        return self.items[0].code if len(self.items) == 1 else None

    @property
    def reason(self) -> str | None:
        return self.items[0].reason if len(self.items) == 1 else None

    @property
    def refused_before_anything(self) -> bool:
        """The immediate reCAPTCHA refusal, and only its exact shape: HTTP 200, a clean answer, exactly ONE matching
        item, that item exactly the recorded refusal (`refusal_shape()`) with code 7 or 8. Measured (FINDINGS 3):
        nothing is charged, nothing is listed. Anything else (a result anywhere, two items, any other slot filled, a
        cut or unreadable frame) is not this."""
        if self.status != 200 or not self.clean or len(self.items) != 1:
            return False
        item = self.items[0]
        return item.refusal_shape and item.code in REFUSED_CODES


def _item(item: list) -> Item:
    payload = None
    if isinstance(item[2], str):
        try:
            payload = json.loads(item[2])
        except ValueError:
            payload = None
    err = item[5] if len(item) > 5 else None
    code = err[0] if isinstance(err, list) and err and type(err[0]) is int else None
    return Item(item[2], payload, code, refusal_shape(item), reason_of(err))


def reason_of(err) -> str | None:
    """The first ErrorInfo reason of an error envelope `[code, null, [["type.googleapis.com/...", [reason]], ...]]`."""
    try:
        reason = err[2][0][1][0]
    except (TypeError, IndexError, KeyError):
        return None
    return reason if isinstance(reason, str) else None


def refusal_shape(item: list) -> bool:
    """Exactly the recorded refusal item (fixtures/flow/live-261023 img1, img6): `["wrb.fr", id, null, null, null,
    [code, null, details], "generic"]`: no result, slots 3 and 4 empty, an int code, slot 1 of the envelope empty, and
    the details (optional) a list of `["type.googleapis.com/...", ...]` entries."""
    if len(item) != 7 or item[6] != "generic":
        return False
    if item[2] is not None or item[3] is not None or item[4] is not None:
        return False
    err = item[5]
    if not isinstance(err, list) or len(err) not in (2, 3) or type(err[0]) is not int or err[1] is not None:
        return False
    details = err[2] if len(err) == 3 else []
    return isinstance(details, list) and all(
        isinstance(d, list) and d and isinstance(d[0], str) and d[0].startswith(ERROR_TYPE_PREFIX) for d in details)


def read_reply(status: int, text: str, rpcid: str) -> Reply:
    """Every `wrb.fr` item of `rpcid` in a `)]}'`-framed answer, across all frames."""
    items, clean = [], True
    for line in text.splitlines():
        line = line.strip()
        if not line or line == ")]}'" or line.isdigit():
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            frame = None
        if not isinstance(frame, list):
            clean = False          # a cut frame, or text that is not one: it may have held anything
            continue
        for i in frame:
            if isinstance(i, list) and len(i) > 2 and i[0] == "wrb.fr" and i[1] == rpcid:
                items.append(_item(i))
            elif rpcid in json.dumps(i):
                clean = False      # our rpc id in any other shape (cut short, moved): it may have carried anything
    return Reply(status, tuple(items), clean)


# ---- one run of one account ---------------------------------------------------------------------------------------
class Site:
    """One run of one account against flow: its client (cookies, proxy, no redirects), the session rotation when due,
    the page load and the RPCs. Use as `async with Site(...) as site:`; leaving it writes the changed cookies back."""

    def __init__(self, ctx, sessions: Sessions, settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.time):
        self.ctx, self.sessions, self.settings, self.clock = ctx, sessions, settings, clock
        self.account = ctx.account
        self.transport = transport
        self.page: Page | None = None
        self.rotation: int | None = None      # the status of this run's RotateCookies; None: none was due
        self._before: Jar | None = None
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Site":
        inner = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(self.account.proxy), trust_env=False)
        async with self.sessions.lock(self.account.id):
            jar = self.sessions.current(self.account)
            self._before = jar
            self.client = httpx.AsyncClient(
                cookies=jar.client_cookies(), transport=inner, timeout=API_TIMEOUT_S, follow_redirects=False,
                trust_env=False, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
            try:
                if self.clock() - jar.rotated_at >= int(self.settings.get("flow.rotate_interval_s")):
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
            log.warning("flow account %s: the renewed session was not written back (%s)", self.account.id,
                        type(e).__name__)

    async def __aexit__(self, *exc) -> None:
        try:
            after = Jar.of_client(self.client.cookies, self._before.rotated_at)
            if after.cookies != self._before.cookies:
                async with self.sessions.lock(self.account.id):
                    await self._write_back(self.sessions.merge(self.account, self._before, after))
        finally:
            await self.client.aclose()

    async def load(self, path: str = "/") -> Page:
        resp = await self.client.get(BASE_URL + path, headers={"Accept": "text/html,application/xhtml+xml"})
        self.page = read_page(resp.status_code, resp.text if resp.status_code == 200 else "")
        return self.page

    async def rpc(self, rpcid: str, args, path: str = "/", timeout: float = API_TIMEOUT_S) -> Reply:
        """One batchexecute call; the page must be loaded (signed in). HTTP errors propagate."""
        page = self.page
        query = urlencode({"rpcids": rpcid, "source-path": path, "bl": page.bl or "", "f.sid": page.fsid or "",
                           "hl": "en", "_reqid": random.randint(10000, 99999), "rt": "c"})
        freq = json.dumps([[[rpcid, json.dumps(args, separators=(",", ":")), None, "generic"]]],
                          separators=(",", ":"))
        resp = await self.client.post(
            "%s%s?%s" % (BASE_URL, RPC_PATH, query), content=urlencode({"f.req": freq, "at": page.at}) + "&",
            timeout=timeout, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                                      "Origin": BASE_URL, "Referer": BASE_URL + path, "X-Same-Domain": "1",
                                      "Accept": "*/*"})
        return read_reply(resp.status_code, resp.text, rpcid)      # the status is judged with the body, not instead


def request_context(project: str, token: str) -> list:
    """The client context of a generate: the project, and the reCAPTCHA token in its last slot."""
    return [None, CLIENT_TOOL, None, None, None, project, None, None, None, None, [token, 1]]
