# Google Flow live run (2026-10-23, 13:35–14:36 GMT+7 / 06:35–07:36Z)

Plain HTTP (`curl_cffi`, Chrome TLS) from the LAN box through the Canadian exit proxy, account acc1 re-exported at
06:34Z. Our own cookie jar was kept alive by `RotateCookies` every 10 min. Results: 3 images accepted
(`img2`, `img8`, `img12`), 1 Veo 3.1 Lite video accepted, many submits refused at the reCAPTCHA check.
`fixtures/flow/FINDINGS.md` sums it up.

| Files | What |
|-------|------|
| `keeper_timeline.json` | signed-in check every 5 min + `RotateCookies` every 10 min, 06:35–07:35Z: always signed in, rotation 200 with new `__Secure-1PSIDTS`/`3PSIDTS`/`SIDCC`s |
| `read_models_HTrJv.json` | `HTrJv` (GetModels), args `[]`: the model catalogue with per-tier credit costs |
| `read_listing_bad_arg.json` | `Zzl0ze` with `[<project id>]`: error `[3]` |
| `read_listing_Zzl0ze.json` | `Zzl0ze` with `["projects/<id>",null,null,null,[1]]`: the project contents (without `[1]` media are left out) |
| `read_getmedia_as29s.json`, `read_getmedia_not_found.json` | `as29s` with `[<media id>]` (fresh signed URLs) and with `["media/<id>"]` (error `[5]`) |
| `img1_*`, `img6_*` | YesCaptcha token, submit refused: `[7,…,PUBLIC_ERROR_UNUSUAL_ACTIVITY]` and `[8,…,PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC]` in 0.4 s |
| `img2_*` | the first accepted image: credits, listing before, token, written-ahead ids, 5 listings while the generate was in flight, the generate (16.6 s held reply), listing right after, credits after, the download |
| `img8_*` | second accepted image; `img8_listing_1min_later.json` is the first listing that shows it |
| `img9_*`, `img11_*`, `pagefetch_generate_refused.json`, `browser_mint.json` | tokens minted by headless / headed Chrome on the project page, and the same body sent by `fetch()` from inside the page: all refused |
| `img12_generate_after_cooldown.json` | YesCaptcha again after 12 min with no submits: accepted (9:16) |
| `video_*` | Veo 3.1 Lite text-to-video: submit (2.6 s), first/last `jwpduf` status reads, first/last listing reads, `as29s` read, the poster and mp4 downloads with ffprobe, credits before/after |

Redaction: every URL query value is replaced by `x` (signed URL `Expires`/`KeyName`/`Signature`, `f.sid`); the project
id is replaced by `33333333-3333-4333-8333-333333333333`; reCAPTCHA tokens appear only as `<token len N>`;
`body_raw` is dropped where the decoded `payload` is kept. A scan for the cookie values (export and our rotated jar),
the tokens, the YesCaptcha key, the proxy login, the project id, `f.sid` values and the email found 0 hits. The JPEGs,
the MP4 and the raw records stay on the box in `~/relay-flow-spike/raw/`.
