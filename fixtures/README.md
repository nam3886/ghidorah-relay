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

## Gaps

What the captures do not hold.

- dola watermark-only: none of the 38 real done videos (30/09) lacked `main_url`. Tests build it from a done chain by deleting `main_url` from its `video_model` (derived, not captured).
- dola throttle SSE (`/chat/completion` `error_code`) and dola likeness refusal: not captured here. The SSE bodies in `dola/sse/*.txt` are reconstructed from the event names and error codes of earlier samples (not live captures); a live 710022004 body replaces them when one is measured.
- vibes derived operations (extend, v2v, image-edit) and vibes error bodies (401, 403, 429, QUOTA, a rejected generate): not captured yet.
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
