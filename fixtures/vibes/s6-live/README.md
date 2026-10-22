# vibes.ai S6 live spike (2026-10-23, 01:56 GMT+7 / 2026-10-22 18:56Z)

Two jobs from the LAN box, plain HTTP, account acc1, the same German exit proxy as S5. They answer the two questions
the submit could not settle from the references: the ratio field and the image-to-video upload.

| Stage | Calls |
|-------|-------|
| `t2i_*` | create batch, `POST /api/generate/images` with `config.aspectRatio: "16:9"`, `GET /api/download/png` |
| `i2v_*` | `POST /api/upload-image` (a 720x720 JPEG of 117 kB as a data URL; the body is replaced by its size), create batch, `POST /api/generate/videos` with `directPromptImageHandle` and `config.aspectRatio: "16:9"`, 11 batch reads every 25 s, `GET /api/download/video` |

What they showed:
- `config.aspectRatio` is the ratio field for images: the API reported 1280x720 and the PNG download was 1440x810.
- The upload answered `{mediaEntId, imageUrl}` in 7.9 s. The generate took `directPromptImageHandle: {image_ent_id,
  image_url, source: "upload"}` and answered `success: true, needsPolling: true` in 20.2 s.
- The video ignored `aspectRatio: "16:9"` and the square start frame: it came back 464x832 (9:16), 5.2 s, like S5's
  text-to-video. Whether a text-to-video honours the ratio was not tested.
- Polling every 25 s met no checkpoint. The batch read `isComplete: true, isLoading: false` on the read at 320 s
  (needsPolling false from then on); every read before it had `isComplete: false, isLoading: true` with a `videoUrl`.

Redacted with the S5 rules (`vibes/s5-live/README.md`): user ids replaced, URL queries replaced, headers reduced; the
download paths' `id` is replaced by `x`. A scan for the cookie values, the proxy login and the user ids found 0 hits.
The PNG, the MP4 and the raw answers stay on the box in `~/relay-vibes-spike/raw-s6/`.
