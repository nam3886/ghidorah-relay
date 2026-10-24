"""gemini credentials: a Google cookie session, imported from a Cookie-Editor export of gemini.google.com.

Accepted input: a JSON list of cookies, `{"url": ..., "cookies": [...], "email": ...}`, or the sealed shape itself.
Only cookies of google.com and its subdomains are kept (the .google.com session set and the .gemini.google.com
cookies); `__Secure-1PSID` must be there. Everything becomes ONE sealed shape:
`{"cookies": [{name, value, domain, path}], "rotated_at": <unix s>, "email": <optional>}`. A domain that starts with
"." is a domain cookie, one without is host-only. `rotated_at` is when the provider last rotated the session (0 on
import: the first use rotates). `email` (optional, lowercased) names the Google account to use in a session signed in
to several: the account check finds its `/u/N` index (FINDINGS 6); without one the jobs go to `/u/0`. The engine writes
the rotated jar back in this same shape (`RunCtx.renew_credential`). No function here logs or raises with a cookie
value.
"""

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from relay_core.providers import ParsedCredential
from relay_gemini.constants import GOOGLE_DOMAIN, SESSION_COOKIE

MAX_COOKIES = 300   # far above a real export (about 20 Google cookies); a longer list is not a cookie export

# RFC 6265 cookie value characters: the value can never break out of the `Cookie:` header it is sent in.
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_EMAIL = re.compile(r"[^@\s\"]+@[^@\s\"]+\.[^@\s\"]+")


def on_google(domain: str) -> bool:
    host = domain.lstrip(".").lower()
    return host == GOOGLE_DOMAIN or host.endswith("." + GOOGLE_DOMAIN)


def _cookie(raw: object, default_domain: str | None) -> dict:
    """One cookie as {name, value, domain, path}. ValueError (never repeating a value) when it is not a cookie."""
    if not isinstance(raw, Mapping):
        raise ValueError("a cookie must be an object")
    name, value, domain = raw.get("name"), raw.get("value"), raw.get("domain") or default_domain
    path = raw.get("path") or "/"
    if not isinstance(name, str) or not name or not isinstance(value, str) or not isinstance(domain, str) \
            or not domain or not isinstance(path, str):
        raise ValueError("a cookie needs a name, a value and a domain")
    if not _COOKIE_NAME.fullmatch(name) or not _COOKIE_VALUE.fullmatch(value) or not path.startswith("/"):
        raise ValueError("a cookie holds characters a cookie cannot carry")
    domain = domain.lower()
    host_only = raw.get("hostOnly")
    if host_only is True:
        domain = domain.lstrip(".")
    elif host_only is False and not domain.startswith("."):
        domain = "." + domain
    return {"name": name, "value": value, "domain": domain, "path": path}


def _cookies(data: object) -> tuple[list[dict], float]:
    default, rotated_at = None, 0.0
    if isinstance(data, Mapping):
        url = data.get("url")
        default = (urlsplit(url).hostname or None) if isinstance(url, str) else None
        stamp = data.get("rotated_at")
        if isinstance(stamp, int | float) and not isinstance(stamp, bool) and stamp >= 0:
            rotated_at = float(stamp)
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return [_cookie(c, default) for c in data], rotated_at


def _email(data: object) -> str | None:
    """The optional `email` of an import or a sealed credential, lowercased. ValueError when it is not an address."""
    raw = data.get("email") if isinstance(data, Mapping) else None
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > 254 or not _EMAIL.fullmatch(raw.strip()):
        raise ValueError("the email is not an email address")
    return raw.strip().lower()


def dump(cookies: list[dict], rotated_at: float, email: str | None = None) -> bytes:
    """The sealed shape."""
    data = {"cookies": cookies, "rotated_at": rotated_at, **({"email": email} if email else {})}
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


def parse_credential(raw: bytes) -> ParsedCredential:
    """Any accepted shape -> the sealed shape. `ValueError` with a fixed reason otherwise (it never holds a value)."""
    text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes | bytearray) else str(raw).strip()
    if not text:
        raise ValueError("empty credential")
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("the credential is not a JSON cookie export") from None
    cookies, rotated_at = _cookies(data)
    email = _email(data)
    cookies = [c for c in cookies if on_google(c["domain"])]
    if not any(c["name"] == SESSION_COOKIE and c["value"] for c in cookies):
        raise ValueError("the credential has no google.com %s cookie" % SESSION_COOKIE)
    # the hint is stored in plain text: no part of the session goes into it (the first check names the account)
    return ParsedCredential(blob=dump(cookies, rotated_at, email), hint="gemini")


def _sealed(blob: bytes):
    try:
        data = json.loads(blob)
        return _cookies(data), _email(data)
    except (ValueError, TypeError):
        raise ValueError("the sealed gemini credential is not readable") from None


def jar_of(blob: bytes) -> tuple[list[dict], float]:
    """(cookies, rotated_at) of a sealed credential. ValueError (no value in it) when the blob is not one."""
    return _sealed(blob)[0]


def email_of(blob: bytes) -> str | None:
    """The Google account a sealed credential names (None: none, the jobs go to `/u/0`)."""
    return _sealed(blob)[1]
