# vibes.ai — live run findings (2026-10-23)

The spike made plain HTTP calls (`requests`, desktop Chrome User-Agent, `Origin`/`Referer` set to `https://vibes.ai`, cookies `cookie_ack=true` and `meta_session`). They went from the LAN box through one German exit proxy, with one account (acc1). It ran one text-to-image job and one text-to-video job (480p, `midjen-short`). The redacted calls are in `fixtures/vibes/s5-live/`. Times below are in GMT+7, with UTC in brackets.

## 1. Login and quota from Germany
- `GET /api/auth/me` returned 200 in 2.9 s. The user is `accountStatus: ACTIVE` and `roles: []`, and the account was created 2026-10-22. `kadabraProfile.syncedAt` is `null`; the earlier capture (`fixtures/vibes/s4-capture`) had a timestamp there.
- The Vercel edge was `fra1`. There was no region block and no geo error.
- `GET /api/quota/upsell` returned `{"upsell": null}` before the jobs and again after both jobs. **It did not change, so it tells us nothing about quota use.** No other quota signal was seen.
- Result media came from US CDN hosts (`scontent-iad3/iad6/ord5`, `video-iad6/ord5`). The s4 capture had Singapore hosts (`sin*`). The CDN follows the exit location.

## 2. The empty batch
- `POST /api/generation-batches` takes the s4 capture's body unchanged and returns `{"batch": null, "id": <id>}` in 1.7–2.5 s.
- A GET of the empty batch returns 200 in 2.8–4.3 s. It has `isComplete: false`, `needsPolling: false`, `hasError: false` and `canRetry: true`, with `generationStartTime == generationEndTime == createdAt`. It holds one content item `<batch>-content-0` with `isLoading: true`, `data: null` and no media. On the server the content `type` is plural (`images`/`videos`), even though `image`/`video` was sent.

## 3. Reconcile question: does the batch change during the synchronous generate POST?
**No.** `POST /api/generate/videos` was in flight from t=4.47 s to t=17.63 s (13.2 s). Polls were sent at 4.47, 8.62, 11.62 and 14.63 s and all returned before the POST did. Every one showed the empty shell unchanged: `updatedAt` stayed at creation, `needsPolling: false`, `isLoading: true`, `data: null`, no `mediaEntId` and no `imageUrl`.

The only poll that showed a change was sent at t=17.62 s, the same moment the POST returned. It had `needsPolling: true`, `data` set (`videoGenEntId`, `requestId`, `imageEntId`), a `mediaEntId`, a poster `imageUrl` and `canRetry: false`. The new `updatedAt` matches the end of the POST.

What this means for money safety: while a generate POST is in flight, and also if it dies in flight, the batch cannot tell "job never started" apart from "job started". Use these rules:
- A shell that is still empty (`needsPolling: false`, `data: null`) after a lost POST is **unknown**, not "not charged".
- `data`/`mediaEntId` present, or `needsPolling: true`, means **accepted**.
- Never re-send a generate on a batch id whose POST outcome is unknown. Read it again first. If it is still an empty shell, treat it as unresolved, not as safe to retry.

The image POST behaves the same way. It is synchronous (7.8 s) and returns the finished `updatedBatch`.

## 4. Timings
| Step | Time |
|------|------|
| image create batch | 2.45 s |
| image GET of empty batch | 4.31 s |
| `POST /api/generate/images` | 7.8 s (batch generationStart→End was 12 s) |
| `GET /api/download/png` | 1.5 s |
| video create batch | 1.7 s |
| `POST /api/generate/videos` | 13.2 s |
| batch GET, normal | 1.0–2.2 s |
| batch GET, the one that finalized | 7.9 s |
| first poster `imageUrl` | at POST return (17.6 s) |
| first `videoUrl` while still `isLoading: true` | 34 s |
| `thumbnailUrl` first seen | 48.6 s |
| last good poll before the block, still `isComplete: false` and `isLoading: true` | 217.9 s |
| `isComplete: true` | only on the GET at 01:08:58 (18:08:58Z), about 19.5 min after start; nothing was observed between 218 s and then |

