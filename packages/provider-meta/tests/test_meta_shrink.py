"""The input image re-encode: what is sent unchanged, what becomes a JPEG, what is refused."""

import asyncio
import functools
import json
import os
import subprocess
import tempfile

from relay_core.testing.mutation import mutation
from relay_meta.engine.shrink import shrink

SHRINK_FILE = "packages/provider-meta/src/relay_meta/engine/shrink.py"
LIMIT_CHECK = "    if len(data) <= UPLOAD_MAX_BYTES and mime in AS_IS:"
FIT_CHECK = "        if len(out) <= UPLOAD_MAX_BYTES:"
OK_CHECK = "    return out if proc.returncode == 0 and out else None"


@functools.cache
def made(ext: str, size: str, noisy: bool = False) -> bytes:
    """A real picture made by ffmpeg (`testsrc`; with `noisy` a noise overlay that PNG cannot compress)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "i." + ext)
        src = "testsrc=size=%s:rate=1:duration=1" % size
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", src]
        if noisy:
            cmd += ["-vf", "noise=alls=100:allf=t"]
        subprocess.run([*cmd, "-frames:v", "1", path], check=True)
        with open(path, "rb") as f:
            return f.read()


def run(data, mime):
    return asyncio.run(shrink(data, mime))


def dims(data: bytes) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-"], input=data,
                         capture_output=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


@mutation(SHRINK_FILE, LIMIT_CHECK, "    if False:", expect="same bytes")
def test_a_small_jpeg_png_or_webp_is_sent_unchanged():
    for ext, mime in (("jpg", "image/jpeg"), ("png", "image/png"), ("webp", "image/webp")):
        data = made(ext, "64x48")
        out = run(data, mime)
        assert out is not None and out[0] is data and out[1] == mime, "same bytes, same type: " + ext


@mutation(SHRINK_FILE, LIMIT_CHECK, "    if mime in AS_IS:", expect="jpeg")
@mutation(SHRINK_FILE, FIT_CHECK, "        if True:", expect="under 1 MiB")
def test_a_large_png_becomes_a_jpeg_under_1_mib_and_2048_px():
    data = made("png", "4000x3000", True)
    assert len(data) > 1 << 20, "the input must be over 1 MiB"
    out = run(data, "image/png")
    assert out is not None
    jpeg, mime = out
    assert mime == "image/jpeg" and jpeg.startswith(b"\xff\xd8\xff")
    assert len(jpeg) <= 1 << 20, "under 1 MiB"
    w, h = dims(jpeg)
    assert max(w, h) <= 2048 and abs(w / h - 4 / 3) < 0.01, "side and aspect"


@mutation(SHRINK_FILE, LIMIT_CHECK, "    if len(data) <= UPLOAD_MAX_BYTES:", expect="re-encoded")
def test_a_small_gif_is_re_encoded_since_meta_takes_jpeg_png_webp():
    data = made("gif", "64x48")
    out = run(data, "image/gif")
    assert out is not None and out[1] == "image/jpeg" and out[0].startswith(b"\xff\xd8\xff"), "re-encoded"


@mutation(SHRINK_FILE, OK_CHECK, "    return out", expect="no image")
def test_bytes_that_are_no_image_give_none():
    assert run(b"this is not an image" * 100, "image/gif") is None, "no image"
    assert run(b"this is not an image" * 100000, "image/png") is None, "no image"      # over 1 MiB: not passed as is
    assert run(b"", "image/gif") is None, "no image"
