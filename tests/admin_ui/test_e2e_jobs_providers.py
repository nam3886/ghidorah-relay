"""The provider side of the Jobs page in Chrome against the real app with the four real provider specs (browser tier):
on a desktop the provider cards of the Today section pick the provider of the list (no strip); on a tablet's 820 px and
a phone's 390 px the provider strip under the top bar is the filter; the Today section reads the provider summary and
the job rows carry their provider chip. No text may be cut but a job title and the line under it (an ellipsis, the
whole text in the title attribute), and the page never scrolls sideways. With RELAY_SCREENSHOTS_DIR set, each state is
saved as jobs-providers-<width>-<pick>.png."""

import os
import re
from pathlib import Path

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from page_helpers import settle, sideways
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR")


def job(db, k, provider: str, model: str, status: str = "done", title: str = "") -> None:
    """A finished (or open) job of a real provider, planted like the fake one's."""
    j = done_job(db, k, ref="%s-%s" % (provider, title), status="done" if status in ("done", "waiting") else status,
                 prompt=title)
    kind = "image" if "image" in model or "seedream" in model else "video"
    db.run("UPDATE jobs SET provider = %s, model = %s, title = %s, media_type = %s WHERE id = %s",
           provider, model, title, kind, j)
    if status == "failed":
        db.run("UPDATE jobs SET error_code = 'render.deadline' WHERE id = %s", j)
    if status == "waiting":
        db.run("DELETE FROM job_media WHERE job_id = %s", j)
        db.run("UPDATE jobs SET status = 'waiting', finished_at = NULL, next_poll_at = now() WHERE id = %s", j)


def plant(db) -> None:
    k = new_key(db)
    job(db, k, "dola", "seedance-2.5", title="Lantern festival in Hoi An at dusk, a slow pan along the river with "
        "paper boats drifting past the old town and the bridge")       # past one line at every width: an ellipsis
    job(db, k, "dola", "seedance-2.0", title="Vintage car chase, 1970s film look, two coupes")
    job(db, k, "dola", "seedream-4.5", "failed", title="Dragon mascot logo, flat")
    job(db, k, "vibes", "vibes-video", title="Rainy Tokyo street at night, neon")
    job(db, k, "vibes", "vibes-video", "waiting", title="Cat astronaut floating, 3D render")
    job(db, k, "meta", "meta-image", title="Product shot: matcha latte on marble")
    for provider, state in (("dola", "available"), ("dola", "available"), ("dola", "dead"), ("meta", "available"),
                            ("vibes", "available"), ("flow", "available")):
        new_account(db, provider=provider, state=state, checked=True, label="%s-acct" % provider)


def claims(page, provider: str, on: bool) -> None:
    """Turn a provider's claims on or off through the admin API, as the Settings page does (meta, vibes and flow ship
    with them off)."""
    status = page.evaluate("""async ([key, on]) => {
        const token = sessionStorage.getItem('relay_admin_token');
        const r = await fetch('/admin/settings/' + key, {method: 'PUT', credentials: 'same-origin',
            headers: {'X-Admin-Session': token, 'Content-Type': 'application/json'},
            body: JSON.stringify({value: on})});
        return r.status;
    }""", [provider + ".claims_enabled", on], isolated_context=False)
    assert status == 200, status


def job_rows(page):
    return page.locator(".jobs__table tbody tr.is-row, .jobs__table button.p-row")


def shot(page, name: str) -> None:
    """The top of the page, then the job list scrolled into view (the viewport only, as the sweep takes them)."""
    if SHOTS:
        settle(page)                                # the entrance animation has ended
        page.screenshot(path=str(Path(SHOTS) / ("jobs-providers-%s.png" % name)))
        page.locator(".jobs__table").scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        page.screenshot(path=str(Path(SHOTS) / ("jobs-providers-%s-list.png" % name)))
        page.locator("h1").first.scroll_into_view_if_needed()


