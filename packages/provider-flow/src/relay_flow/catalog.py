"""flow's model catalog: the models clients send, what each accepts, what a video costs in Flow credits, and the price
lookup. Pure.

Built from the captures of 23/10 (fixtures/flow/live-261023, FINDINGS 4-5): the three image models (text to image,
free) and Veo 3.1 Lite text to video (`veo_3_1_t2v_lite`, 8 s, 16:9, 10 credits on the account's tier: 50 -> 40
measured). Not offered until a live capture proves them (FINDINGS "i2i/t2v/i2v build 24/10"): an input image (the
upload `maseQ` and the start-image video RPC were never captured), other video ratios or durations, Veo Fast/Quality.
"""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_flow.constants import FALLBACK_PRICE

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
# Flow credits one video takes on the account's tier (GetModels `HTrJv`: 10 on tiers 1 and 2; spent 50 -> 40, 23/10)
VIDEO_CREDITS = {"flow-veo-3.1-lite": 10}

DEFAULT_PRICES = {**dict.fromkeys(MODELS, 1), **dict.fromkeys(VIDEO_MODELS, 2)}


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def lookup_price(table: dict[str, int] | None, model: str) -> tuple[int, str | None]:
    """(points, None) from the table, or (fallback, model) when the model is not in it: the caller logs and warns, so a
    wrong charge is never silent."""
    table = table or {}
    if model in table:
        return int(table[model]), None
    return FALLBACK_PRICE, model


def catalog() -> list[ModelSpec]:
    images = [ModelSpec(name, "image", ("text_to_image",),
                        limits={"max_images": 0, "ratios": list(ASPECTS), "default_ratio": DEFAULT_RATIO,
                                "description": "One image from a text prompt (Google Flow, %s), drawn in about 15 s."
                                               % display},
                        prices={"default": DEFAULT_PRICES[name]}, display_name=display)
              for name, (_key, display) in MODELS.items()]
    videos = [ModelSpec(name, "video", ("text_to_video",),
                        limits={"max_images": 0, "ratios": list(VIDEO_ASPECTS), "default_ratio": DEFAULT_RATIO,
                                "durations": [VIDEO_DURATION], "default_duration": VIDEO_DURATION,
                                "description": "An 8 s 720p clip with sound from a text prompt (Google Flow, %s), "
                                               "about 45 s to render." % display},
                        prices={"%ds" % VIDEO_DURATION: DEFAULT_PRICES[name]}, display_name=display)
              for name, (_key, display) in VIDEO_MODELS.items()]
    return images + videos
