# dola provider findings

## HTTP send spike 23/10

Question: can the dola provider send jobs over plain HTTP instead of driving dola.com in Chrome? **Yes.** On 23/10
(13:34–13:44 GMT+7) the LAN box sent text-to-image (Seedream 4.5) and text-to-video (Seedance 2.0, 5 s) orders to
`POST /chat/completion` with curl_cffi `impersonate="chrome"` through the account's proxy, signed with an `a_bogus` made
by a pure-Python signer, and read the conversation, the images and the video master back over HTTP. No browser, no
Node. Account d23-05 (out of production for the spike), German exit proxy. Redacted captures:
`fixtures/dola/api-spike-261023/` (its README lists each file).

Spend: 3 paid jobs (2 images of 4 pictures each, one 5 s video that dola said came from "your daily free credits").
3 more sends were refused by dola before any conversation existed (bad signature, no signature, replay).

### What did not work: the browser capture

The plan was to capture the page's own `/chat/completion` first. On this account's web UI it never fired: there is no
"Create Images" chip row (`enter_image_mode` fails with "could not find the 'Create Images' button"), and with text
typed the send arrow stays disabled, so Enter and a click send nothing (four loads, two with the request fenced by a
CDP `Fetch` pause instead of `page.route`, which is known to break dola's send). The production composer cannot serve
this account's UI today; HTTP could. The body was built from dola's IM SDK bundle (`s1-im-sdk-shell.*.js`, the
`client_meta`/`messages`/`option`/`chat_ability`/`user_context`/`ext` builder) and the `chat_ability` recorded on real
jobs' user messages (`fixtures/dola/chains/image_done-*`, `video_t2v-*`). dola accepted it as is.

### a_bogus: pure Python is enough

`a_bogus` comes from ByteDance's `bdms` SDK (`bdms-sdk.*.js`), which wraps `fetch`/`XMLHttpRequest`. Its format was
decoded and re-implemented in Python (SM3 + a modified RC4 + custom base64; `abogus.py` on the box, ~200 lines, stdlib
only), checked against 93 signatures dola's own page made (`signatures_decoded.json`): all have header magic `3,82`,
SDK version `1.0.1.0`, and bind the exact query string, the exact body and the User-Agent; 92 of 93 pass the internal
XOR checksum (the 93rd still matches all three bindings: a decoder edge in the noise padding, not another format).

One signature holds:
- the inputs, 3 bytes each: `SM3(SM3(query + "dhzx"))` (the query exactly as sent, without `a_bogus`),
  `SM3(SM3(body + "dhzx"))` (exact body bytes), `SM3(base64_s3(rc4'(key=[env>>8, env&255, detect], UA)))`;
- dola's constants: aid 495671, page_id 26930; screen string `1440|900|1448|1031|1440|900|1440|900|Linux x86_64`;
  env 1; detect 14 on a page's first calls, 8 after (NR tag `1,1,1,0` instead of `0,0,0,0`); NR flags 1; tripwire 3;
  call bucket 6; 3–4 ms since SDK init; the clock in ms, `ink = now - 1`, tail `"<(now+3) & 255>,"`.
- `a_bogus` is always the last query parameter (200 characters for this screen string).

Ours (detect 8, NR tag `1,1,1,0`, that screen string, the box Chrome 154 UA as the `user-agent` header) decodes like
the page's own. The Node `bdms` daemon is not needed. Risk: a new `bdms` version may change the format (watch the
file name `bdms-sdk.a68c472ba3.js` and the version block).

### Is the signature checked?

| Call | no `a_bogus` | corrupted `a_bogus` | good `a_bogus` |
|------|--------------|---------------------|----------------|
| `/alice/profile/self_brief`, `/alice/user/launch` | `code 0` | `code 0` | `code 0` |
| `/chat/completion` | 200 SSE, `STREAM_ERROR` 710022004 "rate limited" + `shark_admin` decision (`type verify`, `subtype slide`); no ACK, no conversation | 200 SSE, `STREAM_ERROR` 710022002 "high demand"; no ACK, no conversation | ACK, job runs |

