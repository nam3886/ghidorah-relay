"""qwen's model catalog: the models clients send, what each accepts, what a video costs in qwen credits, and the price
lookup. Pure.

What the web app offers (bundle 0.3.22, FINDINGS 8): images `qwen-image-3.0` (the default; `-pro` is for paid members
only), videos HappyHorse 1.1 (the default) and 1.0. Wan 2.7 is in the bundle but hidden from the model picker, so it is
not offered. Every video is 720P here: HappyHorse 1080P is for paid plans only (`kind: purchase` in the price list).
"""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_qwen.constants import FALLBACK_PRICE

IMAGE_MODEL = "qwen-image-3.0"
IMAGE_UPSTREAM = "qwen-image-3.0"
IMAGE_RATIOS = ("16:9", "1:1", "9:16", "4:3", "3:4", "3:2", "2:3")
# relay name -> (the `rootModel` of the video request, display name)
VIDEO_MODELS = {
    "qwen-happyhorse-1.1": ("happyhorse11", "HappyHorse 1.1"),
    "qwen-happyhorse-1.0": ("happyhorse", "HappyHorse 1.0"),
}
VIDEO_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16")
DEFAULT_RATIO = "16:9"
DURATIONS = (5, 10, 15)
DEFAULT_DURATION = 5
RESOLUTION = "720P"
# qwen credits one 720P video takes, by model and seconds (GET /api/v2/credits/pricing, 23/10; text and image to video
# cost the same). A free account gets 40 a day.
VIDEO_CREDITS = {
    "qwen-happyhorse-1.1": {5: 30, 10: 60, 15: 90},
    "qwen-happyhorse-1.0": {5: 40, 10: 80, 15: 120},
}

DEFAULT_PRICES = {IMAGE_MODEL: 1, **{"%s@%ds" % (m, d): d // 5 for m in VIDEO_MODELS for d in DURATIONS}}


def price_key(model: str, duration: int | None) -> str:
    """`model` for an image, `model@<seconds>s` for a video."""
    return model if duration is None else "%s@%ds" % (model, duration)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def lookup_price(table: dict[str, int] | None, key: str) -> tuple[int, str | None]:
    """(points, None) from the table, or (fallback, key) when the key is not in it: the caller logs and warns, so a
    wrong charge is never silent."""
    table = table or {}
    if key in table:
        return int(table[key]), None
    return FALLBACK_PRICE, key


def catalog() -> list[ModelSpec]:
    image = ModelSpec(IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
                      limits={"max_images": 1, "ratios": list(IMAGE_RATIOS), "default_ratio": DEFAULT_RATIO,
                              "description": "One image from a text prompt, or one image edited by a prompt "
                                             "(Qwen Image 3.0), drawn in about 1-2 min."},
                      prices={"default": DEFAULT_PRICES[IMAGE_MODEL]}, display_name="Qwen Image 3.0")
    return [image] + [
        ModelSpec(name, "video", ("text_to_video", "image_to_video"),
                  limits={"max_images": 1, "ratios": list(VIDEO_RATIOS), "default_ratio": DEFAULT_RATIO,
                          "durations": list(DURATIONS), "default_duration": DEFAULT_DURATION,
                          "description": "A 720p clip with sound of 5, 10 or 15 s from a text prompt or one start "
                                         "image (%s), about 10 min to render." % display},
                  prices={"%ds" % d: DEFAULT_PRICES[price_key(name, d)] for d in DURATIONS}, display_name=display)
        for name, (_root, display) in VIDEO_MODELS.items()]
