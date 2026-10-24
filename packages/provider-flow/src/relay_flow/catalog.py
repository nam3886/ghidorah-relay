"""flow's model catalog: the models clients send, what each accepts, what a video costs in Flow credits, and the price
lookup. Pure.

Built from the captures of 23/10 (fixtures/flow/live-261023, FINDINGS 4-5): the three image models (text to image,
free) and Veo 3.1 Lite text to video (`veo_3_1_t2v_lite`, 8 s, 16:9, 10 credits on the account's tier: 50 -> 40
measured). Not offered until a live capture proves them (FINDINGS "i2i/t2v/i2v build 24/10"): an input image (the
upload `maseQ` and the start-image video RPC were never captured), other video ratios or durations, Veo Fast/Quality.
"""

from relay_core import frames
from relay_core.providers import ModelSpec

# relay name -> (Flow's model key, display name). Flow draws the image inside the generate request (HOLD).
MODELS = {
    "flow-nano-banana-2.1": ("BELUGA", "Nano Banana 2.1"),
    "flow-nano-banana-pro": ("GEM_PIX_2", "Nano Banana Pro"),
    "flow-nano-banana-2-lite": ("HARBOR_SEAL", "Nano Banana 2 Lite"),
}
# The ratio is a request field. Flow's aspect codes (measured 23/10: 3 gave 1376x768).
ASPECTS = {"1:1": 1, "9:16": 2, "16:9": 3, "3:4": 4, "4:3": 5}
DEFAULT_RATIO = "16:9"

# relay name -> (Flow's model key, display name). A video is answered at once and polled (DETACH).
VIDEO_MODELS = {
    "flow-veo-3.1-lite": ("veo_3_1_t2v_lite", "Veo 3.1 Lite"),
}
# The only video request recorded: the field after the model key was 2 and gave 1280x720 (16:9). What another value
# gives is not measured, so 16:9 is the only ratio offered.
VIDEO_ASPECTS = {"16:9": 2}
VIDEO_DURATION = 8               # the 4 s and 6 s Lite keys are offered only on Flow's tier 3


def price_key(model: str) -> str:
    """The option a job is (`flow.prices`, `flow.costs`): an image by its model, a video with its one length."""
    return "%s@%ds" % (model, VIDEO_DURATION) if model in VIDEO_MODELS else model


PRICE_KEYS = tuple(price_key(m) for m in (*MODELS, *VIDEO_MODELS))
# What one job costs the account, in the unit of its pool (the default of `flow.costs`): an image takes no Flow credit
# (50 -> 50 measured) and counts one job; a Veo 3.1 Lite 8 s video takes 10 Flow credits on tiers 1 and 2 (GetModels
# `HTrJv`; spent 50 -> 40, 23/10). Not offered until captured, the official table (support.google.com/flow 16526234)
# reads: Veo 3.1 Fast 20, Quality 100, Gemini Omni Flash 720p 4/6/8/10 s 7/10/12/15, 360p 4/5/6/7.
DEFAULT_COSTS = {**dict.fromkeys(MODELS, 1), price_key("flow-veo-3.1-lite"): 10}
# The sale price in points, the default of `flow.prices` (the owner edits it): an image costs nothing upstream and
# sells at 1 point (owner 24/10).
DEFAULT_PRICES = {**dict.fromkeys(MODELS, 1), **{price_key(m): 2 for m in VIDEO_MODELS}}


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    images = [ModelSpec(name, "image", ("text_to_image",),
                        limits={"max_images": 0, "ratios": list(ASPECTS), "default_ratio": DEFAULT_RATIO,
                                "description": "One image from a text prompt (Google Flow, %s), drawn in about 15 s."
                                               % display},
                        price_keys={"default": price_key(name)}, display_name=display)
              for name, (_key, display) in MODELS.items()]
    videos = [ModelSpec(name, "video", ("text_to_video",),
                        limits={"max_images": 0, "ratios": list(VIDEO_ASPECTS), "default_ratio": DEFAULT_RATIO,
                                "durations": [VIDEO_DURATION], "default_duration": VIDEO_DURATION,
                                "description": "An 8 s 720p clip with sound from a text prompt (Google Flow, %s), "
                                               "about 45 s to render." % display},
                        price_keys={"%ds" % VIDEO_DURATION: price_key(name)}, display_name=display)
              for name, (_key, display) in VIDEO_MODELS.items()]
    return images + videos
