"""`open_session` (coverage BSES02, BSES03, E01, T10): the token, the profile and its two resets, the
lock and the throwaway profile, the launch retry, the launch settings. The pure tests drive the functions open_session
is made of; the browser tests open a real Chrome through it."""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest
from browserkit import PLANTED_SECRET, Account, Proxy, redact, site_for, sync
from fixtures_server import SiteServer

from relay_browser import session as sess
from relay_browser.session import BrowserRefused, SiteProfile, open_session
from relay_core.budget import BrowserBudget, Purpose
from relay_core.testing.mutation import mutation

S = "packages/relay-browser/src/relay_browser/session.py"
KEEP = 'KEEP_IN_DEFAULT = ("Cache", "Code Cache", "Local Storage")'
CLAIM_RESET = "reset_profile_state(profile, drop_local_storage=crashed and bool(site.local_storage_token_keys))"
RELEASE_RESET = "await asyncio.to_thread(release_profile, claim, drop_local_storage=drop_storage)"
KILL = "                self.gone = await _kill_browser(self._claim.path)\n"
BASE = Path("Default")


def plant(profile: Path, *relpaths: str) -> None:
    for rel in relpaths:
        p = profile / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def files_left(profile: Path) -> list[str]:
    return sorted(str(p.relative_to(profile)) for p in profile.rglob("*") if p.is_file() or p.is_symlink())


def state_left(profile: Path) -> list[str]:
    """What is left outside the three kept folders: cookies, session storage, login data, network state."""
    return [f for f in files_left(profile) if not f.startswith(tuple("Default/" + k for k in sess.KEEP_IN_DEFAULT))]


# ---- the token -------------------------------------------------------------------------------------------
async def try_open(tmp_path, token, purpose="submit", **kw):
    """True when open_session refused; False when it opened (and closed again)."""
    try:
        async with open_session(site_for(tmp_path), Account(), purpose=purpose, token=token, redact=redact, **kw):
            return False
    except BrowserRefused:
        return True


@mutation(S, "    check_token(token, purpose)\n", "", expect="opened without a live token")
@sync
async def test_no_token_means_no_browser_and_nothing_on_disk(tmp_path):
    assert await try_open(tmp_path, None), "a browser opened without a live token"
    assert not (tmp_path / "profiles").exists(), "a profile was made before the token was checked"


@mutation(S, 'if token is None or getattr(token, "released", True):', "if token is None:",
          expect="released token opened a browser")
@sync
async def test_a_released_token_does_not_open_a_browser(tmp_path):
    token = BrowserBudget(1).try_acquire(Purpose.SUBMIT)
    token.release()
    assert await try_open(tmp_path, token), "a released token opened a browser"


@mutation(S, "if held is not None and str(held) != str(purpose):", "if False:", expect="token for another purpose")
@sync
async def test_a_token_for_another_purpose_does_not_open_a_browser(tmp_path):
    token = BrowserBudget(2).try_acquire(Purpose.CHECK)
    assert await try_open(tmp_path, token, purpose="submit"), "a token for another purpose opened a browser"


# ---- the profile directory -------------------------------------------------------------------------------------------
@mutation(S, 'return "%s-g%d" % (account_id, int(account.credential_gen))', 'return "%s" % account_id',
          expect="credential generation reuses")
@mutation(S, "if not _ID_OK.fullmatch(account_id):", "if not _ID_OK.match(account_id):", expect="trailing newline")
def test_a_profile_belongs_to_one_account_and_one_credential_generation():
    a1, a2, b1 = Account("a", 1), Account("a", 2), Account("b", 1)
    assert sess.profile_name(a1) == sess.profile_name(Account("a", 1)), "the same account gets a new profile each run"
    assert sess.profile_name(a1) != sess.profile_name(a2), "a new credential generation reuses the old profile"
    assert sess.profile_name(a1) != sess.profile_name(b1)
    with pytest.raises(ValueError):
        sess.profile_name(Account("../escape", 1))       # an id never walks out of the profiles root
    for bad in ("acct-1\n", "acct-1\n\n", "\nacct", "a b", "", "x" * 65, "ä"):
        try:
            name = sess.profile_name(Account(bad, 1))
        except ValueError:
            continue
        raise AssertionError("an id with a trailing newline or other stray characters named a profile: %r" % name)
    assert sess.profile_name(Account("A_b-9" * 12 + "xxxx", 3)).endswith("-g3")      # 64 characters are fine


@mutation(S, KEEP, 'KEEP_IN_DEFAULT = ("Cache", "Code Cache", "Local Storage", "IndexedDB")',
          expect="carries page state into the next job")
@mutation(S, "                if sub not in keep:", "                if False:",
          expect="carries page state into the next job")
def test_a_reset_keeps_only_the_caches_and_the_declared_local_storage(tmp_path):
    prof = tmp_path / "p"
    plant(prof, "Default/Cache/Cache_Data/js", "Default/Code Cache/js/x", "Default/Local Storage/leveldb/000003.log",
          "Default/IndexedDB/https_site_0.indexeddb.leveldb/000003.log", "Default/Cookies", "Default/Session Storage/x",
          "Default/Login Data", "Default/Network/Cookies", "Default/Preferences", "Local State", sess.OPEN_MARK)
    os.symlink("host-123", prof / "SingletonLock")
    sess.reset_profile_state(str(prof))
    assert files_left(prof) == ["Default/Cache/Cache_Data/js", "Default/Code Cache/js/x",
                                "Default/Local Storage/leveldb/000003.log"], \
        "the kept profile carries page state into the next job (drafts, cookies, storage, Chrome lock)"
    sess.reset_profile_state(str(prof), drop_local_storage=True)
    assert files_left(prof) == ["Default/Cache/Cache_Data/js", "Default/Code Cache/js/x"]


@mutation(S, "        if name == OPEN_MARK:\n            continue\n", "        if False:\n            continue\n",
          expect="marker is not removed last")
@mutation(S, "    _remove(os.path.join(profile, OPEN_MARK))\n", "    pass\n", expect="marker is not removed last")
def test_the_open_marker_is_removed_last_so_a_reset_cut_short_still_shows_the_crash(tmp_path, monkeypatch):
    prof = tmp_path / "p"
    plant(prof, "Default/Local Storage/leveldb/1.log", "Default/Cache/c", "Default/Cookies", "Top", sess.OPEN_MARK)
    removed = []
    real = sess._remove
    monkeypatch.setattr(sess, "_remove", lambda path: (removed.append(os.path.relpath(path, prof)), real(path))[1])
    sess.reset_profile_state(str(prof), drop_local_storage=True)
    assert removed.count(sess.OPEN_MARK) == 1 and removed[-1] == sess.OPEN_MARK, \
        "the marker is not removed last: %r" % removed
    assert "Default/Local Storage" in removed and not (prof / "Default/Local Storage").exists()


@mutation(S, "reset_profile_state(profile, drop_local_storage=crashed and bool(site.local_storage_token_keys))",
          "pass", expect="open reset did not run")
@mutation(S, "crashed = os.path.exists(os.path.join(profile, OPEN_MARK))", "crashed = False",
          expect="a crash left tokens in Local Storage")
