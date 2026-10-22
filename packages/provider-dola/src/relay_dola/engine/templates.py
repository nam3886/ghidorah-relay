"""dola's image catalog: the native "Templates" gallery and the style/ratio previews, read from `samantha/skill/pack`.

A template is a ready-made PROMPT (poster, product shot, app mockup...) with an example image; applying it is a plain
text-to-image run with its `prompt`, so nothing here drives the gallery UI. The catalog is global (not per user).

The worker's `refresh_catalog` command calls `build_snapshots` and the core
stores the result as the `templates` and `styles` snapshots; the API reads them and never borrows a
cookie. Nothing in a snapshot is a credential (the preview links are signed CDN links).

Snapshot shapes (what `spec.plan()` and `spec.extras()` read; `catalog.py` documents the same):
    templates = {"categories": {"<category id>": [item, ...]},    # category 0 is the default gallery (index = position)
                 "names": {"<category id>": "name"}}
    item      = {"index", "category", "prompt", "show_value", "thumbnail_uri", "thumbnail_url", "width", "height"}
    styles    = {"styles": [{"value", "label", "image", "default"}, ...], "ratios": [{"value", "label", ...}, ...]}
"""

from relay_dola.engine.http import BASE, DolaHttp


class CatalogError(RuntimeError):
    """The catalog could not be read; the command fails and the old snapshot stays."""


# dola style id -> our short style name (the 13 styles; matched one to one against `meta.option_list`, 260919)
STYLE_MAP = {
    "skill_image_styles_portrait": "portrait",
    "skill_image_styles_landscape": "landscape",
    "skill_image_styles_japanese_anime": "anime",
    "skill_image_styles_3d": "3d",
    "skill_image_styles_cyberpunk": "cyberpunk",
    "skill_image_styles_oil_painting": "oil_painting",
    "skill_image_styles_watercolor": "watercolor",
    "skill_image_styles_flat_illustration": "flat_illustration",
    "skill_image_styles_children_illustration": "children_drawing",
    "skill_image_styles_pixel_style": "pixel",
    "skill_image_styles_colored_pencil": "colored_pencil",
    "skill_image_styles_ink_wash_painting": "ink_wash",
    "skill_image_styles_ink_print": "ink_print",
}


async def skill_pack(http: DolaHttp, category_id: int) -> dict:
    """One `samantha/skill/pack` call (skill_type 3): the `image` part of the answer."""
    try:
        resp = await http.post_chain("/samantha/skill/pack", {
            "skill_type": 3, "condition": {"image_condition": {"category_id": category_id}}}, BASE + "/chat")
        if resp.status_code != 200:
            raise CatalogError("HTTP %s from dola" % resp.status_code)
        body = resp.json()
    except CatalogError:
        raise
    except Exception as exc:   # noqa: BLE001
        raise CatalogError("the catalog could not be read: %s" % str(exc)[:100]) from exc
    return ((body or {}).get("data") or {}).get("image") or {}


def parse_options(image: dict) -> dict:
    """Styles and ratios WITH their preview images, from dola's own `meta.option_list` instead of a hand-made list.
    `value` of a style is our short name; a style dola added that we do not know is dropped (a wrong name is never
    sent)."""
    styles, ratios = [], []
    for group in ((image.get("meta") or {}).get("option_list") or []):
        for o in (group.get("options") or []):
            item = {"label": o.get("show_name") or "", "image": o.get("background_url") or "",
                    "default": bool(o.get("is_default"))}
            if group.get("value") == "style":
                short = STYLE_MAP.get(o.get("value"))
                if short:
                    styles.append({**item, "value": short})
            elif group.get("value") == "ratio":
                ratios.append({**item, "value": o.get("value") or ""})
    return {"styles": styles, "ratios": ratios}


def _category_name(cat, names: dict) -> str:
    """`category` is not uniform in dola's answer: null, an int id, or a list of {category_id, category_name}."""
    if isinstance(cat, int):
        return names.get(cat, "") or ""
    if isinstance(cat, list) and cat:
        first = cat[0]
        if isinstance(first, dict):
            return first.get("category_name") or names.get(first.get("category_id"), "") or ""
        return names.get(first, "") or ""
    return ""


def parse_templates(image: dict) -> list[dict]:
    """The templates of one category as items; the index is the position in dola's list (a prompt-less one is skipped
    but keeps its place, so an index never moves)."""
    names = {c.get("category_id"): c.get("category_name") for c in (image.get("meta") or {}).get("category_list", [])}
    raw = list((image.get("template") or {}).get("image_list") or []) + list(image.get("simple_template") or [])
    out = []
    for i, it in enumerate(raw):
        prompt = (it.get("prompt") or it.get("show_value") or "").strip()
        if not prompt:
            continue
        cover = it.get("default_image") or {}
        out.append({
            "index": i,
            "category": _category_name(it.get("category"), names),
            "prompt": prompt,
            "show_value": it.get("show_value") or prompt,
            "thumbnail_uri": cover.get("uri") or "",
            "thumbnail_url": cover.get("url") or "",    # a signed link with a very long life: browsers fetch it direct
            "width": cover.get("width") or 0,
            "height": cover.get("height") or 0,
        })
    return out


async def build_snapshots(http: DolaHttp) -> dict[str, dict]:
    """The `templates` and `styles` snapshots. The default category gives the styles and ratios too (one call); every
    other category the default one lists costs one more call."""
    default = await skill_pack(http, 0)
    categories = {"0": parse_templates(default)}
    names = {"0": "Featured"}
    for c in (default.get("meta") or {}).get("category_list", []):
        cid = c.get("category_id")
        if not isinstance(cid, int) or cid == 0:
            continue
        categories[str(cid)] = parse_templates(await skill_pack(http, cid))
        names[str(cid)] = c.get("category_name") or ""
    return {"templates": {"categories": categories, "names": names}, "styles": parse_options(default)}
