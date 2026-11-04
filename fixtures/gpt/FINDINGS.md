# chatgpt.com (provider `gpt`) — spike findings (04/11/2026)

One ChatGPT **Plus** account, plain HTTP from the LAN box (`curl_cffi` 0.17, `impersonate="chrome152_macos"`, macOS
Chrome 152 UA) through the account's own proxy (exit DE), never direct. 04/11 23:07–23:36 GMT+7: 8 image sends and 2
text sends, one new conversation each, all hidden afterwards. Two more reads on 05/11 00:11 GMT+7 with no cookie and no
generation (the signed-out session, one sentinel prepare). Full report: `plans/gpt-spike-261104.md`. Fixtures:
`fixtures/gpt/spike-261104/` (see `fixtures/README.md`). The code cites these numbers.

## F1. Session and token
- The only token source is `GET /api/auth/session` with the cookie jar → `accessToken` (JWT, `exp − iat` = 240 h),
  `account {id (uuid), planType "plus", structure}`, `user {id "user-…", email, …}`, `expires` (+90 days),
  `sessionToken`. Cache the token until `exp − 10 min`. No refresh / oauth endpoint is needed or called.
  Fixture `auth_session` (tokens `<redacted len=N>`, identity values faked).
- Signed out (no cookie at all): **200 with body `{}`**, `content-type: application/json`, Set-Cookie names
  `__cf_bm`, `__cflb`, `_cfuvid` only. So "no `accessToken`" (not a status code) means signed out.
  Fixture `auth_session_signed_out` (recorded 05/11).
- The session cookie is `__Secure-next-auth.session-token` split as `.0`/`.1`, rolling: `GET /` and
  `/api/auth/session` re-set it with a new value. The re-set value must be stored or the account dies at the export's
  expiry. Cloudflare also sets `__cf_bm`, `_cfuvid` (rotated) and `oai-client-session-epoch`.
- `_puid` and `oai-did` are not needed: init, `/me`, prepare/finalize answered 200 `chatgpt-paid` with a random
  `OAI-Device-Id` and even with no cookie (bearer only). Sends were made with the full jar only.
- Plan names: JWT `chatgpt_plan_type`, session `account.planType`, sentinel `persona` (`chatgpt-paid` signed in,
  `chatgpt-noauth` without bearer).

## F2. Call sequence of one generation (all through the proxy, ~4–5 s before the send)
0. `GET /` (navigate headers) → `data-build` (config[6]); the sentinel loader names
   `https://chatgpt.com/sentinel/20260810913b/sdk.js` (config[5]). Fixture `home` keeps only those two values.
1. `GET /api/auth/session` → token (F1).
2. `POST /backend-api/conversation/init` → `limits_progress` (optional, F8). Fixture `init_limits`.
3. `POST /backend-api/sentinel/chat-requirements/prepare {"p": requirements token}` → `persona`, `prepare_token`,
   `proofofwork {required, seed, difficulty}`, `turnstile {required, dx}`, `so {required, collector_dx, snapshot_dx}`;
   no `token`, `arkose` or `force_login` seen. Fixtures `prepare` (signed in) and `prepare_signed_out`.
   **prepare needs no bearer**: with no cookie, no `Authorization` and a random `OAI-Device-Id` it answered 200
   (`persona: chatgpt-noauth`, difficulty `061a80`).
4. `POST .../chat-requirements/finalize {prepare_token, proof_token, turnstile_token}` → `{persona, token (~3 KB),
   expire_at, expire_after: 540}`. Fixture `finalize`.
5. `POST /backend-api/f/conversation/prepare` (`client_prepare_state:"success"`, `partial_query`,
   `system_hints:["picture_v2"]`) with the sentinel headers → `{status, conduit_token}`. Fixture `conv_prepare`
   (synthetic: only key names were printed).
6. `POST /backend-api/f/conversation` (`client_prepare_state:"sent"`, `model:"auto"`, `supported_encodings:["v1"]`)
   with `OpenAI-Sentinel-Chat-Requirements-Token`, `OpenAI-Sentinel-Proof-Token`, `X-Conduit-Token`,
   `X-Oai-Turn-Trace-Id` → SSE (F5). This is the one send; nothing after it may re-send.
- Other headers: `OAI-Device-Id`, `OAI-Session-Id` (random uuid), `OAI-Language`, `X-OpenAI-Target-Path/-Route`,
  `Sec-Fetch-*`, `Sec-Ch-Ua*` matching the UA, `Origin`/`Referer`. `OAI-Client-Version`/`-Build-Number` were not sent
  and nothing failed.
- Edit upload before step 5: `POST /backend-api/files {file_name, file_size, use_case:"multimodal", width, height}` →
  `{file_id, upload_url, status}` (Azure host `sdmntprindiasocentral.oaiusercontent.com`); `PUT upload_url`
  (`x-ms-blob-type: BlockBlob`, `x-ms-version: 2020-04-08`) → 201 through the proxy; `POST
  /backend-api/files/{id}/uploaded {}` → 200 `{download_url, status, …}`. Fixtures `files_create`, `files_uploaded`
  (synthetic). The send then carries `content_type:"multimodal_text"`, parts `[{image_asset_pointer
  "file-service://<id>", width, height, size_bytes}, prompt]` and `metadata.attachments`.

