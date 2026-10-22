"""Download filenames: `Content-Disposition` from the `filename_pattern` setting.

Pure strings, no I/O. Tags (any case): {prompt} {model} {duration} {ratio} {resolution} {date} {time} {id} {index};
an unknown tag stays as text. Forbidden characters become `_`; the n-th output of an order gets `_2`, `_3` ... (an
image gets `_NN`), so downloading all of an order's files never overwrites one with another.
"""

import re
from datetime import datetime
from urllib.parse import quote

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_TAG = re.compile(r"\{(\w+)\}")
_EXT = {"video/mp4": "mp4", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
PART_MAX = 45          # one tag value
NAME_MAX = 120         # the whole stem, before the output suffix


def _clean(text: str) -> str:
    text = _FORBIDDEN.sub("_", str(text or ""))
    return re.sub(r"_+", "_", re.sub(r"\s+", "_", text))


def extension(mime: str) -> str:
    return _EXT.get((mime or "").split(";")[0].strip().lower(), "bin")


def media_filename(pattern: str, *, prompt: str, model: str, media_type: str, mime: str, idx: int, job_id: str,
                   created_at: datetime, duration: int | float | None = None, ratio: str = "",
                   resolution: str = "") -> str:
    """The download name of output `idx` (0-based) of a job. `created_at` is shown in the process's local time zone
    (the TZ of the container), the clock the person reading the name keeps."""
    when = created_at.astimezone()
    date_s, time_s = when.strftime("%Y-%m-%d"), when.strftime("%H-%M-%S")
    tags = {
        "prompt": _clean(prompt)[:PART_MAX].strip("_") or media_type or "media",
        "model": _clean(model)[:PART_MAX].strip("_"),
        "duration": "%ss" % duration if duration else "",
        "ratio": _clean(str(ratio).replace(":", "x"))[:PART_MAX].strip("_"),
        "resolution": _clean(resolution)[:PART_MAX].strip("_"),
        "date": date_s, "time": time_s,
        "id": str(job_id)[:8], "index": str(job_id)[:8],
    }
    stem = _TAG.sub(lambda m: tags.get(m.group(1).lower(), m.group(0)), pattern or "{prompt}_{duration}")
    stem = _clean(stem).strip("_")[:NAME_MAX].strip("_") or "%s_%s_%s" % (media_type or "media", date_s, time_s)
    if media_type == "image":
        stem = "%s_%02d" % (stem, idx + 1)
    elif idx:
        stem = "%s_%d" % (stem, idx + 1)
    return "%s.%s" % (stem, extension(mime))


def content_disposition(name: str) -> str:
    """`inline` with an ASCII fallback and the RFC 5987 UTF-8 form (a non-ASCII prompt keeps its letters)."""
    ext = name.rpartition(".")[2]
    ascii_name = name.encode("ascii", "ignore").decode()
    if not ascii_name.rpartition(".")[0].strip("_ "):
        ascii_name = "media.%s" % ext
    return "inline; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name, safe=""))
