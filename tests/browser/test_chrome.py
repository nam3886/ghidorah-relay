"""The browser tier (contracts §14, D33): runs inside the `browser` image on its virtual display, never on the Mac.

`scripts/check-in-container.sh` runs `pytest -m browser` there. dola_v4 drives Google Chrome through
patchright (`channel="chrome"`), the proven setup, so the tier proves exactly that: the .deb Chrome is
installed and patchright can start it headful on the virtual screen.
"""

import os
import subprocess

import pytest

pytestmark = pytest.mark.browser

CHROME = "/opt/google/chrome/chrome"


def test_google_chrome_is_installed_from_the_deb():
    assert os.path.exists(CHROME), "Google Chrome is missing from the browser image"
    version = subprocess.run([CHROME, "--version"], capture_output=True, text=True, check=True).stdout
    assert version.startswith("Google Chrome "), version


def test_patchright_starts_headful_chrome_on_the_virtual_screen():
    from patchright.sync_api import sync_playwright

    assert os.environ.get("DISPLAY"), "no virtual display: run through the image entrypoint (deploy/xvfb-exec)"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=False)
        try:
            page = browser.new_page()
            assert "Chrome/" in page.evaluate("navigator.userAgent")
        finally:
            browser.close()
