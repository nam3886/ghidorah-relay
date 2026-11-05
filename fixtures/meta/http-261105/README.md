# meta.ai over plain HTTP: fixtures (05/11/2026)

Redacted captures from the 05/11 HTTP spike (`plans/meta-http-spike-261105.md`) and from Task 0 of
`plans/meta-http-design-261105/plan.md`. All of them come from one account, the jar `meta-1` (the account the relay
holds as **meta-acc4**, dead in the relay, a separate live session), through the spike proxy. No production account
(meta-acc1/2/5/6) and no production jar was used. Times are GMT+7.

## Redaction

Values are replaced, never keys, with `scripts/redact-fixtures.py`'s `Redactor` (salted HMAC: a value keeps its
length and character classes, and the same value gives the same fake in every file). Replaced:

- every uuid (conversation ids, request ids, message ids), so a conversation id in `frames_*.json` equals the same
  id in its `prompt_page_*.html`;
- the `ecto1:` access token, every `viewerId`, the `username` (the display name), the avatar's file name, the
  uploaded `media_id`s and every cookie value of the spike jars;
- the digit groups of fbcdn file names (`<digits>_<digits>_<digits>_n.webp`);
- every URL query value but `KEEP_QS` (`_nc_cat`, `_nc_ht`, …), including `&`- and `&amp;`-joined ones:
  signed links are `x`s of the same length.

Path classes (`/t39.105495-1/`, `/t58.90637-1/`, `/t0.71334-6/`, …) and every other byte are kept. Frames are decoded
(the frame header, then the protobuf), each text leaf is redacted and the protobuf is re-encoded: its field paths and
wire types are checked equal to the original's.

## Files

### Frames: `frames_<job>.json`

`[{"t": <frame type>, "b64": <whole frame>}]` in received order, with the two sent frames in place and marked
`"dir": "out"`: the `0x0f` EstabStream before the keepalive, the `0x0d` send right after the EstabStream answer.
The HTTP script did not keep its sent frames, so both are **rebuilt** (`"rebuilt": true`) from the browser
template with the job's own conversation id, otid, text and attachment (s4_http.py's builder); their other uuids and
times are not the ones sent. Every `"dir"`-less frame is exactly what the gateway sent (redacted).

| File | Job (text sent) | Engine | Time | Outcome |
|---|---|---|---|---|
| `frames_t2i_n1.json` | `Create 1 image: a vintage bicycle leaning against a brick wall, 3:4` | HTTP (s4_http.py, with Heisenberg) | 16:52 | 1 final webp |
| `frames_t2i_n2.json` | `Create 2 images: a red fox sitting in a snowy forest, 9:16` | HTTP | 16:48 | 2 final webp |
| `frames_t2i_n4.json` | `Create 4 images: a ceramic teapot on a wooden table, 1:1` | HTTP | 16:49 | 4 final webp |
| `frames_t2v.json` | `Create a short video: a paper boat floating down a rainy street` | HTTP | 16:50 | 1 mp4 |
| `frames_i2v.json` | `Animate this image: waves crash against the rocks and the clouds drift slowly` + 1 image | HTTP (s6_upload.py + s4_http.py) | 16:06 | 1 mp4 |
| `frames_refusal.json` | `Create 1 image: Taylor Swift playing guitar on stage` | HTTP | 16:53 | refused, 0 media, `policy_denied` tool result |
| `frames_edit.json` | `Edit this image: turn the sky into a starry night with a full moon, keep the lighthouse and the rocks exactly as they are` + 1 image | HTTP (s6_upload.py + s4_http.py), Task 0 | 17:46 | 1 final webp (2 `t58` previews) |

### `request_skeleton.json`

One row per leaf of the **browser's** captured `Request` (the 15:52 browser image send, `cdp_1793868721`):
`{"path", "wire", "kind", "value"}`. Kinds: `const` (its value appears in the public bundle, kept), `viewer`,
`conversation`, `uuid`, `ms`, `otid`, `text`, `account?`. Leaves that dgw.py reads as text but are messages holding
uuids (`1.1.5`, `1.10`) are split into their own leaves. Small integers (enums, flags, counts) count as `const`.

**`account?` leaves** (not in the bundle; the live smoke on a second account settles them):

| Path | Wire | What it held in the browser capture |
|---|---|---|
| `1.1.12.3.2` | 2 | the mode string `MODE_FAST` (9 chars; not found as text in the captured chunks) |
| `1.1.15` | 2 | the browser's User-Agent (Linux Chrome 154) |
| `1.1.19.2` | 5 (fixed32) | `0x3f800000`, the float 1.0, beside the bloks id `1.1.19.1` |
| `1.15.4` | 2 | the client's IANA time zone (`Asia/Saigon`, the box's; the plan sets `Europe/Berlin`) |
| `1.20` | 2 | 2 raw bytes `03 06` (a packed list of two small enums) |

### Pages

