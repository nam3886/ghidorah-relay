# Gemini web (gemini.google.com) — step-0 spike findings (2026-10-23, PARTIAL: no signed-in session)

Plain HTTP (httpx from the relay image `relay-worker-flow:0a2ee77`) from the LAN box through one exit proxy (line 14 of
the box proxy list, the one Flow used). Scripts: `~/relay-gemini-spike` on the box. Fixtures (redacted: conversation
ids, the geo block and tokens cut): `fixtures/gemini/spike-261023/`. Times UTC (GMT+7 in brackets).

**Status: the live sends could not be made.** The only Google AI Pro session (the account Flow uses) is signed out, and
no other signed-in Google session exists on the box. Nothing was sent on the account; nothing was spent. What follows
splits what was measured today (signed out) from what the mechanism references say (not verified today).

## 1. The session is gone (measured)
- The Flow keeper (`~/relay-flow-spike/keeper_forever.py`, container `dazzling_pare`) kept the session signed in from
  07:35 to 11:25:30Z (14:35–18:25), rotating every 10 min (last good RotateCookies 11:20:27Z, 200). At its next tick,
  11:30:31Z (18:30), the page was already signed out BEFORE the rotation was tried, and that rotation answered 401.
  Every tick since is signed out; the keeper no longer rotates (it stops after a 401) but still re-saves the jar.
- So the session was ended on Google's side between 11:25:30 and 11:30:31Z, 10 min after a good rotation: not an
  age-out. Cause unknown (owner sign-out or password change, a browser using the same session, or Google's own
  revocation). Nothing on the box touched the jar in that window (only keeper ticks were written).
- `gemini.google.com/app` with the keeper jar: 200, no `SNlM0e` (signed out). With the gemini export
  (`relay-secrets-gemini/gemini-cookies-acc1.json`, exported 05:26Z, the same account): signed out too
  (`probe_001_probe.json`).
- **Needed to go on: a fresh Cookie-Editor export of the AI Pro account** (gemini.google.com, normal profile, browser
  closed right after), and the keeper restarted on it (it does not rotate again by itself after its 401). The spike
  then reads the keeper's jar at each use and never rotates (one rotator per session).

## 2. Transport today (measured, signed out)
- Page `GET /app` → 200, ~830 kB. Signed in ⇔ `SNlM0e` (`at`). Always present: `FdrFJe` (`f.sid`), `cfb2h` (`bl`,
  today `boq_gemini-web-uiserver_20261022.15_p0`).
- `POST /_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate?bl&f.sid&hl=en&_reqid&rt=c`, form
  `f.req=[null,"<inner json>"]&at=<at>`, headers X-Same-Domain 1, Origin/Referer gemini.google.com. The references'
  69-slot "inner" (prompt at `[0]`, language `[1]`, request uuid `[59]`) is still accepted: 200 in ~13 s.
- Reply: `)]}'` then length-prefixed frames of `["wrb.fr",null,"<json>"]`.
  - Frame 1 (first bytes): `[null,[cid,rid],{"18":rid,"21":[<token>],"44":true}]`. **The conversation id (`c_…`) and
    response id (`r_…`) come from the server in the first frame**, before any content.
  - Frame 2: `[null,[cid,rid],null,null,[[rcid,[<text>],…]], …]`: candidate `rc_…` with the reply text.
  - The request uuid we send at `inner[59]` does NOT appear in the reply (`rid_echoed: false`).
- Signed out, an image prompt is answered with TEXT and HTTP 200, no error code: "I can try to find an image like that
  for you, but can't create it right now. It's possible you're signed out or image creation isn't available in your
  location." (`anon_004_stream_image_signed_out.txt`). A refusal is a plain text reply, not an error envelope.

## 3. What the references say (NOT verified today)
From the old c2tool gemini code and reports (`c2tool/gemini2api`, `c2tool/gemini_reports`,
`c2tool/docs/gemini2api-runbook.md`, July–September 2026). Mechanism only; the build-dependent flags drift.
- **Image (t2i)**: the same StreamGenerate, prompt asks for an image; the reply is held until drawn (synchronous). The
  image is in candidate `[12][7]…` as `[_, _, filename, url, _, mime, [w, h, size]]`. Download: the `lh3…/gg-dl/`
  URL + `?authuser=0&alr=yes` answers text/plain with the next URL (gg-dl → work.fife → lh3 rd-gg-dl → bytes);
  a bare gg-dl is 403.
- **Image input (i2i/i2v)**: `POST https://content-push.googleapis.com/upload` multipart, headers
  `Push-ID: feeds/mcudyrk2a4khkz`, `X-Tenant-Id: bard-storage`; the body answer is a file id; message content becomes
  `[prompt, 0, null, [[[<file id>], <name>]], null, null, 0]`.
