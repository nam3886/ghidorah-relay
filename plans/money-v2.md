# Money v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One provider-neutral money model: a relay sale price in points and a known upstream cost for every model
option, one points wallet per key with optional per-provider caps, account pools with windows and tiers, the actual
upstream cost and its mismatch alert on every job, and automatic refunds for missing outputs.

**Architecture:** Providers stop pricing. A provider's `plan()` returns a *price key* and a count. The core looks up
the sale price in `<p>.prices` and the upstream cost in the provider's cost table (`spec.costs(snapshots)`), and
refuses the job when either is missing. One migration (`0020_money_v2.sql`) wipes the job history and the money
tables, then recreates them. Account pool spend is no longer a stored counter: it is computed under the account lock
from `account_charges` against the pool's windows (`<p>.pools` setting) for the account's tier. A reported balance
wins until the next refill.

**Tech Stack:** Python 3.13, psycopg 3 / PostgreSQL 16, FastAPI, Vue 3 + Element Plus (admin), pytest with
`@mutation` markers (`scripts/mutate.py`), Vitest.

**Spec:** `/private/tmp/claude-501/-Users-namnguyen-Desktop-www-freelance-ghidorah-relay/7f973859-1ba8-4729-aeca-0f051a1360a0/scratchpad/money-model.md`
(the inventory, written at `432a018`), plus the owner's eight final decisions, restated below. Where the two differ, the decisions win.

## Owner decisions (final, verbatim intent)

1. Every model option has BOTH a sale price in points (admin Settings, shown live on `/v1/models`) AND a known upstream
   cost per option (model, resolution, duration, count). Missing either → refused at creation with a clear code. No
   "credits × factor" fallback, no guessed price. Every job records sale points, expected cost, actual cost (when
   reported) and margin. Actual ≠ expected → alert.
2. One points wallet per API key, relay-wide; 1 point is the single unit (dola on the same scale); optional per-provider cap.
3. Images that cost 0 upstream (flow, qwen) sell at a 1-point default.
4. A multi-output job (n = 1..4) that delivers fewer outputs is refunded the missing share automatically, exactly once.
5. Charged at creation. Any failure refunds exactly once. Invariants unchanged: write-ahead ids, `mark_sent` right
   before ONE paid send, never re-send, ambiguous → reconcile, NotTaken only when certain, refusal/out-of-credits ends
   the job with a refund.
6. Account tier free / pro / ultra, read from upstream where possible with the cycle start; otherwise set by the admin.
   The tier sets the pool windows and budgets. Ultra numbers come from measurement later.
7. Pools: a unit (credits, jobs, uses) and windows (calendar day in a time zone, calendar month from the cycle start,
   rolling N h, N h from first use). Windows add up (`sum`, flow) or must all hold (`all`, gemini). A reported balance
   wins until that window's next refill. Fix `pool.py:113-117`. Budgets, windows, rests are Settings with defaults.
8. Clean slate: wipe the money tables AND the job history; one new migration; drop the old money schema, no shim.

## Global Constraints

- Tier **L** for every phase: `scripts/web.sh check` + `build` + browser files of the changed pages (UI phases),
  `scripts/box.sh --tree <tree> -- scripts/check.sh <changed test files>`, full pytest, the mutation run of every marker
  file naming a changed source file (`MUTATE_ONLY=... scripts/check.sh --full`), one reviewer subagent, a Codex review.
  Deploy only after a full gate containing the commit is `CHECK: GREEN`.
- Box runs pin cores: `env RELAY_GATE_CPUS=0-7|8-15|16-23`; a whole-tree gate runs alone on `0-23`.
- Integer points only; integer upstream units only. All money SQL reads `clock_timestamp()`, never a caller's clock.
- Every money write calls `require_tx(conn)` first. Lock order stays: job → lease → account row → pool rows →
  key wallet rows (wallet rows in scope order `'*'` first, then the provider).
- No error text, setting or field names a provider inside `relay_core` (I18).
- Commit messages: no AI reference, no Co-Authored-By line. Secret scan (counts only) before each commit.
- Tunables are settings with defaults (owner rule 23/10), never new module constants.
- Error codes are registered in `relay_core/errors.py`; `docs/error-codes.md` is regenerated
  (`python -m relay_core.errors`).

## Review Focus

1. **A price or cost removed while jobs are queued.** Jobs already created keep their recorded price; only new
   creations are refused (`model.unpriced` / `model.uncosted`). Test in Task 1.3.
2. **A charge that passes `'*'` but fails the provider cap (or the reverse), then caught by Re-check.** `_recharge`
   catches `QuotaExceeded` and continues its transaction: the half-applied UPDATE must be rolled back by the charge's own
   savepoint. Test in Task 2.2.
3. **A reported balance read just before UTC midnight on a pool whose window is not a UTC day** (flow first-use, qwen).
   It must survive midnight and be replaced only at its own refill. Test in Task 2.4 (the `pool.py:113-117` bug).
4. **A shortfall refund on a job that a Recover later completes.** The recovered outputs are charged again, at most up
   to the missing share; the job's net never exceeds its price. Test in Task 5.1.
5. **Flow balance delta with a concurrent second job on the same account.** `account_slots == 1` for every provider
   today; the delta is only trusted under that bound. Test in Task 4.1 (a provider with `account_slots=2` records no delta).

---

## 1. Data model

### 1.1 Migration `0020_money_v2.sql` (one file, the only one this plan adds)

Order inside the file (one transaction, as `migrate.py` runs each file):

1. Wipe the job history: `TRUNCATE jobs RESTART IDENTITY CASCADE` (takes `job_events`, `job_media`, `leases`,
   `unclear_replies`, `commands` rows naming jobs, `ledger`, `account_charges`). Kept: `api_keys`, `accounts`,
   `settings_kv`, `prompt_frames*`, `admin_*`, `provider_snapshots`, `request_logs`, `billing_pin`,
   `worker_heartbeats`. Media objects become orphans the existing orphan sweep removes.
2. Drop: `billing_alerts`, `ledger`, `key_wallets`, `account_charges`, `account_capacity` (CASCADE).
3. `ALTER TABLE jobs DROP COLUMN wallet_kind, ADD COLUMN price_key text NOT NULL DEFAULT '',
   ADD COLUMN out_count int NOT NULL DEFAULT 1 CHECK (out_count BETWEEN 1 AND 4),
   ADD COLUMN cost_source text NULL CHECK (cost_source IN ('reply', 'balance_delta'))`.
   Kept with their meaning restated: `price` = total points charged (= price each × `out_count`), `account_cost` =
   expected upstream cost in the pool's unit, `upstream_price` = actual upstream cost in the pool's unit (stored only
   when taken), `pool`. Margin is derived on read (§ 6.4), never stored.
4. `ALTER TABLE accounts ADD COLUMN tier text NULL CHECK (tier IN ('free', 'pro', 'ultra')),
   ADD COLUMN tier_read_at timestamptz NULL, ADD COLUMN cycle_start date NULL`. NULL tier = `free`.
