"""qwen's model catalog: the models clients send, what each accepts, what a video costs in qwen credits, and the price
lookup. Pure.

What the web app offers (bundle 0.3.22, read again 25/10; FINDINGS 8 and "Catalog 25/10"):
- images `qwen-image-3.0` and `qwen-image-3.0-pro` (pro members only: `requiredMemberType: pro`, adds the ratio
  `auto`), each with up to 3 input images (the live config's `image_edit.image_max_count`);
- videos HappyHorse 1.1, HappyHorse 1.0 and Wan 2.7 at 720P or 1080P, 5/10/15 s. HappyHorse 1080P is for paid plans
  only (`kind: purchase` in the price list); Wan 1080P is for all. HappyHorse takes one start image (`standard`) or
  2-9 reference images (`multi-reference`); Wan takes one start image or a first and a last frame
  (`first-last-frame`). Wan 2.7 is in the bundle but hidden from its model picker: it is offered unconfirmed.
A pro-only option draws on its own pool (`image_pro`, `video_pro`) that holds nothing on a free account, so a free
account is never picked for it.
"""

from relay_core import frames
from relay_core.providers import ModelSpec

# relay name -> (the upstream `model` of the image request, display name, pool)
IMAGE_MODELS = {
    "qwen-image-3.0": ("qwen-image-3.0", "Qwen Image 3.0", "image"),
    "qwen-image-3.0-pro": ("qwen-image-3.0-pro", "Qwen Image 3.0 Pro", "image_pro"),
}
IMAGE_RATIOS = ("16:9", "1:1", "9:16", "4:3", "3:4", "3:2", "2:3")
PRO_IMAGE_RATIOS = ("auto", *IMAGE_RATIOS)
MAX_IMAGE_INPUTS = 3          # the live config's `image_edit.image_max_count` (25/10)
# relay name -> (the `rootModel` of the video request, display name, the image modes it has beyond one start image)
VIDEO_MODELS = {
    "qwen-happyhorse-1.1": ("happyhorse11", "HappyHorse 1.1", "multi-reference"),
    "qwen-happyhorse-1.0": ("happyhorse", "HappyHorse 1.0", "multi-reference"),
    "qwen-wan-2.7": ("wan27", "Wan 2.7", "first-last-frame"),
}
MODE_MAX_IMAGES = {"multi-reference": 9, "first-last-frame": 2}     # the bundle's `gs`; one start image: `standard`
VIDEO_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16")
DEFAULT_RATIO = "16:9"
DURATIONS = (5, 10, 15)
DEFAULT_DURATION = 5
RESOLUTIONS = ("720p", "1080p")      # `options.resolution`; the request says 720P / 1080P
DEFAULT_RESOLUTION = "720p"
PRO_1080P = ("qwen-happyhorse-1.1", "qwen-happyhorse-1.0")      # 1080P only on a paid plan (the bundle's `es`)
# qwen credits one video takes, by model, resolution and seconds (GET /api/v2/credits/pricing, recorded 23/10; text,
# image, multi-reference and first-last-frame videos cost the same). A free account gets 40 a day.
VIDEO_CREDITS = {
    "qwen-happyhorse-1.1": {"720p": {5: 30, 10: 60, 15: 90}, "1080p": {5: 40, 10: 80, 15: 120}},
    "qwen-happyhorse-1.0": {"720p": {5: 40, 10: 80, 15: 120}, "1080p": {5: 70, 10: 140, 15: 210}},
    "qwen-wan-2.7": {"720p": {5: 20, 10: 40, 15: 60}, "1080p": {5: 30, 10: 60, 15: 90}},
}


def video_pool(model: str, resolution: str) -> str:
    return "video_pro" if resolution == "1080p" and model in PRO_1080P else "video"


def pool_of(model: str, params) -> str:
    """The pool a planned job draws on (the plan's `pool`), from its model and params."""
    if params["kind"] == "image":
        return IMAGE_MODELS[model][2]
    return video_pool(model, str(params.get("resolution", "720P")).lower())


def price_key(model: str, duration: int | None, resolution: str = DEFAULT_RESOLUTION) -> str:
    """The option a job is (`qwen.prices`, `qwen.costs`): `model` for an image, `model@<seconds>s` for a 720p video,
    `model@1080p@<seconds>s` for a 1080p one."""
    if duration is None:
        return model
    return "%s@%ds" % (model, duration) if resolution == "720p" else "%s@%s@%ds" % (model, resolution, duration)


_VIDEO_OPTIONS = [(m, r, d) for m in VIDEO_MODELS for r in RESOLUTIONS for d in DURATIONS]
PRICE_KEYS = (*IMAGE_MODELS, *(price_key(m, d, r) for m, r, d in _VIDEO_OPTIONS))
# What one job costs the account, in the unit of its pool (the default of `qwen.costs`): an image took no qwen credit
# (8 of 8 measured) and counts one job; a video its credits above.
DEFAULT_COSTS = {**dict.fromkeys(IMAGE_MODELS, 1),
                 **{price_key(m, d, r): VIDEO_CREDITS[m][r][d] for m, r, d in _VIDEO_OPTIONS}}
# The sale price in points, the default of `qwen.prices` (the owner edits it): an image sells at 1 (owner 24/10), a
# pro image at 2 (proposed); a 720p video at 1 point per 5 s, a 1080p one at twice that (proposed).
DEFAULT_PRICES = {"qwen-image-3.0": 1, "qwen-image-3.0-pro": 2,
                  **{price_key(m, d, r): d // 5 * (2 if r == "1080p" else 1) for m, r, d in _VIDEO_OPTIONS}}


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    images = [
        ModelSpec(name, "image", ("text_to_image", "image_to_image"),
                  limits={"max_images": MAX_IMAGE_INPUTS,
                          "ratios": list(PRO_IMAGE_RATIOS if pool == "image_pro" else IMAGE_RATIOS),
                          "default_ratio": "auto" if pool == "image_pro" else DEFAULT_RATIO,
                          "description": "One image from a text prompt, or from a prompt and up to 3 reference "
                                         "images (%s%s), drawn in about 1-2 min." % (
                                             display, "; paid qwen accounts only" if pool == "image_pro" else "")},
                  price_keys={"default": name}, display_name=display)
        for name, (_up, display, pool) in IMAGE_MODELS.items()]
    videos = []
    for name, (_root, display, mode) in VIDEO_MODELS.items():
        images_text = ("one start image or 2-9 reference images" if mode == "multi-reference"
                       else "one start image or a first and a last frame")
        videos.append(ModelSpec(
            name, "video", ("text_to_video", "image_to_video"),
            limits={"max_images": MODE_MAX_IMAGES[mode], "ratios": list(VIDEO_RATIOS), "default_ratio": DEFAULT_RATIO,
                    "durations": list(DURATIONS), "default_duration": DEFAULT_DURATION,
                    "resolutions": list(RESOLUTIONS), "default_resolution": DEFAULT_RESOLUTION,
                    "description": "A clip with sound of 5, 10 or 15 s at 720p or 1080p (options.resolution) from a "
                                   "text prompt, %s (%s)%s, about 10 min to render." % (
                                       images_text, display,
                                       "; 1080p on paid qwen accounts only" if name in PRO_1080P else "")},
            price_keys={("%ds" % d if r == "720p" else "%s %ds" % (r, d)): price_key(name, d, r)
                        for r in RESOLUTIONS for d in DURATIONS},
            display_name=display))
    return images + videos
