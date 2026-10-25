"""vibes' model catalog: the two models clients send, what each accepts, and their price lookup. Pure."""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec

IMAGE_MODEL = "vibes-image"
VIDEO_MODEL = "vibes-video"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video"}
UPSTREAM = {IMAGE_MODEL: "midjen-base", VIDEO_MODEL: "midjen-short"}     # the model names vibes' API takes

# The aspect ratio is a request field (`config.aspectRatio`), never a prompt flag. No ratio: vibes picks its own.
# A video comes out 9:16 whatever is asked (S5 text-to-video, S6 image-to-video asked 16:9): only 9:16 is promised
# until another ratio is proven.
RATIOS = {"image": ("1:1", "16:9", "9:16", "4:3", "3:4"), "video": ("9:16",)}
RESOLUTIONS = ("480p", "720p")        # video only, as `options.resolution`
DEFAULT_RESOLUTION = "480p"



def price_key(model: str, resolution: str | None = None) -> str:
    """The option a job is (`vibes.prices`, `vibes.costs`): an image by its model, a video with its resolution."""
    return "%s@%s" % (model, resolution) if model == VIDEO_MODEL else model


PRICE_KEYS = (IMAGE_MODEL, *(price_key(VIDEO_MODEL, r) for r in RESOLUTIONS))
DEFAULT_COSTS = dict.fromkeys(PRICE_KEYS, 1)       # one job of the daily budget (the default of `vibes.costs`)
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)     # points (the default of `vibes.prices`, the owner edits it)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    return [
        ModelSpec(IMAGE_MODEL, "image", ("text_to_image",),
                  limits={"max_images": 0, "ratios": list(RATIOS["image"]),
                          "description": "An image from a text prompt, drawn in about 10 s."},
                  price_keys={"default": IMAGE_MODEL}, display_name="Vibes Image"),
        ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                  limits={"max_images": 1, "ratios": list(RATIOS["video"]), "resolutions": list(RESOLUTIONS),
                          "description": "A clip of about 5 s from a text prompt or one image; 480p or 720p, "
                                         "about 5 min to render."},
                  price_keys={r: price_key(VIDEO_MODEL, r) for r in RESOLUTIONS}, display_name="Vibes Video"),
    ]