Finalizing looks **lazy, on read**. The batch's `generationEndTime` and `updatedAt` both equal the moment of that GET (18:08:58Z), and that GET took 7.9 s where reads are normally about 1.4 s. The s4 capture is consistent with this. So the real render time could not be measured; the s4 capture saw completion at about 300 s.

## 5. Results and downloads
- The image result has `data[0]` with `url`, `imageEntId`, `dimensions` (720x1280), `prompt`, `originalPrompt`, `config` and empty `srefValues`/`orefValues`. This matches S4.
- `GET /api/download/png?id=<batch>-content-0` returned 200 `image/png`, 2,669,526 bytes. **The PNG is 810x1440**, not the 720x1280 the API reports, so the download is re-encoded or upscaled.
- `GET /api/download/video?id=<batch>-content-0` returned 200 `video/mp4`, 3,161,002 bytes. It is H.264 at 464x832, 24 fps, 5.21 s, with stereo AAC at 48 kHz. It used the batch content id (`<batch>-content-0`), not the `items[].id` returned by generate (`<batch>-content-0-<ms>`). The `items[].id` form could not be tested because it got the checkpoint 403.
- Reads are inconsistent from poll to poll:
  - Between 34 s and 179 s, `videoUrl` switched between **two different mp4 paths**. The second one is the final one.
  - `thumbnailUrl` appeared and disappeared from one poll to the next.
  - The poster `imageUrl` changed path for two polls (60–67 s), then went back.

  So no single read can be trusted: decide on `isComplete && !isLoading` and download only after that.
- `videoUrl` appears before completion. Whether that partial URL is playable was not tested.

## 6. Errors seen (verbatim, redacted)
- **Vercel Security Checkpoint.**
  - From t=223.9 s, every request got 403 `text/html`, both the batch GETs and both download attempts at 745/747 s: 84 polls in a row until the 12-min cap. The headers were `X-Vercel-Mitigated: challenge` and `Retry-After: 30`, and the page title was `Vercel Security Checkpoint`.
  - What led up to it: about 45 API calls in about 5.5 min from one IP and session, made of 7 calls in the image stage plus the video stage's 2 s polls during the POST and then about 6.4 s polls. The s4 capture polled about every 7 s for 6 min without a block, but from a different client.
  - Honouring `Retry-After: 30` did not help while polling continued. The 5 s polls never cleared it.
  - It had cleared by 01:08:51 (18:08:51Z), after about 7 min with no requests (last 403 at about 18:01:48Z).
  - A plain HTTP client cannot solve the challenge, which is a JS page. **The provider must treat 403 with `X-Vercel-Mitigated: challenge` as back-off-and-cool-down, never as a job failure, and must poll much more slowly.**
- **500** `{"error": "Failed to fetch generation batch"}` on one poll at t=40.5 s. The next poll was fine. The s4 capture saw the same thing once, so it is transient and should be retried.
- No other error bodies. No `Retry-After` header except on the checkpoint, and no rate-limit headers.

## 7. Differences from the s4 capture
- The checkpoint and the US CDN hosts are new. The s4 capture had no 403s and used Singapore CDN hosts.
- `syncedAt: null` on a fresh account.
- The PNG download is larger than the reported dimensions. The s4 capture had no download calls.
- Request and response shapes for create, generate, batch and the download ids are otherwise the same as the s4 capture. The `items[].id` suffix `-<ms>` is unchanged.
- In the s4 capture the GET of the empty batch already returned `needsPolling: false`. The new information here is that this state lasts through the whole synchronous POST.

## Open items
- Find a poll cadence that does not trigger the checkpoint, for example 15–30 s with jitter. Also check whether a lighter endpoint exists.
- Find out whether completion advances without any read, by reading only once after N minutes.
- Find out whether the partial `videoUrl` is playable, and whether download accepts `items[].id`.
- Find a real quota signal; `upsell` stays `null`.

## S6 (2026-10-23, 01:56 GMT+7): ratio field and image-to-video
Two more jobs, same account and proxy (`fixtures/vibes/s6-live/`):
- `config.aspectRatio` is the ratio field. An image asked for `16:9` came back 1280x720 (the PNG download 1440x810).
- Image to video: `POST /api/upload-image` with `{image: <data URL>}` answered `{mediaEntId, imageUrl}` in 7.9 s; the
  generate takes them as `directPromptImageHandle: {image_ent_id, image_url, source: "upload"}` and answered
  `success: true, needsPolling: true` in 20.2 s.
