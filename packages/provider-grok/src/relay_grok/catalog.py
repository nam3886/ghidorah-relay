"""grok's model catalog: the two models clients send, what they accept, what a job costs and the price lookup. Pure.

A free account draws `n` images in one Imagine send (`num_generations`, one use of `imagePro` each), edits one image
from 1 to MAX_REFS inputs (one use), or makes one 6 s 720p video from text (one use of `video720p`). Only 1:1 images
and a 16:9 video were measured (06/11); the other ratios and MAX_REFS = 3 are cut before claims if the live smoke
disproves them.
"""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec
from relay_grok.constants import VIDEO_DURATION_S

IMAGE_MODEL = "grok-image"
VIDEO_MODEL = "grok-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}

IMAGE_RATIOS = ("1:1", "2:3", "3:2", "9:16", "16:9")
VIDEO_RATIOS = ("16:9", "9:16", "1:1")
DEFAULT_IMAGE_RATIO = "1:1"
DEFAULT_VIDEO_RATIO = "16:9"
MAX_N = 4          # a free account has about 8 image uses a day: n = 4 takes half of it
MAX_REFS = 3       # input images one edit takes (one was measured)


def image_key(n: int) -> str:
    """The price option of a grok-image job of `n` images (an edit is n = 1)."""
    return IMAGE_MODEL if n == 1 else "%s@n%d" % (IMAGE_MODEL, n)


PRICE_KEYS = (*(image_key(n) for n in range(1, MAX_N + 1)), VIDEO_MODEL)
# What one job costs the account, in uses of its pool (the default of `grok.costs`): one use per image, one per video.
DEFAULT_COSTS = {"grok-image": 1, "grok-image@n2": 2, "grok-image@n3": 3, "grok-image@n4": 4, "grok-video": 1}
# The sale price in points, the default of `grok.prices` (the owner edits it): its cost, 1:1.
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    return [
        ModelSpec(IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
                  limits={"max_images": MAX_REFS, "max_n": MAX_N, "ratios": list(IMAGE_RATIOS),
                          "description": "1 to %d images from a text prompt (n, priced per image; without a ratio "
                                         "1:1), or one image edited from 1 to %d input images as the text asks "
                                         "(it keeps the input's shape)." % (MAX_N, MAX_REFS)},
                  price_keys={"n=%d" % n: image_key(n) for n in range(1, MAX_N + 1)}, display_name="Grok Imagine"),
        ModelSpec(VIDEO_MODEL, "video", ("text_to_video",),
                  limits={"max_images": 0, "ratios": list(VIDEO_RATIOS), "durations": [VIDEO_DURATION_S],
                          "default_duration": VIDEO_DURATION_S,
                          "description": "A 6 s 720p video with sound from a text prompt (without a ratio 16:9)."},
                  price_keys={"default": VIDEO_MODEL}, display_name="Grok Imagine Video"),
    ]
