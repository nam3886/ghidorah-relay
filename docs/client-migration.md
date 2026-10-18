# Moving a client to relay

For a client that now talks to dola2api (v4) and will talk to relay. Keys in every example are placeholders (`rk-...`).

## Address and keys

- Base URL: `http://192.168.18.227:8100`. It is reachable from the LAN only. There is no https and no domain.
- Every call needs a relay key. Send it as `Authorization: Bearer rk-...`, or as `X-API-Key` / `x-goog-api-key`. A `?key=`
  query value is never read.
- A missing, unknown or malformed key answers 401 `auth.invalid_key`; a disabled key 403 `auth.key_disabled`; a model the key
  may not use 403 `auth.model_forbidden`.
- Your keys are issued by the owner with a video wallet and an image wallet. `GET /v1/points` shows the wallets, the models the
  key may use, and how many unfinished jobs it holds.

## Three doors, one job

| Door | Create | Poll |
|---|---|---|
| OpenAI | `POST /v1/chat/completions` | native `GET` below, with the `job_id` in the answer |
| Gemini | `POST /v1beta/models/{model}:generateContent` (also `:streamGenerateContent`, and without `/v1beta`) | native `GET` below, with `responseId` |
| Native | `POST /v1/video/generations`, `POST /v1/images/generations` | `GET /v1/video/generations/{id}`, `GET /v1/images/generations/{id}` |

A job is the same whichever door made it. A video job is read on the video route only, an image job on the image route only;
the other route answers 404.

The machine-readable description of the native calls, with every status and stage value and every field, is
`GET /openapi.json` (no key). The interactive page is `/reference`.

**Unknown fields are refused on every door** (400 `input.invalid`, `param` names the field). The OpenAI door takes only `model`,
`messages` and `stream`. The Gemini door takes only `contents` and `systemInstruction` (an empty `generationConfig` is
tolerated; one with content is refused). The native door takes `model`, `prompt`, `ratio`, `n`, `image`, `images`, `options`,
`title`, `callback_url`, plus `duration` for video and `style` for image.

## URLs and images

- `callback_url` and any image URL must be **https** and **public**. A private, LAN or loopback address is refused (SSRF guard).
- A client on the LAN with no public https endpoint therefore **polls** instead of using a webhook, and sends input images as
  **data URIs** (`data:image/png;base64,...`). The Gemini door also takes `inlineData`.
- Images: at most 10 per request, 12 MiB each, JPEG, PNG or WebP (the bytes decide, not the declared type). An unreadable
  image is 400 `input.image_unreadable`; too many is `input.too_many_images`.
- The finished files are at `result_urls` (video) or `images` (image) in the status, as `<base>/m/<token>` links that need no
  key, or through `GET /v1/video/generations/{id}/content` and `GET /v1/images/generations/{id}/content/{n}` with the key.
- A webhook, when you have an https endpoint, is a thin POST `{event, event_id, job_id, status}` (`job.done` or `job.failed`),
  retried 30 s and 300 s after a failure, three attempts in all. Fetch the result with the poll call.

## What is gone from v4

`poll_deadline_s`, `result_url` (use `result_urls`), the singular `warning` (it is `warnings`, a list),
`GET /v1/images/templates` and `GET /v1/images/options`. Send none of them.

## Model names

- Video: `seedance-2.5`, `seedance-2.0`, `seedance-1.0`. Image: `seedream-4.5`, `seedream-5.0-pro`. `GET /v1/models` lists what
  your key may use, with limits and prices in points, and the display `name` of each model ("Dreamina Seedance 2.5"; the
  id when a model has none). Send the id, never the name.
- Video durations: 2.5 takes 5, 10, 20, 25, 30 s; 2.0 takes 5, 10, 15 s; 1.0 takes 5, 10 s. Ratios: 1:1, 3:4, 4:3, 9:16, 16:9,
  21:9. Image ratios: 1:1, 2:3, 3:4, 4:3, 9:16, 16:9.
- **Native door:** send the model name alone, and `duration` and `ratio` as fields (image `style` as a field). A name that carries
  parameters (`seedance-2.5-30s-16:9`) is refused there with 400 `input.bad_model`. Those suffix names belong to the OpenAI
  and Gemini doors, which have no other way to say it.
- Image templates are **not a request parameter**. `options.template`, `options.template_category` and the `-tN` name suffix
  are refused (400). The template list is on `GET /v1/providers/dola/options`; send a template's prompt as `prompt`.
- Price in points: video 2.5 costs 2 for 5 s, 4 for 10 s and 2 for 20, 25 or 30 s; image `seedream-4.5` costs 1,
  `seedream-5.0-pro` costs 4. Your key's wallet is charged when the job is accepted.

## Limits

- Prompt: at most **200,000 characters** (400 `input.prompt_too_long`). The same number is `limits.max_prompt_chars` in `GET /v1/models`.
  A blank prompt is 400 `input.empty_prompt`.
- Title at most 200 characters, `options` at most 16 KiB, an `Idempotency-Key` header at most 200 printable characters.
- A key may hold at most 20 unfinished jobs unless the owner set another number (429 `quota.too_many_queued`). A wallet that
  is used up answers 429 `quota.key_exceeded`.
- `Idempotency-Key` makes a retry safe: the same key and body returns the first job; another body is 409. An SDK that
  re-sends the same request without the header within 30 minutes also gets the first job, and is not charged twice.

## Time: poll, do not hold

A video can take **about an hour** to render (28 to 56 minutes measured). An image takes a minute or two.

- **Native:** create answers at once with `{id, status: "queued"}`. Poll the status every 10 to 30 s until `done` or `failed`.
  Statuses: `queued`, `running` (with a `stage`), `done`, `failed`.
  While `running`, `stage` is `submitting` (the order is being placed with the provider), `unknown` (the order was
  sent and is not confirmed yet), `waiting` (the provider is rendering) or `fetching` (the render is done and relay is
  copying the files). New `status` and `stage` values may appear later: treat a value you do not know as "still running".
