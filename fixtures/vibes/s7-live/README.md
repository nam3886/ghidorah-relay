# vibes.ai S7 live spike (2026-10-23, 09:19–09:28 GMT+7 / 02:19–02:28Z)

Three text-to-image jobs from the LAN box, plain HTTP, account acc1, the German exit proxy production uses for it
(calls spaced 12 s or more, no checkpoint met). Each one asked vibes to draw "the same subject placed on a sandy beach
at sunset" with one reference image, a watercolour of a lighthouse on a cliff (S6's 720x720 start frame), sent the way
vibes' web app sends a "Character" (omni reference, `oref`). The request fields come from vibes' own web bundle
(the `/create` page chunks): `config.oref_image_ent_id` + `config.oref_image_url`, plus
`sourceContentItemIds: [{id, source: "oref"}]` when the reference is a content item of the account.

| Stage | Calls | Reference sent as |
|-------|-------|-------------------|
| `oref_*` | `POST /api/upload-image` (data URL; body replaced by its size), create batch, `POST /api/generate/images`, batch read, PNG download | `oref_image_ent_id`/`oref_image_url` of the upload, top-level `config` only |
| `oref_web_*` | create batch, generate, batch read, PNG download | the same upload, with the web app's full shape: the config also in every input, `directGeneration: true`, `generationType: "t2i"` |
| `oref_project_*` | `POST /api/projects` (one empty project), `POST /api/upload-media` (multipart), `POST /api/projects/<id>/upload` (turns the upload into a content item of the account), create batch with `projectId`, generate, batch read, PNG download | the content item: its `mediaEntId`/`imageUrl` plus `sourceContentItemIds: [{id, source: "oref"}]` |

What they showed:
- Every generate answered `success: true` with the finished `updatedBatch` in 7–11 s, like a plain text-to-image.
  The batch kept the oref fields in its `config` (and, in the third job, `sourceContentItemIds` and
  `hasUploadedAncestor: true` on the item), so the batch flow and its reads are unchanged by them.
- **The reference was ignored every time.** `data[].orefValues` came back `""` and the content item's `orefValues`
  was `null`; all three PNGs (1280x1280) show a person on a beach at sunset, with nothing of the lighthouse, the cliff
  or the watercolour look.
- The first upload-media attempt of the third job met two proxy drops (no answer, then a connect timeout) before it
  reached vibes; nothing was sent to vibes on those, and the project made on the first try was reused.

Redacted with the S6 rules (`vibes/s6-live/README.md`), and `uploadToken` values replaced. A scan for the cookie
values, the proxy login and host, the user ids and the upload token found 0 hits. The PNGs and the raw answers stay on
the box in `~/relay-vibes-spike/raw-i2i/`; the scripts are in `~/relay-vibes-spike/i2i/`.
