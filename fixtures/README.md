# Fixtures (redacted)

Made by `scripts/redact-fixtures.py` from raw spike captures. Values replaced, keys kept.

- `dola/s1-network/i2v.json` — dola page network during one send (http /im/chain/single + ws), 30/09 spike S1.
- `dola/s1-network/image.json` — dola page network during one send (http /im/chain/single + ws), 30/09 spike S1.
- `dola/s1-network/t2v.json` — dola page network during one send (http /im/chain/single + ws), 30/09 spike S1.
- `dola/chains/asked_question-e6d0abb9.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/copyright-20900298.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/copyright-840aa471.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/image_done-cebde2e8.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/multi_video-32c6fe27.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/multi_video-76d1558b.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/no_charge-40b47fe1.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/no_charge-dc7e8c18.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/partial_cell_failed-36389eee.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/refuse_then_deliver-conv719633.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/split_30s_into_15s-1e73d195.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/split_30s_into_15s-35d578d7.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/video_i2v-137e3085.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/video_i2v-9f1435d9.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/video_t2v-0369d0bd.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `dola/chains/video_t2v-32ffb8ef.json` — one /im/chain/single read of a real job's conversation, by case; box, 30/09.
- `meta/s3-network/default-2.json` — meta.ai page network for one prompt (graphql http + gateway ws), 30/09 spike S3.
- `meta/s3-network/default.json` — meta.ai page network for one prompt (graphql http + gateway ws), 30/09 spike S3.
- `meta/s3-network/n4.json` — meta.ai page network for one prompt (graphql http + gateway ws), 30/09 spike S3.
- `meta/s3-network/n8.json` — meta.ai page network for one prompt (graphql http + gateway ws), 30/09 spike S3.
- `vibes/s4-capture/capture.json` — every vibes response: auth, t2i, t2v write-ahead + resume polls, a never-generated shell; 30/09.
- `dola/listing-capped-30.json` — synthetic: a full 30-item conversation listing with one unknown conversation that does not carry the job's text (the capped-listing crash window).
- `dola/action_bar_conf.json` — the dola action-bar config response (`get_item_conf`) the 30 s patch reads, from earlier samples.
- `dola/subscription_entry_config_free.json` — dola's `/alice/commerce/sale/subscription/entry/config/` answer (POST `{}` from the chat tab), identical on two free accounts; box, 25/10. No secret in it.

## Gaps

What the captures do not hold.

- dola watermark-only: none of the 38 real done videos (30/09) lacked `main_url`. Tests build it from a done chain by deleting `main_url` from its `video_model` (derived, not captured).
- dola throttle SSE (`/chat/completion` `error_code`) and dola likeness refusal: not captured here. The SSE bodies in `dola/sse/*.txt` are reconstructed from the event names and error codes of earlier samples (not live captures); a live 710022004 body replaces them when one is measured.
- vibes error bodies (401, 403, 429, QUOTA, a rejected generate, a refused image edit) and an extend past its ~16.7 s ceiling: not captured yet.
- meta refusal, rate or limit text, text-only reply and logged-out page: not captured yet.
- gpt (chatgpt.com): the conversation listing (`GET /backend-api/conversations`), a refusal stream, a
  moderation-blocked stream, an upstream image-limit answer, a free/team/pro session, `limits_progress` with
  `remaining: 0` and an Arkose / `force_login` prepare were never seen: tests build them (synthetic). The bodies of
  `f/conversation/prepare`, `files`, `files/{id}/uploaded`, the attachment download and the deleted-conversation 404
  were not saved by the spike: `gpt/spike-261104/{conv_prepare,files_create,files_uploaded,attachment_download,
  conversation_deleted_404}.json` are synthetic (key names as printed, placeholder values, a `synthetic` field).
- grok (grok.com, 06/11): never seen, so synthetic (a `synthetic` field in the JSON, a leading comment in the HTML):
  `grok/spike-261106/imagine_moderated.json` (a job that completes `moderated:true` with no final),
  `ndjson_edit_moderated.json` (the edit's progress-100 line with `moderated:true`), `ndjson_t2v_decoy.json`
  (muhrifqie's decoy: progress 20/45/70/90/100, 100 at 5 s, no `rRated`), `rest_403_code7.json` (chenyme's anti-bot
  body), `rest_403_cloudflare.html` (a "Just a moment" challenge page), `rest_403_blocked.json` (chenyme's
  `blocked-user` phrases; the real code and wording are unknown and might be `code 7`), `rest_429.json` (the real
  429 body of the send, as a REST read's answer), `imagine_limit_frame.json` (chenyme's `error` frame with
  `err_code`/`err_msg`; the real Imagine limit frame is unseen), `imagine_stray_request.json` (the n2 capture plus a
  final and a `completed` frame of another `request_id`). `page_home.html` is synthetic too: the raw 191 KB page holds
  account values and is not copied; it keeps only the facts the spike measured. Also not captured: a separate
  `quota_info` read after the edit alone (`quota_after_t2v.json` is one read after the edit and the t2v), an upload
  `terminalError`, a read-back of a job still running (`inflightResponses` was only seen empty), an Imagine history
  (there is none: `image_generations_empty.json`).

## meta.ai live run (23/10/2026)

Redacted captures of one live run per scenario (signed CDN query values replaced by `x`); `meta/spike-261023/README.md` describes each scenario and `packages/provider-meta/FINDINGS.md` sums them up.

- `meta/s3-network/default-2.json`
- `meta/s3-network/default.json`
- `meta/s3-network/n4.json`
- `meta/s3-network/n8.json`
- `meta/spike-261023/checkpoint-acc1/cookies_after.json`
- `meta/spike-261023/checkpoint-acc1/http.json`
- `meta/spike-261023/checkpoint-acc1/loaded.state.json`
- `meta/spike-261023/i2i-acc1/attach_done.state.json`
- `meta/spike-261023/i2i-acc1/composer.json`
- `meta/spike-261023/i2i-acc1/final.state.json`
- `meta/spike-261023/i2i-acc1/http.json`
- `meta/spike-261023/i2i-acc1/loaded.state.json`
- `meta/spike-261023/i2i-acc1/send.json`
- `meta/spike-261023/i2i-acc1/timeline.json`
- `meta/spike-261023/i2i-acc1/ws.json`
- `meta/spike-261023/i2v-acc2/attach_done.state.json`
- `meta/spike-261023/i2v-acc2/composer.json`
- `meta/spike-261023/i2v-acc2/cookies_after.json`
- `meta/spike-261023/i2v-acc2/file_inputs.json`
- `meta/spike-261023/i2v-acc2/final.state.json`
- `meta/spike-261023/i2v-acc2/http.json`
- `meta/spike-261023/i2v-acc2/loaded.state.json`
- `meta/spike-261023/i2v-acc2/send.json`
- `meta/spike-261023/i2v-acc2/timeline.json`
- `meta/spike-261023/i2v-acc2/ws.json`
- `meta/spike-261023/i2v-upload-stall-acc1/composer.json`
- `meta/spike-261023/i2v-upload-stall-acc1/cookies_after.json`
- `meta/spike-261023/i2v-upload-stall-acc1/file_inputs.json`
- `meta/spike-261023/i2v-upload-stall-acc1/http.json`
- `meta/spike-261023/i2v-upload-stall-acc1/loaded.state.json`
- `meta/spike-261023/i2v-upload-stall-acc1/send.json`
- `meta/spike-261023/i2v-upload-stall-acc1/timeline.json`
- `meta/spike-261023/i2v-upload-stall-acc1/ws.json`
- `meta/spike-261023/image-1-acc1/composer.json`
- `meta/spike-261023/image-1-acc1/cookies_after.json`
- `meta/spike-261023/image-1-acc1/final.state.json`
- `meta/spike-261023/image-1-acc1/http.json`
- `meta/spike-261023/image-1-acc1/loaded.state.json`
- `meta/spike-261023/image-1-acc1/send.json`
- `meta/spike-261023/image-1-acc1/timeline.json`
- `meta/spike-261023/image-1-acc1/ws.json`
- `meta/spike-261023/image-1-acc2/composer.json`
- `meta/spike-261023/image-1-acc2/cookies_after.json`
- `meta/spike-261023/image-1-acc2/final.state.json`
- `meta/spike-261023/image-1-acc2/http.json`
- `meta/spike-261023/image-1-acc2/loaded.state.json`
- `meta/spike-261023/image-1-acc2/send.json`
- `meta/spike-261023/image-1-acc2/timeline.json`
- `meta/spike-261023/image-1-acc2/ws.json`
- `meta/spike-261023/image-2x169-acc1/composer.json`
- `meta/spike-261023/image-2x169-acc1/cookies_after.json`
- `meta/spike-261023/image-2x169-acc1/final.state.json`
- `meta/spike-261023/image-2x169-acc1/http.json`
- `meta/spike-261023/image-2x169-acc1/loaded.state.json`
- `meta/spike-261023/image-2x169-acc1/send.json`
- `meta/spike-261023/image-2x169-acc1/timeline.json`
- `meta/spike-261023/image-2x169-acc1/ws.json`
- `meta/spike-261023/image-2x169-acc2/composer.json`
- `meta/spike-261023/image-2x169-acc2/cookies_after.json`
- `meta/spike-261023/image-2x169-acc2/final.state.json`
- `meta/spike-261023/image-2x169-acc2/http.json`
- `meta/spike-261023/image-2x169-acc2/loaded.state.json`
- `meta/spike-261023/image-2x169-acc2/send.json`
- `meta/spike-261023/image-2x169-acc2/timeline.json`
- `meta/spike-261023/image-2x169-acc2/ws.json`
- `meta/spike-261023/load-acc1/cookies_after.json`
- `meta/spike-261023/load-acc1/http.json`
- `meta/spike-261023/load-acc1/loaded.state.json`
- `meta/spike-261023/load-acc2/cookies_after.json`
- `meta/spike-261023/load-acc2/http.json`
- `meta/spike-261023/load-acc2/loaded.state.json`
- `meta/spike-261023/t2v-acc1/composer.json`
- `meta/spike-261023/t2v-acc1/final.state.json`
- `meta/spike-261023/t2v-acc1/http.json`
- `meta/spike-261023/t2v-acc1/loaded.state.json`
- `meta/spike-261023/t2v-acc1/send.json`
- `meta/spike-261023/t2v-acc1/timeline.json`
- `meta/spike-261023/t2v-acc1/ws.json`

`meta/spike-261025/i2i-2refs-acc3.dom.html` (25/10): the answer to two reference images attached together (a fox and a
lighthouse, "put the fox ... in front of the lighthouse"): one drawn image in the before/after slider. Classes, styles
and svg stripped, signed query values replaced by `x`, long digit runs by `1`s, the input image's path by `<tok1>`.

## vibes.ai live run (23/10/2026)

Redacted calls of one image job and one video job; `vibes/s5-live/README.md` describes them and `packages/provider-vibes/FINDINGS.md` sums them up.

- `vibes/s5-live/auth_001_auth_me.json`
- `vibes/s5-live/auth_002_quota_upsell_before.json`
- `vibes/s5-live/image_001_image_create_batch.json`
- `vibes/s5-live/image_002_image_get_empty_batch.json`
- `vibes/s5-live/image_003_image_generate.json`
- `vibes/s5-live/image_004_image_get_batch_after.json`
- `vibes/s5-live/image_005_image_download_png.json`
- `vibes/s5-live/log_auth_1792691308.json`
- `vibes/s5-live/log_image_1792691322.json`
- `vibes/s5-live/log_recheck_1792692531.json`
- `vibes/s5-live/log_video_1792691361.json`
- `vibes/s5-live/recheck_001_recheck_batch.json`
- `vibes/s5-live/recheck_002_recheck_download.json`
- `vibes/s5-live/recheck_003_quota_upsell_after.json`
- `vibes/s5-live/video_001_video_create_batch.json`
- `vibes/s5-live/video_002_video_get_empty_batch.json`
- `vibes/s5-live/video_003_video_poll_during_post.json`
- `vibes/s5-live/video_004_video_poll_during_post.json`
- `vibes/s5-live/video_005_video_poll_during_post.json`
- `vibes/s5-live/video_006_video_poll_during_post.json`
- `vibes/s5-live/video_007_video_generate.json`
- `vibes/s5-live/video_008_video_poll_during_post.json`
- `vibes/s5-live/video_009_video_poll_after.json`
- `vibes/s5-live/video_010_video_poll_after.json`
- `vibes/s5-live/video_011_video_poll_after.json`
- `vibes/s5-live/video_012_video_poll_after.json`
- `vibes/s5-live/video_013_video_poll_after.json`
- `vibes/s5-live/video_014_video_poll_after.json`
- `vibes/s5-live/video_015_video_poll_after.json`
- `vibes/s5-live/video_016_video_poll_after.json`
- `vibes/s5-live/video_017_video_poll_after.json`
- `vibes/s5-live/video_018_video_poll_after.json`
- `vibes/s5-live/video_019_video_poll_after.json`
- `vibes/s5-live/video_020_video_poll_after.json`
- `vibes/s5-live/video_021_video_poll_after.json`
- `vibes/s5-live/video_022_video_poll_after.json`
- `vibes/s5-live/video_023_video_poll_after.json`
- `vibes/s5-live/video_024_video_poll_after.json`
- `vibes/s5-live/video_025_video_poll_after.json`
- `vibes/s5-live/video_026_video_poll_after.json`
- `vibes/s5-live/video_027_video_poll_after.json`
- `vibes/s5-live/video_028_video_poll_after.json`
- `vibes/s5-live/video_029_video_poll_after.json`
- `vibes/s5-live/video_030_video_poll_after.json`
- `vibes/s5-live/video_031_video_poll_after.json`
- `vibes/s5-live/video_032_video_poll_after.json`
- `vibes/s5-live/video_033_video_poll_after.json`
- `vibes/s5-live/video_034_video_poll_after.json`
- `vibes/s5-live/video_035_video_poll_after.json`
- `vibes/s5-live/video_036_video_poll_after.json`
- `vibes/s5-live/video_037_video_poll_after.json`
- `vibes/s5-live/video_038_video_poll_after.json`
- `vibes/s5-live/video_039_video_poll_after.json`
- `vibes/s5-live/video_040_video_poll_after.json`
- `vibes/s5-live/video_041_video_poll_after.json`
- `vibes/s5-live/video_124_video_poll_after.json`
- `vibes/s5-live/video_125_video_download_0.json`
- `vibes/s5-live/video_126_video_download_1.json`
- `vibes/s5-live/video_timeline.json`

## vibes.ai live run S6 (23/10/2026)

Redacted calls of one image job asked for 16:9 and one image-to-video job; `vibes/s6-live/README.md` describes them.

- `vibes/s6-live/i2v_001_upload_image.json`
- `vibes/s6-live/i2v_002_create_batch.json`
- `vibes/s6-live/i2v_003_generate.json`
- `vibes/s6-live/i2v_004_poll.json`
- `vibes/s6-live/i2v_005_poll.json`
- `vibes/s6-live/i2v_006_poll.json`
- `vibes/s6-live/i2v_007_poll.json`
- `vibes/s6-live/i2v_008_poll.json`
- `vibes/s6-live/i2v_009_poll.json`
- `vibes/s6-live/i2v_010_poll.json`
- `vibes/s6-live/i2v_011_poll.json`
- `vibes/s6-live/i2v_012_poll.json`
- `vibes/s6-live/i2v_013_poll.json`
- `vibes/s6-live/i2v_014_poll.json`
- `vibes/s6-live/i2v_015_download_video.json`
- `vibes/s6-live/log_i2v_1792695392.json`
- `vibes/s6-live/log_t2i_1792695371.json`
- `vibes/s6-live/t2i_001_create_batch.json`
- `vibes/s6-live/t2i_002_generate.json`
- `vibes/s6-live/t2i_003_download_png.json`

## vibes.ai live run S7 (23/10/2026)

Redacted calls of three text-to-image jobs that sent one reference image as an omni reference (`oref`); vibes ignored
it each time. `vibes/s7-live/README.md` describes them.

- `vibes/s7-live/log_oref_1792721973.json`
- `vibes/s7-live/log_oref_project_1792722383.json`
- `vibes/s7-live/log_oref_web_1792722097.json`
- `vibes/s7-live/oref_001_upload_image.json`
- `vibes/s7-live/oref_002_create_batch.json`
- `vibes/s7-live/oref_003_generate.json`
- `vibes/s7-live/oref_004_read_batch.json`
- `vibes/s7-live/oref_005_download_png.json`
- `vibes/s7-live/oref_project_001_create_project.json`
- `vibes/s7-live/oref_project_001_upload_media.json`
- `vibes/s7-live/oref_project_002_project_upload.json`
- `vibes/s7-live/oref_project_003_create_batch.json`
- `vibes/s7-live/oref_project_004_generate.json`
- `vibes/s7-live/oref_project_005_read_batch.json`
- `vibes/s7-live/oref_project_006_download_png.json`
- `vibes/s7-live/oref_web_001_create_batch.json`
- `vibes/s7-live/oref_web_002_generate.json`
- `vibes/s7-live/oref_web_003_read_batch.json`
- `vibes/s7-live/oref_web_004_download_png.json`

## vibes.ai pinned modes live smoke (25/10/2026)

One extend, one video edit and one image edit of the account's own earlier results, sent by the relay's engine code;
`vibes/pinned-261025/README.md` describes them.

- `vibes/pinned-261025/edit_001_read_source.json`
- `vibes/pinned-261025/edit_002_generate.json`
- `vibes/pinned-261025/edit_003_download_png.json`
- `vibes/pinned-261025/extend_001_read_source.json`
- `vibes/pinned-261025/extend_002_create_batch.json`
- `vibes/pinned-261025/extend_003_generate.json`
- `vibes/pinned-261025/extend_004_poll.json`
- `vibes/pinned-261025/extend_014_poll_done.json`
- `vibes/pinned-261025/extend_015_download_video.json`
- `vibes/pinned-261025/v2v_001_read_source.json`
- `vibes/pinned-261025/v2v_002_create_batch.json`
- `vibes/pinned-261025/v2v_003_generate.json`
- `vibes/pinned-261025/v2v_014_poll_done.json`
- `vibes/pinned-261025/v2v_015_download_video.json`

## vibes.ai session ended between the batch create and the generate (26/10/2026)

Rebuilt from the shape of the vibes-acc1 incident, not captured: the generate's 401 `SESSION_EXPIRED` and the clearing
cookie (`set_cookie_seen`, kept out of `headers`); every value is a placeholder.

- `vibes/signed-out-261026/t2v_generate_session_expired.json`

## vibes.ai refused image (production job f0176f20, 23/10/2026 09:39 GMT+7)

A text-to-image generate that answered `success: true` with a finished batch (`isComplete: true`, `hasError: false`)
whose one item vibes refused: `isLoading: false`, no `imageUrl` or `mediaEntId`, `data` the string
`{"requestId": ...}`, and `error: "This content could not be generated. Please try a different prompt."`; then the PNG
download of that item answered 404 JSON on every try. Rebuilt from the job's log: the fields above are as recorded,
the rest follows `vibes/s6-live/t2i_002_generate.json`; ids, times and the prompt are replaced. The log cut the 404's
`error` text after `Conten`, so `Content not found` is a stand-in.

- `vibes/refused-261023/t2i_generate_refused.json`
- `vibes/refused-261023/t2i_download_404.json`

## dola plain-HTTP send spike (23/10/2026)

Redacted sends to `/chat/completion` over plain HTTP with our own `a_bogus` (good, corrupted, none, a replay of the
first, one video) and the reads around them; `dola/api-spike-261023/README.md` describes each file and
`packages/provider-dola/FINDINGS.md` ("HTTP send spike 23/10") sums them up.

- `dola/api-spike-261023/badsig_request.json`
- `dola/api-spike-261023/badsig_sse.json`
- `dola/api-spike-261023/chain_after_send.json`
- `dola/api-spike-261023/nosig_sse.json`
- `dola/api-spike-261023/page_signatures.json`
- `dola/api-spike-261023/recent_conv_after.json`
- `dola/api-spike-261023/recent_conv_before.json`
- `dola/api-spike-261023/recent_conv_plain_json_content_type.json`
- `dola/api-spike-261023/replay_request.json`
- `dola/api-spike-261023/replay_sse.json`
- `dola/api-spike-261023/send_request.json`
- `dola/api-spike-261023/send_results.json`
- `dola/api-spike-261023/send_sse.json`
- `dola/api-spike-261023/signatures_decoded.json`
- `dola/api-spike-261023/signed_reads.json`
- `dola/api-spike-261023/video_chain_final.json`
- `dola/api-spike-261023/video_request.json`
- `dola/api-spike-261023/video_sse.json`

## Google Flow step-0 spike (23/10/2026)

Redacted plain-HTTP calls of the auth, credits and project-create probes, the session loss and one YesCaptcha task; no
generation was sent. `flow/spike-261023/README.md` describes each file and `fixtures/flow/FINDINGS.md` sums
them up.

- `flow/spike-261023/auth_001_exit_ip.json`
- `flow/spike-261023/auth_002_boot_anon.json`
- `flow/spike-261023/auth_003_boot_plain_requests.json`
- `flow/spike-261023/auth_004_boot_root.json`
- `flow/spike-261023/auth_005_credits_before.json`
- `flow/spike-261023/html_001_boot_root.json`
- `flow/spike-261023/html_002_boot_project.json`
- `flow/spike-261023/osid_001_myaccount.json`
- `flow/spike-261023/osid_002_passive_login_hops.json`
- `flow/spike-261023/osid_003_boot_after_osid.json`
- `flow/spike-261023/project_002_create_project.json`
- `flow/spike-261023/project_003_boot_project.json`
- `flow/spike-261023/rotate_001_rotate_cookies.json`
- `flow/spike-261023/rotate_002_boot_after_rotate.json`
- `flow/spike-261023/yes_001_yescaptcha_one_task.json`

## Google Flow live run (23/10/2026)

Redacted calls of the session keep-alive, 3 accepted images, 1 accepted Veo 3.1 Lite video, the reCAPTCHA refusals
and the free reads (models, listing, media); `flow/live-261023/README.md` describes them.

- `flow/live-261023/browser_mint.json`
- `flow/live-261023/img1_generate_refused.json`
- `flow/live-261023/img1_token.json`
- `flow/live-261023/img11_generate_browser_refused.json`
- `flow/live-261023/img11_token_browser.json`
- `flow/live-261023/img12_generate_after_cooldown.json`
- `flow/live-261023/img2_001_boot_project.json`
- `flow/live-261023/img2_002_credits_before.json`
- `flow/live-261023/img2_003_listing_before.json`
- `flow/live-261023/img2_004_token.json`
- `flow/live-261023/img2_005_written_ahead.json`
- `flow/live-261023/img2_006_listing_inflight.json`
- `flow/live-261023/img2_007_listing_inflight.json`
- `flow/live-261023/img2_008_listing_inflight.json`
- `flow/live-261023/img2_009_listing_inflight.json`
- `flow/live-261023/img2_010_listing_inflight.json`
- `flow/live-261023/img2_011_generate.json`
- `flow/live-261023/img2_012_listing_after.json`
- `flow/live-261023/img2_013_credits_after.json`
- `flow/live-261023/img2_014_download_0.json`
- `flow/live-261023/img2_download_later.json`
- `flow/live-261023/img6_generate_refused.json`
- `flow/live-261023/img6_token.json`
- `flow/live-261023/img8_004_token.json`
- `flow/live-261023/img8_005_written_ahead.json`
- `flow/live-261023/img8_010_generate.json`
- `flow/live-261023/img8_011_listing_after.json`
- `flow/live-261023/img8_listing_1min_later.json`
- `flow/live-261023/img9_generate_browser_refused.json`
- `flow/live-261023/img9_token_browser.json`
- `flow/live-261023/keeper_timeline.json`
- `flow/live-261023/pagefetch_generate_refused.json`
- `flow/live-261023/read_getmedia_as29s.json`
- `flow/live-261023/read_getmedia_not_found.json`
- `flow/live-261023/read_listing_bad_arg.json`
- `flow/live-261023/read_listing_Zzl0ze.json`
- `flow/live-261023/read_models_HTrJv.json`
- `flow/live-261023/video_credits_before.json`
- `flow/live-261023/video_get_boot_project.json`
- `flow/live-261023/video_get_credits_after_video.json`
- `flow/live-261023/video_get_download_0.json`
- `flow/live-261023/video_get_download_1.json`
- `flow/live-261023/video_get_getmedia.json`
- `flow/live-261023/video_listing_before.json`
- `flow/live-261023/video_listing_poll_first.json`
- `flow/live-261023/video_listing_poll_last.json`
- `flow/live-261023/video_status_poll_first.json`
- `flow/live-261023/video_status_poll_last.json`
- `flow/live-261023/video_status_v0.json`
- `flow/live-261023/video_submit_try1.json`

## Google Flow credits, plan and video catalog (25/10/2026)

The `nzlxg` read of the AI Pro account (credits, PaygateTier 1, monthly refill), and the live Lite and Fast video jobs
of that day with the refused sends and the credits before and after; `flow/live-261025/README.md` gives the free
accounts' answers and flow's tier enum.

- `flow/live-261025/credits_pro.json`
- `flow/live-261025/video_jobs.json`

## Google Flow input images and the full catalog (25/10/2026)

GetModels read on the AI Pro account, the image upload (`maseQ`), the sizes Nano Banana 2.1 drew at each of its extra
aspect codes, and one live job of each new mode (image to image, a video from a start frame, from a first and a last
frame, from references; Veo 3.1 Quality; Omni 1.1 Flash); `flow/live-261025b/README.md` describes them.

- `flow/live-261025b/image_ratios.json`
- `flow/live-261025b/input_jobs.json`
- `flow/live-261025b/models_HTrJv.json`
- `flow/live-261025b/upload_maseQ.json`

## Google Flow code 8 on video sends (26-30/10/2026)

The four production video sends flow answered with code 8 (too much traffic), from the relay's own job events: the
envelope's shape, each batch, and the listing reads that never found it; `flow/live-261030/README.md` describes it.

- `flow/live-261030/video_code8_sends.json`

## Google Flow code 7 on video sends (25/10 and 03/11/2026)

The eight production video sends flow answered with code 7 (`PUBLIC_ERROR_UNUSUAL_ACTIVITY`, the reCAPTCHA refusal),
from the relay's own jobs and job events: the envelope's shape, each batch, and the listing reads that never found it;
`flow/live-261103/README.md` describes it.

- `flow/live-261103/video_code7_sends.json`

## Gemini web spikes (23/10/2026)

The signed-out probe (`gemini/spike-261023/`) and the live run with gemini's own session (`gemini/live-261023/`: t2i,
i2i, the not-entitled t2v, chat reads and the chat list, the cut sends, the web app's own requests captured unsent;
the full answers are the `.txt` beside each `.json`). Each folder's README.md describes its files and
`fixtures/gemini/FINDINGS.md` sums them up.

- `gemini/live-261023/cut_summary.json`
- `gemini/live-261023/cutcid_summary.json`
- `gemini/live-261023/i2i_reply.json`
- `gemini/live-261023/t2i_16x9_reply.json`
- `gemini/live-261023/t2i_default_ratio_reply.json`
- `gemini/live-261023/t2v_not_entitled_reply.json`
- `gemini/live-261023/upload.json`
- `gemini/live-261023/web_request_edit.json`
- `gemini/live-261023/web_request_image_16x9.json`
- `gemini/spike-261023/anon_001_anon_page.json`
- `gemini/spike-261023/anon_003_stream_text_signed_out.json`
- `gemini/spike-261023/probe_001_probe.json`

## Gemini Omni video on the AI Pro account (24/10/2026)

The live t2v and i2v sends on the Pro account (`/u/4`), the refused i2v tries (error 1100) and the web app's own i2v
request captured unsent (`gemini/live-261024/`; the full answers are the `.txt` beside each `.json`). Its README.md
describes each file and `fixtures/gemini/FINDINGS.md` sums them up.

- `gemini/live-261024/i2v_accepted_reply.json`
- `gemini/live-261024/i2v_error_1100_reply.json`
- `gemini/live-261024/t2v_accepted_reply.json`
- `gemini/live-261024/web_request_i2v.json`

## Gemini video download User-Agent, portrait and image modes, music and templates (25/10/2026)

The googlevideo.com link answers 403 to any User-Agent but the one that resolved it, the t2i sends per mode with
the t2v portrait sends, the web app's own portrait, template and music requests captured unsent, and one song's
answer, and two songs' "something went wrong, try again" final texts (`gemini/live-261025/`; its README.md describes the probe, redacted to parameter names, statuses and headers,
the sends, numbers and labels only, and the captures).

- `gemini/live-261025/googlevideo_user_agent.json`
- `gemini/live-261025/music_temporary_failure.json`
- `gemini/live-261025/portrait_and_image_modes.json`
- `gemini/live-261025/web_requests_video_music.json`

## chat.qwen.ai live spike (23/10/2026)

Redacted calls of the qwen step-0 spike (auth refresh, WAF answers, t2i, image edit, t2v, i2v, task status, credits);
`qwen/spike-261023/README.md` describes each file and `fixtures/qwen/FINDINGS.md` sums them up.

- `qwen/spike-261023/auth_refresh.json`
- `qwen/spike-261023/bx_reuse_T1.json`
- `qwen/spike-261023/bx_reuse_T2.json`
- `qwen/spike-261023/bx_reuse_U1.json`
- `qwen/spike-261023/bx_reuse_U2.json`
- `qwen/spike-261023/bx_reuse_U3.json`
- `qwen/spike-261023/chat_read_empty.json`
- `qwen/spike-261023/chats_new_plain.json`
- `qwen/spike-261023/credits_history_acc1.json`
- `qwen/spike-261023/credits_history_acc2.json`
- `qwen/spike-261023/credits_pricing.json`
- `qwen/spike-261023/credits_verify_empty.json`
- `qwen/spike-261023/credits_verify_ok.json`
- `qwen/spike-261023/edit_chat_read.json`
- `qwen/spike-261023/edit_send.json`
- `qwen/spike-261023/i2v_chat_read.json`
- `qwen/spike-261023/i2v_send.json`
- `qwen/spike-261023/i2v_task_running.json`
- `qwen/spike-261023/i2v_task_success.json`
- `qwen/spike-261023/member_level.json`
- `qwen/spike-261023/models.json`
- `qwen/spike-261023/sts_plain.json`
- `qwen/spike-261023/t2i_chat_read.json`
- `qwen/spike-261023/t2i_dropped_chat_read.json`
- `qwen/spike-261023/t2i_dropped_send.json`
- `qwen/spike-261023/t2i_send.json`
- `qwen/spike-261023/t2v_chat_read.json`
- `qwen/spike-261023/t2v_no_credits.json`
- `qwen/spike-261023/t2v_send.json`
- `qwen/spike-261023/t2v_task_running.json`
- `qwen/spike-261023/t2v_task_success.json`
- `qwen/spike-261023/task_status_wrong_path.json`
- `qwen/spike-261023/waf_plain_no_bx.json`
- `qwen/spike-261023/waf_replayed_bx_other_body.json`

## muse.ai live spike (26/10/2026)

One free muse.ai account, 11 jobs over HTTP and the Noise tunnel, through one unused proxy
(`plans/muse-spike-261026.md`). The spike's own redaction: every uuid is `<uuid>`, the VM id `<vm_id>`, the side
chat ids `<redacted len=36>`, long base64 `<b64 len=N>`; no cookie or token is in them. `job_<n>.json`: the requests
sent in the tunnel, the answers, and the side chat's final history (j01 one 1:1 image, j03 a 5 s t2v, j07 the refusal
in words, j10 i2v with the image inline, j11 four images in one card); `history_j02.json` and `history_j06.json`: the
last history answer of j02 (a commentary line before the answer) and j06 (two images, one card each), redacted the same
way from the box captures; `results_all.json`: each job's summary with the Usage before and after.
`usage-261026.json`: the Usage action's subscription object and the script lines that declare it (recorded in the
browser). Not captured: a signed-out session, a sleeping workspace's wake, an approval, a quota at 100 %.

- `muse/spike-261026/history_j02.json`
- `muse/spike-261026/history_j06.json`
- `muse/spike-261026/job_j01.json`
- `muse/spike-261026/job_j03.json`
- `muse/spike-261026/job_j07.json`
- `muse/spike-261026/job_j10.json`
- `muse/spike-261026/job_j11.json`
- `muse/spike-261026/results_all.json`
- `muse/usage-261026.json`

## muse.ai reference spike (30/10/2026)

Since 30/10 muse saves a made video (text to video and image to video) in `workspace/your_files/<name>`, not
`workspace/imagine_media/` (`plans/muse-ref-spike-301030/REPORT.md`). `history_your_files.json`: one history answer
of a text to video job, built from the live shapes and redacted the spike's way (`<uuid>`, `<vm_id>`,
`<redacted len=36>`): the user's message as the relay sends it, the result card of the spike's B3 video (its fields,
with another file name) and the answer in the words of production job fba992d9, which muse wrote with a
`sandbox://workspace/your_files/...` link and the relay failed as `muse.refused`. No cookie or token is in it.
`history_edit_two_refs.json`: one history answer of an image edit from two references (the spike's A2: the object of
image 1 put into the scene of image 2, 42 s, one 1920x1280 WebP of 785446 bytes in `workspace/imagine_media/`),
derived from the shapes of `spike-261026/history_j02.json` (the spike kept no raw history of A2): the user's message
as the relay sends an edit, A2's answer word for word and its result card (size and label), redacted the same way.
The user event's attachment fields are not captured. No cookie or token is in it.

- `muse/ref-spike-301030/history_edit_two_refs.json`
- `muse/ref-spike-301030/history_your_files.json`

## muse.ai: video not available for the account (30/10/2026)

On 30/10 muse-acc1 answered every text to video job in words: video generation is not available for the account in
its country or region, a standing restriction (production jobs b1707964, 7233a0bf, bf3c4091, b3d8bfec, 8c9e094b, read
from `relay.jobs.error`; muse answers in the prompt's language). `history_video_unavailable.json`: `answers` holds the
five answers word for word (two in Vietnamese); `result` is one history answer built from the live shapes of
`history_your_files.json` and redacted the same way: the user's message as the relay sends it for job 7233a0bf and
muse's complete answer to it, with no result card. No cookie, token or account id is in it.

- `muse/region-301030/history_video_unavailable.json`

## muse.ai: results judged by the new files (30/10/2026)

On 30/10 muse saved results in `workspace/your_files` without a result card (job aae305b4, an image edit answered
"kept private in your workspace") or named only by a `sandbox://` link in the answer (job 1c7ddda2, a video); both
were refunded while muse had made them. `fs_list_after.json`: one live answer of `POST /fs/list` for
`workspace/your_files` (`{path, entries: [{name, type, size, modifiedAt}]}`), names kept, no VM id or URL.
`history_linked_video.json` and `history_unlinked_image.json`: history answers built from the two jobs' answers
and the shapes of `ref-spike-301030/history_your_files.json`, redacted the same way, with no result card. No cookie,
token or account id is in them.

- `muse/new-files-301030/fs_list_after.json`
- `muse/new-files-301030/history_linked_video.json`
- `muse/new-files-301030/history_unlinked_image.json`

## chatgpt.com spike (04-05/11/2026)

One Plus account over plain HTTP through its own proxy (`plans/gpt-spike-261104.md`); `fixtures/gpt/FINDINGS.md`
(F1–F8) sums them up. Each file is `{status, headers, body | stream_lines}`. The spike logged bodies and stream lines
only, so captures from 04/11 have `headers: {}` and a `note` saying so; the two 05/11 reads (signed-out session, one
prepare made with no cookie, no bearer and a random device id) keep their response headers, Set-Cookie as names
(`set_cookie_names`). Shaped on the box (tokens, `dx` programs, sentinel/conduit/resume tokens → `<redacted len=N>`;
account, user, device ids, email, name, picture, IP and region → fixed fakes; scanned for every jar cookie, the access
token, the proxy parts and the identity values: 0 hits), then put through `scripts/redact-fixtures.py`'s `Redactor`
(salted fakes, URL query values → `x`, `check --raw` → 0 leaks). `redact` itself was not used: it walks only its
dola/meta/vibes layout and rewrites the destination README. `account.structure` reads a faked word because the
`_account` cookie holds the same word. `safe_urls` in the conversation reads (the account's own earlier
links) are `example.invalid`. `turnstile_dx.json` is `{p, dx, go_token_len}` copied unredacted (its `p` carries no
account value; redaction would break the XOR); `go_token_len` is the length of aurora's Go VM answer (1184).

- `gpt/spike-261104/home.json` — `data-build` and the sentinel `sdk.js` URL only.
- `gpt/spike-261104/auth_session.json` — `GET /api/auth/session` signed in (Plus).
- `gpt/spike-261104/auth_session_signed_out.json` — the same with no cookie: 200 `{}` (05/11).
- `gpt/spike-261104/init_limits.json` — `POST /backend-api/conversation/init` (`limits_progress`).
- `gpt/spike-261104/prepare.json` — sentinel `chat-requirements/prepare`, signed in (`chatgpt-paid`).
- `gpt/spike-261104/prepare_signed_out.json` — the same with no cookie and no bearer (`chatgpt-noauth`, 05/11).
- `gpt/spike-261104/finalize.json` — sentinel `chat-requirements/finalize`.
- `gpt/spike-261104/turnstile_dx.json` — the 05/11 prepare's `p` and `dx`, with the Go VM's token length.
- `gpt/spike-261104/sse_t2i_1x1.json` — run t5A stream (1:1, user message echoed).
- `gpt/spike-261104/sse_t2i_16x9.json` — run t1 stream (16:9).
- `gpt/spike-261104/sse_edit.json` — run t3 stream (image edit).
- `gpt/spike-261104/conv_t2i.json` — run t5A conversation read.
- `gpt/spike-261104/conv_edit.json` — run t3 conversation read (input as `sediment://` in the user message).
- `gpt/spike-261104/conv_prepare.json` — synthetic `f/conversation/prepare` answer.
- `gpt/spike-261104/files_create.json` — synthetic `POST /backend-api/files` answer.
- `gpt/spike-261104/files_uploaded.json` — synthetic `POST /backend-api/files/{id}/uploaded` answer.
- `gpt/spike-261104/attachment_download.json` — synthetic attachment download answer (real key set).
- `gpt/spike-261104/conversation_deleted_404.json` — synthetic 404 of a hidden conversation.

## meta.ai over plain HTTP (05/11/2026)

Redacted frames, pages and schema from the HTTP spike and Task 0 of the HTTP plan, all on the meta-acc4 jar through the spike proxy; `meta/http-261105/README.md` says which engine made each file and which markers tell the links apart.

- `meta/http-261105/frames_t2i_n1.json` — HTTP job frames (sent ones rebuilt): text to 1 image.
- `meta/http-261105/frames_t2i_n2.json` — HTTP job frames: text to 2 images, 9:16.
- `meta/http-261105/frames_t2i_n4.json` — HTTP job frames: text to 4 images, 1:1.
- `meta/http-261105/frames_t2v.json` — HTTP job frames: text to video.
- `meta/http-261105/frames_i2v.json` — HTTP job frames: image to video (one attachment).
- `meta/http-261105/frames_refusal.json` — HTTP job frames: a safety refusal (`policy_denied`, 0 media).
- `meta/http-261105/frames_edit.json` — HTTP job frames: an edit from one reference image (1 final, 2 previews).
- `meta/http-261105/request_skeleton.json` — one row per leaf of the browser's sent `Request`, with its kind (`const`, `account?`, ...).
- `meta/http-261105/page_signed_in.html` — `GET /` signed in (token, viewerId, display name).
- `meta/http-261105/page_signed_out.html` — `GET /` with no cookie after the `rd` pass.
- `meta/http-261105/page_rd_403.html` — meta's 478-byte `rd` challenge page.
- `meta/http-261105/page_vercel.html` — the Vercel Security Checkpoint page.
- `meta/http-261105/prompt_page_t2i_n1.html` — `GET /prompt/<cid>` of the n = 1 job.
- `meta/http-261105/prompt_page_t2i_n2.html` — `GET /prompt/<cid>` of the n = 2 job.
- `meta/http-261105/prompt_page_t2i_n4.html` — `GET /prompt/<cid>` of the n = 4 job.
- `meta/http-261105/prompt_page_i2v.html` — `GET /prompt/<cid>` of the image-to-video job.
- `meta/http-261105/prompt_page_refusal.html` — `GET /prompt/<cid>` of the refused job.
- `meta/http-261105/prompt_page_edit.html` — `GET /prompt/<cid>` of the HTTP edit job (input, preview and final links).
- `meta/http-261105/schema_261105.json` — the two base64 FileDescriptorProtos of the gateway schema (public JS).
- `meta/http-261105/gateway_constants.txt` — bundle excerpts holding `x-dgw-appid` and the `product_context` strings (public JS).

## grok.com spike and step-0 probe (06/11/2026)

One free account over plain HTTP/WS through its own proxy (`plans/grok-spike-261106.md`,
`plans/grok-design-261106/step0.md`); `fixtures/grok/FINDINGS.md` (F1–F9) sums them up. Each JSON file is
`{status, headers, body | lines + t_ms | frames: [{dir, t_ms, data}]}`, plus a `note`; read-backs add the `request`
they answer. The box saved its evidence already redacted (ids as `<UUID>` / step-0 labels, the user id as `<SECRET>`,
email, names); a throwaway script outside the repo turned every placeholder into a fixed UUID-shaped fake, the same
across files (the id table is at the top of FINDINGS), cut every `blob` to a 16×16 sample image, dropped `cf-ray` /
`date`, faked the account's creation time, and rebuilt the sent Imagine frames from the spike script. `evidence/run.log`
(whose chat frames still hold partly cut conversation ids) and `secrets/` were not copied; the bodies taken from
run.log carry no id. Checked: `scripts/redact-fixtures.py check` → 0 leaks; `scripts/secret-scan.py` over every
`~/relay-secrets` file (the test jar, the spike proxy, the user id and email) → 0; the proxy host, user and password,
the user id (with and without dashes), the email and its local part, `sso` / `sso-rw` / `x-userid` → 0 hits each.

- `grok/spike-261106/session_signed_in.json` — `GET /api/auth/session` signed in (free, `xSubscriptionType` "").
- `grok/spike-261106/session_signed_out.json` — the same with no cookie / a made-up `sso`.
- `grok/spike-261106/quota_before.json` — `quota_info` before any generation (`imagePro` 4, `video720p` 1).
- `grok/spike-261106/quota_after_t2i.json` — after the n=2 t2i (`imagePro` 2).
- `grok/spike-261106/quota_after_t2v.json` — after the edit and the t2v (`imagePro` 1, `video720p` 0 + `nextAvailableAt`).
- `grok/spike-261106/quota_video_exhausted.json` — right before the out-of-quota send (11:55).
- `grok/spike-261106/rest_401_no_credentials.json` — REST 401 `code 16`, no cookie.
- `grok/spike-261106/rest_401_bad_credentials.json` — REST 401 `code 16`, a made-up `sso`.
- `grok/spike-261106/ws_401.json` — the Imagine WS handshake refused 401.
- `grok/spike-261106/rest_400_code3.json` — `conversations/new` with body `{}`.
- `grok/spike-261106/imagine_t2i_n2.json` — Imagine WS t2i, n=2: sent frames (rebuilt) and every received frame.
- `grok/spike-261106/upload_v2.json` — `POST /http/upload-file-v2/direct` answer.
- `grok/spike-261106/ndjson_edit.json` — the edit's NDJSON lines with their times.
- `grok/spike-261106/ndjson_t2v.json` — the t2v's NDJSON lines with their times (`videoId` at 0.97 s, 100 at 34.5 s).
- `grok/spike-261106/send_out_of_quota.json` — the t2v send with `video720p` 0: 429 `code 8`, one line.
- `grok/spike-261106/readback_edit.json` — `GET /rest/app-chat/conversations/{id}/responses` of the edit.
- `grok/spike-261106/readback_t2v.json` — the same for the t2v.
- `grok/spike-261106/readback_t2i.json` — the same for the t2i (two assets = the two finals).
- `grok/spike-261106/readback_empty.json` — the same for an empty-body probe conversation (no asset).
- `grok/spike-261106/list_conversations.json` — `GET /rest/app-chat/conversations` (edit and probes).
- `grok/spike-261106/list_conversations_imagine.json` — the same with `kind=CONVERSATION_KIND_IMAGINE` (t2v, t2i).
- `grok/spike-261106/post_list_removed.json` — `POST /rest/media/post/list`: 400 `code 9`, removed.
- `grok/spike-261106/image_generations_empty.json` — `GET /rest/app-chat/image-generations`: empty.
- `grok/spike-261106/page_home.html` — synthetic signed-in home page (see Gaps).
- `grok/spike-261106/imagine_moderated.json` — synthetic: Imagine WS job that completes `moderated:true` with no final (see Gaps).
- `grok/spike-261106/imagine_stray_request.json` — synthetic: the n2 capture plus a final and a `completed` frame of another `request_id`.
- `grok/spike-261106/imagine_limit_frame.json` — synthetic: Imagine WS `error` frame with `err_code`/`err_msg` (chenyme's shape; real one unseen).
- `grok/spike-261106/imagine_rate_limit_frame.json` — captured 06/11 (redacted): the real Imagine WS `error` frame `rate_limit_exceeded` / "Image rate limit exceeded", sent 0.28 s after the request with no `session` frame first (FINDINGS F10).
- `grok/spike-261106/ndjson_edit_moderated.json` — synthetic: the edit's progress-100 line with `moderated:true`, url kept.
- `grok/spike-261106/ndjson_t2v_decoy.json` — synthetic: muhrifqie's decoy t2v lines (progress 20/45/70/90/100, no `rRated`).
- `grok/spike-261106/rest_403_code7.json` — synthetic: REST 403 `code 7` anti-bot body (chenyme's).
- `grok/spike-261106/rest_403_blocked.json` — synthetic: REST 403 `blocked-user` phrases (real wording unknown).
- `grok/spike-261106/rest_429.json` — synthetic: the send's real 429 `code 8` body as a REST read's answer.
- `grok/spike-261106/rest_403_cloudflare.html` — synthetic: a "Just a moment" Cloudflare challenge page (see Gaps).
