"""dola's prompt frames: the shipped defaults give, byte for byte, the text relay sent before frames existed.

The golden strings below were recorded from the code of commit 9cc7c3a (prompt + the `dola.video_prompt_hint` sentence +
`[marker]`, composed by `jobs.create.sent_text_of`) before any frame code was written. They are literals on purpose:
they must not be computed by the code under test. The check then runs through the real renderer and the seeded
defaults."""

import unicodedata
import uuid

import pytest
from kit_a import plan

from relay_core import frames
from relay_core.api.doors import checked_prompt
from relay_core.api.errors_http import ApiError
from relay_core.jobs.create import marker_for
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC

SPEC_FILE = "packages/provider-dola/src/relay_dola/spec.py"
FRAMES_FILE = "packages/relay-core/src/relay_core/frames.py"

HINT = ("Generate it now as a single video in one go — do not ask questions, do not offer options, and do not split it "
        "into segments. Always reply to me in English; keep any dialogue and on-screen text in the video exactly as "
        "written in the prompt.")

JOB_VIDEO = uuid.UUID("00000000-0000-4000-8000-000000000001")
JOB_MULTI = uuid.UUID("00000000-0000-4000-8000-000000000002")
JOB_IMAGE = uuid.UUID("00000000-0000-4000-8000-000000000003")

PLAIN = "a calm sea at dusk"
MULTI = "  a lantern\n\tfloats over a lake,\r\n  été — 日本語  \n"
CAT = "a cat on a sofa"

GOLDEN = {
    "video plain": (JOB_VIDEO, "seedance-2.5", PLAIN, "7908", "a calm sea at dusk " + HINT + " [7908]"),
    "video multi-line, tabbed, non-ASCII": (JOB_MULTI, "seedance-2.5", MULTI, "3326",
                                            "a lantern floats over a lake, été — 日本語 " + HINT + " [3326]"),
    "image plain": (JOB_IMAGE, "seedream-4.5", CAT, "3391", "a cat on a sofa [3391]"),
}


@mutation(SPEC_FILE, 'DEFAULT_VIDEO_FRAME = "{{client_prompt}} " + DEFAULT_VIDEO_HINT + " {{tracking_code}}"',
          'DEFAULT_VIDEO_FRAME = "{{client_prompt}} " + DEFAULT_VIDEO_HINT + "! {{tracking_code}}"',
          expect="the video default differs from the golden text")
def test_the_shipped_defaults_render_the_golden_texts_byte_for_byte():
    for name, (job_id, model, prompt, marker, golden) in GOLDEN.items():
        assert marker_for(job_id) == marker, name
        kind = plan(model, prompt).media_type
        got = frames.render(SPEC.frames.defaults[kind], prompt, marker)
        assert got == golden, "the video default differs from the golden text: %s\n%r\n%r" % (name, got, golden)


def test_the_shipped_defaults_pass_the_grammar_cover_the_catalog_and_assert_nothing_about_ownership():
    assert set(SPEC.frames.defaults) == {m.media_type for m in SPEC.catalog()} == {"video", "image"}
    for kind, text in SPEC.frames.defaults.items():
        assert frames.check_frame(text) == text, kind
        for word in ("ownership", "identity", "consent"):
            assert word not in text.lower(), "the default %s frame must assert no ownership, identity or consent" % kind
    assert "reply to me in english" in SPEC.frames.defaults["video"].lower(), (
        "the default video frame no longer asks dola to answer in English: replies in other languages are unreadable "
        "to the classifier and a length notice is never answered")
    assert SPEC.frames.defaults["image"] == "{{client_prompt}} {{tracking_code}}"
    assert SPEC.frames.max_sent_chars == 200_000 and SPEC.max_prompt_chars == 200_000


@mutation(SPEC_FILE, "    max_prompt_chars = PROMPT_MAX_CHARS", "    max_prompt_chars = 50_000",
          expect="dola's prompt cap")
@mutation("packages/provider-dola/src/relay_dola/constants.py", "FRAME_MAX_SENT_CHARS = 200_000 ",
          "FRAME_MAX_SENT_CHARS = 50_000 ", expect="the longest prompt the shipped")
