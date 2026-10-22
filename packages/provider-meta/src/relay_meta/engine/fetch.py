"""Fetch: the bytes of one meta image. An async generator, as the core expects.

`Output.ref` is the final signed fbcdn link the answer showed (`scontent.xx.fbcdn.net/v/t39.105495-1/..._n.webp`); it
carries no cookie. `net.stream_download` takes it direct first and through the account's proxy when the direct route
fails, on fbcdn.net hosts only.
"""

import contextlib
from collections.abc import AsyncIterator
from urllib.parse import quote

from relay_core.net import stream_download, system_resolver
from relay_meta.constants import MEDIA_HOSTS

MEDIA_MAX_BYTES = 64 * 2**20     # a 2048x1152 webp is well under 1 MB
MEDIA_DEADLINE_S = 120


def proxy_url(proxy) -> str | None:
    """`http://user:pass@host:port` for an HTTP client; None without a proxy. The user and password are quoted."""
    if proxy is None:
        return None
    auth = ""
    if proxy.auth:
        user, _, secret = proxy.auth.partition(":")
        auth = quote(user, safe="") + (":" + quote(secret, safe="") if secret else "") + "@"
    return "http://%s%s:%d" % (auth, proxy.host, proxy.port)


async def fetch(ctx, out, *, media_transport=None, media_resolver=system_resolver) -> AsyncIterator[bytes]:
    """The bytes of `out`, streamed. `media_transport` and `media_resolver` are the test seams of the download."""
    async with contextlib.aclosing(stream_download(
            out.ref, hosts=MEDIA_HOSTS, proxy=proxy_url(ctx.account.proxy), max_bytes=MEDIA_MAX_BYTES,
            deadline_s=MEDIA_DEADLINE_S, resolver=media_resolver, transport=media_transport)) as parts:
        async for part in parts:
            yield part
