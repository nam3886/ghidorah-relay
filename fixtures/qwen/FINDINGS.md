# chat.qwen.ai — live spike findings (2026-10-23)

Step 0 for a `qwen` provider: learn the protocol and find out how much of it plain HTTP can do. No provider code yet.
Everything ran on the LAN box with two real free accounts (acc1, acc2), each through its own Canadian proxy (Toronto
PoP). Plain HTTP was `requests` (on the box) or `httpx` (inside the worker image). The browser was the
`relay-worker-dola` image (Chrome 154 on Xvfb, patchright). The redacted calls are in
`fixtures/qwen/spike-261023/`. Times are GMT+7. Web version `0.3.22`.

## Jobs sent (whole spike)
The earlier run that was stopped sent no generate. Its one browser capture aborted the completion inside the browser,
and the chats it made read back empty. The owner then lifted the budget. Every job below was sent from httpx:

| When (GMT+7) | Acc | Kind | Outcome |
|---|---|---|---|
| 13:31 | acc1 | t2i, `qwen-image-3.0`, 16:9 | 1 PNG, 1664x928, 10.7 MB; the stream took 92.5 s |
| 13:40 | acc2 | image edit (t2i + 1 image) | 1 PNG, 1664x928; the edit was correct (red bicycle turned blue); the stream took 108 s |
| 13:43 | acc1 | t2v, HappyHorse 1.1, 720P, 5 s, 16:9 | 30 credits taken at submit; mp4 1280x720, 24 fps, h264 + AAC, 5.16 s; ready about 10.5 min later |
| 13:57 | acc2 | i2v, same settings, the edit input as the first frame | 30 credits; ready about 9.5 min later |
| 13:58 | acc1 | t2i (bx test S1) | the peer cut the stream, so the outcome was **unclear**; never re-sent; read back: assistant `done: false`, no content, still the same about 1.5 h later |
| 14:16–14:18 | acc1 | t2i ×2 (bx test T1, T2) | both made an image |
| 14:32–15:03 | acc1 | t2i ×3 (bx age test U1–U3) | all made an image (§3) |
| 14:34 | acc1 | t2v with 10 credits left | the server refused it: no task, no charge (§7) |

## 1. Auth (the 20/10 change)
- The jar carries a `refresh_token` cookie on `.qwen.ai`. It is a JWT with `type: refresh_token` and a 30-day `exp`.
  There is no `token` cookie.
- `GET https://auth.qwen.ai/api/v2/auths/refresh` works over plain HTTP with the jar and no `bx-*`. It needs the
  headers `Version: 0.3.22`, `Timezone: <JS Date string>`, `source: web` and
  `x-request-origin: https://chat.qwen.ai`, with Origin and Referer set to chat.qwen.ai. It answers 200 in about
  1.5 s with:
  - `data.access_token`: a JWT with `type: access_token`, valid for **15 min**;
  - `data.refresh_token`: **the same value as the cookie** (no rotation in 8+ refreshes);
  - the user: `id`, `email`, `name`, `role`, …

  The answer sets only the `acw_tc` cookie.
- Every API call then takes `Authorization: Bearer <access_token>`. The provider refreshes each account's token over
  plain HTTP before the 15 minutes run out.

## 2. What the Aliyun WAF checks
- Plain HTTP works everywhere **except `POST /api/v2/chat/completions`**. These all answered normally without `bx-*`:
  auth refresh, whoami, models, credits pricing, credits verify, credits history, member level, chats
  list/new/read, `getstsToken` and task status.
- Completions without `bx-ua`/`bx-umidtoken` answer HTTP 200 with JSON
  `{"ret": ["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试"], "data": {"url": ".../_____tmd_____/punish?x5secdata=...&action=captcha"}}`.
  This happens with or without the jar's `ssxmod_itna*`. A `fetch`/XHR made inside the page but not signed by the
  page's baxia gets the HTML captcha page instead (`<meta name="aliyun_waf_aa">`). Nothing is generated in either case.
- `bx-ua`, `bx-umidtoken` and `bx-v: 2.5.37` come from Alibaba's baxia/AWSC script. The page loads it as
  `o.alicdn.com/frontend-lib/common-lib/jquery.min.js captchaType=qwen`, and loads `AWSC/awsc.js` +
  `baxia-entry/baxiaCommon.js` later as needed. It signs only the front end's own requests to paths on its protected
  list (completions, chats, `/api/v2/task`, `getstsToken`, files, …). Our own in-page XHR/fetch did not get signed.
