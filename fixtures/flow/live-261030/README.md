# Google Flow: code 8 on video sends (26-30/10/2026)

`video_code8_sends.json`: the four production Veo 3.1 Lite text-to-video sends that flow answered at once with the
code 8 envelope (RESOURCE_EXHAUSTED, `PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC`): 27/10 01:19, 27/10 09:51,
30/10 09:04 and 30/10 10:39 GMT+7 (UTC in the file). Read from the relay's `job_events` and `provider_state`; the raw
replies were not kept, so `envelope` is the item's shape as the relay parsed it (one item, no result, code 8), written
as flow sends it.

What it proves (FINDINGS "Code 8 on video 30/10"): each batch was written ahead and looked up in the project listing
five times over 15 min and never found; one was looked up again 1 h 40 min after its send and was still not listed.
Nothing was made: a video's code 8 is an account refusal like an image's (the job goes to another account), no longer
an unknown send. Code 7 on a video is not proven so and stays unknown. No cookie, token, project id, proxy or email.