5. Recreate:

```sql
CREATE TABLE key_wallets (
    key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    scope text NOT NULL,                       -- '*' (the key's points wallet) or a provider name (its cap)
    day_limit int NULL CHECK (day_limit IS NULL OR day_limit >= 0),
    total_limit int NULL CHECK (total_limit IS NULL OR total_limit >= 0),
    used_day int NOT NULL DEFAULT 0 CHECK (used_day >= 0),
    used_total int NOT NULL DEFAULT 0 CHECK (used_total >= 0),
    day_anchor date NULL,
    PRIMARY KEY (key_id, scope));
CREATE TABLE ledger (
    id bigserial PRIMARY KEY, key_id uuid NOT NULL, scope text NOT NULL, job_id uuid NULL,
    provider text NULL,                        -- the job's provider; NULL only on an adjust
    entry text NOT NULL CHECK (entry IN ('charge', 'refund', 'adjust')),
    reason text NULL CHECK (reason IN ('failed', 'shortfall')),
    amount int NOT NULL, billing_day date NOT NULL, refund_of bigint NULL REFERENCES ledger(id),
    at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (key_id, scope) REFERENCES key_wallets (key_id, scope),
    CHECK ((entry = 'refund') = (refund_of IS NOT NULL AND reason IS NOT NULL)),
    CHECK (entry = 'adjust' OR amount >= 0), CHECK ((entry = 'adjust') = (job_id IS NULL)));
CREATE UNIQUE INDEX ledger_one_refund ON ledger (refund_of);   -- one refund row per charge row, full or partial
CREATE INDEX ledger_job ON ledger (job_id);
ALTER TABLE ledger ADD CONSTRAINT ledger_job_fk FOREIGN KEY (job_id) REFERENCES jobs(id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE account_pools (
    account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT, pool text NOT NULL,
    reported_left int NULL, reported_at timestamptz NULL, refill_at timestamptz NULL,
    rest_until timestamptz NULL, rest_reason text NULL, PRIMARY KEY (account_id, pool));
CREATE TABLE account_charges (
    id bigserial PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    job_id uuid NOT NULL REFERENCES jobs(id), pool text NOT NULL, amount int NOT NULL CHECK (amount >= 0),
    at timestamptz NOT NULL DEFAULT clock_timestamp(), refunded_at timestamptz NULL, UNIQUE (job_id, pool));
CREATE INDEX account_charges_window ON account_charges (account_id, pool, at) WHERE refunded_at IS NULL;
CREATE TABLE billing_alerts (
    id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT clock_timestamp(),
    alert text NOT NULL CHECK (alert IN ('clamp', 'drift', 'cost_mismatch')),
    key_id uuid NULL, scope text NULL, ledger_id bigint NULL REFERENCES ledger(id),
    amount int NULL, used_day int NULL, used_total int NULL,
    job_id uuid NULL, provider text NULL, price_key text NULL, expected int NULL, actual int NULL,
    detail text NULL, acknowledged_at timestamptz NULL, acknowledged_by text NULL,
    CHECK (alert = 'cost_mismatch' OR (key_id IS NOT NULL AND scope IS NOT NULL AND amount IS NOT NULL
                                       AND used_day IS NOT NULL AND used_total IS NOT NULL)),
    CHECK (alert <> 'clamp' OR ledger_id IS NOT NULL),
    CHECK (alert <> 'cost_mismatch' OR (job_id IS NOT NULL AND provider IS NOT NULL AND price_key IS NOT NULL
                                        AND expected IS NOT NULL AND actual IS NOT NULL)));
CREATE INDEX billing_alerts_at ON billing_alerts (at);
```

6. Grants (the 0006 / 0009 / 0012 matrix, unchanged in spirit): `key_wallets` api ALL, worker S+U; `ledger` S+INS both;
   `account_pools`, `account_charges` api S, worker ALL; `billing_alerts` S+INS both, api
   `UPDATE (acknowledged_at, acknowledged_by)`; accounts: `GRANT UPDATE (tier, cycle_start) ON accounts TO relay_api`
   (the admin sets a tier that cannot be read; auth-adjacent, reviewed in Codex). Sequences: `GRANT USAGE, SELECT ON
   ALL SEQUENCES IN SCHEMA relay`.
7. Wallets for the existing keys: one `'*'` row CLOSED (`day_limit = 0`) and one CLOSED row per provider that has
   accounts or settings (`SELECT DISTINCT provider FROM accounts`); `relay serve`'s `ensure_wallets` adds the rest at
   start. The owner re-opens each key in the admin.
8. Settings: `DELETE FROM settings_kv WHERE key ~ '\.(video|image)_budget$'` (replaced by `<p>.pools`). Stored
   `<p>.prices` are kept (owner data); an unknown key in them is dropped on load (`on_load`, as `disabled_models`).

`migrate.py`'s docstring rule "a file only adds, never drops what a running build still reads" does not hold for
0020: its docstring gets one line naming 0020 as the owner-approved contract migration that runs with every service
stopped (§ 9).

### 1.2 Code types (`relay_core/providers.py`)

| Type | Change |
|---|---|
| `PoolSpec` | `name`, `unit: Literal["credits", "jobs", "uses"]`. `tz` and `budget_setting` go (windows live in `<p>.pools`). |
| `Caps` | `wallet_kinds` goes. |
| `ModelSpec` | `prices: Mapping[str, int]` → `price_keys: Mapping[str, str]` (option label, e.g. `"10s"`, `"default"`, `"720p"` → price key). `limits["max_n"]` is read (default 1). |
| `Plan` | drop `wallet_kind`, `price`, `account_cost`; add `price_key: str`, `count: int = 1`. `pool` stays. |
| `Cost` (new) | `amount: int` (per count unit, pool unit), `source: Literal["live", "measured", "official"]`. |
| `ProviderSpec.costs(snapshots) -> Mapping[str, Cost]` (new) | price key → cost. Pure. |
| `Facts` | add `cost_source: Literal["reply", "balance_delta"] | None = None`; `upstream_price` = actual cost of the whole job, pool unit. |
| `Balance` | `pool`, `left_n`, `refill_at: datetime | None = None` (aware). `source` and `BALANCE_SOURCES` go (only reported balances exist now). |
| `Tier` (new effect) | `name: Literal["free", "pro", "ultra"]`, `cycle_start: date | None = None`. Added to `AccountEffect`. |

`_problems` (registry) adds: `<p>.prices` and `<p>.pools` must be declared; every price key of the catalog must be in
`spec.costs({})` OR be one the provider documents as live-only (none today); every `PoolSpec` must appear in the
`<p>.pools` default.

## 2. Pricing resolution

`relay_core/pricing.py` (new, ~80 lines):

- `prices_setting(provider, price_keys, default) -> Spec` — the one validator replacing the six `_prices` copies:
  a dict of known price key → whole points ≥ 0; unknown keys refused on write, dropped on load.
