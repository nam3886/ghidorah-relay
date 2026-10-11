"""The wait after `goto` (some accounts' pages reload themselves a few seconds after loading) and the retry of a
navigation that happens while the order is being composed. Scripted page, no Chrome."""

from datetime import timedelta
from types import SimpleNamespace

from kit_c import ACCEPTED, NOW, Scene, sync, types

from relay_core.providers import Ambiguous, Cooldown, NotTaken, Retry
from relay_core.testing.mutation import mutation
from relay_dola import constants, errors
from relay_dola.engine.submit import PageDriver

SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
STEP = 0.25


class SettlePage:
    """A page whose waits advance a clock; `navigations` are the times (s after goto) a main-frame navigation fires,
    `forever` makes one fire every wait. The test fails if the loop never ends (a missing cap)."""

    def __init__(self, navigations=(), forever=False, other_frame_at=()):
        self.navigations, self.forever, self.other = list(navigations), forever, list(other_frame_at)
        self.main_frame, self.handlers, self.waited = object(), [], 0.0
        self.removed = False

    def on(self, event, handler):
        assert event == "framenavigated"
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.handlers.remove(handler)
        self.removed = True

    # plain event plumbing for the navigation-watch test
    listeners: dict = {}

    def on_event(self, event, handler):
        self.listeners = {**self.listeners, event: handler}

    def off_event(self, event, handler):
        self.listeners = {k: v for k, v in self.listeners.items() if k != event}

    def fire(self, event):
        if event in self.listeners:
            self.listeners[event]()

    async def goto(self, url, **kw):
        pass

    async def wait_for_timeout(self, ms):
        self.waited += ms / 1000
        assert self.waited < 100, "the settle wait was not capped"
        for t in self.navigations:
            if abs(self.waited - t) < 1e-6:
                for h in list(self.handlers):
                    h(self.main_frame)
        for t in self.other:
            if abs(self.waited - t) < 1e-6:
                for h in list(self.handlers):
                    h(object())
        if self.forever:
            for h in list(self.handlers):
                h(self.main_frame)


async def settle(page) -> float:
    await PageDriver().open_chat(page)
    return page.waited


@mutation(SUB, "SETTLE_MIN_S = 4.0", "SETTLE_MIN_S = 0.0", expect="a quiet page was left before the minimum wait")
@sync
async def test_a_quiet_page_is_left_after_the_minimum_wait_only():
    page = SettlePage()
    waited = await settle(page)
    assert waited == 4.0, "a quiet page was left before the minimum wait or waited too long: %s s" % waited
    assert page.removed and page.handlers == [], "the navigation listener was left on the page"


@mutation(SUB, "                    quiet = 0.0\n", "                    pass\n",
          expect="a navigation did not restart the quiet period")
@sync
async def test_a_navigation_restarts_the_quiet_period_and_a_subframe_does_not():
    waited = await settle(SettlePage(navigations=[3.5]))
    assert waited == 6.5, "a navigation did not restart the quiet period: waited %s s" % waited
    waited = await settle(SettlePage(other_frame_at=[3.5]))
    assert waited == 4.0, "a subframe navigation restarted the quiet period: waited %s s" % waited


@mutation(SUB, "SETTLE_CAP_S = 15.0", "SETTLE_CAP_S = 1000.0", expect="the settle wait was not capped")
@sync
async def test_a_page_that_never_stops_navigating_is_waited_for_at_most_the_cap():
    assert await settle(SettlePage(forever=True)) == 15.0, "the settle wait was not capped"


NAV_ERROR = RuntimeError("Page.evaluate: Execution context was destroyed, most likely because of a navigation.")


FAILED_NAV = "        if PAGE_NAVIGATED in str(exc).lower():\n            until"


@mutation(SUB, FAILED_NAV, "        if False:\n            until",
          expect="a navigation during compose was not a page_reloaded retry")
@sync
async def test_a_navigation_that_comes_twice_is_a_retry_that_uses_no_attempt_and_sends_nothing():
    scene = Scene(build_error=NAV_ERROR)
    res = await scene.run()
    assert res.job == Retry(errors.PAGE_RELOADED), \
        "a navigation during compose was not a page_reloaded retry: %r" % (res.job,)
    assert types(res) == ["Success", "Cooldown"], types(res)      # the login read was good: the strike count is cleared
    assert res.account[1].until == NOW + timedelta(seconds=constants.BROWSER_FAULT_COOLDOWN_S)
    assert isinstance(res.account[1], Cooldown) and "send" not in scene.log
    assert scene.log.count("build") == 2, "the order was composed once more in the same open, not more"
    assert errors.CODES[errors.PAGE_RELOADED].counts_attempt is False