- **Video (Veo, t2v)**: no separate model; "video mode" sets `inner[49]=11, [54]=[], [55]=[[16]]`, a nested tool flag
  in the message content (`[9][6] = [[null,null,null,1]]`), a 99-slot inner with build-current flags (`[79]`, `[96]`…)
  and `x-goog-ext-525001261-jspb` capability headers (without them a paid account got "upgrade your subscription").
  The stream answers "generating your video" + `video_gen_chip`, then stays open; the video is async.
  - Status: `batchexecute` rpc `hNvQHb` (read conversation) `[cid,10,null,1,[0],[4],null,1]`, source-path
    `/app/<cid>`; the video appears under a dict key `"60"` in candidate `[12][…]`; the downloadable URL is
    `contribution.usercontent.google.com/download?c=…` (needs the session cookies). `BardErrorInfo 1167` = not ready.
  - Refusals arrive as text: "I can't create more videos for you today" (daily cap), a public-figure block, an
    "upgrade your subscription" answer; `BardErrorInfo 1155` = video tool flag missing (our bug).
  - Measured in July: Veo, 10 s, 1280×720, H.264, ~2.6 MB; "video slow (minutes), rate-limit strict".
- **Quota**: `jSf9Qc` (source-path `/usage`, needs the full jar) → `[tier, [[remaining, used_fraction, window,
  [[reset_epoch]]] …]]`; window 1 = 5 h, 2 = week. The daily video cap is not in it (seen only as the refusal text).
- **Account**: `otAQ7b` (source-path `/app`) → `body[14]` null = available, else LOCATION_REJECTED /
  ACCOUNT_REJECTED / ACCESS_TEMPORARILY_UNAVAILABLE.
- **Conversations**: `MaZiqc` lists chats, `hNvQHb` reads one.
- **Session**: the same `RotateCookies` as Flow; two rotators on one session made each other 401 (seen live in July).

## 4. Write-ahead id and reconcile (analysis; to prove live)
- Server ids (`cid`, `rid`) arrive in the first frame, so a reply cut AFTER the first frame is reconciled by `cid`
  (`hNvQHb`). A reply cut BEFORE it (or a timeout with no byte) leaves no server id.
- No client id we send is echoed (signed-out text test). Candidate write-ahead ids to test live: the request uuid
  `inner[59]`, the 32-hex client message id `inner[4]` (video mode), the `x-goog-ext-525005358-jspb` uuid. If none
  shows in `MaZiqc`/`hNvQHb`, the write-ahead id is a **tracking code inside the sent text** (the core frame's
  `{{tracking_code}}`, as dola and meta do): `MaZiqc` lists recent chats, `hNvQHb` reads the user turn, the code
  matches the job. A new chat per job keeps the match to one conversation.
- After a cut stream: `hNvQHb` by `cid` (image: the drawn image in the candidate; video: the `"60"` slot, `1167`
  pending, the refusal texts terminal).

## 5. Live plan once a session exists (each kind once, never re-sent)
1. Page + `otAQ7b` + `jSf9Qc` (free): signed in, tier, the 5 h/week windows.
2. t2i: one image; record frames with timings, `cid` arrival time, which client ids show in `MaZiqc`/`hNvQHb`; download
   via the gg-dl chain through the proxy; measure px.
3. i2i: one upload + one edit; px.
4. t2v: one Veo; cut our read after the chip; `hNvQHb` every 30 s until the `"60"` slot; download; ffprobe.
5. i2v: one upload + one Veo; ffprobe.
6. `jSf9Qc` again: cost per kind; whether the video cap shows anywhere.
7. Ratio and length: whether the prompt text or a request field sets them (the references know no field).

## 6. Shared Google session (proposal for the owner, not built)
Flow and Gemini use one Google account. A relay account row belongs to one provider (`accounts.provider`, unique
`(provider, identity)`), and each row's jar is rotated by its own worker. Two rows holding copies of one session would
be two rotators: the first rotation makes the other's `__Secure-1PSIDTS` stale (401 in July). Options:
- **A. Two rows, one stored session (recommended).** A gemini row may be *linked* to a flow row: it has no jar of its
  own; reads and `renew_credential` go to the owner row's credential (same compare-and-swap and merge). A rotation is
  done by whichever worker finds it due, under a Postgres advisory lock on the owner row (`pg_try_advisory_lock`;
  a worker that does not get it skips the rotation and re-reads the jar). Either provider's checks keep the session
  alive; one stopped worker does not kill it. Pools, strikes, budgets, proxy stay per row. Cost: a core migration
  (`accounts.credential_of uuid NULL`), the link in admin import, and both providers' `Site` using the lock. Tier L.
- B. One row, one provider ("google" serving Flow and Gemini models in one worker). One rotator by construction, but it
  merges two providers (settings, pools, caps, captcha pacing) and renames flow. Not recommended.
- C. A different Google account per provider. No code; the owner needs a second AI Pro account. Good default when
  there is one; A is for sharing.
- D. Flow's worker is the only rotator, gemini only reads. Rejected: gemini dies 20 min after the flow worker stops.

## Open questions
- Who/what ended the session at 11:25–11:30Z (18:25–18:30 GMT+7)? If Google revokes sessions that are rotated from a
  server, a shared session dies for both providers at once.
- Everything in 3–5 above, live.
