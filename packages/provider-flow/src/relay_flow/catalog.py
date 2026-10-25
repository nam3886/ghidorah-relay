"""flow's model catalog: the models clients send, what each accepts, what a video costs in Flow credits, and the price
lookup. Pure.

Built from the captures of 23/10 (fixtures/flow/live-261023, FINDINGS 4-5): the three image models (text to image,
free) and Veo 3.1 Lite text to video (`veo_3_1_t2v_lite`, 8 s, 10 credits on the account's tier: 50 -> 40 measured).
From GetModels of 25/10 (FINDINGS "Catalog 25/10"): Lite's 9:16 in the same key, and Veo 3.1 Fast text to video
(8 s, 20 credits on free and Pro; one key per orientation). Not offered until a live capture proves them: an input
image (the upload `maseQ` and the start-image video RPC were never captured), 4 s and 6 s (Ultra only), Veo Quality.
"""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec

# relay name -> (Flow's model key, display name). Flow draws the image inside the generate request (HOLD).
MODELS = {
    "flow-nano-banana-2.1": ("BELUGA", "Nano Banana 2.1"),
    "flow-nano-banana-pro": ("GEM_PIX_2", "Nano Banana Pro"),
    "flow-nano-banana-2-lite": ("HARBOR_SEAL", "Nano Banana 2 Lite"),
}
# The ratio is a request field. Flow's aspect codes (measured 23/10: 3 gave 1376x768).
ASPECTS = {"1:1": 1, "9:16": 2, "16:9": 3, "3:4": 4, "4:3": 5}
DEFAULT_RATIO = "16:9"

# relay name -> ({ratio: (Flow's model key, aspect code)}, display name). A video is answered at once and polled
# (DETACH). The aspect code is the field after the model key: 2 landscape (recorded 23/10: 1280x720), 1 portrait.
# GetModels lists each key's codes (`[[2, 1]]` Lite, both in one key; `[[2]]` and `[[1]]` for Fast's two keys).
VIDEO_MODELS = {
    "flow-veo-3.1-lite": ({"16:9": ("veo_3_1_t2v_lite", 2), "9:16": ("veo_3_1_t2v_lite", 1)}, "Veo 3.1 Lite"),
    "flow-veo-3.1-fast": ({"16:9": ("veo_3_1_t2v_fast", 2), "9:16": ("veo_3_1_t2v_fast_portrait", 1)},
                          "Veo 3.1 Fast"),
}
VIDEO_DURATION = 8               # the 4 s and 6 s Lite and Fast keys are offered only on Flow's Ultra tier


def price_key(model: str) -> str:
    """The option a job is (`flow.prices`, `flow.costs`): an image by its model, a video with its one length."""
    return "%s@%ds" % (model, VIDEO_DURATION) if model in VIDEO_MODELS else model


# Options flow sells nothing of yet (no live capture of their request): their Flow credits from the official table
# (support.google.com/flow 16526234, the non-Ultra column), so the owner can see and price them ahead; a job of one
# cannot be planned until its model is in the catalog.
TABLE_ONLY_COSTS = {
    **{"flow-veo-3.1-fast@%ds" % d: 20 for d in (4, 6)},
    "flow-veo-3.1-quality@8s": 100,
    **{"flow-omni-flash-720p@%ds" % d: c for d, c in ((4, 7), (6, 10), (8, 12), (10, 15))},
    **{"flow-omni-flash-360p@%ds" % d: c for d, c in ((4, 4), (6, 5), (8, 6), (10, 7))},
}
PRICE_KEYS = tuple(price_key(m) for m in (*MODELS, *VIDEO_MODELS)) + tuple(TABLE_ONLY_COSTS)
# What one job costs the account, in the unit of its pool (the default of `flow.costs`): an image takes no Flow credit
# (50 -> 50 measured) and counts one job; an 8 s video takes 10 Flow credits (Lite) or 20 (Fast) on free and Pro
# (GetModels `HTrJv`, 25/10; Lite spent 50 -> 40, 23/10); the table-only options above.
DEFAULT_COSTS = {**dict.fromkeys(MODELS, 1), price_key("flow-veo-3.1-lite"): 10, price_key("flow-veo-3.1-fast"): 20,
                 **TABLE_ONLY_COSTS}
# The sale price in points, the default of `flow.prices` (the owner edits it): its cost, one point per Flow credit or
# image job.
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    images = [ModelSpec(name, "image", ("text_to_image",),
                        limits={"max_images": 0, "ratios": list(ASPECTS), "default_ratio": DEFAULT_RATIO,
                                "description": "One image from a text prompt (Google Flow, %s), drawn in about 15 s."
                                               % display},
                        price_keys={"default": price_key(name)}, display_name=display)
              for name, (_key, display) in MODELS.items()]
    videos = [ModelSpec(name, "video", ("text_to_video",),
                        limits={"max_images": 0, "ratios": list(keys), "default_ratio": DEFAULT_RATIO,
                                "durations": [VIDEO_DURATION], "default_duration": VIDEO_DURATION,
                                "description": "An 8 s 720p clip with sound from a text prompt (Google Flow, %s), "
                                               "about 45 s to render." % display},
                        price_keys={"%ds" % VIDEO_DURATION: price_key(name)}, display_name=display)
              for name, (keys, display) in VIDEO_MODELS.items()]
    return images + videos
