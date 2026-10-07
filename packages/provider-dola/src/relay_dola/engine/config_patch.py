"""Patch the action-bar config so dola's composer offers durations it does not offer by itself.

This is the core of the whole system, kept PURE on purpose: a JSON string in, a JSON string out. No browser, no
network, so the offline tests can prove it.

Why the RESPONSE and never the request: dola signs `a_bogus` over the whole request body, so changing one field of a
request is rejected. What the app RECEIVES can be changed: the app then builds its own body and signs it, so the
signature is valid.

Structural trap: the selector sits in JSON nested inside a JSON STRING (two layers), so the patch recurses into
strings too. A surface scan finds nothing.
"""

import json
from typing import Any

DURATION_SELECTOR_KEY = "video-duration"

# Parse a string only when it looks like it holds the config: not every string.
_NESTED_HINTS = (DURATION_SELECTOR_KEY, "option_list")

# The request whose RESPONSE carries the config (a CDP `Fetch` pattern over the URL; see site.py).
CONFIG_URL_PART = "action_bar_v3/get_item_conf"


def _make_option(option_list: list, seconds: int) -> dict:
    """A new option in the exact shape dola uses."""
    max_id = 0
    for opt in option_list:
        if isinstance(opt, dict):
            try:
                max_id = max(max_id, int(opt.get("id", 0)))
            except (TypeError, ValueError):
                pass
    return {"id": max_id + 1, "display_text": "%ds" % seconds, "message_text": "", "option_key": str(seconds)}


def _is_duration_selector(node: dict) -> bool:
    """Recognise the duration selector: by `key`, and, since dola may rename keys, by shape (every option a number of
    seconds and 5 and 10 among them). Wide recognition beats silently failing to patch and delivering 10 s videos."""
    if str(node.get("key")) == DURATION_SELECTOR_KEY:
        return True
    options = node.get("option_list")
    if not isinstance(options, list) or not options:
        return False
    keys = [str(o.get("option_key", "")) for o in options if isinstance(o, dict)]
    return bool(keys) and all(k.isdigit() for k in keys) and {"5", "10"} <= set(keys)


def _patch_node(node: Any, seconds: int) -> tuple[Any, bool]:
    """Patch recursively. Returns (new node, whether anything changed)."""
    changed = False

    if isinstance(node, list):
        out = []
        for item in node:
            new_item, item_changed = _patch_node(item, seconds)
            changed = changed or item_changed
            out.append(new_item)
        return out, changed

    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            new_value, value_changed = _patch_node(value, seconds)
            changed = changed or value_changed
            out[key] = new_value

        # The NEW composer (A/B per account, since 23/09): a slider instead of a list:
        # {"key": "video-duration", "lower_bound": 4, "upper_bound": 10, "tag_starting_duration": 11}. From 11 s up
        # it is a paid tier (own label, the click leads to a purchase page). Raise the ceiling to `seconds` and move
        # the paid mark past it. Measured 24/09: patched to 30, one send, a 30.08 s video. "Patched" is reported when
        # the slider CONTAINS `seconds` after the patch, even if nothing changed (7 s is already in 4-10): the caller
        # uses the flag to know the selector reached the UI.
        if str(out.get("key")) == DURATION_SELECTOR_KEY and "upper_bound" in out:
            try:
                lower, upper = int(out.get("lower_bound", 0)), int(out["upper_bound"])
            except (TypeError, ValueError):
                return out, changed
            if upper < seconds:
                out["upper_bound"] = seconds
            try:
                if int(out.get("tag_starting_duration", seconds + 1)) <= seconds:
                    out["tag_starting_duration"] = seconds + 1
            except (TypeError, ValueError):
                out["tag_starting_duration"] = seconds + 1
            return out, changed or lower <= seconds

        if _is_duration_selector(out) and isinstance(out.get("option_list"), list):
            options = out["option_list"]
            already = any(str(o.get("option_key")) == str(seconds) for o in options if isinstance(o, dict))
            if not already:
                # Insert right after the 10 s option so the dropdown stays ascending.
                after = next((i for i, o in enumerate(options)
                              if isinstance(o, dict) and str(o.get("option_key")) == "10"), len(options) - 1)
                options.insert(after + 1, _make_option(options, seconds))
                changed = True
        return out, changed

    if isinstance(node, str):
        # The string may be nested JSON: open it only when it shows a sign of the config.
        stripped = node.strip()
        if stripped.startswith(("{", "[")) and any(h in node for h in _NESTED_HINTS):
            try:
                inner = json.loads(node)
            except (ValueError, TypeError):
                return node, False
            new_inner, inner_changed = _patch_node(inner, seconds)
            if inner_changed:
                # Re-pack only on a real change: no byte edits for nothing.
                return json.dumps(new_inner, ensure_ascii=False), True
        return node, False

    return node, False


def patch_duration_options(config_json: str, seconds: int = 30) -> tuple[str, bool]:
    """Add the `seconds` level to the duration selector of the action-bar config.

    Returns (the patched JSON string, whether it patched). A second call does NOT add a duplicate. When no selector is
    found it returns the text unchanged and False: the caller MUST treat that as a failure and stop, not send on and
    deliver a 10 s video."""
    try:
        data = json.loads(config_json)
    except (ValueError, TypeError):
        return config_json, False
    patched, changed = _patch_node(data, seconds)
    if not changed:
        return config_json, False
    return json.dumps(patched, ensure_ascii=False), True


def find_duration_options(config_json: str) -> list:
    """The duration levels the config offers now: one list of option keys per selector. For tests and logs."""
    found: list = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if _is_duration_selector(node) and isinstance(node.get("option_list"), list):
                found.append([str(o.get("option_key")) for o in node["option_list"] if isinstance(o, dict)])
            for value in node.values():
                walk(value)
        elif isinstance(node, str):
            stripped = node.strip()
            if stripped.startswith(("{", "[")) and any(h in node for h in _NESTED_HINTS):
                try:
                    walk(json.loads(node))
                except (ValueError, TypeError):
                    pass

    try:
        walk(json.loads(config_json))
    except (ValueError, TypeError):
        pass
    return found