The send is checked, reads are not. The good-signature control went through within a minute of the refusals, same
account and proxy: the refusals were the signature, not load. **dola reports a bad signature with its throttle codes.**
Today the engine reads 710022002/710022004 as a throttled account and rests it (the owner's ruling ends a job at the
first 710022002 with a refund, which is right: no conversation exists; replaced 25/10: every refusal moves the job
until `dola.refusal_max_accounts` accounts refused it). Over HTTP a signer bug would look like every
account throttled at once; the provider needs its own signal (the `verify` decision on 710022004, both codes on every
account at once).

### The send, exactly

`POST https://www.dola.com/chat/completion?<query>&a_bogus=<sig>`
- Query in order: `version_code=20800, language=en, device_platform=web, doubao_device_platform=web, aid=495671,
  real_aid=495671, pkg_type=release_version, device_id=, pc_version=3.42.0, doubao_pc_version=3.42.0, web_id=<W>,
  tea_uuid=<W>, region=VN, sys_region=VN, samantha_web=1, web_platform=browser, use-olympus-account=1,
  web_tab_id=<uuid4>, tz_name=Asia/Ho_Chi_Minh`, then `a_bogus`. `<W>` from unsigned `POST /alice/user/get_web_anon_id`
  (empty region/sys_region, no web_id/tea_uuid; `code 0`). `msToken` only as a cookie.
- Headers: `accept: text/event-stream`, `content-type: application/json`, `agw-js-conv: str`, origin/referer
  dola.com[/chat], the Chrome 154 Linux UA + matching `sec-ch-ua*`, `sec-fetch-*` same-origin cors,
  `accept-language: en-US`, the whole cookie jar.
- Body (compact JSON = what is hashed): `client_meta {local_conversation_id: "local_<16 digits>", conversation_id: "",
  bot_id: <assistant_bot_id from POST /alice/user/launch>, last_section_id: "", last_message_index: null,
  local_permissions: []}`; `messages [{local_message_id: <uuid>, content_block: [text block 10000], message_status: 0}]`;
  `option` (SDK list: create_time_ms, collect_id, unique_key, need_create_conversation true, conversation_init_option
  {need_ack_conversation: true}, sse_recv_event_options {support_chunk_delta: true}, recovery_option, model_config,
  aggregate_params, false flags); `chat_ability` image `{"ability_type":3,"ability_param":"{\"ability_param\":{\"model\":
  \"Seedream 4.5\",\"input_box_content\":{\"user_input_content\":<p>,\"reply_message_format\":\"Generate image: %s\"}},
  \"ability_type\":1}"}` with text `Generate image: <p>`; video `{"ability_type":17,"ability_param":"{\"ratio\":\"16:9\",
  \"model\":\"seedance_v2.0\",\"duration\":5,\"input_box_content\":{\"user_input_content\":<p>}}"}` with text `<p>`;
  `user_context []`; `ext {fp: <s_v_web_id cookie>, collection_id, commerce_credit_config_enable "0",
  use_deep_think "0", sub_conv_firstmet_type "1", conversation_init_option "{\"need_ack_conversation\":true}",
  is_finish "1"}`.
- Answer (SSE): `SSE_ACK` at 1.2–6.5 s with `ack_client_meta.conversation_id`, our `local_conversation_id`,
  `section_id`, `query_list[{question_id, local_message_id, message_index}]`; then FULL_MSG_NOTIFY, DOWNLINK_CMD,
  STREAM_MSG_NOTIFY, STREAM_CHUNK patches, heartbeats every 5 s, three `SSE_REPLY_END` (end_type 1, 2, 3). Images finish
  inside the stream (17–20 s, 4 pictures); video stream closes at 10 s, cell done ~114 s later.

Results over HTTP via the engine's own read path (`fetch_chain`, `extract_image_urls`, `extract_videos`,
`master_hq_url`): image `image_ori_raw` (`ppe_image_raw` template) 2048x2048 PNG; video `main_url` (HEVC 720p, 5.04 s)
and the `get_play_info` master with `logo_type=unwatermarked` (H.264 720p). No watermark on frames or image. Video 5.09 s
as asked though the reply said "10-second duration": `ability_param` counts, the wording does not.

### Reconcile and write-ahead

- The client makes every id before the POST (`local_conversation_id`, `local_message_id`, `unique_key`, `collect_id`);
  the spike fsynced them with the text before sending.
