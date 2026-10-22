# vibes.ai S5 live spike (2026-10-23)

These are redacted calls to the vibes.ai JSON API. They were made over plain HTTP (Python `requests`) from the LAN box, through one German exit proxy, with one account (acc1, created 2026-10-22). The run used one text-to-image job and one text-to-video job (480p, `midjen-short`). The analysis is in `packages/provider-vibes/FINDINGS.md`.

Files are named `<stage>_<seq>_<step>.json`. Each record holds `t` (seconds since the stage started), `elapsed_s`, `method`, `path`, `request`, `status`, `headers` and `body`.

| Stage | When (UTC) | Calls |
|-------|------------|-------|
| `auth_*` | 17:48:28 | `GET /api/auth/me`, `GET /api/quota/upsell` |
| `image_*` | 17:48:42 | create empty batch, GET empty batch, `POST /api/generate/images`, GET batch, `GET /api/download/png` |
| `video_*` | 17:49:21 | create empty batch, GET empty batch, `POST /api/generate/videos` with 2 s polls while in flight, then 5 s polls (cap 12 min), download attempts |
| `recheck_*` | 18:08:51 | one GET of the video batch, `GET /api/download/video`, `GET /api/quota/upsell` |

- `video_timeline.json` lists every batch state the poller saw, with timestamps. It includes the moments the generate POST was sent and returned, and all 84 of the 403 responses.
- `log_*.json` hold the per-stage call lists (t, elapsed, status).
- 82 identical 403 poll records were dropped. The first one (`video_041`) and the last one (`video_124`) are kept, and the timeline still has all of them.

How the files were redacted:
- Headers are reduced to `Content-Type`, `Content-Length`, `Retry-After` and `X-Vercel-Mitigated`.
- User, profile and session ids and the username are replaced with placeholders such as `<user.id>`. They also appear inside `requestId`.
- The query string of every URL, of any scheme, is replaced by `?<redacted-query>`.
- `oh`, `oe`, `_nc_*`, `efg`, `vs`, `ccb`, `token`, `sig`, `session` and `Authorization` parameters are blanked wherever they appear.
- The path of the profile-picture URL is replaced.
- HTML bodies (the Vercel checkpoint page) are reduced to their `<title>` and length.

Before the files were copied off the box, they were scanned for the cookie values, the proxy host, user and password, and the user ids. The scan found 0 hits. Binaries (PNG, MP4) and raw responses stay on the box in `~/relay-vibes-spike/raw/`.
