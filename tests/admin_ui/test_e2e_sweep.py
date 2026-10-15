"""The whole-app CSP sweep of the Vue admin, at three widths in both themes, against the real app (browser tier). One
signed-in session walks the login page, all eight tabs and the job detail, and opens every dialog, confirm box, toast,
select popper, dropdown, date field and loading state they have, under the CSP watch of `conftest.CspWatch`: a violation
on either channel fails the sweep, at every width. What it opens is listed per route in `sweep_states/<route>.py`
(`STATES`) and run in the order of `MODULES`; a control that cannot be found fails the step (nothing is caught), and
`finish()` fails when a listed state was not reached, so a renamed button cannot silently shrink the sweep.

With RELAY_SCREENSHOTS_DIR set, every opened state is also saved as a full-page `<width>-<theme>-<route>-<state>.png`
for the design review; with it unset nothing is written. The names of all passes are the exact cross-product of
widths x themes x states, and two states with one name fail the sweep."""

import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from accountkit import done_job, new_account
from jobkit import key as new_key
from patchright.sync_api import expect
from sweep_states import (
    SHOTS_ENV,
    TIMEOUT_MS,
    accounts,
    alerts,
    frames,
    job_drawer,
    jobs,
    keys,
    login,
    logs,
    settings,
    shell,
)
from sweep_states import test as test_tab
from test_admin_alert_groups import plant_unclear
from test_admin_alerts import plant
from test_admin_jobs import failed_job

from relay_core.testing.mutation import mutation

pytestmark = pytest.mark.browser

THEMES = ("light", "dark")
WIDTHS = {1280: 800, 768: 1024, 390: 844}     # width -> height of the viewport: desktop, tablet, phone
DEFAULT_WIDTH = 1280
PASSES = [(width, theme) for width in WIDTHS for theme in THEMES]

# The route modules in the order the sweep walks them, and everything they open, as (route, state).
MODULES = (login, jobs, job_drawer, accounts, keys, logs, alerts, frames, settings, test_tab, shell)
OPENS = [state for module in MODULES for state in module.STATES]


def shot_name(width, theme, route, state):
    return "%d-%s-%s-%s.png" % (width, theme, route, state)


def shot_names(passes, states):
    """The file name of every state in every (width, theme) pass; two states with one name fail."""
    names = [shot_name(width, theme, route, state) for width, theme in passes for route, state in states]
    twice = sorted({n for n in names if names.count(n) > 1})
    assert not twice, "two states write the same screenshot: %s" % twice
    return names


# True once nothing that has a finite end is still moving: every dialog, popper, toast and fade is at rest. A loop that
# never ends (the skeleton shimmer, a spinner) does not count.
AT_REST_JS = """() => document.getAnimations().every(
    a => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)"""
TWO_FRAMES_JS = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


def settle(page):
    """Wait until every transition has finished, so a shot never shows a dialog half faded in. Two frames first: a
    transition only exists once the browser has styled the element that starts it."""
    page.evaluate(TWO_FRAMES_JS)
    page.wait_for_function(AT_REST_JS, timeout=TIMEOUT_MS)


STEP_LIMIT_S = 90


class StepTimeout(AssertionError):
    """A step ran past its hard limit: a step that waits for something that never comes fails, it never hangs."""


