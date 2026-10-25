"""meta's model catalog: the two models clients send and their price lookup. Pure."""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec
from relay_meta.constants import RATIOS

IMAGE_MODEL = "meta-image"
VIDEO_MODEL = "meta-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}

PRICE_KEYS = (IMAGE_MODEL, VIDEO_MODEL)
DEFAULT_COSTS = {IMAGE_MODEL: 1, VIDEO_MODEL: 1}      # one job of the daily budget (the default of `meta.costs`)
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)      # points (the default of `meta.prices`, the owner edits it)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    return [
        ModelSpec(IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
                  limits={"max_images": 1, "ratios": list(RATIOS["image"]),
                          "description": "Images from a text prompt (several per run), or one image edited "
                                         "as the text asks."},
                  price_keys={"default": IMAGE_MODEL}, display_name="Meta AI Image"),
        ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                  limits={"max_images": 1, "ratios": list(RATIOS["video"]),
                          "description": "A video from a text prompt, or one image animated with optional "
                                         "motion text; about 10 s at 720p."},
                  price_keys={"default": VIDEO_MODEL}, display_name="Meta AI Video"),
    ]
