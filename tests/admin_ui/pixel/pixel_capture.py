"""The app side: put the app in the state of an artboard and photograph it at the board's viewport."""

from patchright.sync_api import expect

TIMEOUT_MS = 10_000
TIMEZONE = "Asia/Saigon"            # the zone the boards' times are written in
TWO_FRAMES_JS = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
AT_REST_JS = """() => document.getAnimations().every(
    a => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)"""


def settle(page) -> None:
    """Fonts loaded, two frames drawn, every finite transition finished."""
    page.evaluate("document.fonts.ready.then(() => true)", isolated_context=False)
    page.evaluate(TWO_FRAMES_JS)
    page.wait_for_function(AT_REST_JS, timeout=TIMEOUT_MS)


def capture(app, db, live, entry, dark: bool, width: int, height: int) -> bytes:
    """A viewport screenshot (or the entry's region of it) of the app in the state `entry` describes."""
    data = entry.seed(db, live) if entry.seed else None
    context, page = app.new_page("dark" if dark else "light")
    try:
        # the shared page factory takes no time zone: set it on the page's own session before anything loads
        context.new_cdp_session(page).send("Emulation.setTimezoneOverride", {"timezoneId": TIMEZONE})
        page.set_viewport_size({"width": width, "height": height})
        if entry.now:
            context.clock.set_fixed_time(entry.now)
        if entry.signed_in:
            app.sign_in(page)
        page.goto(app.url + entry.route)
        if entry.drive:
            entry.drive(page, live, data)
        expect(page.locator("body")).to_be_visible()
        settle(page)
        clip = None
        if entry.region:
            clip = dict(zip(("x", "y", "width", "height"), entry.region, strict=True))
        png = page.screenshot(clip=clip, caret="hide", animations="disabled")
        page.csp.check()
        # a script error, a failed resource or a console error means the photo may be of a half-drawn app
        assert not page.problems, "the app reported problems while %s was drawn: %r" % (entry.name, page.problems)
        return png
    finally:
        context.close()