- dola keeps `local_message_id` on the user message (`/im/chain/single`) and echoes it in `SSE_ACK`: reconcile can match
  by this exact id instead of by text.
- `POST /im/chain/recent_conv` (cmd 3200) answers over plain HTTP with `content-type: application/json; encoding=utf-8`
  (every `/im/*` call; plain `application/json` gives 712012002). Reconcile no longer needs a browser for the list.
- Final reconcile matched all 6 sends by `local_message_id`: 3 accepted own one conversation each, 3 refused own none.

### Idempotency

The first send's exact body (same `local_conversation_id`, `local_message_id`, `unique_key`), freshly signed, 6.5 min
later: heartbeats 37 s, then `STREAM_ERROR 710020702 "system error"`, `extra.is_replay: "true"`; no ACK, no new
conversation, the original still holds 2 messages and 4 pictures. Which id is the key and how long it holds: not measured.

### Money-safety answers

- Mark sent before the POST (unchanged); write-ahead = ids + text before the first byte.
- `SSE_ACK` with `conversation_id` = taken. HTTP 200 with `STREAM_ERROR` and no `SSE_ACK` = not taken (no conversation,
  checked each time). A drop or timeout before `SSE_ACK` = unclear, to reconcile by `local_message_id`, never re-sent.
- A replay with the same ids is refused: a safety net, not a licence; "no re-send after mark_sent" stays.
- Signature failures arrive as 710022002/710022004: no conversation, no charge, account not actually throttled.
- Only credit text seen: the video's "This generation uses your daily free credits"; dola reports no image counter.

### Open questions

- 30 s video over HTTP by sending `duration: 30` with `seedance_v2.5` (no action-bar patch): not tried.
- The replay check's key and window.
- Reference images (i2i/i2v: `/alice/resource/prepare_upload` + imagex + attachment block 10052): not tried.
- Follow-up send into an existing conversation (`conversation_id` set, `need_create_conversation: false`): not tried.
- Risk scoring for a fleet that never loads dola pages (fixed fingerprint, no `/webid`, no telemetry); the `slide`
  decision is the captcha a flagged account would meet.
- Image ratio/style over HTTP (none sent: square default).
- Why the kit's Chrome cannot send on this account's new UI (matters only if the browser path stays).

## HTTP video live check 24/10

Question: which video orders deliver exactly what was asked over the HTTP send? Account d23-05 (disabled in
production, checked with `ro.sh` before the run), its production proxy, the production image `relay-worker-dola:f170653`
(its own `submit` + `poll` + `fetch`, `browser_only` bypassed for the probe), 24/10 07:06–07:16 GMT+7, after dola's
07:00 daily reset. One send per row, none re-sent; every delivered file measured with `ffprobe`. Prompt: "a paper kite
rising over green rice fields, slow tilt up. Generate it now as a single video in one go. Do not ask questions.
[marker]".