def test_claiming_a_profile_resets_it_and_a_crashed_run_loses_its_local_storage(tmp_path):
    site = site_for(tmp_path, local_storage_token_keys=("token",))
    root = str(tmp_path / "profiles")
    prof = tmp_path / "profiles" / "acct-1-g1"
    plant(prof, "Default/IndexedDB/draft", "Default/Cache/c", "Default/Local Storage/leveldb/1.log", "Default/Cookies")
    claim = sess.claim_profile(root, "acct-1-g1", site)
    assert not claim.throwaway and claim.path == str(prof)
    assert files_left(prof) == sorted(["Default/Cache/c", "Default/Local Storage/leveldb/1.log", "Default/Preferences",
                                       sess.OPEN_MARK]), \
        "open reset did not run, or it dropped Local Storage without a crash"
    assert json.loads((prof / "Default/Preferences").read_text()) == sess.SEED_PREFERENCES
    sess.release_profile(claim)
    assert files_left(prof) == ["Default/Cache/c", "Default/Local Storage/leveldb/1.log"], \
        "the marker outlived a clean run"
    # a run that died before its close left the marker: nothing stripped the tokens, so Local Storage goes whole
    sess.mark_open(str(prof))
    claim = sess.claim_profile(root, "acct-1-g1", site)
    assert files_left(prof) == sorted(["Default/Cache/c", "Default/Preferences", sess.OPEN_MARK]), \
        "a crash left tokens in Local Storage"
    sess.release_profile(claim)


@mutation(S, "reset_profile_state(claim.path, drop_local_storage=drop_local_storage)", "pass",
          expect="close reset did not run")
def test_releasing_a_profile_resets_it_again_and_frees_the_lock(tmp_path):
    claim = sess.claim_profile(str(tmp_path), "a-g1", site_for(tmp_path))
    plant(Path(claim.path), "Default/Cookies", "Default/Session Storage/x", "Default/Login Data", "Default/Cache/c")
    sess.release_profile(claim)
    assert files_left(Path(claim.path)) == ["Default/Cache/c"], "close reset did not run"
    again = sess.claim_profile(str(tmp_path), "a-g1", site_for(tmp_path))
    assert not again.throwaway, "the lock was not released"
    sess.release_profile(again)


@mutation(S, 'cold = not os.path.isdir(os.path.join(profile, "Default", "Cache"))',
          "cold = False", expect="a profile with no cache was not called cold")
@mutation(S, "claim = ProfileClaim(profile, lock, False, cold=cold)", "claim = ProfileClaim(profile, lock, False)",
          expect="a profile with no cache was not called cold")
def test_a_profile_without_an_http_cache_is_cold_and_one_with_it_is_not(tmp_path):
    """23/10: the first run after a cookie refresh (a new credential generation: a new, empty profile) loaded dola too
    slowly for the composer wait. The engine waits longer on a cold profile; it must be told which one is."""
    site = site_for(tmp_path)
    first = sess.claim_profile(str(tmp_path), "a-g2", site)
    assert first.cold, "a profile with no cache was not called cold"
    plant(Path(first.path), "Default/Cache/Cache_Data/index")
    throwaway = sess.claim_profile(str(tmp_path), "a-g2", site)
    assert throwaway.throwaway and throwaway.cold, "a throwaway profile starts empty: it is cold"
    sess.release_profile(throwaway)
    sess.release_profile(first)
    again = sess.claim_profile(str(tmp_path), "a-g2", site)
    assert not again.cold, "a profile that kept its cache was called cold"
    sess.release_profile(again)


# ---- the lock and the throwaway profile ------------------------------------------------------------------------------
@mutation(S, "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)",
          expect="two runs hold the same account's profile")
def test_a_second_run_on_a_held_profile_gets_a_throwaway_that_is_deleted_at_release(tmp_path):
    site = site_for(tmp_path)
    first = sess.claim_profile(str(tmp_path), "a-g1", site)
    second = sess.claim_profile(str(tmp_path), "a-g1", site)
    assert not first.throwaway
    assert second.throwaway and second.lock is None, "two runs hold the same account's profile"
    assert second.path != first.path and Path(second.path).parent == tmp_path
    assert Path(second.path).name.startswith("tmp-")
    sess.release_profile(second)
    assert not Path(second.path).exists(), "a throwaway profile outlived its run"
    assert Path(first.path).is_dir(), "releasing the throwaway touched the held profile"
    sess.release_profile(first)


@mutation(S, "if path == keep or not os.path.isdir(path):", "if not os.path.isdir(path):",
          expect="the sweep removed a profile in use")
@mutation(S, "if now - os.path.getmtime(path) <= max_age_s:", "if False:", expect="the sweep removed a profile in use")
@mutation(S, "        if fd is None:\n            continue\n        try:",
          "        if fd is None:\n            fd = -1\n        try:", expect="deleted a profile another run holds")
def test_the_sweep_removes_only_old_unheld_profiles(tmp_path):
    old, fresh, mine, busy = (tmp_path / n for n in ("old", "fresh", "mine", "busy"))
    for p in (old, fresh, mine, busy):
        p.mkdir()
    past = time.time() - 3 * 86400 - 60
    for p in (old, mine, busy):
        os.utime(p, (past, past))
    held = sess.lock_profile(str(busy))
    assert held is not None and sess.lock_profile(str(busy)) is None
    removed = sess.sweep_profiles(str(tmp_path), keep=str(mine), max_age_s=3 * 86400)
    assert fresh.exists() and mine.exists(), "the sweep removed a profile in use"
    assert busy.exists(), "the sweep deleted a profile another run holds"
    assert not old.exists() and removed == 1, "a profile nobody opened for days is still on disk"
    sess.unlock_profile(held)


@mutation(S, "        if lock is not None:\n            unlock_profile(lock)\n        raise\n", "        raise\n",
          expect="lock leaked")
@mutation(S, "    except Exception as exc:      # noqa: BLE001 - old profiles",
          "    except ZeroDivisionError as exc:      # noqa: BLE001 - old profiles", expect="sweep failed the claim")
def test_a_claim_that_fails_after_taking_the_lock_releases_it_and_a_failing_sweep_is_not_a_failed_claim(tmp_path,
                                                                                                         monkeypatch):
    site = site_for(tmp_path)

    def disk_full(profile):
        raise OSError("disk full")
    monkeypatch.setattr(sess, "seed_preferences", disk_full)
    with pytest.raises(OSError):
        sess.claim_profile(str(tmp_path), "a-g1", site)
    held = sess.lock_profile(str(tmp_path / "a-g1"))
    assert held is not None, "the profile lock leaked: a claim that failed after taking it did not release it"
    sess.unlock_profile(held)
    monkeypatch.undo()

    def no_sweep(*args, **kwargs):
        raise RuntimeError("sweep broke")
    monkeypatch.setattr(sess, "sweep_profiles", no_sweep)
    try:
        claim = sess.claim_profile(str(tmp_path), "a-g1", site)
    except RuntimeError:
        raise AssertionError("a sweep failed the claim") from None
    assert not claim.throwaway
    sess.release_profile(claim)


@mutation(S, "        work.add_done_callback(_release_unclaimed)\n", "        pass\n", expect="profile stayed locked")
@mutation(S, "        return await asyncio.shield(work)", "        return await work", expect="profile stayed locked")
@mutation(S, "    claim = await claim_profile_in_thread(profile_root(site), name, site)",
          "    claim = await asyncio.to_thread(claim_profile, profile_root(site), name, site)",
          expect="profile stayed locked")
@sync
async def test_a_run_cancelled_while_its_profile_is_claimed_gives_the_profile_back(tmp_path, token, monkeypatch):
    site = site_for(tmp_path)
    prof = profile_of(tmp_path)
    started, gate = threading.Event(), threading.Event()
    real_sweep = sess.sweep_profiles

    def slow_sweep(*args, **kwargs):               # the claim runs in its thread; the sweep may take long
        started.set()
        gate.wait(10)
        return real_sweep(*args, **kwargs)
    monkeypatch.setattr(sess, "sweep_profiles", slow_sweep)

    async def run():
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact):
            raise AssertionError("the run was meant to be cancelled before Chrome started")
    task = asyncio.ensure_future(run())
    assert await asyncio.to_thread(started.wait, 10), "premise: the claim is running"
    task.cancel()
    await asyncio.sleep(0.05)
    gate.set()                                     # the thread finishes with a claim nobody is waiting for
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):                           # the claim is released when its thread is done
        if not (prof / sess.OPEN_MARK).exists():
            break
        await asyncio.sleep(0.05)
    monkeypatch.undo()
    again = sess.claim_profile(str(tmp_path / "profiles"), sess.profile_name(Account()), site)
    assert not again.throwaway, "the profile stayed locked after the run that claimed it was cancelled"
    sess.release_profile(again)


