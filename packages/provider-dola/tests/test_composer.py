"""dola's composer driver (coverage COMP00/COMP01) against a scripted page: the pure parts (chip
patterns, label maps, step error codes) and the steps themselves on a small simulation of the two composers (old: a
duration chip and a Ratio chip; new: ONE merged chip with a panel). No Chrome."""

import ast
import inspect
import re

import pytest
from kit_c import SendPage, sync

from relay_core.testing.mutation import mutation
from relay_dola import errors
from relay_dola.engine import composer
from relay_dola.engine.composer import ComposerError

CO = "packages/provider-dola/src/relay_dola/engine/composer.py"
MERGED_CHIP_RIGHT = '    if "·" not in chip["text"]:\n        return (await select_video_duration'
MERGED_CHIP_WRONG = '    if "·" in chip["text"]:\n        return (await select_video_duration'


# ---- a scripted page ------------------------------------------------------------------------------------------------
class Mouse:
    def __init__(self, page):
        self.page = page

    async def click(self, x, y):
        self.page.clicked(x)


class Keyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        self.page.pressed(key)


class Slider:
    def __init__(self, page):
        self.page = page
        self.first = self

    async def focus(self):
        self.page.slider_focused = True


class Pg:
    """A page that answers the composer's JS by identity of the script, and reacts to clicks and keys.

    `chips` is what the chip row shows; `offers` the items of an open dropdown; a click on a chip opens its dropdown (or
    the panel), a click on an item applies it when `take` is true. The new composer is `merged=True`: one chip, a panel
    with ratio tiles and a slider that Home/ArrowRight move and Escape confirms."""

    def __init__(self, *, chips: dict[str, str] | None = None, offers: dict[str, list[str]] | None = None,
                 take: bool = True, merged: bool = False, slider_range=(4, 30), tiles=("16:9", "9:16"),
                 buttons=("Create Videos",), hydrate_after: int = 0, overflow: tuple | None = None,
                 overflow_chip: str = "Model Seedream 4.5"):
        self.chips = dict(chips or {})
        self.offers = offers or {}
        self.take, self.merged, self.slider_range, self.tiles = take, merged, slider_range, tiles
        self.buttons, self.hydrate_after = set(buttons), hydrate_after
        self.open: str | None = None
        self.mouse, self.keyboard = Mouse(self), Keyboard(self)
        self.slider_value, self.slider_focused, self.ratio = 4, False, "Auto"
        self.pressed_keys: list[str] = []
        self.pressed_buttons: list[str] = []
        self.reloads = 0
        self.waits = 0
        self.items: list[str] = []
        # The row folded behind "···": None = no such button; else the items of its menu. Choosing one shows
        # `overflow_chip` as the Model chip ("" = nothing appears).
        self.overflow, self.overflow_chip, self.menu_open = overflow, overflow_chip, False
        self.menu_clicks: list[str] = []

    # -- the page API the composer uses
    def locator(self, selector):
        return Slider(self)

    async def wait_for_timeout(self, ms):
        self.waits += 1

    async def reload(self, **kw):
        self.reloads += 1

    async def evaluate(self, js, arg=None):
        if js is composer._RECT_JS:
            for name, text in self.chips.items():
                if re.search(arg, text):
                    return {"x": list(self.chips).index(name), "y": 0, "text": text}
            return None
        if js is composer._OPTION_JS:
            self.items = self._open_items()
            for i, text in enumerate(self.items):
                if re.search(arg, text):
                    return {"x": 1000 + i, "y": 0, "text": text}
            return None
        if js is composer._OPTIONS_JS:
            return [t for t in self._open_items() if re.search(arg, t)]
        if js is composer._FIND_TEXT_JS:
            return arg in self.buttons and self.hydrate_after <= len(self.pressed_buttons) + 99
        if js is composer._CLICK_TEXT_JS:
            self.pressed_buttons.append(arg)
            return True
        if js is composer._OVERFLOW_JS:
            return {"x": 5000, "y": 0, "text": "···"} if self.overflow is not None else None
        if js is composer._VISIBLE_TEXT_JS:
            if self.menu_open and arg in self.overflow:
                return {"x": 6000 + self.overflow.index(arg), "y": 0, "text": arg}
            return None
        if js is composer._SLIDER_RANGE_JS:
            return {"min": self.slider_range[0], "max": self.slider_range[1]} if self.merged else None
        raise AssertionError("an unscripted script reached the page")

    def _open_items(self) -> list[str]:
        if self.open is None:
            return []
        if self.merged:
            return list(self.tiles) + ["%ds" % self.slider_range[0]]
        return list(self.offers.get(self.open, []))

    # -- reactions
    def clicked(self, x):
        if x == 5000:
            self.menu_open = True
            self.menu_clicks.append("···")
            return
        if x >= 6000:
            self.menu_open = False
            self.menu_clicks.append(self.overflow[x - 6000])
            if self.overflow_chip:
                self.chips["model"] = self.overflow_chip
            return
        if x < 1000:
            self.open = list(self.chips)[x]
            return
        text = self.items[x - 1000]
        if not self.take:
            return
        if self.merged:
            self.ratio = text
            return
        if self.open == "duration":
            self.chips["duration"] = text
            self.open = None
        elif self.open in ("ratio", "image_ratio"):
            self.open = None
            self.chips[self.open or "ratio"] = self.chips.get("ratio", "Ratio")
        elif self.open in ("model", "style"):
            self.chips[self.open] = text if self.open == "style" else "Model " + text
            self.open = None

    def pressed(self, key):
        self.pressed_keys.append(key)
        if key == "Escape":
            self.menu_open = False
        if key == "Home":
            self.slider_value = self.slider_range[0]
        elif key == "ArrowRight":
            self.slider_value += 1
        elif key == "Escape" and self.merged:
            self.open = None
            if self.take:
                self.chips["combo"] = "%s · %ds" % (self.ratio, self.slider_value)