| # | Form | Model | Asked | dola | Measured |
|---|------|-------|-------|------|----------|
| 1 | page ("Generated video: <p>, 9:16" + `reply_message_format`, `ability_param.ratio` 9:16) | Seedance 2.0 | 5 s 9:16 | ACK; "will be generated using the Seedance 2.0 model in 1-3 minutes ... daily free credits", then ~2 min later "I can't generate the video and didn't use any credit for this generation." (`ai_creation_res_code` 710082023) | no video (relay: `dola.content_refused`) |
| 2 | bare (the engine's) | Seedance 2.0 | 10 s 16:9 | ACK; "Dreamina Seedance 2.0 Fast model in 5 minutes ... daily free credits"; "Your video is ready." | H.264 1280x720, 10.04 s (AAC 10.08 s) |
| 3 | bare | Seedance 1.0 | 5 s 16:9 | at once "I can't generate the requested content. Try something else." | no video |
| 4 | page | Seedance 2.0 | 5 s 1:1 | "You've reached the daily free usage limit for Video generation." | not taken (`pool.account_out_of_points`) |
| – | page | Seedance 2.0 | 5 s 21:9 | not sent: the day's limit | – |

So the account's daily video budget was 2 credits (row 2 at 2 credits; rows 1 and 3 charged nothing).

Routing (`send_http.PROVEN_VIDEO`): Seedance 2.0 at 10 s 16:9 joins 5 s 16:9 on HTTP. Every other ratio, Seedance 1.0
and 2.5, and 2.0 15 s stay on the browser. Not known: whether row 1 failed for the page form, the 9:16 ratio over
HTTP, or dola's own refusal of this prompt (the same prompt in the bare form at 16:9 delivered); row 3's model id may
not be offered to this account over HTTP (the browser path names 1.0 by its chip label). A next live day should try
the bare form with the ratio in the text ("<p>, 9:16") and 1:1/21:9 before any non-16:9 video moves to HTTP.

## v2v spike 24/10

Question: can a customer give dola a VIDEO and get a new video back? **Yes in the web app (video as a reference for
Seedance), not proven live.** Account d23-05 (already disabled, the spike account), its production proxy. Run
23/10 21:25–21:50 GMT+7. No paid send.

**Where in the UI.** The *Create Videos* composer (skill 17, `ability_type 17`) has an upload button
(`VideoActionUploadFileToolButtonPlugin`, test id `image-creation-chat-input-picture-reference-button`) that opens a
file picker built on the fly (no `input[type=file]` in the DOM until the click). Its `accept` comes from the selected
model: images `.jpg,.png,.jpeg,.webp,.apng`, video `.mp4,.mov,video/mp4,video/quicktime`, audio `.mp3,.wav`
(chunk `async-infra-input-tool-menu-option`, module `qSz`). Also a canvas "edit video" path: an attachment whose
`from` is `canvas_video_edit` is reported as `video_edit` (`s2-canvas-video-generation-composer`), and the AB keys
`video_post_processing.is_show_video_edit` / `video_edit_model` exist. So the web app's video input is a
*reference* to a Seedance generation (restyle/remix by prompt), not a separate edit endpoint.

**Request shape (from the bundle, chat.6745dd6c02 + async chunks fetched through the account proxy).**
- Upload: not imagex. A media uploader with `useResourcePrepareUpload: true`, `resourcePrepareUploadParams:
  {tenant_id: "3", scene_id: "46", resource_type: Video}` (audio: scene 47). The result is a VOD-style `vid`.
- The send is the ordinary `/chat/completion` with `chat_ability` 17 (`ratio`, `model`, `duration`, `resolution`,
  `input_box_content`), plus one attachment per media, built by the composer:
  `{type: AttachmentTypeVideo, identifier: <localKey>, media: {vid, duration, size, name, media_type: video|audio,
  md5?, resource_uri?, path?, cover?}, parse_state, review_state, upload_status: success, progress: 100, src}`.
  Images keep the known image attachment.
- Duration of the input is read client-side from the MP4 `moov/mvhd` box.

**Limits.** `get_item_conf` (read, replayed over plain HTTP, 200) carries per-model `ref_media_info`:
`ref_media_type 5`, `max_ref_video_count 1`, `max_ref_image_count 10`, `max_ref_video_duration 15` (seconds),
`ref_user_avatar_mode 1` (seen on `seedance_v2.0` and `ic_mini`). Formats mp4/mov. Price with a reference video: not
measured.

**Why no live proof.** On d23-05 the kit's Chrome still renders a bare composer: no *Create Videos* button
(`enter_video_mode`: "could not find the 'Create Videos' button"), `/chat/create-image` (the *AI Creation* link)
renders an empty panel, `/chat/create-video` is "Page not found", and there is no upload button to click (three
loads, completion fenced by a route abort; nothing reached `/chat/completion`). The HTTP path would need the
resource prepare-upload + VOD upload chain for the `vid`, which is not built. Open: capture that chain from a
working browser profile, then one paid send with a ≤15 s mp4 and `ffprobe` the result.

## Plan (25/10)
The chat page asks `POST /alice/commerce/sale/subscription/entry/config/` (plain query, body `{}`; no `a_bogus`
needed). Two free accounts answered `subs_status: "free"`, `has_active_subscription: false`,
`membership_display_name: ""` (`fixtures/dola/subscription_entry_config_free.json`). The bundle has a membership
service and a subscription modal; no paid account exists, so a paid `subs_status` is unknown and is not written.
