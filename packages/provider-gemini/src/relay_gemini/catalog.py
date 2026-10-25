"""gemini's model catalog: the image, video and music models clients send, what they accept, and the price lookup. Pure.

The video model is the web app's "Create video" tool (Gemini Omni, `omni_1p2_pro`): it needs an account whose plan
includes it (AI Pro measured 24/10; a free account answers "upgrade your subscription"). One 10 s clip with sound per
job, from text or from one image and text, landscape (16:9, 1280x720) or portrait (9:16, 720x1280, measured 25/10),
optionally in the style of a video gallery template (`options.template`).

The music model is the "Create music" tool (Lyria 3.5, `lyria_3_5_fullsong`, AI Pro, 25/10): one song with lyrics per
job, about 1 min ("short") or 3 min ("standard"), with vocals or instrumental, in a genre, or in the style of a music
gallery template. gemini answers an MP3, an MP4 (the cover art held still over the song) and the lyrics; the relay
sells the MP4, so a song is a job of the core's "video" kind and the core needs no audio kind for it.

The pro-tier image model is the image tool sent with the account's "Pro" mode (AI Pro): Google labels its images "Nano
Banana 2.1" (the default Flash-Lite mode draws "Nano Banana 2 Lite", the free plan's Auto "Nano Banana 2"; 25/10). Its
jobs take the `image_pro` pool, whose budget is 0 for a free account (spec `POOL_DEFAULTS`), so only a pro or ultra
account ever claims one.
"""

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_gemini.templates import MUSIC_TEMPLATES, VIDEO_TEMPLATES

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
# otAQ7b tool 185: 16 Landscape (the default), 17 Portrait. The web app names the ratio twice (captured 25/10): its
# option id at inner[55] and an orientation code in the message (`[9][6][0][3]`: 1 landscape, 2 portrait, from text or
# from an image alike). Sends with the option id and the ratio as text came back landscape (FINDINGS 7).
VIDEO_RATIO_IDS = {"16:9": 16, "9:16": 17}
VIDEO_ORIENTATION = {"16:9": 1, "9:16": 2}
VIDEO_RATIO = "16:9"
VIDEO_DURATION = 10                 # no length option; every measured clip was 10.0 s

MUSIC_MODEL = "gemini-music"
MUSIC_DISPLAY = "Gemini Music (Lyria 3.5)"
# otAQ7b tool 230's options (captured 25/10): each chosen one's id goes in inner[55] (length, vocals, genre, in that
# order), and the length and the vocals also as codes in the message (`[9][6][2]` = [length, vocals]). Measured: a
# "short" song 64 s, a "standard" one 178 s, one with no length chosen 180 s.
MUSIC_LENGTHS = {"short": (42, 4), "standard": (43, 5)}
MUSIC_VOCALS = {"on": (27, 2), "instrumental": (26, 1)}
MUSIC_GENRES = {"pop": 44, "hip-hop & rap": 45, "rock": 46, "k-pop": 40, "latin": 55, "electronic": 47, "r&b": 48,
                "country": 49, "afrobeats": 56, "reggae": 57, "jazz & blues": 50, "classical": 52, "folk": 54,
                "lo-fi": 51, "acoustic": 53, "cinematic": 36, "ambient": 68}


def price_key(model: str) -> str:
    """The option a job is (`gemini.prices`, `gemini.costs`): an image or a song by its model (a short and a standard
    song took about the same units: 51 and 61), a video with its one length."""
    return "%s@%ds" % (model, VIDEO_DURATION) if model == VIDEO_MODEL else model


PRICE_KEYS = (IMAGE_MODEL, PRO_IMAGE_MODEL, price_key(VIDEO_MODEL), MUSIC_MODEL)
# What one job costs the account (the default of `gemini.costs`): one use of its image, video or music budget (a video
# takes about a fifth of a 5-hour window on AI Pro, 453 of 2400 units, a song 51-61; counted as one use for now).
DEFAULT_COSTS = {IMAGE_MODEL: 1, PRO_IMAGE_MODEL: 1, price_key(VIDEO_MODEL): 1, MUSIC_MODEL: 1}
# points (the default of `gemini.prices`); the song's 2 is a proposal the owner has not approved yet (25/10)
DEFAULT_PRICES = {IMAGE_MODEL: 1, PRO_IMAGE_MODEL: 2, price_key(VIDEO_MODEL): 6, MUSIC_MODEL: 2}


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing (the core's one rule)?"""
    return frames.shows_nothing(text)


def template_of(templates: dict, name) -> str | None:
    """The gallery's own spelling of a template name sent in any case; None when the gallery has no such template."""
    if not isinstance(name, str):
        return None
    return next((t for t in templates if t.lower() == name.strip().lower()), None)


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
                              "templates": list(VIDEO_TEMPLATES),
                              "description": "A 10 s 720p clip with sound, landscape or portrait, from a text prompt "
                                             "or from one image and a text, optionally in the style of a gallery "
                                             "template (`options.template`) (Gemini web, Gemini Omni); about 1-3 min."},
                      price_keys={"%ds" % VIDEO_DURATION: price_key(VIDEO_MODEL)}, display_name=VIDEO_DISPLAY),
            ModelSpec(MUSIC_MODEL, "video", ("text_to_music",),
                      limits={"max_images": 0, "lengths": list(MUSIC_LENGTHS), "vocals": list(MUSIC_VOCALS),
                              "genres": list(MUSIC_GENRES), "templates": list(MUSIC_TEMPLATES),
                              "description": "A song with lyrics from a text prompt, as an MP4 of its cover art over "
                                             "the song; `options.length` (short, about 1 min, or standard, about "
                                             "3 min), `options.vocals`, `options.genre` and `options.template` are "
                                             "each optional: gemini picks (Gemini web, Lyria 3.5); about 1-1.5 min."},
                      price_keys={"default": MUSIC_MODEL}, display_name=MUSIC_DISPLAY)]
