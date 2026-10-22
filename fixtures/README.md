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
