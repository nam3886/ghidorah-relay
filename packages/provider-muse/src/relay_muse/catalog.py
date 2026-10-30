"""muse's model catalog: the two models clients send and their price lookup. Pure.

muse is quota-only (a weekly Usage meter, no credits): every output costs one use of its pool and sells at one point
(`prices_at_cost`). Ratios, durations and the image count are what the spike of 26/10 measured: all followed exactly.
"""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec

IMAGE_MODEL = "muse-image"
VIDEO_MODEL = "muse-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}

MAX_N = 4                                              # 1, 2 and 4 images in one chat were drawn as asked
MAX_REFS = 4                                           # an edit's references: 1-2 followed well, 3-4 best effort
IMAGE_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4")   # each delivered at exactly that shape
VIDEO_RATIOS = ("16:9", "9:16")                        # text to video; a video from an image keeps the image's shape
DURATIONS = (5, 10)                                    # seconds, delivered to the frame
DEFAULT_DURATION = 5


def image_key(n: int) -> str:
    return IMAGE_MODEL if n == 1 else "%s@n%d" % (IMAGE_MODEL, n)


def video_key(seconds: int) -> str:
    return "%s@%ds" % (VIDEO_MODEL, seconds)


IMAGE_KEYS = tuple(image_key(n) for n in range(1, MAX_N + 1))
VIDEO_KEYS = tuple(video_key(s) for s in DURATIONS)
PRICE_KEYS = (*IMAGE_KEYS, *VIDEO_KEYS)
# one use of the pool per output (the default of `muse.costs`): muse's own meter is a coarse weekly percentage
DEFAULT_COSTS = {**{image_key(n): n for n in range(1, MAX_N + 1)}, **dict.fromkeys(VIDEO_KEYS, 1)}
# points 1:1 with the upstream cost, at least 1 (the owner's rule; the default of `muse.prices`, the owner edits it)
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)


def is_blank_prompt(text: str) -> bool:
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    return [
        ModelSpec(IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
                  limits={"max_images": MAX_REFS, "max_n": MAX_N, "ratios": list(IMAGE_RATIOS),
                          "description": "1 to %d images from a text prompt (n, priced per image), or one image made "
                                         "from 1 to %d reference images as the text asks (an edit, priced as one "
                                         "image; the text may call them image 1, image 2... in the order sent; 1 or 2 "
                                         "are followed best), in the ratio asked (or muse's own choice)."
                                         % (MAX_N, MAX_REFS)},
                  price_keys={"n=%d" % n: image_key(n) for n in range(1, MAX_N + 1)}, display_name="Muse Image"),
        ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                  limits={"max_images": 1, "ratios": list(VIDEO_RATIOS), "durations": list(DURATIONS),
                          "description": "A 5 or 10 s video with sound from a text prompt (16:9 or 9:16), or one "
                                         "image animated in its own shape."},
                  price_keys={"%ds" % s: video_key(s) for s in DURATIONS}, display_name="Muse Video"),
    ]
