"""An input image (image to image, image to video): uploaded the way the web app does it, before the send. Free.

`POST /api/v2/files/getstsToken` (plain HTTP) gives short-lived STS keys, the file id and its object path in qwen's
bucket; the file is then PUT to Alibaba OSS signed with those keys (OSS V4, as the web app's ali-oss client is set up:
`authorizationV4: true`), through the account's proxy. The message then names the file by its id and the CDN link
getstsToken gave (`file_url`); qwen rewrites it to its own `cdn.qwenlm.ai` link. No STS key is logged or kept.
"""

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from relay_qwen.constants import OSS_HOST_SUFFIX, UPLOAD_TIMEOUT_S
from relay_qwen.engine.http import BadAnswer, Site

STS_PATH = "/api/v2/files/getstsToken"
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def oss_v4_headers(*, method: str, bucket: str, key: str, region: str, access_key_id: str, access_key_secret: str,
                   security_token: str, content_type: str, now: datetime) -> dict[str, str]:
    """The headers of an OSS V4 signed request (Alibaba's OSS4-HMAC-SHA256, unsigned payload)."""
    day, stamp = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    signed = {"content-type": content_type, "x-oss-content-sha256": "UNSIGNED-PAYLOAD", "x-oss-date": stamp,
              "x-oss-security-token": security_token}
    canonical = "\n".join((
        method, "/%s/%s" % (bucket, quote(key, safe="/")), "",
        "".join("%s:%s\n" % (k, signed[k].strip()) for k in sorted(signed)), "", "UNSIGNED-PAYLOAD"))
    scope = "%s/%s/oss/aliyun_v4_request" % (day, region)
    to_sign = "\n".join(("OSS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    k = _hmac(("aliyun_v4" + access_key_secret).encode(), day)
    for part in (region, "oss", "aliyun_v4_request"):
        k = _hmac(k, part)
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    auth = "OSS4-HMAC-SHA256 Credential=%s/%s,Signature=%s" % (access_key_id, scope, signature)
    return {**signed, "Authorization": auth}


def file_entry(*, file_id: str, url: str, name: str, mime: str, size: int, user_id: str, now_ms: int) -> dict:
    """One `files` entry of the message, as the web app sends an uploaded image (FINDINGS 5)."""
    return {"type": "image",
            "file": {"created_at": now_ms, "data": {}, "filename": name, "hash": None, "id": file_id,
                     "user_id": user_id, "meta": {"name": name, "size": size, "content_type": mime},
                     "update_at": now_ms, "lastModified": now_ms, "name": name, "webkitRelativePath": "",
                     "size": size, "type": mime},
            "id": file_id, "url": url, "name": name, "collection_name": "", "progress": 0, "status": "uploaded",
            "greenNet": "success", "size": size, "error": "", "itemId": str(uuid.uuid4()), "file_type": mime,
            "showType": "image", "file_class": "vision", "uploadTaskId": str(uuid.uuid4())}


async def upload_image(site: Site, data: bytes, mime: str, *, now: datetime | None = None) -> dict:
    """Upload one image; its `files` entry. `BadAnswer` when a step answers something unusable."""
    name = "input.%s" % EXTENSIONS.get(mime, "jpg")
    status, body = await site.call("POST", STS_PATH, {"filename": name, "filesize": str(len(data)),
                                                      "filetype": "image"})
    sts = body.get("data") if isinstance(body.get("data"), dict) else {}
    fields = ("access_key_id", "access_key_secret", "security_token", "file_path", "file_id", "bucketname",
              "region", "endpoint", "file_url")
    if status != 200 or body.get("success") is not True or not all(
            isinstance(sts.get(f), str) and sts.get(f) for f in fields):
        raise BadAnswer("qwen answered %d to the upload token" % status)
    host = "%s.%s" % (sts["bucketname"], sts["endpoint"])
    if not host.endswith(OSS_HOST_SUFFIX):
        raise BadAnswer("qwen named an upload host outside Alibaba OSS")
    headers = oss_v4_headers(method="PUT", bucket=sts["bucketname"], key=sts["file_path"],
                             region=sts["region"].removeprefix("oss-"), access_key_id=sts["access_key_id"],
                             access_key_secret=sts["access_key_secret"], security_token=sts["security_token"],
                             content_type=mime, now=now or datetime.now(UTC))
    resp = await site.client.put("https://%s/%s" % (host, quote(sts["file_path"], safe="/")), content=data,
                                 headers=headers, timeout=UPLOAD_TIMEOUT_S)
    if resp.status_code != 200:
        raise BadAnswer("the object store answered %d to the upload" % resp.status_code)
    return file_entry(file_id=sts["file_id"], url=sts["file_url"], name=name, mime=mime, size=len(data),
                      user_id=str(site.user.get("id") or ""), now_ms=int(time.time() * 1000))