@mutation(SUB, "            await driver.open_chat(page)\n", "            raise\n",
          expect="a reload that landed after the settle wait was not absorbed")
@mutation(SUB, "            await driver.open_chat(page)\n", "", expect="the page was not loaded again")
@sync
async def test_a_reload_after_the_settle_wait_is_absorbed_by_one_settle_and_compose_in_the_same_open():
    scene = Scene(build_error=NAV_ERROR, build_error_times=1, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert not isinstance(res.job, Retry), \
        "a reload that landed after the settle wait was not absorbed: %r" % (res.job,)
    assert scene.log.count("open_chat") == 2, "the page was not loaded again: %r" % scene.log
    assert scene.log.count("build") == 2 and scene.log.count("send") == 1, "the order must be sent once: %r" % scene.log
    assert scene.log.count("mark_sent") == 1


@mutation(SUB, "            if self.sent or PAGE_NAVIGATED not in str(exc).lower():",
          "            if PAGE_NAVIGATED not in str(exc).lower():",
          expect="a navigation after the send was composed again")
@sync
async def test_a_navigation_after_the_send_is_unknown_never_composed_or_sent_again():
    scene = Scene(send_error=NAV_ERROR, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert isinstance(res.job, Ambiguous), \
        "a navigation after the send was composed again: %r" % (res.job,)
    assert scene.log.count("send") == 1 and scene.log.count("open_chat") == 1 and scene.log.count("build") == 1, \
        "a navigation after the send was composed again: %r" % scene.log


class AttachPage:
    """A page for the real `PageDriver.attach`: one input, and a script call that fails with `error`."""

    def __init__(self, error):
        self.error = error
        self.loc = SimpleNamespace(count=self._count, first=SimpleNamespace(set_input_files=self._choose))

    def locator(self, selector):
        return self.loc

    async def _count(self):
        return 1

    async def _choose(self, paths):
        raise self.error

    def on(self, event, handler):
        pass

    def remove_listener(self, event, handler):
        pass


ATTACH_RAISE = ("            if raise_navigation and PAGE_NAVIGATED in str(exc).lower():\n"
                "                raise          # the page moved under the run: the in-open recompose handles it, "
                "not an attach reason\n")


@mutation(SUB, ATTACH_RAISE, "", expect="a navigation during the attach was turned into an attach reason")
@sync
async def test_a_navigation_during_the_attach_is_raised_other_attach_errors_stay_reasons():
    image = [(b"x", "image/png")]
    try:
        reason = await PageDriver().attach(AttachPage(NAV_ERROR), image)
    except RuntimeError:
        reason = None
    else:
        raise AssertionError("a navigation during the attach was turned into an attach reason: %r" % reason)
    assert reason is None
    other = await PageDriver().attach(AttachPage(RuntimeError("upload failed")), image)
    assert other.startswith("attach error:"), other


REGION_RECHECK = ("            if REGION_BLOCK_MARK in await driver.page_url(page):\n"
                  "                return self._region_blocked(\"the page moved to dola's region page while "
                  "composing\")\n")


@mutation(SUB, REGION_RECHECK, "", expect="the region page after the settle was not noticed")
@sync
async def test_a_page_that_moved_to_the_region_page_while_composing_is_the_proxys_fault_not_a_recompose():
    scene = Scene(build_error=NAV_ERROR, build_error_times=1, region_after_settle=True)
    res = await scene.run()
    assert res.job == NotTaken("pool.account_region_blocked"), \
        "the region page after the settle was not noticed: %r" % (res.job,)
    assert scene.log.count("build") == 1 and "send" not in scene.log


MOVED_CHECK = ("        if self.watch is not None and self.watch.moved():\n")


@mutation(SUB, MOVED_CHECK, "        if False:\n", expect="a page load before the send was not seen")
@sync
async def test_a_page_load_between_the_compose_and_the_send_is_a_navigation_before_the_send():
    # absorbed once: the second compose finds a quiet page and sends exactly once
    scene = Scene(moves=[True, False], replies={1: [ACCEPTED]})
    res = await scene.run()
    assert not isinstance(res.job, Retry), "a page load before the send was not seen: %r" % (res.job,)
    assert scene.log.count("build") == 2 and scene.log.count("open_chat") == 2 and scene.log.count("send") == 1, \
        "a page load before the send was not seen: %r" % scene.log
    assert scene.log.count("mark_sent") == 1
    # twice: the second time is page_reloaded and nothing was sent
    again = Scene(moves=[True, True])
    res = await again.run()
    assert res.job == Retry(errors.PAGE_RELOADED), "a page load before the send was not seen"
    assert "send" not in again.log and "mark_sent" not in again.log
    assert all(w.stopped for w in again.driver.watches), "the navigation watch was left running"


@sync
async def test_a_real_navigation_watch_counts_document_loads_not_same_document_moves():
    page = SettlePage()
    watch = PageDriver().watch_navigation(SimpleNamespace(on=page.on_event, remove_listener=page.off_event))
    assert not watch.moved()
    page.fire("framenavigated")                 # a same-document move: not a load
    assert not watch.moved()
    page.fire("domcontentloaded")
    assert watch.moved()
    watch.stop()
    assert page.listeners == {}


# ---- the watch starts after the build; a recompose loads the page afresh ------------------------------------------
BUILD_WATCH = ("            built = await driver.build(page, spec)\n"
               "            self.watch = driver.watch_navigation(page)")
WATCH_BEFORE_BUILD = ("            self.watch = driver.watch_navigation(page)\n"
                      "            built = await driver.build(page, spec)")


@mutation(SUB, BUILD_WATCH, WATCH_BEFORE_BUILD, expect="the build's own reload was counted as a navigation")
@sync
async def test_a_reload_the_build_does_itself_is_not_a_navigation_under_the_run():
    scene = Scene(build_reloads=1, replies={1: [ACCEPTED]})
    res = await scene.run()
    assert not isinstance(res.job, Retry), "the build's own reload was counted as a navigation: %r" % (res.job,)
    assert scene.log.count("build") == 1 and scene.log.count("send") == 1 and scene.log.count("open_chat") == 1, \
        "the build's own reload was counted as a navigation: %r" % scene.log


def one_image_job():
    from kit_c import make_job
    from test_submit_outcomes import media_ref
    return make_job(images=(media_ref(0),))


async def image_bytes(ref):
    return b"img"


@mutation(SUB, "            await driver.open_chat(page)\n", "            await driver.settle(page)\n",
          expect="a recompose sent more images than the order has")
@sync
async def test_a_recompose_starts_from_a_fresh_page_so_the_send_carries_exactly_the_orders_images():
    scene = Scene(job=one_image_job(), read_media=image_bytes, moves=[True, False], replies={1: [ACCEPTED]})
    await scene.run()
    assert scene.log.count("attach") == 2 and scene.log.count("send") == 1, scene.log
    assert scene.driver.images_at_send == [1], \
        "a recompose sent more images than the order has: %r" % scene.driver.images_at_send


@mutation(SUB, "images, raise_navigation=False)", "images)", expect="a navigation during the re-send attach was raised")
@sync
async def test_a_navigation_during_the_re_send_attach_keeps_the_outcome_it_had():
    from kit_c import GATE
    scene = Scene(job=one_image_job(), read_media=image_bytes, new_composer=False,
                  replies={1: [GATE]}, attach_errors={2: NAV_ERROR})
    res = await scene.run()
    assert not isinstance(res.job, Ambiguous), "a navigation during the re-send attach was raised: %r" % (res.job,)
    assert scene.log.count("send") == 1 and scene.log.count("attach") == 2


LOGGED_IN_RESET = "        self.watch, self.logged_in = None, False\n"


@mutation(SUB, LOGGED_IN_RESET, "        self.watch = None\n",
          expect="a guest read after a good one attached a Success")
@sync
async def test_a_guest_read_in_the_second_compose_clears_no_strikes():
    from kit_c import GOOD_PROBE
    from test_submit_outcomes import GUEST
    scene = Scene(build_error=NAV_ERROR, build_error_times=1)
    probes = [GOOD_PROBE, GUEST]

    async def probe(page):
        return probes.pop(0)
    scene.driver.probe = probe
    res = await scene.run()
    assert "Success" not in types(res), "a guest read after a good one attached a Success: %r" % types(res)