def test_the_longest_prompt_dola_takes_with_the_shipped_frames_is_pinned():
    """A prompt of the cap passes the door; the rendered text (prompt + frame + marker) must fit `max_sent_chars`, so
    the longest prompt is the cap less what the frame adds. The two numbers are literals on purpose."""
    assert SPEC.max_prompt_chars == 200_000, "dola's prompt cap is the measured edge, 200,000"
    marker = marker_for(JOB_VIDEO)
    longest = {}
    for kind, text in SPEC.frames.defaults.items():
        n = SPEC.frames.max_sent_chars - (len(frames.render(text, "x", marker)) - 1)     # what the frame adds is fixed
        assert len(frames.render(text, "x" * n, marker)) == SPEC.frames.max_sent_chars
        assert len(frames.render(text, "x" * (n + 1), marker)) == SPEC.frames.max_sent_chars + 1
        longest[kind] = n
    assert longest == {"video": 199_753, "image": 199_993}, "the longest prompt the shipped frames allow: %r" % longest


@mutation("packages/provider-dola/src/relay_dola/constants.py", "PROMPT_MAX_CHARS = 200_000 ",
          "PROMPT_MAX_CHARS = 200_001 ", expect="dola refuses a prompt over its cap")
def test_the_door_takes_a_dola_prompt_of_200000_characters_and_refuses_one_more():
    assert checked_prompt("x" * 200_000, SPEC.max_prompt_chars) == "x" * 200_000
    with pytest.raises(ApiError) as refused:
        checked_prompt("x" * 200_001, SPEC.max_prompt_chars)
        pytest.fail("dola refuses a prompt over its cap: 200,001 characters went through")
    assert refused.value.code == "input.prompt_too_long"


@mutation(SPEC_FILE, '    return (SINGLE_VIDEO_WARNING,) if kind == "video" and "single video" not in text.casefold()',
          "    return () if True", expect="the single-video warning is dropped")
def test_a_video_frame_without_single_video_is_saved_with_one_warning_and_other_frames_are_not():
    warn = SPEC.frames.warnings
    asks = "{{client_prompt}} Make a SINGLE VIDEO please {{tracking_code}}"
    plain = "{{client_prompt}} please {{tracking_code}}"
    assert warn("video", asks) == (), "the single-video warning is dropped (it fired on a frame that asks)"
    assert len(warn("video", plain)) == 1 and "single video" in warn("video", plain)[0].lower(), \
        "the single-video warning is dropped"
    assert warn("image", plain) == () and warn("video", SPEC.frames.defaults["video"]) == ()


def test_a_frame_with_the_template_slot_is_refused_like_any_unknown_placeholder():
    with pytest.raises(frames.FrameError) as e:
        frames.check_frame("{{template_prompt}} {{client_prompt}} {{tracking_code}}")
    assert (e.value.code, e.value.param) == ("input.frame_placeholder", "{{template_prompt}}")


@mutation(FRAMES_FILE, "_ALWAYS = frozenset({0x034F, 0x115F, 0x1160})", "_ALWAYS = frozenset({0x115F, 0x1160})",
          expect="the core's invisible-character rule differs from the kit's")
def test_the_cores_invisible_character_rule_equals_the_kits_over_every_code_point():
    """The kit may not import the core (nor the core the kit): two copies of one rule, pinned equal here."""
    from relay_browser.text import _invisible
    wrong = [hex(cp) for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF
             and frames.is_invisible(chr(cp)) != _invisible(chr(cp))]
    assert not wrong, "the core's invisible-character rule differs from the kit's: %s" % wrong[:10]
    # the grammar folds with NFKC first: a Hangul filler is refused, a visible letter is not
    assert frames.has_invisible("a\u3164b") and frames.has_invisible("a\ufe0fb") and not frames.has_invisible("ab")
    assert unicodedata.normalize("NFKC", "\u3164") == "\u1160"


ALERTS_FILE = "packages/relay-core/src/relay_core/alerts.py"
PRIMES = '"\\u2032": "\'", "\\u2033": \'"\', "\\u00b4": "\'", "\\u0060": "\'",'
NO_PRIMES = ""


@mutation(ALERTS_FILE, PRIMES, NO_PRIMES, expect="the core's typographic map differs from the kit's")
def test_the_cores_typographic_map_equals_the_kits_over_every_code_point():
    """The kit may not import the core (nor the core the kit): two copies of one map, pinned equal here."""
    from relay_browser.text import _TYPOGRAPHIC
    from relay_core import alerts
    wrong = [hex(cp) for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF
             and chr(cp).translate(alerts.TYPOGRAPHIC) != chr(cp).translate(_TYPOGRAPHIC)]
    assert not wrong, "the core's typographic map differs from the kit's: %s" % wrong[:10]