- `price_job(spec, plan, settings, snapshots, pools) -> Priced` where
  `Priced(price_key: str, count: int, price: int, account_cost: int)`:
  1. `each = settings[<p>.prices].get(plan.price_key)`; missing → raise `Unpriced` (`model.unpriced`, 403).
  2. `cost = spec.costs(snapshots).get(plan.price_key)`; missing → raise `Uncosted` (`model.uncosted`, 403).
  3. `plan.count` outside `1..model.limits.max_n` → `input.invalid` (param `n`).
  4. `account_cost = cost.amount * count`; when `pool.ceiling(pools[plan.pool])` is known and smaller →
     `TooCostly` (`input.too_costly`, 400) — replaces the flow/qwen plan-time budget refusals.
  5. `price = each * count`.
- `model_prices(spec, model, settings) -> dict[str, int | None]` for `/v1/models`.

New error rules (`errors.py`): `model.unpriced` 403 permission_error "this model has no price yet", `model.uncosted` 403
permission_error "this model's upstream cost is not known yet", `input.too_costly` 400 "no account can ever afford
this job". All `job_scoped=False`, `refund=False`.

### 2.1 Price keys and upstream cost sources per provider

Cost is always in the job's pool unit. "Live" = read by the worker's `refresh_catalog` into `provider_snapshots`;
the shipped table is the fallback and is what tests pin.

| Provider | Price key (options in it) | Pool / unit | Expected cost per count unit | Source |
|---|---|---|---|---|
| dola | `seedance_v1.0@5s`, `@10s` | video / credits | 1, 2 | measured (`catalog.py:22-28`) |
| dola | `seedance_v2.0@5s`, `@10s`, `@15s` | video / credits | 1, 2, 3 | measured |
| dola | `seedance_v2.5@5s`, `@10s`, `@20s`, `@25s`, `@30s` | video / credits | 2, 4, 2, 2, 2 | measured |
| dola | `seedream-4.5`, `seedream-5.0-pro` (one job = up to 4 pictures) | image / jobs | 1, 1 | measured (the 21-job/day ceiling counts jobs); 5.0 Pro "4x" open (Q6) |
| flow | `flow-nano-banana-2.1`, `-pro`, `-2-lite` | image / jobs | 1 | measured (0 credits, 50 → 50) |
| flow | `flow-veo-3.1-lite@8s` | video / credits | 10 | live `HTrJv` (`veo_3_1_t2v_lite`, tier 1/2), measured 50 → 40 |
| flow (table only, not in the catalog until captured) | `flow-veo-3.1-fast@{4,6,8}s` 20; `flow-veo-3.1-quality@8s` 100; `flow-omni-flash@{4,6,8,10}s\|720p` 7/10/12/15; `…\|360p` 4/5/6/7 | video / credits | as listed | official (support.google.com/flow/answer/16526234) |
| qwen | `qwen-image-3.0` | image / jobs | 1 | measured (0 credits for 8 images) |
| qwen | `qwen-happyhorse-1.1@{5,10,15}s` | video / credits | 30, 60, 90 | live `GET /api/v2/credits/pricing`, else `catalog.VIDEO_CREDITS` |
| qwen | `qwen-happyhorse-1.0@{5,10,15}s` | video / credits | 40, 80, 120 | same |
| gemini | `gemini-nano-banana-2` | image / uses | 1 | measured (no per-image figure exists; quota is relative) |
| meta | `meta-image`, `meta-video` | image, video / jobs | 1 | measured (one chat = one job) |
| vibes | `vibes-image`; `vibes-video@480p`, `vibes-video@720p` | image, video / jobs | 1 | measured (used-up flag only) |

Default sale prices (`<p>.prices` defaults; the owner edits them): today's values on the same keys (dola = its measured
credits; flow video 2; qwen video `d // 5`; meta/vibes/gemini 1), images that cost 0 upstream at **1** (decision 3).
Keys with no default (flow Fast/Quality/Omni) are absent → `model.unpriced` until the owner sets one.

## 3. Wallet operations (`relay_core/billing.py`)

- `charge(conn, key_id, provider, points, job_id) -> Charge(ledger_ids: tuple[int, ...], billing_day)`: inside a
  savepoint (`async with conn.transaction()`), ONE conditional UPDATE over `scope = ANY(ARRAY['*', provider])` with the
  existing day/total predicates (text of `_CHARGE` lines 26-40 kept), `RETURNING w.scope, d.today`. Fewer than 2 rows
  updated → the savepoint rolls back and `_explain_refusal` raises `WalletMissing` / `KeyDisabled` / `QuotaExceeded`.
  One ledger `charge` row per scope.
- `refund(conn, charge_id, *, amount: int | None = None, reason: str = "failed") -> Refund`: unchanged algorithm
  (ON CONFLICT (refund_of), same-day rule, clamp alert) with `kind` → `scope` and a partial `amount` (≤ the charge).
- `create_wallets(conn, key_id, scopes: Mapping[str, Limits])`, `ensure_wallets(conn, provider)`: a provider registered
  later gets a CLOSED cap row on every key (keeps I29; see Q7).
- Job net points = Σ over `scope = '*'` rows (`_NET` in `recheck.py`, `_STATUS` in `doors.py`, checker I8).

## 4. Pool and window engine (`relay_core/pool.py`)

Setting `<p>.pools` (dict), validated by `pool.pools_setting(provider, pool_specs, default) -> Spec`:

```json
{"video": {"combine": "sum", "windows": [
   {"name": "day", "kind": "first_use", "hours": 24, "budget": {"free": 50, "pro": 50}},
   {"name": "month", "kind": "month", "budget": {"pro": 1000}}]}}
```

`kind`: `day` (needs `tz`), `month` (anchored on `accounts.cycle_start`, default the 1st), `rolling` (needs `hours`),
`first_use` (needs `hours`). A window with no budget for the account's tier is unknown (allows), as today.

Pure functions (unit-tested without a database):

- `window_start(w, now, cycle_start, charges) -> datetime | None` — `day`: local midnight in `w.tz`; `month`: last
  cycle anniversary (day clamped to the month length); `rolling`: `now - hours`; `first_use`: walk the charges oldest
  first: a window opens at the first charge at/after the previous window's end and lasts `hours`; None when no window
  is open now.
- `left_of(cfg, tier, cycle_start, charges, now, reported) -> int | None`:
  - `reported` (`reported_left`, `reported_at`, `refill_at`) wins while no window refilled after `reported_at`
    (refill = `refill_at` when given, else the earliest computed next refill after `reported_at`):
    `reported_left - Σ unrefunded charges at > reported_at`.
  - else `all`: `min_w(budget_w - spent_w)`; `sum`: charges are allocated oldest first to the window that refills
    first (daily before monthly), `left = Σ max(0, budget_w - allocated_w)`. Any window with no budget → None.