def old_page(**kw):
    chips = {"model": "Model Dreamina Seedance 2.5", "duration": "10s", "ratio": "Ratio"}
    return Pg(chips=chips, offers={"duration": ["5s", "10s", "30s"], "ratio": ["1:1", "16:9", "9:16"]}, **kw)


def new_page(**kw):
    chips = {"model": "Model Dreamina Seedance 2.5", "combo": "Auto · 10s"}
    return Pg(chips=chips, merged=True, **kw)


# ---- pure parts ---------------------------------------------------------------------------------------------------
def test_chip_patterns_carry_no_backslash():
    # a backslash does not survive the Python -> JS escaping layers: `[0-9]`, never `\d`
    for pattern in (composer.MODEL_CHIP, composer.DURATION_CHIP, composer.STYLE_CHIP, composer.COMBO_CHIP,
                    composer.IMAGE_RATIO_CHIP, composer.RATIO_CHIP):
        assert "\\" not in pattern, "pattern %r has a backslash: it breaks on its way into the page" % pattern


@mutation(CO, 'DURATION_CHIP = "(?:^|·| )[0-9]+ *s$"', 'DURATION_CHIP = "^[0-9]+ *s$"', expect="duration chip pattern")
def test_the_duration_chip_pattern_follows_dolas_label_changes():
    # measured 23/09 from the failing screen: the chip read `Auto · 10s`, no longer `10s`
    for label in ("10s", "30 s", "Auto · 10s", "Auto · 5s", "16:9 · 30s", "21:9 ·  4s"):
        assert re.search(composer.DURATION_CHIP, label), "duration chip pattern misses the real label %r" % label
    for label in ("Model Dreamina Seedance 2.5", "Ratio", "Create Videos"):
        assert not re.search(composer.DURATION_CHIP, label), "duration chip pattern catches another chip: %r" % label


def test_the_merged_chip_is_told_from_the_old_duration_chip():
    for label in ("Auto · 10s", "16:9 · 30s", "21:9 ·  4s"):
        assert re.search(composer.COMBO_CHIP, label)
    assert not re.search(composer.COMBO_CHIP, "10s"), "the old duration chip was taken for the merged one"