- **httpx can send a completion that the page signed.** The page builds the request and we abort it inside the
  browser. httpx then sends it through the same proxy with the same headers (`bx-*`, the browser UA and client hints)
  and the browser's cookies. The generate runs. All four kinds (t2i, edit, t2v, i2v) were sent this way.
- **The `bx-*` pair is not bound to the body, and it can be reused.** One pair, minted from one aborted page send,
  was used by httpx for other prompts in other chats (those chats were made over plain HTTP). Every send generated,
  at 1 s, 78 s, 3 s, 15 min and 30 min after minting (§3).
- One `bx-*` pair failed: it came from an earlier browser session and was sent with the jar cookies and an
  empty-messages body. It got `FAIL_SYS_USER_VALIDATE`. What the pair is tied to (the browser's cookies such as
  `cna`/`isg`/`tfstk`, its UA, the exit IP) was not isolated. Send the browser's cookies and UA with the pair.

So the browser is needed for one thing only: minting the `bx-ua`/`bx-umidtoken` pair. It loads chat.qwen.ai with the
jar, lets the page send one completion, aborts that send inside the browser, and keeps its headers and cookies.
Everything else, the generate included, is plain HTTP.

## 3. bx-* lifetime and reuse
- One pair (T run): T1 at 1 s and T2 at 78 s; both generated.
- One pair (U run): U1 at 3 s, U2 at 15 min, U3 at 30 min; all generated. So one pair lasted at least 30 min and
  3 sends.
- The first T run's third send, at 15 min, failed at `chats/new` before any completion went out. The access token
  copied from the browser had expired (15 min). The U run refreshes the token over plain HTTP before each send.
- Untested: past 30 min, and more than 3 sends per pair.

## 4. Flow: chat_id first
1. `POST /api/v2/chats/new` (plain HTTP, free) with `{"chatId": "", "models": ["qwen3.7-plus"], "project_id": "",
   "timestamp": <ms>, "chat_type": "t2i"|"t2v"|"i2v", "chat_mode": "normal"}` returns `data.id`. The new chat reads
   back with `messages: []`. This id is the write-ahead key: one chat per job.
2. A free pre-check is available: `POST /api/v2/entitlement/credits/verify` with
   `{"chat_type": "t2v", "task_info": {"create_credits": 30}}` returns `access: true`, or `access: false` with
   `code: "CREDITS_EMPTY"` and a paywall card. Without `task_info` it answers 400 `ENTITLEMENT_INVALID_ARGUMENT`.
3. `POST /api/v2/chat/completions?chat_id=<id>` with `bx-*`. This is the only paid call.
4. Images stream the result in that answer. Videos answer at once with a task id; then poll
   `GET /api/v2/task/status/<task_id>` over plain HTTP.
5. Reconcile at any time with `GET /api/v2/chats/<id>`.

**The server does not echo our message ids.** The front end puts random UUIDs in `messages[0].fid` and
`childrenIds[0]`, but the server stores its own user message id and assistant/response id. So the only write-ahead id
we own is the `chat_id`, made before the send. To reconcile, read the chat: no user message means not sent; a user
message means sent.

## 5. Paid request shapes (as the page builds them)
All kinds share this body:

```
{"stream": <bool>, "version": "2.1", "incremental_output": true, "chatId": <id>, "parentId": "", "chat_id": <id>,
 "chat_mode": "normal", "model": "qwen3.7-plus", "parent_id": null, "timestamp": <s>, "size": "16:9",
 "messages": [{"id": null, "fid": <uuid>, "parentId": null, "childrenIds": [<uuid>], "role": "user",
   "content": <prompt>, "user_action": "chat", "files": [...], "timestamp": <s>, "models": ["qwen3.7-plus"],
   "model": "", "chat_type": ..., "feature_config": {"thinking_enabled": false, "output_schema": "phase",
   "research_mode": "normal", "auto_thinking": false, "thinking_mode": "Fast", "auto_search": true},
   "extra": {"meta": ...}, "sub_chat_type": ..., "parent_id": null}]}
```

Headers: `Authorization`, `bx-ua`, `bx-umidtoken`, `bx-v`, `Version: 0.3.22`, `source: web`, `Timezone`,
`x-request-id`, `x-accel-buffering: no`, `Content-Type`/`Accept: application/json`, and the browser UA +
`sec-ch-ua*`.

