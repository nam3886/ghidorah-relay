# meta.ai: what a live run showed (23/10/2026)

Two accounts, each through its own proxy exiting in Germany, driven by the repo's browser image (Chrome 154).
Redacted captures: `fixtures/meta/spike-261023/` (one folder per scenario, see its README).

## Works from Germany
Text→image, image→video, text→video and image→image all work. No region block.

## Signing in
- Both accounts were signed in from their cookie jars; `[data-testid="user-menu-button"]` shows within 4–8 s.
- Real jars carry `ecto_1_sess` but no `abra_sess`; the credential check must not expect `abra_sess`.
- One account received an `rd_challenge` cookie on its first load and still worked.
- **Browser checkpoint:** the 4th launch of one account within ~15 minutes (right after a run killed mid-upload) got
  HTTP 403 with meta's `?challenge=3` page, then "Vercel Security Checkpoint — Failed to verify your browser (Code 11)";
  the URL stayed `https://www.meta.ai/`. This is neither signed in nor signed out: it needs its own state (rest the
  account, recheck later), recognised by the page title or the 403 on the document. The URL marks for `/login` and
  `checkpoint` do not catch it.
  The 403 alone is not the sign: every cold load in the captures answers 403 first, POSTs `?challenge=` and reloads
  with 200 a second later. The failure sentence decides at once; the title alone only when the read gives up.
- **Cookies a run rotates (25/10, box, 3 accounts, 2 checks each; names and lengths only):** `ecto_1_sess` (the
  session itself) changes on every load, with `dpr`, `wd` and `rd_challenge`. The session is never written back: the
  page names no account (no id or handle in the HTML, graphql answers, links or Local Storage), so a rotated session
  cannot be checked against the account. Only Vercel's challenge-pass cookies are kept (`CHALLENGE_COOKIES`, added to
  the jar when missing): `rd_challenge` was seen live; `_vcrcs` is Vercel's usual name and is **not confirmed** —
  read the cookie names on the next real checkpoint and correct the list.

## The composer
- On a cold load the composer is a `<textarea>` placeholder for 7–12 s that ignores input (0.1 s on a warm load).
  Afterwards `[data-testid="composer-input"][contenteditable="true"]` matches a Lexical contenteditable div. The engine
  must wait for the contenteditable and never type into the placeholder.

## Sending and the chat id
- After Enter the URL becomes `/prompt/<uuid>` within 0.1 s. The uuid equals `conversationId` in GraphQL and is known
  before Enter (the `warmupConversation` call).
- Stop button while answering: `[data-testid="composer-stop-button"][aria-label="Stop"]`.
- Re-opening `/prompt/<uuid>` shows the whole thread (the `conversation(conversationId, count)` query returns
  `streamingState`, the markdown and the media URLs) without sending anything: this is the reconcile path.

## Done signal
`[data-testid="assistant-message"]` moves `data-streaming-state` INIT → STREAMING → DONE and ends with
`data-streaming-complete="true"`. `thinking-status` stays "in-progress" after done: not a signal.

## Network
- The prompt and the streamed answer travel over one WebSocket, `wss://gateway.meta.ai/ws/clippy`, whose URL carries
  an auth token in its query: never log that URL.
- Frames are binary, holding JSON pages of the answer, including the media URLs.
- GraphQL is `POST /api/graphql` with `{doc_id, variables}` only; the operation shows only in the response `data` key
  (`warmupConversation`, `triggerHeisenbergCheck`, `conversation`, `mediaShareMenu`, …).

## Images
- First URL in a WebSocket frame ~10 s after Enter: a low-res preview (`t58.90637-1`), then the final
  `scontent.xx.fbcdn.net/v/t39.105495-1/…_n.webp` (signed with `oh`/`oe`); it downloads through the proxy (200).
- In the page: `button[aria-label="View media"] > img[data-testid="ur-image-tile"]`, alt `gallery/image_<date>_<time>.webp`.
- A plain prompt gave 1 image (1920×1280). "2 images, 16:9" gave 2 images (2048×1152): count and ratio are honoured.
- Done 16–27 s after Enter.

## Image → video
- Upload: set the single `input[type=file]`; the upload goes GET/OPTIONS/POST to
  `rupload.meta.ai/gen_ai_document_gen_ai_tenant/<uuid>` and answers `{media_id}`; a 560 KB webp took 7.3 s.
