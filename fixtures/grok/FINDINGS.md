# grok.com (provider `grok`): spike findings (06/11/2026)

One **free** test account, plain HTTP/WebSocket from the LAN box (`curl_cffi` 0.17, `impersonate="chrome"`) through one
datacenter proxy (exit Berlin, DE), never direct. Spike 11:24–11:31 GMT+7 06/11 (1 chat, 1 t2i of 2 images, 1 edit,
1 t2v), the owner-approved out-of-quota send at 11:55, the step-0 read-back probe 12:05–12:07 (reads only). Full
reports: `plans/grok-spike-261106.md`, `plans/grok-design-261106/step0.md`. Fixtures: `fixtures/grok/spike-261106/`
(see `fixtures/README.md`). The code cites these numbers.

**Fixture ids.** The box evidence was saved with placeholders (`<UUID>`, `<SECRET>`, step-0 labels `<CONV_n>` …).
Each became a fixed UUID-shaped fake, the same in every file, so a stream and its read-back match:

| fake (first group) | stands for |
|---|---|
| `d0000000-…-000000000001` | the account's user id (`userId`, `ownerUserId`, `users/<uid>/…` paths) |
| `c0000000-…-00000000000N` | conversation `CONV_N`: 1 = edit, 7 = t2v, 8 = t2i, 2–5 = empty-body probes; 90/91 synthetic |
| `e0000000-…`, `a1000000-…`, `af000000-…` | step-0 response ids, asset ids, file-metadata ids (label number kept) |
| `70000000-…-1/2` | t2i preview ids (job order 0/1) |
| `60000000-…-1/2` | t2i `image_id` of `start_stage` / `completed` |
| `50000000-…`, `40000000-…` | upload `uploadId`, edit stream `imageId` |
| `90000000-…`, `80000000-…` | t2v `videoId`, `videoPostId` |
| `img_000…01` | the spike's own t2i request id (`img_` + 32 hex) |

