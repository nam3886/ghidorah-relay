# Google Flow (flow.google.com) — step-0 spike findings (2026-10-23)

Plain HTTP (`curl_cffi`, Chrome TLS) from the LAN box through one Canadian exit proxy, account acc1. Fixtures:
`fixtures/flow/spike-261023/` (auth probes, the first session loss) and `fixtures/flow/live-261023/` (live run).
Times GMT+7 (UTC). Result: 3 images and 1 Veo 3.1 Lite video generated over plain HTTP; 27 submits refused at the
reCAPTCHA check (no charge). No browser is needed except, possibly, for reCAPTCHA (see 3).

## 1. Auth and session
- `GET /` (or `/project/<id>`) → Angular app HTML. Signed in ⇔ `SNlM0e` (`at`) present; also `FdrFJe` (`f.sid`, new per
  load), `cfb2h` (`bl`), `oPEP7c` (email). Signed out still answers 200. No bearer, no token exchange; TLS impersonation
  not required (plain `requests` worked).
- Cookies: the .google.com session set (SID, __Secure-1/3PSID, HSID, SSID, APISID, SAPISID, __Secure-1/3PAPISID,
  __Secure-1/3PSIDTS, SIDCC, __Secure-1/3PSIDCC, NID, AEC) + host-only OSID/__Secure-OSID on flow.google.com.
- `__Secure-1PSIDTS` ages out in ~20 min unless rotated. First export (12:26): signed in 12:31, signed out by 12:51;
  myaccount → ServiceLogin, RotateCookies → 401 (too late). Second export (13:34, same SID, new PSIDTS): our own
  `POST https://accounts.google.com/RotateCookies` (body `[000,"-0000000000000000000"]`, Content-Type application/json,
  Origin accounts.google.com, via the proxy) → 200 + Set-Cookie __Secure-1/3PSIDTS, SIDCC, __Secure-1/3PSIDCC.
  Rotating every 10 min kept the session signed in for the whole 60-min watch (13:35–14:36); page loads also refresh
  the SIDCC trio. The provider must own the jar and rotate it (≤10 min), writing every Set-Cookie back.
- The exporting browser was a normal (not incognito) profile, closed right after the export: only we use the session.
- No "Confirm it's you" seen. Never sign out: the account is shared with Gemini.

## 2. Transport
`POST /_/AiSandboxAngularFrontend/data/batchexecute?rpcids=<id>&source-path=<path>&bl=<bl>&f.sid=<sid>&hl=en&_reqid=<n>&rt=c`,
form `f.req=[[["<id>","<args json>",null,"generic"]]]&at=<at>&`, headers X-Same-Domain: 1, Origin/Referer
flow.google.com. No x-goog-ext header. Reply `)]}'` frames; result `["wrb.fr",id,"<payload json>",null,null,<err>,"generic"]`.
A failure is HTTP 200 with payload null and `item[5] = [code, null, [["type.googleapis.com/google.rpc.ErrorInfo",[reason]]]]`
(3 INVALID_ARGUMENT, 4/5 not found, 7 PERMISSION_DENIED, 8 RESOURCE_EXHAUSTED). Reads 0.3–1.5 s.

| rpcid | method | args that worked |
|---|---|---|
| nzlxg | GetCredits | `[]` → `[balance,3,8,1,null,balance,[refresh_s,ns]]` |
| jHPbke | CreateProject (free) | `["projects/*",[null,[title]],[null,22]]` → `[uuid,[title]]` |
| HTrJv | GetModels | `[]` → catalogue with per-tier costs |
| Zzl0ze | GetProjectContents | `["projects/<id>",null,null,null,[1]]` (without `[1]` no media; `[<id>]` → error 3) |
| as29s | GetMedia | `[<media id>]` → record with fresh signed URLs (`["media/<id>"]` → 5) |
| jwpduf | video status | `[null,null,[[<media id>]]]` |
| ogiZ0b | BatchGenerateImages | see 4 |
| YhhmEf | video from text | see 5 |

Others (from the JS): maseQ UploadImage, MZZa6b/eb1hJf/nprQif video from references/start/start+end, CK0Pk cancel.

## 3. reCAPTCHA (the blocker for reliability)
- Enterprise, key `6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV`; actions IMAGE_GENERATION, VIDEO_GENERATION, UPLOAD_IMAGE
  (+AUDIO/TEXT/CHAT/APP_GENERATION). Token goes in the client context
  `[null,22,null,null,null,<project>,null,null,null,null,[<token>,1]]`.
