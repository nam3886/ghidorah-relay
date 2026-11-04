"""The input image of an edit, uploaded the way the web app does it, before the send. Free (FINDINGS F2).

`POST /backend-api/files {file_name, file_size, use_case:"multimodal", width, height}` names a file id and an Azure
upload URL; the bytes are PUT there (`Web.put_blob`: https `*.oaiusercontent.com` only, through the account's proxy);
`POST /backend-api/files/{id}/uploaded {}` closes it. The width and height come from ffprobe (`fetcher.probe`, in
the worker image) on a temp file. The upload URL is signed: it is never logged nor put in an error.
"""

import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from relay_gpt.constants import INPUT_MAX_BYTES
from relay_gpt.engine.web import BadAnswer, Web

FILES_PATH = "/backend-api/files"
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


@dataclass
class Uploaded:
    file_id: str
    width: int
    height: int
    size: int
    mime: str
    name: str


async def probe_size(data: bytes, mime: str) -> tuple[int, int]:
    """(width, height) of an image by ffprobe; BadAnswer when it shows no picture."""
    from relay_core.jobs.fetcher import FetchFailure, probe  # worker-only: the spec half never imports it
    fd, path = tempfile.mkstemp(suffix="." + EXTENSIONS.get(mime, "img"))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            got = await probe(path, mime)
        except FetchFailure:
            raise BadAnswer(0, "an input image ffprobe cannot read") from None
        return got.width, got.height
    finally:
        os.unlink(path)


async def upload_image(web: Web, data: bytes, mime: str, *,
                       measure: Callable[[bytes, str], Awaitable[tuple[int, int]]] = probe_size) -> Uploaded:
    """Upload one image; raises BadAnswer (too large, unreadable, a step answered something else) before any send."""
    if len(data) > INPUT_MAX_BYTES:
        raise BadAnswer(0, "an input image over %d bytes" % INPUT_MAX_BYTES)
    width, height = await measure(data, mime)
    name = "image.%s" % EXTENSIONS.get(mime, "png")
    status, made = await web.post_json(FILES_PATH, {"file_name": name, "file_size": len(data),
                                                    "use_case": "multimodal", "width": width, "height": height})
    made = made if isinstance(made, dict) else {}
    file_id, url = made.get("file_id"), made.get("upload_url")
    if status != 200 or not isinstance(file_id, str) or not _FILE_ID.match(file_id) or not isinstance(url, str):
        raise BadAnswer(status, "the file create")
    put = await web.put_blob(url, data, mime)
    if put != 201:
        raise BadAnswer(put, "the image upload")
    status, _ = await web.post_json("%s/%s/uploaded" % (FILES_PATH, file_id), {})
    if status != 200:
        raise BadAnswer(status, "the upload close")
    return Uploaded(file_id, width, height, len(data), mime, name)
