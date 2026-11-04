"""gpt credentials: a cookie jar of chatgpt.com (a Cookie-Editor export), and its session write-back.

Accepted input: a JSON list of cookies, `{"url": ..., "cookies": <list>}` (a cookie without a domain takes the url's
host), or the sealed shape itself. Only cookies of chatgpt.com and its subdomains are kept; a session part must be
there: `__Secure-next-auth.session-token` itself, or its split parts `.0`, `.1`, ... Everything becomes ONE sealed
shape: `{"cookies": <list of {name, value, domain, path}>, "renewed_at": <unix s>}`.

chatgpt.com re-sets the session cookie on use, and may change how it is split (`.0/.1` -> a single `.0`, or the bare
name). The engine writes the new value back through `RunCtx.renew_credential` with `with_session`, which replaces the
WHOLE set of session parts, so a stale `.1` never survives next to a new `.0`. `renewed_at` is when that set was last
written (0: as imported). Two writes that raced are merged by `merge_blobs`: the session set of the newer one, whole.
No function here logs or raises with a cookie value.
"""

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from relay_core.providers import ParsedCredential
from relay_gpt.constants import SESSION_COOKIE, SITE_HOST

MAX_COOKIES = 300   # far above a real export (a chatgpt.com jar holds about 20); a longer list is not a cookie export

# RFC 6265 cookie characters: a value can never break out of the `Cookie:` header it is sent in.
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_SESSION_PART = re.compile(re.escape(SESSION_COOKIE) + r"(\.\d+)?")


def on_site(domain: str) -> bool:
    """Is the cookie for chatgpt.com (the site or a subdomain)?"""
    host = domain.lstrip(".").lower()
    return host == SITE_HOST or host.endswith("." + SITE_HOST)


def is_session_part(name: str) -> bool:
    return _SESSION_PART.fullmatch(name) is not None


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


def _parts(data: object) -> tuple[list[dict], float]:
    """The cookies (chatgpt.com's only, each clean) and `renewed_at` of any accepted shape."""
    default, renewed_at = None, 0.0
    if isinstance(data, Mapping):
        url = data.get("url")
        default = (urlsplit(url).hostname or None) if isinstance(url, str) else None
        stamp = data.get("renewed_at", 0.0)
        if not isinstance(stamp, int | float) or isinstance(stamp, bool) or stamp < 0:
            raise ValueError("renewed_at is not a time")
        renewed_at = float(stamp)
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    kept = [c for c in (_cookie(raw, default) for raw in data) if on_site(c["domain"])]
    if not all(_clean(c) for c in kept):
        raise ValueError("a chatgpt.com cookie holds characters a cookie cannot carry")
    return kept, renewed_at


def _dump(cookies: list[dict], renewed_at: float) -> bytes:
    return json.dumps({"cookies": cookies, "renewed_at": renewed_at}, separators=(",", ":"),
                      ensure_ascii=False).encode()


def parse_credential(raw: bytes) -> ParsedCredential:
    """Any accepted shape -> the sealed shape. `ValueError` with a fixed reason otherwise (it never holds a value)."""
    text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes | bytearray) else str(raw).strip()
    if not text:
        raise ValueError("empty credential")
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("the credential is not a JSON cookie export") from None
    kept, renewed_at = _parts(data)
    if not any(c["value"] for c in kept if is_session_part(c["name"])):
        raise ValueError("the credential has no chatgpt.com %s cookie" % SESSION_COOKIE)
    # the hint is stored in plain text: no part of the session goes into it (the first check names the account)
    return ParsedCredential(blob=_dump(kept, renewed_at), hint="gpt")


def _sealed(blob: bytes) -> tuple[list[dict], float]:
    try:
        return _parts(json.loads(blob))
    except (ValueError, TypeError):
        raise ValueError("the sealed gpt credential is not readable") from None


def jar_of(blob: bytes) -> dict[str, str]:
    """name -> value of the sealed jar (the last one wins). ValueError (no value in it) when the blob is not one."""
    return {c["name"]: c["value"] for c in _sealed(blob)[0]}


def session_parts(jar: Mapping[str, str]) -> dict[str, str]:
    """The session cookie of a jar: the bare name and every `.N` part."""
    return {k: v for k, v in jar.items() if is_session_part(k)}


def with_session(blob: bytes, parts: Mapping[str, str], renewed_at: float) -> bytes:
    """The sealed jar with its session set replaced by `parts`, ALL old parts dropped, stamped `renewed_at`. The new
    parts take the domain and path the old session had (chatgpt.com, / when it had none)."""
    cookies, _ = _sealed(blob)
    old = next((c for c in cookies if is_session_part(c["name"])), None)
    domain, path = (old["domain"], old["path"]) if old else (SITE_HOST, "/")
    kept = [c for c in cookies if not is_session_part(c["name"])]
    return _dump(kept + [{"name": n, "value": v, "domain": domain, "path": path} for n, v in parts.items()],
                 float(renewed_at))


def _renewed_at(blob: bytes) -> float:
    return _sealed(blob)[1]


def merge_blobs(stored: bytes, mine: bytes) -> bytes:
    """`RunCtx.renew_credential`'s merge (another run wrote first): the session set of the blob renewed later, whole,
    with its `renewed_at` (a tie goes to `mine`); every other cookie from `stored`."""
    newer = mine if _renewed_at(mine) >= _renewed_at(stored) else stored
    cookies, renewed_at = _sealed(newer)
    others = [c for c in _sealed(stored)[0] if not is_session_part(c["name"])]
    return _dump(others + [c for c in cookies if is_session_part(c["name"])], renewed_at)
