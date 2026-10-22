"""The model catalog and its name grammar (coverage MRES00, TPL00; contracts 5.4.1)."""

import pytest
from kit_a import TEMPLATES, refused

from relay_core.providers import ProviderRegistry
from relay_core.testing.mutation import mutation
from relay_dola import catalog as C
from relay_dola.spec import SPEC

CATALOG_FILE = "packages/provider-dola/src/relay_dola/catalog.py"
FRAMES_FILE = "packages/relay-core/src/relay_core/frames.py"


def spec(kind, model, duration=None, ratio=None, style=None):
    return C.GenSpec(kind, model, duration, ratio, style)


@mutation(CATALOG_FILE, '+ ([ratio] if ratio else []))', '+ [])',
          expect="reads back as")
def test_a_public_name_reads_back_as_the_same_parameters():
    # The dashboard shows this name; calling the API with it must make the job it names.
    for s in (spec("video", "seedance_v2.5", 30, "21:9"), spec("video", "seedance_v2.0", 15, "9:16"),
              spec("image", "seedream-4.5", ratio="1:1", style="anime"), spec("image", "seedream-5.0-pro")):
        name = C.public_name(s.kind, s.model, s.duration, s.ratio, s.style)
        assert C.resolve(name) == s, "%r reads back as %r, not %r" % (name, C.resolve(name), s)
    assert C.public_name("video", "seedance_v2.5", 30, "21:9") == "seedance-2.5-30s-21:9"
    assert C.public_name("video", "unknown_model", 5, "1:1") == "unknown_model"


@mutation(CATALOG_FILE, "duration=max(allowed), ratio=DEFAULT_RATIO", "duration=min(allowed), ratio=DEFAULT_RATIO",
          expect="the default is the longest length")
def test_the_default_is_the_longest_length_and_16_9():
    v = C.resolve("seedance-2.5-30s-16:9")
    assert v.kind == "video" and v.model == "seedance_v2.5" and v.duration == 30 and v.ratio == "16:9", v
    for name, longest in (("seedance-2.5", 30), ("seedance-2.0", 15), ("seedance-1.0", 10)):
        assert C.resolve(name).duration == longest, "the default is the longest length"
        assert C.resolve(name).ratio == "16:9"
    assert C.resolve("seedance-2.0-15s").duration == 15


def test_image_names_carry_ratio_and_style():
    i = C.resolve("seedream-4.5-1:1-anime")
    assert (i.kind, i.model, i.ratio, i.style) == ("image", "seedream-4.5", "1:1", "anime")
    assert C.resolve("seedream-5.0-pro").model == "seedream-5.0-pro"
    all_of_it = C.resolve("seedream-4.5-9:16-ink_wash")
    assert (all_of_it.ratio, all_of_it.style) == ("9:16", "ink_wash")


@mutation(CATALOG_FILE, "        if _TPL_RE.match(tok):", "        if False:", expect="the retired template suffix")
def test_the_retired_template_suffix_is_refused_by_name_never_read_as_a_parameter():
    for name in ("seedream-4.5-t3", "seedream-4.5-9:16-ink_wash-t12", "seedance-2.5-t3"):
        with pytest.raises(C.ModelNameError) as caught:
            C.resolve(name)
        assert caught.value.code == "input.bad_model", "the retired template suffix %r: %s" % (name, caught.value.code)
    with pytest.raises(C.ModelNameError, match="template suffix is gone"):
        C.resolve("seedream-4.5-t3")
    e = refused("seedream-4.5-t3", door="facade")
    assert e.code == "input.bad_model" and "template" in e.detail


@mutation(CATALOG_FILE, "if best is None or len(base) > len(best):", "if best is None or len(base) < len(best):",
          expect="longest base prefix")
def test_the_longest_base_prefix_wins():
    # `seedream-5.0-pro-16:9` must not be read as a shorter base plus an unknown suffix.
    r = C.resolve("seedream-5.0-pro-16:9")
    assert r.model == "seedream-5.0-pro" and r.ratio == "16:9", "longest base prefix"
    # no real base is a prefix of another today, so the rule is proven on a table that has one
    assert C._match_base("a-b-c", {"a": 1, "a-b": 2}) == "a-b", "longest base prefix"
    assert C._match_base("a-bc", {"a": 1, "a-b": 2}) == "a", "a prefix must end at a hyphen"


