"""dola's composer UI: enter video or image mode, pick model, duration, ratio and style, send.

What this costs to get wrong, learned the hard way (dola_v4 composer.py):

- **Timing.** The chip row shows up only after React hydrates; every wait here is on a condition, never a fixed sleep.
- **Real mouse clicks.** dola's dropdowns ignore `element.click()`; the click is a mouse event at the element's centre.
- **No silent success.** Every step reads its label back from the page. A wrong read raises at once, never sends a
  video with the wrong parameters.
- **Two composers (A/B per account since 23/09).** The old one has a duration chip and a Ratio chip; the new one has
  ONE chip `<ratio> · <N>s` that opens a panel with a ratio tile and a duration slider. `select_video_ratio_duration`
  tells them apart by the "·" on the chip and returns which one it drove; the answer feeds `order_taken`.

The regular expressions handed to JS contain no backslash (`[0-9]` instead of `\\d`): they cross several layers of
escaping and a backslash does not survive them.
"""

import re

from relay_browser.ui import UiError, type_and_send
from relay_dola.catalog import (
    DEFAULT_RATIO,
    IMAGE_MODEL_LABELS,
    IMAGE_STYLE_LABELS,
    MODEL_LABELS,
    MODEL_VERSION,
    VALID_IMAGE_RATIOS,
    VALID_RATIOS,
)
from relay_dola.errors import (
    UI_DURATION_PATCH_FAILED,
    UI_MODE_NOT_READY,
    UI_MODEL_SELECT_FAILED,
    UI_RATIO_SELECT_FAILED,
    UI_SEND_FAILED,
    UI_STYLE_SELECT_FAILED,
)

# One error code per composer step (relay_dola.errors): "chip not found" and "could not attach" differ in the data, not
# only in the message.


class ComposerError(RuntimeError):
    """A UI step failed. Raised to stop early instead of sending with the wrong parameters; `code` names the step."""

    def __init__(self, message: str, code: str = UI_SEND_FAILED):
        super().__init__(message)
        self.code = code


# An element whose text matches, in the lower part of the screen (the composer). Returns its centre for a real click.
_RECT_JS = """(pattern)=>{
  const re = new RegExp(pattern);
  const els = [...document.querySelectorAll('button,[role=button],div,span')].filter(e=>{
    const t = (e.textContent||'').trim();
    const r = e.getBoundingClientRect();
    return re.test(t) && t.length <= 30 && r.width > 0 && r.height > 0 && r.top > innerHeight*0.6;
  });
  els.sort((a,b)=>(a.textContent||'').length - (b.textContent||'').length);
  if (!els[0]) return null;
  const r = els[0].getBoundingClientRect();
  return {x: r.x + r.width/2, y: r.y + r.height/2, text: (els[0].textContent||'').trim()};
}"""

# An item of the open dropdown: it renders in a floating layer, so no position limit.
_OPTION_JS = """(pattern)=>{
  const re = new RegExp(pattern);
  const els = [...document.querySelectorAll('div,span,li,button,[role=menuitem],[role=option]')].filter(e=>{
    const t = (e.textContent||'').trim();
    const r = e.getBoundingClientRect();
    return re.test(t) && t.length <= 40 && r.width > 0 && r.height > 0;
  });
  els.sort((a,b)=>(a.textContent||'').length - (b.textContent||'').length);
  if (!els[0]) return null;
  const r = els[0].getBoundingClientRect();
  return {x: r.x + r.width/2, y: r.y + r.height/2, text: (els[0].textContent||'').trim()};
}"""

_OPTIONS_JS = """(pattern)=>{
  const re = new RegExp(pattern);
  const seen = new Set();
  for (const e of document.querySelectorAll('div,span,li,button,[role=menuitem],[role=option]')) {
    const t = (e.textContent||'').trim();
    const r = e.getBoundingClientRect();
    if (re.test(t) && t.length <= 40 && r.width > 0 && r.height > 0) seen.add(t);
  }
  return [...seen];
}"""