- **t2i**: `stream: true`, `chat_type: "t2i"`, `sub_chat_type: "t2i"`,
  `extra.meta: {"subChatType": "t2i", "size": "16:9", "model": "qwen-image-3.0"}`.
- **Image edit**: the same t2i body with one entry in `files`. The front end no longer sends
  `chat_type: "image_edit"` (the bundle still has the name). The entry is
  `{"type": "image", "file": {...}, "id": <file_id>, "url": <OSS signed GET, x-oss-expires=300>, "name",
  "file_type": "image/jpeg", "file_class": "vision", "showType": "image", "status": "uploaded",
  "greenNet": "success", "size", "itemId", "uploadTaskId"}`. The chat stores it rewritten to
  `cdn.qwenlm.ai/<user>/<file_id>_<name>?key=`.
- **t2v**: `stream: false`, `chat_type: "t2v"`, `sub_chat_type: "zaodian_generate_video"`, and
  `extra.meta: {"size": "16:9", "subChatType": "zaodian_generate_video", "videoGenerationMode": "standard",
  "biz_data": "{\"bizScene\":\"genVideo\",\"req\":{\"rootModel\":\"happyhorse11\",\"genMode\":\"vid_gen\",\"params\":{\"resolution\":\"720P\",\"size\":\"16:9\",\"duration\":5,\"audio\":true}}}"}`.
- **i2v**: the t2v body with `chat_type: "i2v"`, one image in `files`, and
  `params.attachments: [{"type": "image", "materialId": <file_id>}]` inside `biz_data`.
- **Upload** (free): `POST /api/v2/files/getstsToken` with `{"filename", "filesize": "<bytes>", "filetype": "image"}`
  returns STS credentials (`access_key_id`, `access_key_secret`, `security_token`), `file_id`,
  `file_path` `<user>/<file_id>_<name>`, `bucketname: qwen-webui-prod`, `region: oss-(Singapore region)` and
  `endpoint: oss-accelerate.aliyuncs.com`. **It works over plain HTTP without `bx-*`**, even though the page signs it.
  The front end then PUTs the file to OSS with the ali-oss SDK and an OSS-signed `Authorization`. That PUT was not
  replayed over plain HTTP in this spike.

## 6. Answers, task status, timings, result URLs
- **t2i/edit answer (SSE):**
  1. `response.created {chat_id, parent_id, response_id}` at about 1.7 s;
  2. `response.info {action: keep_alive}` every 10 s;
  3. one `choices[0].delta` with `content: <png url>`, `phase: "image_gen"`, `extra.output_image_hw: [["928","1664"]]`,
     `extra.model` and `status: "typing"`;
  4. a last delta with `status: "finished"`.

  End to end: 60–108 s.
- **The image is made only while the stream stays connected.** The peer cut S1's stream. Its assistant message stayed
  `done: false` with no content and was unchanged about 1.5 h later. So a lost image stream will not deliver later:
  read the chat, and if it shows `done: false` with no content after the stream died, the job failed.
- **t2v/i2v answer (JSON, 3.8 s):** `data.messages[0]` holds:
  - `content_list[0].content` = "Sure, I'm generating your video… consuming 30 Credits. You currently have 10 Credits
    remaining…";
  - `extra.wanx {task_id: "tsk_…", status: "processing", model: "happyhorse-1.1-t2v"|"happyhorse-1.1-i2v"}`;
  - `extra.quota {consume_amount: 30, entitlement: {deducted: true, record_id, transaction_id, chat_id, message_id},
    state: "deducted"}`.

  **Credits are taken at submit.**
- **Task status:** the path is `GET /api/v2/task/status/<task_id>`, with singular `task`. The plural
  `/api/v2/tasks/status/…` answers `not found`, and `/api/v1/tasks/status/…` answers 404. While running it answers
  `data.task_status: "running"` with the quota and `remaining_time`. `remaining_time` counts down from about 86400,
  so it is not an ETA. When done it answers `"success"` with `content: <mp4 url>`. Each poll takes about 0.5 s over
  plain HTTP. t2v took 10.5 min and i2v 9.5 min.
- After success, the chat's assistant message has `content: <mp4 url>`, `done: true` and
  `extra.wanx.status: "finish"`.
- **Result URLs** look like
  `https://cdn.qwenlm.ai/output/<user_id>/<t2i|t2v|i2v>/<response_id>/<name>.<png|mp4>?key=<JWT>`:
  - The `key` JWT holds `resource_chat_id`, `resource_id` and `resource_user_id`, with **no `exp`**.
  - Responses carry `Cache-Control: max-age=604800`.
  - Without `key` the URL answers 404.

  Reading the chat always gives the URL again. Store the file, not the URL.