# ---- the launch retry ------------------------------------------------------------------------------------------------
def claim_for(tmp_path, throwaway=False) -> sess.ProfileClaim:
    path = tmp_path / "profile"
    plant(path, "Default/Cookies", "damage")
    return sess.ProfileClaim(str(path), None, throwaway)


async def launch_error(launch, claim) -> BaseException:
    try:
        await sess.launch_with_retry(launch, claim)
    except Exception as err:      # noqa: BLE001 - the test looks at the type
        return err
    raise AssertionError("the launch did not fail")


@mutation(S, "        mark_open(claim.path)\n        try:\n            return await launch(claim.path)",
          "        mark_open(claim.path)\n        raise exc\n        try:\n            return await launch(claim.path)",
          expect="was not retried")
@mutation(S, 'raise BrowserLaunchError("the browser would not start: %s" % str(again)[:300]) from again', "raise again",
          expect="not the kit's launch error")
@mutation(S, "        await asyncio.to_thread(shutil.rmtree, claim.path, True)", "        pass",
          expect="was not wiped")
@sync
async def test_a_failed_launch_wipes_the_profile_and_retries_once(tmp_path):
    claim = claim_for(tmp_path)
    seen = []

    async def launch(path):
        seen.append(files_left(Path(path)) if os.path.isdir(path) else None)
        if len(seen) == 1:
            raise RuntimeError("profile damaged")
        return "context"
    assert await sess.launch_with_retry(launch, claim) == "context", "a failed launch was not retried"
    assert seen[1] == [sess.OPEN_MARK, "Default/Preferences"], \
        "the damaged profile was not wiped before the retry: %r" % seen[1]
    assert claim.cold, "a wiped profile lost its cache and was not called cold"

    async def always_fails(path):
        seen.append("again")
        raise RuntimeError("still broken")
    seen.clear()
    err = await launch_error(always_fails, claim)
    assert isinstance(err, sess.BrowserLaunchError) and "still broken" in str(err), \
        "not the kit's launch error: %r" % err
    assert isinstance(err.__cause__, RuntimeError), "the launch error does not carry the cause"
    assert len(seen) == 2, "a failing launch is tried more than twice"


@mutation(S, "        if claim.throwaway:\n            raise", "        if False:\n            raise",
          expect="throwaway profile was retried")
@mutation(S, 'raise BrowserLaunchError("the browser would not start: %s" % str(exc)[:300]) from exc', "raise exc",
          expect="not the kit's launch error")
@sync
async def test_a_failed_throwaway_launch_is_not_retried(tmp_path):
    claim = claim_for(tmp_path, throwaway=True)
    seen = []

    async def launch(path):
        seen.append(path)
        raise RuntimeError("no")
    err = await launch_error(launch, claim)      # the kit's own type, not the driver's
    assert isinstance(err, sess.BrowserLaunchError) and isinstance(err.__cause__, RuntimeError), \
        "not the kit's launch error carrying its cause: %r" % err
    assert len(seen) == 1, "a throwaway profile was retried"


# ---- launch settings (BSES02) ----------------------------------------------------------------------------------------
@mutation(S, "if site.block_images and not images:", "if site.block_images:", expect="cannot show the attached images")
@mutation(S, 'return args + ["--disk-cache-size=%d" % site.disk_cache_bytes]', "return args",
          expect="disk cache has no cap")
def test_chrome_flags(tmp_path):
    site = site_for(tmp_path)
    assert "--blink-settings=imagesEnabled=false" in sess.launch_args(site)
    assert not any("imagesEnabled" in a for a in sess.launch_args(site, images=True)), \
        "images=True cannot show the attached images"
    assert "--disable-remote-fonts" in sess.launch_args(site, images=True)
    assert "--disk-cache-size=%d" % (150 * 1024 * 1024) in sess.launch_args(site), "the disk cache has no cap"
    assert not any("imagesEnabled" in a for a in sess.launch_args(site_for(tmp_path, block_images=False)))


def test_an_account_with_a_proxy_gets_the_webrtc_preference_on_top_of_the_seed(tmp_path):
    claim = sess.claim_profile(str(tmp_path), "a-g1", site_for(tmp_path))
    prefs = tmp_path / "a-g1" / "Default" / "Preferences"
    assert json.loads(prefs.read_text()) == sess.SEED_PREFERENCES, "an account with no proxy keeps the plain seed"
    sess.seed_proxy_preferences(claim.path)
    assert json.loads(prefs.read_text()) == {**sess.SEED_PREFERENCES,
                                             "webrtc": {"ip_handling_policy": "disable_non_proxied_udp"}}
    sess.release_profile(claim)


@mutation(S, "    if site.headless is not None:\n        return site.headless\n", "",
          expect="an explicit headless setting must win")
@mutation(S, 'return os.environ.get("RELAY_BROWSER_HEADLESS") == "1"', "return False",
          expect="the environment must decide")
def test_headless_is_the_sites_choice_then_the_environments_then_headful(monkeypatch):
    site = lambda **kw: SiteProfile(name="t", host="t.test", **kw)      # noqa: E731
    monkeypatch.delenv("RELAY_BROWSER_HEADLESS", raising=False)
    assert sess.is_headless(site()) is False, "with nothing set the browser must be headful (the box runs Xvfb)"
    assert sess.is_headless(site(headless=True)) is True, "an explicit headless setting must win (nothing set)"
    assert sess.is_headless(site(headless=False)) is False, "an explicit headless setting must win (still nothing set)"
    monkeypatch.setenv("RELAY_BROWSER_HEADLESS", "1")
    assert sess.is_headless(site()) is True, "the environment must decide when the site does not"
    assert sess.is_headless(site(headless=False)) is False, "an explicit headless setting must win over the environment"
    assert sess.is_headless(site(headless=True)) is True
    monkeypatch.setenv("RELAY_BROWSER_HEADLESS", "0")
    assert sess.is_headless(site()) is False, "only 1 means headless"
    assert sess.launch_kwargs(site(headless=False), None)["headless"] is False


@mutation(S, '        env["TMPDIR"] = tmpdir', "        pass", expect="Chrome keeps its temp files")
@mutation(S, "if k in CHROME_ENV_NAMES or k.startswith(CHROME_ENV_PREFIXES)}", "if True}",
          expect="Chrome would be handed")
@mutation(S, "k.startswith(CHROME_ENV_PREFIXES)", "any(p in k for p in CHROME_ENV_PREFIXES)",
          expect="a name outside the allow-list is handed on")
@mutation(S, 'CHROME_ENV_PREFIXES = ("LC_", "XDG_", "FONTCONFIG_")', "CHROME_ENV_PREFIXES = ()",
          expect="locale and display settings are lost")
