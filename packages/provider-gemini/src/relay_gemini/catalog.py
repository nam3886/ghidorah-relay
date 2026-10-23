"""gemini's model catalog: the image model clients send, what it accepts, and its price lookup. Pure.

TODO(video, next step): Veo text/image to video. The send reaches gemini's video tool (inner[49] = 11, ratio ids 16
and 17), but the spike account is on the free plan and was answered "upgrade your subscription" (FINDINGS 5); the
status read and the result of a real video are not measured, so no video model is sold until an AI Pro session proves
them.
"""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_gemini.constants import FALLBACK_PRICE

IMAGE_MODEL = "gemini-nano-banana-2"
DISPLAY = "Gemini Nano Banana 2"
# The image tool's ratio options and their ids (otAQ7b tool 248, measured 23/10). No ratio: gemini picks (16:9 for the
# two measured prompts).
RATIO_IDS = {"1:1": 61, "9:16": 59, "3:4": 60, "4:3": 62, "16:9": 63}

DEFAULT_PRICES = {IMAGE_MODEL: 1}


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
    return [ModelSpec(IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
                      limits={"max_images": 1, "ratios": list(RATIO_IDS),
                              "description": "One image from a text prompt, or one image edited as the text asks "
                                             "(Gemini web, Nano Banana 2); about 30 s."},
                      prices={"default": DEFAULT_PRICES[IMAGE_MODEL]}, display_name=DISPLAY)]