- **OpenAI and Gemini:** the request is held, at most 540 s without `stream`. If the job is not done by then, the answer is
  not an error: `finish_reason` is `length` (Gemini `MAX_TOKENS`) and the text says `still rendering — poll job <id>`. The
  job keeps running and stays charged. Poll it with the native call. A stream is held up to 30 minutes and sends a
  heartbeat every 5 s. Do not re-send the request: the job exists.
- A held request or a stream that you close never cancels the job.

## Progress and expected finish

The status carries three estimate fields. Ignore fields you do not know: more may be added, and none that exists is
ever renamed or removed.

| Field | Meaning |
|---|---|
| `progress` | A whole number 0 to 100, always an **estimate** (there is no separate flag): the provider reports no percentage. It is based on recent render times of this model and length, and it still advances when there is little data. 0 before the render starts; then rising while it renders; 95 while the files are copied; 100 only when `done`; `null` when `failed`. It does not go down while the render runs, and is never 100 before `done`. A job that takes longer than usual stays near the top of the range until it ends. After an operator re-check or a re-send the render restarts, and so does the estimate. |
| `render_started_at` | ISO time the current render attempt started. `null` before the render starts (queued, being placed) and for a failed job. |
| `estimated_finish_at` | ISO time the render is expected to end, to the minute, only while the render runs and only when there is enough recent data for this model and length. `null` otherwise: with little data relay makes no promise. `null` once the job has run longer than usual (a time in the past is not an estimate), and `null` for a failed job. |

Images are fast: they show 0, then 95, then 100, and no `estimated_finish_at`. Decide that a job has ended from `status`, never from `progress`.

All times are server time with a UTC offset. Compare an estimated time with the server's clock (the HTTP `Date` header of the answer), not with your own.

## Refusals and refunds

A job can fail long after it was accepted: dola may refuse it (copyright, safety, a question instead of an order) or render
nothing. This usually comes within about an hour; at the latest about 3 hours 30 minutes after acceptance (the render deadline, 2 hours
30 minutes by default, plus one final read that can take up to an hour). A failed job is **refunded** to the key's wallet, once: `refunded: true` in its status. `points` in the status is what the
job's charge took (its price, taken when it was made; a re-check after a refund charges it again, never twice). On a
refunded job, `points` is the amount that was refunded. Net for a client is therefore `refunded ? 0 : points`. Poll for it; do not assume
that a `queued` or `running` job will succeed.

## Errors

Every door except Gemini answers `{"error": {"code", "message", "type", "param"}}`, for example
`{"error": {"code": "input.bad_duration", "message": "...", "type": "invalid_request_error", "param": "duration"}}`.
Gemini paths answer the Gemini shape (`error.code` is the HTTP status, the relay code is `details[0].reason`, the field is in
`details[0].metadata.param`). A failed job shows the same `error` and `error_code` in its status. The full list of codes, with
HTTP status and whether the charge is returned, is in `docs/error-codes.md`.

## Capacity

The video pool is nine accounts, each with about **4 video points a day**: 36 points, so about **18 videos a day at 2 points
each** (a 10 s video on 2.5 costs 4). The day resets at **07:00 Vietnam time**. Plan your volume to this number; extra jobs are
queued and wait or are refused when the pool is empty.

## Examples

Native, video: create, then poll.

```bash
curl -s http://192.168.18.227:8100/v1/video/generations \
  -H "Authorization: Bearer rk-..." -H "Content-Type: application/json" \
  -d '{"model": "seedance-2.5", "prompt": "A paper boat drifting on a calm pond at dawn", "duration": 5, "ratio": "16:9"}'
# {"id": "<job id>", "status": "queued", "queue_position": 1, "warnings": []}

curl -s http://192.168.18.227:8100/v1/video/generations/<job id> -H "Authorization: Bearer rk-..."
# {"status": "done", "result_urls": ["http://192.168.18.227:8100/m/<token>"], "refunded": false, "points": 2, ...}
# while rendering: {"status": "running", "stage": "waiting", "progress": 45, "render_started_at": "2026-10-18T10:03:10+00:00", "estimated_finish_at": "2026-10-18T10:45:00+00:00", ...}
```

OpenAI door (name suffixes allowed here). If the answer says `still rendering`, poll the native video route with `job_id`.

```bash
curl -s http://192.168.18.227:8100/v1/chat/completions \
  -H "Authorization: Bearer rk-..." -H "Content-Type: application/json" \
  -d '{"model": "seedance-2.5-5s-16:9", "messages": [{"role": "user", "content": "A paper boat drifting on a calm pond"}]}'
# {"job_id": "<job id>", "choices": [{"message": {"content": "still rendering — poll job <job id>"}, "finish_reason": "length"}]}

curl -s http://192.168.18.227:8100/v1/video/generations/<job id> -H "Authorization: Bearer rk-..."
```

Gemini door. `responseId` is the job id; poll the same native route.

```bash
curl -s "http://192.168.18.227:8100/v1beta/models/seedance-2.5-5s-16:9:generateContent" \
  -H "x-goog-api-key: rk-..." -H "Content-Type: application/json" \
  -d '{"contents": [{"role": "user", "parts": [{"text": "A paper boat drifting on a calm pond"}]}]}'
# {"candidates": [{"content": {"parts": [{"text": "still rendering — poll job <job id>"}]}, "finishReason": "MAX_TOKENS"}], "responseId": "<job id>"}

curl -s http://192.168.18.227:8100/v1/video/generations/<job id> -H "x-goog-api-key: rk-..."
```
