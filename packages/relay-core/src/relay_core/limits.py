"""The fixed limits: numbers that shape behaviour and are code, not settings (the admin Fixed limits card).

Each entry reads the constant itself, so the card can never show a number the code does not run with. A provider adds
its own through `ProviderSpec.limits`, and every provider's `Caps` is turned into rows here, with the reason written
once. Only pure modules are imported (no provider engine, no browser package), because `relay serve` loads this.
`tests/test_fixed_limits.py` fails on a numeric constant that is neither listed here nor excused there.
"""

from collections.abc import Mapping
from types import ModuleType
from typing import Any

from relay_core import budget, frames, keys, leases, pool
from relay_core.admin import auth, router, routes_accounts, routes_jobs, routes_stats, users
from relay_core.api import doors, facade_common, progress, runners
from relay_core.jobs import (
    apply,
    checks,
    claim,
    commands,
    create,
    engine,
    fetcher,
    poller,
    reaper,
    sweeps,
    titles,
    webhooks,
    worker,
)
from relay_core.providers import FixedLimit, ProviderRegistry

MIB, KIB = 1024 * 1024, 1024


def _c(module: ModuleType, const: str, reason: str, unit: str = "") -> FixedLimit:
    """A limit read from `module.const`; its name is the module path under `relay_core` and the constant."""
    return FixedLimit("%s.%s" % (module.__name__.removeprefix("relay_core."), const), getattr(module, const), reason,
                      unit)


