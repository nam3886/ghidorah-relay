"""qwen credentials: a cookie jar of chat.qwen.ai (a Cookie-Editor export), and the anti-bot pair minted for it.

Accepted input: a JSON list of cookies, `{"url": ..., "cookies": [...]}`, or the sealed shape itself. Only cookies of
qwen.ai and its subdomains are kept; `refresh_token` must be there. Everything becomes ONE sealed shape:
`{"cookies": [{name, value, domain, path}], "bx": {header: value} | null, "minted_at": <unix s>}`.

`bx` is what a browser mint kept from the page's own (aborted) generate: `bx-ua`, `bx-umidtoken`, `bx-v`, the
browser's user agent and client hints and the web app version (`BX_HEADERS`). A mint also replaces `cookies` with the
browser's cookies of the same session, since the pair is sent with them (FINDINGS 2). `minted_at` is when that mint
was made (0: never); a pair found stale is dropped by writing `bx: null` with the same `minted_at`. The engine writes
the shape back through `RunCtx.renew_credential`. No function here logs or raises with a cookie or header value.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from relay_core.providers import ParsedCredential
from relay_qwen.constants import QWEN_DOMAIN, REFRESH_COOKIE

MAX_COOKIES = 300   # far above a real export (about 25 qwen.ai cookies); a longer list is not a cookie export
# The headers a mint keeps, all sent again with the paid call.
BX_HEADERS = ("bx-ua", "bx-umidtoken", "bx-v", "user-agent", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
              "version")
PAIR = ("bx-ua", "bx-umidtoken")       # a pair without both is no pair

# RFC 6265 cookie value characters: the value can never break out of the `Cookie:` header it is sent in.
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
_COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_HEADER_VALUE = re.compile(r"[\x20-\x7e]+")      # printable ASCII: no line break can ride in a header


@dataclass
class Cred:
    """One account's credential: its cookies, keyed (domain, path, name), the minted pair (None: none) and when it was
    minted."""
    cookies: dict[tuple[str, str, str], str]
    bx: dict[str, str] | None = None
    minted_at: float = 0.0

    def blob(self) -> bytes:
        return dump([{"name": n, "value": v, "domain": d, "path": p} for (d, p, n), v in self.cookies.items()],
                    self.bx, self.minted_at)

    def copy(self) -> "Cred":
        return Cred(dict(self.cookies), dict(self.bx) if self.bx else None, self.minted_at)


def on_qwen(domain: str) -> bool:
    host = domain.lstrip(".").lower()
    return host == QWEN_DOMAIN or host.endswith("." + QWEN_DOMAIN)


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


def _bx(raw: object) -> dict[str, str] | None:
    """The minted headers, or None. ValueError (no value in it) when they are not a clean header set."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("the minted headers must be an object")
    out = {}
    for name, value in raw.items():
        if name not in BX_HEADERS or not isinstance(value, str) or not _HEADER_VALUE.fullmatch(value):
            raise ValueError("the minted headers hold a header that is not kept or not clean")
        out[name] = value
    if not all(out.get(h) for h in PAIR):
        raise ValueError("the minted headers lack the bx pair")
    return out


def _parts(data: object) -> tuple[list[dict], dict | None, float]:
    default, bx, minted_at = None, None, 0.0
    if isinstance(data, Mapping):
        url = data.get("url")
        default = (urlsplit(url).hostname or None) if isinstance(url, str) else None
        stamp = data.get("minted_at")
        if isinstance(stamp, int | float) and not isinstance(stamp, bool) and stamp >= 0:
            minted_at = float(stamp)
        bx = _bx(data.get("bx"))
        data = data.get("cookies")
    if not isinstance(data, list) or not data:
        raise ValueError("no cookies")
    if len(data) > MAX_COOKIES:
        raise ValueError("too many cookies")
    return [_cookie(c, default) for c in data], bx, minted_at


def dump(cookies: list[dict], bx: Mapping[str, str] | None, minted_at: float) -> bytes:
    """The sealed shape."""
    return json.dumps({"cookies": cookies, "bx": dict(bx) if bx else None, "minted_at": minted_at},
                      separators=(",", ":"), ensure_ascii=False).encode()


def parse_credential(raw: bytes) -> ParsedCredential:
    """Any accepted shape -> the sealed shape. `ValueError` with a fixed reason otherwise (it never holds a value)."""
    text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes | bytearray) else str(raw).strip()
    if not text:
        raise ValueError("empty credential")
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("the credential is not a JSON cookie export") from None
    cookies, bx, minted_at = _parts(data)
    cookies = [c for c in cookies if on_qwen(c["domain"])]
    if not any(c["name"] == REFRESH_COOKIE and c["value"] for c in cookies):
        raise ValueError("the credential has no qwen.ai %s cookie" % REFRESH_COOKIE)
    # the hint is stored in plain text: no part of the session goes into it (the first check names the account)
    return ParsedCredential(blob=dump(cookies, bx, minted_at), hint="qwen")


def cred_of(blob: bytes) -> Cred:
    """The `Cred` of a sealed credential. ValueError (no value in it) when the blob is not one."""
    try:
        cookies, bx, minted_at = _parts(json.loads(blob))
    except (ValueError, TypeError):
        raise ValueError("the sealed qwen credential is not readable") from None
    return Cred({(c["domain"], c["path"], c["name"]): c["value"] for c in cookies}, bx, minted_at)


def merge_blobs(stored: bytes, mine: bytes) -> bytes:
    """`RunCtx.renew_credential`'s merge: another run wrote first. The credential of the newer mint wins whole; at the
    same mint, mine (a drop of that pair, or the same write) wins."""
    a, b = cred_of(stored), cred_of(mine)
    return (b if b.minted_at >= a.minted_at else a).blob()