- The video ignored `aspectRatio: "16:9"` and the square start frame: 464x832 (9:16), 5.2 s. Text-to-video with a
  ratio is not tested.
- Reads every 25 s met no checkpoint; `isComplete: true, isLoading: false` came on the read at 320 s.
- Owner ruling 23/10: until a ratio is proven for video, vibes-video takes no ratio or `9:16` only; any other ratio is refused (`input.bad_ratio`), image to video included.

## Production, 2026-10-23 09:39 GMT+7: a refused image (job f0176f20)
The image generate answered `success: true` with `updatedBatch` finished (`isComplete: true`, `hasError: false`,
batch `error: null`), but its one item had `isLoading: false`, no `imageUrl`, `data` = the string
`{"requestId": ...}` and `error: "This content could not be generated. Please try a different prompt."`.
`GET /api/download/png?id=<batch>-content-0` then answered 404 `{"error": "Conten..."}` on every try. So a finished
batch is not a result: an item with an `error` or no media (`mediaEntId`/`imageUrl`/`videoUrl`) is a refusal and the
job fails at once (`vibes.content_refused`), and a 404 JSON `{"error"}` download fails the job
(`vibes.output_missing`), both refunded (`fixtures/vibes/refused-261023/`).

## S7 (2026-10-23, 09:19 GMT+7): image to image
Question: can `vibes-image` take a customer's image? vibes has two forms; neither is built.

**Image edit, `POST /api/generate/image-edit`** (from the references `c2tool/vibesai-go` `edit.go`/`docs/api.md` and
`vibesai2api_v2`, not run here). Body `{sourceImageEntId, editPrompt, projectId?}`; it answers
`{success, contentItem: {imageUrl, mediaEntId}}` synchronously in about 8 s. It makes no batch: there is no id we
choose, nothing to write ahead, and nothing to read back after a lost answer (`/api/download/png` 404s on the
`mediaEntId`, the result is only a signed CDN URL). And the source must be an image the account generated itself: an
uploaded image is refused with 403 "You do not have access to this image". So it cannot edit a customer's image at all,
only an earlier vibes result of the same account. To sell it anyway the core would need a send mode whose ambiguous
answer fails the job at once with its refund (the platform has only `AfterSent.RECONCILE`), plus a job pinned to the
account that owns the source.

**Reference images on a text-to-image** (from vibes' own web bundle, the `/create` page chunks, then three live jobs,
`fixtures/vibes/s7-live/`). The composer's Character / Style / Scene pickers go into the generate config:
`oref_image_ent_id` + `oref_image_url` (one character, the omni reference), `sref_image_ent_ids` +
`sref_image_urls` (styles, space-separated), `setting_image_ent_ids` + `setting_image_url` (scene), and
`sourceContentItemIds: [{id, source: "oref"|"sref"|"setting"}]` when the pick is a content item. It is the ordinary
batch flow (create batch, one generate, batch read), so money-wise it would fit: write-ahead batch id, `mark_sent`
before the one POST, reconcile by reading the batch, which keeps the reference fields. **But vibes ignored the
reference in all three jobs**: an `/api/upload-image` id in the config; the same with the web app's full shape
(config in every input, `directGeneration`, `generationType: "t2i"`); and an upload turned into a content item of
the account (`/api/upload-media` + `POST /api/projects/<id>/upload`) with `sourceContentItemIds`. Each answered
`success: true` in 7–11 s with `orefValues: ""` and a picture with nothing of the reference. It cannot be sold as
image to image, so `vibes-image` stays text only (`max_images: 0`).

Not tried (each needs more live jobs on the production account): a reference that is an image the account generated
(the web app drops an `oref_image_ent_id` that is not one of the account's results: "Reference image was deleted —
regenerating without it"), the `oref_image_handle` (oil handle) form, style references (`sref_*`), and the
`--oref <url>` prompt flag. The third job left one empty project on acc1.

## v2v spike 24/10

Question: can a customer give vibes a VIDEO and get a new video back? **Partial: edit (v2v) and extend work on a
video the same account generated; a customer's uploaded video is refused.** Account vibes-acc1 (disabled for the
run, re-enabled after), its production proxy, plain HTTP. Two sends, 23/10 21:26 and 21:40 GMT+7.

