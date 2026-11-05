"""meta credentials: the cookie jar shapes the importer accepts, and the one shape that is sealed.

Accepted input: a Cookie-Editor or EditThisCookie export (a JSON list of cookies), a Playwright `storage_state`
(`{"cookies": [...], "origins": [...]}`) and a bare `Cookie:` header string (`a=b; c=d`). All of them become ONE sealed
shape, the Cookie-Editor shape `{"cookies": [{name, value, domain, path, secure, httpOnly, sameSite, session,
expirationDate}]}`, which `relay_browser.ui.cookies_for_playwright` reads. No function here logs or raises with a
cookie value.
"""

import hashlib
import json
import re
from collections.abc import Mapping

from relay_core.providers import ParsedCredential
from relay_meta.constants import COOKIE_DOMAIN, SESSION_COOKIES, SITE_HOST

MAX_COOKIES = 200   # far above a real meta.ai jar; a longer list is not a cookie export

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
    # Cookie-Editor says `expirationDate`, a Playwright state `expires` (-1 = session), EditThisCookie `expiry` too.
    expires = raw.get("expirationDate", raw.get("expires", raw.get("expiry")))
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
    """The cookie list of a JSON export: a bare list, or a storage state's `cookies`."""
    if isinstance(data, Mapping):
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return data


def on_site(domain: str) -> bool:
    """Is the cookie for meta.ai (the site or a subdomain), so the browser sends it there?"""
    host = domain.lstrip(".").lower()
    return host == SITE_HOST or host.endswith("." + SITE_HOST)


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
        if len(cookies) > MAX_COOKIES:
            raise ValueError("too many cookies")
    if not any(c["name"] in SESSION_COOKIES and c["value"] and on_site(c["domain"]) for c in cookies):
        raise ValueError("the credential has no meta.ai session cookie (%s)" % " or ".join(SESSION_COOKIES))
    blob = json.dumps({"cookies": cookies}, separators=(",", ":"), ensure_ascii=False).encode()
    # the hint is stored in plain text: no part of a session cookie goes into it (the admin names the account)
    return ParsedCredential(blob=blob, hint="meta")


# ---- the sealed jar at run time ---------------------------------------------------------------------------------
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


def jar_of(blob: bytes) -> dict[str, str]:
    """name -> value of the sealed jar (the last one wins). ValueError (no value in it) when the blob is not one."""
    try:
        return {c["name"]: c["value"] for c in json.loads(blob)["cookies"]}
    except (ValueError, TypeError, KeyError):
        raise ValueError("the sealed meta credential is not readable") from None


def renewable(name: object, value: object) -> bool:
    """Can a cookie from a Set-Cookie go into the sealed jar (and so into a `Cookie:` header)?"""
    return isinstance(name, str) and isinstance(value, str) and bool(value) and bool(_COOKIE_NAME.fullmatch(name)) \
        and bool(_COOKIE_VALUE.fullmatch(value))


def with_cookies(blob: bytes, updates: Mapping[str, str]) -> bytes:
    """The sealed jar with these cookies set (a value meta rotated), a new name added for meta.ai, same shape."""
    data = json.loads(blob)
    cookies = data["cookies"]
    for name, value in updates.items():
        hits = [c for c in cookies if c["name"] == name]
        for c in hits:
            c["value"] = value
        if not hits:
            cookies.append({"name": name, "value": value, "domain": COOKIE_DOMAIN})
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


def viewer_hash(viewer_id: str) -> str:
    """The account identity kept for a viewerId: a hash, never the id."""
    return hashlib.sha256(viewer_id.encode()).hexdigest()[:32]
