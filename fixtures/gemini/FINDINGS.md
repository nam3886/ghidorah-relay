# Gemini web (gemini.google.com) — step-0 spike findings (2026-10-23/24)

Plain HTTP (httpx from the relay image) from the LAN box through one exit proxy (box proxy list, index 15), with the
gemini session the owner exported for this provider alone (`relay-secrets-gemini/gemini-cookies-acc1-2026-10-24.json`;
the spike was its only rotator, every 10 min, the rotated jar written back 0600). Times UTC (GMT+7 = +7 h). Fixtures,
redacted (conversation ids replaced, geo cut, signed links and tokens replaced, other chats' titles hidden):
`live-261023/` (the live run) and `spike-261023/` (the first, signed-out probe, 23/10 when the old session was dead).

## Results

| kind | sent | model | output measured |
|---|---|---|---|
| t2i, ratio 16:9 | 1 | Nano Banana 2 (mode "Auto") | PNG 1408×768, 1,178,053 B; answer 26.3 s |
| t2i, no ratio | 1 | Nano Banana 2 | PNG 1408×768, 1,863,550 B; 28.0 s (gemini picked 16:9) |
| i2i (edit, the t2i PNG in) | 1 + upload | Nano Banana 2 | PNG 1408×768, 1,410,987 B; 25.4 s (the edit applied) |
| t2v | 1 | Veo tool (inner[49] = 11) | **refused**: "If you upgrade your subscription, I can create that video for you today." |
| i2v | 0 | — | not sent: the account is not entitled to video (t2v answer above) |

**The exported session is a FREE Gemini plan, not AI Pro.** The jar holds four signed-in Google accounts (`/u/0`..`/u/3`);
on every one the mode menu shows "3.1 Pro" locked (`upsell_MODE_CATEGORY_PRO`), an "Upgrade" button, no video tool,
and `jSf9Qc` tier 1 (5 h window 600, week 12096, nothing used). Video (t2v/i2v) needs a session of an account whose
Gemini plan includes Veo; the provider ships images only until that is measured.

## 1. Auth and session
- `GET /app` → 200; signed in ⇔ `SNlM0e` (`at`); `FdrFJe` (`f.sid`) and `cfb2h` (`bl`, `boq_gemini-web-uiserver_20261022.15_p0`)
  always there; `oPEP7c` the email. `/u/N/app` and `/u/N/_/...` address the Nth account of a multi-account jar.
- `POST https://accounts.google.com/RotateCookies` (as Flow) → 200 + new `__Secure-1PSIDTS` pair and the SIDCC trio. Rotated
  every 10 min from 15:33Z on, signed in throughout.
- 23/10: the previous session (shared with Flow) was ended by Google between 11:25 and 11:30Z, 10 min after a good
  rotation; the owner then exported one independent session per provider (owner ruling: each worker rotates only its own).

## 2. The send (`StreamGenerate`)
- `POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?bl&f.sid&hl&_reqid&rt=c`, form
  `f.req=[null,"<inner json>"]&at=<at>`, headers `x-goog-ext-525001261-jspb` (mode id, capability list, request uuid),
  `x-goog-ext-525005358-jspb` `[uuid,1]`, `x-goog-ext-73010989-jspb` `[0]`, `x-goog-ext-73010990-jspb` `[0,0,0]`.
- The inner is the web app's 99-slot list, captured in a browser with the request aborted before it left
  (`web_request_*.json`): prompt at `[0][0]`, `[4]` a 32-hex message id, `[49]` the tool (14 image, 11 video, 0 none),
  `[55]` the tool option (image ratio ids 61 1:1, 59 9:16, 60 3:4, 62 4:3, 63 16:9; video 16 16:9, 17 9:16; from
  `otAQ7b`), the ratio also in the message at `[0][9][6] = [null,[null,"16:9"]]`, `[59]` the request uuid.
  The page puts an anti-bot token (`!…`) at `[3]`; sends WITHOUT it were accepted (all four above).
- Answer: `)]}'`, then length lines and frames; nothing arrives for ~11-12 s, then the ids frame
  `[null,[cid,rid],{...}]`, then cumulative content frames, and a complete answer ALWAYS ends with the end item
  `[["e",<frames>,null,null,<bytes>]]` (every send and read). The image record sits under the candidate's `[12]`:
  `[null,1,<file>,"https://lh3.googleusercontent.com/gg-dl/…",null,<token>,…,"image/png",…,[w,h,bytes]]`.
- A refusal is a complete answer with a final text and no image record (no error code): the not-entitled video answer;
  signed out, "can't create it right now … signed out or … location".
- No client id we send (request uuid, message id) is echoed anywhere (reply, chat read, chat list).

## 3. Image input and download
- Upload (free): `POST https://push.clients6.google.com/upload/` with `Push-ID: feeds/mcudyrk2a4khkz`,
  `X-Tenant-Id: bard-storage`, `X-Goog-Upload-Command: start` (+ length, `resumable`) → `x-goog-upload-url`; the bytes
  to it with `upload, finalize` → body `/contrib_service/ttl_1d/…`. The send names it at `[0][3]`:
  `[[[path,1,null,mime,<uuid>],name,null,…,[0]]]`.
- Download: the `/gg-dl/` link (or `/gg/` from a chat read) + `=d-I?alr=yes` answers text/plain with the next link:
  lh3 → work.fife.usercontent.google.com or lh3.google.com (these need the session cookies; without them the last hop is
  403) → lh3 with the PNG (no cookie needed). Without `=d-I` the bytes are a 512 px preview; `=s0`/`=d` a re-encoded
  full size. The `contribution.usercontent` link of the record is a protobuf (601 kB), not the image.
- The PNG carries Gemini's visible sparkle watermark in the corner (file name `watermarked_img_…`).

## 4. Write-ahead id, reconcile, cut streams
- `MaZiqc` `[13,null,[0,null,1]]` lists the newest 13 chats `[cid,title,…,[secs,nanos],…]`; the time is when the send
  started (all four: 1 s after our POST). A finished chat is listed 0.6 s after its answer ends (the not-entitled video
  answer too).
- `hNvQHb` `[cid,10,null,1,[0],[4],null,1]` (source-path `/app/<cid without c_>`) reads a chat: the prompt as sent at
  `turn[2][0][0]`, the answer with the image record (a `/gg/` link) at `turn[3]`.
- A tracking code typed into the prompt is DRAWN INTO THE IMAGE ("[7134]" painted on the duck, `t2i_default_ratio_*`):
  the prompt must go verbatim. The write-ahead key is therefore the job's exact sent text and its send time: a chat
  started near `sent_at` whose prompt is that text is the job's. The account sends one job at a time.
- A send whose read was cut before the first byte (3 s) and one cut right after the ids frame (9 s) left NO chat in
  4.5 min of listing (`cut_summary.json`, `cutcid_summary.json`): closing the stream seems to cancel the turn. Not proof;
  the provider still treats a cut send as unknown and never sends it again.

## 5. Quota and account reads
- `jSf9Qc` (source-path `/usage`, full jar): `[tier,[[remaining,used_fraction,window,[[reset]]]…],…]`; window 1 = 5 h (600),
  2 = week (12096); tier 1 here. The images above did not move it measurably in the spike's reads (not re-read after).
- `otAQ7b` (`/app`): `[14]` null = available; the mode list and the tool options (image ratio tool 248, video tool 185
  ratios, "Video gallery").

## Open questions
- Video: needs a session whose Gemini plan includes Veo (the send shape reaches Veo; status, result link and length
  are unmeasured).
- The free plan's daily image limit and its refusal text; rate limits per account.
- Whether a cut stream can ever still produce an image (two cuts produced none).