## 7. Quotas and errors
- **Free plan** (`memberType: free`): **40 credits a day** (`credits.init.title.daily`). Unused credits expire
  (`credits.dec.title.daily.expire`).
- **Credit balance:**
  `GET https://sg-entitlement.qwen.com/api/entitlement/credits/credits/history?pageNum=1&pageSize=20&biz_id=ai_qwen&chat_client=h5&device=pc&fr=pc&pr=qwen-i18n&la=en-US&tz=UTC&wv=0.3.22`
  (Bearer, plain HTTP) returns `totalCredits` and a `usageList` with titles such as
  `credits.dec.HappyHorse1.1.720P.5s.video`.
- **Price list,** `GET /api/v2/credits/pricing`:
  - Images: 1 credit, but **no credit was taken for 8 images** (t2i and edit, both accounts).
  - Video (5/10/15 s): HappyHorse 1.1 720P 30/60/90; HappyHorse 1.0 720P 40/80/120; Wan 2.7 720P 20/40/60 and
    1080P 30/60/90.
  - HappyHorse 1080P is `kind: purchase` (paid plans only).
- At default settings, a free account gets one 5 s HappyHorse 1.1 video a day (30 of its 40 credits).
- **Not enough credits:** the page does not send (its verify call says `CREDITS_EMPTY`). Sent anyway from httpx, it
  answers HTTP 200 `success: true` with one assistant message:
  `content: "Insufficient Credits\nThis generation requires 30 Credits; 10 Credits are currently available…"`,
  `extra: {update_member: true, quota_limit: true}`, and no `wanx`. Nothing is charged.
- WAF answers: see §2.
- Member level: `POST https://sg-member.qwen.com/api/member/level/query?<same query>` returns `memberType`. The same
  path on chat.qwen.ai answers 500.
- Not met in this spike: an image daily limit, a content refusal, and `RateLimited` (the reference repos parse
  `data.code: "RateLimited"` with `num` hours).

## 8. Models, sizes and durations today
- `GET /api/v2/models/` lists the chat models: `qwen3.7-plus` (the default; its `chat_type` list includes `t2i`,
  `image_edit` and `t2v`), `qwen3.8-max`, `qwen3.7-max`, `qwen3.6-plus`, `qwen3.5-plus`, and omni models. The
  generate sends `model: "qwen3.7-plus"`; the media model goes in `extra.meta`.
- **Image models** (from the bundle):
  - `qwen-image-3.0`: the default, default ratio 16:9;
  - `qwen-image-3.0-pro`: VIP only (`requiredMemberType: pro`), adds `auto`;
  - `qwen-image-2.0-pro`: legacy.

  Ratios: `3:4, 9:16, 2:3, 1:1, 3:2, 4:3, 16:9`. 16:9 gave 1664x928.
- **Video** (from the bundle):
  - Models: `happyhorse-1.1` (the default, `rootModel: happyhorse11`), `happyhorse-1.0` (`happyhorse`) and `wan-2.7`
    (`wan27`).
  - Resolutions: `720P` (default) and `1080P`.
  - Durations: `5, 10, 15`.
  - Ratios: `16:9` (default), `4:3, 1:1, 3:4, 9:16, original` (`origin` on the wire).
  - Modes: `standard`, `multi-reference` (HappyHorse) and `first-last-frame` (Wan).
  - `audio: true` is sent.

## What an HTTP-first provider needs
- Per account: the jar (`refresh_token` and the browser cookies) and its proxy. Refresh the access token over plain
  HTTP at least every 15 min.
- A small browser step per account that mints the `bx-ua`/`bx-umidtoken` pair, plus the cookies and UA that go with
  it: load chat.qwen.ai with the jar, let the page send one t2i, abort it inside the browser.
  - Reuse the pair across sends; one lasted at least 30 min and 3 sends (§3).
  - Mint a new pair when an answer is `FAIL_SYS_USER_VALIDATE`. That answer is never a sent generate.
- Plain HTTP covers: chats/new, credits verify, completions (with the pair), the image SSE, task status, chat read,
  credits history and getstsToken. The OSS PUT needs an ali-oss style signer (or the browser).
