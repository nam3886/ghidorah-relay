"""The login page: the form and the busy Sign in button."""

import re

from patchright.sync_api import expect

from . import TIMEOUT_MS, slow, unslow

ROUTE = "login"
STATES = [("login", "form"), ("login", "busy-button")]


def run(page, app, live, sw, data):
    def login_form():
        page.goto(app.url + "#/login")
        expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    sw.step("login", "form", login_form)

    def login_busy():
        slow(page, "**/admin/login", 1500)
        page.get_by_label("Username").fill(live.user)
        page.get_by_label("Password").fill(live.password)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("button", name="Signing in...")).to_be_visible()
        expect(page).to_have_url(re.compile(r"#/jobs$"), timeout=TIMEOUT_MS * 2)
        unslow(page, "**/admin/login")
    sw.step("login", "busy-button", login_busy)