**Where in the UI.** On a generated video: *Edit* (`handleVideoToVideo`, result labelled "Edited") and *Extend*
(`handleExtendVideo`, "Extended"); the "Video Editing Tools" page has only *Prompt* enabled (*Adjust*, *Restyle*,
*Effects*, *Angle* are `comingSoon`). The client itself refuses a source without `videoHandle`/`videoGenEntId`:
"This video cannot be edited because it is missing required metadata. This may happen with older videos or videos
that were uploaded directly."

**Request shape** (bundle `/create` chunks, the same batch flow as t2v):
1. `POST /api/generation-batches` with our id (write-ahead), `type: "videos"`, the config below.
2. `POST /api/generate/videos`:
   `{inputs: [{type: "video", mediaEntId: <source videoGenEntId>, videoUrl: <source mp4>, prompt, config}],
   config: {directGeneration: true, videoModel: "midjen-video-edit", imageModel: "midjen-base", editType: "v2v",
   generationType: "v2v", sourceVideoUrl, sourceVideoHandle?, directPromptImageHandle?: {oil_handle, image_url},
   sourceContentItemIds: [{id: <source content item>, source: "v2v"}]}, batchId, mg_request_id, projectId}`.
   Extend: input `{type: "extend", mediaEntId, videoUrl, prompt, extendDirective?}`, `videoModel: "midjen-extend"`,
   `generationType: "extend"`, `sourceContentItemIds: [{id, source: "extend_video"}]`, audio carried by
   `audioSourceEntId/Handle/Url` and `reuseOriginalAudio`.
3. Poll `GET /api/generation-batches/<id>` (25 s), then `GET /api/download/video?id=<batch>-content-0`.
- Upload of a video: `POST /api/upload-video-direct`, multipart field `video`, answers `{mediaEntId, cdnUrl}`
  (413 = "File too large"). In the web app it serves only Studio text effects and playable previews.

**Proof.**
- Send 1 (own video, batch `a82272d5-…`): source = the s6 image→video result (464×832, 5.2 s, a field with birds).
  Generate answered `success: true, needsPolling: true, videoGenEntIds: [...]` in 2 s; `isComplete` on the read at
  315 s; download 200: **H.264 464×830, 5.21 s, AAC, 2.78 MB**. Frame 2 s: the same field, trees and birds, repainted
  as watercolor. The "Meta AI" watermark is on source and output.
- Send 2 (customer video): a 5.1 s 1280×720 mp4 (not from vibes) uploaded via `upload-video-direct` (200), then the
  same v2v body with the upload's `mediaEntId`/`cdnUrl`: generate **500** `GENERATION_FAILED` "Couldn't generate",
  item error *"This video is no longer available. Please use a different video."*; the batch reads `hasError: true`,
  "All video generation submissions failed", no media. Not re-sent.

**Limits/cost.** Output follows the source (≈5 s, 464×832 portrait). No quota signal moved (`upsell` stays null), as
for t2v. The extend path was read from the bundle only.

So vibes can sell "edit/extend a video we made for you on the same account" (like image edit, S7: the job must be
pinned to the account that owns the source), not "upload your video and restyle it".

## Plan (25/10)
No plan is exposed: `/api/auth/me` has no plan or subscription field, and the web bundle's API paths (`system-status`,
`meta-graphql`, `settings/*`, `projects`, `playables`, uploads, `quota/upsell`) name no plan, billing or subscription
endpoint. The check reports no tier.

## Security checkpoint on production, 25/10: the send gap and the growing rest

**What production showed** (vibes-acc1, its one account, images, GMT+7):
- 15 image jobs 32 s apart (10:32–10:39) were all done; the 16th, 24 s after the last, met the checkpoint on its
  generate (10:40:12: unknown, later failed `vibes.render_failed`, refunded). Each image job is about 3 calls (batch
  create, the generate, the download): ~48 calls in 8 min, like the ~45 in 5.5 min of the first finding.