Measured equalities kept: t2i final `job_id` = final `id` = the id in its `.jpg` URL = the read-back `assetId` (box
check on the raw URLs + `match.py`, 2/2); read-back `key` path id = its `assetId`; the edit input `fileMetadataId` =
`inputAssets[0]` = the id in `users/<uid>/<id>/content`. **Assumed, not measured** (the raw stream ids were redacted
before they could be compared): edit/t2v stream `assetId` and `responseId`s = the read-back's; t2i order 0 → the
asset created first (04:28:38.294Z), order 1 → the second (.394Z, also the list's `latestAssetMetadata`).
`videoId` ≠ `assetId` and `image_id` ≠ `job_id` are kept distinct: never rely on them being equal.

## F1. Session and identity
- `GET /api/auth/session` signed in → 200 `{"status":"authenticated","session":{userId, email, emailDomain, givenName,
  familyName, profileImage, xUserId:"", xUsername:"", organizationId:"", …, xSubscriptionType:"", createTime (ms),
  sessionId, signInMethod, …}}`. No plan field: free is read from `xSubscriptionType == ""` plus the quota. Fixture
  `session_signed_in`.
- `userId` is the `x-userid` cookie's value (one value in the jar and the signed-in page).
- The `sso` cookie alone is enough for the page, `quota_info` and the Imagine WS (11:31:47–54); `sso-rw`, `__cf_bm`
  and TLS impersonation are not needed (with `impersonate=None` all three still answered).
- Signed out: see F8. Fixture `session_signed_out`.

## F2. `quota_info` and the pools
- `POST /rest/media/imagine/quota_info` body `{}` → `{"image":null, "imagePro":{available, remainingQueries,
  windowSizeSeconds:86400[, nextAvailableAt]}, "imageEdit":null, "video":null, "video720p":{…}}`.
- **No `totalQueries`** anywhere. `nextAvailableAt` (RFC 3339, nanoseconds) appears **only at 0 left** (absent from
  `imagePro` at 1). `available` stays `true` at 0: only `remainingQueries` says the pool is empty.
- Costs: t2i n = 2 non-pro (`enable_pro:false`) took **2** `imagePro` (4 → 2), the edit **1** (2 → 1), the t2v **1**
  `video720p` (1 → 0). So pool `image` = `imagePro` (t2i and edit, 1 per image), pool `video` = `video720p`.
- Video refill: `nextAvailableAt` = 05:30:10Z 07/11 = **25 h after the send** (04:30:10Z). Its sub-second part changes
  between reads (`.536…`, `.874…`, `.051…`): compare it to the second, never exactly.
- The account had 4 `imagePro` left at 11:26 after 4 used earlier: ~8 per 24 h on free.
- Fixtures `quota_before` (4/1), `quota_after_t2i` (2/1), `quota_after_t2v` (1/0 + `nextAvailableAt`; one read after
  the edit and the t2v), `quota_video_exhausted` (11:55, 1/0).

## F3. Imagine WS frames and the preview/final rule (t2i)
- `wss://grok.com/ws/imagine/listen`, header `Origin: https://grok.com`; opened in ~1 s with the jar.
- Sent: `conversation.item.create` `content:[{type:"reset"}]`, then one `conversation.item.create` with
  `content:[{requestId, text, type:"input_text", properties:{…, aspect_ratio, enable_pro:false, enable_nsfw:false,
  num_generations:n}}]`. The spike did not log sent frames; the fixture rebuilds them from `s5_image.py`.
- Received (times from the send): `session {conversation_id}` 0.65 s; per job `json current_status:"start_stage"`
  (`job_id`, `image_id`, `order`, `width/height`) 3.15 / 3.46 s; per job an `image` **preview** (URL `.png`,
  `percentage_complete: 50.0`, blob ~24 KB PNG) 3.46 / 3.58 s; per job an `image` **final** (URL `.jpg`,
  `percentage_complete: 100.0`, adds `mode`) 4.31 / 4.50 s; per job `json current_status:"completed"` (`moderated:
  false`, `r_rated:false`, `model_name:"imagine-x-1"`, `mode:"fast"`) 4.50 s.
- Correction to the spike report: the preview **does** carry `percentage_complete` (50.0). The rule is: a final is an
  `image` frame with a `.jpg` URL **and** `percentage_complete == 100`; anything else is never delivered.
- Every job frame carries **`request_id`** (snake case) equal to the sent `content[0].requestId`; the `session` frame
  has none. Filter on `request_id`.
- `blob` is plain base64 (no `data:` prefix): its length is exactly the base64 length of the saved file (228 168 chars
  for 171 125 bytes). Finals 960×960 JPEG. In the fixtures every blob is a 16×16 sample.
- The final `id` / URL id = `job_id` = the read-back `assetId` (see the table above). URL
  `https://imagine-public.x.ai/imagine-public/images/<id>.jpg`: 200 **without cookie**, same bytes as the blob.
- Fixture `imagine_t2i_n2`.

## F4. Upload v2 (edit input)
- `POST /http/upload-file-v2/direct`, multipart `file` + `file_source=IMAGINE_SELF_UPLOAD_FILE_SOURCE` → 200 in 3.0 s
  `{uploadId, fileMetadata:{fileMetadataId, fileMimeType, fileName, fileUri:"users/<uid>/<id>/content",
  parsedFileUri:"", createTime, fileSource:"SELF_UPLOAD_FILE_SOURCE"}}`. `fileMetadataId` is what `inputAssets` takes.
- A `terminalError` answer was not seen. Fixture `upload_v2`.

## F5. Edit NDJSON
- `POST /rest/app-chat/conversations/new` `{modelName:"imagine-image-edit", message, enableImageStreaming:true,
  enableSideBySide:true, sendFinalMetadata:true, mediaGenInput:{imageToImage:{prompt, inputAssets:[fileMetadataId]}}}`,
  no `x-statsig-id` → 200 `application/json`, one JSON object per line.
- Line 1 (0.98 s): **`result.conversation.conversationId`**: this is the conversation-id field the design left open
  (`kind: CONVERSATION_KIND_UNSPECIFIED`). Line 2 (0.98 s): `result.response.userResponse` (the human turn:
  `responseId`, `message`, `fileAttachments`, `mediaGenInput`) and `result.response.responseId` (the assistant turn;
  every later line repeats it).
- Then `progressReport` ×2, `queryAction`, `imageAttachmentInfo`, `imageDimensions` (1.02–1.07 s);
  `streamingImageGenerationResponse` progress 50 at 5.43 s (`imageUrl` `…/generated/<imageId>-part-0/image.jpg`);
  progress **100** at 6.24 s: `imageUrl` relative `users/<uid>/generated/<assetId>/image.jpg`, `assetId`,
  `moderated:false`, `rRated:false`; `token` 6.25 s; `modelResponse` 6.29 s (`responseId`, `parentResponseId`,
  `generatedImageUrls`, `fileAttachments:[assetId]`); `result.title.newTitle` 6.5 s, then the stream ends.
- Output: 1024×1024 JPEG. Fixture `ndjson_edit` (`t_ms` per line from the POST).

## F6. Text→video NDJSON
- Same POST, `{modelName:"imagine-video-gen", message:"<prompt> --mode=custom", kind:"CONVERSATION_KIND_IMAGINE",
  mediaGenInput:{textToVideo:{prompt, aspectRatio:"16:9", duration:6, resolutionName:"720p"}}}`, no statsig.
- Line 1 (0.95 s) `result.conversation.conversationId` (`kind: CONVERSATION_KIND_IMAGINE`); `userResponse`;
  `queryAction`; `imageAttachmentInfo`; `streamingVideoGenerationResponse` progress **1 at 0.97 s with `videoId` and
  `videoPostId`**; 5 and 10 at 13.27 s (10 adds `width 1280`, `height 720`, `moderated:false`); 16 … 95 at 25–32 s;
  **100 at 34.5 s** with `assetId`, `videoUrl` (`users/<uid>/generated/<assetId>/generated_video.mp4`),
  `thumbnailImageUrl`, **`rRated:false`**; `token`, `modelResponse` 34.53 s, title 34.94 s.
- Output: MP4 6.04 s, h264 1280×720 @ 24 fps, AAC 48 kHz, 4.39 MB. A real video, not a decoy (34.5 s, `rRated`
  present, content matches the prompt). Fixture `ndjson_t2v`.

## F7. Media hosts and cookies
- `imagine-public.x.ai` (t2i): 200 with **no cookie**.
- `assets.grok.com` (edit, video): **403 without cookie, 200 with** the account's jar (video 4.39 MB in 2.3 s through
  the proxy). Stream URLs are relative (`users/<uid>/…`); the full URL is `https://assets.grok.com/<relative>`, the
  same shape as the read-back `key`.

