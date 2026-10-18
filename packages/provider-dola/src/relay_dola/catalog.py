"""dola's model catalog: the names clients send, the parameters they stand for, prices and snapshot readers.

Port of dola_v4 `core/models.py` (limits) and `core/model_resolver.py` (name grammar). Pure.

Names carry parameters (contracts 5.4.1): video `base[-Ns][-W:H]`, image `base[-W:H][-style]`, matched on the
longest base prefix. The internal (canonical) names `seedance_v2.5` and the like resolve too, so one path serves both
spellings. `public_name()` is the inverse of `resolve()`.
"""

import re
from dataclasses import dataclass
from typing import Any

from relay_core import frames
from relay_core.providers import ModelSpec
from relay_dola.constants import FALLBACK_IMAGE_PRICE, FALLBACK_VIDEO_PRICE

VALID_MODELS = ("seedance_v2.5", "seedance_v2.0", "seedance_v1.0")
DEFAULT_MODEL = "seedance_v2.5"
MODEL_2_0_MAX_SECONDS = 15   # 2.0 is always cut to 15 s by dola

# Valid lengths PER MODEL. dola itself offers 5 s and 10 s; every other level is a configuration we inject, so each
# one is listed only after it was MEASURED on the delivered file (ffprobe, 19-20/09):
#   2.5 @30s -> 30.08 s (n=7); 2.5 @20s -> 20.06 s; 2.5 @25s -> 25.06 s (2 credits each, the price of 30 s)
#   2.0 @15s -> 15.10 s (n=2)
#   1.0 @15s -> 10.04 s (n=1): NOT offered, dola cuts it to 10 s and the customer would pay for 15
#   2.5 @60s -> 30.08 s: never offered, dola cuts it to 30 s
#   2.5 @15s: dola quotes 6-30 credits for it, per account, against 4 credits a day: no order could run
DURATIONS_BY_MODEL = {
    "seedance_v2.5": (5, 10, 20, 25, 30),
    "seedance_v2.0": (5, 10, 15),
    "seedance_v1.0": (5, 10),
}
VALID_DURATIONS = tuple(sorted({d for ds in DURATIONS_BY_MODEL.values() for d in ds}))
VALID_RATIOS = ("1:1", "3:4", "4:3", "9:16", "16:9", "21:9")     # the six dola offers for video (UI, 11/09)
DEFAULT_RATIO = "16:9"                                              # the video comes out 1280x720

VALID_IMAGE_MODELS = ("seedream-4.5", "seedream-5.0-pro")
DEFAULT_IMAGE_MODEL = "seedream-4.5"
VALID_IMAGE_RATIOS = ("1:1", "2:3", "3:4", "4:3", "9:16", "16:9")   # images have 2:3 and no 21:9
VALID_STYLES = ("portrait", "landscape", "anime", "3d", "cyberpunk", "oil_painting",
                "watercolor", "flat_illustration", "children_drawing", "pixel",
                "colored_pencil", "ink_wash", "ink_print")

# What dola's page calls them (composer chips) and what dola says about itself in a reply.
MODEL_LABELS = {
    "seedance_v2.5": "Dreamina Seedance 2.5",
    "seedance_v2.0": "Dreamina Seedance 2.0 Fast",
    "seedance_v1.0": "Dreamina Seedance 1.0",
}
MODEL_VERSION = {"seedance_v2.5": "2.5", "seedance_v2.0": "2.0", "seedance_v1.0": "1.0"}   # chips show only the number
IMAGE_MODEL_LABELS = {"seedream-4.5": "Seedream 4.5", "seedream-5.0-pro": "Seedream 5.0 Pro"}
IMAGE_STYLE_LABELS = {
    "portrait": "Portraits", "landscape": "Landscape", "anime": "Anime", "3d": "3D",
    "cyberpunk": "cyberpunk", "oil_painting": "Oil Painting", "watercolor": "Watercolor",
    "flat_illustration": "Flat Illustration", "children_drawing": "Children's Drawing",
    "pixel": "Pixel", "colored_pencil": "Colored Pencil", "ink_wash": "Ink Wash", "ink_print": "Ink Print",
}

