Gemini step-0 spike, 2026-10-23, signed out (see ../FINDINGS.md). Redacted: conversation and response ids (`<id>`),
the per-reply token (`<token>`), and the geo block (cut).

- `probe_001_probe.json`: `/app` with the Flow keeper's jar: 200, no `SNlM0e` (signed out). The gemini export gave the same.
- `anon_001_anon_page.json`: `/app` with no cookie: the page ids present today (`bl`, `f.sid`; no `at`).
- `anon_003_stream_text_signed_out.json`: one signed-out text StreamGenerate (the references' 69-slot inner): 200.
- `anon_004_stream_image_signed_out.txt`: one signed-out image prompt: 200, a text refusal, no image.
