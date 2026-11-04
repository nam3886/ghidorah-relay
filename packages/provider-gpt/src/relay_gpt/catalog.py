"""gpt's model catalog: the one image model clients send, what it accepts, what a job costs and the price lookup. Pure.

chatgpt.com draws one image per send, from a prompt or from a prompt and one uploaded image (an edit; only one input
was proven live). With no ratio asked it draws 3:2 (1536x1024); a ratio is asked for in the prompt frame (spike sizes:
1:1 1254x1254, 16:9 1672x941, 9:16 941x1672, 3:2 1536x1024).
"""

from relay_core import frames
from relay_core.pricing import prices_at_cost
from relay_core.providers import ModelSpec

IMAGE_MODEL = "gpt-image"
RATIOS = ("1:1", "16:9", "9:16", "3:2")
MAX_IMAGE_INPUTS = 1


def price_key(model: str) -> str:
    """The option a job is (`gpt.prices`, `gpt.costs`): the model."""
    return model


PRICE_KEYS = (IMAGE_MODEL,)
# What one job costs the account, in the unit of its pool (the default of `gpt.costs`): one image job.
DEFAULT_COSTS = {IMAGE_MODEL: 1}
# The sale price in points, the default of `gpt.prices` (the owner edits it): its cost, 1:1.
DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def catalog() -> list[ModelSpec]:
    return [ModelSpec(
        IMAGE_MODEL, "image", ("text_to_image", "image_to_image"),
        limits={"max_images": MAX_IMAGE_INPUTS, "ratios": list(RATIOS),
                "description": "One image from a text prompt, or an edit of one input image, drawn in about 30 s "
                               "(GPT Image). Without a ratio it is 3:2."},
        price_keys={"default": IMAGE_MODEL}, display_name="GPT Image")]