# Measured prices in points (ffprobe + the number dola states, 20/09, >= 3 samples a tier, 0 conflicts). The default
# of `dola.prices`; an operator may change it. 10 s costs twice 30 s: measured, not a typo. 5.0 Pro is 4 (user, 24/09):
# the new UI says "4x credit usage". The account's own image wallet still spends 1 a job (`IMAGE_ACCOUNT_COST`).
DEFAULT_PRICES = {
    "seedance_v1.0@5s": 1, "seedance_v1.0@10s": 2,
    "seedance_v2.0@5s": 1, "seedance_v2.0@10s": 2, "seedance_v2.0@15s": 3,
    "seedance_v2.5@5s": 2, "seedance_v2.5@10s": 4,
    "seedance_v2.5@20s": 2, "seedance_v2.5@25s": 2, "seedance_v2.5@30s": 2,
    "seedream-4.5": 1, "seedream-5.0-pro": 4,
}

# Public base name -> internal model id. The canonical names resolve too, so one resolver serves both spellings: a
# separate path for them once checked the duration under the name the customer sent, an alias missed the table and a
# level the model cannot deliver got through.
_VIDEO_BASE = {
    "seedance-2.5": "seedance_v2.5", "seedance-2.0": "seedance_v2.0", "seedance-1.0": "seedance_v1.0",
    "seedance_v2.5": "seedance_v2.5", "seedance_v2.0": "seedance_v2.0", "seedance_v1.0": "seedance_v1.0",
}
_VIDEO_PUBLIC = ("seedance-2.5", "seedance-2.0", "seedance-1.0")   # listed in the catalog (canonical names are aliases)
_IMAGE_BASE = {"seedream-5.0-pro": "seedream-5.0-pro", "seedream-4.5": "seedream-4.5"}

_DUR_RE = re.compile(r"^(\d+)s$")
_RATIO_RE = re.compile(r"^(\d+):(\d+)$")
_TPL_RE = re.compile(r"^t\d+$")  # the retired `-tN` suffix: refused by name, never read as a parameter


# Platform codes a name can be refused with.
E_BAD_MODEL, E_BAD_DURATION, E_BAD_RATIO, E_BAD_STYLE = (
    "input.bad_model", "input.bad_duration", "input.bad_ratio", "input.bad_style")


