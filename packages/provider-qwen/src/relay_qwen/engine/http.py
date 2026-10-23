"""HTTP to chat.qwen.ai over httpx, bound to one account's cookie jar and its proxy.

Every qwen call answers plain HTTP except the paid `chat/completions`, which also needs the minted anti-bot pair
(FINDINGS 2). With a proxy on the account every call goes through it and nothing goes direct; no call follows a
redirect. The transport is a seam: tests pass an `httpx.MockTransport`.

Session keeping (FINDINGS 1): the jar's `refresh_token` buys a 15-min access token at `auth.qwen.ai`; each run reuses
the token this process holds while it is younger than `TOKEN_REUSE_S`, and refreshes it otherwise. `Sessions` keeps
each account's newest credential (a mint replaces it) and token in this process; `write_back` stores a changed
credential through `RunCtx.renew_credential` (sealed, compare-and-swap, merged with `merge_blobs`).
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from relay_core.providers import Proxy
from relay_qwen.constants import (
    API_TIMEOUT_S,
    BASE_URL,
    CREDITS_QUERY,
    CREDITS_URL,
    MEDIA_HOSTS,
    QWEN_DOMAIN,
    REFRESH_COOKIE,
    REFRESH_URL,
    SEC_CH_UA,
    SEC_CH_UA_PLATFORM,
    TOKEN_REUSE_S,
    USER_AGENT,
    WEB_VERSION,
)
from relay_qwen.credential import Cred, cred_of, merge_blobs, merge_creds

log = logging.getLogger(__name__)


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port` for httpx; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


class SignedOut(Exception):
    """The refresh said the session is gone. The text names the answer, never a value."""


class BadAnswer(Exception):
    """A plain step answered something unusable (not a sign-out). The text names the step and the status only."""


@dataclass
class Token:
    value: str
    at: float
    user: dict


class Sessions:
    """Each account's newest credential and access token in this worker process, and one lock per account (a mint)."""
    # ponytail: per process, one worker process per provider. A second process would mint and refresh on its own; what
    # it writes back is still a valid credential of the same session.

    def __init__(self) -> None:
        self._creds: dict[str, tuple[int, Cred]] = {}
        self._tokens: dict[str, tuple[int, Token]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refreshing: dict[str, asyncio.Lock] = {}

    def lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    def refresh_lock(self, account_id: str) -> asyncio.Lock:
        """One session refresh of an account at a time in this process; the others wait and reuse its token."""
        return self._refreshing.setdefault(account_id, asyncio.Lock())

    def current(self, account) -> Cred:
        """The credential this process holds merged with the one the account was loaded with, field by field
        (`merge_creds`). A held one of another credential generation (the account was re-imported) is dropped."""
        loaded = cred_of(account.credential)
        held = self._creds.get(account.id)
        if held is not None and held[0] == account.credential_gen:
            return merge_creds(loaded, held[1])
        return loaded

    def keep(self, account, cred: Cred) -> None:
        self._creds[account.id] = (account.credential_gen, cred.copy())

    def forget(self, account_id: str) -> None:
        self._creds.pop(account_id, None)
        self._tokens.pop(account_id, None)

    def token(self, account, now: float) -> Token | None:
        held = self._tokens.get(account.id)
        if held is None or held[0] != account.credential_gen or now - held[1].at >= TOKEN_REUSE_S:
            return None
        return held[1]

    def keep_token(self, account, token: Token) -> None:
        self._tokens[account.id] = (account.credential_gen, token)


async def write_back(ctx, sessions: Sessions, cred: Cred) -> None:
    """Store `cred` as the account's credential; this process then holds exactly what was stored."""
    account = ctx.account
    try:
        stored = await ctx.renew_credential(cred.blob(), merge_blobs)
    except Exception as e:      # noqa: BLE001 - this process still holds the new credential; the next run writes it
        sessions.keep(account, cred)
        log.warning("qwen account %s: the credential was not written back (%s)", account.id, type(e).__name__)
        return
    if stored is None:
        sessions.forget(account.id)          # re-imported meanwhile: the new credential wins
    else:
        sessions.keep(account, cred_of(stored))


def js_date(now: float) -> str:
    """The `Timezone` header the web app sends: JavaScript's Date string, in UTC as the worker runs."""
    return time.strftime("%a %b %d %Y %H:%M:%S GMT+0000", time.gmtime(now))


def json_of(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class Site:
    """One run of one account against qwen: its client (cookies, proxy, no redirects), its access token and the
    headers the web app sends. Use as `async with Site(...) as site:`."""

    def __init__(self, ctx, sessions: Sessions, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.time):
        self.ctx, self.sessions, self.clock = ctx, sessions, clock
        self.account = ctx.account
        self.transport = transport
        self.cred = sessions.current(self.account)
        self.user: dict = {}
        self._token: str | None = None
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Site":
        inner = self.transport or httpx.AsyncHTTPTransport(proxy=proxy_url(self.account.proxy), trust_env=False)
        self.client = httpx.AsyncClient(transport=inner, timeout=API_TIMEOUT_S, follow_redirects=False,
                                        trust_env=False)
        self.adopt(self.cred)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.client.aclose()

    def adopt(self, cred: Cred) -> None:
        """Send `cred`'s cookies from now on (a mint replaced them)."""
        self.cred = cred
        jar = httpx.Cookies()
        for (domain, path, name), value in cred.cookies.items():
            jar.set(name, value, domain=domain, path=path)
        self.client.cookies = jar

    # ---- headers ----------------------------------------------------------------------------------------------
    def headers(self, referer: str = "/", *, bx: bool = False) -> dict[str, str]:
        """What the web app sends with an API call; `bx` adds the minted pair (the paid call only)."""
        minted = self.cred.bx or {}
        out = {"Accept": "application/json", "Content-Type": "application/json", "Accept-Language": "en-US,en;q=0.9",
               "source": "web", "Origin": BASE_URL, "Referer": BASE_URL + referer, "x-request-id": str(uuid.uuid4()),
               "Version": minted.get("version") or WEB_VERSION, "Timezone": js_date(self.clock()),
               "User-Agent": minted.get("user-agent") or USER_AGENT,
               "sec-ch-ua": minted.get("sec-ch-ua") or SEC_CH_UA,
               "sec-ch-ua-mobile": minted.get("sec-ch-ua-mobile") or "?0",
               "sec-ch-ua-platform": minted.get("sec-ch-ua-platform") or SEC_CH_UA_PLATFORM}
        if self._token:
            out["Authorization"] = "Bearer " + self._token
        if bx:
            out.update({k: minted[k] for k in ("bx-ua", "bx-umidtoken", "bx-v") if minted.get(k)})
            out["x-accel-buffering"] = "no"
        return out

    # ---- the session ------------------------------------------------------------------------------------------
    async def refresh(self) -> dict:
        """A new access token from the jar (always a call); the user it names. `SignedOut` when qwen says the session
        is gone, `BadAnswer` on any other unusable answer; network errors propagate. One refresh per account at a
        time, always with the newest stored session cookie."""
        async with self.sessions.refresh_lock(self.account.id):
            return await self._refresh()

    async def _refresh(self) -> dict:
        self.adopt(self.sessions.current(self.account))       # the newest session, never a stale copy
        headers = self.headers()
        headers.pop("Authorization", None)
        headers["x-request-origin"] = BASE_URL
        resp = await self.client.get(REFRESH_URL, headers=headers)
        body = json_of(resp)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        token = data.get("access_token")
        if resp.status_code == 200 and body.get("success") is True and isinstance(token, str) and token:
            self._token = token
            self.user = {k: data.get(k) for k in ("id", "email", "name") if isinstance(data.get(k), str)}
            await self._keep_rotated(data.get("refresh_token"))
            self.sessions.keep_token(self.account, Token(token, self.clock(), self.user))
            return self.user
        if resp.status_code in (401, 403) or (resp.status_code == 200 and body.get("success") is False):
            raise SignedOut("qwen answered %d to the session refresh (%s)" % (
                resp.status_code, str(data.get("code") or body.get("message") or "no code")[:60]))
        raise BadAnswer("qwen answered %d to the session refresh" % resp.status_code)

    async def _keep_rotated(self, answered) -> None:
        """A refresh that hands back another refresh token (in its body or a cookie) rotated the session: the new
        one is written back at once, before anything else uses the session. Not seen so far (24 of 24 recorded
        refreshes gave the jar's own token back, FINDINGS 1): a guard, in case qwen starts rotating."""
        key = next((k for k in self.cred.cookies if k[2] == REFRESH_COOKIE), ("." + QWEN_DOMAIN, "/", REFRESH_COOKIE))
        jar = [c.value for c in self.client.cookies.jar if c.name == REFRESH_COOKIE and c.value]
        new = answered if isinstance(answered, str) and answered else (jar[-1] if jar else None)
        if not new or new == self.cred.cookies.get(key):
            return
        rotated = self.cred.copy()
        rotated.cookies[key] = new
        rotated.refreshed_at = self.clock()
        await write_back(self.ctx, self.sessions, rotated)
        self.adopt(self.sessions.current(self.account))

    async def token(self) -> str:
        """The access token: the one this process holds while it is young enough, else a refresh. Runs that need one
        together make one refresh; the others reuse its token."""
        async with self.sessions.refresh_lock(self.account.id):
            held = self.sessions.token(self.account, self.clock())
            if held is not None:
                self._token, self.user = held.value, held.user
                return held.value
            await self._refresh()
            return self._token

    # ---- calls ------------------------------------------------------------------------------------------------
    async def call(self, method: str, path: str, body: Any = None, *, referer: str = "/",
                   timeout: float | None = None) -> tuple[int, dict]:
        """One plain API call (the token must be there): (status, JSON object or {})."""
        url = path if path.startswith("https://") else BASE_URL + path
        kw: dict[str, Any] = {"headers": self.headers(referer)}
        if body is not None:
            kw["json"] = body
        if timeout:
            kw["timeout"] = timeout
        resp = await self.client.request(method, url, **kw)
        return resp.status_code, json_of(resp)

    async def new_chat(self, chat_type: str, model: str) -> str:
        """A new empty chat (free): its id, the job's write-ahead key. `BadAnswer` without one."""
        status, body = await self.call("POST", "/api/v2/chats/new", {
            "chatId": "", "models": [model], "project_id": "", "timestamp": int(self.clock() * 1000),
            "chat_type": chat_type, "chat_mode": "normal"}, referer="/c/new-chat")
        chat = (body.get("data") or {}).get("id") if isinstance(body.get("data"), dict) else None
        if status != 200 or body.get("success") is not True or not isinstance(chat, str) or not chat:
            raise BadAnswer("qwen answered %d to the new chat" % status)
        return chat

    async def read_chat(self, chat: str) -> dict | None:
        """The chat's record (`data`), or None when it could not be read."""
        status, body = await self.call("GET", "/api/v2/chats/%s" % quote(chat, safe=""), referer="/c/" + chat)
        data = body.get("data")
        if status != 200 or body.get("success") is not True or not isinstance(data, dict):
            return None
        return data

    async def credits(self) -> int | None:
        """The account's qwen credits now (`totalCredits`), or None when they could not be read."""
        status, body = await self.call("GET", CREDITS_URL + "?" + "&".join(
            "%s=%s" % (k, quote(v, safe="")) for k, v in CREDITS_QUERY.items()))
        total = (body.get("data") or {}).get("totalCredits") if isinstance(body.get("data"), dict) else None
        if status != 200 or type(total) is not int or total < 0:
            return None
        return total


# ---- the chat record --------------------------------------------------------------------------------------------
def messages(chat: dict) -> list[dict]:
    """The chat's messages, oldest first."""
    inner = chat.get("chat") if isinstance(chat.get("chat"), dict) else {}
    found = inner.get("messages")
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


def has_user_message(chat: dict) -> bool:
    return any(m.get("role") == "user" for m in messages(chat))


def assistant(chat: dict) -> dict | None:
    """The last assistant message, or None."""
    found = [m for m in messages(chat) if m.get("role") == "assistant"]
    return found[-1] if found else None


def media_link(text) -> str | None:
    """`text` when it is a link on qwen's result CDN."""
    return text if isinstance(text, str) and any(text.startswith("https://%s/" % h) for h in MEDIA_HOSTS) else None


def _contents(message: dict | None) -> list[str]:
    """Every content string of a message: its `content`, then each `content_list` item's (an image chat keeps its
    link there and leaves `content` empty)."""
    message = message or {}
    items = message.get("content_list") if isinstance(message.get("content_list"), list) else []
    found = [message.get("content")] + [i.get("content") for i in items if isinstance(i, dict)]
    return [c for c in found if isinstance(c, str) and c]


def links_of(message: dict | None) -> list[str]:
    """The result links a message carries, in order, each once."""
    return list(dict.fromkeys(c for c in _contents(message) if media_link(c)))


def text_of(message: dict | None) -> str:
    """What a message says in words (no link)."""
    return "\n".join(c for c in _contents(message) if not media_link(c)).strip()


def task_of(message: dict | None) -> str | None:
    """The video task a message names (`extra.wanx.task_id`)."""
    extra = (message or {}).get("extra")
    wanx = extra.get("wanx") if isinstance(extra, dict) else None
    task = wanx.get("task_id") if isinstance(wanx, dict) else None
    return task if isinstance(task, str) and task else None