- **Enter is ignored while the upload is pending.** With a 5 MB PNG the upload never answered: nothing was sent and
  nothing was charged. The engine must wait for the upload to finish (and keep input files small: it re-encodes an
  image over 1 MiB to a JPEG of at most 2048 px a side).
- Video URL in a WebSocket frame at 38.9 s, answer done at 47.5 s. The `<video>` src is a direct https fbcdn `.mp4`
  (not `blob:`); a plain fetch got 200 and 4.7 MB on the first try (no 503 seen).
- File: 10.0 s, 1152×768, h264 24 fps, with audio (AAC mono 24 kHz). No ask-back.

## Engine smoke, 23/10 03:09–03:13 (GMT+7)
The engine's own submit, reconcile, poll and fetch, driven on the box with stand-ins for the core (one launch each):
- Text→image (acc2): Enter 19 s after launch, done 15 s after Enter, one 1280×1920 webp (portrait for a plain
  prompt: the size is not always 1920×1280), 646 KB through the fetcher.
- Image→video (acc3) from a 14 MB 2560×3840 PNG: the browser re-encoded it to a 626 KB JPEG before meta's page loaded;
  the upload was answered 10.6 s after the file choice, the typed text survived the attach, Enter came 35 s after
  launch and the answer was done 47 s after Enter. The MP4: 10.0 s, 768×1152 (follows the input's orientation), h264
  24 fps, AAC mono 24 kHz, 5.8 MB, fetched direct on the first try (the fetcher then went direct first; since
  23/10 an account with a proxy downloads through it only).
- Reconcile of that video chat (acc3, second launch): `/prompt/<uuid>` showed the signed-in thread with the DONE answer
  and the same `.mp4` link; `Found` 21 s after launch, nothing typed or sent; the poll handed the link on.

## Text → video and image → image, 23/10 09:15–09:40 (GMT+7)
acc1 alone, through a third German proxy; a capture (one browser, two chats) then the engine's own submit for each.
Captures: `t2v-acc1`, `i2i-acc1`.
- **No video mode.** The composer's mode menu (`composer-mode-dropdown-button`) holds only "Instant" and "Thinking";
  the attach button opens the file input. A video comes from the words: "Create a short video of a red fox trotting
  through a snowy forest at dawn" gave one video and no image. A plain description draws an image (the text→image
  captures), so the engine types `VIDEO_FROM_TEXT` ("Create a short video: ") before the core's text of a
  `text_to_video` job, and nothing before any other job.
- Text→video, capture: answer streaming at 13 s, the `<video>` (direct https fbcdn `.mp4`) at 46.6 s, DONE at 48.6 s.
  MP4: 10.0 s, 1280×720 (landscape for a text prompt), h264 24 fps, AAC mono 24 kHz, 9.4 MB, fetched on the first try.
  No ask-back; the reply says "10 seconds".
- Text→video, engine ("A red fox trotting through a snowy forest at dawn [7101]", no word of video in the client
  text): `warmupConversation` named the chat before Enter as for images, Enter 23.7 s after launch, done 48 s after
  Enter, one 10.0 s 1280×720 MP4 (12.7 MB) through the fetcher.
- Image→image, capture: a 646 KB 1280×1920 webp was uploaded (rupload answered after 8.0 s) with "Edit this image: make
  it a night scene under a full moon, keep everything else the same". A low-res preview (`t58.90637-1`, alt
  "Inline image") at 16.3 s, the final `t39.105495-1` webp at 17.3 s, DONE at 22.4 s. **One image, the input's size
  (1280×1920)**; no ask-back.
- **The edit is not an image tile.** The answer shows a before/after slider
  (`role="slider"`, `aria-label="Before and after image comparison"`, "Hold to compare"): the edited image is its
  shown `img` (alt `gallery/image_<date>_<time>.webp`), the input image (an `m1/v/t0.71334-6` fbcdn link) sits under
  an `aria-hidden="true"` part of the same slider and is never a result. The engine takes as the result only a shown
  slider image with a generated image's alt (`gallery/`); an image hidden by `aria-hidden` on itself or a parent is
  never one, and any other shown image in the slider, or a preview link anywhere in the answer, keeps a done answer
  unknown. A done answer holding only media of the other kind (an image for a video job, a video for an image job)
  is unknown too, never refunded at once; only an answer with no media at all fails as `meta.no_image`/`no_video`.