_CLICK_TEXT_JS = """(label)=>{
  const el = [...document.querySelectorAll('*')].find(
    e => e.childElementCount === 0 && (e.textContent||'').trim() === label);
  if (el) { el.click(); return true; }
  return false;
}"""

# Find only (no click): poll for the button without pressing it too early.
_FIND_TEXT_JS = """(label)=>[...document.querySelectorAll('*')].some(
  e => e.childElementCount === 0 && (e.textContent||'').trim() === label)"""

MODEL_CHIP = "^Model "
# The duration chip ENDS with the seconds, but dola may put a prefix before them (measured 23/09: `Auto · 10s`, no
# longer `10s`). The old pattern anchored at the start missed it: `duration chip not found`, and three i2v jobs died.
DURATION_CHIP = "(?:^|·| )[0-9]+ *s$"
# The old composer's Ratio chip shows the LABEL "Ratio", not the value (measured 260911).
RATIO_CHIP = "^Ratio$"
# The new composer: ratio and duration in ONE chip "<ratio> · <N>s" (`Auto · 10s`, `16:9 · 30s`).
COMBO_CHIP = "· *[0-9]+ *s$"

# The labels and version numbers the chips show come from the catalog (the one table of dola's names).
RATIOS = VALID_RATIOS


async def _wait_rect(page, pattern: str, timeout_s: int = 25, js: str = _RECT_JS) -> dict | None:
    """Wait until an element matching the pattern shows up. None on timeout."""
    for _ in range(timeout_s):
        rect = await page.evaluate(js, pattern)
        if rect:
            return rect
        await page.wait_for_timeout(1000)
    return None


async def _click(page, rect: dict) -> None:
    """A REAL mouse click at the coordinates: `element.click()` does not open dola's dropdowns."""
    await page.mouse.click(rect["x"], rect["y"])
    await page.wait_for_timeout(2000)


async def enter_video_mode(page, settle_s: int = 9) -> str:
    """Enter *Create Videos* and wait for the chip row. Returns the Model chip label read (proof of the mode).

    Adaptive: poll for the "Create Videos" button and press it at once (ceiling `settle_s`) instead of sleeping.
    Pressing before React hydrates shows no chip row, so a second press is the self-heal."""
    ready = False
    for _ in range(settle_s * 2):          # every 0.5 s, up to settle_s seconds
        if await page.evaluate(_FIND_TEXT_JS, "Create Videos"):
            ready = True
            break
        await page.wait_for_timeout(500)
    if not ready:
        raise ComposerError("could not find the 'Create Videos' button", code=UI_MODE_NOT_READY)

    await page.evaluate(_CLICK_TEXT_JS, "Create Videos")
    rect = await _wait_rect(page, MODEL_CHIP, timeout_s=6)
    if not rect:
        # The first press did not take (late hydration or an early click): press again and wait longer.
        await page.evaluate(_CLICK_TEXT_JS, "Create Videos")
        rect = await _wait_rect(page, MODEL_CHIP, timeout_s=12)
    if not rect:
        raise ComposerError("chip row did not appear after clicking Create Videos", code=UI_MODE_NOT_READY)
    return rect["text"]


async def select_video_model(page, model: str = "seedance_v2.5") -> str:
    """Pick the model. Returns the chip label after the pick, the proof it took.

    2.5 is required for a 30 s video: 2.0 is always cut to 15 s."""
    label = MODEL_LABELS.get(model)
    version = MODEL_VERSION.get(model)
    if not label or not version:
        raise ComposerError("unsupported model: %s" % model, code=UI_MODEL_SELECT_FAILED)

    chip = await _wait_rect(page, MODEL_CHIP)
    if not chip:
        raise ComposerError("Model chip not found", code=UI_MODEL_SELECT_FAILED)
    if version in chip["text"]:
        return chip["text"]          # already right (the choice survives a reload)

    await _click(page, chip)
    option = await page.evaluate(_OPTION_JS, label)
    if not option:
        raise ComposerError("Model dropdown has no item '%s'" % label, code=UI_MODEL_SELECT_FAILED)
    await _click(page, option)
    await page.wait_for_timeout(1500)

    after = await _wait_rect(page, MODEL_CHIP, timeout_s=10)
    if not after or version not in after["text"]:
        raise ComposerError("model selection did not take: chip is still %r" % (after["text"] if after else None),
                            code=UI_MODEL_SELECT_FAILED)
    return after["text"]


