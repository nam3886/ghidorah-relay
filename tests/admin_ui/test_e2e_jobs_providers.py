"""The provider side of the Jobs page in Chrome against the real app with the four real provider specs (browser tier):
the provider strip under the top bar is the provider filter, the "Jobs today" table reads the provider summary, and
the job rows carry their provider. At a desktop width, a tablet's 820 px and a phone's 390 px, where the table turns into
cards; no text may be cut but a job title, one line ending in an ellipsis. With RELAY_SCREENSHOTS_DIR set, each state is saved as jobs-providers-<width>-<pick>.png."""

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
    return page.locator(".jobs__table tbody tr:not(.is-day), .jobs__table li.data-table__item")


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
    """Nothing in the provider table, the strip's tabs or a job card is cut (the status pill beside a card's title
    included), and the page never scrolls sideways. A job title, a card's or the table's title link, is one line at
    every width: a longer one ends in an ellipsis and carries its whole text in its title attribute (and the detail)."""
    cut = page.evaluate("""() => {
        const sel = '[data-testid=provider-table] *, .ptab, .jobs__table li.data-table__item *';
        const seen = (el) => el.offsetParent && !el.closest('.prow__title, .title-link, .spark, .mini__bar');
        const wide = (el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible';
        return [...document.querySelectorAll(sel)].filter((el) => seen(el) && wide(el))
            .map((el) => el.className + ': ' + el.textContent.trim().slice(0, 40));
    }""", isolated_context=False)
    assert cut == [], cut
    titles = page.evaluate("""() => [...document.querySelectorAll('.jobs__table .prow__title, .jobs__table .title-link')]
        .filter((el) => el.offsetParent).map((el) => {
            const cs = getComputedStyle(el);
            return {text: el.textContent.trim(), title: el.getAttribute('title'),
                    oneLine: cs.whiteSpace === 'nowrap' && el.clientHeight < 2 * parseFloat(cs.fontSize),
                    ellipsis: cs.textOverflow === 'ellipsis', cut: el.scrollWidth > el.clientWidth};
        })""", isolated_context=False)
    assert titles, "no job title drawn"
    for t in titles:
        assert t["oneLine"] and t["ellipsis"] and t["title"] == t["text"], t
    assert all(t["cut"] for t in titles if t["text"].startswith("Lantern")), titles   # cut on one line, not wrapped
    assert sideways(page) == 0, "the page scrolls sideways by %d px" % sideways(page)


@pytest.mark.parametrize("width", [1440, 820, 390])
def test_the_strip_and_the_jobs_today_table_filter_the_list_by_provider(app, app_page, live, db, real_providers,
                                                                        width):
    page = app_page
    page.set_viewport_size({"width": width, "height": 1000 if width > 767 else 844})
    plant(db)
    app.sign_in(page)
    for provider in ("meta", "vibes"):
        claims(page, provider, True)
    claims(page, "flow", False)
    page.reload()
    expect(job_rows(page)).to_have_count(6)

    strip = page.get_by_role("navigation", name="Provider")
    expect(strip.get_by_role("button")).to_have_count(5)            # All + the four registered providers
    expect(strip.get_by_test_id("provider-tab-all")).to_have_attribute("aria-pressed", "true")
    expect(strip.get_by_test_id("provider-tab-flow")).to_contain_text("paused")
    expect(strip.get_by_test_id("provider-tab-flow")).to_have_attribute("aria-label", "flow, paused")
    expect(strip.get_by_test_id("provider-tab-vibes")).to_have_text(re.compile(r"^V\s*vibes\s*$"))  # no count
    expect(page.get_by_role("banner").get_by_test_id("provider-filter")).to_have_count(0)

    table = page.get_by_test_id("provider-table")
    expect(table).to_contain_text("5 finished today · 1 failed")
    row = (lambda n: table.get_by_test_id("provider-row-%s" % n)) if width > 860 else \
        (lambda n: table.get_by_test_id("provider-card-%s" % n))
    expect(row("dola")).to_be_visible()
    expect(row("dola")).to_contain_text("2/3")                      # usable / available + dead + disabled
    expect(row("dola")).to_contain_text("video pts")
    expect(row("flow")).to_contain_text("paused")
    first = job_rows(page).first
    expect(first).to_have_class(re.compile(r"\bp-(dola|vibes|meta)\b"))
    if width > 767:                                                  # the Model cell names the job's own kind
        expect(job_rows(page).filter(has_text="matcha")).to_contain_text("image meta-image")
        expect(job_rows(page).filter(has_text="Dragon")).to_contain_text("image seedream-4.5")
        expect(job_rows(page).filter(has_text="Tokyo")).to_contain_text("video vibes-video")
    no_cut_text(page)
    shot(page, "%d-all" % width)

    # the strip filters the list; a row of the table picks the same provider, and a second click clears it
    strip.get_by_test_id("provider-tab-dola").click()
    expect(job_rows(page)).to_have_count(3)
    expect(row("vibes")).to_have_class(re.compile(r"\bis-dim\b"))
    no_cut_text(page)
    shot(page, "%d-dola" % width)
    row("vibes").click()
    expect(strip.get_by_test_id("provider-tab-vibes")).to_have_attribute("aria-pressed", "true")
    expect(job_rows(page)).to_have_count(2)
    row("vibes").click()
    expect(strip.get_by_test_id("provider-tab-all")).to_have_attribute("aria-pressed", "true")
    expect(job_rows(page)).to_have_count(6)