def test_chrome_gets_its_own_temp_directory_and_only_an_allow_list_of_the_environment(monkeypatch):
    site = SiteProfile(name="t", host="t.test")
    planted = {"CREDENTIAL_SEAL_PRIVATE_KEY": "FAKE-private-key-for-the-test", "DATABASE_URL": "postgresql://u:p@db/x",
               "MINIO_SECRET_KEY": "fake", "RELAY_TEST_KEEP": "yes"}
    # the whole environment is set here: whatever the caller's shell exports cannot change the outcome
    monkeypatch.setattr(os, "environ", {**planted, "PATH": "/usr/bin", "HOME": "/home/w", "TMPDIR": "/shared",
                                        "LC_ALL": "C.UTF-8", "LC_CTYPE": "en_US.UTF-8", "DISPLAY": ":99",
                                        "XDG_RUNTIME_DIR": "/run/u", "TZ": "UTC", "LCX_NOT_LOCALE": "no",
                                        "XDGX_NOT_XDG": "no", "FONTCONFIG_PATH": "/etc/fonts", "EDITOR": "vi",
                                        "RELAY_XDG_TOKEN": "fake"})
    env = sess.launch_kwargs(site, None, tmpdir="/scratch/x")["env"]
    assert env.get("TMPDIR") == "/scratch/x", "Chrome keeps its temp files in the shared temp directory"
    leaked = sorted(set(planted) & set(env))
    assert not leaked, "Chrome would be handed the worker's secrets: %r" % leaked
    assert {k: env.get(k) for k in ("PATH", "HOME", "LC_ALL", "DISPLAY", "XDG_RUNTIME_DIR", "TZ")} == {
        "PATH": "/usr/bin", "HOME": "/home/w", "LC_ALL": "C.UTF-8", "DISPLAY": ":99", "XDG_RUNTIME_DIR": "/run/u",
        "TZ": "UTC"}, "the locale and display settings are lost"
    assert {k: env.get(k) for k in ("LC_CTYPE", "FONTCONFIG_PATH")} == {
        "LC_CTYPE": "en_US.UTF-8", "FONTCONFIG_PATH": "/etc/fonts"}, "a name with an allowed prefix is not handed on"
    assert not {"LCX_NOT_LOCALE", "XDGX_NOT_XDG", "EDITOR", "RELAY_XDG_TOKEN"} & set(env), \
        "a name outside the allow-list is handed on"
    assert set(env) == {"PATH", "HOME", "TMPDIR", "LC_ALL", "LC_CTYPE", "DISPLAY", "XDG_RUNTIME_DIR", "TZ",
                        "FONTCONFIG_PATH"}
    # without a session directory the shared TMPDIR is not handed on either
    assert "TMPDIR" not in sess.launch_kwargs(site, None)["env"]


@mutation(S, "launch_timeout_ms: int = 60_000", "launch_timeout_ms: int = 180_000", expect="no 60 s timeout")
def test_launch_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("RELAY_CHROME_PATH", raising=False)
    monkeypatch.delenv("RELAY_BROWSER_HEADLESS", raising=False)
    site = SiteProfile(name="t", host="t.test", viewport=(1440, 900))
    kw = sess.launch_kwargs(site, {"server": "http://127.0.0.1:1"})
    assert kw["timeout"] == 60_000, "the launch has no 60 s timeout"
    assert kw["channel"] == "chrome" and "executable_path" not in kw and kw["headless"] is False
    assert "env" in kw, "without an env Playwright hands Chrome the whole environment of the worker"
    assert kw["locale"] == "en-US" and kw["timezone_id"] == "Asia/Ho_Chi_Minh"
    assert kw["viewport"] == {"width": 1440, "height": 900} and kw["proxy"] == {"server": "http://127.0.0.1:1"}
    monkeypatch.setenv("RELAY_CHROME_PATH", "/opt/chrome")
    monkeypatch.setenv("RELAY_BROWSER_HEADLESS", "1")
    kw = sess.launch_kwargs(site, None)
    assert kw["executable_path"] == "/opt/chrome" and "channel" not in kw and kw["headless"] is True
    assert "viewport" not in sess.launch_kwargs(SiteProfile(name="t", host="t.test"), None)
    with pytest.raises(ValueError):
        SiteProfile(name="", host="x")


# ---- a real Chrome through open_session (browser tier) ---------------------------------------------------------------
def profile_of(tmp_path, account=None) -> Path:
    return tmp_path / "profiles" / sess.profile_name(account or Account())


@pytest.mark.browser
@mutation(S, RELEASE_RESET, "pass", expect="left page state behind")
@sync
async def test_after_the_session_the_profile_holds_no_cookie_session_storage_or_login_data(tmp_path, token):
    prof = profile_of(tmp_path)
    async with open_session(site_for(tmp_path, viewport=(1100, 700)), Account(), purpose="submit", token=token,
                            redact=redact) as s:
        assert await s.page.evaluate("1 + 1") == 2
        assert await s.page.evaluate("navigator.language") == "en-US"
        assert await s.page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") in (
            "Asia/Ho_Chi_Minh", "Asia/Saigon")           # the same zone; ICU's canonical name differs by build
        assert await s.page.evaluate("[innerWidth, innerHeight]") == [1100, 700]
        assert len(s.ctx.pages) == 1, "more than one tab is open"
        assert s.throwaway is False and Path(s.profile) == prof
        await s.ctx.add_cookies([{"name": "sid", "value": PLANTED_SECRET, "url": "http://127.0.0.1/"}])
    left = state_left(prof)
    assert left == [], "the session left page state behind: %r" % left
    assert not any(n.startswith("tmp-") for n in os.listdir(tmp_path / "profiles"))


@pytest.mark.browser
@mutation(S, CLAIM_RESET, "pass", expect="stale state was still there")
@mutation(S, KEEP, 'KEEP_IN_DEFAULT = ("Code Cache", "Local Storage")', expect="stale state was still there")
@sync
async def test_the_session_starts_from_a_reset_profile_and_keeps_the_caches(tmp_path, token):
    prof = profile_of(tmp_path)
    plant(prof, "Default/IndexedDB/draft/000003.log", "Default/Planted-state", "Planted-top", "Default/Cache/kept",
          "Default/Code Cache/kept")
    os.symlink("host-1", prof / "SingletonLock")      # what a killed Chrome leaves behind
    async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact):
        stale = [n for n in ("Default/IndexedDB", "Default/Planted-state", "Planted-top") if (prof / n).exists()]
        assert not stale, "stale state was still there: %r" % stale
        assert (prof / "Default/Cache/kept").exists() and (prof / "Default/Code Cache/kept").exists(), \
            "stale state was still there or the caches were removed"
    assert (prof / "Default/Cache/kept").exists() and (prof / "Default/Code Cache/kept").exists(), \
        "the close-time reset removed the caches"


@pytest.mark.browser
@sync
async def test_the_profile_is_reset_and_the_lock_freed_when_the_work_raises(tmp_path, token):
    class Boom(Exception):
        pass
    with pytest.raises(Boom):
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
            await s.ctx.add_cookies([{"name": "sid", "value": "v", "url": "http://127.0.0.1/"}])
            raise Boom()
    assert state_left(profile_of(tmp_path)) == []
    held = sess.lock_profile(str(profile_of(tmp_path)))
    assert held is not None, "the profile lock outlived a failed run"
    sess.unlock_profile(held)


@pytest.mark.browser
@sync
async def test_a_second_session_on_the_same_account_runs_on_a_throwaway_profile(tmp_path, token):
    async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as first:
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as second:
            assert first.throwaway is False and second.throwaway is True
            assert second.profile != first.profile
            assert await second.page.evaluate("2 + 2") == 4 and await first.page.evaluate("3 + 3") == 6
        assert not Path(second.profile).exists(), "the throwaway profile outlived its session"
    assert [n for n in os.listdir(tmp_path / "profiles") if n.startswith("tmp-")] == []