_TRAILING_SECONDS = re.compile(r"(\d+)\s*s$")


def _seconds_on_chip(label: str) -> int | None:
    """The seconds read from a chip label, whatever prefix dola adds (`Auto · 10s` -> 10)."""
    m = _TRAILING_SECONDS.search((label or "").strip())
    return int(m.group(1)) if m else None


def _is_auto(label: str) -> bool:
    """The chip is in LEAVE-IT-TO-DOLA mode. `Auto · 10s` says dola is ABOUT to make 10 s, not that we set 10 s:
    reading it as "set" skips the pick, lets dola choose the length, and we still bill the length the client asked."""
    return "auto" in (label or "").lower()


async def select_video_duration(page, seconds: int = 30) -> str:
    """Old composer: pick the duration. Returns the chip label after the pick.

    A 30 s option exists only when the action-bar config was patched. A missing option means the PATCH failed: raise at
    once, never send on and get a 10 s video."""
    chip = await _wait_rect(page, DURATION_CHIP)
    if not chip:
        raise ComposerError("duration chip not found", code=UI_DURATION_PATCH_FAILED)

    want = "%ds" % seconds
    # Skip the pick only when the chip is SET to exactly these seconds; in auto mode pick by hand even if the number
    # happens to match.
    if not _is_auto(chip["text"]) and _seconds_on_chip(chip["text"]) == seconds:
        return chip["text"]

    await _click(page, chip)

    # List the whole dropdown, not only the item we want: it is the only proof the patch reached the UI, and when it
    # failed it says which levels dola offers.
    offered = sorted(await page.evaluate(_OPTIONS_JS, DURATION_CHIP),
                     key=lambda t: int("".join(ch for ch in t if ch.isdigit()) or 0))

    option = await page.evaluate(_OPTION_JS, "(?:^|·| )%d *s$" % seconds)
    if not option:
        raise ComposerError("duration dropdown has no item %s (available: %s) - config not patched?"
                            % (want, "/".join(offered) or "none"), code=UI_DURATION_PATCH_FAILED)
    await _click(page, option)
    await page.wait_for_timeout(1500)

    after = await _wait_rect(page, DURATION_CHIP, timeout_s=10)
    if not after or _is_auto(after["text"]) or _seconds_on_chip(after["text"]) != seconds:
        raise ComposerError("duration selection did not take: chip is still %r" % (after["text"] if after else None),
                            code=UI_DURATION_PATCH_FAILED)
    return "%s (dropdown offered: %s)" % (after["text"], "/".join(offered))


# The slider's range, read from the "<N>s" labels in the panel (current, floor, ceiling).
_SLIDER_RANGE_JS = """()=>{
  const s = document.querySelector('[role=slider]');
  if (!s) return null;
  let box = s;
  for (let i = 0; i < 8 && box && !/Duration/.test(box.innerText || ''); i++) box = box.parentElement;
  if (!box) return null;
  const secs = [...box.querySelectorAll('*')].filter(e => e.childElementCount === 0)
    .map(e => (e.textContent || '').trim()).filter(t => /^[0-9]+s$/.test(t)).map(t => parseInt(t));
  return secs.length ? {min: Math.min(...secs), max: Math.max(...secs)} : null;
}"""


def _chip_says(text: str) -> str:
    """A chip label with its whitespace collapsed: `16:9 ·  30s` -> `16:9 · 30s`."""
    return " ".join((text or "").split())


async def select_video_ratio_duration(page, ratio: str, seconds: int) -> tuple[str, str, bool]:
    """Pick ratio and duration on BOTH composers. Returns (duration label, ratio label, new composer?).

    The old composer has its own duration and Ratio chips; the new one merges them into `<ratio> · <N>s`, told apart by
    the "·" on the chip. The flag is what the acceptance verdict needs (`order_taken`)."""
    chip = await _wait_rect(page, DURATION_CHIP)
    if not chip:
        raise ComposerError("duration chip not found", code=UI_DURATION_PATCH_FAILED)
    if "·" not in chip["text"]:
        return (await select_video_duration(page, seconds), await select_video_ratio(page, ratio), False)
    return (*await _set_ratio_duration_popover(page, ratio, seconds), True)


