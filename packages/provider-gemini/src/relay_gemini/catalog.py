"""gemini's model catalog: the image and video models clients send, what they accept, and the price lookup. Pure.

The video model is the web app's "Create video" tool (Gemini Omni, `omni_1p2_pro`): it needs an account whose plan
includes it (AI Pro measured 24/10; a free account answers "upgrade your subscription"). One 10 s clip with sound per
job, from text or from one image and text, landscape (16:9, 1280x720) only.

The pro-tier image model is the image tool sent with the account's "Pro" mode (AI Pro): Google labels its images "Nano
Banana 2.1" (the default Flash-Lite mode draws "Nano Banana 2 Lite", the free plan's Auto "Nano Banana 2"; 25/10). Its
jobs take the `image_pro` pool, whose budget is 0 for a free account (spec `POOL_DEFAULTS`), so only a pro or ultra
account ever claims one.
"""

from relay_core import frames
from relay_core.providers import ModelSpec

IMAGE_MODEL = "gemini-nano-banana-2"
DISPLAY = "Gemini Nano Banana 2"
PRO_IMAGE_MODEL = "gemini-nano-banana-2.1"
PRO_DISPLAY = "Gemini Nano Banana 2.1"
IMAGE_MODELS = (IMAGE_MODEL, PRO_IMAGE_MODEL)
# The image tool's ratio options and their ids (otAQ7b tool 248, measured 23/10; the same under every mode). No ratio:
# gemini picks (16:9 for the two measured prompts).
RATIO_IDS = {"1:1": 61, "9:16": 59, "3:4": 60, "4:3": 62, "16:9": 63}

VIDEO_MODEL = "gemini-omni-video"
VIDEO_DISPLAY = "Gemini Omni video"
# otAQ7b tool 185: 16 Landscape (the default), 17 Portrait. Portrait is not sold: two text to video sends with [[17]]
# and the message's ratio "9:16" both came back 1280x720 landscape, and [[17]] with no ratio in the message was answered
# "Sorry, something went wrong" (25/10, FINDINGS 7).
VIDEO_RATIO_IDS = {"16:9": 16}
VIDEO_RATIO = "16:9"
VIDEO_DURATION = 10                 # no length option; both measured clips were 10.0 s



def price_key(model: str) -> str:
    """The option a job is (`gemini.prices`, `gemini.costs`): an image by its model, a video with its one length."""
    return "%s@%ds" % (model, VIDEO_DURATION) if model == VIDEO_MODEL else model


PRICE_KEYS = (IMAGE_MODEL, PRO_IMAGE_MODEL, price_key(VIDEO_MODEL))
# What one job costs the account (the default of `gemini.costs`): one use of the image budget, or one of the video
# budget (a video takes about a fifth of a 5-hour window on AI Pro: 453 of 2400 units; counted as one use for now).
DEFAULT_COSTS = {IMAGE_MODEL: 1, PRO_IMAGE_MODEL: 1, price_key(VIDEO_MODEL): 1}
DEFAULT_PRICES = {IMAGE_MODEL: 1, PRO_IMAGE_MODEL: 2, price_key(VIDEO_MODEL): 6}   # points (default of `gemini.prices`)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def _image(model: str, display: str, which: str) -> ModelSpec:
    return ModelSpec(model, "image", ("text_to_image", "image_to_image"),
                     limits={"max_images": 1, "ratios": list(RATIO_IDS),
                             "description": "One image from a text prompt, or one image edited as the text asks "
                                            "(Gemini web, %s); about 30 s." % which},
                     price_keys={"default": model}, display_name=display)


def catalog() -> list[ModelSpec]:
    return [_image(IMAGE_MODEL, DISPLAY, "Nano Banana 2"),
            _image(PRO_IMAGE_MODEL, PRO_DISPLAY, "Nano Banana 2.1, on AI Pro accounts"),
            ModelSpec(VIDEO_MODEL, "video", ("text_to_video", "image_to_video"),
                      limits={"max_images": 1, "ratios": list(VIDEO_RATIO_IDS), "default_ratio": VIDEO_RATIO,
                              "durations": [VIDEO_DURATION], "default_duration": VIDEO_DURATION,
                              "description": "A 10 s 1280x720 clip with sound from a text prompt, "
                                             "or from one image and a text (Gemini web, Gemini Omni); about 1-2 min."},
                      price_keys={"%ds" % VIDEO_DURATION: price_key(VIDEO_MODEL)}, display_name=VIDEO_DISPLAY)]