## F8. Signed-out and refusal shapes (verbatim)
- Session with no cookie or a made-up `sso`: 200 `{"status":"unauthenticated"}`.
- REST (`quota_info`, `rate-limits`): 401 `{"code":16,"message":"No credentials presented.
  [WKE=unauthenticated:no-credentials]","details":[]}` (no cookie) or `"Bad credentials.
  [WKE=unauthenticated:bad-credentials]"` (made-up `sso`). Imagine WS: handshake refused 401 (`curl: (22) Refused
  WebSocket upgrade: 401`), no frame.
- `conversations/new` with body `{}`: 400 `{"error":{"code":3,"message":"Cannot generate response to empty
  conversation.","details":[]}}` with any `x-statsig-id` value or none. It **still creates an empty conversation**
  (F9).
- Not seen on this IP: a Cloudflare challenge (the normal page loads `/cdn-cgi/challenge-platform/…` but has no "Just
  a moment" / `__cf_chl`), a 403 `code 7`, a moderated answer, a blocked account, a decoy video. Those fixtures are
  synthetic (README Gaps).
- Fixtures `rest_401_no_credentials`, `rest_401_bad_credentials`, `ws_401`, `rest_400_code3`.

## F9. Step 0: read-back and the out-of-quota answer
- **Out of quota** (11:55, `video720p` 0): the t2v send answered **429** `application/json` in 0.87 s, one line
  `{"error":{"code":8,"message":"Too many requests","details":[]}}`, no `Retry-After`, no window; no conversation was
  created and the quota did not change. The body does not say "video quota": it cannot be told apart from a plain
  rate limit. Fixture `send_out_of_quota`.
- **Read one conversation:** `GET /rest/app-chat/conversations/{conversationId}/responses` (query `includeThreads`,
  `conversationKind` optional, same answer without) → `{responses:[…], inflightResponses:[…], …}`. Human turn
  (`sender:"human"`, `message`, `mediaGenInput`, `createTime`); assistant turn (`sender:"assistant"`, `parentResponseId`
  = the human turn) with `fileAttachmentAssetMetadata[]`: `assetId`, `mimeType`, `key` (path on `assets.grok.com`),
  `rRated` (bool, images and video), **`auxKeys.moderated` as a string `"true"`/`"false"`**, `isModelGenerated:true`,
  `fileSource:"IMAGINE_GENERATED_FILE_SOURCE"`. The edit's input image sits on the human turn with
  `isModelGenerated:false`: never an output. **No progress**: the assistant turn appears only once the job is done; a
  running job is presumably in `inflightResponses` (only seen empty). Fixtures `readback_edit` (CONV_1),
  `readback_t2v` (CONV_7), `readback_t2i` (CONV_8: the Imagine WS also makes a conversation, its two assets are the
  two finals), `readback_empty` (CONV_2: an assistant turn with no asset, from a 400 `code 3` probe → no image).
- t2v timing in the read-back: human `createTime` 04:30:09.974Z → assistant 04:30:43.588Z = 33.6 s.
- **Recent conversations:** `GET /rest/app-chat/conversations?pageSize=30` lists only `CONVERSATION_KIND_UNSPECIFIED`
  (the edit and the empty probes); `&kind=CONVERSATION_KIND_IMAGINE` lists t2v and t2i. Entries hold `conversationId`,
  `title`, `createTime`, `modifyTime`, `kind`, `latestAssetMetadata` (the IMAGINE ones carry the final output), **not
  the sent text**: matching by text needs the `responses` read of each candidate. Fixtures `list_conversations`,
  `list_conversations_imagine`.
- Not usable: `POST /rest/media/post/list` → 400 `{"code":9,"message":"MongoDB-backed media post listing has been
  removed"}` (`post_list_removed`); `GET /rest/app-chat/image-generations` → `{"generations":[]}`, no Imagine images
  (`image_generations_empty`): there is **no separate Imagine history**, the conversation read covers t2i.
  `GET /rest/app-chat/conversations_v2/{id}` holds metadata only (not kept).
- Every stream names its conversation first: edit/t2v line 1 (0.95–0.98 s), t2i `session` frame (0.65 s), before any
  progress. Step-0 decision: `after_sent = RECONCILE` for t2i, edit and t2v (t2i overrides the design's Q5 default
  `FAIL_REFUND` only if the owner confirms).