async def read_composer_kind(page) -> str | None:
    """`new` when the duration chip is the merged `<ratio> · <N>s` one, `old` when it is the plain duration chip,
    None when no chip shows (video mode not entered)."""
    chip = await _wait_rect(page, DURATION_CHIP, timeout_s=10)
    if not chip:
        return None
    return "new" if "·" in chip["text"] else "old"


async def _set_ratio_duration_popover(page, ratio: str, seconds: int) -> tuple[str, str]:
    """New composer: open the panel from the merged chip, click the ratio tile, set the slider, read the chip back.

    The slider is set by KEYBOARD (Home, then ArrowRight the exact number of steps), not by dragging: a level's x
    coordinate depends on the panel width, the step count does not. The check is the chip after the panel closes: the
    chip is what dola reads at send time (measured 24/09: chip `16:9 · 30s` -> the request carries "duration":30)."""
    if ratio not in RATIOS:
        raise ComposerError("unsupported ratio: %s (dola offers %s)" % (ratio, "/".join(RATIOS)),
                            code=UI_RATIO_SELECT_FAILED)
    want = "%s · %ds" % (ratio, seconds)
    chip = await _wait_rect(page, COMBO_CHIP)
    if not chip:
        raise ComposerError("ratio/duration chip not found", code=UI_DURATION_PATCH_FAILED)
    if _chip_says(chip["text"]) == want:
        return chip["text"], ratio

    await _click(page, chip)
    tile = await page.evaluate(_OPTION_JS, "^%s$" % ratio)
    if not tile:
        raise ComposerError("ratio %s not in the ratio/duration panel (available: %s)"
                            % (ratio, "/".join(sorted(await page.evaluate(_OPTIONS_JS, "^[0-9]+:[0-9]+$")))),
                            code=UI_RATIO_SELECT_FAILED)
    await _click(page, tile)
    span = await page.evaluate(_SLIDER_RANGE_JS)
    if not span:
        raise ComposerError("duration slider not found in the ratio/duration panel", code=UI_DURATION_PATCH_FAILED)
    if not span["min"] <= seconds <= span["max"]:
        raise ComposerError("duration %ds is outside the slider (%d-%ds) - config not patched?"
                            % (seconds, span["min"], span["max"]), code=UI_DURATION_PATCH_FAILED)
    await page.locator("[role=slider]").first.focus()
    await page.keyboard.press("Home")
    for _ in range(seconds - span["min"]):
        await page.keyboard.press("ArrowRight")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1000)

    after = await _wait_rect(page, COMBO_CHIP, timeout_s=10)
    if not after or _chip_says(after["text"]) != want:
        raise ComposerError("ratio/duration did not take: chip is %r, wanted %r"
                            % (after["text"] if after else None, want), code=UI_DURATION_PATCH_FAILED)
    return after["text"], "%s (slider %d-%ds)" % (ratio, span["min"], span["max"])


