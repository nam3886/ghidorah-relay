"""The pinned Chrome: the constant matches the Dockerfile, a drifted browser is warned about by name, and the
version is logged once per launch. Pure tests: no browser is started."""

import asyncio
import logging
import re
from pathlib import Path

from relay_browser import session as sess
from relay_core.testing.mutation import mutation

S = "packages/relay-browser/src/relay_browser/session.py"
DOCKERFILE = Path(__file__).resolve().parents[3] / "deploy" / "Dockerfile"
PIN = sess.PINNED_CHROME_VERSION


@mutation(S, 'PINNED_CHROME_VERSION = "154.0.8037.92"', 'PINNED_CHROME_VERSION = "154.0.8037.98"',
          expect="pin differs from the Dockerfile")
def test_the_pin_equals_the_dockerfile_chrome_version():
    arg = re.search(r"^ARG CHROME_VERSION=(\S+)$", DOCKERFILE.read_text(), re.M)
    assert arg, "deploy/Dockerfile has no CHROME_VERSION ARG"
    assert re.sub(r"-\d+$", "", arg.group(1)) == PIN, \
        "pin differs from the Dockerfile: %s against %s" % (PIN, arg.group(1))


@mutation(S, 'if got.group(1) != want.group(1) or got.group(3) != want.group(3):',
          'if got.group(1) != want.group(1):', expect="a different build was not warned about")
@mutation(S, 'if got.group(1) != want.group(1) or got.group(3) != want.group(3):',
          'if got.group(1) != want.group(1) and got.group(3) != want.group(3):',
          expect="a different major was not warned about")
@mutation(S, 'if got is None:\n        return', 'if got is None:\n        return None  #',
          expect="an unreadable version passed")
def test_a_browser_that_is_not_the_pinned_build_is_warned_about_by_both_versions():
    assert sess.chrome_version_warning("Chrome/" + PIN) is None
    assert sess.chrome_version_warning("154.0.8037.98") is None, "a patch level apart is the same build"
    for running in ("Chrome/155.0.8059.40", "HeadlessChrome/155.0.8037.92"):
        text = sess.chrome_version_warning(running)
        assert text, "a different major was not warned about: %s" % running
        assert PIN in text and running.split("/")[1] in text, "the warning does not name both versions: %r" % text
    text = sess.chrome_version_warning("Chrome/154.0.8040.1")
    assert text and "154.0.8040.1" in text, "a different build was not warned about"
    assert sess.chrome_version_warning(""), "an unreadable version passed"
    assert sess.chrome_version_warning("no version here"), "an unreadable version passed"


class FakeCdp:
    def __init__(self, product=None, error=None):
        self.product, self.error, self.detached = product, error, False

    async def send(self, method):
        assert method == "Browser.getVersion"
        if self.error:
            raise self.error
        return {"product": self.product, "revision": "x"}

    async def detach(self):
        self.detached = True


class FakeCtx:
    def __init__(self, cdp):
        self.cdp = cdp

    async def new_cdp_session(self, page):
        return self.cdp


def run_log(cdp, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="relay_browser.session"):
        asyncio.run(sess.log_browser_version(FakeCtx(cdp), object()))
    return [(r.levelno, r.getMessage()) for r in caplog.records]


@mutation(S, 'log.info("browser %s", product)', "pass", expect="the version was not logged")
@mutation(S, "        log.warning(warning)", "        pass", expect="a drifted browser was not warned about")
def test_the_version_is_logged_once_and_a_drift_is_a_warning(caplog):
    good = run_log(FakeCdp("Chrome/" + PIN), caplog)
    assert good == [(logging.INFO, "browser Chrome/" + PIN)], "the version was not logged once: %r" % good
    drift = run_log(FakeCdp("Chrome/155.0.8059.40"), caplog)
    assert [lvl for lvl, _ in drift] == [logging.INFO, logging.WARNING], \
        "a drifted browser was not warned about: %r" % drift
    assert "155.0.8059.40" in drift[1][1] and PIN in drift[1][1]


@mutation(S, "        log.warning(\"could not read the browser version: %s\", str(exc)[:80])\n        return",
          "        raise", expect="an unreadable browser stopped the run")
def test_a_browser_that_cannot_answer_does_not_stop_the_run(caplog):
    try:
        got = run_log(FakeCdp(error=RuntimeError("target closed")), caplog)
    except RuntimeError as exc:
        raise AssertionError("an unreadable browser stopped the run: %s" % exc) from None
    assert got and got[0][0] == logging.WARNING and "could not read" in got[0][1], \
        "an unreadable browser stopped the run or was not logged: %r" % got


DETACH = ("        if cdp is not None:\n            with contextlib.suppress(Exception):\n"
          "                await cdp.detach()")


@mutation(S, DETACH, "        pass", expect="the CDP session stayed attached")
def test_the_cdp_session_is_detached_whether_the_version_was_read_or_not(caplog):
    for cdp in (FakeCdp("Chrome/" + PIN), FakeCdp(error=RuntimeError("target closed"))):
        run_log(cdp, caplog)
        assert cdp.detached, "the CDP session stayed attached"
