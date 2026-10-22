"""Fetch: the bytes of one output. An async generator, as the core expects.

The link in `Output.ref` is a signed CDN link with no cookie: it is fetched DIRECT first and through the account proxy
only when the direct route fails (a flaky proxy once gave 7 MB in 300 s against 1.5 s direct). Video: with the setting
`dola.master_quality` the H.264 master (~12 Mbit/s, no watermark) replaces the lighter rendition: the cell's own
`fallback_api` gives it, the token in it is decoded (qAAB). Any failure there silently keeps the rendition. Images: the
clean `image_ori_raw` link only, which is what the poll put in `ref`.

Master quality needs the cell's `fallback_api`, which carries a decode seed, so it is NOT kept in `Output.meta`
(no secrets there): the conversation is read again by the output's `meta.conversation_id` and `upstream_id` (the cell's
video id) and the cell is found in it. That read also gives fresh links when the poll's have expired.
"""

import base64
import contextlib
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from relay_core.net import UnsafeUrl, host_allowed, stream_download, system_resolver, url_host
from relay_dola import credential
from relay_dola.engine import resolver
from relay_dola.engine.http import DolaHttp
from relay_dola.engine.resolver import fetch_chain

log = logging.getLogger("relay.dola.fetch")

# Media links are signed CDN links: https on *-flow-image-sign.ibyteimg.com for images, plain http for video and only
# on the video CDN hosts the recorded chains show (v16-dola.dola.com, v19-dola.dola.com); every other link must be
# https. 256 MiB is about 4x the largest master measured (58.6 MB).
MEDIA_HOSTS = ("dola.com", "ibyteimg.com")
MEDIA_HTTP_HOSTS = ("v16-dola.dola.com", "v19-dola.dola.com")
MEDIA_MAX_BYTES = 256 * 2**20
MEDIA_DEADLINE_S = 300      # both routes, a master of ~60 MB over a slow proxy

# The AES key material for qAAB tokens (cracked and verified 260916: 1280x720 H.264 12.2 Mbit/s, the file the vendor's
# own page downloads). MUST be all 64 bytes: a 16-byte cut decodes to garbage.
QAAB_SALT = bytes.fromhex(
    "4dd4c2e6b83162090e52b3c7a6733ba4"
    "1cb2462b829ab58a196b39db57177524"
    "f49baf7f08e8d68d26a72e37c1a95a2f"
    "1f05a51892aef2949732b62a38aadd58")
QAAB_HEADER = b"\xa8\x00\x01\x00"

# Hosts the cell's `fallback_api` may name. The request carries the account's cookie, so a link that points anywhere
# else (a changed answer, a hostile one) is never followed. Seen on every recorded chain: vod-urls-mya.byteintlapi.com.
MASTER_HOST_SUFFIXES = ("dola.com", "byteintlapi.com")


def trusted_master_host(url: str) -> bool:
    """Is `url` an https link on the default port (443) of one of the hosts allowed to receive the account's cookie."""
    return host_allowed(url, MASTER_HOST_SUFFIXES, default_port_only=True)


def walk_field(node, field: str) -> list[str]:
    """Every string value of `field` in a nested structure, JSON strings inside strings included."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == field and isinstance(v, str):
                out.append(v)
            else:
                out += walk_field(v, field)
    elif isinstance(node, list):
        for v in node:
            out += walk_field(v, field)
    elif isinstance(node, str) and field in node:
        try:
            out += walk_field(json.loads(node), field)
        except (ValueError, TypeError):
            pass
    return out


def _b64loose(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def decode_qaab(token: str, key_seed: str) -> str:
    """Decode a qAAB `main_url` token (AES-CBC). key|iv = SHA512(SHA512(seed[:32]) || 64-byte salt). The URL, or ""
    when it cannot be decoded."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    data, seed = _b64loose(token), _b64loose(key_seed)
    d1 = hashlib.sha512(seed[:32]).digest()
    d2 = hashlib.sha512(d1 + QAAB_SALT).digest()
    payload = data[4:] if data[:4] == QAAB_HEADER else data
    if not payload or len(payload) % 16:
        return ""
    plain = Cipher(algorithms.AES(d2[:16]), modes.CBC(d2[16:32])).decryptor()
    plain = plain.update(payload) + plain.finalize()
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16:
        plain = plain[:-pad]
    url = plain.decode("ascii", "ignore")
    return url if url.startswith("http") else ""


