Gemini live spike, 2026-10-23 15:33-16:00Z, the gemini session (free plan), proxy index 15 (see ../FINDINGS.md).
Redacted: conversation/response ids replaced by fixed fakes (consistent across files), signed links by
`IMAGE<n>`/`REDACTED<n>`, opaque tokens by `<token>`, the geo block by `<geo>`/`<map>`/`<cc>`, the owner's other chats'
titles by `<another chat>`, the email by owner@example.com. Frames are re-encoded (length lines recomputed).

- `t2i_16x9_reply.*`: one t2i with ratio 16:9; `.txt` the whole StreamGenerate answer, `.json` the request sent and timings.
- `t2i_default_ratio_reply.*`: one t2i, no ratio, the prompt ending in "[7134]" (the code was drawn into the image).
- `i2i_reply.*`: one edit of the t2i PNG (uploaded first, `upload.json`).
- `t2v_not_entitled_reply.*`: one t2v with the video tool: "If you upgrade your subscription, ...".
- `t2i_read_hNvQHb.txt`, `default_ratio_read_hNvQHb.txt`: the chat reads of the two t2i sends.
- `listing_MaZiqc.txt`: the chat list before the sends (other chats only).
- `account_otAQ7b.txt`, `account_jSf9Qc.txt`: the account and usage reads.
- `cut_summary.json`, `cutcid_summary.json`: a send whose read was cut at 3 s (no byte) and right after the ids frame.
- `web_request_image_16x9.json`, `web_request_edit.json`: the web app's own requests, captured and aborted unsent.
