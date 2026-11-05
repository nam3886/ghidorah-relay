"""An input image as meta takes it: JPEG, PNG or WebP of at most `UPLOAD_MAX_BYTES`, else a JPEG made by ffmpeg."""

import asyncio
import contextlib

from relay_meta.constants import UPLOAD_MAX_BYTES, UPLOAD_MAX_SIDE

AS_IS = ("image/jpeg", "image/png", "image/webp")      # sent unchanged when small enough
QUALITIES = (2, 4, 6, 8, 10)                           # ffmpeg -q:v, best first
BOUND_S = 30
SCALE = "scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease" % (UPLOAD_MAX_SIDE, UPLOAD_MAX_SIDE)


async def _encode(data: bytes, q: int) -> bytes | None:
    """One ffmpeg run, stdin to stdout. None when it fails or times out."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-i", "pipe:0", "-vf", SCALE, "-frames:v", "1", "-pix_fmt", "yuvj420p",
            "-c:v", "mjpeg", "-q:v", str(q), "-f", "image2pipe", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(data), BOUND_S)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise
    except (OSError, TimeoutError):
        return None
    return out if proc.returncode == 0 and out else None


async def shrink(data: bytes, mime: str) -> tuple[bytes, str] | None:
    """`(data, mime)` unchanged when small enough and of a type meta takes; else a JPEG inside `UPLOAD_MAX_SIDE`
    that fits `UPLOAD_MAX_BYTES`. None when the input does not decode or no quality fits."""
    if len(data) <= UPLOAD_MAX_BYTES and mime in AS_IS:
        return data, mime
    for q in QUALITIES:
        out = await _encode(data, q)
        if out is None:
            return None             # a bad input fails the same at every quality
        if len(out) <= UPLOAD_MAX_BYTES:
            return out, "image/jpeg"
    return None