## F3. Proof of work
FNV-1a 32-bit + murmur3 finaliser (constants 2166136261, 16777619, 2246822507, 3266489909), not SHA3. Answer
`"gAAAAAB" + b64(config) + "~S"`, requirements token `"gAAAAAC" + b64(config) + "~S"`, 500,000 tries, failure
prefix `wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D`. Config = 25 fields (UA, sdk.js URL, data-build, device id in [14], …).
A 6-hex-digit difficulty took 4–87 tries, milliseconds.

## F4. Turnstile
`turnstile.dx` (~30 KB) is a VM program XOR-ed with the requirements token `p`; its register ids are floats
(`6.21`, `4.48`), so `6.21` and `6` are different registers. aurora's Go VM (MIT, `internal/turnstile`, built as
`tsolve`) solves it (~1.2 KB token). Fixture `turnstile_dx` = `{p, dx, go_token_len}` from the 05/11 signed-out
prepare (random device id, no bearer, so `p` holds no account value; copied unredacted because redaction would break
the XOR): **`go_token_len` = 1184**. Not enforced today: an image send with no Turnstile header and a text send with an
empty `turnstile_token` both passed; finalize also took the SDK's failure proof. SO (`so.required: true`) and Arkose
were never sent and nothing failed.

## F5. SSE stages (`v1` delta encoding, `{"p","o","v"}`)
`event: delta_encoding` `"v1"` → `resume_conversation_token` (names `conversation_id`) → (t5A only: the user message
and hidden system messages echoed; the edit stream and the other 39-line streams do not echo the user message) →
assistant `code` message to tool `t2uay3k.sj1i4kz` (`{"size":"1024x1024","n":1}` or `{"skipped_mainline":true}`) →
`image_gen_estimated_duration_ms` → `title_generation` → `role:"tool"` message whose `multimodal_text` part is an
`image_asset_pointer` `sediment://file_…` with `width`, `height`, `size_bytes`, `metadata.generation`,
`status: finished_successfully` → empty assistant message `end_turn: true` → `server_ste_metadata` (`model_slug`,
`tool_invoked`, `turn_use_case`) → `message_stream_complete` → `[DONE]`. `: ping` comment lines appear.
- The image arrives inside the stream (21–26 s); `async_task_type` was absent in every run. The conversation read
  (`GET /backend-api/conversation/{id}`) shows the same final message: it is the fallback when the stream breaks.
- `tool_invoked` can be `false` with an image made: never classify a refusal by it.
- **Edit: in the conversation read the input image is a `sediment://` pointer in the `role:"user"` message** (the
  `file-service://` id that was sent is rewritten), with the input's own size (2888899 bytes); the result is the
  `sediment://` pointer of the `role:"tool"` message (2864440 bytes). Only a pointer in a tool message is a result.
- Ratio follows the prompt: 1:1 → 1254×1254, 16:9 → 1672×941, 9:16 → 941×1672, none → 1536×1024; always one PNG
  ("2 different images" gave one composite). Fixtures `sse_t2i_1x1` (t5A), `sse_t2i_16x9` (t1), `sse_edit` (t3),
  `conv_t2i` (t5A), `conv_edit` (t3).
- Two sends 1 s apart on one account both finished (concurrency allowed).

## F6. Download and the bearer
`GET /backend-api/conversation/{cid}/attachment/{file_id}/download` → `{creation_time, download_url, file_name,
file_size_bytes, metadata, mime_type, no_auth_user_upload, status}`; `download_url` =
`https://chatgpt.com/backend-api/estuary/content?id&fn&cd&ts&p&cid&sig&v`. It answers 403 with no auth and 200 with
the bearer alone (no cookie). It is on chatgpt.com, so it goes through the proxy; the URL cannot be handed to a client.
Fixture `attachment_download` (synthetic values, real key set).

## F7. Hiding is deleting
`PATCH /backend-api/conversation/{id} {"is_visible": false}` → 200 `{"success":true}`; afterwards the conversation and
its attachment downloads answer **404 "Conversation has been deleted"**, and `is_visible: true` also gives 404. The
bytes must be downloaded before any hide (run t1's image was lost this way). Fixture `conversation_deleted_404`
(synthetic body).

## F8. Quota is unreadable on Plus
`conversation/init` → `limits_progress[{feature_name, remaining, reset_after}]`: `image_gen` said `remaining: 120`
before and after all 8 images, `reset_after` always now + 24 h. It does not count these generations; the relay needs
its own per-account count and must treat the upstream's limit answer (not observed) as the signal. No refusal was
observed either (one real-person probe was generated).
