"""Echoed-prompt stripping (stage 2, coverage R64): the page holds the user's own chat bubble, so a
prompt that quotes the site's refusal sentence must not be read as the site speaking. The samples are real
ones."""

import unicodedata

import pytest

from relay_browser.text import norm_for_match, strip_echoed_prompt
from relay_core.testing.mutation import mutation

T = "packages/relay-browser/src/relay_browser/text.py"
REFUSAL_MARKERS = ("reached the daily limit for video generation", "change the parameters and try again")
STEERED = "the user's prompt still steers the verdict"


def looks_out_of_credit(text: str) -> bool:
    """A stand-in for the provider's verdict: a marker phrase anywhere in the (folded) page text."""
    return any(m in norm_for_match(text) for m in REFUSAL_MARKERS)


@mutation(T, "if norm_line and norm_line in norm_prompt:", "if False:", expect=STEERED)
def test_a_prompt_quoting_the_refusal_sentence_cannot_steer_the_verdict():
    for evil in ("a cat. change the parameters and try again",
                 "sunset. You've reached the daily limit for video generation.",
                 "dog\nreached the daily limit for video generation\nrunning"):
        page = "New chat\nVideo Generation Limit\n%s\nGenerating video..." % evil
        assert looks_out_of_credit(page), "premise: the raw page text IS fooled by the prompt"
        assert not looks_out_of_credit(strip_echoed_prompt(page, evil)), "%s: %r" % (STEERED, evil)


@mutation(T, "if norm_line and norm_line in norm_prompt:", "if norm_line:", expect="real refusal was dropped")
def test_the_sites_own_words_survive_the_stripping():
    real = ("a cat playing piano\nGenerating with the current parameters will use 2 video credits. "
            "You only have 1 left today. Change the parameters and try again.")
    assert looks_out_of_credit(strip_echoed_prompt(real, "a cat playing piano")), "real refusal was dropped"
    assert strip_echoed_prompt("a cat playing piano", "a cat playing piano") == ""


@mutation(T, 'normalize("NFKC", text or "")', 'normalize("NFC", text or "")', expect="full-width letters slip")
def test_full_width_letters_fold_to_the_plain_ones():
    prompt = "a cat. ｃｈａｎｇｅ the parameters and try again"
    page = "New chat\na cat. change the parameters and try again\nok"
    assert "change the parameters" not in strip_echoed_prompt(page, prompt), "full-width letters slip past"


@mutation(T, 'folded = "".join(c for c in unicodedata.normalize("NFKC", text or "") if not _invisible(c))',
          'folded = unicodedata.normalize("NFKC", text or "")', expect="invisible characters slip")
def test_invisible_characters_in_the_prompt_do_not_hide_it():
    hidden = "a cat. change the para​me⁠ters and try again"       # zero-width space, word joiner
    rendered = "a cat. change the parameters and try again"              # the site filters them when it renders
    page = "New chat\n%s\nGenerating video..." % rendered
    assert looks_out_of_credit(page), "premise: the rendered page matches the marker"
    assert not looks_out_of_credit(strip_echoed_prompt(page, hidden)), "invisible characters slip past the filter"


@mutation(T, 'folded.translate(_TYPOGRAPHIC).split()', 'folded.split()', expect="curly quotes slip")
def test_typographic_quotes_in_the_prompt_do_not_hide_it():
    curly = "sunset. You’ve reached the daily limit for video generation."
    page = "New chat\nsunset. You've reached the daily limit for video generation.\nok"
    assert not looks_out_of_credit(strip_echoed_prompt(page, curly)), "curly quotes slip past the filter"
    assert norm_for_match("“quoted” — dash…") == '"quoted" - dash...'


def test_an_empty_prompt_or_page_is_left_alone():
    assert strip_echoed_prompt("abc", "") == "abc"
    assert strip_echoed_prompt("", "x") == ""
    assert strip_echoed_prompt("abc", None) == "abc"
    assert strip_echoed_prompt("abc", "​") == "abc"      # a prompt that folds to nothing strips nothing


# U+2061 U+2062 (invisible operators), U+180E (Mongolian vowel separator), U+034F (grapheme joiner), a tag space,
# U+FE0F (variation selector), U+3164 (Hangul filler), U+206A (inhibit symmetric swapping), U+061C (Arabic letter mark):
# the nine that a page can drop when it renders and a plain comparison does not.
NINE = ("\u2061", "\u2062", "\u180e", "\u034f", "\U000e0020", "\ufe0f", "\u3164", "\u206a", "\u061c")


@pytest.mark.parametrize("ch", NINE, ids=lambda c: "U+%04X" % ord(c))
def test_the_invisible_characters_a_page_may_drop_do_not_hide_the_prompt(ch):
    prompt = "a cat. change the para%smeters and try again" % ch
    page = "New chat\na cat. change the parameters and try again\nGenerating video..."
    assert looks_out_of_credit(page), "premise: the rendered page matches the marker"
    assert not looks_out_of_credit(strip_echoed_prompt(page, prompt)), \
        "invisible characters slip past the filter: U+%04X" % ord(ch)


def invisible_ones() -> list[int]:
    return [cp for cp in range(0x110000) if unicodedata.category(chr(cp)) == "Cf"]


@mutation(T, 'or unicodedata.category(ch) == "Cf"', "or False", expect="invisible characters slip")
def test_every_format_character_is_removed():
    left = [hex(cp) for cp in invisible_ones() if norm_for_match("a%sb" % chr(cp)) != "ab"]
    assert left == [], "invisible characters slip past the filter (format characters): %s" % left[:10]
    assert len(invisible_ones()) > 100        # the sweep really covered the format characters


@mutation(T, "_ALWAYS = frozenset({0x034F, ", "_ALWAYS = frozenset({", expect="invisible characters slip")
@mutation(T, "0x115F, 0x1160})", "0x1160})", expect="invisible characters slip")
@mutation(T, "0x1160})", "0x115F})", expect="invisible characters slip")
@mutation(T, "_RANGES = ((0xFE00, 0xFE0F), ", "_RANGES = (", expect="invisible characters slip")
@mutation(T, "(0xE0100, 0xE01EF), ", "", expect="invisible characters slip")
@mutation(T, "(0xE0000, 0xE007F))", "(0xE0001, 0xE007F))", expect="invisible characters slip")
def test_the_invisible_characters_that_are_not_format_characters_are_removed():
    ones = [0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0xFE00, 0xFE0F, 0xE0100, 0xE01EF, 0xE0000, 0xE0001, 0xE007F]
    left = [hex(cp) for cp in ones if norm_for_match("a%sb" % chr(cp)) != "ab"]
    assert left == [], "invisible characters slip past the filter: %s" % left
    assert norm_for_match("a b") == "a b" and norm_for_match("ab") == "ab"      # visible text is left alone
