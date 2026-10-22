"""Refuse an order whose PROMPT says one thing and whose PARAMETERS say another, before any charge and before dola.

Pure.

Why refuse instead of fixing: dola reads BOTH. Job 36389eee asked for 21:9 while the prompt said "30-second, 16:9
...": dola asked "Which version do you want?", the pipeline pushed on, the assistant picked another model and the
customer got a video unlike what was paid for. Rewriting the customer's words is silent; an error lets them choose
(user, 24/09: refuse, for video and image alike).

Only what speaks of the WHOLE video counts, not scene timings. Script prompts are full of "at 7 seconds", "0-3s", "a
3-second close-up": those are editing beats, not a length, and refusing them would block exactly the most careful
prompts.
"""

import re

CODE_RATIO = "input.prompt_ratio_conflict"
CODE_DURATION = "input.prompt_duration_conflict"

# Every ratio dola offers, for video and image. Not next to another digit or colon: "1:16:9" and "10:30" (a time)
# are not ratios.
_RATIO = re.compile(r"(?<![\d.:])(21:9|16:9|9:16|4:3|3:4|3:2|2:3|1:1)(?![\d:])")

_UNIT = r"(?:seconds?|secs?|s|giây)"
# Nouns for the WHOLE work, and nouns for ONE PART of it: a part noun between the number and the work noun cancels.
_WHOLE = r"(?:video|clip|film|movie|ad|advert|commercial|trailer|reel|spot|short)"
_PART = re.compile(r"\b(?:shot|scene|pause|beat|cut|close-?up|take|segment|part|transition|"
                   r"moment|sequence|montage|intro|outro|frame)s?\b", re.I)

# "30-second, 16:9 Vietnamese public-safety news video": number-dash-second, then AT MOST 5 more words up to a noun for
# the whole video. The words in between are checked on their own, see `_whole_video_lengths`.
_HYPHEN = re.compile(r"\b(\d{1,3})\s*-\s*(?:seconds?|secs?)\b((?:[\s,]+[\w:'’.-]+){0,5}?)[\s,]+%s\b" % _WHOLE, re.I)
_DIRECT = [
    re.compile(r"\b(\d{1,3})\s*%s\s+%s\b" % (_UNIT, _WHOLE), re.I),                         # 15s video
    re.compile(r"\b%s\s+(?:of|lasting|runs?|lasts?|is|dài)\s+(\d{1,3})\s*%s\b" % (_WHOLE, _UNIT), re.I),
    re.compile(r"\b(\d{1,3})\s*(?:seconds?|secs?)\s+(?:long|in length|in total|total)\b", re.I),
    re.compile(r"\b(?:total\s+)?(?:duration|length|runtime|thời lượng|độ dài)\s*(?:of|is|:|=)?\s*(\d{1,3})"
               r"\s*%s\b"
               % _UNIT, re.I),
    re.compile(r"\b(?:video|clip)\s+(\d{1,3})\s*giây\b", re.I),                              # video 30 giây
    re.compile(r"\bdài\s+(\d{1,3})\s*giây\b", re.I),                                         # dài 30 giây
]


def _whole_video_lengths(prompt: str) -> set[int]:
    """Every number the prompt uses for the length of the WHOLE video."""
    found = {int(m.group(1)) for m in _HYPHEN.finditer(prompt) if not _PART.search(m.group(2) or "")}
    for rx in _DIRECT:
        found |= {int(x) for x in rx.findall(prompt)}
    return found


def find_conflict(prompt: str, kind: str, ratio: str | None, duration: int | None) -> tuple[str, str] | None:
    """(platform code, message) when the prompt contradicts the parameters that will run; None when it does not.

    `ratio` None = dola chooses (an image sent with no ratio): nothing to contradict. Length counts for video only.
    The ratio is judged first: once it is fixed the next call reports the length, one clear thing at a time."""
    text = prompt or ""
    if ratio:
        other = sorted({r for r in _RATIO.findall(text) if r != ratio})
        if other:
            return (CODE_RATIO,
                    "the prompt says %s but the %s ratio is %s: make them match (set `ratio`, or use a model name "
                    "with that ratio) or remove the ratio from the prompt" % (" / ".join(other), kind, ratio))
    if kind == "video" and duration:
        other = sorted(_whole_video_lengths(text) - {int(duration)})
        if other:
            return (CODE_DURATION,
                    "the prompt describes a %s-second video but duration is %d: make them match (set `duration`, or "
                    "use a model name with that duration) or remove the length from the prompt"
                    % (" / ".join(str(x) for x in other), int(duration)))
    return None