def test_the_ratio_chip_patterns():
    assert re.match(composer.RATIO_CHIP, "Ratio") and not re.match(composer.RATIO_CHIP, "16:9")
    for label in ("Ratio", "Aspect Ratio Auto", "Aspect Ratio 16:9", "Aspect ratio Auto"):
        assert re.match(composer.IMAGE_RATIO_CHIP, label), "image ratio chip %r is not found" % label
    assert not re.match(composer.IMAGE_RATIO_CHIP, "16:9"), "a ratio tile was taken for the chip"


def test_the_label_maps_are_the_catalogs_and_complete():
    assert composer.MODEL_LABELS["seedance_v2.5"] == "Dreamina Seedance 2.5"
    assert "2.0 Fast" in composer.MODEL_LABELS["seedance_v2.0"]
    models = {"seedance_v2.5", "seedance_v2.0", "seedance_v1.0"}
    assert set(composer.MODEL_LABELS) == set(composer.MODEL_VERSION) == models
    assert composer.MODEL_VERSION["seedance_v1.0"] == "1.0" and "1.0" in composer.MODEL_LABELS["seedance_v1.0"]
    assert composer.IMAGE_MODEL_LABELS["seedream-4.5"] == "Seedream 4.5"
    assert composer.RATIOS == ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9") and composer.DEFAULT_RATIO == "16:9"
    assert set(composer.IMAGE_RATIOS) >= {"1:1", "2:3", "16:9"} and "21:9" not in composer.IMAGE_RATIOS
    assert composer.IMAGE_STYLE_LABELS["anime"] == "Anime" and len(composer.IMAGE_STYLE_LABELS) == 13


def test_the_scripts_read_the_signals_the_steps_depend_on():
    assert "innerHeight*0.6" in composer._RECT_JS, "chips must be looked for in the composer area, the lower screen"
    assert "getBoundingClientRect" in composer._OPTION_JS, "a real click needs coordinates"
    assert "role=slider" in composer._SLIDER_RANGE_JS and "Duration" in composer._SLIDER_RANGE_JS


def test_the_composer_is_a_textarea_or_a_contenteditable_and_the_kit_reads_both():
    from relay_browser import ui
    assert "contenteditable" in ui._COMPOSER_TEXT_JS and "textarea" in ui._COMPOSER_TEXT_JS


def test_the_chip_readers_follow_dolas_label_changes():
    assert composer._seconds_on_chip("Auto · 10s") == 10 and composer._seconds_on_chip("30s") == 30
    assert composer._seconds_on_chip("Ratio") is None and composer._seconds_on_chip(None) is None
    # `Auto · 10s` says dola is ABOUT to make 10 s, not that we set 10 s
    assert composer._is_auto("Auto · 10s") and not composer._is_auto("10s")
    assert composer._chip_says("16:9 ·  30s") == "16:9 · 30s"


def test_every_step_raises_the_error_code_of_its_own_step():
    """Before, every composer failure carried one code, so "no duration chip" and "could not select the model" could
    not be told apart in the data."""
    expected = {"enter_video_mode": "UI_MODE_NOT_READY", "enter_image_mode": "UI_MODE_NOT_READY",
                "select_video_model": "UI_MODEL_SELECT_FAILED", "select_image_model": "UI_MODEL_SELECT_FAILED",
                "select_video_duration": "UI_DURATION_PATCH_FAILED",
                "select_video_ratio_duration": "UI_DURATION_PATCH_FAILED",
                "_set_ratio_duration_popover": None,      # mixed: ratio and duration steps, checked below
                "select_video_ratio": "UI_RATIO_SELECT_FAILED", "select_image_ratio": "UI_RATIO_SELECT_FAILED",
                "select_image_style": "UI_STYLE_SELECT_FAILED", "send_prompt": "UI_SEND_FAILED"}
    tree = ast.parse(inspect.getsource(composer))
    seen = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name not in expected:
            continue
        seen.add(fn.name)
        for node in ast.walk(fn):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                    and getattr(node.exc.func, "id", "") == "ComposerError":
                code = {k.arg: ast.unparse(k.value) for k in node.exc.keywords}.get("code")
                assert code is not None, "%s raises a ComposerError with no code" % fn.name
                if expected[fn.name]:
                    assert code == expected[fn.name], (
                        "%s raises code %s, expected %s" % (fn.name, code, expected[fn.name])
                    )
                else:
                    assert code in ("UI_RATIO_SELECT_FAILED", "UI_DURATION_PATCH_FAILED"), code
    assert seen == set(expected), "a composer step is not covered: %s" % (set(expected) - seen)


