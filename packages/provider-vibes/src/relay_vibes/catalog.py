"""vibes' model catalog: the two models clients send, what each accepts, and their price lookup. Pure."""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_vibes.constants import FALLBACK_PRICE

IMAGE_MODEL = "vibes-image"
VIDEO_MODEL = "vibes-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}
UPSTREAM = {IMAGE_MODEL: "midjen-base", VIDEO_MODEL: "midjen-short"}     # the model names vibes' API takes

# The aspect ratio is a request field, never a prompt flag. No ratio: vibes picks its own.
RATIOS = {"image": ("1:1", "16:9", "9:16", "4:3", "3:4"), "video": ("16:9", "9:16", "1:1")}
RESOLUTIONS = ("480p", "720p")        # video only, as `options.resolution`
DEFAULT_RESOLUTION = "480p"

DEFAULT_PRICES = {IMAGE_MODEL: 1, VIDEO_MODEL: 1}


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
    return [
        ModelSpec(IMAGE_MODEL, "image", ("text_to_image",),
                  limits={"max_images": 0, "ratios": list(RATIOS["image"]),
                          "description": "An image from a text prompt, drawn in about 10 s."},
                  prices={"default": DEFAULT_PRICES[IMAGE_MODEL]}, display_name="Vibes Image"),
        ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                  limits={"max_images": 1, "ratios": list(RATIOS["video"]), "resolutions": list(RESOLUTIONS),
                          "description": "A clip of about 5 s from a text prompt or one image; 480p or 720p, "
                                         "about 5 min to render."},
                  prices={"default": DEFAULT_PRICES[VIDEO_MODEL]}, display_name="Vibes Video"),
    ]
