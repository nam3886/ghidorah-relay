# Google Flow: code 7 on video sends (25/10 and 03/11/2026)

`video_code7_sends.json`: the eight production Veo 3.1 Lite text-to-video sends that flow answered at once with the
code 7 envelope (PERMISSION_DENIED, `PUBLIC_ERROR_UNUSUAL_ACTIVITY`, the reCAPTCHA refusal): five on 25/10 (10:32 to
13:27 GMT+7) and three on 03/11 (11:16, 12:17 and 14:00 GMT+7; UTC in the file). Read from the relay's `jobs` and
`job_events`; the raw replies were not kept, so `envelope` is the item's shape as the relay parsed it (one item, no
result, code 7), written as flow sends it.

What it proves (FINDINGS "Code 7 on video 03/11"): each batch was written ahead and looked up in the project listing
five times over 15 min and never found, the last read of each answered 15 min or more after its send; the five of 25/10
spent no credit (FINDINGS "Catalog 25/10"). Both halves of the proof the morning-proof plan asked for: a video's code 7
in exactly the refusal shape made nothing, and is tried again with a new token like an image's. No cookie, token,
project id, proxy or email.
