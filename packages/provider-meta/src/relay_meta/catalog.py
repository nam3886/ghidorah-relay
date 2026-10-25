"""meta's model catalog: the two models clients send and their price lookup. Pure."""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec
from relay_meta.constants import RATIOS

IMAGE_MODEL = "meta-image"
VIDEO_MODEL = "meta-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}

# An image job draws `n` images in its one chat (n = 1..MAX_N, asked in the words: FINDINGS.md) and is priced per image
# at creation: one price key per count, n = 1 keeping the model's own key.
MAX_N = 4
# An edit takes up to MAX_REFS reference images, attached together in its one chat (two were drawn into one picture,
# 25/10); a video takes one.
MAX_REFS = 3


def image_key(n: int) -> str:
    return IMAGE_MODEL if n == 1 else "%s@n%d" % (IMAGE_MODEL, n)


IMAGE_KEYS = tuple(image_key(n) for n in range(1, MAX_N + 1))
PRICE_KEYS = (*IMAGE_KEYS, VIDEO_MODEL)
# one use of the daily budget per image (the default of `meta.costs`; meta's own count per image is not measured)
DEFAULT_COSTS = {**{image_key(n): n for n in range(1, MAX_N + 1)}, VIDEO_MODEL: 1}
# points 1:1 with the upstream cost, at least 1 (the owner's rule; the default of `meta.prices`, the owner edits it)
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    return [
        ModelSpec(IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
                  limits={"max_images": MAX_REFS, "max_n": MAX_N, "ratios": list(RATIOS["image"]),
                          "description": "1 to %d images from a text prompt (n, priced per image), or one image made "
                                         "from 1 to %d reference images as the text asks." % (MAX_N, MAX_REFS)},
                  price_keys={"n=%d" % n: image_key(n) for n in range(1, MAX_N + 1)}, display_name="Meta AI Image"),
        ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                  limits={"max_images": 1, "ratios": list(RATIOS["video"]),
                          "description": "A video from a text prompt, or one image animated with optional "
                                         "motion text; about 10 s at 720p."},
                  price_keys={"default": VIDEO_MODEL}, display_name="Meta AI Video"),
    ]
