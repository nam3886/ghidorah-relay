"""Chrome's own background traffic: a session through the account's proxy must not call Google by
itself. The profile is reset every run, so Chrome acts like a first run each time: component updates, account and
time checks, safe browsing and the like would all go through the account's proxy otherwise."""

import asyncio
import json
import os
from collections import Counter

import pytest
from browserkit import Account, Proxy, redact, site_for, sync

from relay_browser import session as sess
from relay_browser.session import open_session
from relay_core.testing.mutation import mutation

S = "packages/relay-browser/src/relay_browser/session.py"
GOOGLE_SUFFIXES = (".google.com", ".googleapis.com", ".gstatic.com")
IDLE_SECONDS = 10
# Measured on Chrome 154 / macOS (10 s idle): 11 requests to Google before, 1 or 2 after (`www.google.com/async/folae`,
# an AI-mode entry check that rides on a host a site may use, so it is not refused; Chrome sends it once or twice,
# the same with and without the environment allow-list).
MAX_GOOGLE_REQUESTS = 2


def is_google(host: str) -> bool:
    return host == "google.com" or host.endswith(GOOGLE_SUFFIXES)


async def google_requests_of_a_session(tmp_path, token) -> tuple[list[str], list[str]]:
    """(hosts that were Google's, all hosts) of what one idle session sent through the forwarder to the upstream."""
    heads: list[str] = []

    async def upstream(r, w):
        try:
            heads.append((await r.readuntil(b"\r\n\r\n")).decode("latin-1"))
            w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await w.drain()
        except Exception:      # noqa: BLE001 - a client that went away
            pass
        finally:
            w.close()
    up = await asyncio.start_server(upstream, "127.0.0.1", 0)
    try:
        account = Account(proxy=Proxy("127.0.0.1", up.sockets[0].getsockname()[1], "pu:pp"))
        async with open_session(site_for(tmp_path), account, purpose="submit", token=token, redact=redact):
            await asyncio.sleep(IDLE_SECONDS)
    finally:
        up.close()
    hosts = []
    for head in heads:
        target = head.splitlines()[0].split(" ")[1]
        hosts.append((target.split("://")[-1].split("/")[0].rsplit(":", 1)[0]).lower())
    return [h for h in hosts if is_google(h)], hosts


@pytest.mark.browser
@mutation(S, "            seed_preferences(profile)\n            mark_open(profile)", "            mark_open(profile)",
          expect="not seeded before the first launch")
@mutation(S, "refuse_hosts: tuple[str, ...] = CHROME_BACKGROUND_HOSTS", "refuse_hosts: tuple[str, ...] = ()",
          expect="Google through the account")
@sync
async def test_an_idle_session_sends_nothing_to_google_through_the_account_proxy(tmp_path, token, monkeypatch):
    # The claim seeds the preferences. Without them the first launch fails (no Preferences to add the proxy's to) and
    # the retry wipes the profile and seeds it, so the Google count alone cannot see a missing seed: every run would
    # just lose its cache. Each launch's starting preferences are read here.
    seeded = []
    real = sess.seed_proxy_preferences

    def spy(path):
        prefs = os.path.join(path, "Default", "Preferences")
        try:
            with open(prefs, encoding="utf-8") as f:
                seeded.append(json.load(f).get("safebrowsing") == sess.SEED_PREFERENCES["safebrowsing"])
        except OSError:
            seeded.append(False)
        real(path)
    monkeypatch.setattr(sess, "seed_proxy_preferences", spy)
    google, hosts = await google_requests_of_a_session(tmp_path, token)
    assert seeded == [True], "the profile was not seeded before the first launch (per launch: %r)" % seeded
    print("background traffic: %d requests through the proxy, %d to Google: %s"
          % (len(hosts), len(google), dict(Counter(hosts))))
    assert len(google) <= MAX_GOOGLE_REQUESTS, \
        "Chrome called Google through the account's proxy by itself: %s" % dict(Counter(google))