async def select_video_ratio(page, ratio: str = DEFAULT_RATIO) -> str:
    """Old composer: pick the frame ratio. Returns the label observed.

    Unlike model and duration, the Ratio chip always shows the word "Ratio", never the value, and the dropdown has no
    readable "selected" mark (measured 260911). So the proof is that the dropdown CLOSES after the click (an option
    still there means the click missed). The final proof is the video's orientation under ffprobe in the poll."""
    if ratio not in RATIOS:
        raise ComposerError("unsupported ratio: %s (dola offers %s)" % (ratio, "/".join(RATIOS)),
                            code=UI_RATIO_SELECT_FAILED)

    chip = await _wait_rect(page, RATIO_CHIP)
    if not chip:
        raise ComposerError("Ratio chip not found", code=UI_RATIO_SELECT_FAILED)

    await _click(page, chip)
    offered = await page.evaluate(_OPTIONS_JS, "^[0-9]+:[0-9]+$")
    if not offered:
        raise ComposerError("opened Ratio dropdown but found no options", code=UI_RATIO_SELECT_FAILED)
    option = await page.evaluate(_OPTION_JS, "^%s$" % ratio)
    if not option:
        raise ComposerError("Ratio dropdown has no item %s (available: %s)" % (ratio, "/".join(sorted(offered))),
                            code=UI_RATIO_SELECT_FAILED)
    await _click(page, option)
    await page.wait_for_timeout(1200)

    still = await page.evaluate(_OPTIONS_JS, "^[0-9]+:[0-9]+$")
    if len(still) >= len(offered):
        raise ComposerError("ratio selection %s did not take: dropdown still open" % ratio,
                            code=UI_RATIO_SELECT_FAILED)
    return "%s (dropdown offered: %s)" % (ratio, "/".join(sorted(offered)))


async def send_prompt(page, prompt: str, *, before_enter=None) -> None:
    """Type the prompt, run `before_enter()`, press Enter ONCE (the kit's `type_and_send`: one typing and sending step
    for every browser provider). Never the Send button: an input that stays full is "unproven", and the caller maps
    it (sent before: Ambiguous, reconcile decides; not yet: retry). A UI error becomes a `ComposerError` with the send
    step's code."""
    try:
        await type_and_send(page, prompt, before_enter=before_enter)
    except UiError as exc:
        raise ComposerError(str(exc), code=UI_SEND_FAILED) from exc


# ---- image generation ------------------------------------------------------------------------------------------------
# The same composer in "Create Images" mode, with three chips: Model, Ratio, Style (seen 260917). Picks are CLICKS on
# the chips: dola builds and signs the request itself (the request is never edited; see site.py).
STYLE_CHIP = "^Style$"

IMAGE_RATIOS = VALID_IMAGE_RATIOS
# The old composer's image ratio chip shows the label "Ratio"; the new one (A/B per account) shows the value
# ("Aspect Ratio Auto", "Aspect Ratio 16:9") and opens a tile panel instead of a dropdown.
IMAGE_RATIO_CHIP = "^(Ratio|Aspect Ratio.*)$"


async def enter_image_mode(page, settle_s: int = 9) -> str:
    """Enter *Create Images* and wait for the chip row (the press is retried as in `enter_video_mode`).

    One press before React hydrates shows no Model chip: it needs another press and a longer wait (measured 260917)."""
    ready = False
    for _ in range(settle_s * 2):
        if await page.evaluate(_FIND_TEXT_JS, "Create Images"):
            ready = True
            break
        await page.wait_for_timeout(500)
    if not ready:
        raise ComposerError("could not find the 'Create Images' button", code=UI_MODE_NOT_READY)

    rect = None
    for attempt in range(3):                # late hydration or an early click: press again, wait longer
        await page.evaluate(_CLICK_TEXT_JS, "Create Images")
        rect = await _wait_rect(page, MODEL_CHIP, timeout_s=6 if attempt == 0 else 12)
        if rect:
            break
    if not rect:
        raise ComposerError("chip row did not appear after clicking Create Images", code=UI_MODE_NOT_READY)
    return rect["text"]


async def dismiss_consent(page) -> None:
    """Close the cookies-consent popup if there is one ('OK'). It BLOCKS the first chip click (measured 260918: a
    non-default model pick missed because the Model dropdown would not open under the popup)."""
    try:
        await page.evaluate(_CLICK_TEXT_JS, "OK")
    except Exception:      # noqa: BLE001 - no popup is the normal case
        pass


