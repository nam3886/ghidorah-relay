"""Page helpers shared by every browser provider (stage 1).

Each rule below cost a lost session or a dead browser once:
- closing the last tab makes Chrome quit: open the new tab FIRST, close the old ones after;
- piling up tabs crashes Chrome: keep exactly one tab of the site;
- "log in again" is destructive: a missing cookie is only local state. Inject the credential again; a new login
  creates a new session and kills the old credential.
"""

import asyncio
import json
import sys
from collections.abc import Sequence

# Playwright takes these spellings; Cookie-Editor exports others.
_SAMESITE = {"no_restriction": "None", "lax": "Lax", "strict": "Strict", "unspecified": "Lax"}
_EDITOR_SAMESITE = {"none": "no_restriction", "lax": "lax", "strict": "strict"}


class SessionError(RuntimeError):
    """The session is not in the state the caller needs. Stop early rather than send on the wrong account."""


class UiError(RuntimeError):
    """A UI action did not take (the prompt would not go in, or stayed in the input after Enter)."""


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
    """Open a new tab, then close the other tabs of the same site (`host` in their URL) and every `about:blank` tab (the
    one a fresh Chrome starts with). The new tab opens first, so the browser never has no tab, and it is never closed.
    Returns the new tab."""
    page = await ctx.new_page()
    for other in list(ctx.pages):
        if other is not page and (host in other.url or other.url == "about:blank"):
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


RENEW_TIMEOUT_S = 5.0           # the whole renewal, the cookie read and the write: past it nothing is written
EXPIRY_SLACK_S = 3600           # an expiry moved by less than this is the same expiry (no write for clock jitter)


def _key(c: dict) -> tuple[str, str, str]:
    return c["domain"], c.get("path") or "/", c["name"]


def _on_host(domain: str, host: str) -> bool:
    d = domain.lstrip(".").lower()
    return d == host or d.endswith("." + host)


def _one_session(stored: list[dict], browser: list[dict], name: str, host: str | None) -> bool:
    """The jar holds exactly one cookie `name`, at path `/` on `host` (or a subdomain), and the browser holds exactly
    that cookie (domain, path and value) and no other of that name."""
    mine = [c for c in stored if c["name"] == name]
    theirs = [c for c in browser if c["name"] == name]
    if len(mine) != 1 or not mine[0].get("value") or (mine[0].get("path") or "/") != "/":
        return False
    if host is not None and not _on_host(mine[0]["domain"], host):
        return False
    return len(theirs) == 1 and _key(theirs[0]) == _key(mine[0]) and theirs[0].get("value") == mine[0]["value"]


def rotated_cookies(stored: list[dict], browser: list[dict], *, stable: Sequence[str] = (), host: str | None = None,
                    only: Sequence[str] | None = None, domains: Sequence[str] | None = None,
                    at_most_one: Sequence[str] = ()) -> dict | None:
    """What the browser rotated of a sealed jar (`stored`, the Cookie-Editor shape) at the end of a signed-in run:
    {(domain, path, name): the cookie updated}, for cookies the jar already holds whose value changed or whose expiry
    moved. With `only`, nothing but those names. With `domains`, nothing but cookies at path `/` on exactly one of those
    domains; with `only` too, a cookie of those names the jar does not hold yet is added (never any other new cookie).
    None when the browser may not carry the account's session: a `stable` cookie (one that names the account and never
    rotates) is not exactly one at `/` on `host` in the jar and the very same one in the browser (`_one_session`), or
    the jar holds more than one cookie of the `at_most_one` names. A cookie the browser dropped is kept as stored."""
    if not all(_one_session(stored, browser, name, host) for name in stable):
        return None
    if sum(c["name"] in at_most_one for c in stored) > 1:
        return None
    jar = {_key(c): c for c in stored}
    out = {}
    for c in browser:
        if not c.get("value") or (only is not None and c["name"] not in only):
            continue
        if domains is not None and (c["domain"].lstrip(".").lower() not in domains or (c.get("path") or "/") != "/"):
            continue
        old = jar.get(_key(c))
        if old is None:
            if only is None or domains is None:
                continue
            old = {"name": c["name"], "domain": c["domain"], "path": "/",
                   "secure": bool(c.get("secure")), "httpOnly": bool(c.get("httpOnly"))}
            same = _EDITOR_SAMESITE.get(str(c.get("sameSite") or "").lower())
            if same:
                old["sameSite"] = same
        expires = c.get("expires")
        session = not isinstance(expires, int | float) or expires <= 0
        moved = (session != bool(old.get("session"))) or (
            not session and abs(expires - (old.get("expirationDate") or 0)) >= EXPIRY_SLACK_S)
        if c["value"] == old.get("value") and not moved:
            continue
        new = {**old, "value": c["value"], "session": session}
        new.pop("expirationDate", None)
        if not session:
            new["expirationDate"] = expires
        out[_key(c)] = new
    return out


