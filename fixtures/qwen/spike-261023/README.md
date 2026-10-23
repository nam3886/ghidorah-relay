# chat.qwen.ai live spike, 23/10/2026 (redacted)

Captured on the box with two free accounts (acc1, acc2), each through its own Canadian proxy; plain HTTP from
`requests`/`httpx`, `bx-*` minted by the page in the `relay-worker-dola` image (Chrome 154, Xvfb). Redacted on the
box: credential headers and keys (`authorization`, `cookie`, `set-cookie`, `bx-ua`, `bx-umidtoken`, `access_token`,
`refresh_token`, `email`, `birthday`, `profile_image_url`, STS keys, `file_url`, `ga-ap`) become `<redacted>`, every URL
query value becomes `x`, JWT-shaped strings become `<tok>`, the two account user ids and display names become fixed
fakes. Every jar cookie, token, email and proxy login was scanned for afterwards: 0 hits.
`fixtures/qwen/FINDINGS.md` sums them up.

Each file is one call record: `method`, `path`/`url`, `request`, `request_headers`, `status`, `headers`, `body`
(plain-HTTP calls, `t`/`wall`/`elapsed_s`) or, for the generates sent from httpx, `request`, `request_headers`,
`stream_lines` (`[seconds since send, line]`), `elapsed_s`, and `page_requests` (the page's API calls before the
aborted send, with which of `authorization`/`bx-*` each carried).

- `auth_refresh`: `GET auth.qwen.ai/api/v2/auths/refresh` with the jar, plain → access token (15 min) + the same
  refresh token.
- `models`, `credits_pricing`, `member_level` (sg-member.qwen.com), `credits_verify_ok`, `credits_verify_empty`
  (`CREDITS_EMPTY` with 10 credits left), `credits_history_acc1`/`_acc2` (after the jobs).
- `chats_new_plain`, `chat_read_empty`: a chat made over plain HTTP, read back empty.
- `waf_plain_no_bx`: completions without `bx-*` (empty `messages`, nothing could be generated) → `FAIL_SYS_USER_VALIDATE`.
- `waf_replayed_bx_other_body`: a `bx-*` pair from an earlier browser session with the jar cookies → the same WAF answer.
- `sts_plain`: `getstsToken` over plain HTTP without `bx-*` (STS values redacted).
- `t2i_send`, `t2i_chat_read`: acc1 t2i 16:9, sent from httpx with the page's headers; SSE answer.
- `edit_send`, `edit_chat_read`: acc2 image edit (t2i + one uploaded image).
- `t2v_send`, `t2v_task_running`, `t2v_task_success`, `t2v_chat_read`: acc1 t2v HappyHorse 1.1 720P 5 s.
- `i2v_send`, `i2v_task_running`, `i2v_task_success`, `i2v_chat_read`: acc2 i2v, same settings.
- `task_status_wrong_path`: `/api/v2/tasks/status/<id>` (plural) answers `not found`; the right path is `/api/v2/task/status/<id>`.
- `t2v_no_credits`: a t2v sent with 10 credits left → "Insufficient Credits" answer, no task.
- `t2i_dropped_send`, `t2i_dropped_chat_read`: a t2i whose stream the peer cut (write-ahead record only, no lines);
  the chat read about 1.5 h later still shows the assistant `done: false` with no content.
- `bx_reuse_T1`, `bx_reuse_T2`, `bx_reuse_U1`–`U3`: one `bx-*` pair from an aborted page send, reused from httpx for
  other prompts in other chats at 1 s, 78 s, 3 s, 15 min and 30 min (`bx_age_s`); all generated.