CORE: tuple[FixedLimit, ...] = (
    # ---- browser capacity -------------------------------------------------------------------------------------
    _c(budget, "RECONCILE_SHARE", "the most browser slots all reconciles may hold at once", "slots"),
    _c(budget, "OTHER_SHARE", "the most browser slots account checks and commands may hold together", "slots"),
    _c(budget, "RAMP_PERIOD_S", "after an abnormal restart the submit share starts at half and doubles each "
       "healthy period", "s"),
    _c(engine, "MAX_INFLIGHT", "submits one worker runs at once", "jobs"),
    _c(poller, "MAX_INFLIGHT", "polls one worker runs at once", "jobs"),
    _c(engine, "MAX_ERRORS", "unexpected errors in a row before a worker stops and exits", "errors"),
    _c(commands, "MAX_TASKS", "commands one worker runs at once", "commands"),
    _c(checks, "MAX_TASKS", "account checks one worker runs at once", "checks"),
    _c(users, "HASH_CONCURRENCY", "password hashes at once: argon2id holds 64 MiB per call", "hashes"),
    # ---- leases and heartbeats --------------------------------------------------------------------------------
    _c(leases, "HEARTBEAT_S", "a lease holder renews its lease this often", "s"),
    _c(leases, "LEASE_TTL_S", "a lease not renewed for this long is gone", "s"),
    _c(leases, "APPLY_MARGIN_S", "a job lease lasts the submit timeout plus this, so a late result can still be "
       "applied", "s"),
    _c(leases, "CHECK_HARD_S", "hard bound of one account check", "s"),
    _c(leases, "RECONCILE_HARD_S", "hard bound of one reconcile", "s"),
    _c(leases, "COMMAND_HARD_S", "hard bound per command kind that runs under an account lease", "s"),
    _c(engine, "BEAT_S", "a worker beats this often", "s"),
    _c(worker, "DEFAULT_GRACE_S", "a stopping worker gives its in-flight work this long; below the container's "
       "780 s stop grace in deploy/compose.yml", "s"),
    _c(reaper, "OWNER_GONE_S", "3 x the worker heartbeat: an owner silent this long is gone", "s"),
    _c(reaper, "INTERVAL_S", "the reaper runs this often", "s"),
    _c(reaper, "COMMAND_STALL_S", "bound of a command that takes no lease and has no hard bound of its own", "s"),
    _c(reaper, "COMMAND_STALL_MARGIN_S", "room for a command to record its own outcome before it counts as stalled",
       "s"),
    _c(routes_stats, "HEARTBEAT_STALE_S", "a worker silent for %g beats of %g s shows stale"
       % (routes_stats.HEARTBEAT_STALE_S / engine.BEAT_S, engine.BEAT_S), "s"),
    _c(pool, "REFILL_PAST_S", "an upstream refill this far in the past is still believed: the pool waits for the "
       "next check", "s"),
    _c(pool, "REFILL_AHEAD_S", "an upstream refill further ahead is not believed: the pool's own window decides", "s"),
    _c(pool, "REPEAT_QUIET_S", "a bot checkpoint this long after the account's last one rests the base again; "
       "sooner, the rest doubles up to the provider's max", "s"),
    _c(pool, "MAX_ROLLING_HOURS", "the longest rolling window a pool may set: as far ahead as a refill is believed",
       "h"),
    # ---- job timing -------------------------------------------------------------------------------------------
    _c(apply, "FIRST_POLL_S", "an accepted job is first polled this long after", "s"),
    _c(apply, "UNKNOWN_FIRST_S", "the first reconcile of an unknown job; it doubles up to the next limit", "s"),
    _c(apply, "UNKNOWN_MAX_S", "the longest wait between reconciles of an unknown job", "s"),
    _c(apply, "NOT_SENT_MARGIN_S", "the proving read starts this long after sent_at plus the submit timeout", "s"),
    _c(apply, "REQUEUE_PAUSE_S", "a requeued job is not claimed again for this long", "s"),
    _c(claim, "GAP_DEADLINE_SHARE", "a provider's send gap is cut to this share of the unsent deadline, so queued jobs "
       "do not expire waiting for it"),
    _c(apply, "RENDER_BACKSTOP_S", "a late job whose reads never answer still ends this long after its render "
       "deadline", "s"),
    _c(apply, "FETCH_WINDOW_S", "a video found at or after the deadline keeps its fetch retries this long", "s"),
    _c(apply, "FINAL_RECONCILE_RETRY_S", "a final reconcile that did not answer is tried again this soon", "s"),
    _c(apply, "RECONCILE_CLAIM_STOP_S", "no final reconcile is claimed in the last minute before the backstop", "s"),
    _c(apply, "STARTUP_GRACE_S", "after a worker starts, the backstop and the fetch window fail nothing", "s"),
    _c(apply, "DELTA_MAX_FACTOR", "a balance delta above this many times the expected cost is not recorded as the "
       "job's actual cost", "times"),
    _c(poller, "POLL_VISIBILITY_S", "a claimed poll is hidden from other workers this long", "s"),
    _c(poller, "POLL_BOUND_S", "hard bound of one poll", "s"),
    _c(poller, "ERROR_RETRY_S", "a poll that failed is tried again after this long", "s"),
    _c(fetcher, "FETCH_VISIBILITY_S", "a claimed fetch is hidden from other workers this long", "s"),
    _c(fetcher, "FETCH_BOUND_S", "hard bound of one fetch", "s"),
    _c(fetcher, "PROBE_BOUND_S", "hard bound of the read that probes a finished file", "s"),
    _c(checks, "RETRY_S", "next account check after an unreadable result, a failure or a bad proxy", "s"),
    _c(commands, "MAX_DEADLOCK_ATTEMPTS", "claims of a command in all when the database reports a deadlock", "claims"),
    _c(titles, "MIN_SENT_S", "a job is titled only this long after it was sent", "s"),
    _c(titles, "MAX_AGE_S", "a job older than this is no longer titled", "s"),
    _c(titles, "SWEEP_EVERY_S", "the title sweep runs this often", "s"),
    _c(create, "INPUT_LINK_TTL_S", "a link to an uploaded input image works this long", "s"),
    _c(sweeps, "ALERT_EVERY_H", "the same billing drift raises a new alert at most this often", "h"),
    # ---- webhooks and api loops -------------------------------------------------------------------------------
    _c(webhooks, "MAX_ATTEMPTS", "deliveries of one webhook before it is final", "attempts"),
    _c(webhooks, "RETRY_AFTER_S", "wait after the 1st and the 2nd failed delivery", "s"),
    _c(webhooks, "SEND_TIMEOUT_S", "one delivery gives up after this long", "s"),
    _c(runners, "WEBHOOK_EVERY_S", "the api looks for due webhooks this often", "s"),
    _c(runners, "HOUSEKEEPING_EVERY_S", "the api sweeps old rows and orphan files this often", "s"),
    _c(runners, "LEDGER_EVERY_S", "the api checks the billing ledger this often", "s"),
    # ---- requests ---------------------------------------------------------------------------------------------
    _c(keys, "TOUCH_EVERY_S", "a key's last call and address are written at most this often (Keys, Last used)", "s"),
    _c(doors, "MAX_IMAGES", "input images in one request", "images"),
    _c(doors, "MAX_IMAGE_BYTES", "one input image", "bytes"),
    _c(doors, "MAX_TITLE", "characters of a job title", "chars"),
    _c(doors, "MAX_IDEM_KEY", "characters of an Idempotency-Key", "chars"),
    _c(doors, "MAX_OPTIONS_BYTES", "the client's options as JSON, kept for good with the job", "bytes"),
    _c(doors, "RETRY_WINDOW_S", "a retry with no Idempotency-Key finds its job this far back", "s"),
    _c(doors, "MAX_BODY_BYTES", "a request body: ten images of 12 MiB as base64, plus the text", "bytes"),
    _c(doors, "NET_OPTIONS", "fetching an image the client gave as a URL: its size and its time"),
    _c(facade_common, "HOLD_LIMIT_S", "a stream waits this long at most", "s"),
    _c(facade_common, "NON_STREAM_HOLD_S", "a non-stream request answers within this, below the OpenAI SDK's 600 s "
       "timeout", "s"),
    _c(frames, "FRAME_MAX", "characters of a stored prompt frame", "chars"),
    _c(progress, "WINDOW", "the newest finished jobs of a shape the progress median is taken over", "jobs"),
    _c(progress, "MIN_SAMPLES", "below this the median is not trusted and a fixed fallback is used", "jobs"),
    _c(progress, "AGE_DAYS", "a finished job older than this is not a progress sample", "days"),
    _c(progress, "SAMPLE_MAX_S", "a longer finished job is no progress sample; it copies the default render deadline, "
       "not the live setting", "s"),
    _c(progress, "TTL_S", "an estimate is kept this long before it is read again", "s"),
    # ---- admin ------------------------------------------------------------------------------------------------
    _c(auth, "LOGIN_MAX_FAILS", "failed logins one IP may make in the window", "logins"),
    _c(auth, "LOGIN_WINDOW_S", "the window of failed logins; the next attempt is answered 429", "s"),
    _c(router, "MAX_BODY", "every admin body but the account import", "bytes"),
    _c(router, "MAX_IMPORT_BODY", "the account import body: every item at its field limit plus slack", "bytes"),
    _c(routes_accounts, "MAX_IMPORT", "accounts in one import", "accounts"),
    _c(routes_accounts, "MAX_CREDENTIAL_CHARS", "characters of one credential", "chars"),
    _c(routes_accounts, "LABEL_WAIT_S", "a label edit waits this long for a worker that holds the account", "s"),
    _c(users, "MIN_PASSWORD_LEN", "shortest admin password", "chars"),
    _c(users, "MAX_PASSWORD_LEN", "longest admin password: it bounds the work a request can ask of the hasher",
       "chars"),
    _c(users, "MAX_USERNAME_LEN", "longest admin username", "chars"),
    _c(routes_jobs, "MAX_BULK", "jobs one bulk action may touch", "jobs"),
    _c(routes_stats, "MIN_SAMPLE", "a rate over fewer jobs than this says nothing", "jobs"),
    _c(routes_stats, "ERROR_RATE", "share of failed jobs in 24 h that raises the error-rate alert"),
    _c(routes_stats, "CONSECUTIVE_ERRORS", "errors in a row that raise a worker alert", "errors"),
)


