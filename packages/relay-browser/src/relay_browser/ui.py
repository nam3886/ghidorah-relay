"""Page helpers shared by every browser provider (contracts §6.3 stage 1).

Each rule below cost a lost session or a dead browser once:
- closing the last tab makes Chrome quit: open the new tab FIRST, close the old ones after;
- piling up tabs crashes Chrome: keep exactly one tab of the site;
- "log in again" is destructive: a missing cookie is only local state. Inject the credential again; a new login
  creates a new session and kills the old credential.
"""

import json
import sys

# Playwright takes these spellings; Cookie-Editor exports others.
_SAMESITE = {"no_restriction": "None", "lax": "Lax", "strict": "Strict", "unspecified": "Lax"}


class SessionError(RuntimeError):
    """The session is not in the state the caller needs. Stop early rather than send on the wrong account."""


class UiError(RuntimeError):
    """A UI action did not take (the prompt would not go in, or stayed in the input after Send)."""


def cookies_for_playwright(jar) -> list[dict]:
    """A cookie jar (a Cookie-Editor export: a list, or `{"cookies": [...]}`; as data, or as JSON text or bytes) in
    the shape the browser context takes.

    A session cookie (`session: true`) must NOT get `expires`, or the browser treats it as already expired and drops
    it. `sameSite` is mapped; an unknown value becomes Lax."""
    raw = json.loads(jar) if isinstance(jar, (str, bytes, bytearray)) else jar
    items = raw["cookies"] if isinstance(raw, dict) else raw
    out = []
    for c in items:
        cookie = {
            "name": c["name"], "value": c["value"], "domain": c["domain"],
            "path": c.get("path", "/"), "secure": bool(c.get("secure")),
            "httpOnly": bool(c.get("httpOnly")),
            "sameSite": _SAMESITE.get(str(c.get("sameSite", "")).lower(), "Lax"),
        }
        if not c.get("session") and c.get("expirationDate"):
            cookie["expires"] = int(c["expirationDate"])
        out.append(cookie)
    return out


async def keep_single_tab(ctx, host: str):
    """Open a new tab, then close the other tabs of the same site (`host` in their URL). Returns the new tab."""
    page = await ctx.new_page()
    for other in list(ctx.pages):
        if other is not page and host in other.url:
            try:
                await other.close()
            except Exception:      # noqa: BLE001 - a tab already gone is the goal
                pass
    return page


async def inject_cookies(ctx, jar, identity_names, read_url: str) -> list[str]:
    """Replace the context's cookies by the jar's and READ BACK the identity cookies (`identity_names`, the
    provider's key cookies) at `read_url`. Returns the names verified.

    Clearing first keeps two accounts' cookies apart; reading back catches a clear or an injection that only half
    worked, which would send as one account and charge another. A jar with none of the identity cookies is refused:
    there would be nothing to check."""
    cookies = cookies_for_playwright(jar)
    want = {c["name"]: c["value"] for c in cookies if c["name"] in identity_names}
    if not want:
        raise SessionError("the credential holds none of the identity cookies %s" % ", ".join(sorted(identity_names)))
    try:
        await ctx.clear_cookies()
    except Exception:      # noqa: BLE001 - a fresh context has nothing to clear
        pass
    await ctx.add_cookies(cookies)
    got = {c["name"]: c["value"] for c in await ctx.cookies(read_url) if c["name"] in identity_names}
    for name, value in want.items():
        if got.get(name) != value:
            raise SessionError("cookie %s injection did not take: the browser carries a different session" % name)
    return sorted(want)


# The composer's JS. Generic: a textarea or a contenteditable, and the lowest big button as Send.
_COMPOSER_TEXT_JS = """()=>{
  const t = document.querySelector('textarea');
  const c = document.querySelector('[contenteditable="true"]');
  return ((t && t.value) || (c && c.textContent) || '').trim();
}"""

_FOCUS_COMPOSER_JS = """()=>{
  const t = document.querySelector('textarea') || document.querySelector('[contenteditable="true"]');
  if (t) { t.focus(); if ('value' in t) t.value = ''; }
  return !!t;
}"""

# The whole prompt at once, not key by key (8 ms a character is minutes for a long prompt). A React-controlled
# textarea ignores a plain `.value =`: use the native setter, then fire `input`.
_FAST_SET_JS = """(text)=>{
  const t = document.querySelector('textarea');
  if (t) {
    const d = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
    d.set.call(t, text);
    t.dispatchEvent(new Event('input', {bubbles:true}));
    return (t.value || '').length;
  }
  const c = document.querySelector('[contenteditable="true"]');
  if (c) {
    c.focus();
    c.textContent = text;
    c.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:text}));
    return (c.textContent || '').length;
  }
  return -1;
}"""

_CLICK_SEND_JS = """()=>{
  const bs = [...document.querySelectorAll('button')].filter(b=>{
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.top > innerHeight*0.5;
  });
  const b = bs[bs.length-1];
  if (b) { b.click(); return true; }
  return false;
}"""


def select_all_key(platform: str = sys.platform) -> str:
    """The keyboard shortcut that selects all: Command+A on macOS, Control+A elsewhere."""
    return "Meta+A" if platform == "darwin" else "Control+A"


async def type_and_send(page, prompt: str, attempts: int = 5) -> None:
    """Type the prompt and send it. The input must be empty afterwards: text still there means NOT sent.

    The site sends on every Enter and the keyboard types a newline as Enter, so a multi-line prompt would become
    several messages (several conversations): it is flattened to one line first. Fast path: set the whole prompt at
    once; if the page did not take it, type it key by key (select all first, so a leftover draft is replaced,
    not appended to). Enter sends; if the text stays, the Send button is the fallback; if it still stays, raise."""
    prompt = " ".join((prompt or "").split())
    typed = False
    if await page.evaluate(_FOCUS_COMPOSER_JS):
        try:
            await page.evaluate(_FAST_SET_JS, prompt)
        except Exception:      # noqa: BLE001 - fall back to typing
            pass
        await page.wait_for_timeout(200)
        got = (await page.evaluate(_COMPOSER_TEXT_JS)) or ""
        if len(got) >= min(len(prompt), 40) and got[:40] == prompt[:40]:
            typed = True
    if not typed:
        for _ in range(attempts):
            if not await page.evaluate(_FOCUS_COMPOSER_JS):
                await page.wait_for_timeout(800)
                continue
            await page.keyboard.press(select_all_key())
            await page.keyboard.type(prompt, delay=8)
            await page.wait_for_timeout(300)
            if len((await page.evaluate(_COMPOSER_TEXT_JS)) or "") >= max(1, min(10, len(prompt))):
                typed = True
                break
            await page.wait_for_timeout(600)
    if not typed:
        raise UiError("could not type the prompt into the input")
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1500)
    if await page.evaluate(_COMPOSER_TEXT_JS):
        await page.evaluate(_CLICK_SEND_JS)
        await page.wait_for_timeout(2000)
    if await page.evaluate(_COMPOSER_TEXT_JS):
        raise UiError("prompt still in the input: send failed")
