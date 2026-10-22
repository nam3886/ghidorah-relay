"""meta's model catalog: the two models clients send and their price lookup. Pure."""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_meta.constants import FALLBACK_PRICE

IMAGE_MODEL = "meta-image"
VIDEO_MODEL = "meta-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}

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
                  limits={"max_images": 0, "description": "Images from a text prompt; several images per run."},
                  prices={"default": DEFAULT_PRICES[IMAGE_MODEL]}, display_name="Meta AI Image"),
        ModelSpec(VIDEO_MODEL, "video", ("image_to_video",),
                  limits={"max_images": 1, "min_images": 1,
                          "description": "Animates one image, with optional motion text; about 10 s at 720p."},
                  prices={"default": DEFAULT_PRICES[VIDEO_MODEL]}, display_name="Meta AI Video"),
    ]
