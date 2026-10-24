"""gemini's model catalog: the image and video models clients send, what they accept, and the price lookup. Pure.

The video model is the web app's "Create video" tool (Gemini Omni, `omni_1p2_pro`): it needs an account whose plan
includes it (AI Pro measured 24/10; a free account answers "upgrade your subscription"). One 10 s 1280x720 clip with
sound per job, from text or from one image and text. Landscape (16:9) is the only ratio measured; the tool's portrait
option (9:16, id 17) is not sold until it is.
"""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_gemini.constants import FALLBACK_PRICE

IMAGE_MODEL = "gemini-nano-banana-2"
DISPLAY = "Gemini Nano Banana 2"
# The image tool's ratio options and their ids (otAQ7b tool 248, measured 23/10). No ratio: gemini picks (16:9 for the
# two measured prompts).
RATIO_IDS = {"1:1": 61, "9:16": 59, "3:4": 60, "4:3": 62, "16:9": 63}

VIDEO_MODEL = "gemini-omni-video"
VIDEO_DISPLAY = "Gemini Omni video"
VIDEO_RATIO_IDS = {"16:9": 16}      # otAQ7b tool 185: 16 Landscape (16:9, the default), 17 Portrait (not measured)
VIDEO_RATIO = "16:9"
VIDEO_DURATION = 10                 # no length option; both measured clips were 10.0 s

DEFAULT_PRICES = {IMAGE_MODEL: 1, VIDEO_MODEL: 2}


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
                      prices={"default": DEFAULT_PRICES[IMAGE_MODEL]}, display_name=DISPLAY),
            ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                      limits={"max_images": 1, "ratios": list(VIDEO_RATIO_IDS), "default_ratio": VIDEO_RATIO,
                              "durations": [VIDEO_DURATION], "default_duration": VIDEO_DURATION,
                              "description": "A 10 s 1280x720 clip with sound from a text prompt, or from one image "
                                             "and a text (Gemini web, Gemini Omni); about 1-2 min."},
                      prices={"%ds" % VIDEO_DURATION: DEFAULT_PRICES[VIDEO_MODEL]}, display_name=VIDEO_DISPLAY)]