@pytest.mark.browser
@mutation(S, '"DOMStorage.removeDOMStorageItem"', '"DOMStorage.getDOMStorageItems"', expect="token survived")
@sync
async def test_the_declared_token_keys_are_stripped_from_local_storage_and_the_rest_stays(tmp_path, token):
    site = site_for(tmp_path, local_storage_token_keys=("auth_token",))
    async with SiteServer() as server:
        server.add("GET", "/", "<title>app</title>")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.evaluate("localStorage.setItem('auth_token', 'T'); localStorage.setItem('ui_strings', 'S')")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            kept = await s.page.evaluate("[localStorage.getItem('auth_token'), localStorage.getItem('ui_strings')]")
    assert kept == [None, "S"], "the token survived the close, or the rest of Local Storage was lost: %r" % kept


@pytest.mark.browser
@mutation(S, "        if not pages:\n            return\n",
          "        if not pages:\n            self.local_storage_clean = True\n            return\n",
          expect="token left on disk")
@sync
async def test_local_storage_goes_whole_when_no_page_of_the_site_could_be_stripped(tmp_path, token):
    site = site_for(tmp_path, local_storage_token_keys=("auth_token",))
    prof = profile_of(tmp_path)
    async with SiteServer() as server:
        server.add("GET", "/", "<title>app</title>")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.evaluate("localStorage.setItem('auth_token', 'T')")
            await s.page.goto("about:blank")                 # no page of the site is open at close
    assert not (prof / "Default" / "Local Storage").exists(), "token left on disk"


REWRITER = """<title>app</title><script>
localStorage.setItem('ui_strings', 'S');
setInterval(() => localStorage.setItem('auth_token', 'T-' + Date.now()), 20);
const ping = new MessageChannel();           // and again at once after every write: no gap to slip a close into
ping.port1.onmessage = () => { localStorage.setItem('auth_token', 'T-' + Date.now()); ping.port2.postMessage(0); };
ping.port2.postMessage(0);
</script>"""


@pytest.mark.browser
@mutation(S, 'await asyncio.wait_for(cdp.send("Emulation.setScriptExecutionDisabled", {"value": True}),',
          'await asyncio.wait_for(cdp.send("Emulation.setScriptExecutionDisabled", {"value": False}),',
          expect="token survived")
@sync
async def test_a_token_the_page_writes_again_every_20_ms_is_gone_after_the_close(tmp_path, token):
    site = site_for(tmp_path, local_storage_token_keys=("auth_token",))
    async with SiteServer() as server:
        server.add("GET", "/", REWRITER)
        server.add("GET", "/blank", "<title>blank</title>")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.wait_for_function("localStorage.getItem('auth_token') !== null")
            await s.page.wait_for_timeout(200)             # the timer has fired many times
        assert s.local_storage_clean, "token survived: the close dropped Local Storage instead of stripping it"
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url("/blank"))
            kept = await s.page.evaluate("[localStorage.getItem('auth_token'), localStorage.getItem('ui_strings')]")
    assert kept == [None, "S"], "token survived the close (the page wrote it again), or the rest was lost: %r" % kept


@pytest.mark.browser
@mutation(S, KEEP, 'KEEP_IN_DEFAULT = ("Code Cache", "Local Storage")', expect="downloaded again")
@sync
async def test_the_http_cache_survives_between_sessions_so_the_second_load_costs_nothing(tmp_path, token):
    async with SiteServer() as server:
        server.add("GET", "/", '<title>app</title><script src="/app.js"></script>')
        server.add("GET", "/app.js", lambda r: (200, {"Cache-Control": "max-age=3600"}, "document.title = 'loaded'"),
                   "application/javascript")
        for _ in range(2):
            async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
                await s.page.goto(server.url())
                # evaluate() runs in an isolated world: read the shared DOM
                assert await s.page.title() == "loaded"
        hits = [r.path for r in server.requests].count("/app.js")
    assert hits == 1, "the script was downloaded again by the second session (%d downloads)" % hits


@pytest.mark.browser
@sync
async def test_images_are_off_unless_the_run_asks_for_them(tmp_path, token):
    px = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
          b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")
    async with SiteServer() as server:
        server.add("GET", "/", '<title>app</title><img src="/px.png">')
        server.add("GET", "/px.png", px, "image/png")
        for images in (False, True):
            async with open_session(site_for(tmp_path / str(images)), Account(), purpose="submit", token=token,
                                    images=images, redact=redact) as s:
                await s.page.goto(server.url())
                await s.page.wait_for_timeout(500)
        hits = [r.path for r in server.requests].count("/px.png")
    assert hits == 1, "images were loaded with images=False, or not loaded with images=True (%d requests)" % hits


@pytest.mark.browser
@mutation(S, "                    drop_storage = not session.local_storage_clean\n",
          "                    drop_storage = False\n", expect="token survived a cancelled close")
@sync
async def test_a_cancel_during_the_close_time_strip_still_leaves_no_token_in_local_storage(tmp_path, token,
                                                                                            monkeypatch):
    # the strip hangs (a page that never answers) and the caller's timeout fires while it runs
    async def hang(self):
        await asyncio.sleep(3600)
    monkeypatch.setattr(sess.BrowserSession, "_strip_local_storage", hang)
    site = site_for(tmp_path, local_storage_token_keys=("auth_token",))
    prof = profile_of(tmp_path)

    async def work(server):
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.evaluate("localStorage.setItem('auth_token', 'T'); localStorage.setItem('ui_strings', 'S')")
            asyncio.get_running_loop().call_later(3, asyncio.current_task().cancel)
        raise AssertionError("the close finished: the strip was meant to hang")

    async with SiteServer() as server:
        server.add("GET", "/", "<title>app</title>")
        task = asyncio.ensure_future(work(server))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (prof / "Default" / "Local Storage").exists(), "token survived a cancelled close"
        assert not (prof / sess.OPEN_MARK).exists()
        monkeypatch.undo()                                    # the next run strips normally
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            kept = await s.page.evaluate("[localStorage.getItem('auth_token'), localStorage.getItem('ui_strings')]")
    assert kept[0] is None, "token survived a cancelled close: it is still there at the next open"


@pytest.mark.browser
@mutation(S, "            shutil.rmtree(scratch, ignore_errors=True)", "            pass", expect="temp files piled up")
@sync
async def test_chrome_temp_files_go_to_the_sessions_own_directory_and_none_are_left(tmp_path, token, monkeypatch):
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
        await s.page.goto("about:blank")
        scratch = list(shared.iterdir())
        assert len(scratch) == 1 and scratch[0].is_dir(), "the session made no directory of its own"
    assert list(shared.iterdir()) == [], "temp files piled up: %r" % [p.name for p in shared.iterdir()]


@pytest.mark.browser
@pytest.mark.box      # Linux Chrome only: on macOS Chrome ignores TMPDIR and hides its environment (Chrome 154)
@sync
async def test_chrome_runs_with_the_sessions_directory_as_its_temp_directory_and_without_the_workers_secrets(
        tmp_path, token, monkeypatch):
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    monkeypatch.setenv("CREDENTIAL_SEAL_PRIVATE_KEY", "FAKE-private-key-for-the-test")
    async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
        [scratch] = list(shared.iterdir())
        profile = Path(s.profile)
        pid = os.readlink(profile / "SingletonLock").rsplit("-", 1)[1]       # `host-pid`
        raw = (Path("/proc") / pid / "environ").read_bytes().decode()
        env = dict(item.split("=", 1) for item in raw.split("\0") if "=" in item)
        assert env.get("TMPDIR") == str(scratch), "Chrome was not given the session's directory as TMPDIR"
        assert "CREDENTIAL_SEAL_PRIVATE_KEY" not in env, "Chrome inherited the worker's private key"
        assert os.readlink(profile / "SingletonSocket").startswith(str(scratch) + os.sep), \
            "Chrome's singleton socket is not in the session's temp directory"