- Image→image, engine (646 KB webp as is, "Make it a night scene under a full moon, keep everything else the same
  [7101]"): upload answered after 7.1 s, Enter 27.7 s after launch, done 25 s after Enter, one 1280×1920 webp (742 KB)
  through the fetcher.
- Reconcile of that edit chat (09:40, a fourth launch, read only): `/prompt/<uuid>` showed the same slider; `Found`
  5.2 s after launch with the one edited webp (the input image not among the outputs), nothing typed or sent.

## Aspect ratio from the words, 23/10 18:25–19:42 (GMT+7)
meta has no ratio control; it reads the ratio from the prompt. The production account meta-acc1 (disabled for the run,
re-enabled after) through its own proxy, driven by the engine's own submit and fetcher, one launch per order, text
alone. Bike = "A red bicycle leaning against a brick wall in a quiet street", fox = the text→video prompt above (the
engine adds "Create a short video: " before it).

| order | kind | output |
|---|---|---|
| bike`, 16:9` | image | 1 × 2048×1152 |
| bike`, 9:16` | image | 1 × 1152×2048 |
| bike`, 1:1` | image | 1 × 1600×1600 |
| bike`. Aspect ratio 9:16` | image | 1 × 1152×2048 |
| bike`, 4:3` | image | 1 × 1792×1344 |
| bike`, 3:4` | image | 1 × 1344×1792 |
| fox`, 9:16` | video | 720×1280, 10.0 s |
| fox`. Aspect ratio 9:16` | video | 720×1280, 10.0 s (the fetch timed out at 7 MB; the header was read) |
| fox`, 16:9` | video | 1st: composer never hydrated, nothing sent; 2nd: answer DONE with **no video** (`meta.no_video`) |
| fox`, 1:1` | video | answer DONE with **no video** (`meta.no_video`) |
| fox`. Aspect ratio 16:9` | video | not sent: Vercel checkpoint on this 12th launch in ~75 min |

- Every image ratio came out exact, in both written forms; a video honoured 9:16. A video asked 16:9 or 1:1 got an
  answer without a video (its text was not read); a plain text→video already comes out 1280×720.
- So the plan takes `ratio` from text alone: images 16:9, 9:16, 1:1, 4:3, 3:4; videos 9:16 and
  16:9. The engine types `, <ratio>` after the final text, except for a 16:9 video, sent with no tail since a plain
  text→video already comes out 1280×720 (ruled 23/10). Any other ratio, and a ratio with an input image (an edit
  keeps the input's size, an animation follows its orientation), is `input.bad_ratio`; a prompt naming another ratio
  is `input.prompt_ratio_conflict`.
- 12 launches of one account within ~75 min ended in the browser checkpoint (the first one came on the 4th launch in
  15 min after a killed run): pace live runs.

## Not seen yet
A signed-out page, limit or refusal text, ask-back text, a 503 on the first MP4 fetch, more than one image from an
edit.

## Fingerprint note
The browser timezone was Asia/Ho_Chi_Minh while the exit IP was German; meta did not object, but generated file names
carry GMT+7 times, which a fingerprint check could notice. Consider matching the timezone to the proxy.

## v2v spike 24/10

Question: can a customer give meta.ai a VIDEO and get a new video back? **No (web, this account, German exit).**
Account meta-acc1 (disabled for the run, re-enabled after), its production proxy, the repo's meta browser image.
Run 23/10 21:45–21:50 GMT+7, three launches, one send.

- **The composer takes a video.** `input[type=file]` accepts `video/mp4, video/quicktime` among images and documents;
  a 5.1 s 1280×720 mp4 uploaded to `rupload.meta.ai/gen_ai_document_gen_ai_tenant/<uuid>` (the same path as images)
  in 8.4 s and shows as a tile with a play icon above the editor (not inside the contenteditable, unlike an image).
- **The send:** "Restyle this video as a hand-drawn watercolor painting, keep the same motion" with the clip attached.
  DONE after 9 s, no media. The answer, verbatim: *"I can't directly restyle your attached video file yet — direct
  video-to-video editing isn't available right now."* It offered to take a still frame and animate it instead
  (that is i2v, which already exists). Chat `/prompt/7e43759d-…`. Nothing generated, so nothing charged.
- **The bundle has the pieces, unshipped here.** Attachment kinds `VIDEO_REMIX`, `VIDEO_PRESET`; trace events
  `RESTYLE`, `REMIX_CREATION`; the Media page drop handler routes a dropped video to an `"edit-video"` flow; an
  onboarding card "Extend videos — Make your videos longer with just one click". The Media page (`/media`) shows
  image templates and no video upload entry for this account. These are the Meta AI app's restyle/extend features;
  the web chat refuses them today.
- Note: the account's history already holds a chat titled "Video generation quota exhausted" from an earlier run.

Exists: no. Revisit if meta enables "edit-video" on the web (watch the answer text above and the Media page).

## Plan (25/10)
meta.ai has paid plans ("Meta One Plus", "Meta One Core", "Meta One Premium" = `PRODUCT_TIER_3`). The settings code
reads them with the persisted query `MetaSubsSettingsQuery` → `viewer.metaSubsSettingsInfo {premiumState, productName,
usagePercent, expiryTime}` (`premiumState === "SUBSCRIBED"` means paid). The settings page did not send it over HTTP
on load (no graphql POST seen; the data comes over the gateway) and a plain `/api/graphql` replay did not answer, so the
check does not read a plan yet. The user menu and `/settings` show no plan for the accounts we have.

## Browser checkpoint on production, 25/10: the send gap and the growing rest

**What production showed** (jobs and events of 25/10, GMT+7, meta-acc1, its German proxy):
- 10:32:19 and 10:32:52 sent and done; the next claim at about 10:33:20 met "Vercel Security Checkpoint — Failed to
  verify your browser (Code 11)" (`fra1`): the third page load in about 70 s.
- After the 30 min rest: 11:03:37 and 11:11:51 sent and done (8 min apart); the next at 11:13:22, 1.5 min later, met
  the checkpoint again.
- The spike (above): the 4th launch within ~15 min, and 12 launches in ~75 min, met it too.

So the trigger is page loads per account in a short window, not a daily quota: two loads pass, a third close behind
does not, whatever the jobs are.

**Why every job counts.** Each job is a fresh page load that runs Vercel's challenge: the browser profile keeps only the
caches (`reset_profile_state` removes the cookies at open and at close), and `inject_cookies` replaces the context's
cookies by the saved jar. So the challenge's pass cookie (Vercel's `_vcrcs`, not seen in our captures by name: no
network log is kept for a checkpoint) never reaches the next job, and every job solves the challenge again from the
same IP. A real browser solves it once and keeps the cookie.

**Can it be passed legitimately?** Waiting does not help: the read already polls for 30 s while the challenge runs (a
cold load passes in about 1 s), and "Failed to verify your browser" is the challenge's own verdict, not a slow pass;
reloading would be one more challenge. What would help is keeping the pass cookie between jobs: that is the job of the
branch that saves rotated cookies (dola, meta, vibes). It must keep the Vercel challenge cookie of `www.meta.ai` with
the jar, or meta keeps re-solving. This change keeps to the wait and the detection, which were right.

**The defaults:**
- `meta.send_gap_s` = 600: the least time between two jobs of one account (the claim skips an account whose
  `last_used_at` is newer; an account check loads the page too, so it stamps `last_used_at` as well). Loads 33 s and
  8 min apart passed, 1.5 min and ~5 min (the spike's 4 in 15 min) failed
  in a series; 10 min stays clear of every failing spacing seen. With 3 accounts that is 18 jobs an hour, below the
  daily pools (40 images, 20 videos per account).
- `meta.checkpoint_rest_s` = 1800 (unchanged) and `meta.checkpoint_rest_max_s` = 21600: each repeat within a day (seen
  by a check or by a submit) doubles the rest, 30 min, 1 h, 2 h, 4 h, 6 h. A quiet day starts again from 30 min.
- Live, 25/10 13:01–13:07 GMT+7 (production code, jobs spaced by hand; another agent's meta probe ran in the meta
  worker at the same time): meta-acc2, quiet since 23/10, took two jobs 5 min apart, both done. meta-acc1 (last load
  11:13) and meta-acc3 (last load 11:28) each met the checkpoint on their FIRST load after ~2 h of quiet; the job went
  back to the queue twice with no attempt used and acc2 delivered it. The run was stopped there to spare the accounts.
  So an account that met the checkpoint earlier in the day can meet it again on its first load hours later: the gap
  keeps a clean account clean, it does not clear a marked one. That is what the growing rest is for (under this
  change acc1 would now rest 1 h, acc3 1 h, instead of 30 min each), and why keeping the challenge cookie matters.
- A checkpoint is never a strike and never ends the account any more (the old 3-in-a-row `dead: checkpoint` is gone):
  the account is fine, the browser is in doubt. The job goes back to the queue with no attempt used and no charge
  (`meta.checkpoint`, unchanged).