def test_the_codes_are_the_registered_ui_codes():
    assert composer.UI_DURATION_PATCH_FAILED == errors.UI_DURATION_PATCH_FAILED == "dola.ui_duration_patch_failed"
    assert composer.ComposerError("x").code == errors.UI_SEND_FAILED


# ---- the steps on a scripted page ---------------------------------------------------------------------------------
@sync
async def test_the_model_is_read_back_after_the_pick():
    page = Pg(chips={"model": "Model 2.0"}, offers={"model": ["Dreamina Seedance 2.5"]})
    assert "2.5" in await composer.select_video_model(page, "seedance_v2.5")
    assert page.chips["model"].endswith("Dreamina Seedance 2.5")
    stuck = Pg(chips={"model": "Model 2.0"}, offers={"model": ["Dreamina Seedance 2.5"]}, take=False)
    with pytest.raises(ComposerError, match="did not take") as err:
        await composer.select_video_model(stuck, "seedance_v2.5")
    assert err.value.code == errors.UI_MODEL_SELECT_FAILED


@sync
async def test_an_unknown_model_is_refused_before_any_click():
    page = Pg(chips={"model": "Model 2.0"})
    with pytest.raises(ComposerError) as err:
        await composer.select_video_model(page, "seedance_v9")
    assert err.value.code == errors.UI_MODEL_SELECT_FAILED and not page.items


@sync
async def test_the_old_composer_picks_the_duration_and_the_ratio_and_says_it_is_old():
    page = old_page()
    duration, ratio, new = await composer.select_video_ratio_duration(page, "16:9", 30)
    assert new is False, "the old composer was reported as the new one"
    assert page.chips["duration"] == "30s" and "30s" in duration and "16:9" in ratio


@mutation(CO, MERGED_CHIP_RIGHT, MERGED_CHIP_WRONG, expect="duration dropdown has no item")
@sync
async def test_the_new_composer_is_told_by_the_merged_chip_and_driven_through_its_panel():
    page = new_page()
    duration, ratio, new = await composer.select_video_ratio_duration(page, "16:9", 30)
    assert new is True, "the merged chip (a new composer) was driven as the old one"
    assert page.chips["combo"] == "16:9 · 30s", "the merged chip does not say 16:9 · 30s: %r" % page.chips
    keys = page.pressed_keys
    assert keys[0] == "Home" and keys.count("ArrowRight") == 26 and keys[-1] == "Escape"
    assert duration == "16:9 · 30s"


@sync
async def test_a_merged_chip_already_right_is_left_alone():
    page = new_page()
    page.chips["combo"] = "16:9 · 30s"
    await composer.select_video_ratio_duration(page, "16:9", 30)
    assert not page.pressed_keys and page.open is None, "a chip that already says what we want was clicked anyway"


@mutation(CO, '    if not _is_auto(chip["text"]) and _seconds_on_chip(chip["text"]) == seconds:',
          '    if _seconds_on_chip(chip["text"]) == seconds:', expect="auto mode")
@sync
async def test_the_auto_chip_is_not_taken_as_a_chosen_duration():
    page = old_page()
    page.chips["duration"] = "Auto · 30s"
    await composer.select_video_duration(page, 30)
    assert page.chips["duration"] == "30s", "auto mode was read as set: dola picks the length itself (auto mode)"
    # a chip that is set to the same number is left alone
    quiet = old_page()
    quiet.chips["duration"] = "30s"
    await composer.select_video_duration(quiet, 30)
    assert quiet.open is None


