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

- `muse/ref-spike-301030/history_your_files.json`