- Money safety:
  - Write the `chat_id` ahead (chats/new is free) and send exactly one completion into it, never a second.
  - A WAF answer (`ret` with `FAIL_SYS_USER_VALIDATE`, or the HTML captcha page) means nothing was sent. It is safe
    to mint a new pair and send again into the same chat, after checking that the chat is still empty.
  - Any other unclear answer: read the chat.
    - No user message: not sent.
    - A user message on an image job: keep reading the open stream. If the stream died and the chat shows
      `done: false` with no content, the job failed: refund it.
    - A user message on a video job: the assistant message carries `extra.wanx.task_id` and `extra.quota`; poll the
      task status.
  - Videos are charged at submit (`quota.state: "deducted"`). Images took no credits today.
  - Check video credits first with the free verify call. An "Insufficient Credits" answer has no `wanx` and charges
    nothing.

## Provider build, 23/10 evening (21:41-21:50 GMT+7): both refresh tokens revoked
The provider's first live runs (relay_qwen engine in the `relay-worker-dola:0a2ee77` image, each account through its
own proxy) proved the plain-HTTP session and nothing paid:
- acc1, 21:41: refresh OK, credits read (`totalCredits` 10, so the account check's video balance works); then the
  first browser mint (`open_session`, warm profile, cookies added, `?inputFeature=t2i&text=…`, Enter) saw no generate
  in 30 s. From the next run on (21:43) every refresh of acc1 answers `200 {"success": false, "data": {"code":
  "Unauthorized", "details": "Token is revoked, please login again."}}`.
- acc2, about 21:48: one plain refresh (python `requests`) answered `success: true`. About 21:49, before any browser
  ran for acc2, the next refresh answered the same "Token is revoked".
- With a revoked token the page loads as a guest (an age-check modal, a "Log in" button) and calls only
  `auths/refresh` and `auths/` (both `Unauthorized`); it did not call `auths/signout`.

The cause is not isolated: one account was revoked right after a browser session, the other with none. Until it is,
the mint fails the page's `/api/v2/auths/signout` and `/api/v2/users/logout/*` in the browser and writes the browser's
cookies back even when it fails. New jars are needed for any further live run.

## Open questions
- What revoked both refresh tokens on 23/10 evening (above), and how to keep a session alive.
- How long a `bx-*` pair lasts beyond 30 min and 3 sends, and what it is tied to (cookies, UA, exit IP). Can one pair
  serve an account through another proxy?
- A failed or refused video: its `task_status` value, and whether the credits come back (none seen).
- The free accounts' daily image limit (none met in 8 images).
- The OSS PUT over plain HTTP with the STS (not tried).
- Does a lost image stream ever finish later (S1 still `done: false` after 1.5 h), and does it count against any quota?
- `RateLimited` and content-refusal answers: not met.

## v2v spike 24/10

Question: can a customer give chat.qwen.ai a VIDEO and get a new video back? **No in the web app (0.3.22, still the
live version on 23/10 21:25 GMT+7); the backend price list knows video edit/extend.** No account used, no send.

- **Web app.** The video-generation composer (`t2v`) uploads images only: `Mf[VideoGeneration]` accepts
  `JPG, JPEG, PNG, BMP, WEBP`, and the request builder keeps only `type === "image"` files as
  `params.attachments [{type: "image", materialId}]`. Modes: `standard` (1 image), `multi-reference` (up to 9 images,
  `genMode: "multi_ref"`), `first-last-frame` (2 images, Wan only); `genMode` is only `vid_gen` or `multi_ref`. The
  chat types are `t2i, t2v, i2v, image_edit, …`; nothing takes a video as a generation input (a video upload in a
  normal chat is understanding, not generation). The Wan 2.7 card reads "Flexible video editing", but its modes are
  `standard` and `first-last-frame`.
- **Backend price list** (`GET /api/v2/credits/pricing`, `credits_pricing.json`) has scenes the web never builds:
  `hh11Vide720P` 视频编辑 (video edit) HappyHorse 1.1, 30/60/90 credits for 5/10/15 s; `hhVide720P` HappyHorse 1.0
  40/80/120; `wan27Vide720P` / `wan27Ve720P` video edit Wan 2.7 20/40/60; `wan27Vc720P` 视频续写 (extend) Wan 2.7
  20/40/60; `wan27ActImit720P` 动作模仿 (motion imitation) 20/40/60; 1080P doubles, HappyHorse 1080P paid only.
  The `genMode` strings and attachment shape for these are not in the bundle, so a send would be a blind guess at
  30 credits of a 40-credit day. Not sent.

Revisit when a web version adds a video `attachments` type or a `genMode` beyond `vid_gen`/`multi_ref`.
