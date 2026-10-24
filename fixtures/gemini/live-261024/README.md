Gemini live run, 2026-10-24 00:5x-01:2xZ, the same gemini session (rotated only by the keeper), its AI Pro account at
`/u/4`, proxy index 15 (see ../FINDINGS.md sections 6 and 7). Redacted as live-261023 (ids to fixed fakes, signed links
and tokens replaced, the email by owner@example.com, the owner's other chats' titles by `<another chat>`).

- `t2v_accepted_reply.*`: one t2v (16:9, mode Flash): the "I'm generating your video" answer.
- `t2v_pending_read_hNvQHb.txt`, `t2v_done_read_hNvQHb.txt`: its chat while generating, and with the video record.
- `i2v_accepted_reply.*`, `i2v_pending_read_hNvQHb.txt`, `i2v_done_read_hNvQHb.txt`: the same for an image to video
  (the 23/10 t2i PNG uploaded with `?authuser=4`).
- `i2v_error_1100_reply.*`: an earlier i2v try (upload without `authuser`): error 13 / 1100, no chat, not taken.
- `video_limit_read_hNvQHb.txt`: an older chat of the owner's: "I can't create more videos for you today, ...".
- `listing_pro_MaZiqc.txt`, `account_pro_otAQ7b.txt`, `account_pro_jSf9Qc.txt`: the account's chat list, modes and usage.
- `web_request_i2v.json`: the web app's own i2v request and upload calls, captured with the send aborted unsent.