@sync
async def test_a_missing_level_says_what_dola_offers_and_that_the_patch_failed():
    page = old_page()
    page.offers["duration"] = ["5s", "10s"]
    with pytest.raises(ComposerError, match=r"available: 10s/5s|available: 5s/10s") as err:
        await composer.select_video_duration(page, 30)
    assert err.value.code == errors.UI_DURATION_PATCH_FAILED and "config not patched" in str(err.value)


@sync
async def test_a_duration_that_did_not_take_is_an_error():
    page = old_page(take=False)
    with pytest.raises(ComposerError, match="did not take") as err:
        await composer.select_video_duration(page, 30)
    assert err.value.code == errors.UI_DURATION_PATCH_FAILED


@sync
async def test_no_duration_chip_is_a_patch_failure_code():
    page = Pg(chips={"model": "Model 2.5"})
    with pytest.raises(ComposerError, match="duration chip not found") as err:
        await composer.select_video_ratio_duration(page, "16:9", 30)
    assert err.value.code == errors.UI_DURATION_PATCH_FAILED


@sync
async def test_the_panel_refuses_a_ratio_it_does_not_offer_and_a_duration_outside_the_slider():
    page = new_page(tiles=("1:1",))
    with pytest.raises(ComposerError, match="not in the ratio/duration panel") as err:
        await composer.select_video_ratio_duration(page, "16:9", 30)
    assert err.value.code == errors.UI_RATIO_SELECT_FAILED
    capped = new_page(slider_range=(4, 10))
    with pytest.raises(ComposerError, match="outside the slider") as err:
        await composer.select_video_ratio_duration(capped, "16:9", 30)
    assert err.value.code == errors.UI_DURATION_PATCH_FAILED, "a slider capped at 10 s means the patch did not apply"
    with pytest.raises(ComposerError, match="unsupported ratio") as err:
        await composer.select_video_ratio_duration(new_page(), "7:5", 30)
    assert err.value.code == errors.UI_RATIO_SELECT_FAILED


@sync
async def test_the_panel_checks_the_chip_after_it_closes():
    page = new_page(take=False)
    with pytest.raises(ComposerError, match="did not take") as err:
        await composer.select_video_ratio_duration(page, "16:9", 30)
    assert err.value.code == errors.UI_DURATION_PATCH_FAILED


@mutation(CO, "    if len(still) >= len(offered):\n        raise ComposerError(\"ratio selection %s did not take",
          "    if False:\n        raise ComposerError(\"ratio selection %s did not take", expect="dropdown still open")
@sync
async def test_the_old_ratio_pick_is_proven_by_the_dropdown_closing():
    page = old_page()
    assert "16:9" in await composer.select_video_ratio(page, "16:9")
    stuck = old_page(take=False)
    with pytest.raises(ComposerError, match="dropdown still open") as err:
        await composer.select_video_ratio(stuck, "16:9")
    assert err.value.code == errors.UI_RATIO_SELECT_FAILED


@sync
async def test_video_mode_is_entered_with_a_second_press_when_the_first_did_not_take():
    page = Pg(chips={}, buttons=("Create Videos",))
    # the chip row appears only after a second press (late hydration)
    original = page.evaluate

    async def evaluate(js, arg=None):
        if js is composer._RECT_JS and len(page.pressed_buttons) >= 2:
            return {"x": 0, "y": 0, "text": "Model 2.5"}
        if js is composer._RECT_JS:
            return None
        return await original(js, arg)
    page.evaluate = evaluate
    assert await composer.enter_video_mode(page) == "Model 2.5"
    assert page.pressed_buttons == ["Create Videos", "Create Videos"], "the press was not repeated"


@sync
async def test_a_button_that_never_shows_is_a_mode_error():
    page = Pg(chips={}, buttons=())
    with pytest.raises(ComposerError, match="could not find the 'Create Videos' button") as err:
        await composer.enter_video_mode(page, settle_s=1)
    assert err.value.code == errors.UI_MODE_NOT_READY
    with pytest.raises(ComposerError, match="could not find the 'Create Images' button"):
        await composer.enter_image_mode(page, settle_s=1)


