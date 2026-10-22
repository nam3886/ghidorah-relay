"""HTTP to vibes.ai over httpx, bound to one account's session cookie and proxy.

vibes.ai is a plain JSON API that does not check the client's TLS fingerprint, so the async httpx client that
relay-core already carries is enough (no Chrome impersonation, no thread hop). The transport is a seam: tests pass an
`httpx.MockTransport`, nothing here opens a socket on import. No call follows a redirect: the cookie must not travel to
wherever an answer points, and a 3xx is read as a failed read.
"""

from urllib.parse import quote

import httpx

from relay_core.providers import Proxy
from relay_vibes.constants import ACK_COOKIE, API_TIMEOUT_S, BASE_URL, SESSION_COOKIE, USER_AGENT
from relay_vibes.credential import session_of


def proxy_url(proxy: Proxy | None) -> str | None:
    """`http://user:pass@host:port` for httpx; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


def headers_for(session: str) -> dict[str, str]:
    """What the web app sends with every API call. The cookie value is never logged."""
    return {"Cookie": "%s; %s=%s" % (ACK_COOKIE, SESSION_COOKIE, session), "User-Agent": USER_AGENT,
            "Origin": BASE_URL, "Referer": BASE_URL + "/", "Accept": "application/json"}


def client_for(account, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """A client for one run of one account: its session cookie, its proxy, no redirects, no environment proxies."""
    return httpx.AsyncClient(base_url=BASE_URL, headers=headers_for(session_of(account.credential)),
                             proxy=None if transport else proxy_url(account.proxy), transport=transport,
                             timeout=API_TIMEOUT_S, follow_redirects=False, trust_env=False)
