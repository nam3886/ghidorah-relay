# Google Flow credits and plan (2026-10-25, 10:12 GMT+7 / 03:12Z)

`nzlxg` (GetCredits, args `[]`) read for each of the three flow accounts from the LAN box, through each account's own
proxy, with no rotation and nothing written back. The answer is `[credits, PaygateTier, _, _, null, credits, [refill s,
ns]]`; flow's JS names the tier enum `{0 UNSPECIFIED, 1 TIER_ONE, 2 TIER_TWO, 3 NOT_PAID, 4 UNSUBSCRIBED_WITH_CREDITS,
5 ZERO, 6 EXEMPT, 7 GEMNOVA, 8 TIER1P5}`.

| Account | Answer | Read as |
|---|---|---|
| the AI Pro account (`credits_pro.json`) | `[1050, 1, 2, 2, null, 1050, [1794120126, …]]` | pro, 1050 credits, refill 2026-11-08 (monthly) |
| two free accounts | `[50, 3, 8, 1, null, 50, [read + 4 to 5 h, …]]` (same shape as `live-261023/video_credits_before.json`) | free, 50 credits, refill within a day |

No Ultra account exists: its code (2, TIER_TWO) comes from the JS enum only. Nothing in these files is a secret (no
cookie, token, project id, email or proxy).