@pytest.mark.parametrize("bad, code", [
    ("", "input.bad_model"), ("nope", "input.bad_model"), ("seedance-2.0-30s", "input.bad_duration"),
    ("seedance-2.5-7s", "input.bad_duration"), ("seedance-2.5-2:1", "input.bad_ratio"),
    ("seedance-2.5-fast", "input.bad_model"), ("seedream-4.5-badstyle", "input.bad_style"),
    ("seedream-4.5-21:9", "input.bad_ratio")])
def test_a_bad_name_or_parameter_is_refused_with_its_code(bad, code):
    with pytest.raises(C.ModelNameError) as e:
        C.resolve(bad)
    assert e.value.code == code


def test_the_canonical_names_resolve_to_the_same_spec_as_the_public_ones():
    for pub, canon in (("seedance-2.5", "seedance_v2.5"), ("seedance-2.0", "seedance_v2.0"),
                       ("seedance-1.0", "seedance_v1.0")):
        assert C.resolve(pub) == C.resolve(canon)
        assert C.resolve(canon + "-10s-1:1") == C.resolve(pub + "-10s-1:1")
    assert C.canonical_model("seedance-2.0") == "seedance_v2.0" and C.canonical_model("zzz") == "zzz"


def test_video_name_params_reads_what_the_name_states():
    assert C.video_name_params("seedance-2.5-30s-16:9") == ("seedance-2.5", 30, "16:9")
    assert C.video_name_params("seedance_v2.0-5s") == ("seedance-2.0", 5, None)
    assert C.video_name_params("seedream-4.5") == ("seedream-4.5", None, None)


def test_carries_parameters_tells_a_suffix_from_a_bare_name():
    for bare in ("seedance-2.5", "seedance_v2.5", "seedream-5.0-pro", "unknown"):
        assert not C.carries_parameters(bare), bare
    for suffixed in ("seedance-2.5-30s", "seedance-2.5-1:1", "seedream-4.5-anime", "seedream-4.5-t3",
                     "seedream-5.0-pro-16:9"):
        assert C.carries_parameters(suffixed), suffixed


def test_the_catalog_lists_the_base_ids_and_registers():
    ids = {m.id: m for m in C.catalog()}
    assert set(ids) == {"seedance-2.5", "seedance-2.0", "seedance-1.0", "seedream-5.0-pro", "seedream-4.5"}
    assert ids["seedance-2.5"].aliases == ("seedance_v2.5",) and ids["seedance-2.5"].media_type == "video"
    assert ids["seedance-2.5"].limits["durations"] == [5, 10, 20, 25, 30]
    assert ids["seedance-2.0"].limits["durations"] == [5, 10, 15] and ids["seedance-1.0"].limits["durations"] == [5, 10]
    assert ids["seedream-4.5"].limits["styles"] == list(C.VALID_STYLES) and len(C.VALID_STYLES) == 13
    assert ids["seedance-2.5"].prices["30s"] == 2 and ids["seedance-2.5"].prices["10s"] == 4
    assert ids["seedream-5.0-pro"].prices == {"default": 4}
    registry = ProviderRegistry()
    # unique ids and aliases, settings in its namespace, the settings the core reads
    registry.register(SPEC)
    hit = registry.resolve("seedance_v2.5")
    assert hit is not None and hit[1].id == "seedance-2.5"


@mutation(CATALOG_FILE, '"seedance_v2.0": "Seedance 2.0 Fast",', '"seedance_v2.0": "Dreamina Seedance 2.0 Fast",',
          expect="the words the design draws")
@mutation(CATALOG_FILE, 'prices={"default": DEFAULT_PRICES[model]}, display_name=DISPLAY_NAMES[model]',
          'prices={"default": DEFAULT_PRICES[model]}', expect="every model has a display name")
@mutation(CATALOG_FILE, 'for d in allowed},\n            display_name=DISPLAY_NAMES[model]',
          'for d in allowed}', expect="every model has a display name")
