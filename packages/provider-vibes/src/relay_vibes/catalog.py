"""vibes' model catalog: the models clients send, what each accepts, and their price lookup. Pure.

Three of them work on an output of the customer's own earlier vibes job (`source_job_id`), never on an upload: vibes
refuses another account's media and uploaded media (FINDINGS S7, v2v spike), so the job runs on the account that made
the source (a pinned job). Extend adds about 3.8 s to a video per round (up to about 16.7 s), video edit restyles a
video, image edit changes an image.
"""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec

IMAGE_MODEL = "vibes-image"
VIDEO_MODEL = "vibes-video"
EXTEND_MODEL = "vibes-video-extend"
VIDEO_EDIT_MODEL = "vibes-video-edit"
IMAGE_EDIT_MODEL = "vibes-image-edit"
KIND = {IMAGE_MODEL: "image", VIDEO_MODEL: "video", EXTEND_MODEL: "video", VIDEO_EDIT_MODEL: "video",
        IMAGE_EDIT_MODEL: "image"}
# the model names vibes' API takes (image edit has its own call and names none)
UPSTREAM = {IMAGE_MODEL: "midjen-base", VIDEO_MODEL: "midjen-short", EXTEND_MODEL: "midjen-extend",
            VIDEO_EDIT_MODEL: "midjen-video-edit", IMAGE_EDIT_MODEL: "midjen-base"}
# A model that works on a source job: the operation it is and the kind of output its source must have made.
SOURCE_MODES = {EXTEND_MODEL: ("extend_video", "video"), VIDEO_EDIT_MODEL: ("video_to_video", "video"),
                IMAGE_EDIT_MODEL: ("edit_image", "image")}

# The aspect ratio is a request field (`config.aspectRatio`), never a prompt flag. No ratio: vibes picks its own.
# A video comes out 9:16 whatever is asked (S5 text-to-video, S6 image-to-video asked 16:9): only 9:16 is promised
# until another ratio is proven.
RATIOS = {"image": ("1:1", "16:9", "9:16", "4:3", "3:4"), "video": ("9:16",)}
RESOLUTIONS = ("480p", "720p")        # video only, as `options.resolution`
DEFAULT_RESOLUTION = "480p"



def price_key(model: str, resolution: str | None = None) -> str:
    """The option a job is (`vibes.prices`, `vibes.costs`): an image by its model, a video with its resolution."""
    return "%s@%s" % (model, resolution) if model == VIDEO_MODEL else model


PRICE_KEYS = (IMAGE_MODEL, *(price_key(VIDEO_MODEL, r) for r in RESOLUTIONS), *SOURCE_MODES)
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
        ModelSpec(EXTEND_MODEL, "video", ("extend_video",),
                  limits={"max_images": 0, "ratios": [], "source": "video",
                          "description": "Extends a video an earlier vibes job of yours made by about 3.8 s (up to "
                                         "about 16.7 s in all); the prompt says how it goes on. Send its job id as "
                                         "source_job_id."},
                  price_keys={"default": EXTEND_MODEL}, display_name="Vibes Video Extend"),
        ModelSpec(VIDEO_EDIT_MODEL, "video", ("video_to_video",),
                  limits={"max_images": 0, "ratios": [], "source": "video",
                          "description": "Restyles a video an earlier vibes job of yours made, as the prompt says; "
                                         "same length and size. Send its job id as source_job_id."},
                  price_keys={"default": VIDEO_EDIT_MODEL}, display_name="Vibes Video Edit"),
        ModelSpec(IMAGE_EDIT_MODEL, "image", ("edit_image",),
                  limits={"max_images": 0, "ratios": [], "source": "image",
                          "description": "Changes an image an earlier vibes job of yours made, as the prompt says, in "
                                         "about 10 s. Send its job id as source_job_id."},
                  price_keys={"default": IMAGE_EDIT_MODEL}, display_name="Vibes Image Edit"),
    ]