@contextmanager
def hard_limit(seconds, what):
    """Interrupt the running step after `seconds` whatever it is waiting on (the Playwright calls have their own
    timeouts; this is the backstop for a wait that has none). Signals reach the main thread only; elsewhere no limit."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def expired(signum, frame):
        raise StepTimeout("%s ran for more than %ss" % (what, seconds))
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Sweep:
    """Runs and records the states of one pass: one width in one theme."""

    def __init__(self, page, theme, expected=OPENS, limit_s=STEP_LIMIT_S, width=DEFAULT_WIDTH):
        self.page, self.theme, self.width = page, theme, width
        self.expected, self.done, self.limit_s, self.shots = list(expected), [], limit_s, []

    def step(self, route, state, action, after=None):
        with hard_limit(self.limit_s, "step %s/%s" % (route, state)):
            action()  # raises when a control is missing: nothing is caught
            self.page.csp.check()
            settle(self.page)
            self.shot(route, state)
            self.done.append((route, state))
            if after:
                after()                            # what to undo once the state has been photographed

    def shot(self, route, state):
        name = shot_name(self.width, self.theme, route, state)
        assert name not in self.shots, "two states write the same screenshot: %s" % name
        self.shots.append(name)
        folder = os.environ.get(SHOTS_ENV)
        if not folder:
            return None
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        return path

    def finish(self):
        missing = [s for s in self.expected if s not in self.done]
        assert not missing, "the sweep did not open: %r" % (missing,)
        want = shot_names([(self.width, self.theme)], self.expected)
        assert sorted(self.shots) == sorted(want), "not exactly the expected screenshots: %r" % (
            sorted(set(self.shots) ^ set(want)),)


def seed(app, db, live):
    k = new_key(db)
    new_account(db, label="alive", checked=True)
    new_account(db, label="gone", state="dead", checked=True)
    filler = new_account(db, label="filler", checked=True)
    for i in range(30):                                  # a Jobs table taller than its box, older than the rest
        done_job(db, k, filler, ref="fill-%d" % i, status="done", prompt="filler %d" % i)
    done_job(db, k, new_account(db, label="worker", checked=True), ref="r1", status="done", prompt="a cat on a roof")
    failed = app.run(db, lambda p: failed_job(p, k, "one"))
    db.one("INSERT INTO request_logs (key_id, provider, route, status_code, duration_ms) VALUES (%s, 'fake', "
           "'/v1/videos', 422, 5) RETURNING id", k)
    plant(db)
    plant_unclear(db, k, "Which style do you want?")
    return {"key": k, "failed": failed}


def run_sweep(page, app, live, sw, data):
    page.set_default_timeout(TIMEOUT_MS)
    for module in MODULES:
        module.run(page, app, live, sw, data)
    page.unroute_all(behavior="wait")              # no late-answer handler may outlive the page
    sw.finish()


@pytest.mark.parametrize("width,color_scheme", PASSES, ids=["%d-%s" % p for p in PASSES])
def test_every_dialog_popper_toast_and_loading_state_runs_under_the_csp_at_every_width_in_both_themes(
        app, app_page, live, db, framed, width, color_scheme):
    app_page.set_viewport_size({"width": width, "height": WIDTHS[width]})
    data = seed(app, db, live)
    data["raw_key"] = live.call(live.env.new_key()).raw
    run_sweep(app_page, app, live, Sweep(app_page, color_scheme, width=width), data)
    # the sweep provokes a refused save, which the browser logs as a failed load; nothing else may be in the list
    unexpected = [p for p in app_page.problems if "Failed to load resource" not in p]
    assert not unexpected, unexpected
    app_page.problems.clear()


# ---- the sweep's own guards -----------------------------------------------------------------------------------------

SELF = "tests/admin_ui/test_e2e_sweep.py"


# The anchors are written as two adjacent literals, so the marker's own text is not a second match of its anchor.
@mutation(SELF, 'THEMES = ("light", ' '"dark")', 'THEMES = ("light",)', expect="{'light'}")
def test_the_sweep_covers_both_themes():
    assert set(THEMES) == {"light", "dark"}


@mutation(SELF, ", 390: " "844}", "}", expect="assert [768, 1280] == [390, 768, 1280]")
def test_the_sweep_runs_at_desktop_tablet_and_phone_widths():
    assert sorted(WIDTHS) == [390, 768, 1280]


@mutation(SELF, "assert not " "twice,", "assert True or twice,", expect="DID NOT RAISE")
def test_the_screenshot_names_are_the_exact_cross_product_of_widths_themes_and_states():
    names = shot_names(PASSES, OPENS)
    assert len(PASSES) == len(set(PASSES)) == len(WIDTHS) * len(THEMES)
    assert len(names) == len(WIDTHS) * len(THEMES) * len(OPENS)
    assert set(names) == {"%d-%s-%s-%s.png" % (w, t, r, s) for w in WIDTHS for t in THEMES for r, s in OPENS}
    assert "390-dark-login-form.png" in names and "1280-light-shell-multiple-select-tick.png" in names
    with pytest.raises(AssertionError, match="two states write the same screenshot"):
        shot_names(PASSES, OPENS + [OPENS[0]])


@mutation(SELF, "action()  # raises when " "a control is missing: nothing is caught",
          "try:\n                action()\n            except AssertionError:\n                pass",
          expect="DID NOT RAISE")
def test_a_listed_control_that_cannot_be_found_fails_the_step_and_the_sweep(app, app_page):
    page = app_page
    page.set_default_timeout(TIMEOUT_MS)                   # every wait here has its own explicit limit, none is shared
    page.goto(app.url + "#/login")
    sw = Sweep(page, "light", expected=[("login", "form"), ("login", "ghost")], limit_s=60)
    sw.step("login", "form", lambda: expect(page.get_by_role("button", name="Sign in")).to_be_visible(
        timeout=TIMEOUT_MS))
    with pytest.raises(AssertionError):
        sw.step("login", "ghost", lambda: expect(page.get_by_role("button", name="No such button")).to_be_visible(
            timeout=1500))
    with pytest.raises(AssertionError, match="did not open"):
        sw.finish()


@mutation(SELF, "signal.setitimer(signal.ITIMER_REAL, " "seconds)", "pass", expect="DID NOT RAISE")
def test_a_step_that_never_ends_is_stopped_by_its_hard_limit(app, app_page):
    app_page.goto(app.url + "#/login")
    sw = Sweep(app_page, "light", expected=[("login", "stuck")], limit_s=1)
    started = time.monotonic()
    with pytest.raises(StepTimeout, match="step login/stuck ran for more than 1s"):
        sw.step("login", "stuck", lambda: time.sleep(4))
    assert time.monotonic() - started < 3.5
    assert ("login", "stuck") not in sw.done


@mutation(SELF, "assert not " "missing,", "assert True or missing,", expect="Regex pattern did not match")
def test_a_state_that_was_never_reached_fails_the_sweep(app, app_page):
    sw = Sweep(app_page, "dark", expected=[("jobs", "list")])
    with pytest.raises(AssertionError, match="did not open"):
        sw.finish()


@mutation(SELF, "assert sorted(self.shots) " "== sorted(want)", "assert True or sorted(want)",
          expect="DID NOT RAISE")
def test_a_state_that_was_not_listed_fails_the_sweep(app, app_page):
    page = app_page
    page.goto(app.url + "#/login")
    sw = Sweep(page, "light", expected=[("login", "form")])
    sw.step("login", "form", lambda: None)
    sw.step("login", "extra", lambda: None)
    with pytest.raises(AssertionError, match="not exactly the expected screenshots"):
        sw.finish()


@mutation(SELF, "assert name not " "in self.shots,", "assert True or name not in self.shots,",
          expect="DID NOT RAISE")
def test_a_second_state_with_the_same_name_fails_the_step(app, app_page, monkeypatch):
    page = app_page
    page.goto(app.url + "#/login")
    monkeypatch.delenv(SHOTS_ENV, raising=False)           # the check holds with no folder as well
    sw = Sweep(page, "light", expected=[("login", "form")])
    sw.step("login", "form", lambda: None)
    with pytest.raises(AssertionError, match="two states write the same screenshot"):
        sw.step("login", "form", lambda: None)


PROBE = """() => {
    const d = document.createElement('div');
    d.id = 'probe';
    d.textContent = 'x';
    document.body.appendChild(d);
    d.animate([{opacity: 0}, {opacity: 1}], {duration: 800, fill: 'forwards'});
    const loop = document.createElement('div');
    loop.id = 'loop';
    document.body.appendChild(loop);
    loop.animate([{opacity: 0.5}, {opacity: 1}], {duration: 300, iterations: Infinity});
}"""
OPACITY = "id => getComputedStyle(document.getElementById(id)).opacity"


@mutation(SELF, "page.wait_for_function(AT_REST_JS, " "timeout=TIMEOUT_MS)", "pass",
          expect="the shot was taken while a transition was still running")
def test_a_shot_waits_for_a_running_transition_but_not_for_one_that_never_ends(app, app_page):
    page = app_page
    page.goto(app.url + "#/login")
    page.evaluate(PROBE, isolated_context=False)
    settle(page)
    assert page.evaluate(OPACITY, "probe", isolated_context=False) == "1", \
        "the shot was taken while a transition was still running"
    assert page.evaluate(OPACITY, "loop", isolated_context=False) != "1"        # the loop is still going: no wait


def test_screenshots_are_saved_only_when_a_folder_is_given(app, app_page, tmp_path, monkeypatch):
    page = app_page
    page.goto(app.url + "#/login")
    monkeypatch.delenv(SHOTS_ENV, raising=False)
    Sweep(page, "light").step("login", "form", lambda: None)
    assert not list(tmp_path.iterdir()), "nothing is written without the variable"
    monkeypatch.setenv(SHOTS_ENV, str(tmp_path / "shots"))
    Sweep(page, "light").step("login", "form", lambda: None)
    files = [p.name for p in (tmp_path / "shots").iterdir()]
    assert files == ["1280-light-login-form.png"], files
    assert (tmp_path / "shots" / files[0]).stat().st_size > 1000