OVERFLOW_ITEM = "    item = await page.evaluate(_VISIBLE_TEXT_JS, label)\n"


@mutation(CO, '        return await _mode_from_overflow_menu(page, "Create Images")\n',
          '        raise ComposerError("could not find the \'Create Images\' button", code=UI_MODE_NOT_READY)\n',
          expect="the folded row was not entered through its menu")
@mutation(CO, OVERFLOW_ITEM, "    item = trigger\n", expect="the folded row was not entered through its menu")
@sync
async def test_a_row_folded_behind_its_dots_menu_is_entered_through_it_and_only_through_a_visible_item():
    """23/10: on a first run the composer row read `+ | Fast ···`, with no Create Images button: its buttons were
    folded into the "···" menu. The mode is entered through that menu, by a real click on the item; a menu without the
    item is a mode error before anything is sent, never a guess."""
    page = Pg(chips={}, buttons=(), overflow=("Create Videos", "Create Images", "Writing"))
    try:
        got = await composer.enter_image_mode(page, settle_s=1)
    except ComposerError as exc:
        got = "error: %s" % exc
    assert got == "Model Seedream 4.5", "the folded row was not entered through its menu: %r" % got
    assert page.menu_clicks == ["···", "Create Images"] and page.pressed_buttons == [], \
        "the folded row was not entered through its menu by real clicks: %r" % page.menu_clicks
    video = Pg(chips={}, buttons=(), overflow=("Create Videos",), overflow_chip="Model Dreamina Seedance 2.5")
    assert await composer.enter_video_mode(video, settle_s=1) == "Model Dreamina Seedance 2.5"
    assert video.menu_clicks == ["···", "Create Videos"]
    without = Pg(chips={}, buttons=(), overflow=("Writing", "Translate"))
    with pytest.raises(ComposerError, match="nor the item") as err:
        await composer.enter_image_mode(without, settle_s=1)
    assert err.value.code == errors.UI_MODE_NOT_READY and without.menu_clicks == ["···"]
    assert "Escape" in without.pressed_keys and not without.menu_open, "the menu was left open"
    blank = Pg(chips={}, buttons=(), overflow=("Create Images",), overflow_chip="")
    with pytest.raises(ComposerError, match="chip row did not appear after choosing Create Images") as err:
        await composer.enter_image_mode(blank, settle_s=1)
    assert err.value.code == errors.UI_MODE_NOT_READY


def test_the_button_scripts_take_only_an_element_on_screen():
    # a hidden copy (a folded menu, a measuring layer) is never taken for the button: the fold reaches the menu
    for js in (composer._FIND_TEXT_JS, composer._CLICK_TEXT_JS, composer._VISIBLE_TEXT_JS):
        assert "getBoundingClientRect" in js and "r.width > 0 && r.height > 0" in js
    assert "overflow-list" in composer._OVERFLOW_JS and "innerHeight*0.6" in composer._OVERFLOW_JS


@sync
async def test_the_composer_kind_is_read_from_the_chip():
    assert await composer.read_composer_kind(new_page()) == "new"
    assert await composer.read_composer_kind(old_page()) == "old"
    assert await composer.read_composer_kind(Pg(chips={"model": "Model 2.5"})) is None


@sync
async def test_the_image_ratio_and_style_steps_prove_their_pick():
    page = Pg(chips={"image_ratio": "Ratio", "style": "Style"},
              offers={"image_ratio": ["16:9 (widescreen)"], "style": ["Anime"]})
    assert "16:9" in await composer.select_image_ratio(page, "16:9")
    assert await composer.select_image_style(page, "anime") == "Anime"
    for call in (composer.select_image_ratio(page, "21:9"), composer.select_image_style(page, "nope")):
        with pytest.raises(ComposerError):
            await call


@mutation(CO, '    new_ui = chip["text"].lower().startswith("aspect ratio")',
          '    new_ui = chip["text"].startswith("Aspect Ratio")', expect="the lower-case ratio chip")
