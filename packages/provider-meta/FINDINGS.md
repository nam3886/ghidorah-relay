# meta.ai: what a live run showed (23/10/2026)

Two accounts, each through its own proxy exiting in Germany, driven by the repo's browser image (Chrome 154).
Redacted captures: `fixtures/meta/spike-261023/` (one folder per scenario, see its README).

## Works from Germany
Text→image and image→video both work. No region block.

## Signing in
- Both accounts were signed in from their cookie jars; `[data-testid="user-menu-button"]` shows within 4–8 s.
- Real jars carry `ecto_1_sess` but no `abra_sess`; the credential check must not expect `abra_sess`.
- One account received an `rd_challenge` cookie on its first load and still worked.
- **Browser checkpoint:** the 4th launch of one account within ~15 minutes (right after a run killed mid-upload) got
  HTTP 403 with meta's `?challenge=3` page, then "Vercel Security Checkpoint — Failed to verify your browser (Code 11)";
  the URL stayed `https://www.meta.ai/`. This is neither signed in nor signed out: it needs its own state (rest the
  account, recheck later), recognised by the page title or the 403 on the document. The URL marks for `/login` and
  `checkpoint` do not catch it.

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
  nothing was charged. The engine must wait for the upload to finish (and keep input files small).
- Video URL in a WebSocket frame at 38.9 s, answer done at 47.5 s. The `<video>` src is a direct https fbcdn `.mp4`
  (not `blob:`); a plain fetch got 200 and 4.7 MB on the first try (no 503 seen).
- File: 10.0 s, 1152×768, h264 24 fps, with audio (AAC mono 24 kHz). No ask-back.

## Not seen yet
A signed-out page, limit or refusal text, ask-back text, video from text alone, a 503 on the first MP4 fetch.

## Fingerprint note
The browser timezone was Asia/Ho_Chi_Minh while the exit IP was German; meta did not object, but generated file names
carry GMT+7 times, which a fingerprint check could notice. Consider matching the timezone to the proxy.
