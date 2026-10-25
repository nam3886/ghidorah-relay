# Google Flow input images and the full catalog (2026-10-25, 16:28-18:00 GMT+7 / 09:28-11:00Z)

Plain HTTP from the LAN box through the AI Pro account's own proxy, with the relay's session read from production
(read-only: no rotation, nothing written back) and the worktree's own `relay_flow` code (plan, submit, poll, fetch).
`fixtures/flow/FINDINGS.md` ("Inputs and the full catalog 25/10") sums them up.

| File | What |
|------|------|
| `models_HTrJv.json` | `HTrJv` (GetModels), args `[]`: every model key with `[4]` its credits per credit tier (1 free, 2 Pro, 3 Ultra), `[7]`/`[8]` the inputs each takes, `[9]` its most references, `[12]`/`[13]` its aspect codes, `[16]` its length, `[23]` its resolution |
| `upload_maseQ.json` | `maseQ` (UploadImage): the args flow's web app sends, the answer (the media record, then its workflow record; flow captioned the image itself) |
| `image_ratios.json` | Nano Banana 2.1 (`BELUGA`) drawn once at each aspect code 6-14: the size of what came back names the ratio |
| `input_jobs.json` | one live job per new mode: the plan's params, the outcome, the seconds, the file (ffprobe); `recover_*` are accepted videos fetched by their batch id after the harness stopped |

Redaction: the project id is the fixtures' placeholder `33333333-3333-4333-8333-333333333333`; signed link query values
are `x`; the image bytes of the upload are not kept. Media, workflow and batch ids are flow's own and name nothing of the
account. No cookie, token, email or proxy is in these files.