- `next_refill(cfg, tier, cycle_start, charges, now) -> datetime | None` — `sum`: earliest window refill; `all`: the
  latest refill among exhausted windows.
- `ceiling(cfg) -> int | None` — the largest amount any tier can ever hold (for `input.too_costly`).

Database functions (names kept so callers and anchors stay): `pool_configs(caps, get_setting)`, `ensure_rows`,
`lock_capacity(conn, account_id, cfg) -> Capacity(account_id, pool, left_n, rest_until, rest_reason)` (locks the
account `SKIP LOCKED`, then the `account_pools` row, reads the tier and the charges of the longest open window, returns
the effective `left_n`), `reserved`, `can_afford` (unchanged), `charge_account` (insert only; no counter),
`refund_job(conn, job_id, pools)` (sets `refunded_at`; the day restriction goes, since spend is derived),
`apply_effects` (Balance → `reported_*`, and when `left_n` = 0 a rest until `refill_at` or `next_refill`; `Tier` →
`accounts.tier, tier_read_at, cycle_start`). Deleted: `next_pool_reset`, `check_drift`, `Drift`, `_logged_drift`, the
day rollover in `_lock` (the bug at `pool.py:113-117`), reaper `_pool_drift`, `ops.restore_mark`'s `left_n` reset
(becomes `UPDATE account_pools SET reported_left = NULL, reported_at = NULL`).

Default `<p>.pools` (settings; owner tunes):

