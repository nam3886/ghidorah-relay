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
