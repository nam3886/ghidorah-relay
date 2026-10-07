"""dola credentials: what the importer accepts, the one shape that is sealed, and the readers the engine uses.

Accepted input (D36): a Cookie-Editor export (a JSON list of cookies), a Playwright `storage_state`
(`{"cookies": [...], "origins": [...]}`), a bare `Cookie:` header string (`a=b; c=d`), and dola_v4's jar JSON (one jar
`{"cookies": [...], "proxy": ...}`, a bare list, `{"jars": [jar]}` or a list holding one jar). All of them become ONE
sealed shape, the Cookie-Editor shape `{"cookies": [{name, value, domain, path, secure, httpOnly, sameSite, session,
expirationDate}]}`, which `relay_browser.ui.cookies_for_playwright` and `jar_from_blob` both read.

The jar's own `proxy` is dropped: a proxy belongs to the account row, not to the sealed credential (the importer takes
it as its own field). No function here logs or raises with a cookie value.
"""

import json
from collections.abc import Mapping
from urllib.parse import quote

from relay_core.providers import ParsedCredential, Proxy
from relay_dola.constants import COOKIE_DOMAIN, IDENTITY_COOKIES, SESSION_COOKIE

IDENTITY_COOKIE_NAMES = IDENTITY_COOKIES
MAX_COOKIES = 200   # a real dola jar holds about 20; a bigger list is not a cookie export

_EDITOR_SAMESITE = {"none": "no_restriction", "no_restriction": "no_restriction", "lax": "lax", "strict": "strict"}


def _cookie(raw: object, *, default_domain: str | None = None) -> dict:
    """One cookie in the Cookie-Editor shape. ValueError (never repeating a value) when it is not a cookie."""
    if not isinstance(raw, Mapping):
        raise ValueError("a cookie must be an object")
    name, value = raw.get("name"), raw.get("value")
    domain = raw.get("domain") or default_domain
    if not isinstance(name, str) or not name or not isinstance(value, str) or not isinstance(domain, str) or not domain:
        raise ValueError("a cookie needs a name, a value and a domain")
    out = {"name": name, "value": value, "domain": domain, "path": str(raw.get("path") or "/"),
           "secure": bool(raw.get("secure")), "httpOnly": bool(raw.get("httpOnly"))}
    same = _EDITOR_SAMESITE.get(str(raw.get("sameSite") or "").lower())
    if same:
        out["sameSite"] = same
    # Cookie-Editor says `expirationDate` and `session`; a Playwright state says `expires` (-1 = session).
    expires = raw.get("expirationDate", raw.get("expires"))
    if isinstance(expires, bool) or not isinstance(expires, int | float):
        expires = None
    session = bool(raw.get("session")) or expires is None or expires <= 0
    out["session"] = session
    if not session:
        out["expirationDate"] = expires
    return out


def _header_cookies(text: str) -> list[dict]:
    cookies = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        if not sep or not name.strip():
            raise ValueError("a Cookie header holds name=value pairs")
        cookies.append(_cookie({"name": name.strip(), "value": value.strip(), "domain": COOKIE_DOMAIN,
                                "secure": True}))
    return cookies


def _cookie_list(data: object) -> list:
    """The cookie list inside any accepted JSON shape."""
    if isinstance(data, Mapping) and "jars" in data:
        data = data["jars"]
    if isinstance(data, list) and data and all(isinstance(j, Mapping) and "cookies" in j for j in data):
        if len(data) != 1:
            raise ValueError("one account per credential: the file holds %d jars" % len(data))
        data = data[0]
    if isinstance(data, Mapping):
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return data


def parse_credential(raw: bytes) -> ParsedCredential:
    """Any accepted shape -> the sealed shape. `ValueError` with a fixed reason otherwise (it never holds a cookie)."""
    text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes | bytearray) else str(raw).strip()
    if not text:
        raise ValueError("empty credential")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("the credential is not valid JSON") from None
        cookies = [_cookie(c) for c in _cookie_list(data)]
    else:
        cookies = _header_cookies(text)
    by_name = {c["name"]: c for c in cookies}
    if not by_name.get(SESSION_COOKIE, {}).get("value"):
        raise ValueError("the credential has no %s cookie" % SESSION_COOKIE)
    blob = json.dumps({"cookies": cookies}, separators=(",", ":"), ensure_ascii=False).encode()
    # the hint is stored in plain text: no part of a session cookie goes into it (the admin names the account)
    return ParsedCredential(blob=blob, hint="dola")


def jar_from_blob(blob: bytes | str) -> list[dict]:
    """The cookies of a sealed credential, in the Cookie-Editor shape. ValueError when it is not one."""
    try:
        data = json.loads(blob)
    except ValueError:
        raise ValueError("the sealed credential is not JSON") from None
    return [_cookie(c) for c in _cookie_list(data)]


def cookie_header(cookies: list[dict]) -> str:
    """The `Cookie:` header value for HTTP calls."""
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in cookies)


def identity(cookies: list[dict]) -> dict[str, str]:
    """The identity cookies only (the session and the user), to compare what a browser carries with what was sealed."""
    return {c["name"]: c["value"] for c in cookies if c["name"] in IDENTITY_COOKIE_NAMES}


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port` for an HTTP client; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)