async def select_image_model(page, model: str = "seedream-4.5") -> str:
    """Pick the image model (Seedream 4.5 / 5.0 Pro); the chip label after the pick is the proof."""
    label = IMAGE_MODEL_LABELS.get(model)
    if not label:
        raise ComposerError("unsupported image model: %s" % model, code=UI_MODEL_SELECT_FAILED)
    chip = await _wait_rect(page, MODEL_CHIP)
    if not chip:
        raise ComposerError("Model chip not found", code=UI_MODEL_SELECT_FAILED)
    if label in chip["text"]:
        return chip["text"]                         # already right (the default is 4.5)
    # Open the dropdown and find the option; the first click may be eaten by the consent popup: dismiss, retry.
    option = None
    for _ in range(3):
        await _click(page, chip)
        option = await page.evaluate(_OPTION_JS, "^%s$" % re.escape(label))
        if option:
            break
        await dismiss_consent(page)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        chip = await _wait_rect(page, MODEL_CHIP) or chip
    if not option:
        raise ComposerError("Model dropdown has no item '%s'" % label, code=UI_MODEL_SELECT_FAILED)
    await _click(page, option)
    await page.wait_for_timeout(1500)
    after = await _wait_rect(page, MODEL_CHIP, timeout_s=10)
    if not after or label not in after["text"]:
        raise ComposerError("image model selection did not take: chip is %r" % (after["text"] if after else None),
                            code=UI_MODEL_SELECT_FAILED)
    return after["text"]


async def select_image_ratio(page, ratio: str) -> str:
    """Pick the image ratio. The option reads 'X:Y (description)', so it matches by PREFIX. The proof: the dropdown
    closes (old composer), or the chip carries the value (new composer)."""
    if ratio not in IMAGE_RATIOS:
        raise ComposerError("unsupported image ratio: %s (dola offers %s)" % (ratio, "/".join(IMAGE_RATIOS)),
                            code=UI_RATIO_SELECT_FAILED)
    chip = await _wait_rect(page, IMAGE_RATIO_CHIP)
    if not chip:
        raise ComposerError("Ratio chip not found", code=UI_RATIO_SELECT_FAILED)
    new_ui = chip["text"].startswith("Aspect Ratio")
    if new_ui and _chip_says(chip["text"]).endswith(" " + ratio):
        return chip["text"]
    await _click(page, chip)
    offered = await page.evaluate(_OPTIONS_JS, "^[0-9]+:[0-9]+")
    option = await page.evaluate(_OPTION_JS, "^%s([^0-9]|$)" % re.escape(ratio))
    if not option:
        raise ComposerError("Ratio dropdown has no item %s (available: %s)" % (ratio, "/".join(sorted(offered))),
                            code=UI_RATIO_SELECT_FAILED)
    await _click(page, option)
    await page.wait_for_timeout(1200)
    if new_ui:
        # New composer: the tile panel may stay open after the click; the chip carries the value, so check the chip.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        after = await _wait_rect(page, IMAGE_RATIO_CHIP, timeout_s=10)
        if not after or not _chip_says(after["text"]).endswith(" " + ratio):
            raise ComposerError("image ratio %s did not take: chip is %r" % (ratio, after["text"] if after else None),
                                code=UI_RATIO_SELECT_FAILED)
        return "%s (panel offered: %s)" % (after["text"], "/".join(sorted(offered)))
    still = await page.evaluate(_OPTIONS_JS, "^[0-9]+:[0-9]+")
    if len(still) >= len(offered):
        raise ComposerError("image ratio %s did not take: dropdown still open" % ratio, code=UI_RATIO_SELECT_FAILED)
    return "%s (offered: %s)" % (ratio, "/".join(sorted(offered)))


async def select_image_style(page, style: str) -> str:
    """Pick the image style (optional). The proof: the option is found and clicked; the dropdown closes."""
    label = IMAGE_STYLE_LABELS.get(style)
    if not label:
        raise ComposerError("unsupported style: %s (dola offers %s)" % (style, "/".join(IMAGE_STYLE_LABELS)),
                            code=UI_STYLE_SELECT_FAILED)
    chip = await _wait_rect(page, STYLE_CHIP)
    if not chip:
        raise ComposerError("Style chip not found", code=UI_STYLE_SELECT_FAILED)
    await _click(page, chip)
    option = await page.evaluate(_OPTION_JS, "^%s$" % re.escape(label))
    if not option:
        raise ComposerError("Style dropdown has no item '%s'" % label, code=UI_STYLE_SELECT_FAILED)
    await _click(page, option)
    await page.wait_for_timeout(1200)
    return label