def no_cut_text(page) -> None:
    """Nothing in the Today section, the strip's tabs or a job row is cut, and the page never scrolls sideways. A job
    title and the line under it end in an ellipsis on one line (two lines for a phone's title) and carry their whole
    text in their title attribute (and the detail); a row that scrolls sideways is the arrow scroller's."""
    cut = page.evaluate("""() => {
        const sel = '[data-testid=jobs-today] *, .jt-card *, .ptab, .jobs__table *, .jobs__bar *';
        const skip = '.title-link, .sub, .p-row__title, .p-row__model, .asc__row, .jt-bars, .jt-spark';
        const seen = (el) => el.offsetParent && !el.closest(skip);
        const wide = (el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible';
        return [...document.querySelectorAll(sel)].filter((el) => seen(el) && wide(el))
            .map((el) => el.className + ': ' + el.textContent.trim().slice(0, 40));
    }""", isolated_context=False)
    assert cut == [], cut
    titles = page.evaluate("""() => [
        ...document.querySelectorAll('.jobs__table .title-link, .jobs__table .sub, .jobs__table .p-row__title')]
        .filter((el) => el.offsetParent).map((el) => {
            const cs = getComputedStyle(el);
            return {text: el.textContent.trim(), title: el.getAttribute('title'), clamp: cs.webkitLineClamp,
                    oneLine: cs.whiteSpace === 'nowrap' && el.clientHeight < 2 * parseFloat(cs.fontSize),
                    ellipsis: cs.textOverflow === 'ellipsis'};
        })""", isolated_context=False)
    assert titles, "no job title drawn"
    for t in titles:
        assert t["title"] == t["text"], t
        assert (t["oneLine"] and t["ellipsis"]) or t["clamp"] == "2", t
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


@pytest.mark.parametrize("width", [1440, 820, 390])
def test_the_provider_cards_or_the_strip_filter_the_list_by_provider(app, app_page, live, db, real_providers, width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 1000 if width > 767 else 844})
    plant(db)
    app.sign_in(page)
    for provider in ("meta", "vibes"):
        claims(page, provider, True)
    claims(page, "flow", False)
    page.reload()
    expect(job_rows(page)).to_have_count(6)
    expect(job_rows(page).first.locator(".pchip")).to_have_class(re.compile(r"\bp-(dola|vibes|meta)\b"))
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)
    strip = page.get_by_role("navigation", name="Provider")
    today = page.get_by_test_id("jobs-today")

    if width >= 1280:
        expect(strip).to_have_count(0)                                    # the cards are the picker on a desktop
        expect(today.locator(".jt-big")).to_have_text("4")                # done today; the failed one apart
        expect(today).to_contain_text("Failed today1")

        def card(name):
            return page.get_by_test_id("provider-card-%s" % name)

        expect(card("dola")).to_contain_text("2 / 3")                     # usable / every account
        expect(card("dola")).to_contain_text("1 failed")
        expect(card("flow")).to_contain_text("Paused")
        expect(job_rows(page).filter(has_text="matcha")).to_contain_text("meta-image")
        no_cut_text(page)
        shot(page, "%d-all" % width)
        card("dola").click()
        expect(card("dola")).to_have_attribute("aria-pressed", "true")
        expect(job_rows(page)).to_have_count(3)
        no_cut_text(page)
        shot(page, "%d-dola" % width)
        card("vibes").click()
        expect(card("vibes")).to_have_attribute("aria-pressed", "true")
        expect(job_rows(page)).to_have_count(2)
        card("vibes").click()
        expect(card("vibes")).to_have_attribute("aria-pressed", "false")
        expect(job_rows(page)).to_have_count(6)
        return

    # All + the four registered providers (the scroller's arrows are no tab)
    expect(strip.locator("[data-testid^=provider-tab-]")).to_have_count(5)
    expect(strip.get_by_test_id("provider-tab-all")).to_have_attribute("aria-pressed", "true")
    expect(strip.get_by_test_id("provider-tab-flow")).to_contain_text("paused")
    expect(strip.get_by_test_id("provider-tab-flow")).to_have_attribute("aria-label", "flow, paused")
    expect(strip.get_by_test_id("provider-tab-vibes")).to_have_text(re.compile(r"^V\s*vibes\s*$"))  # no count
    expect(page.get_by_test_id("provider-card-dola")).to_have_count(0)
    no_cut_text(page)
    shot(page, "%d-all" % width)
    strip.get_by_test_id("provider-tab-dola").click()
    expect(job_rows(page)).to_have_count(3)
    no_cut_text(page)
    shot(page, "%d-dola" % width)
    strip.get_by_test_id("provider-tab-all").click()
    expect(strip.get_by_test_id("provider-tab-all")).to_have_attribute("aria-pressed", "true")
    expect(job_rows(page)).to_have_count(6)
