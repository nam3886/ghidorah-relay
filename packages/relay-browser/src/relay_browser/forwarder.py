"""The local proxy forwarder.

Chrome goes through a local proxy that needs no password; the forwarder adds `Proxy-Authorization` when it passes
the request on to the account's real proxy. Why: a proxy with a password makes Playwright enable CDP `Fetch` to answer
the login, and our own `Fetch` response patches then fight it (ERR_INVALID_AUTH_CREDENTIALS). Without it Playwright
leaves `Fetch` and the HTTP cache alone. It lives for one browser session.

502 on purpose: an upstream 407 (wrong or expired proxy password) passed through would make Chrome, which has no
password, show a login prompt and hang the page for 60 s, an error the worker cannot tell from a dead network. A 502
fails at once (`ERR_TUNNEL_CONNECTION_FAILED`) and the account rests as a dead proxy does. The same 502 answers an
upstream that stays silent (`REPLY_TIMEOUT_S`) or closes without a reply. Each such 502 is logged with its cause and
the proxy's host and port, never its login (23/10: a burst of ERR_TUNNEL_CONNECTION_FAILED could not be explained).

`refuse` names hosts that get that 502 without ever reaching the account's proxy: Chrome's own background services
(updates, account and push checks) that no flag switches off, and that would put a Google fingerprint on the proxy.
"""

import asyncio
import base64
import contextlib
import logging
from urllib.parse import urlsplit

log = logging.getLogger("relay_browser.forwarder")

_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
HEAD_TIMEOUT_S = 30
CONNECT_TIMEOUT_S = 20
REPLY_TIMEOUT_S = 30


def proxy_settings(proxy) -> dict | None:
    """A proxy (`host`, `port`, `auth` as `user:password` or None) as the dict the browser launch takes; None stays
    None (the browser goes direct). The password may hold colons: only the first one splits."""
    if proxy is None:
        return None
    host = "[%s]" % proxy.host if ":" in proxy.host and not proxy.host.startswith("[") else proxy.host
    out = {"server": "http://%s:%d" % (host, proxy.port)}
    if proxy.auth:
        user, _, password = proxy.auth.partition(":")
        out["username"], out["password"] = user, password
    return out


def _with_auth(head: bytes, auth_line: bytes | None) -> bytes:
    """The request head Chrome sent the local proxy, rewritten for the real proxy: our password in, any other out.

    CONNECT (HTTPS, almost everything) is a tunnel: one head, then only encrypted bytes. A plain HTTP request has a
    head of its own each time, and only the FIRST head on a connection can be rewritten, so `Connection: close` makes
    every connection carry exactly one request."""
    lines = [x for x in head.split(b"\r\n") if x]
    first, rest = lines[0], lines[1:]
    tunnel = first.upper().startswith(b"CONNECT ")
    drop = (b"proxy-authorization:",) if tunnel else (b"proxy-authorization:", b"proxy-connection:", b"connection:")
    rest = [x for x in rest if not x.lower().startswith(drop)]
    if not tunnel:
        rest.append(b"Connection: close")
    return b"\r\n".join([first] + rest + ([auth_line] if auth_line else []) + [b"", b""])


def target_host(head: bytes) -> str:
    """The host a request head asks the proxy for (`CONNECT host:port` or `GET http://host/path`), lower-cased."""
    try:
        target = head.split(b"\r\n", 1)[0].split(b" ")[1].decode("latin-1")
    except IndexError:
        return ""
    if "://" in target:
        target = target.split("://", 1)[1]
    return urlsplit("//" + target.split("/", 1)[0]).hostname or ""


def _refused(host: str, refuse: tuple[str, ...]) -> bool:
    return any(host == r or host.endswith("." + r) for r in refuse)


def _status(head: bytes) -> int:
    try:
        return int(head.split(b" ", 2)[1])
    except (IndexError, ValueError):
        return 0


async def _pipe(reader, writer) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:      # noqa: BLE001 - one end closing is the normal end
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


class ProxyForwarder:
    """A passwordless proxy on 127.0.0.1 (random port) that forwards to the account's proxy.

    `upstream` is the dict of `proxy_settings`: `server`, `username`, `password`."""

    def __init__(self, upstream: dict, refuse: tuple[str, ...] = ()):
        self._refuse = tuple(r.lower() for r in refuse)
        u = urlsplit(upstream["server"])
        self._host, self._port = u.hostname, u.port or 80
        cred = "%s:%s" % (upstream.get("username") or "", upstream.get("password") or "")
        self._auth = (b"Proxy-Authorization: Basic " + base64.b64encode(cred.encode("utf-8"))
                      if upstream.get("username") else None)      # a proxy without a login gets none
        self._server = None
        self._writers: set = set()

    async def start(self) -> dict:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return {"server": "http://127.0.0.1:%d" % self._server.sockets[0].getsockname()[1]}

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for w in list(self._writers):
            with contextlib.suppress(Exception):
                w.close()

    def _log_502(self, why: str) -> None:
        """One 502 Chrome is given, with its cause and the proxy's host and port only: never the login."""
        log.warning("proxy %s:%d: 502 to Chrome (%s)", self._host, self._port, why)

    async def _handle(self, cr, cw) -> None:
        self._writers.add(cw)
        uw = up = None
        try:
            head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
            if _refused(target_host(head), self._refuse):
                log.debug("502 to Chrome: %s is a refused host", target_host(head))
                cw.write(_BAD_GATEWAY)
                await cw.drain()
                return
            try:
                ur, uw = await asyncio.wait_for(asyncio.open_connection(self._host, self._port), CONNECT_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - the real proxy cannot be reached
                self._log_502("connect timeout" if isinstance(exc, TimeoutError)
                              else "connect failed: %s" % (str(exc) or type(exc).__name__)[:120])
                cw.write(_BAD_GATEWAY)
                await cw.drain()
                return
            self._writers.add(uw)
            uw.write(_with_auth(head, self._auth))
            await uw.drain()
            up = asyncio.ensure_future(_pipe(cr, uw))    # a request body, if any, travels in parallel
            try:
                reply = await asyncio.wait_for(ur.readuntil(b"\r\n\r\n"), REPLY_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - silent for too long, or closed without a reply
                reply, why = b"", ("reply timeout" if isinstance(exc, TimeoutError) else "closed without a reply")
            if not reply or _status(reply) == 407:
                self._log_502(why if not reply else "upstream 407 (the proxy refused its login)")
                cw.write(_BAD_GATEWAY)
                await cw.drain()
                return
            cw.write(reply)
            await cw.drain()
            await _pipe(ur, cw)
        except Exception:      # noqa: BLE001 - an end closed midway: Chrome reports the network error itself
            pass
        finally:
            if up is not None:
                up.cancel()
            for w in (cw, uw):
                if w is not None:
                    self._writers.discard(w)
                    with contextlib.suppress(Exception):
                        w.close()
