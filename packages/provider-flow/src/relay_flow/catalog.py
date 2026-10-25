"""flow's model catalog: the models clients send, what each accepts, what a video costs in Flow credits, and the price
lookup. Pure.

Built from the captures of 23/10 (fixtures/flow/live-261023, FINDINGS 4-5), GetModels of 25/10 (`HTrJv`, read on
the AI Pro account: fixtures/flow/live-261025b) and the live jobs of 25/10 (FINDINGS "Catalog 25/10" and "Inputs and
the full catalog 25/10"). Every model key below is GetModels' own; each mode's request shape was read from flow's web
app and proven by one live job.

An input image is uploaded first (`maseQ`, free, no reCAPTCHA token needed) and named by the media id flow gives it.
An image takes up to 10 as references. A video takes them by `options.mode`: "frames" (the default) is one start frame
or a first and a last frame; "references" is up to 3 (Veo) or 7 (Omni) reference images.
"""

from relay_core import frames
from relay_core.providers import ModelSpec

# relay name -> (Flow's model key, display name). Flow draws the image inside the generate request (HOLD).
MODELS = {
    "flow-nano-banana-2.1": ("BELUGA", "Nano Banana 2.1"),
    "flow-nano-banana-pro": ("GEM_PIX_2", "Nano Banana Pro"),
    "flow-nano-banana-2-lite": ("HARBOR_SEAL", "Nano Banana 2 Lite"),
}
# The ratio is a request field: Flow's aspect codes. 1-5 measured 23/10 (3 gave 1376x768); 6-14 are Nano Banana 2.1's
# only (GetModels lists 1-14 for BELUGA, 1-5 for the other two), each measured 25/10 by the size it came back in
# (6: 848x1264, 10: 1584x672, 13: 352x2928, fixtures/flow/live-261025b/image_ratios.json).
ASPECTS = {"1:1": 1, "9:16": 2, "16:9": 3, "3:4": 4, "4:3": 5, "2:3": 6, "3:2": 7, "4:5": 8, "5:4": 9, "21:9": 10,
           "1:4": 11, "4:1": 12, "1:8": 13, "8:1": 14}
IMAGE_RATIOS = {"flow-nano-banana-2.1": tuple(ASPECTS), "flow-nano-banana-pro": tuple(ASPECTS)[:5],
                "flow-nano-banana-2-lite": tuple(ASPECTS)[:5]}
DEFAULT_RATIO = "16:9"
MAX_IMAGE_REFS = 10              # GetModels [9] of each image model

# ---- video ----------------------------------------------------------------------------------------------------------
# A video is answered at once and polled (DETACH). The aspect code is the field after the model key: 2 landscape, 1
# portrait. Modes: text, start (one start frame), frames (a first and a last frame), references.
VIDEO_MODELS = {
    "flow-veo-3.1-lite": "Veo 3.1 Lite",
    "flow-veo-3.1-fast": "Veo 3.1 Fast",
    "flow-veo-3.1-quality": "Veo 3.1 Quality",
    "flow-omni-flash-720p": "Omni 1.1 Flash 720p",
    "flow-omni-flash-360p": "Omni 1.1 Flash 360p",
}
OPERATIONS = {"text": "text_to_video", "start": "image_to_video", "frames": "frames_to_video",
              "references": "references_to_video"}
MAX_VIDEO_REFS = {"flow-veo-3.1-lite": 3, "flow-veo-3.1-fast": 3, "flow-omni-flash-720p": 7,
                  "flow-omni-flash-360p": 7}           # GetModels [9] of the r2v keys; Quality has no reference mode
OMNI_360P = 4                    # GetModels [23] of the `_360p` keys, sent as the resolution (720p sends none)


def _veo_keys() -> dict:
    """(model, mode, seconds) -> (16:9 key, 9:16 key), GetModels 25/10. The 4 s and 6 s keys are Ultra's only."""
    keys = {}
    for d in (4, 6):
        for model, word in (("flow-veo-3.1-lite", "lite"), ("flow-veo-3.1-fast", "fast"),
                            ("flow-veo-3.1-quality", "quality")):
            keys[model, "text", d] = ("veo_3_1_t2v_%s_%ds" % (word, d),) * 2
            keys[model, "start", d] = ("veo_3_1_i2v_s_%s_%ds" % (word, d),) * 2
            keys[model, "frames", d] = ("veo_3_1_i2v_s_%s_%ds_fl" % (word, d),) * 2
    lite, fast, quality = "flow-veo-3.1-lite", "flow-veo-3.1-fast", "flow-veo-3.1-quality"
    keys.update({
        (lite, "text", 8): ("veo_3_1_t2v_lite",) * 2,
        (lite, "start", 8): ("veo_3_1_i2v_lite",) * 2,
        (lite, "frames", 8): ("veo_3_1_interpolation_lite",) * 2,
        (lite, "references", 8): ("veo_3_1_r2v_lite",) * 2,
        (fast, "text", 8): ("veo_3_1_t2v_fast", "veo_3_1_t2v_fast_portrait"),
        (fast, "start", 8): ("veo_3_1_i2v_s_fast", "veo_3_1_i2v_s_fast_portrait"),
        (fast, "frames", 8): ("veo_3_1_i2v_s_fast_fl", "veo_3_1_i2v_s_fast_portrait_fl"),
        (fast, "references", 8): ("veo_3_1_r2v_fast_landscape", "veo_3_1_r2v_fast_portrait"),
        (quality, "text", 8): ("veo_3_1_t2v", "veo_3_1_t2v_portrait"),
        (quality, "start", 8): ("veo_3_1_i2v_s", "veo_3_1_i2v_s_portrait"),
        (quality, "frames", 8): ("veo_3_1_i2v_s_fl", "veo_3_1_i2v_s_portrait_fl"),
    })
    for model, sfx in (("flow-omni-flash-720p", ""), ("flow-omni-flash-360p", "_360p")):
        for d in (4, 6, 8, 10):
            keys[model, "text", d] = ("abra_t2v_%ds%s" % (d, sfx),) * 2
            keys[model, "start", d] = ("abra_i2v_%ds%s" % (d, sfx),) * 2
            keys[model, "frames", d] = ("omni_flash_i2v_%ds_first_last%s" % (d, sfx),) * 2
            keys[model, "references", d] = ("abra_r2v_%ds%s" % (d, sfx),) * 2
    return keys