# ---- what open_session undoes when it cannot finish ------------------------------------------------------------------
@mutation(S, "    if not callable(redact):\n", "    if False:\n", expect="started with no redactor")
@sync
async def test_no_redactor_means_nothing_starts(tmp_path, token):
    for bad in (None, "not callable"):
        with pytest.raises(TypeError, match="redact"):
            async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=bad):
                raise AssertionError("a browser started with no redactor")
    assert not (tmp_path / "profiles").exists(), "a profile was made for a run that had no redactor"


@mutation(S, "        if fwd is not None:\n            fwd.close()\n",
          "        if fwd is not None:\n            pass\n", expect="forwarder was left running")
@mutation(S, "            shutil.rmtree(scratch, ignore_errors=True)", "            pass", expect="scratch directory")
@mutation(S, RELEASE_RESET, "pass", expect="lock was not released")
@sync
async def test_a_launch_that_fails_leaves_no_forwarder_scratch_directory_or_lock(tmp_path, token, monkeypatch):
    monkeypatch.delenv("RELAY_CHROME_PATH", raising=False)
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    closed = []
    real_close = sess.ProxyForwarder.close
    monkeypatch.setattr(sess.ProxyForwarder, "close", lambda self: (closed.append(1), real_close(self))[1])
    upstream = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    site = site_for(tmp_path, executable_path="/nonexistent/chrome")
    account = Account(proxy=Proxy("127.0.0.1", upstream.sockets[0].getsockname()[1], "u:p"))
    try:
        with pytest.raises(sess.BrowserLaunchError):
            async with open_session(site, account, purpose="submit", token=token, redact=redact):
                raise AssertionError("the launch was meant to fail")
    finally:
        upstream.close()
    assert closed == [1], "the proxy forwarder was left running"
    assert list(shared.iterdir()) == [], "the scratch directory was left: %r" % [p.name for p in shared.iterdir()]
    again = sess.claim_profile(str(tmp_path / "profiles"), sess.profile_name(account), site)
    assert not again.throwaway, "the profile lock was not released"
    sess.release_profile(again)


@pytest.mark.browser
@pytest.mark.box      # the kill finds Chrome's processes through /proc (Linux); elsewhere the profile is quarantined
@mutation(S, "await asyncio.wait_for(self.ctx.close(), CLOSE_TIMEOUT_S)", "await self.ctx.close()",
          expect="hung close was not cut off")
@mutation(S, KILL, "                self.gone = True\n", expect="still running")
@sync
async def test_a_close_that_hangs_is_cut_off_the_browser_killed_and_the_cleanup_still_runs(tmp_path, token,
                                                                                          monkeypatch):
    monkeypatch.setattr(sess, "CLOSE_TIMEOUT_S", 1.0)
    shared = tmp_path / "shared-tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    seen = {}
    async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
        prof = Path(s.profile)
        pid = int(os.readlink(prof / "SingletonLock").rsplit("-", 1)[1])

        async def hang(*args, **kwargs):
            await asyncio.sleep(30)
        s.ctx.close = hang
        shutdown = s._shutdown

        async def timed_shutdown():
            start = time.monotonic()
            await shutdown()
            seen["took"] = time.monotonic() - start
            seen["alive"] = running(pid)              # no polling here: the shutdown itself waits for the death
            seen["named"] = naming(str(prof))
        s._shutdown = timed_shutdown
    assert seen["took"] < 15, "a hung close was not cut off: %.1f s" % seen["took"]
    assert seen["alive"] is False, "the browser was still running after its close timed out"
    assert seen["named"] == [], "Chrome processes still named the profile after the kill: %r" % seen["named"]
    assert list(shared.iterdir()) == [], "the scratch directory was left behind"
    assert state_left(prof) == [], "the profile was not reset after a hung close"
    held = sess.lock_profile(str(prof))
    assert held is not None, "the profile lock was not released after a hung close"
    sess.unlock_profile(held)


@pytest.mark.browser
@mutation(S, "                self.local_storage_clean = False\n                self.gone = await _kill_browser",
          "                self.gone = await _kill_browser", expect="a killed close counted as clean")
@sync
async def test_a_close_that_had_to_be_killed_never_counts_as_clean_and_no_token_survives(tmp_path, token, monkeypatch):
    """The strip succeeds in memory, then Chrome hangs on close and is killed before it writes the removal to disk:
    the close reset must drop Local Storage whole, so the next open reads no token."""
    monkeypatch.setattr(sess, "CLOSE_TIMEOUT_S", 1.0)
    site = site_for(tmp_path, local_storage_token_keys=("auth_token",))
    async with SiteServer() as server:
        server.add("GET", "/", "<title>app</title>")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.evaluate("localStorage.setItem('auth_token', 'KILLTOKEN-abc')")
            await s.page.wait_for_timeout(6500)         # LevelDB writes after ~5 s: the token is on disk now

            async def hang(*a, **k):
                await asyncio.sleep(30)
            s.ctx.close = hang
        assert s.local_storage_clean is False, "a killed close counted as clean"
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            kept = await s.page.evaluate("localStorage.getItem('auth_token')")
    assert kept is None, "a killed close counted as clean: the token was read back %r" % kept



@pytest.mark.browser
@mutation(S, "                await log_browser_version(ctx, session.page)\n", "",
          expect="the browser version was not logged exactly once per launch")
@sync
async def test_one_open_session_logs_the_running_browser_version_exactly_once(tmp_path, token, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="relay_browser.session"):
        async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact):
            pass
    lines = [r.getMessage() for r in caplog.records if r.name == "relay_browser.session"
             and r.getMessage().startswith("browser ")]
    assert len(lines) == 1 and "/" in lines[0], \
        "the browser version was not logged exactly once per launch: %r" % (lines,)


# ---- a Chrome that was killed: the profile waits for its death -------------------------------------------------------
HAS_PROC = Path("/proc/self").exists()
linux = pytest.mark.skipif(not HAS_PROC, reason="the kill reads /proc (Linux); elsewhere it quarantines")


def running(pid: int) -> bool:
    """Alive and not a zombie (a zombie holds no file open)."""
    try:
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def naming(profile: str) -> list[int]:
    """Live processes whose command line names `profile`."""
    found = []
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and running(int(d.name)) and profile.encode() in (d / "cmdline").read_bytes():
                found.append(int(d.name))
        except OSError:
            pass
    return found


def spawn(code: str, *args: str) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", code, *args])
    threading.Thread(target=proc.wait, daemon=True).start()      # reaped at once: a dead child is no zombie
    return proc


class FakePage:
    url = "about:blank"

    def on(self, *args):
        pass

    async def close(self):
        pass


class FakeCtx:
    def __init__(self, close):
        self.pages, self._close = [], close

    async def new_page(self):
        self.pages.append(FakePage())
        return self.pages[-1]

    async def new_cdp_session(self, page):
        raise RuntimeError("a fake browser has no devtools")

    async def close(self):
        await self._close()


async def hang():
    await asyncio.sleep(30)


