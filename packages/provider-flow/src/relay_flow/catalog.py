"""flow's model catalog: the image models clients send, what each accepts, and their price lookup. Pure.

TODO(video, next step): Veo 3.1 text to video (`YhhmEf` submit, `jwpduf` status, `as29s` for the mp4; FINDINGS 5). It
is not offered: a video costs Flow credits (10 per 8 s Lite clip on the Pro tier), so it needs a `video` pool and
wallet, the credit balance read (`nzlxg`), the VIDEO_GENERATION token and a DETACH poll before it can be sold.
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

DEFAULT_PRICES = dict.fromkeys(MODELS, 1)


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
    return [ModelSpec(name, "image", ("text_to_image",),
                      limits={"max_images": 0, "ratios": list(ASPECTS), "default_ratio": DEFAULT_RATIO,
                              "description": "One image from a text prompt (Google Flow, %s), drawn in about 15 s."
                                             % display},
                      prices={"default": DEFAULT_PRICES[name]}, display_name=display)
            for name, (_key, display) in MODELS.items()]