def test_every_model_has_the_display_name_the_design_draws():
    # The display names follow the admin design; dola's page labels (MODEL_LABELS) are separate on purpose: the
    # browser clicks those, and they change when dola renames a chip.
    names = {m.id: m.display_name for m in C.catalog()}
    assert all(names.values()), "every model has a display name: %s" % names
    assert names == {"seedance-2.5": "Dreamina Seedance 2.5", "seedance-2.0": "Seedance 2.0 Fast",
                     "seedance-1.0": "Seedance 1.0", "seedream-4.5": "Seedream 4.5",
                     "seedream-5.0-pro": "Seedream 5.0 Pro"}, "the words the design draws: %s" % names


# ---- templates (TPL00) ----------------------------------------------------------------------------------------------
def test_the_template_list_is_served_from_the_snapshot():
    assert [t["index"] for t in C.template_list(TEMPLATES)] == [0, 3]
    assert C.template_list(None) == [] and C.template_list({"categories": {}}) == []


def test_extras_serve_the_snapshots_and_are_empty_without_them():
    assert C.extras({}) == {"templates": [], "styles": [], "ratios": []}
    styles = {"styles": [{"label": "Anime", "image": "https://example.invalid/a.jpg", "default": False,
                          "value": "anime"}],
              "ratios": [{"label": "1:1", "image": "", "default": True, "value": "1:1"}]}
    out = SPEC.extras({"templates": TEMPLATES, "styles": styles})
    assert out["styles"][0]["value"] == "anime" and out["ratios"][0]["value"] == "1:1"
    assert out["templates"][0]["prompt"] == "hero section etched engraving"
    assert "cookie" not in str(out).lower()


def test_a_name_with_a_suffix_carries_parameters_and_a_bare_name_does_not():
    assert C.carries_parameters("seedance-2.5-30s") and C.carries_parameters("seedream-4.5-t3")
    assert not C.carries_parameters("seedream-4.5") and not C.carries_parameters("nope")


@mutation(CATALOG_FILE, 'return url if isinstance(url, str) and "[redacted]" not in url else ""',
          'return url if isinstance(url, str) else ""', expect="a dead preview link was served")
def test_a_preview_link_the_core_redacted_is_not_served_as_a_broken_link():
    """The core stores snapshots through its redactor, which blanks the signature of a signed CDN link."""
    from relay_core.redact import redact
    signed = ("https://p16.example.invalid/tos-x-i-abcdefghij/d37de81286584d70b6340f21150f328f~tplv-i.jpeg"
              "?x-expires=1&x-signature=AbCd%2B")
    templates = {"categories": {"0": [{**TEMPLATES["categories"]["0"][0], "thumbnail_url": signed}]}}
    styles = {"styles": [{"label": "Anime", "image": signed, "default": False, "value": "anime"}],
              "ratios": [{"label": "1:1", "image": "https://example.invalid/live.jpg", "default": True,
                          "value": "1:1"}]}
    stored = redact({"templates": templates, "styles": styles})
    assert "[redacted]" in str(stored), "the redactor no longer blanks signed links: vacuous test"
    out = SPEC.extras(stored)
    assert out["templates"][0]["thumbnail_url"] == "", "a dead preview link was served"
    assert out["templates"][0]["prompt"] == "hero section etched engraving"
    assert out["styles"][0]["image"] == "", "a dead preview link was served"
    assert out["styles"][0]["value"] == "anime"
    assert out["ratios"][0]["image"] == "https://example.invalid/live.jpg", "a live link must survive"


@mutation(FRAMES_FILE, 'return cp in _ALWAYS or unicodedata.category(ch) == "Cf" or any(',
          'return unicodedata.category(ch) == "Cf" or any(', expect="the pure fold left an invisible")
def test_the_pure_blank_check_agrees_with_the_browser_kits_fold_on_every_code_point():
    """`is_blank_prompt` is a pure twin of `norm_for_match` (the catalog cannot import the kit): a character the kit
    drops must count as blank here, and a visible one must not."""
    from relay_browser.text import norm_for_match
    wrong = [hex(cp) for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF
             and C.is_blank_prompt(chr(cp)) != (norm_for_match(chr(cp)) == "")]
    assert not wrong, "the pure fold left an invisible (or hid a visible one): %s" % wrong[:10]
    assert C.is_blank_prompt("") and C.is_blank_prompt("\u200b \n\t\u3164") and not C.is_blank_prompt("a\u200b")