VIDEO_KEYS = _veo_keys()
VIDEO_ASPECTS = {"16:9": 2, "9:16": 1}
DEFAULT_DURATION = 8
# What one video costs in Flow credits by model and length (GetModels `HTrJv` [4], 25/10): Veo's 8 s on free and Pro
# (Lite 10, Fast 20, Quality 100); its 4 s and 6 s at Ultra's price, the only plan that has them; Omni alike on all.
VIDEO_CREDITS = {
    "flow-veo-3.1-lite": {4: 5, 6: 5, 8: 10},
    "flow-veo-3.1-fast": {4: 10, 6: 10, 8: 20},
    "flow-veo-3.1-quality": {4: 100, 6: 100, 8: 100},
    "flow-omni-flash-720p": {4: 7, 6: 10, 8: 12, 10: 15},
    "flow-omni-flash-360p": {4: 4, 6: 5, 8: 6, 10: 7},
}
# The plans (the account's tier, from `nzlxg`) an option runs on, when not every plan: Veo's 4 s and 6 s are Ultra's
# only (GetModels has no free or Pro price for them); Quality takes 100 credits, more than a free day holds.
TIERS = {**{(m, d): ("ultra",) for m in ("flow-veo-3.1-lite", "flow-veo-3.1-fast", "flow-veo-3.1-quality")
            for d in (4, 6)},
         ("flow-veo-3.1-quality", 8): ("pro", "ultra")}


def durations(model: str) -> list[int]:
    return sorted(VIDEO_CREDITS[model])


def price_key(model: str, duration: int | None = None) -> str:
    """The option a job is (`flow.prices`, `flow.costs`): an image by its model, a video with its length."""
    return "%s@%ds" % (model, duration or DEFAULT_DURATION) if model in VIDEO_MODELS else model


PRICE_KEYS = (*MODELS, *(price_key(m, d) for m in VIDEO_MODELS for d in durations(m)))
# What one job costs the account, in the unit of its pool (the default of `flow.costs`): an image takes no Flow credit
# (50 -> 50 measured) and counts one job; a video its Flow credits.
DEFAULT_COSTS = {**dict.fromkeys(MODELS, 1),
                 **{price_key(m, d): c for m, by in VIDEO_CREDITS.items() for d, c in by.items()}}
# The sale price in points, the default of `flow.prices` (the owner edits it): an image costs nothing upstream and
# sells at 1 point (owner 24/10); Lite 8 s 2 and Fast 8 s 4 (owner, 25/10). The rest is PROPOSED at the same rate, one
# point per 5 Flow credits rounded up (to be confirmed by the owner before deploy).
DEFAULT_PRICES = {**dict.fromkeys(MODELS, 1),
                  **{price_key(m, d): -(-c // 5) for m, by in VIDEO_CREDITS.items() for d, c in by.items()}}


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def _video_text(model: str) -> str:
    modes = {mode for (m, mode, _d) in VIDEO_KEYS if m == model}
    parts = ["text", "one start frame", "a first and a last frame"]
    if "references" in modes:
        parts.append("up to %d reference images" % MAX_VIDEO_REFS[model])
    lengths = durations(model)
    ultra = [d for d in lengths if TIERS.get((model, d)) == ("ultra",)]
    res = "360p" if model.endswith("360p") else "720p"
    only = " (%s s on an AI Ultra account only)" % "/".join(map(str, ultra)) if ultra else ""
    return "A %s clip with sound (Google Flow, %s) from %s; %s s%s." % (
        res, VIDEO_MODELS[model], ", ".join(parts[:-1]) + " or " + parts[-1], "/".join(map(str, lengths)), only)


def catalog() -> list[ModelSpec]:
    images = [ModelSpec(name, "image", ("text_to_image", "image_to_image"),
                        limits={"max_images": MAX_IMAGE_REFS, "ratios": list(IMAGE_RATIOS[name]),
                                "default_ratio": DEFAULT_RATIO,
                                "description": "One image from a text prompt and up to %d reference images (Google "
                                               "Flow, %s), drawn in about 15 s." % (MAX_IMAGE_REFS, display)},
                        price_keys={"default": price_key(name)}, display_name=display)
              for name, (_key, display) in MODELS.items()]
    videos = []
    for name, display in VIDEO_MODELS.items():
        modes = {mode for (m, mode, _d) in VIDEO_KEYS if m == name}
        videos.append(ModelSpec(
            name, "video", tuple(op for mode, op in OPERATIONS.items() if mode in modes),
            limits={"max_images": MAX_VIDEO_REFS.get(name, 2), "ratios": list(VIDEO_ASPECTS),
                    "default_ratio": DEFAULT_RATIO, "durations": durations(name), "default_duration": DEFAULT_DURATION,
                    "modes": ["frames"] + (["references"] if "references" in modes else []),
                    "description": _video_text(name)},
            price_keys={"%ds" % d: price_key(name, d) for d in durations(name)}, display_name=display))
    return images + videos
