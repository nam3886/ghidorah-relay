"""A prompt that contradicts the parameters is refused before any charge (coverage PCONF00)."""

import pytest

from relay_core.testing.mutation import mutation
from relay_dola.conflicts import CODE_DURATION, CODE_RATIO, find_conflict

CONFLICTS_FILE = "packages/provider-dola/src/relay_dola/conflicts.py"

# Verbatim start of the prompt of job 36389eee: it asked for 21:9 and 30 s.
THAT = ("Create a 30-second, 16:9 Vietnamese public-safety news video about motorcycle theft. "
        "At 3 seconds the thief appears. At 7 seconds he breaks the lock. The video should run "
        "30 seconds in total.")


def test_a_ratio_in_the_prompt_that_contradicts_the_parameter_is_a_conflict():
    found = find_conflict(THAT, "video", "21:9", 30)
    assert found and found[0] == CODE_RATIO == "input.prompt_ratio_conflict" and "16:9" in found[1], (
        "the prompt says 16:9 yet a 21:9 job got through: dola asks, picks another model (job 36389eee)")
    assert find_conflict(THAT, "video", "16:9", 30) is None, "scene marks 'At 3 seconds' were read as the video length"


@mutation(CONFLICTS_FILE, 'if kind == "video" and duration:', "if False:", expect="a 15 second job got through")
def test_a_whole_video_duration_in_the_prompt_that_contradicts_the_parameter_is_a_conflict():
    found = find_conflict(THAT, "video", "16:9", 15)
    assert found and found[0] == CODE_DURATION == "input.prompt_duration_conflict" and "30" in found[1], (
        "the prompt says a 30 second video and a 15 second job got through")
    for text, secs in (("Make a 15s video of a cat", 15), ("A clip lasting 10 seconds", 10),
                       ("It should be 20 seconds long", 20), ("Duration: 10s", 10),
                       ("Tạo video 15 giây về mèo", 15), ("Video dài 10 giây", 10)):
        found = find_conflict(text, "video", "16:9", 30)
        assert found and found[0] == CODE_DURATION and str(secs) in found[1], "%r not read as the whole length" % text


@mutation(CONFLICTS_FILE, 'if not _PART.search(m.group(2) or "")', "if True", expect="blocked wrongly")
def test_scene_timings_are_not_whole_video_durations():
    for text in ("A 3-second close-up of the hand, then a 2-second pause before the next shot.",
                 "0-3s: wide shot of the city; 3-7s: close-up; 7-10s: logo.",
                 "a 4-second shot in the video shows the street",
                 "Meeting at 10:30, then the video fades out.",
                 "A 3-second close-up shot of the video's hero, then a 2-second scene in the video"):
        assert find_conflict(text, "video", "16:9", 30) is None, "blocked wrongly: %r" % text


@mutation(CONFLICTS_FILE, "    if ratio:", "    if False:", expect="a 16:9 job got through")
def test_image_prompts_check_the_ratio_only_and_accept_an_unset_ratio():
    found = find_conflict("A 1:1 square poster of a lighthouse", "image", "16:9", None)
    assert found and found[0] == CODE_RATIO, "the image asked 16:9, the prompt says 1:1, and a 16:9 job got through"
    assert find_conflict("A 1:1 square poster, 10 seconds long", "image", "1:1", None) is None
    assert find_conflict("A 9:16 phone wallpaper", "image", None, None) is None, (
        "an image with no ratio (dola chooses) was blocked because the prompt holds a ratio")
    assert find_conflict("A 9:16 wallpaper, 10 seconds long", "image", "9:16", 30) is None, "an image has no length"


def test_the_ratio_is_judged_before_the_length_and_a_ratio_is_not_a_time():
    found = find_conflict("a 10-second 9:16 clip", "video", "16:9", 30)
    assert found and found[0] == CODE_RATIO, "one clear thing at a time: the ratio first"
    for text in ("meet at 10:30", "version 1:16:9 notes", "the 21:90 gauge"):
        assert find_conflict(text, "video", "16:9", 30) is None, "%r was read as a ratio" % text
    assert find_conflict("a 3:2 photo", "image", "1:1", None)[0] == CODE_RATIO
    assert find_conflict("a real prompt with no contradiction", "video", "16:9", 30) is None
    assert find_conflict("", "video", "16:9", 30) is None and find_conflict(None, "video", "16:9", 30) is None


@pytest.mark.parametrize("ratio", ["21:9", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "1:1"])
def test_every_ratio_dola_offers_is_recognised_in_a_prompt(ratio):
    other = "1:1" if ratio != "1:1" else "16:9"
    found = find_conflict("a film in %s format" % ratio, "video", other, 30)
    assert found and found[0] == CODE_RATIO and ratio in found[1]
