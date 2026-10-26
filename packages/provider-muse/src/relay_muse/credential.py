"""muse credentials: the cookie jar shapes the importer accepts, and the one shape that is sealed.

Accepted input, as for the other cookie providers: a Cookie-Editor or EditThisCookie export (a JSON list), a Playwright
`storage_state` (an object with a `cookies` list) and a bare `Cookie:` header string (`a=b; c=d`). It must carry a
non-empty `hatch_sess` for muse.ai. Sealed shape: an object whose `cookies` list holds {name, value, domain}, only the
muse.ai cookies (an export may hold others). Every value is one a `Cookie:` header can carry. No function here logs or
raises with a value.
"""

import json
import re
from collections.abc import Mapping

from relay_core.providers import ParsedCredential
from relay_muse.constants import SESSION_COOKIE, SITE_HOST

MAX_COOKIES = 200   # far above a real muse.ai jar; a longer list is not a cookie export

# RFC 6265 cookie-octets: no space, quote, comma, semicolon or backslash, so a value never breaks out of its header
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


def on_site(domain: str) -> bool:
    """Is the cookie for muse.ai (the site or a subdomain)?"""
    host = domain.lstrip(".").lower()
    return host == SITE_HOST or host.endswith("." + SITE_HOST)


def _cookie(raw: object, default_domain: str | None) -> dict:
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
    if isinstance(data, Mapping):
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return [_cookie(c, None) for c in data]


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
    kept = [c for c in cookies if on_site(c["domain"])]
    if not any(c["name"] == SESSION_COOKIE and c["value"] for c in kept):
        raise ValueError("the credential has no muse.ai %s cookie" % SESSION_COOKIE)
    if not all(_COOKIE_NAME.fullmatch(c["name"]) and _COOKIE_VALUE.fullmatch(c["value"]) for c in kept):
        raise ValueError("a muse.ai cookie holds characters a cookie cannot carry")
    blob = json.dumps({"cookies": kept}, separators=(",", ":"), ensure_ascii=False).encode()
    # the hint is stored in plain text: no part of a cookie goes into it (the admin names the account)
    return ParsedCredential(blob=blob, hint="muse")


def jar_of(blob: bytes) -> dict[str, str]:
    """name -> value of the sealed jar (the last one wins). ValueError (no value in it) when the blob is not one."""
    try:
        cookies = json.loads(blob)["cookies"]
        return {c["name"]: c["value"] for c in cookies}
    except (ValueError, TypeError, KeyError):
        raise ValueError("the sealed muse credential is not readable") from None


def renewable(name: object, value: object) -> bool:
    """Can a cookie from a Set-Cookie go into the sealed jar (and so into a `Cookie:` header)?"""
    return isinstance(name, str) and isinstance(value, str) and bool(value) and bool(_COOKIE_NAME.fullmatch(name)) \
        and bool(_COOKIE_VALUE.fullmatch(value))


def with_cookies(blob: bytes, updates: Mapping[str, str]) -> bytes:
    """The sealed jar with these cookies set (a value muse rotated), new names added for muse.ai, in the same shape."""
    data = json.loads(blob)
    cookies = data["cookies"]
    for name, value in updates.items():
        hits = [c for c in cookies if c["name"] == name]
        for c in hits:
            c["value"] = value
        if not hits:
            cookies.append({"name": name, "value": value, "domain": "." + SITE_HOST})
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
