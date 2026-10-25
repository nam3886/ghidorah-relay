"""vibes credentials: the shapes the importer accepts, and the one shape that is sealed.

Accepted input: a Cookie-Editor export (a JSON list of cookies, or `{"url": ..., "cookies": [...]}`), a bare `Cookie:`
header string (`a=b; c=d`, with or without the `Cookie:` name) and `{"meta_session": "..."}`. Each must carry a
non-empty `meta_session` cookie for vibes.ai. All of them become ONE sealed shape:
`{"meta_session": "...", "cookies": [{name, value, domain}]}`. Only `meta_session` is sent to vibes; the other cookies
(an export may hold auth.meta.com ones) are kept as given and not used. No function here logs or raises with a cookie
value.
"""

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from relay_core.providers import ParsedCredential
from relay_vibes.constants import SESSION_COOKIE, SITE_HOST

MAX_COOKIES = 200   # far above a real vibes.ai export; a longer list is not a cookie export

# The characters a cookie value may hold (RFC 6265): no space, quote, comma, semicolon or backslash, so the value can
# never break out of the `Cookie:` header it is sent in.
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+")


def _cookie(raw: object, default_domain: str | None) -> dict:
    """One cookie as {name, value, domain}. ValueError (never repeating a value) when it is not a cookie."""
    if not isinstance(raw, Mapping):
        raise ValueError("a cookie must be an object")
    name, value = raw.get("name"), raw.get("value")
    domain = raw.get("domain") or default_domain
    if not isinstance(name, str) or not name or not isinstance(value, str) or not isinstance(domain, str) or not domain:
        raise ValueError("a cookie needs a name, a value and a domain")
    return {"name": name, "value": value, "domain": domain}


def _header_cookies(text: str) -> list[dict]:
    if text[:7].lower() == "cookie:":
        text = text[7:]
    cookies = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        if not sep or not name.strip():
            raise ValueError("a Cookie header holds name=value pairs")
        cookies.append(_cookie({"name": name.strip(), "value": value.strip()}, SITE_HOST))
    return cookies


def _json_cookies(data: object) -> list[dict]:
    """The cookies of a JSON credential: a bare list, `{url, cookies}`, or `{"meta_session": ...}`."""
    default = None
    if isinstance(data, Mapping):
        if SESSION_COOKIE in data:
            return [_cookie({"name": SESSION_COOKIE, "value": data[SESSION_COOKIE]}, SITE_HOST)]
        url = data.get("url")
        default = (urlsplit(url).hostname or None) if isinstance(url, str) else None
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return [_cookie(c, default) for c in data]


def on_site(domain: str) -> bool:
    """Is the cookie for vibes.ai (the site or a subdomain)?"""
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
        cookies = _json_cookies(data)
    else:
        cookies = _header_cookies(text)
        if len(cookies) > MAX_COOKIES:
            raise ValueError("too many cookies")
    session = next((c["value"] for c in cookies
                    if c["name"] == SESSION_COOKIE and c["value"] and on_site(c["domain"])), None)
    if session is None:
        raise ValueError("the credential has no vibes.ai %s cookie" % SESSION_COOKIE)
    if not _COOKIE_VALUE.fullmatch(session):
        raise ValueError("the %s cookie holds characters a cookie cannot carry" % SESSION_COOKIE)
    blob = json.dumps({SESSION_COOKIE: session, "cookies": cookies}, separators=(",", ":"), ensure_ascii=False).encode()
    # the hint is stored in plain text: no part of the session goes into it (the admin names the account)
    return ParsedCredential(blob=blob, hint="vibes")


def session_of(blob: bytes) -> str:
    """The `meta_session` value of a sealed credential. ValueError (no value in it) when the blob is not one."""
    try:
        session = json.loads(blob)[SESSION_COOKIE]
    except (ValueError, TypeError, KeyError):
        raise ValueError("the sealed vibes credential is not readable") from None
    if not isinstance(session, str) or not _COOKIE_VALUE.fullmatch(session):
        raise ValueError("the sealed vibes credential is not readable")
    return session


def with_session(blob: bytes, session: str) -> bytes:
    """The sealed credential `blob` with a renewed `meta_session` (vibes rotated it), in the same shape."""
    data = json.loads(blob)
    data[SESSION_COOKIE] = session
    for c in data.get("cookies") or []:
        if c.get("name") == SESSION_COOKIE and on_site(str(c.get("domain") or "")):
            c["value"] = session
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


def renewable(session: object) -> bool:
    """Can a `meta_session` value from a Set-Cookie go into the sealed credential (and so into a `Cookie:` header)?"""
    return isinstance(session, str) and bool(_COOKIE_VALUE.fullmatch(session))
