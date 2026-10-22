# meta.ai live spike, 23/10/2026 (redacted)

Captured on the box in the `browser` image (Chrome 154, Xvfb), with two real accounts, each through its own German
proxy. They were redacted on the box: headers are reduced to content-type, query values are replaced by `x` (except
`stp`, `_nc_cat`, `_nc_ht`, `ccb`), and long tokens become `<tokN>`. Digit runs of 9 or more are replaced by stable
`N…` fakes. Cookie values, proxy credentials, display names and viewer ids were scanned for and gave 0 hits. Times are
seconds from Enter.

Each folder can hold:
- `http.json`: non-static HTTP requests (GraphQL `doc_id` + `variables` + response body, rupload, fbcdn media).
- `ws.json`: the gateway WebSocket frames (`wss://gateway.meta.ai/ws/clippy`), as printable strings of the binary
  frames.
- `timeline.json`: a one-second poll of URL, stop button, `data-streaming-state`, image tiles and videos.
- `*.state.json`: the visible test ids, sign-in signals and page state.
- `*.dom.html`: the assistant message and composer DOM, with classes and svg stripped.
- `reply.txt`, `composer.json`, `file_inputs.json`, `cookies_after.json` (names only), `send.json`, `fetch_log.jsonl`.

The folders:
- `load-acc1`, `load-acc2`: page load only. Signed in (user menu visible).
- `image-1-acc1`, `image-1-acc2`: "A watercolor painting of a lighthouse at sunset" gave 1 image, 1920×1280.
- `image-2x169-acc1`, `image-2x169-acc2`: "Create 2 images of a red bicycle, 16:9" gave 2 images, 2048×1152.
- `i2v-acc2`: a generated image (webp) was uploaded with "Animate this image into a short video: gentle waves". The
  result was an mp4 of 10 s, 1152×768, h264 24 fps, with AAC audio.
- `i2v-upload-stall-acc1`: a 5 MB PNG whose rupload POST never answered. Enter was ignored and nothing was sent.
- `checkpoint-acc1`: a later load of acc1 got 403 with meta's `?challenge=3` page, then "Vercel Security Checkpoint —
  Failed to verify your browser, Code 11".