| File | What | Engine | Time |
|---|---|---|---|
| `page_signed_in.html` | `GET /`, signed in | HTTP (curl_cffi) | 16:34 |
| `page_signed_out.html` | `GET /` with no cookie, after the `rd` pass | HTTP, Task 0 | 18:13 |
| `page_rd_403.html` | meta's own 478-byte `rd` challenge page (`x-fb-rd`, `?challenge=`) | HTTP | 15:45 |
| `page_vercel.html` | the "Vercel Security Checkpoint" page | HTTP | 15:59 |
| `prompt_page_t2i_n1.html` | `GET /prompt/<cid>` of `frames_t2i_n1` | HTTP, Task 0 | 17:52 |
| `prompt_page_t2i_n2.html` | of `frames_t2i_n2` | HTTP, Task 0 | 17:57 |
| `prompt_page_t2i_n4.html` | of `frames_t2i_n4` | HTTP, Task 0 | 18:03 |
| `prompt_page_i2v.html` | of `frames_i2v` | HTTP | 16:41 |
| `prompt_page_refusal.html` | of `frames_refusal` | HTTP, Task 0 | 18:08 |
| `prompt_page_edit.html` | of `frames_edit`: an edit chat **made over HTTP** (upload + one send), not by the browser | HTTP, Task 0 | 17:47 |

### Public JS (unredacted)

- `schema_261105.json`: the base64 FileDescriptorProtos of `ecto_request_client.proto` and
  `ecto_response_client.proto`, as embedded in the bundle.
- `gateway_constants.txt`: bundle excerpts (±300 chars) around `x-dgw-appid`, the app id and the `product_context`
  strings (`Abra Web Main Key`, `desktop_web`, `MODE_FAST`, `HUMAN_AGENT`).

## Markers in a prompt page

fbcdn links are told apart by their path class. Distinct file names per page, as recorded:

| Page | `t39.105495-1` (generated) | `t58.90637-1` (preview) | `t0.71334-6` (input) | `t39.30808-6` (avatar) |
|---|---|---|---|---|
| `prompt_page_t2i_n1` | 1 | 0 | 0 | 1 |
| `prompt_page_t2i_n2` | 2 | 0 | 0 | 1 |
| `prompt_page_t2i_n4` | 4 | 0 | 0 | 1 |
| `prompt_page_i2v` | 1 (`.mp4`) | 0 | 1 | 1 |
| `prompt_page_edit` | 1 | **1** | 1 | 1 |
| `prompt_page_refusal` | 0 | 0 | 0 | 1 |

- **Generated image:** `…/v/t39.105495-1/<ids>_n.webp`, under `artifacts[].full_image.url` and `preview_image.url`,
  in `<img alt="gallery/image_<date>_<time>.webp" class="outline-fill-divider rounded-22 block h-…">` and in a
  `<link rel="preload" as="image">`. The distinct count equals n, and the links equal the final links of the job's
  frames.
- **Video:** `…/v/t39.105495-1/<ids>_n.mp4` under `video.url`.
- **Preview:** `…/v/t58.90637-1/`. None in the text-to-image pages. In the edit page one `t58` link stands as its
  **own artifact** (its `full_image.url` and `preview_image.url` are both the `t58` link), so a reader must count
  `/t39.105495-1/` only, never "every artifact". The edit's frames carried 2 `t58` previews.
- **Input image:** `…/m1/v/t0.71334-6/<opaque>` under the user message's `attachments[].uri`, in `<img alt="">` and a
  preload `<link>`.
- **Avatar:** `…/v/t39.30808-6/<ids>_n.jpg` in `socialProfile.profile_picture`, plus an `<img>` and a `<link>`, on
  every signed-in page, prompt pages included. It shares the `/t39.` prefix: match `/t39.105495-1/` in full.
- **Signed-in home page only:** chat-list thumbnails `…/v/t0.83350-7/` (`thumbnailUrl`) and suggestion art under
  `…/o1/v/t6/`.
- **Done and refusal:** every prompt page holds `\"streamingState\":\"DONE\"` twice. The refusal page holds no
  generated link and the answer `I wasn't able to generate the image you requested.`
- **Signed in / out:** `\"isLoggedIn\":true` (twice) or `\"isLoggedIn\":false` (twice). The signed-out page has
  `\"accessToken\":null` and no `user-menu-button`.

## Where the display name sits (signed-in page)

- In the Next.js payload: `\"socialProfile\":{\"username\":\"<display name>\",\"profile_picture\":\"<avatar url>\"}`,
  next to `viewerId` and `isLoggedIn`.
- In the HTML: the text of the element `data-testid="user-menu-button"` (the first text piece after it).
- The access token is `\"accessToken\":\"ecto1:…\"` in the same payload (here a fake of the same length).

## The edit over HTTP (Task 0, 05/11 17:46)

One edit, sent once, never re-sent: the reference image (the spike's 2048×1152 lighthouse webp, 762 KB) uploaded
with `s6_upload.py` (rupload `200` in 6.4 s, a 16-digit `media_id`), then `s4_http.py` with that attachment and the
saved page's token (no page load, no GraphQL warmup). EstabStream `200`, one `0x0d` send, first final link 20.1 s and
the final frame 25.1 s after the send. **It worked:** 1 final `t39.105495-1` webp, 2096×1184, 724 KB, downloaded
through the proxy in 2.0 s; the answer text says it kept the lighthouse and rocks. So an edit from a reference
image works over HTTP with the same attachment shape as the image-to-video job.
