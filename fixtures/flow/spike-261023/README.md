# Google Flow step-0 spike (2026-10-23, 12:31–13:29 GMT+7 / 05:31–06:29Z)

Plain HTTP (`curl_cffi`, Chrome TLS impersonation) from the LAN box through one Canadian exit proxy (Ontario), with
account acc1 (`~/relay-secrets-flow/flow-cookies-acc1.json`). **No generation was sent**: the account's Google session
died between 12:32 and 12:51 GMT+7, before any generate. `fixtures/flow/FINDINGS.md` sums it up.

| File | What |
|------|------|
| `auth_001_exit_ip.json` | exit IP country/region through the proxy (CA, Ontario) |
| `auth_002_boot_anon.json` | `GET flow.google.com/` with no cookies: 200, no `at` (`SNlM0e`), has `f.sid` (`FdrFJe`), `bl`, the reCAPTCHA site key |
| `auth_003_boot_plain_requests.json` | the same with cookies through Python `requests` (no TLS impersonation): signed in, `at` present |
| `auth_004_boot_root.json` | the same with cookies through `curl_cffi`: signed in |
| `auth_005_credits_before.json` | `nzlxg` (`VideoFxService.GetCredits`) replayed over HTTP: `[50,3,8,1,null,50,[<next refresh s>,<ns>]]` |
| `project_002_create_project.json` | `jHPbke` (`AiSandbox.CreateProject`): `["projects/*",[null,[<title>]],[null,22]]` → `[<project uuid>,[<title>]]` |
| `project_003_boot_project.json` | `GET /project/<id>`: 200, signed in |
| `html_001_boot_root.json`, `html_002_boot_project.json` | 19 min later, same cookies: 200 but signed out (no `at`, no email) |
| `osid_001_myaccount.json` | `myaccount.google.com` → 302 to `ServiceLogin`: the Google session itself (SID) is gone, not only flow's OSID |
| `osid_002_passive_login_hops.json` | passive `ServiceLogin?continue=flow.google.com` → the identifier page (sign-in needed) |
| `osid_003_boot_after_osid.json` | flow root after that: still signed out |
| `rotate_001_rotate_cookies.json` | one `POST accounts.google.com/RotateCookies` (Chrome's `__Secure-1PSIDTS` refresh): 401 |
| `rotate_002_boot_after_rotate.json` | flow root after that: still signed out |
| `yes_001_yescaptcha_one_task.json` | one YesCaptcha `RecaptchaV3TaskProxylessM1` (enterprise, action `IMAGE_GENERATION`): ready in 14.8 s, token 2574 chars, balance 9795 → 9770 |

Redaction: every URL query value is replaced by `x` (this covers `f.sid`; the rpcid and `bl` are kept in the
`rpcid` and `body.bl` fields); the project id is replaced by `33333333-3333-4333-8333-333333333333`. The records never held a
cookie value, the `at` token, the email, the YesCaptcha key or token, or the proxy login (the scripts log booleans,
lengths and names only). The signed-in HTML pages and the fetched JS stay on the box in `~/relay-flow-spike/`.