- After the 10 min rest and ~30 min idle, 5 jobs ~92 s apart (11:12–11:18) were done, and the 6th (11:19:50) met it
  before the send (`pool.account_throttled`, requeued twice, done at 11:40). That is ~18 calls in 7 min, far below the
  first rate: the account was still marked, so a repeat soon after a checkpoint needs a longer rest, not the same
  10 min.

**The defaults:**
- `vibes.send_gap_s` = 300: the least time between two jobs of one account (the claim skips an account whose
  `last_used_at` is newer). An image job then costs ~3 calls per 5 min, a video ~15 (polls every 20–30 s), against
  the ~8 calls a minute that brought the checkpoint and the reads every 25 s that never did. The per-call spacing
  `vibes.account_min_gap_s` (8 s) stays: it spaces the calls inside one job.
- `vibes.checkpoint_rest_s` = 600 (unchanged) and `vibes.checkpoint_rest_max_s` = 7200: each repeat within a day
  doubles the rest, 10, 20, 40, 80, 120 min. One checkpoint is one repeat, however many calls of the job see it.
- Live, 25/10 12:54–13:05 GMT+7 (production code, jobs spaced by hand at the new gap): 3 image jobs on vibes-acc1,
  claimed 5 min 24 s and 5 min 32 s apart, all done in 24–32 s, no checkpoint (the account had rested since 11:40).
  A small sample: it shows the gap is not too tight, not that it is enough for a whole day.
- The checkpoint is still never a strike and never fails a job before the send (`pool.account_throttled`, no attempt
  used); after the send it is unknown and reconciled, never re-sent.

## Pinned modes live, 25/10 (extend, video edit, image edit)

Sold as three models that work on an output of the customer's own earlier vibes job (`source_job_id`): the core pins
the job to the account that made the source (vibes refuses another account's media and any upload). One live job each
on vibes-acc1, sent by the relay's engine code (`fixtures/vibes/pinned-261025/`, GMT+7):
- **Extend** (`vibes-video-extend`, `midjen-extend`), 16:51: the source batch is read first (its `videoUrl` is signed
  and short-lived), then the web app's body: input `{type: "extend", mediaEntId, videoUrl, prompt: <the source's own
  prompt>, extendDirective: <the customer's words>}`, config = the source's `structuredOutput` and batch config plus
  `videoModel: midjen-extend`, `generationType: extend`, `sourceVideoUrl`/`audioSourceUrl`, `sourceContentItemIds:
  [{id, source: "extend_video"}]`. Done at 322 s: **5.21 s became 9.04 s** (+3.83 s), 464x832, same scene continued.
- **Video edit** (`vibes-video-edit`, `midjen-video-edit`, `generationType: v2v`), 16:57, on the extend's result (an
  output of a pinned job is itself a source): done at 326 s, 9.04 s, 464x830, a pencil-sketch restyle.
- **Image edit** (`vibes-image-edit`, `POST /api/generate/image-edit {sourceImageEntId, editPrompt, projectId}`), 17:03,
  on a production `vibes-image` result: answered in 11.7 s with `{success, contentItem: {id, mediaEntId, imageUrl,
  structuredOutput.editedFrom}, batchId, requestId}`. The batch id is vibes' own, so a lost answer cannot be looked up:
  the plan is `fail_refund` (an unclear answer fails the job at once with its refund). **The edited item downloads by
  its id** like any image (`/api/download/png?id=<contentItem.id>`: 200 `image/png` 810x1440); the CDN `imageUrl` is a
  smaller JPEG and is not used. Correction of S7: the download 404 seen there was on the `mediaEntId`, not the item id.
- The source is found from the source job's own state: its batch (video modes) or its output's `upstream_id` (the
  `mediaEntId`, image edit). A source vibes no longer holds (batch 404, an item with no video, no image id) fails
  before the send (`vibes.source_missing`, refunded); a failed source read is a retry.
- Not measured: the extend ceiling (~16.7 s from the web app's text: a 4th round from 5.2 s would pass it; vibes'
  answer to that is not captured, it fails as any refusal would), whether an extend or an edit uses daily quota, and
  error bodies of a refused edit.