def fake_chrome(monkeypatch, code=None, close=hang, lock=False):
    """patchright's launch replaced: the 'browser' is `python -c code --user-data-dir=<profile>` (None: no process;
    `lock`: a SingletonLock is left anyway), its `ctx.close()` is `close`. Returns (the list the patched
    `release_profile` fills with "did a process still name the profile?", the list the launched process lands in)."""
    procs = []

    class PW:
        chromium = property(lambda self: self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def launch_persistent_context(self, path, **kw):
            if code is not None:
                procs.append(spawn(code, "--user-data-dir=" + path))
            if lock:
                os.symlink("host-999999", os.path.join(path, "SingletonLock"))
            return FakeCtx(close)
    # through sys.modules: the driver itself is imported only inside relay_browser (tests/guards.py)
    monkeypatch.setitem(sys.modules, "patchright.async_api", types.SimpleNamespace(async_playwright=PW))
    monkeypatch.setattr(sess, "CLOSE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(sess, "QUARANTINED", {})
    released = []
    real = sess.release_profile
    monkeypatch.setattr(sess, "release_profile",
                        lambda claim, **kw: (released.append(HAS_PROC and naming(claim.path) != []),
                                             real(claim, **kw))[1])
    return released, procs


async def run_fake(tmp_path, token):
    async with open_session(site_for(tmp_path), Account(), purpose="submit", token=token, redact=redact) as s:
        plant(Path(s.profile), "Default/Cookies")
        return Path(s.profile)


def assert_quarantined(prof: Path, released: list, why: str, planted: bool = True):
    assert released == [], "the profile was released while %s" % why
    assert not planted or (prof / "Default" / "Cookies").exists(), "the profile was reset while %s" % why
    assert sess.lock_profile(str(prof)) is None, "the profile lock was given up while %s" % why
    assert str(prof) in sess.QUARANTINED, "the profile was not listed as quarantined while %s" % why


NO_KILL = type("NoKill", (), {"SIGKILL": 0})      # os.kill(pid, 0) only probes: the process ends on its own


@linux
@mutation(S, "        if not alive and not strangers:", "        if True:", expect="released before")
@sync
async def test_a_killed_browser_that_dies_late_is_waited_for_before_the_profile_is_released(tmp_path, token,
                                                                                           monkeypatch):
    released, _ = fake_chrome(monkeypatch, "import time; time.sleep(1.5)")
    monkeypatch.setattr(sess, "signal", NO_KILL)
    start = time.monotonic()
    prof = await run_fake(tmp_path, token)
    assert released == [False], "the profile was released before the killed browser was gone: %r" % released
    assert time.monotonic() - start >= 1.0, "released before the browser could have died"
    assert state_left(prof) == [], "the profile was not reset after the browser died"
    held = sess.lock_profile(str(prof))
    assert held is not None, "the profile lock was not released after the browser died"
    sess.unlock_profile(held)


@linux
@mutation(S, KILL, "                self.gone = True\n", expect="released while")
@mutation(S, "        if gone:\n", "        if True:\n", expect="released while")
@mutation(S, "    lift_quarantine(profile)\n", "", expect="not lifted")
@sync
async def test_a_killed_browser_that_never_dies_quarantines_its_profile_until_it_is_gone(tmp_path, token,
                                                                                         monkeypatch):
    released, procs = fake_chrome(monkeypatch, "import time; time.sleep(60)")
    monkeypatch.setattr(sess, "signal", NO_KILL)
    monkeypatch.setattr(sess, "KILL_WAIT_S", 0.5)
    root, name, site = str(tmp_path / "profiles"), sess.profile_name(Account()), site_for(tmp_path)
    try:
        prof = await run_fake(tmp_path, token)
        assert_quarantined(prof, released, "the browser still ran")
        nxt = sess.claim_profile(root, name, site)
        assert nxt.throwaway, "the next run was given the quarantined profile while its browser still ran"
        sess.release_profile(nxt)
    finally:
        procs[0].kill()
    for _ in range(100):
        if not running(procs[0].pid):
            break
        time.sleep(0.05)
    again = sess.claim_profile(root, name, site)
    assert not again.throwaway, "the quarantine was not lifted once its browser was gone"
    assert sess.QUARANTINED == {}, "the lifted profile is still listed as quarantined"
    assert not (Path(again.path) / "Default" / "Cookies").exists(), "the lifted profile was not reset"
    sess.release_profile(again)


@mutation(S, "    if any(not p.zombie and names.search(p.cmdline) for p in table.values()):\n        return\n", "",
          expect="lifted while")
@sync
async def test_a_quarantine_is_not_lifted_while_a_process_names_the_profile(tmp_path, monkeypatch):
    root, site = str(tmp_path / "profiles"), site_for(tmp_path)
    claim = sess.claim_profile(root, "acct-1-g1", site)
    monkeypatch.setattr(sess, "QUARANTINED", {claim.path: claim})
    me = os.getpid()
    named = {me: sess.Proc(1, "1", False, b"python"),
             4242: sess.Proc(1, "9", False, b"/opt/google/chrome/chrome --type=renderer --user-data-dir="
                             + claim.path.encode() + b" --lang=en-US")}
    monkeypatch.setattr(sess, "processes", lambda: named)
    assert sess.claim_profile(root, "acct-1-g1", site).throwaway, "lifted while a process named the profile"
    longer = {me: named[me], 4242: sess.Proc(1, "9", False, b"chrome\0--user-data-dir=" + claim.path.encode() + b"0")}
    monkeypatch.setattr(sess, "processes", lambda: longer)        # acct-1-g10 is not acct-1-g1
    freed = sess.claim_profile(root, "acct-1-g1", site)
    assert not freed.throwaway, "a longer path that starts with the profile's kept it quarantined"
    sess.release_profile(freed)


@sync
async def test_a_quarantine_is_kept_while_the_process_table_cannot_be_read(tmp_path, monkeypatch):
    root, site = str(tmp_path / "profiles"), site_for(tmp_path)
    claim = sess.claim_profile(root, "acct-1-g1", site)
    monkeypatch.setattr(sess, "QUARANTINED", {claim.path: claim})

    def unreadable():
        raise PermissionError("/proc")
    monkeypatch.setattr(sess, "processes", unreadable)
    assert sess.claim_profile(root, "acct-1-g1", site).throwaway, "lifted while the process table was unreadable"


@mutation(S, KILL, "                self.gone = True\n", expect="released while")
@sync
async def test_a_chrome_lock_with_no_browser_found_quarantines_the_profile(tmp_path, token, monkeypatch):
    released, _ = fake_chrome(monkeypatch, None, lock=True)
    prof = await run_fake(tmp_path, token)
    assert_quarantined(prof, released, "no browser was found")


@linux
@mutation(S, "                    gone = session.gone\n", "                    gone = True\n", expect="released while")
@sync
async def test_a_close_cut_short_by_a_cancel_quarantines_the_profile(tmp_path, token, monkeypatch):
    async def cancelled():
        raise asyncio.CancelledError
    released, procs = fake_chrome(monkeypatch, "import time; time.sleep(60)", close=cancelled)
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_fake(tmp_path, token)
        assert_quarantined(tmp_path / "profiles" / sess.profile_name(Account()), released,
                           "a cancel cut the close short")
    finally:
        procs[0].kill()


@sync
async def test_a_browser_that_closes_releases_its_profile_at_once(tmp_path, token, monkeypatch):
    async def closes():
        pass
    released, procs = fake_chrome(monkeypatch, "import time; time.sleep(60)", close=closes)
    try:
        prof = await run_fake(tmp_path, token)
    finally:
        procs[0].kill()
    assert len(released) == 1, "a browser that closed cleanly did not release its profile"     # close() is trusted
    assert state_left(prof) == []


@linux
@mutation(S, "            gone = False        # a Chrome runs now", "            pass  #", expect="released while")
@sync
async def test_a_browser_left_running_by_a_failure_before_the_session_quarantines_the_profile(tmp_path, token,
                                                                                            monkeypatch):
    released, procs = fake_chrome(monkeypatch, "import time; time.sleep(60)")

    def broken(*args):
        raise RuntimeError("evidence writer broke")
    monkeypatch.setattr(sess, "EvidenceWriter", broken)
    try:
        with pytest.raises(RuntimeError, match="evidence writer broke"):
            await run_fake(tmp_path, token)
        assert_quarantined(tmp_path / "profiles" / sess.profile_name(Account()), released,
                           "its browser was never shut down", planted=False)
    finally:
        procs[0].kill()


@linux
@mutation(S, "                if pid not in ours and p.ppid in ours and parent and parent.start == ours[p.ppid]:",
          "                if False:", expect="child process")
@sync
async def test_the_kill_takes_the_browsers_child_processes_and_waits_for_them_too(tmp_path, token, monkeypatch):
    # the child's command line does not name the profile: only the walk of the tree finds it
    child_pid = tmp_path / "child.pid"
    code = ("import subprocess, sys, time; c = subprocess.Popen([sys.executable, '-c', 'import time; "
            "time.sleep(60)']); open(%r, 'w').write(str(c.pid)); time.sleep(60)" % str(child_pid))

    async def started_then_hang():
        for _ in range(100):
            if child_pid.exists() and child_pid.read_text():
                break
            await asyncio.sleep(0.05)
        await hang()
    fake_chrome(monkeypatch, code, close=started_then_hang)
    try:
        await run_fake(tmp_path, token)
        assert not running(int(child_pid.read_text())), \
            "a child process of the killed browser was still running at release"
    finally:
        with contextlib.suppress(Exception):
            os.kill(int(child_pid.read_text()), signal.SIGKILL)


# ---- the kill on a scripted process table: only this worker's own Chrome is ever signalled ---------------------------
class Table:
    """A process table that changes as it is killed: `deaths` = how many kills each pid takes to die (absent: never),
    `forks` = {pid: (child pid, Proc)} added at the second read while that pid lives (a child forked after the first
    signal), `reuse` = {pid: Proc} that takes the pid over once it died."""

    def __init__(self, rows, deaths=None, forks=None, reuse=None):
        self.rows, self.deaths = dict(rows), dict(deaths or {})
        self.forks, self.reuse = dict(forks or {}), dict(reuse or {})
        self.reads, self.kills = 0, []

    def read(self):
        self.reads += 1
        if self.reads == 2:
            for pid, (child, proc) in self.forks.items():
                if pid in self.rows:
                    self.rows[child] = proc
                    self.deaths.setdefault(child, 1)
        return dict(self.rows)

    def kill(self, pid, sig):
        self.kills.append(pid)
        if pid not in self.rows:
            raise ProcessLookupError(pid)
        if pid in self.deaths:
            self.deaths[pid] -= 1
            if self.deaths[pid] <= 0:
                del self.rows[pid]
                if pid in self.reuse:
                    self.rows[pid] = self.reuse.pop(pid)


def scripted(monkeypatch, table: Table):
    monkeypatch.setattr(sess, "processes", table.read)
    monkeypatch.setattr(sess.os, "kill", table.kill)
    monkeypatch.setattr(sess, "KILL_WAIT_S", 0.5)


PROFILE = "/profiles/acct-1-g1"
CHROME = b"/opt/google/chrome/chrome\0--user-data-dir=" + PROFILE.encode() + b"\0--no-sandbox"
ZYGOTE = b"/opt/google/chrome/chrome --type=zygote --user-data-dir=" + PROFILE.encode()


def proc(ppid, start="1", cmd=b"x", zombie=False):
    return sess.Proc(ppid, start, zombie, cmd)


def rows(*extra):
    """tini 1, the worker (this process) under it, its node driver 500 under the worker, and `extra` (pid, Proc)."""
    me = os.getpid()
    return {1: proc(0, cmd=b"tini"), me: proc(1, cmd=b"python worker"), 500: proc(me, cmd=b"node driver"),
            **dict(extra)}


@mutation(S, "                 if pid in table and table[pid].start == start and not table[pid].zombie]",
          "                 if pid in table and not table[pid].zombie]", expect="a reused pid")
@sync
async def test_a_pid_reused_by_another_process_is_never_signalled(monkeypatch):
    t = Table(rows((600, proc(500, "10", CHROME)), (601, proc(600, "11", ZYGOTE))),
              deaths={600: 1, 601: 1}, reuse={600: proc(1, "99", b"/usr/bin/other-service")})
    scripted(monkeypatch, t)
    assert await sess._kill_browser(PROFILE) is True
    assert t.kills.count(600) == 1, "a reused pid was signalled again: %r" % t.kills


@mutation(S, "            if flag in p.cmdline.split(b\"\\0\") and _descends(table, pid, me):",
          "            if flag in p.cmdline.split(b\"\\0\"):", expect="outside this worker")
@sync
async def test_a_chrome_on_the_profile_outside_this_workers_tree_is_never_signalled_and_quarantines(monkeypatch):
    t = Table(rows((700, proc(1, "5", CHROME))), deaths={700: 1})
    scripted(monkeypatch, t)
    gone = await sess._kill_browser(PROFILE)
    assert 700 not in t.kills, "a process outside this worker's tree was signalled: %r" % t.kills
    assert gone is False, "a profile still named by a live process was released"


@mutation(S, "        pid = table[pid].ppid\n        if pid == ancestor:\n            return True\n",
          "        if pid == ancestor:\n            return True\n        pid = table[pid].ppid\n",
          expect="the worker or its parent")
@sync
async def test_the_worker_and_its_parents_are_never_signalled(monkeypatch):
    me = os.getpid()
    t = Table({1: proc(0, cmd=b"tini\0" + CHROME), me: proc(1, cmd=b"python\0" + CHROME)})
    scripted(monkeypatch, t)
    assert await sess._kill_browser(PROFILE) is False
    assert t.kills == [], "the worker or its parent was signalled: %r" % t.kills


@mutation(S, "        grown = True\n        while grown:", "        grown = False\n        while grown:",
          expect="forked after")
@sync
async def test_a_child_forked_after_the_first_signal_is_killed_and_waited_for(monkeypatch):
    # the browser takes two kills to go (still alive at the second read) and forks 602 before it; 602's command line
    # does not name the profile, so only the walk of the tree finds it
    t = Table(rows((600, proc(500, "10", CHROME))), deaths={600: 2},
              forks={600: (602, proc(600, "12", b"/opt/google/chrome/chrome --type=renderer"))})
    scripted(monkeypatch, t)
    assert await sess._kill_browser(PROFILE) is True
    assert 602 in t.kills and 602 not in t.rows, "a child forked after the first signal was left: %r" % t.kills


@sync
async def test_an_unreadable_process_table_proves_nothing_and_signals_nothing(monkeypatch):
    t = Table(rows((600, proc(500, "10", CHROME))), deaths={600: 1})
    scripted(monkeypatch, t)

    def unreadable():
        raise PermissionError("/proc/600/cmdline")
    monkeypatch.setattr(sess, "processes", unreadable)
    assert await sess._kill_browser(PROFILE) is False, "an unreadable process table counted as the browser gone"
    assert t.kills == []


@mutation(S, "            quiet += 1\n            if quiet >= 2:", "            quiet += 2\n            if quiet >= 2:",
          expect="one quiet read")
@sync
async def test_gone_takes_two_quiet_reads_in_a_row(monkeypatch):
    # a process that names the profile (not ours) is gone at the second read and back at the third
    seq = [rows((600, proc(500, "10", CHROME))), rows(), rows((800, proc(1, "20", ZYGOTE))), rows(), rows()]
    reads = iter(seq)
    monkeypatch.setattr(sess, "processes", lambda: next(reads))
    monkeypatch.setattr(sess.os, "kill", lambda pid, sig: None)
    assert await sess._kill_browser(PROFILE) is True
    assert next(reads, "done") == "done", "gone was declared after one quiet read"