def _number(v: Any) -> str:
    return "%g" % v if isinstance(v, float) else str(v)


def _bytes(n: int) -> str:
    if n == 1:
        return "1 byte"
    if n >= MIB:
        return ("%g MiB" if n % MIB == 0 else "\u2248 %g MiB") % round(n / MIB, 2)
    if n % KIB == 0:
        return "%d KiB" % (n // KIB)
    return "%d bytes" % n


def _entry(key: str, value: Any, unit: str) -> str:
    """One value of a table: with no unit of its own it takes one from its key (`..._bytes`, `..._s`)."""
    if not unit and key.endswith("_bytes"):
        return _bytes(value)
    return value_text(value, unit or ("s" if key.endswith("_s") else ""))


def value_text(value: Any, unit: str = "") -> str:
    """The value as the admin reads it: seconds as `720 s`, bytes as MiB or KiB, a count with its unit (`1 point`,
    `2 points`), a tuple as `30, 300 s`, a table as `recover 600 s, ...`. A string is shown as it is (`no limit`,
    `never`)."""
    if isinstance(value, str):
        return value
    if unit == "bytes":
        return _bytes(value)
    if value == 1 and not isinstance(value, tuple | Mapping) and len(unit) > 2 and unit.endswith("s"):
        # one point, not "1 points"; one hash, one retry (`s` and `h` are symbols, not plurals)
        if unit.endswith("ies"):
            unit = unit[:-3] + "y"
        else:
            unit = unit[:-2] if unit.endswith(("ches", "shes", "sses", "xes")) else unit[:-1]
    tail = " " + unit if unit else ""
    if isinstance(value, Mapping):
        return ", ".join("%s %s" % (k, _entry(k, v, unit)) for k, v in value.items())
    if isinstance(value, tuple):
        return ", ".join(_number(v) for v in value) + tail
    return _number(value) + tail


def caps_limits(spec: Any) -> tuple[FixedLimit, ...]:
    """The limits every provider declares through `Caps` and its spec, with one reason each."""
    caps = spec.caps
    rows = [
        FixedLimit("caps.account_slots", "no limit" if caps.account_slots is None else caps.account_slots,
                   "jobs one account may run at once", "" if caps.account_slots is None else "jobs"),
        FixedLimit("caps.max_attempts", caps.max_attempts, "counted attempts before a job fails", "attempts"),
        FixedLimit("caps.suspect_threshold", caps.suspect_threshold, "strikes before the strike effect applies",
                   "strikes"),
        FixedLimit("caps.proxy_rest_s", caps.proxy_rest_s, "a failed proxy probe rests the account this long", "s"),
        FixedLimit("caps.check_interval_s", caps.check_interval_s, "an account is checked this often", "s"),
        FixedLimit("caps.catalog_refresh_s", "never" if caps.catalog_refresh_s is None else caps.catalog_refresh_s,
                   "the worker refreshes its catalog snapshots this often",
                   "" if caps.catalog_refresh_s is None else "s"),
        FixedLimit("max_prompt_chars", spec.max_prompt_chars, "longest prompt a customer may send", "chars"),
    ]
    rules = getattr(spec, "frames", None)
    if rules is not None:
        rows.append(FixedLimit("frames.max_sent_chars", rules.max_sent_chars, "longest rendered text it is sent",
                               "chars"))
    return tuple(rows)


def fixed_limits(registry: ProviderRegistry) -> list[dict]:
    """Every fixed limit as `{group, name, value, reason}`: the core first, then each provider in name order."""
    out = [{"group": "core", "name": f.name, "value": value_text(f.value, f.unit), "reason": f.reason} for f in CORE]
    for name in registry.names():
        spec = registry.spec(name)
        rows = caps_limits(spec) + tuple(getattr(spec, "limits", ()))
        out += [{"group": name, "name": f.name, "value": value_text(f.value, f.unit), "reason": f.reason}
                for f in rows]
    return out
