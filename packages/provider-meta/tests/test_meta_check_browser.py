"""The real `check_account` in a real Chrome against small local pages that carry meta's signs: a signed-in page, a
signed-out page, a redirect to the login URL and a page with no sign. Browser tier."""

import asyncio
import functools
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from relay_browser.session import SiteProfile
from relay_core.budget import BrowserBudget, Purpose
from relay_core.providers import Cooldown, Suspect
from relay_core.testing.mutation import mutation
from relay_meta.engine.check import check_account

CHECK_FILE = "packages/provider-meta/src/relay_meta/engine/check.py"

PAGES = {
    "/in": '<!doctype html><title>meta</title><div><button data-testid="user-menu-button">Nam Tester</button></div>',
    "/out": '<!doctype html><title>meta</title><button data-testid="login-button">Log in</button>',
    "/card": ('<!doctype html><title>meta</title>'
              '<button data-testid="logged-out-signin-card-dismiss-button">x</button>'),
    "/hidden": ('<!doctype html><title>meta</title>'
                '<button data-testid="user-menu-button" style="display:none">Nam Tester</button>'),
    "/late": ('<!doctype html><title>meta</title><script>setTimeout(()=>{document.body.innerHTML='
              '\'<button data-testid="user-menu-button" aria-label="Late Name"></button>\'},700)</script>'),
    "/login": "<!doctype html><title>log in</title><p>Log in to continue</p>",
    "/blank": "<!doctype html><title>meta</title><p>Something went wrong</p>",
}
JAR = json.dumps({"cookies": [{"name": "ecto_1_sess", "value": "v-sess", "domain": ".meta.ai", "path": "/",
                               "secure": True, "httpOnly": True, "session": True}]}).encode()


@dataclass(frozen=True)
class Account:
    id: str = "acct-meta-1"
    credential_gen: int = 1
    proxy: object = None
    credential: bytes = JAR


class Pages:
    """A local HTTP server: the pages above, `/go-login` answers a redirect to `/login`."""

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()

    def url(self, path: str) -> str:
        return "http://127.0.0.1:%d%s" % (self.port, path)

    async def _handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode().partition("?")[0]
            body = PAGES.get(path, "").encode()
            if path == "/go-login":
                status = b"302 Found\r\nLocation: /login?next=/"
            else:
                status = b"200 OK" if path in PAGES else b"404 Not Found"
            writer.write(b"HTTP/1.1 %s\r\nContent-Type: text/html\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                         % (status, len(body)) + body)
            await writer.drain()
        except Exception:      # noqa: BLE001 - a client that went away is not a test failure
            pass
        finally:
            writer.close()


def sync(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


async def check(tmp_path, path: str, wait_s: float = 5):
    token = BrowserBudget(1).try_acquire(Purpose.CHECK)
    site = SiteProfile(name="meta-test", host="127.0.0.1", profile_root=str(tmp_path / "profiles"))
    ctx = SimpleNamespace(account=Account(), browser_token=token)
    try:
        async with Pages() as pages:
            return await check_account(ctx, None, site=site, url=pages.url(path), wait_s=wait_s)
    finally:
        token.release()


@pytest.mark.browser
@mutation(CHECK_FILE, '            return Health("alive", display_name=name or None, screenshot=shot)',
          '            return Health("alive", screenshot=shot)', expect="the display name")
@sync
async def test_a_page_that_shows_the_user_menu_is_alive_with_the_name_and_a_picture(tmp_path):
    health = await check(tmp_path, "/in")
    assert health.state == "alive" and health.display_name == "Nam Tester", "the display name: %r" % (health,)
    assert health.screenshot and health.screenshot.startswith(b"\x89PNG")
    late = await check(tmp_path, "/late")
    assert (late.state, late.display_name) == ("alive", "Late Name"), "a sign that comes after the load"


@pytest.mark.browser
@sync
async def test_a_login_button_the_signed_out_card_or_a_login_redirect_is_a_strike_and_a_rest(tmp_path):
    for path in ("/out", "/card", "/go-login"):
        health = await check(tmp_path, path)
        assert health.state == "unknown", (path, health.state)
        assert [type(e) for e in health.effects] == [Suspect, Cooldown], (path, health.effects)
        assert health.display_name is None and health.screenshot


@pytest.mark.browser
@mutation(CHECK_FILE, "  const shown = e => !!e && e.getClientRects().length > 0;", "  const shown = e => !!e;",
          expect="a hidden user menu")
@sync
async def test_a_page_with_no_sign_or_a_hidden_menu_is_unknown_with_no_strike(tmp_path):
    hidden = await check(tmp_path, "/hidden", wait_s=1)
    assert (hidden.state, hidden.effects) == ("unknown", ()), "a hidden user menu counted as signed in: %s" % (
        hidden,)
    blank = await check(tmp_path, "/blank", wait_s=1)
    assert blank.state == "unknown" and not blank.effects and blank.screenshot, blank.state