@mutation(CO, 'IMAGE_RATIO_CHIP = "^(Ratio|Aspect [Rr]atio.*)$"', 'IMAGE_RATIO_CHIP = "^(Ratio|Aspect Ratio.*)$"',
          expect="the lower-case ratio chip")
@sync
async def test_the_lower_case_aspect_ratio_chip_is_the_new_composers():
    # 25/10 dola wrote the chip `Aspect ratio Auto` (fixtures/dola/composer-261025/image_row.html): a chip already on
    # the asked ratio is the new composer's and needs no click; read as the old one it looks for a dropdown and fails
    page = Pg(chips={"image_ratio": "Aspect ratio 16:9"})
    try:
        got = await composer.select_image_ratio(page, "16:9")
    except ComposerError as exc:
        got = "error: %s" % exc
    assert got == "Aspect ratio 16:9", "the lower-case ratio chip was not read as the new composer's: %r" % got


UI = "packages/relay-browser/src/relay_browser/ui.py"


def hook(log, *, error=None):
    async def before_enter():
        log.append("before_enter")
        if error:
            raise error
    return before_enter


@sync
async def test_the_marker_hook_runs_after_the_typing_and_right_before_enter():
    log = []
    page = SendPage(log)
    await composer.send_prompt(page, "a lantern\n over a lake", before_enter=hook(log))
    assert log == ["before_enter", "Enter"], "the hook must come right before Enter, and nothing is clicked"
    assert page.entered == ["a lantern over a lake"] and page.text == "", "the prompt was not typed before Enter"


@mutation(UI, "CLEAR_WAIT_S = 30.0 ", "CLEAR_WAIT_S = 0.0 ", expect="while the input was still clearing")
@sync
async def test_a_slow_clearing_input_is_waited_for_with_the_kits_one_wait():
    log = []
    page = SendPage(log, clear_after=20)         # the text stays for 20 waits (5 s) after a send that WORKED
    try:
        await composer.send_prompt(page, "x", before_enter=hook(log))
        failed = None
    except ComposerError as exc:
        failed = exc
    assert failed is None and log == ["before_enter", "Enter"], (
        "the send was given up while the input was still clearing: %r %r" % (failed, log)
    )


@mutation(UI, "CLEAR_WAIT_S = 30.0 ", "CLEAR_WAIT_S = 10.0 ", expect="a send that took 20 s to clear was given up")
@sync
async def test_an_input_that_clears_after_twenty_seconds_is_still_a_send_that_worked():
    log = []
    page = SendPage(log, clear_after=80)         # 80 waits of 250 ms = 20 s: measured sends took up to 13.5 s
    try:
        await composer.send_prompt(page, "x", before_enter=hook(log))
        failed = None
    except ComposerError as exc:
        failed = exc
    assert failed is None, "a send that took 20 s to clear was given up: %r" % (failed,)


@sync
async def test_an_input_that_stays_full_is_one_enter_never_a_click_and_a_send_error():
    log = []
    page = SendPage(log, clear_after=-1)         # Enter does nothing; any other script (a Send click) raises
    with pytest.raises(ComposerError, match="still in the input") as err:
        await composer.send_prompt(page, "x", before_enter=hook(log))
    assert err.value.code == errors.UI_SEND_FAILED
    assert log == ["before_enter", "Enter"], "exactly one Enter and nothing else: %r" % log


@sync
async def test_a_refused_enter_hook_means_no_enter():
    log = []
    with pytest.raises(RuntimeError, match="stop"):
        await composer.send_prompt(SendPage(log), "x", before_enter=hook(log, error=RuntimeError("stop")))
    assert "Enter" not in log


@sync
async def test_a_prompt_that_cannot_be_typed_never_reaches_the_hook_or_enter():
    log = []
    with pytest.raises(ComposerError, match="could not type") as err:
        await composer.send_prompt(SendPage(log, typeable=False), "x", before_enter=hook(log))
    assert err.value.code == errors.UI_SEND_FAILED and log == [], "the order was marked sent for a prompt never typed"