class ModelNameError(ValueError):
    """A model name or a parameter that cannot be served; `code` is the platform code to refuse with."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class GenSpec:
    kind: str                          # "video" | "image"
    model: str                         # internal model id
    duration: int | None = None        # video
    ratio: str | None = None
    style: str | None = None           # image


def is_blank_prompt(text: str) -> bool:
    """Does the prompt show nothing: only white space and characters that draw nothing (the core's one rule, NFKC
    first, as the page folds)? A prompt like that is sent as the bare marker, which any conversation of the account
    could be taken for."""
    return frames.shows_nothing(text)


def _match_base(name: str, table: dict) -> str | None:
    """The longest base that is `name` or a prefix of it followed by '-'."""
    best = None
    for base in table:
        if name == base or name.startswith(base + "-"):
            if best is None or len(base) > len(best):
                best = base
    return best


def _suffix_tokens(name: str, base: str) -> list[str]:
    rest = name[len(base):].lstrip("-")
    return [t for t in rest.split("-") if t] if rest else []


def canonical_model(name: str) -> str:
    """The internal id of a model name; an unknown name comes back as it is."""
    try:
        return resolve(name).model
    except ModelNameError:
        return name


def resolve(name: str) -> GenSpec:
    """A model name -> its parameters. `ModelNameError` (with the platform code) for a bad name or parameter."""
    name = (name or "").strip()
    if not name:
        raise ModelNameError(E_BAD_MODEL, "model is required")
    vbase = _match_base(name, _VIDEO_BASE)
    ibase = _match_base(name, _IMAGE_BASE)
    if vbase and (ibase is None or len(vbase) >= len(ibase)):
        return _resolve_video(name, vbase)
    if ibase:
        return _resolve_image(name, ibase)
    raise ModelNameError(E_BAD_MODEL, "unknown model %r (see GET /v1/models)" % name)


def _resolve_video(name: str, base: str) -> GenSpec:
    # The default is the LONGEST length the model offers (a fixed 30 s once made `seedance-2.0` fail unless the
    # customer added `-15s`).
    model_id = _VIDEO_BASE[base]
    allowed = DURATIONS_BY_MODEL[model_id]
    spec = GenSpec(kind="video", model=model_id, duration=max(allowed), ratio=DEFAULT_RATIO)
    for tok in _suffix_tokens(name, base):
        md, mr = _DUR_RE.match(tok), _RATIO_RE.match(tok)
        if md:
            spec.duration = int(md.group(1))
        elif mr:
            spec.ratio = tok
        else:
            raise ModelNameError(E_BAD_MODEL, "unknown suffix %r in video model %r" % (tok, name))
    if spec.duration not in allowed:
        raise ModelNameError(E_BAD_DURATION, "%s does not support %ds (allowed: %s)"
                             % (spec.model, spec.duration, allowed))
    if spec.ratio not in VALID_RATIOS:
        raise ModelNameError(E_BAD_RATIO, "unsupported ratio %r (allowed: %s)" % (spec.ratio, VALID_RATIOS))
    return spec


def _resolve_image(name: str, base: str) -> GenSpec:
    spec = GenSpec(kind="image", model=_IMAGE_BASE[base])
    for tok in _suffix_tokens(name, base):
        mr = _RATIO_RE.match(tok)
        if _TPL_RE.match(tok):
            raise ModelNameError(E_BAD_MODEL, "the %r template suffix is gone: templates are chosen in the UI, send "
                                 "the template's prompt as `prompt` (see GET /v1/providers/dola/options)" % tok)
        if mr:
            if tok not in VALID_IMAGE_RATIOS:
                raise ModelNameError(E_BAD_RATIO,
                                     "unsupported image ratio %r (allowed: %s)" % (tok, VALID_IMAGE_RATIOS))
            spec.ratio = tok
        elif tok in VALID_STYLES:
            spec.style = tok
        else:
            raise ModelNameError(E_BAD_STYLE, "unknown suffix %r in image model %r (style must be one of %s)"
                                 % (tok, name, VALID_STYLES))
    return spec


def carries_parameters(name: str) -> bool:
    """True when the name holds suffix parameters (`seedance-2.5-30s`), False for a bare model name or an unknown
    name. The native door refuses the first kind: its parameters have their own fields."""
    name = (name or "").strip()
    for table in (_VIDEO_BASE, _IMAGE_BASE):
        base = _match_base(name, table)
        if base and _suffix_tokens(name, base):
            return True
    return False


def public_name(kind: str, model: str, duration: int | None = None, ratio: str | None = None,
                style: str | None = None) -> str:
    """The full public name of a job's parameters: the inverse of `resolve`. `seedance_v2.5` + 30 s + 21:9 ->
    `seedance-2.5-30s-21:9`. An unknown model comes back as it is."""
    if kind == "video":
        pub = next((p for p in _VIDEO_PUBLIC if _VIDEO_BASE.get(p) == model), None)
        if pub is None:
            return model or ""
        return "-".join([pub] + (["%ds" % duration] if duration else []) + ([ratio] if ratio else []))
    if model in _IMAGE_BASE.values():
        return "-".join([model] + [x for x in (ratio, style) if x])
    return model or ""


def video_name_params(name: str) -> tuple[str, int | None, str | None]:
    """(public base name, length, ratio) WRITTEN in a video model name; None for what it does not state. A name that
    is not a video model comes back as (name, None, None)."""
    name = (name or "").strip()
    base = _match_base(name, _VIDEO_BASE)
    if not base:
        return name, None, None
    duration = ratio = None
    for tok in _suffix_tokens(name, base):
        if _DUR_RE.match(tok):
            duration = int(_DUR_RE.match(tok).group(1))
        elif _RATIO_RE.match(tok):
            ratio = tok
    public = next((p for p in _VIDEO_PUBLIC if _VIDEO_BASE[p] == _VIDEO_BASE[base]), base)
    return public, duration, ratio


def video_warnings(model: str, duration: int | None) -> list[str]:
    """Notes that do not block an order. Seedance 2.0 and 1.0 at their longest length are cut by dola."""
    if model == "seedance_v2.0" and duration == MODEL_2_0_MAX_SECONDS:
        return ["Seedance 2.0 output is capped at ~%ds by dola." % MODEL_2_0_MAX_SECONDS]
    return []


# ---- prices -------------------------------------------------------------------------------------------------------
def price_key(model: str, kind: str, duration: int | None) -> str:
    """Price table key: video has a length (`model@30s`), an image is priced per JOB (`model`)."""
    return model if kind == "image" else "%s@%ds" % (model, int(duration or 0))


def lookup_price(table: dict[str, int] | None, model: str, kind: str, duration: int | None) -> tuple[int, str | None]:
    """(points, None) from the table, or (fallback, tier) when the tier is not in it: the caller logs the tier and
    warns. Falling back silently would charge wrongly with nobody seeing it. The fallback is high for video (the
    dearest plain tier measured; guessing low would let a customer make more than they paid for) and 1 for images."""
    tier = price_key(model, kind, duration)
    table = table or {}
    if tier in table:
        return int(table[tier]), None
    return (FALLBACK_IMAGE_PRICE if kind == "image" else FALLBACK_VIDEO_PRICE), tier


# ---- the catalog served to clients ---------------------------------------------------------------------------------
_DESCRIPTIONS = {
    "seedance_v2.5": "Newest video model: best quality, clips up to 30 seconds.",
    "seedance_v2.0": "Faster video model: clips up to 15 seconds.",
    "seedance_v1.0": "Original video model: clips up to 10 seconds.",
    "seedream-5.0-pro": "Highest-quality images: 4 images per run.",
    "seedream-4.5": "Everyday images: 4 images per run.",
}
# The names people see in the catalog, as the admin design draws them. Not MODEL_LABELS / IMAGE_MODEL_LABELS on
# purpose: those are what the browser clicks on dola's page and change when dola renames a chip.
DISPLAY_NAMES = {
    "seedance_v2.5": "Dreamina Seedance 2.5",
    "seedance_v2.0": "Seedance 2.0 Fast",
    "seedance_v1.0": "Seedance 1.0",
    "seedream-4.5": "Seedream 4.5",
    "seedream-5.0-pro": "Seedream 5.0 Pro",
}
VIDEO_OPS = ("text_to_video", "image_to_video")
IMAGE_OPS = ("text_to_image", "image_to_image")


def catalog() -> list[ModelSpec]:
    """The public models: ids are the hyphen names, the canonical names are aliases. Variants (`-30s`, `-anime`) are
    not listed: the core resolves a name that extends a listed one and `plan()` judges the suffix."""
    out = []
    for pub in _VIDEO_PUBLIC:
        model = _VIDEO_BASE[pub]
        allowed = DURATIONS_BY_MODEL[model]
        out.append(ModelSpec(
            pub, "video", VIDEO_OPS, aliases=(model,),
            limits={"durations": list(allowed), "ratios": list(VALID_RATIOS), "default_duration": max(allowed),
                    "default_ratio": DEFAULT_RATIO, "max_images": 10, "description": _DESCRIPTIONS[model]},
            prices={"%ds" % d: DEFAULT_PRICES[price_key(model, "video", d)] for d in allowed},
            display_name=DISPLAY_NAMES[model]))
    for model in _IMAGE_BASE:
        out.append(ModelSpec(
            model, "image", IMAGE_OPS,
            limits={"ratios": list(VALID_IMAGE_RATIOS), "styles": list(VALID_STYLES), "max_images": 10,
                    "templates": True, "description": _DESCRIPTIONS[model]},
            prices={"default": DEFAULT_PRICES[model]}, display_name=DISPLAY_NAMES[model]))
    return out


# ---- snapshots (refreshed by the worker's `refresh_catalog`, read here) ---------------------------------------------
# `templates`: {"categories": {"<category id>": [{"index", "category", "prompt", "show_value", "thumbnail_uri",
#                "thumbnail_url", "width", "height"}, ...]}}   (category 0 is the default gallery)
# `styles`:    {"styles": [{"label", "image", "default", "value"}], "ratios": [{"label", "image", "default", "value"}]}
def usable_link(url: Any) -> str:
    """A preview link as the options endpoint may serve it. The core stores snapshots through its redactor, which blanks
    the signature and long path parts of a signed CDN link: the stored link is then dead, and a dead link is "" (the
    client shows no preview) rather than a broken image. Needs a core change to keep the live form (contracts v2)."""
    return url if isinstance(url, str) and "[redacted]" not in url else ""


def template_list(snapshot: Any, category: int = 0) -> list[dict]:
    cats = snapshot.get("categories") if isinstance(snapshot, dict) else None
    items = (cats or {}).get(str(category)) or []
    return [{"index": t["index"], "category": t.get("category", ""), "prompt": t["prompt"],
             "show_value": t.get("show_value", t["prompt"]), "thumbnail_url": usable_link(t.get("thumbnail_url")),
             "width": t.get("width", 0), "height": t.get("height", 0)}
            for t in items if isinstance(t, dict) and "index" in t and t.get("prompt")]


def extras(snapshots: dict[str, Any]) -> dict:
    """What `/v1/providers/dola/options` serves: the templates and the styles/ratios with their preview images (a
    list is empty until the worker has read dola once). Never holds a credential."""
    styles = snapshots.get("styles") if isinstance(snapshots.get("styles"), dict) else {}
    return {"templates": template_list(snapshots.get("templates")),
            "styles": _with_usable_images(styles.get("styles")), "ratios": _with_usable_images(styles.get("ratios"))}


def _with_usable_images(options: Any) -> list[dict]:
    return [{**o, "image": usable_link(o.get("image"))} for o in (options or []) if isinstance(o, dict)]