- A refusal is immediate (0.4–0.5 s): `[7,…,"PUBLIC_ERROR_UNUSUAL_ACTIVITY"]`, once `[8,…,"…_TOO_MUCH_TRAFFIC"]`.
  Nothing is charged or listed: safe to retry with a new token.
- YesCaptcha `RecaptchaV3TaskProxylessM1` (isEnterprise, pageAction) solves in 10–17 s for 25 points. Accepted 4 of
  20: rapid back-to-back tries kept being refused; after a 12-min pause the next image and then the video (1 min
  later) were accepted on the first try. `M1S9` was no better (1 try, refused). Sending the solver's userAgent made
  no difference.
- Tokens minted in Chrome on the project page (headless, headless with the UA fixed, headed under xvfb; Chrome via
  the repo's ProxyForwarder, which is needed for Chrome to reach Google through this proxy) were all refused, and so
  was the same body sent by fetch() from inside the real page. So the refusals are about the token or the account/IP
  risk, not our HTTP client.

## 4. Images (`ogiZ0b`)
- args: `[null,[entry…],1,<ctx>,[<batch uuid>]]`; entry =
  `[null,null,<refs|null>,<seed int>,<aspect>,<model>,null,<ctx>,[[["<prompt>"]]],null,null,null,<wf uuid>,<media uuid>]`.
  aspect: 1 1:1, 2 9:16, 3 16:9, 4 3:4, 5 4:3. Models: BELUGA (Nano Banana 2.1), GEM_PIX_2 (Pro), HARBOR_SEAL (2 Lite).
- The reply is held until the image is done: 13.5–16.6 s. It carries the server media id and workflow id, the seed,
  the prompt, 1376×768 (16:9) and a signed `https://flow-content.google/image/<media id>?Expires&KeyName&Signature`
  URL. The JPEG was ~75–80 kB; a signed URL works without cookies; as29s re-signs it later.
- Ids: the media/workflow uuids we send are ignored (the server assigns its own); the batch uuid is echoed and appears
  in the Zzl0ze workflow record → reconcile key.
- Cost: 0 credits (balance 50 → 50).

## 5. Video (`YhhmEf` → `jwpduf` → `as29s`)
- args: `[[[[null,null,[[["<prompt>"]]]],"veo_3_1_t2v_lite",2,null,[null,null,null,null,<media uuid>,<wf uuid>]]],<ctx>,[<batch uuid>,1]]`.
- Submit reply in 2.6 s: `[null,<new credit balance>,[workflow record incl. batch id],[[media record, status [6]]]]`.
  Status 6 → 2 (running, by +3 s) → 3 (done, between +36 s and +46 s). At 3 jwpduf/Zzl0ze carry the byte size but
  no URL; `as29s [<media id>]` gives the poster `flow-content.google/image/<id>` (JPEG) and the
  `flow-content.google/video/<id>` mp4.
- File: H.264 1280×720, 24 fps, 8.000 s, AAC 48 kHz stereo, 8,468,655 bytes (downloaded in 2.1 s).
- Cost: 10 credits (50 → 40); the submit reply already shows 40. In the catalogue, Lite 8 s costs 10 on tiers 1/2 and
  5 on tier 3; the 4 s/6 s Lite keys are offered only on tier 3.

## 6. Reconcile
Zzl0ze (`[1]` option) lists workflows with title, primary media id, batch id and status. A video is listed about 14 s
after submit (status 2). An image is not listed while its reply is held, nor ~0.4 s after it, but is listed about a
minute later. After a lost reply: match the batch id; "not listed yet" is unknown, not "not charged".

## What an HTTP-first provider needs
1. A per-account cookie jar it owns and rotates (≤10 min RotateCookies; write every Set-Cookie back).
2. One page load per session for at/f.sid/bl; one free project per account.
3. A token source with a good accept rate: YesCaptcha at a low submit rate, with retries on the immediate 7/8 refusal
   (safe), spaced out; measure the accept rate per account and per IP before production. Browser mint was refused here.
4. The batch uuid written ahead before submit; Zzl0ze + jwpduf + as29s for status/reconcile; nzlxg (or the video
   reply's balance) for cost.

## Open questions
- Which token source and spacing give a usable accept rate; whether refusals are tied to the account, the IP or the rate.
- Token lifetime (tokens were used within seconds of minting).
- Image-to-image/upload (UPLOAD_IMAGE token), count > 1 per entry, other ratios for video (2 gave 16:9 here).
