"""The Settings page's limits in Chrome against the real app (browser tier), at 1440, 1024 and 390 px: the allowed
text under each bounded setting, the Fixed limits card, the Containers card and the container's own cgroup read end to
end."""

import json
import re

import pytest
from page_helpers import settle, sideways
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

TIMEOUT_MS = 10_000
SIZES = ((1440, 900), (1024, 768), (390, 800))
ALLOWED = (("log_retention_days", "at least 1"), ("fake.browser_slots", "1–30"),
           ("media_orphan_sweep", "true or false"), ("filename_pattern", "not empty"))
WORKER_LIMITS = {"read": True, "cpus": 8.0, "memory_bytes": 12 * 1024**3, "pids": 2048, "shm_bytes": 1024**3,
                 "missing": [], "boot": {"WORKER_ID": "w-limits", "RELAY_PROFILE_ROOT": "/profiles"}}
# a text that the page cuts off has more width than its box shows
CLIPPED_JS = """(el) => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1"""


def open_settings(app, page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(app.url + "#/settings")
    expect(page.locator("h1.settings__title")).to_be_visible()
    page.wait_for_load_state("networkidle")
    expect(page.locator("[aria-busy=true]")).to_have_count(0, timeout=TIMEOUT_MS)
    settle(page)


def on_phone(page) -> bool:
    return page.viewport_size["width"] < 768


def service_head(page, service: str):
    """What names a service in the Containers card: its column header (a table) or its block (a phone)."""
    return page.locator('#containers thead th[data-service="%s"], #containers section[data-service="%s"]'
                        % (service, service))


def limit_cell(page, service: str, label: str):
    """The cell of one measure of one service: a table cell, or the value of the phone block's line."""
    if on_phone(page):
        line = page.locator('#containers section[data-service="%s"] li' % service)
        return line.filter(has=page.get_by_text(label, exact=True)).locator(".set-list__value")
    named = page.locator("th", has_text=re.compile("^%s$" % re.escape(label)))
    row = page.locator("#containers tbody tr").filter(has=named)
    return row.locator('td[data-service="%s"]' % service)


def row_of(page, key: str):
    return page.locator("#settings tbody tr, #settings ul.list li", has_text=key).filter(has=page.locator(".key"))


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_allowed_text_of_a_bounded_setting_shows_under_its_description_uncut(app, app_session, width, height):
    page = app_session
    open_settings(app, page, width, height)
    for key, text in ALLOWED:
        line = row_of(page, key).first.locator(".allowed")
        expect(line).to_have_text("Allowed: " + text)
        expect(line).to_be_visible()
        assert page.evaluate(CLIPPED_JS, line.element_handle(), isolated_context=False) is False, key
    assert sideways(page) <= 0, "the page scrolls sideways"


@pytest.mark.parametrize(("width", "height"), SIZES[:1] + SIZES[2:])
def test_a_value_over_its_bound_is_refused_and_the_stored_value_stays(app, app_session, db, width, height):
    page = app_session
    open_settings(app, page, width, height)
    page.get_by_label("Value of fake.browser_slots").fill("31")
    page.get_by_role("button", name="Save fake.browser_slots").click()
    expect(page.locator(".el-message--error")).to_contain_text("(value)")
    assert db.one("SELECT count(*) FROM settings_kv WHERE key = 'fake.browser_slots'")[0] == 0
    page.problems.clear()                                    # the 400 is a console line


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_fixed_limits_link_brings_its_card_into_view_with_the_core_and_the_providers_rows(app, app_session, width,
                                                                                                height):
    page = app_session
    open_settings(app, page, width, height)
    page.locator(".snav").get_by_role("link", name="Fixed limits").click()
    settle(page)
    card = page.locator("#limits")
    expect(card).to_be_in_viewport()
    name = card.get_by_text("jobs.engine.MAX_INFLIGHT", exact=True)
    expect(name).to_be_visible()
    named = page.get_by_text("jobs.engine.MAX_INFLIGHT", exact=True)
    assert "32" in card.locator("tr, li").filter(has=named).first.inner_text()
    expect(card.get_by_text("fake", exact=True)).to_be_visible()                     # the provider's band
    expect(card.get_by_text("caps.max_attempts", exact=True)).to_be_visible()
    if not on_phone(page):
        expect(card.locator("thead th")).to_have_text(["Name", "Value", "Reason"])
    assert sideways(page) <= 0, "the page scrolls sideways"


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_the_containers_card_lists_the_api_with_four_readable_limits(app, app_session, width, height):
    page = app_session
    open_settings(app, page, width, height)
    page.locator(".snav").get_by_role("link", name="Containers").click()
    settle(page)
    api = service_head(page, "api")
    expect(api).to_be_visible()
    expect(api).to_contain_text("this process, read at start")
    # inside the box container the api reads its own cgroup: no limit may come back unreadable
    for label in ("CPUs", "Memory", "PIDs", "Shared memory (/dev/shm)"):
        value = limit_cell(page, "api", label)
        expect(value).to_be_visible()
        assert value.inner_text().strip() not in ("unreadable", "", "-"), label
    assert sideways(page) <= 0, "the page scrolls sideways"


@pytest.mark.parametrize(("width", "height"), SIZES)
def test_a_reporting_worker_shows_its_limits_and_one_from_an_older_version_says_not_reported(app, app_session, db,
                                                                                               width, height):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at, limits) VALUES "
           "('w-limits', 'fake', now() - interval '1 hour', now() - interval '20 seconds', %s::jsonb)",
           json.dumps(WORKER_LIMITS))
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "('w-older', 'fake', now() - interval '2 hours', now() - interval '30 seconds')")
    page = app_session
    open_settings(app, page, width, height)
    worker = service_head(page, "w-limits")
    expect(worker).to_be_visible()
    for label, text in (("CPUs", "8"), ("Memory", "12 GiB"), ("PIDs", "2048"), ("Shared memory (/dev/shm)", "1 GiB"),
                        ("RELAY_PROFILE_ROOT", "/profiles")):
        expect(limit_cell(page, "w-limits", label)).to_have_text(text)
    expect(worker).to_contain_text("seen just now")
    expect(worker.locator(".status-chip")).to_have_count(0)
    older = service_head(page, "w-older")
    expect(older).to_contain_text("Not reported by this worker version")
    expect(older).to_contain_text("seen just now")
    if on_phone(page):
        expect(older.locator("li")).to_have_count(0)
    else:
        expect(limit_cell(page, "w-older", "CPUs")).to_have_text("-")
    assert sideways(page) <= 0, "the page scrolls sideways"
