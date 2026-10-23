# dola plain-HTTP send spike (2026-10-23, 13:34–13:44 GMT+7 / 06:34–06:44Z)

Sends to dola's `/chat/completion` over plain HTTP (curl_cffi `impersonate="chrome"`), no browser, from the LAN box,
account d23-05 (taken out of production for the spike) through its German exit proxy, signed with an `a_bogus` made by
a pure-Python signer written for the spike. `packages/provider-dola/FINDINGS.md` ("HTTP send spike 23/10") sums it up.

| File | What it is |
|------|------------|
| `signatures_decoded.json` | Every `a_bogus` dola's own page made in four page loads (92 of 93 pass the checksum), plus ours on the first send, decoded: aid, page id, flags, screen string, clock, and whether it binds the query, the body and the User-Agent. The signature values themselves are not kept. |
| `page_signatures.json` | The 92 `a_bogus` values dola's own page made (the same captures as `signatures_decoded.json`, without our send), each with the page's User-Agent and the 3 bytes it binds of the query, the body and the UA, computed on the box from the raw request with the spike's `abogus.py`. No query text is kept (each holds a tab id, `web_id`/`tea_uuid` on most), only its 3 bytes; the body is kept where it carries no account id (no `visit_id`): 84 of 92. The signer's tests (`packages/provider-dola/tests/test_signing.py`) decode every one. |
| `signed_reads.json` | `/alice/profile/self_brief` and `/alice/user/launch` sent with no, a corrupted and a good `a_bogus`: all three answer `code: 0` (reads do not check it). Status and code only. |
| `recent_conv_plain_json_content_type.json` | `/im/chain/recent_conv` sent with `content-type: application/json`: `status_code 712012002` ("unsupported encoding type"). The `/im/*` calls need `application/json; encoding=utf-8`. |
| `recent_conv_before.json`, `recent_conv_after.json` | The account's conversation list over HTTP before and after the first send (3 then 4 cells, `has_more: false`). Other conversations are cut to their id and key names. |
| `send_request.json` | The first send, text-to-image Seedream 4.5: URL (query values replaced by x's), headers (the cookie header is not kept), the write-ahead ids and the body. `ext.fp` is the `s_v_web_id` cookie value, replaced. |
| `send_sse.json` | Its SSE answer, line by line with the second it arrived: `SSE_ACK` at 2.3 s carries `conversation_id`, our `local_conversation_id` and `query_list[].local_message_id`; `SSE_REPLY_END` x3 (end_type 1, 2, 3) at 20.3 s. |
| `chain_after_send.json` | `/im/chain/single` of that conversation over HTTP: our user message with our `local_message_id` and the `chat_ability` we sent; the bot message with 4 `image_gen` / `seedream_v43_flow` creations, 2048x2048, each with an `image_ori_raw` (no watermark). |
| `badsig_request.json`, `badsig_sse.json` | The same kind of send with one character of a good `a_bogus` changed: no `SSE_ACK`, `STREAM_ERROR` `710022002` "high demand"; no conversation. |
| `nosig_sse.json` | The same send with no `a_bogus`: `STREAM_ERROR` `710022004` "rate limited" with a `shark_admin` verify/slide decision (its opaque `detail` token replaced by x's); no conversation. |
| `replay_request.json`, `replay_sse.json` | The first send's exact body again (same `local_conversation_id`, `local_message_id`, `unique_key`), freshly signed, 6.5 min later: heartbeats for 37 s, then `STREAM_ERROR` `710020702` "system error" with `extra.is_replay: "true"`; no new conversation, no new image. |
| `video_request.json`, `video_sse.json`, `video_chain_final.json` | Text-to-video Seedance 2.0, 5 s, 16:9 (`ability_type 17`): `SSE_ACK` at 6.5 s, stream ends at 10 s; the chain read 114 s later holds one 5.09 s cell with a clean `main_url`. |
| `send_results.json` | One line per send: signature, HTTP status, seconds, whether a conversation was made. |

Order of the sends (GMT+7): first image 13:34 (good signature), bad signature 13:38, none 13:39, good-signature
control image 13:40, replay of the first 13:40, video 13:42. Paid jobs: 3 (two images of 4 pictures each, one 5 s video,
"daily free credits"); the three refused sends made nothing.

Redacted with `scripts/redact-fixtures.py`'s `Redactor` (salted, same-shape fakes; URL query values x'd) plus the
account's ids found under `creator_id`/`sender_id`/`user_id`/`uid`/`visit_id`/`web_id`/`tea_uuid` as literals. Before
redaction the box scripts already replaced every cookie value and the proxy login. The raw answers, the downloaded media
and the scripts (`abogus.py`, `dolahttp.py`, `send.py`, `send2.py`, `reads.py`, `poll_video.py`) stay on the box in
`~/relay-dola-api-spike/`.
