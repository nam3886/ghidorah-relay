# vibes.ai pinned modes, live smoke (2026-10-25, 16:51–17:03 GMT+7 / 09:51–10:03Z)

One job of each mode that works on an output of the account's own earlier job, sent by the relay's own engine code
(`relay_vibes.engine.submit` / `batch`, run from the LAN box in a one-off container of the worker image), account
vibes-acc1 through its production proxy. Nothing was written to the database (the harness logged `report_ref` and
`mark_sent`); jobs were spaced by the 5 min send gap and met no checkpoint.

| Files | Mode | Source | What came back |
|-------|------|--------|----------------|
| `extend_*` | extend (`midjen-extend`) | the S6 image-to-video result (batch `e2b1e259…`, 464x832, 5.21 s) | generate 200 `success, needsPolling` in 3.3 s (submit 19.4 s with the source read); done on the read at 322 s; download H.264 464x832, **9.04 s** (+3.83 s), AAC, 3.98 MB |
| `v2v_*` | video edit (`midjen-video-edit`, `generationType: v2v`) | the extend result above (an extend of an extend's source chain) | done at 326 s; download H.264 464x830, 9.04 s, AAC, 4.29 MB; a pencil-sketch restyle of the same scene |
| `edit_*` | image edit (`POST /api/generate/image-edit`) | production job 030eb966 (a `vibes-image`, batch `c3f9d392…`) | 200 `{success, contentItem, batchId, requestId}` in 11.7 s; `GET /api/download/png?id=<contentItem.id>` 200 `image/png` 810x1440, 1.76 MB |

`edit_001_read_source.json` is the source batch read of an earlier probe (16:40 GMT+7, same account and source), given
the recorded-call shape. Each file is one call: `t`, `elapsed_s`, `method`, `path`, `status`, `headers` (content type
and checkpoint headers only), `request`, `body` (none for a download). Redacted with `scripts/redact-fixtures.py`'s
`Redactor` (user ids replaced, signed query values x'd, keys kept); `leaks()` and the secret scan found 0.
