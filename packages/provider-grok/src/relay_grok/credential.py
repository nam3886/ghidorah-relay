"""grok credentials: a cookie jar of grok.com, read from any accepted shape into the one sealed shape.

Accepted input: a Cookie-Editor export (a JSON list of cookies), `{"url": ..., "cookies": <list>}` (a cookie without a
domain takes the url's host), a `Cookie:` header (`sso=...; sso-rw=...`, with or without the `Cookie:` name), or the
sealed shape itself. Only cookies of grok.com (`grok.com`, `.grok.com`) are kept, and of those only the ones a
`Cookie:` header can carry (an unclean `sso` refuses the import); `sso` must be there. Everything becomes ONE sealed
shape: `{"cookies": <list of {name, value, domain, path}>}`. Nothing is ever written back (grok's `sso` rotation is not
measured). No function here logs or raises with a cookie value.
"""

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from relay_core.providers import ParsedCredential
from relay_grok.constants import COOKIE_DOMAIN, MAX_COOKIES, SITE_HOST, SSO_COOKIE

# RFC 6265 cookie characters: a value can never break out of the `Cookie:` header it is sent in.
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_HEADER_NAME = re.compile(r"cookie\s*:", re.IGNORECASE)


def on_site(domain: str) -> bool:
    """Is the cookie grok.com's own (`grok.com` or `.grok.com`)?"""
    return domain.lstrip(".").lower() == SITE_HOST


def _cookie(raw: object, default_domain: str | None) -> dict:
    """One cookie as {name, value, domain, path}. ValueError (never repeating a value) when it is not a cookie."""
    if not isinstance(raw, Mapping):
        raise ValueError("a cookie must be an object")
    name, value, domain = raw.get("name"), raw.get("value"), raw.get("domain") or default_domain
    path = raw.get("path") or "/"
    if not isinstance(name, str) or not name or not isinstance(value, str) or not isinstance(domain, str) \
            or not domain or not isinstance(path, str):
        raise ValueError("a cookie needs a name, a value and a domain")
    domain = domain.lower()
    host_only = raw.get("hostOnly")
    if host_only is True:
        domain = domain.lstrip(".")
    elif host_only is False and not domain.startswith("."):
        domain = "." + domain
    return {"name": name, "value": value, "domain": domain, "path": path}


def _clean(cookie: dict) -> bool:
    return bool(_COOKIE_NAME.fullmatch(cookie["name"])) and bool(_COOKIE_VALUE.fullmatch(cookie["value"])) \
        and cookie["path"].startswith("/")


def _header(text: str) -> list[dict]:
    """The cookies of a `Cookie:` header, all for grok.com."""
    text = _HEADER_NAME.sub("", text, count=1) if _HEADER_NAME.match(text) else text
    cookies = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        if not sep or not name.strip():
            raise ValueError("a Cookie header holds name=value pairs")
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": COOKIE_DOMAIN,
                        "path": "/"})
    return cookies


def _listed(data: object) -> list[dict]:
    """The cookies of a JSON export: a bare list, `{url, cookies}` or the sealed shape."""
    default = None
    if isinstance(data, Mapping):
        url = data.get("url")
        try:            # a parser's message may repeat the url's authority (a login in it): never let it through
            default = (urlsplit(url).hostname or None) if isinstance(url, str) else None
        except ValueError:
            raise ValueError("the url is not a URL") from None
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return [_cookie(raw, default) for raw in data]


def _kept(cookies: list[dict]) -> list[dict]:
    """grok.com's cookies, each clean. An unclean `sso` refuses the whole credential; any other unclean cookie is
    dropped."""
    kept = [c for c in cookies if on_site(c["domain"])]
    if not all(_clean(c) for c in kept if c["name"] == SSO_COOKIE):
        raise ValueError("the grok.com sso cookie holds characters a cookie cannot carry")
    return [c for c in kept if _clean(c)]


def parse_credential(raw: bytes) -> ParsedCredential:
    """Any accepted shape -> the sealed shape. `ValueError` with a fixed reason otherwise (it never holds a value)."""
    text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes | bytearray) else str(raw).strip()
    if not text:
        raise ValueError("empty credential")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("the credential is not a JSON cookie export") from None
        cookies = _listed(data)
    else:
        cookies = _header(text)
        if len(cookies) > MAX_COOKIES:
            raise ValueError("too many cookies")
    kept = _kept(cookies)
    if not any(c["value"] for c in kept if c["name"] == SSO_COOKIE):
        raise ValueError("the credential has no grok.com %s cookie" % SSO_COOKIE)
    blob = json.dumps({"cookies": kept}, separators=(",", ":"), ensure_ascii=False).encode()
    # the hint is stored in plain text: no part of a cookie goes into it (the first check names the account)
    return ParsedCredential(blob=blob, hint="grok")


def jar_of(blob: bytes) -> dict[str, str]:
    """name -> value of the sealed jar (the last one wins). ValueError (no value in it) when the blob is not one."""
    try:
        return {c["name"]: c["value"] for c in _kept(_listed(json.loads(blob)))}
    except (ValueError, TypeError, KeyError):
        raise ValueError("the sealed grok credential is not readable") from None