| Provider | Pool | combine | Windows and budgets |
|---|---|---|---|
| dola | video (credits) | sum | `day` UTC, free 4 (measured 2 on one account 24/10; reported balance wins) |
| dola | image (jobs) | sum | `day` UTC, free 21 (measured 260921; replaces 40) |
| flow | video (credits) | sum | `first_use` 24 h free 50 / pro 50; `month` pro 1000 |
| flow | image (jobs) | sum | none (unknown: allows; pacing rest stays) |
| qwen | video (credits) | sum | `day` UTC, free 40 (reset hour unconfirmed) |
| qwen | image (jobs) | sum | none |
| gemini | image (uses) | all | `day` UTC free 100 (unmeasured, kept as today's guard); `rolling` 5 h and `rolling` 168 h without budgets until measured |
| meta | image / video (jobs) | sum | `day` UTC free 40 / 20 (unmeasured) |
| vibes | image / video (jobs) | sum | `day` UTC free 1000 / 1000 (unmeasured; used-up flag rests) |

## 5. Tier, refill and actual cost per provider

| Provider | Tier (+ cycle) | Balance refill | Actual cost (`Facts.upstream_price`, `cost_source`) |
|---|---|---|---|
| dola | not reported (free only) | none | video: the stated price (`reply`), else before − after (`balance_delta`, existing `_facts`); image: none. Out of credit: `Balance(kind, 0)` instead of `_rest` (core rests until refill) |
| flow | not readable yet → admin (Q5) | `nzlxg` `payload[6][0]` epoch → `refill_at` (check + pre-send read) | video: pre-send `nzlxg` minus the accept reply's balance (`balance_delta`) |
| qwen | `memberType` (member/level/query) → `Tier` | none | video: `consuming N Credits` in the accept text (`reply`) |
| gemini | `jSf9Qc` tier 1 → free, else none (admin) | `jSf9Qc` reset of an exhausted window → `Balance(image, 0, refill_at)` | none |
| meta, vibes | not reported | vibes used-up flag → `Balance(pool, 0)` | none |

The core writes `upstream_price` and `cost_source` only when taken (I7 rule unchanged). In the same transaction, when
`upstream_price IS NOT NULL AND upstream_price <> account_cost`, `billing_alerts` gets a `cost_mismatch` row (job,
provider, price_key, expected, actual).

## 6. Partial delivery, API and admin

### 6.1 Partial delivery
`apply_fetched` (same anchors): after the media rows, `delivered = len(media)`; when `delivered < out_count` and the
job has unrefunded charge rows, each charge row gets `refund(charge_id, amount=row.amount // out_count * missing,
reason="shortfall")` in the done transaction. More outputs than asked: no extra charge (26/09 rule). The account
charge stays (upstream spent it). Recover of a done job with a shortfall charges again `price_each × newly attached`
up to the missing share (`_recharge(..., target)`).

### 6.2 Public API
- `/v1/models`: `prices` = `{label: points | null}` from `pricing.model_prices` (live setting); `limits.max_n`.
- `/v1/points`: `{"points": {day_limit, used_day, total_limit, used_total}, "providers": {p: cap row}, "models",
  "unfinished_jobs", "max_unfinished_jobs"}`.
- Status: `points` = net on `'*'` rows; `refunded` stays a bool (true = fully refunded, failed); new
  `refunded_points` and `delivered`; OpenAPI text in `api/schemas.py` updated.
- `n` honoured only up to `max_n` (1 everywhere today); dola, meta, vibes switch from ignoring `n` to refusing n > 1.

### 6.3 Admin API
- `/admin/keys`: `wallets: [{scope, mode, day_limit, total_limit, used_day, used_total}]`, `'*'` first.
- `/admin/prices`: one row per (provider, model, label, price_key): `points` (setting or null), `cost`, `unit`,
  `cost_source`, measured `jobs`, `mismatched`, `lowest_actual` (keeps the substitution exclusion `SUBSTITUTION`).
- `/admin/accounts`: `tier`, `tier_read` (bool), `cycle_start`, `pools: [{pool, unit, left, source:
  reported|computed|unknown, refill_ms, rest_until_ms, rest_reason, windows: [{name, kind, budget, spent, refill_ms}]}]`;
  PATCH takes `tier`, `cycle_start`.
- `/admin/stats/pool`: per provider, per pool, with its unit; never summed across providers. `drift` field gone.
- `/admin/jobs/{id}`: `price_key`, `out_count`, `delivered`, `unit`, `cost_source`, `margin`.
- Alerts hub: billing groups are `(alert, key_id, scope)` for clamp/drift and `(alert, provider, price_key)` for
  `cost_mismatch`.

### 6.4 Margin
`margin = price - (upstream_price ?? account_cost) × <p>.unit_value[pool]`, computed on read; null while the owner has
not set `<p>.unit_value` (points one upstream unit is worth, reporting only, never used to charge). See Q3.

### 6.5 Admin UI
Keys form/list (`KeyFormDialog.vue`, `WalletLines.vue`, `key-model.ts`, `KeysView.vue`): one points wallet + per-provider
cap rows. Settings: `PricesTable.vue` becomes the price table (sale points editable, cost/unit/source, measured);
`<p>.pools` and `<p>.unit_value` use the generic dict field. Accounts (`account-model.ts`, `AccountsView.vue`,
`AccountLanes.vue`): tier chip + edit, per-window bars. Jobs drawer (`JobTechnical.vue`): price key, n/delivered,
expected/actual cost with unit, margin. Alerts (`alerts-model.ts`): cost-mismatch group words.

---

## 7. Phases

Each phase is one tier-L hand-over and one deploy. Phase 2 is the only one with a migration.

### Phase 1 — Core pricing on the existing schema (no migration)

Shippable alone: the core prices every job from `<p>.prices` + `spec.costs()`, refuses unpriced/uncosted models, and
`/v1/models` shows live prices. The old columns are still filled (`wallet_kind` = media type, `price`, `account_cost`).

**Task 1.1 — types and pricing module.**
Files: create `core/pricing.py`, `tests/test_pricing.py`; modify `core/providers.py` (`Plan`, `Cost`, `ModelSpec.price_keys`,
`costs`, registry checks), `core/errors.py`, `docs/error-codes.md`, `core/testing/fake_provider.py` (`costs` from
option-free table `{"fake-video": Cost(1, "measured"), "fake-image": Cost(1, "measured")}`, `<fake>.prices` setting
default `{"fake-video": 10, "fake-image": 1}`; tests that steered `price` through options set the setting instead).
Tests: `test_a_missing_sale_price_refuses_model_unpriced_and_charges_nothing`,
`test_a_missing_upstream_cost_refuses_model_uncosted`, `test_price_is_each_times_count_and_cost_is_cost_times_count`
(each 7, count 3 → price 21, cost 3 → account_cost 9), `test_a_count_above_max_n_is_input_invalid`,
`test_an_unknown_price_key_in_the_setting_is_refused_on_write_and_dropped_on_load`.
Markers to add: on `pricing.py` the two refusals, the multiplication, `max_n`, the unknown-key check (5).

**Task 1.2 — six providers quote price keys.**
Files: each provider's `spec.py`, `catalog.py`, `constants.py` (dola, flow, qwen, gemini, meta, vibes): `plan()` returns
`price_key`/`count`; `catalog()` returns `price_keys`; `costs(snapshots)` from the table in § 2.1 (flow reads the
`models` snapshot from `HTrJv`, qwen the `pricing` snapshot, both falling back to the shipped table); delete
`_prices`, `lookup_price`, `FALLBACK_*`, `*_ACCOUNT_COST`, `WARN_PRICE_FALLBACK` uses, their `FixedLimit` rows, and
the flow/qwen plan-time budget refusals. dola/meta/vibes refuse `n > 1` (`input.invalid`).
Flow and qwen `run_command("refresh_catalog")` return the new snapshots; `Caps.catalog_refresh_s` set for both (a
setting-backed value per the tunables rule: `<p>.catalog_refresh_s`, default 21600).
Tests (rewrite in place): `test_plan.py`, `test_catalog.py`, `test_dola_settings.py`, `test_flow_spec.py`,
`test_qwen_spec.py`, `test_meta_spec.py`, `test_vibes_spec.py`, `test_gemini_spec.py`; new
`test_every_price_key_has_a_cost_and_a_default_price` per provider (except flow Fast/Quality/Omni, which are not in the
catalog), `test_a_live_cost_snapshot_overrides_the_shipped_table` (flow, qwen).
Markers broken here: dola `spec.py:93,95,247,253`, `catalog.py:299`, `constants.py:36,38`; flow `spec.py:192,200`;
qwen `spec.py:180,183,187`; meta `spec.py:187`; vibes `spec.py:190` (15). Markers to add: one per provider on its
`costs` table lookup and one on the `n > 1` refusal of dola/meta/vibes (9).

**Task 1.3 — the door prices and the catalog shows live prices.**
Files: `core/api/doors.py` (`plan_request` calls `pricing.price_job`), `core/jobs/create.py` (`NewJob.priced`),
`core/jobs/recheck.py` (`regenerate_job` takes the priced plan), `core/api/routes_catalog.py`
(`model_entry` prices from settings), `core/admin/routes_keys.py` (`/models` same), `core/admin/routes_jobs.py`
(regenerate). Tests: `test_models_show_the_live_sale_price_and_null_when_unset`,
`test_a_price_removed_after_creation_leaves_the_queued_job_priced_as_created` (Review Focus 1),
`test_regenerate_prices_today`. Markers to add: `model_entry` live lookup, `plan_request` pricing call (2).

Checks: `scripts/box.sh --tree <tree> -- env RELAY_GATE_CPUS=<set> scripts/check.sh` with the files above, then full
pytest, `MUTATE_ONLY` over the changed source files.

### Phase 2 — The money schema: migration 0020, wallets, pool port (+ keys UI)

**Task 2.1 — migration 0020.** Files: `core/db/migrations/0020_money_v2.sql`, `core/db/migrate.py` (docstring),
`tests/test_migrations.py` (`TABLES`: `account_capacity` → `account_pools`; `INDEXES` add `account_charges_window`),
`tests/test_roles.py` (matrix rows for the new tables and the two account columns).
Tests: `test_0020_wipes_jobs_and_money_and_keeps_keys_accounts_settings_frames`,
`test_0020_leaves_every_key_with_closed_star_and_provider_rows`, `test_0020_drops_the_budget_settings`.
Markers broken: `0002:46,47`, `0006:23`, `0012:5`, `0018:6` (5) — re-anchored on `0020` (5 new markers: the refund
unique index, `ledger_job`, the api grant on `account_pools`, the acknowledge grant, the window index).

**Task 2.2 — wallets.** Files: `core/billing.py`, `core/keys.py`, `core/jobs/create.py`, `core/jobs/fail.py`,
`core/jobs/recheck.py`, `core/jobs/sweeps.py` (ledger check per `(key_id, scope)`), `core/alerts.py` + `routes_alerts.py`
(scope columns, group form), `core/api/routes_catalog.py` (`/v1/points`), `core/api/doors.py` (`_STATUS` on `'*'`,
`refunded_points`), `core/api/schemas.py`, `core/testing/checker.py` (I8/I21/I3 on `'*'` rows),
`core/admin/routes_keys.py`, `apps/relay/src/relay_cli/main.py` (`ensure_wallets(conn, spec.name)`, `key create`
flags `--points-day/--points-total`), web keys files.
Tests (rewrite `test_billing.py`, `test_keys.py`, `api/test_admin_keys.py`, `api/test_catalog_routes.py`):
`test_a_charge_takes_the_star_wallet_and_the_provider_cap_together_or_neither`,
`test_a_provider_cap_refusal_inside_recheck_leaves_the_star_wallet_untouched` (Review Focus 2),
`test_eight_concurrent_charges_never_pass_the_star_day_limit`, `test_a_new_provider_is_closed_on_existing_keys`,
`test_status_points_count_only_the_star_rows`, `test_points_route_shows_the_wallet_and_the_caps`.
Markers broken: `billing.py:204`, `create.py:202`, `recheck.py:94`, `doors.py:515×2,516×2,517`, `alerts.py:388`,
`main.py:244`, web `key-model.ts:36,108,112`, `alerts-model.ts:54` (15). Markers to add: the savepoint rollback, the
2-row count check, `'*'` filter in `_NET`, `_STATUS` and I8, `ensure_wallets` closed row, the key-model scope order (7).
UI: Keys list and form, before/after screenshots.

**Task 2.3 — pool port onto `account_pools` / `account_charges.at` with `day` windows.**
Files: `core/pool.py` (pure window functions for `day` + `reported`; DB functions above), `core/jobs/claim.py`
(`_CANDIDATES` on `account_pools`), `core/jobs/apply.py` (Balance/rest wiring unchanged at its anchors),
`core/jobs/reaper.py` (drop `_pool_drift`), `core/jobs/runctx.py` (capacity map from `lock`-free `left_of` read),
`core/jobs/ops.py`, `core/db/accounts.py` (delete checks name `account_pools`), `core/admin/routes_common.py`
(`pool_point` → `pool_view`), `core/admin/routes_accounts.py`, `core/admin/routes_stats.py` (per provider),
`scripts/soak.py`, provider `spec.py` `PoolSpec`s and `<p>.pools` defaults, dola `engine/submit.py` (`_rest` and
`next_pool_reset` removed; `Balance(kind, 0)`), flow/qwen/vibes `Balance` calls (no `source`).
Tests (rewrite `test_pool.py`, `test_claim.py`, `api/test_admin_stats.py`, `api/test_admin_accounts.py`):
`test_a_reported_balance_survives_utc_midnight_until_its_own_refill` (Review Focus 3, the 113-117 bug),
`test_left_is_the_day_budget_minus_unrefunded_charges_of_the_day`, `test_a_zero_balance_rests_the_pool_until_the_next_refill`,
`test_a_refund_of_yesterdays_charge_changes_nothing_today`, `test_stats_pool_never_sums_two_providers`.
Markers broken: `pool.py:113,156,185×2,221,230,235,272`, `providers.py:323`, `routes_common.py:44×2,45×2,47`,
`routes_stats.py:56×2,174,175,203,209,214,215,220`, `reaper.py:158`, `ops.py:74`, dola `submit.py:1606,1642` (28).
Markers to add: reported-wins condition, refill-after-report check, charge insert idempotence, refund `refunded_at`,
rest-until-refill, per-provider stats grouping (6).

### Phase 3 — Windows, combine and tiers

**Task 3.1 — window kinds.** `month`, `rolling`, `first_use`, `combine: all|sum`, `next_refill`, `ceiling`,
`input.too_costly` in `pricing.price_job`. Tests (pure, no DB): flow `first_use` 50 + `month` 1000 allocation
(spend 60 in one day on pro → day 0 left, month 990 left); gemini `all` 5 h/168 h; month clamp on 31 → 28 Feb;
`test_a_cost_above_every_tiers_ceiling_is_input_too_costly` (qwen HappyHorse 1.0 15 s = 120 on free 40). Markers to add:
one per window kind's start rule, the sum allocation order, the all-min, the ceiling (6).

**Task 3.2 — tiers.** `Tier` effect in `pool.apply_effects`; admin PATCH `tier`/`cycle_start`
(`routes_accounts.py`, column grant from 0020); qwen check reads `memberType`; gemini check reads `jSf9Qc`
(tier + exhausted-window refill); flow check and pre-send read keep `nzlxg` refill stamp → `Balance.refill_at`.
Tests: `test_a_pro_account_gets_the_pro_budgets`, `test_an_upstream_tier_overwrites_an_admin_tier_and_marks_it_read`,
qwen/gemini/flow check fixtures (`fixtures/qwen/spike-261023`, `fixtures/gemini`, `fixtures/flow/live-261023/video_credits_before.json`).
Markers to add: tier lookup in `left_of`, the Tier write, each provider read (5). UI: account tier chip/edit and window bars.

### Phase 4 — Actual cost and the mismatch alert (+ price table UI)

Files: `core/jobs/apply.py` (store `cost_source`, write `cost_mismatch` in the taken transaction), flow
`engine/submit.py` (`video_answer` gets the pre-send balance: `Facts(before - after, cost_source="balance_delta")`,
only when `caps.account_slots == 1`), qwen `engine/submit.py` (`consuming (\d+) Credits` → `Facts(n, "reply")`),
dola `engine/submit.py` (`_facts` sets `cost_source`), `core/admin/routes_prices.py` (price table), `core/alerts.py`
(cost-mismatch group), web `PricesTable.vue`, `SettingsView.vue`, `JobTechnical.vue`, `api/settings.ts`,
`api/jobs-detail.ts`.
Tests: `test_a_taken_job_whose_actual_cost_differs_raises_one_cost_mismatch_alert`,
`test_no_actual_cost_no_alert`, `test_flow_balance_delta_is_the_cost_only_with_one_slot` (Review Focus 5),
`test_qwen_reads_the_consumed_credits_from_the_accept_text`, `test_price_table_lists_every_key_with_points_cost_and_mismatches`.
Markers broken: `routes_prices.py:21,22`, web `PricesTable.vue:33`, `SettingsView.vue:87`, `JobTechnical.vue:30,39` (6).
Markers to add: the mismatch condition, flow delta guard, qwen parse, price-table join, margin null-when-unvalued (5).

### Phase 5 — Multi-output shortfall refunds

Files: `core/jobs/apply.py` (`apply_fetched` shortfall), `core/billing.py` (partial `amount`), `core/jobs/recheck.py`
(`_recharge(..., target)` for Recover), `core/testing/checker.py` (I8: net ∈ {0, price, price − shortfall}),
`core/testing/fake_provider.py` (`max_n` 4, outputs from options), `api/doors.py` (`delivered`).
Tests: `test_three_of_four_outputs_refund_one_quarter_once`, `test_a_repeated_fetch_apply_refunds_nothing_twice`,
`test_more_outputs_than_asked_cost_nothing_extra`, `test_recover_of_a_short_job_charges_at_most_the_missing_share`
(Review Focus 4), checker planted-violation case. Markers to add: the `delivered < out_count` guard, the amount
formula, the once-only (unique refund_of), the recover cap, the I8 shortfall clause (5).
No provider sets `max_n > 1` in this phase (Q4).

**Markers to add, total: 61** (1.1: 5, 1.2: 9, 1.3: 2, 2.1: 5, 2.2: 7, 2.3: 6, 3.1: 6, 3.2: 5, 4: 5, 5: 5, plus the
6 Vitest-backed web markers named in their tasks). **Existing markers whose anchors break: 66** (Appendix A).

## 8. Rollout and deploy order

1. Phases 1 → 5 merge to `main` in order; each gets its full gate (`check.sh --full`, `RELAY_GATE_CPUS=0-23`) before
   its deploy.
2. Phase 2 deploy (the only destructive one): drain every provider (`relay provider drain <p>`), wait until no job is
   `submitting`/`waiting`/`unknown`/`fetching`, `pg_dump` the relay schema to `~/relay-backups/pre-0020-<sha>.dump`,
   stop api + workers, `scripts/deploy.sh <sha> --allow-migrate`, re-open keys in the admin, set prices/tiers,
   resume providers one at a time (dola last: browser).
3. Rollback after 0020 is not `rollback.sh` alone: restore the dump, then `scripts/rollback.sh --allow-schema-mismatch`.
4. Phases 3-5 have no migration: ordinary deploys; `rollback.sh` works.

## 9. Risks

1. **0020 is destructive** and breaks the expand/contract rule; running it with an old api alive crashes that api.
   Mitigation: § 8 step 2; never run on a database with real customer money.
2. **Flow credit order and refill are partly unconfirmed** (daily vs monthly spend order, first-use stamp). The
   pre-send `nzlxg` read stays the real guard; the reported balance wins.
3. **Ultra accounts pay a different cost** (Lite 5 vs 10). The expected cost is quoted at the standard tier, so Ultra
   jobs raise `cost_mismatch` until per-tier cost lands with the first Ultra account.
   `# ponytail: standard-tier cost; per-tier expected cost when Ultra accounts exist.`
4. **Spend derived from charges** costs one indexed range scan per claim candidate.
   `# ponytail: derived spend; cache a counter only if claim latency shows it.`
5. **Removing the fallback price refuses jobs** until the owner prices every key: after Phase 1 deploy, check
   `/admin/prices` for null rows before resuming claims.
6. **Mutation re-run volume**: 1,225 markers sit on the 72 source files this plan changes; the narrowed runs per phase
   must cover each phase's files, and anchors elsewhere must be checked before the long gate (lesson 22/10).

## 10. Open questions (code contradicts or does not cover a decision)

- **Q1 (decision 5).** "Out-of-credits ends the job with a refund" does not match the code: dola NO_POINTS is
  `NotTaken(pool.account_out_of_points)` (requeued to another account), flow/qwen pre-send shortage is
  `Retry(pool.account_out_of_points)`, and the registry forbids `pool.*` from being terminal. Only qwen's post-send
  "Insufficient Credits" is `Fail` with a refund. The plan keeps the code (requeue when certainly not taken). Confirm,
  or rule that dola's post-send NO_POINTS ends the job.
- **Q2 (decision 8).** `migrate.py` states migrations never drop what a running build reads; 0020 must run with every
  service stopped and makes rollback a database restore (§ 8). Confirm that downtime.
- **Q3 (decision 1, margin).** Points and upstream units have no exchange rate, and decision 1 forbids pricing by a
  factor. The plan adds `<p>.unit_value` (reporting only) and shows margin null until it is set. Confirm.
- **Q4 (decision 4).** No provider can deliver n > 1 today (flow/gemini/qwen refuse, dola/meta/vibes ignore `n`; flow's
  ×N request is not captured). The core supports it; providers stay at `max_n = 1`. Is a dola/meta image job (up to 4
  pictures) one set (plan) or n = 4 with a refund per missing picture?
- **Q5 (decision 6).** Flow's own account tier and every provider's cycle start are not in any captured reply
  (`HTrJv` lists cost per tier, `nzlxg` fields are unexplained). Admin sets them until a Pro capture shows the field.
- **Q6 (decision 1).** dola Seedream 5.0 Pro: the UI says "4x credit usage" but its effect on the 21-job ceiling is
  unmeasured; the plan costs it 1 job. Quota-only providers (meta, vibes, gemini, dola images) have no credit cost; the
  plan's cost is 1 job/use in the pool unit.
- **Q7 (decision 2).** A relay-wide wallet would open a newly registered provider on every `'*'` key, which I29
  forbids today. The plan keeps a closed per-provider cap row for new providers. Confirm.

## Appendix A — existing markers whose anchors break (66)

- `web/components/alerts/alerts-model.ts` (1): :54 `test_web_mutation_r2_keys_logs_alerts.py::test_the_alert_names_and_live_words_are_the_canvas_words`
- `web/components/jobs/JobTechnical.vue` (2): :30, :39 `test_web_mutation_r1_job_drawer.py::test_the_drawer_draws_media_only_from_safe_links_and_shows_the_actions_the_job_allows`
- `web/components/keys/key-model.ts` (3): :36 `…::test_an_unlimited_wallet_draws_a_bar_as_a_share_of_the_busiest_key`; :108, :112 `…::test_the_wallets_are_listed_video_first` (`test_web_mutation_r2_keys_logs_alerts.py`)
- `web/components/settings/PricesTable.vue` (1): :33 `test_web_mutation_r3.py::test_settings_rules`
- `web/views/SettingsView.vue` (1): :87 `test_web_mutation_r3.py::test_settings_errors_as_drawn`
- `apps/relay/src/relay_cli/main.py` (1): :244 `test_cli.py::test_serve_backfills_closed_wallets_for_a_newly_declared_pair_and_leaves_the_others`
- `provider-dola/catalog.py` (1): :299 `test_catalog.py::test_every_model_has_the_display_name_the_design_draws`
- `provider-dola/constants.py` (2): :36 `test_plan.py::test_a_tier_missing_from_the_table_uses_the_fallback_logs_it_and_warns`; :38 `test_plan.py::test_an_image_with_a_dear_model_still_costs_the_account_one_job`
- `provider-dola/engine/submit.py` (2): :1606 `test_submit_outcomes.py::test_zero_credits_left_with_nothing_else_recognised_is_unclear_and_rests_the_pool`; :1642 `test_submit_outcomes.py::test_the_pool_that_rests_is_the_jobs_kind_not_dolas_wording`
- `provider-dola/spec.py` (4): :93 `test_dola_settings.py::test_a_wrong_typed_price_table_is_refused_naming_the_fault`; :95 `test_dola_settings.py::test_a_negative_price_is_refused`; :247 `test_plan.py::test_plan_ignores_price_account_cost_and_timeout_in_options`; :253 `test_plan.py::test_a_video_costs_the_account_its_price_and_an_image_costs_one`
- `provider-flow/spec.py` (2): :192 `test_flow_spec.py::test_what_the_video_capture_did_not_prove_is_refused`; :200 `test_flow_spec.py::test_a_veo_video_is_planned_as_recorded_and_costs_its_flow_credits_against_the_video_pool`
- `provider-meta/spec.py` (1): :187 `test_meta_spec.py::test_a_model_missing_from_the_prices_is_charged_the_fallback_with_a_warning`
- `provider-qwen/spec.py` (3): :180 `test_qwen_spec.py::test_a_video_that_needs_more_credits_than_an_account_has_a_day_is_refused`; :183 `…::test_a_video_is_detached_in_the_video_pool_and_costs_the_account_its_credits`; :187 `…::test_a_video_is_priced_by_model_and_seconds_and_a_missing_key_warns`
- `provider-vibes/spec.py` (1): :190 `test_vibes_spec.py::test_a_model_missing_from_the_prices_is_charged_the_fallback_with_a_warning`
- `core/admin/routes_common.py` (5): :44 `test_admin_accounts.py::test_the_list_shows_what_the_next_claim_will_see_once_a_pool_day_is_over`; :44, :45, :45 `test_admin_stats.py::test_the_pool_panel_counts_points_unknowns_drift_proxy_failures_and_the_last_full_check`; :47 `test_admin_account_extras.py::test_a_counted_value_that_disagrees_with_the_charges_is_flagged_and_a_reported_one_never_is`
- `core/admin/routes_prices.py` (2): :21, :22 `test_admin_prices.py::test_a_tier_shows_what_was_charged_the_disagreements_and_the_lowest_upstream_figure`
- `core/admin/routes_stats.py` (9): :56 ×2 `test_admin_stats.py::test_the_cards_count_the_accounts_the_list_marks_in_use_and_asleep`; :203 `…::test_an_empty_pool_gives_zero_figures_for_every_pool_and_no_last_check`; :174, :175, :209, :214, :215, :220 `…::test_the_pool_panel_counts_points_unknowns_drift_proxy_failures_and_the_last_full_check`
- `core/alerts.py` (1): :388 `test_admin_alert_groups.py::test_acknowledge_stamps_only_the_open_rows_up_to_through_id_and_a_new_row_reopens_the_group`
- `core/api/doors.py` (5): :515 ×2, :516 ×2 `test_create_doors.py::test_status_shows_the_points_the_job_charged_the_key`; :517 `test_create_doors.py::test_the_status_query_reads_the_ledger_through_its_job_index`
- `core/billing.py` (1): :204 `test_billing.py::test_a_provider_added_after_the_key_is_closed_on_it_until_the_admin_opens_it`
- `core/db/migrations/0002_keys_billing.sql` (2): :46 `test_billing.py::test_eight_concurrent_refunders_give_one_refund_row_and_one_decrement`; :47 `test_billing.py::test_a_charge_made_again_after_a_refund_can_itself_be_refunded`
- `core/db/migrations/0006_grants.sql` (1): :23 `test_roles.py::test_the_api_is_refused_what_belongs_to_the_worker`
- `core/db/migrations/0012_billing_alerts_acknowledge.sql` (1): :5 `test_admin_alerts.py::test_the_api_may_update_the_acknowledge_columns_of_an_alert_and_nothing_else_the_worker_nothing`
- `core/db/migrations/0018_stats_indexes.sql` (1): :6 `test_final_read.py::test_the_stats_indexes_exist_on_the_columns_the_stats_read`
- `core/jobs/create.py` (1): :202 `test_create.py::test_a_failure_between_the_charge_and_the_job_row_leaves_no_charge`
- `core/jobs/ops.py` (1): :74 `test_ops.py::test_restore_forgets_counted_capacity_keeps_reported_and_makes_every_account_due`
- `core/jobs/reaper.py` (1): :158 `test_worker.py::test_the_running_worker_corrects_a_planted_pool_drift`
- `core/jobs/recheck.py` (1): :94 `test_recheck_recover.py::test_recheck_after_a_refund_charges_the_key_again_restarts_the_clock_and_a_new_failure_refunds_once`
- `core/pool.py` (8): :113 `test_pool.py::test_a_new_pool_day_resets_left_n_to_the_budget_whatever_it_was`; :156 `…::test_charging_the_same_job_twice_decrements_once`; :185 `…::test_a_refund_after_the_pool_day_rolled_credits_nothing`; :185 `…::test_eight_concurrent_refunds_of_one_charge_restore_it_once`; :221 `…::test_the_log_once_set_forgets_entries_older_than_a_pool_day`; :230 `…::test_drift_never_overwrites_a_reported_value`; :235 `…::test_drift_leaves_a_matching_row_alone`; :272 `…::test_a_reported_positive_balance_wakes_a_resting_pool_and_a_zero_one_does_not`
- `core/providers.py` (1): :323 `test_admin_summary.py::test_a_pool_counts_points_or_jobs_and_nothing_else`

Method: every marker from `scripts/mutate.py collect()` (3,292 in the tree at `432a018`) whose anchor lies in a line
this plan rewrites or deletes, or (migrations) in a table 0020 drops so the mutation can no longer reach the schema.
Anchors this plan deliberately keeps verbatim (the `_CHARGE` predicates, `ON CONFLICT (refund_of)`, `cap.left_n`,
`ensure_rows`, `lock_capacity`, `refund_job`, `jobs.price`/`account_cost`/`upstream_price` names, the `apply_fetched`
head) are not counted. The inventory's "about 230" is the number of markers living in the 15 money test files it
named (231 exactly); most of those anchor on code this plan keeps.

## Appendix B — markers to re-run (mutation of every marker naming a changed source file): 1,225 on 72 files

web/api/jobs-detail.ts 4; web/components/accounts/account-model.ts 23; web/components/alerts/alerts-model.ts 28;
web/components/jobs/JobDetailDrawer.vue 15; web/components/jobs/JobTechnical.vue 5; web/components/keys/KeyFormDialog.vue 6;
web/components/keys/WalletLines.vue 1; web/components/keys/key-model.ts 26; web/components/settings/PricesTable.vue 3;
web/components/settings/settings-cards.ts 4; web/components/shared/LedgerList.vue 2; web/components/shared/MoneyCell.vue 3;
web/components/test/test-models.ts 7; web/views/AccountsView.vue 28; web/views/KeysView.vue 21; web/views/SettingsView.vue 20;
apps/relay/src/relay_cli/main.py 38; dola catalog.py 8; dola classify.py 14; dola constants.py 6; dola engine/submit.py 105;
dola spec.py 20; flow engine/check.py 3; flow engine/submit.py 29; flow spec.py 15; gemini spec.py 5; meta constants.py 4;
meta spec.py 17; qwen catalog.py 1; qwen engine/check.py 1; qwen engine/submit.py 16; qwen spec.py 5; vibes catalog.py 1;
vibes engine/check.py 7; vibes spec.py 15; core admin/routes_accounts.py 9; admin/routes_alerts.py 3;
admin/routes_common.py 5; admin/routes_jobs.py 66; admin/routes_keys.py 8; admin/routes_prices.py 3;
admin/routes_settings.py 11; admin/routes_stats.py 72; alerts.py 46; api/doors.py 65; api/openapi_docs.py 10;
api/routes_catalog.py 10; api/schemas.py 8; billing.py 19; db/accounts.py 10; migrations 0002 2, 0006 2, 0012 1, 0018 3;
errors.py 11; jobs/apply.py 60; jobs/checks.py 37; jobs/claim.py 17; jobs/create.py 14; jobs/fail.py 7; jobs/ops.py 9;
jobs/reaper.py 43; jobs/recheck.py 11; jobs/runctx.py 11; jobs/sweeps.py 8; keys.py 7; limits.py 5; pool.py 26;
providers.py 22; settings.py 25; testing/checker.py 36; scripts/soak.py 17.