async def master_hq_url(http: DolaHttp, creation: dict) -> str | None:
    """URL of the MASTER of one cell, or None when it cannot be had (the rendition is used then). Never raises: the
    master is a bonus, any failure keeps the old route."""
    try:
        fallback = next((u for u in walk_field(creation, "fallback_api") if u.startswith("http")), None)
        if not fallback:
            return None
        if not trusted_master_host(fallback):
            log.warning("fallback link outside the cookie hosts (host or port), keeping the rendition: %s",
                        url_host(fallback))
            return None
        u = urlparse(fallback)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        q.update({"codec_type": "8", "logo_type": "unwatermarked", "channel": "no"})
        answer = await http.get_with_cookie(urlunparse(u._replace(query=urlencode(q))))
        if 300 <= answer.status_code < 400:     # a redirect is never followed (the cookie): no master
            return None
        resp = answer.json()
        vd = (resp.get("video_info") or {}).get("data") or resp.get("data") or resp
        video_list = vd.get("video_list") or {}
        entries = list(video_list.values()) if isinstance(video_list, dict) else video_list
        best = max((e for e in entries if isinstance(e, dict) and e.get("main_url")),
                   key=lambda e: float(e.get("bitrate") or 0), default=None)
        if not best:
            return None
        seeds = walk_field(resp, "key_seed") or re.findall(r"[?&]key_seed=([^&\"'\s]+)", json.dumps(resp))
        for seed in seeds:
            url = decode_qaab(best["main_url"], seed)
            if url.startswith("http"):
                return url
        return None
    except Exception:   # noqa: BLE001 - the master is a bonus: silent fallback to the rendition
        return None


async def _master_for(http: DolaHttp, out) -> str | None:
    """Re-read the output's conversation, find its cell by video id, ask for its master."""
    conversation_id = (out.meta or {}).get("conversation_id")
    if not conversation_id or not out.upstream_id:
        return None
    try:
        body = await fetch_chain(http, str(conversation_id))
    except resolver.ChainError:
        return None
    for creation, _at in resolver.video_creations(body):
        if resolver.cell_id(creation) == out.upstream_id:
            return await master_hq_url(http, creation)
    return None


async def fetch(ctx, out, settings, *, http: DolaHttp | None = None, media_transport=None,
                media_resolver=system_resolver) -> AsyncIterator[bytes]:
    """The bytes of `out`, streamed: direct then through the account proxy (`net.stream_download`). Raises when neither
    route gives bytes. `media_transport` and `media_resolver` are the test seams of the media download. A master link
    that the download refuses (a host outside the media list, a non-default port, a redirect off the list, a
    malformed link) is not used: the rendition is downloaded instead, unless a byte of the master was already handed
    over."""
    http = http or DolaHttp.for_account(ctx.account)
    urls = [out.ref]
    if str(out.mime).startswith("video/") and settings.get("dola.master_quality"):
        hq = await _master_for(http, out)
        if hq:
            urls.insert(0, hq)
    handed = False
    for i, url in enumerate(urls):
        try:
            async with contextlib.aclosing(stream_download(
                    url, hosts=MEDIA_HOSTS, http_hosts=MEDIA_HTTP_HOSTS, proxy=credential.proxy_url(ctx.account.proxy),
                    max_bytes=MEDIA_MAX_BYTES, deadline_s=MEDIA_DEADLINE_S, resolver=media_resolver,
                    transport=media_transport)) as parts:
                async for part in parts:
                    handed = True
                    yield part
            return
        except UnsafeUrl:       # already logged with its reason and host; a refused master keeps the rendition
            if handed or i == len(urls) - 1:
                raise
