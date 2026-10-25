Gemini live probe, 2026-10-25 ~05:45Z, read only, inside the production worker-gemini container: the AI Pro account's
video job of 25/10 that failed `render.download_failed` (04:46Z), its `/gg/` link resolved again with the relay's own
`Site.resolve` (no rotation), then the googlevideo.com link it gave fetched through the account's proxy by a fresh
client with no cookie, once per User-Agent (see ../FINDINGS.md section 7).

- `googlevideo_user_agent.json`: the link's shape (query keys only, no values but `rqh`) and each try's status and
  response headers. The link carries `eaua` and `rqh=1`: it answered only the User-Agent that resolved it (the
  provider's desktop Chrome) 200 video/mp4; httpx's default, curl's and a bare `Mozilla/5.0` got 403 text/plain, 0 B.
- `portrait_and_image_modes.json` (06:05-06:29Z, jobs through the relay with a scratch api and worker on the
  production database, the session rotated only by the relay): six t2i sends with the mode id and kind each used and
  the image model the answer named, and the two t2v sends with the portrait option and what came back (FINDINGS 7, 8).
  Numbers and labels only: no link, id or token.