def laid_over(blob: bytes, changes: dict, started: dict) -> bytes:
    """The sealed jar `blob` with each of `changes` (by key) taken only where the stored cookie is still the one this
    run started from (`started`, by key; a cookie the run adds: still absent): a cookie another run renewed or added
    meanwhile keeps that run's value, so a run that writes second never brings an older rotation back. Nothing else in
    the jar moves."""
    data = json.loads(blob)
    held = {_key(c) for c in data["cookies"]}
    data["cookies"] = [changes[_key(c)] if _key(c) in changes and c == started.get(_key(c)) else c
                       for c in data["cookies"]]
    data["cookies"] += [c for k, c in changes.items() if k not in held and k not in started]
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


async def renew_jar(browser_ctx, run_ctx, urls: Sequence[str], *, stable: Sequence[str] = (), host: str | None = None,
                    only: Sequence[str] | None = None, domains: Sequence[str] | None = None,
                    at_most_one: Sequence[str] = (), log=None) -> bool:
    """At the end of a run that read the account signed in: write the cookies the site rotated back into the account's
    sealed credential (`run_ctx.renew_credential`: sealed with its key, compare-and-swap, never over a re-import), each
    cookie only where the stored one is still the one this run started from (`laid_over`). See `rotated_cookies` for
    what is never written. The whole of it is cut at `RENEW_TIMEOUT_S`. True when a jar was written. Never raises (a
    failed or late write is a warning with no value in it, and the next run tries again)."""
    async def renew() -> bool:
        stored = json.loads(run_ctx.account.credential)["cookies"]
        changes = rotated_cookies(stored, await browser_ctx.cookies(list(urls)), stable=stable, host=host,
                                  only=only, domains=domains, at_most_one=at_most_one)
        if changes is None:
            if log is not None:
                log.warning("the session cookies are not exactly one each, the same in the jar and the browser: "
                            "nothing is written back")
            return False
        if not changes:
            return False
        started = {_key(c): c for c in stored}

        def merge(current: bytes, _mine: bytes) -> bytes:
            return laid_over(current, changes, started)
        written = await run_ctx.renew_credential(laid_over(run_ctx.account.credential, changes, started), merge)
        if written is not None and log is not None:
            log.info("rotated cookies written back: %s", ", ".join(sorted({k[2] for k in changes})))
        return written is not None

    try:
        return await asyncio.wait_for(renew(), RENEW_TIMEOUT_S)
    except Exception as exc:      # noqa: BLE001 - the credential stays as it was; never a reason to fail the run
        if log is not None:
            log.warning("the rotated cookies were not written back (%s)", type(exc).__name__)
        return False


# The composer's JS. Generic: a textarea or a contenteditable.
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

CLEAR_WAIT_S = 30.0     # how long the input may take to empty after Enter: the one wait, for the kit and every engine
#                         (measured through a proxy: 1.4, 3.9, 4.1, 11.1 and 13.5 s for sends that all went through)


def select_all_key(platform: str = sys.platform) -> str:
    """The keyboard shortcut that selects all: Command+A on macOS, Control+A elsewhere."""
    return "Meta+A" if platform == "darwin" else "Control+A"


async def _cleared(page, seconds: float) -> bool:
    """True when the input is empty, polled every 250 ms for up to `seconds`: a send that goes through a slow proxy
    empties the input late, and an input that is still full a moment after Enter is not yet proof of 'not sent'."""
    waited = 0.0
    while True:
        if not await page.evaluate(_COMPOSER_TEXT_JS):
            return True
        if waited >= seconds:
            return False
        await page.wait_for_timeout(250)
        waited += 0.25


async def type_and_send(page, prompt: str, attempts: int = 5, *, before_enter=None,
                        clear_wait_s: float = CLEAR_WAIT_S) -> None:
    """Type the prompt and send it with ONE Enter. The input must be empty afterwards: text still there means the
    send is unproven, and nothing else is pressed (never the Send button: a second send is a second paid order).

    The site sends on every Enter and the keyboard types a newline as Enter, so a multi-line prompt would become
    several messages (several conversations): it is flattened to one line first. Fast path: set the whole prompt at
    once; if the page did not take it, type it key by key (select all first, so a leftover draft is replaced,
    not appended to). `before_enter` (an async callable: the caller's mark-as-sent or lease fence) runs after the
    typing and immediately before the Enter keypress; if it raises, nothing is sent. The input is then given
    `clear_wait_s` to empty (a slow connection empties it late); if it does not, raise `UiError`."""
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
    if before_enter is not None:
        await before_enter()
    await page.keyboard.press("Enter")
    if not await _cleared(page, clear_wait_s):
        raise UiError("prompt still in the input")
