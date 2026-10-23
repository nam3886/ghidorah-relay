"""The provider protocol, trimmed to what the first provider needs.

Pure types: no I/O, no database, no browser. The API imports a provider's spec; only a worker imports its engine.
Members marked as later additions (pinned jobs, poll-held leases, per-pool slots, bytes outputs, fail-and-refund,
maintain, the cooldown strike effect) are deliberately absent. Credential push exists in one narrow form:
`RunCtx.renew_credential`, a renewed copy of the same session (a cookie rotation), never a new identity.

This module is also the one core module that may name providers (the registry); it names none itself.
"""

import importlib
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from relay_core import frames


class Completion(StrEnum):
    HOLD = "hold"        # the result is awaited inside the session
    DETACH = "detach"    # the result is polled after the session


class AfterSent(StrEnum):
    RECONCILE = "reconcile"   # an ambiguous send is reconciled, never re-sent


# ---- account effects ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Rest:
    """One pool of one account rests until `until`. Never shortens a longer rest."""
    pool: str
    until: datetime
    reason: str

    def __post_init__(self):
        _aware(self.until)


@dataclass(frozen=True)
class Cooldown:
    """The whole account cools down until `until`. Never shortens a longer cooldown (GREATEST, no rank).
    `recheck` also makes the next account check due at `until`, instead of at the normal interval."""
    reason: str
    until: datetime
    recheck: bool = False

    def __post_init__(self):
        _aware(self.until)


@dataclass(frozen=True)
class Suspect:
    """A doubt about the account: a strike, and the strike threshold ends in the provider's `strike_effect`.
    `min_gap_s` spaces strikes: a doubt that comes less than that many seconds after the last counted strike counts
    once (it adds no strike). 0 counts every one."""
    reason: str
    min_gap_s: int = 0

    def __post_init__(self):
        if self.min_gap_s < 0:
            raise ValueError("Suspect.min_gap_s must be >= 0")


@dataclass(frozen=True)
class Dead:
    reason: str


@dataclass(frozen=True)
class Revive:
    reason: str


BALANCE_SOURCES = ("reported", "counted", "reset")


@dataclass(frozen=True)
class Balance:
    """The account's remaining capacity in a pool. A `reported` value always overrides a counted one."""
    pool: str
    left_n: int | None
    source: str = "reported"

    def __post_init__(self):
        if self.source not in BALANCE_SOURCES:
            raise ValueError("Balance.source must be one of %s" % (BALANCE_SOURCES,))


@dataclass(frozen=True)
class Success:
    """An accepted submit, a good poll or an alive check: clears the strike counter."""


@dataclass(frozen=True)
class ProxyBad:
    reason: str


AccountEffect = Rest | Cooldown | Suspect | Dead | Revive | Balance | Success | ProxyBad
StrikeEffect = Dead   # the strike threshold's terminal effect; the cooldown form arrives with the later providers


def _aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("a time must carry a timezone")


# ---- result types ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Warning:
    code: str
    text: str


@dataclass(frozen=True)
class Facts:
    upstream_price: int | None = None
    upstream_model: str | None = None


@dataclass(frozen=True)
class Output:
    ref: str
    mime: str
    idx: int
    upstream_id: str | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)   # never holds a secret


def _code(code: str) -> None:
    if not code:
        raise ValueError("a result needs a code")


@dataclass(frozen=True)
class Accepted:
    """Sent and accepted. `taken=False`: sent, but the order is not yet proven."""
    ref: str
    provider_state: Mapping[str, Any] = field(default_factory=dict)
    facts: Facts = Facts()
    taken: bool = True
    unclear: str | None = field(default=None, repr=False)
    """The upstream's own reply the provider could not classify (admin alerts): never page text, `""`
    when no reply text could be read, None when there is nothing to report. Only for an order not yet proven taken."""

    def __post_init__(self):
        if self.unclear is not None and (not isinstance(self.unclear, str) or self.taken):
            raise ValueError("Accepted.unclear is a str, and only on an order that is not proven taken")


@dataclass(frozen=True)
class Completed:
    """Done inside the session; implies taken."""
    outputs: tuple[Output, ...]
    facts: Facts = Facts()

    def __post_init__(self):
        if not self.outputs:
            raise ValueError("Completed needs at least one output")


@dataclass(frozen=True)
class NotTaken:
    """Sent, and upstream certainly did not take it. A re-send is allowed (the job goes back to queued)."""
    code: str
    state_patch: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _code(self.code)


@dataclass(frozen=True)
class Retry:
    """Only before `mark_sent`; after it the core treats it as Ambiguous."""
    code: str

    def __post_init__(self):
        _code(self.code)


@dataclass(frozen=True)
class Fail:
    """A certain rejection: terminal."""
    code: str
    detail: str = ""
    state_patch: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _code(self.code)


@dataclass(frozen=True)
class Ambiguous:
    """Sent, outcome unknown: the job becomes `unknown` and is reconciled, never re-sent."""
    note: str = ""


SubmitOutcome = Accepted | Completed | NotTaken | Retry | Fail | Ambiguous


@dataclass(frozen=True)
class SubmitResult:
    job: SubmitOutcome
    account: tuple[AccountEffect, ...] = ()
    warnings: tuple[Warning, ...] = ()
    title: str | None = None


@dataclass(frozen=True)
class Pending:
    """Not ready yet. `answered` False: the read itself failed (no usable answer), so the job is polled again but the
    render deadline may not treat this as the read that says "not ready"."""
    note: str = ""
    next_in_s: int = 30
    state_patch: Mapping[str, Any] = field(default_factory=dict)
    answered: bool = True


@dataclass(frozen=True)
class Done:
    outputs: tuple[Output, ...]
    facts: Facts = Facts()


@dataclass(frozen=True)
class Failed:
    code: str
    detail: str = ""
    taken: bool = True
    state_patch: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        _code(self.code)


PollOutcome = Pending | Done | Failed


class OutputGone(Exception):
    """Raised by `engine.fetch` when upstream answers that the output does not exist, so downloading again cannot
    help: the fetch fails with `code` instead of the retried `render.download_failed`. A terminal `code` fails the job
    at once with its refund; any other code is retried like every failed download."""

    def __init__(self, code: str, detail: str = ""):
        _code(code)
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class PollResult:
    outcome: PollOutcome
    effects: tuple[AccountEffect, ...] = ()
    warnings: tuple[Warning, ...] = ()
    title: str | None = None


@dataclass(frozen=True)
class Found:
    ref: str
    provider_state: Mapping[str, Any] = field(default_factory=dict)
    taken: bool = True


@dataclass(frozen=True)
class NotSent:
    """Only with positive proof. `covers_send_window` is the provider's assertion that the listing it read covers
    the whole send window (a capped or truncated listing never does); the core also applies the timing rule (I27)."""
    covers_send_window: bool


@dataclass(frozen=True)
class StillUnknown:
    """The read did not settle it. `answered` True: the read worked and found nothing (or nothing provable); False (the
    default): it failed or gave no proof, so the render deadline may not treat it as the final read."""
    answered: bool = False


Reconciled = Found | NotSent | StillUnknown

HEALTH_STATES = ("alive", "signed_out", "unknown")


@dataclass(frozen=True)
class Health:
    state: Literal["alive", "signed_out", "unknown"]
    identity: str | None = None
    display_name: str | None = None
    screenshot: bytes | None = None
    state_patch: Mapping[str, Any] = field(default_factory=dict)
    effects: tuple[AccountEffect, ...] = ()

    def __post_init__(self):
        if self.state not in HEALTH_STATES:
            raise ValueError("Health.state must be one of %s" % (HEALTH_STATES,))


@dataclass(frozen=True)
class ProxyVerdict:
    ok: bool
    reason: str = ""


# ---- capabilities, catalog, plan ------------------------------------------------------------------------------
@dataclass(frozen=True)
class PoolSpec:
    """One account pool: its day rule is a constant of the spec, its daily budget a live setting (or None). `unit` is
    what the budget counts, for the admin: points (an account cost that varies per job) or jobs."""
    name: str
    tz: str
    budget_setting: str | None = None
    unit: Literal["points", "jobs"] = "jobs"

    def __post_init__(self):
        if not self.name:
            raise ValueError("a pool needs a name")
        if self.unit not in ("points", "jobs"):
            raise ValueError("a pool counts points or jobs")
        ZoneInfo(self.tz)   # an unknown timezone refuses here, not at the first rollover


@dataclass(frozen=True)
class Caps:
    execution: Literal["http", "browser"]
    wallet_kinds: tuple[str, ...]
    pools: tuple[PoolSpec, ...]
    account_slots: int | None          # max simultaneous leases per account; None = unlimited
    max_attempts: int
    suspect_threshold: int             # strikes that trigger `strike_effect`
    strike_effect: StrikeEffect
    has_poll: bool = False
    has_reconcile: bool = False
    has_probe_proxy: bool = False
    titles: bool = False
    catalog_refresh_s: int | None = None
    proxy_rest_s: int = 1800           # a failed proxy probe rests the account on every pool this long
    check_interval_s: int = 21600      # the account check cadence; the retry stays an env value

    def __post_init__(self):
        if self.execution not in ("http", "browser"):
            raise ValueError("execution must be http or browser")
        if not self.wallet_kinds:
            raise ValueError("a provider declares at least one wallet kind")
        names = [p.name for p in self.pools]
        if len(set(names)) != len(names):
            raise ValueError("pool names must be unique")
        if self.account_slots is not None and self.account_slots < 1:
            raise ValueError("account_slots must be >= 1 or None")
        if self.max_attempts < 1 or self.suspect_threshold < 1:
            raise ValueError("max_attempts and suspect_threshold must be >= 1")
        if self.proxy_rest_s < 1 or self.check_interval_s < 1:
            raise ValueError("proxy_rest_s and check_interval_s must be >= 1")
        if not isinstance(self.strike_effect, Dead):
            raise ValueError("strike_effect must be Dead(reason)")

    def pool(self, name: str) -> PoolSpec:
        for p in self.pools:
            if p.name == name:
                return p
        raise KeyError("no pool %r" % name)


@dataclass(frozen=True)
class ModelSpec:
    id: str
    media_type: str
    operations: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    limits: Mapping[str, Any] = field(default_factory=dict)
    prices: Mapping[str, int] = field(default_factory=dict)
    display_name: str = ""             # the product name shown to people; empty: the catalog shows the id

    def names(self) -> tuple[str, ...]:
        return (self.id, *self.aliases)


@dataclass(frozen=True)
class MediaIn:
    sha256: str
    mime: str
    size: int


class InputError(ValueError):
    """What `ProviderSpec.plan()` raises to refuse a request: a registered platform `code` and, optionally, the
    request field (`param`) it is about. The API answers with that code; any other exception is a crash."""

    def __init__(self, code: str, param: str | None = None):
        super().__init__(code)
        self.code, self.param = code, param


@dataclass(frozen=True)
class CreateRequest:
    model: str
    prompt: str
    duration: int | None = None
    ratio: str | None = None
    n: int | None = None
    images: tuple[MediaIn, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    title: str | None = None
    callback_url: str | None = None
    door: Literal["native", "facade"] = "native"


@dataclass(frozen=True)
class FrameUse:
    """The frame a job is sent with: the stored version's id (None for the core frame, which has no
    row), its text and its provider's `FrameRules.verbatim`."""
    id: int | None
    text: str
    verbatim: bool = False


@dataclass(frozen=True)
class FrameRules:
    """What a provider declares about its prompt frames. `defaults` is the shipped default text per media kind and must
    cover every kind of the catalog and pass the grammar; `warnings(kind, text)` is pure and returns the texts an
    admin save shows (never a refusal); `max_sent_chars` is the longest rendered text the provider accepts.
    `verbatim`: the provider sends the text in a request field, not typed into a chat, and finds its job by its own
    id: `{{tracking_code}}` renders as nothing and the customer's line breaks are kept (`frames.render`)."""
    defaults: Mapping[str, str]
    warnings: Callable[[str, str], tuple[str, ...]]
    max_sent_chars: int
    verbatim: bool = False


@dataclass(frozen=True)
class Plan:
    operation: str
    media_type: str
    model: str
    params: Mapping[str, Any]
    completion: Completion
    after_sent: AfterSent
    wallet_kind: str
    price: int
    pool: str | None
    account_cost: int
    submit_timeout_s: int
    warnings: tuple[Warning, ...] = ()
    frame: FrameUse | None = None     # set by the core in the plan step (api.doors); a provider's value is replaced

    def __post_init__(self):
        if self.price < 0 or self.account_cost < 0 or self.submit_timeout_s < 1:
            raise ValueError("price and account_cost must be >= 0 and submit_timeout_s >= 1")


# ---- what the engine sees -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MediaRef:
    idx: int
    object_key: str
    mime: str
    size: int


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    auth: str | None = None   # a secret: never logged


@dataclass(frozen=True)
class ParsedCredential:
    blob: bytes               # what gets sealed; the spec has already validated it
    hint: str = ""            # a non-secret label suggestion


@dataclass(frozen=True)
class AccountView:
    """Read-only: the decrypted credential and proxy of the account a run is using."""
    id: str
    label: str
    credential: bytes
    credential_gen: int
    proxy: Proxy | None
    provider_state: Mapping[str, Any]
    capacity: Mapping[str, int | None]   # pool -> left_n (None = unknown)
    seal_key: str | None = None          # the public key that sealed the credential (not a secret); None: no renewal
    sealed: bytes | None = field(default=None, repr=False)        # the ciphertext read: a renewal swaps exactly it
    reload: "Callable[[], Awaitable[AccountView]] | None" = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class JobView:
    id: str
    provider: str
    operation: str
    model: str
    params: Mapping[str, Any]
    prompt: str
    marker: str
    sent_text: str | None
    render_since: datetime | None
    sent_at: datetime | None
    external_ref: str | None
    provider_state: Mapping[str, Any]
    inputs: Callable[[], Awaitable[list[MediaRef]]]   # loaded lazily
    frame_id: int | None = None       # the frame version `sent_text` was rendered with; None = the core frame


class RunCtx(Protocol):
    """The only way an engine touches the platform. Engines never write the database."""

    account: AccountView
    browser_token: object | None      # the budget token the core took for this purpose (browser providers)
    settings: Any                     # read-only settings view
    log: Any                          # structured log, redacted

    async def fence(self) -> None:
        """Raises LeaseLost unless the lease is live now. Call before each irreversible action after the first."""

    async def mark_sent(self) -> None:
        """fence() plus `sent_at = now`, in one transaction, immediately before the first irreversible action."""

    async def report_ref(self, ref: str, state: Mapping[str, Any]) -> None:
        """Fenced: persist `external_ref` and `provider_state` as soon as they are known. No secrets."""

    async def event(self, text: str, stage: str, source: str) -> None: ...

    async def evidence(self, name: str, data: bytes, mime: str) -> None: ...

    async def renew_credential(self, blob: bytes,
                               merge: Callable[[bytes, bytes], bytes] | None = None) -> bytes | None:
        """Replace the account's sealed credential with a renewed copy of the SAME session (an upstream that rotates
        its cookies), sealed with the key that sealed it, by compare-and-swap on the exact sealed value this run last
        read or wrote, at the `credential_gen` the run read (never bumped: a credential imported meanwhile is never
        overwritten). With `merge(stored, mine)` (the provider's) every write is the merge of the stored session with
        `blob`, and a swap lost to another run is merged with the new stored value and tried once more. Returns the
        credential now stored (the provider keeps exactly that), None when superseded. The blob is never logged."""


@dataclass(frozen=True)
class FixedLimit:
    """A limit that is code, not a setting: `value` is the constant itself, `reason` says in one line why it is that
    number. The admin Fixed limits card lists them; `unit` is shown after a number."""
    name: str
    value: Any
    reason: str
    unit: str = ""


@dataclass(frozen=True)
class RateAlert:
    """A drift alert a provider declares: `setting` (one of its own, a rate from 0 to 1) is the share of the last 24 h
    jobs, at least five of them, that ended with the error code `code` (`source` "error_code") or carry the warning
    code `code` (`source` "warning"); a tuple counts a job that matches any of its codes. The admin stats compute it
    from columns; the core guesses nothing."""
    setting: str
    code: str | tuple[str, ...]
    source: Literal["error_code", "warning"]


class ProviderSpec(Protocol):
    """Pure; the API imports it."""

    name: str
    caps: Caps
    max_prompt_chars: int                        # the longest prompt a customer may send, in characters
    rate_alerts: tuple[RateAlert, ...] = ()      # a spec without the attribute declares none (read with getattr)
    limits: tuple[FixedLimit, ...] = ()          # fixed limits it adds to the Fixed limits card (read with getattr)
    # a live setting (one of its own) whose value, http or browser, is how it sends now instead of caps.execution: the
    # admin shows that one (read with getattr; None = caps.execution)
    execution_setting: str | None = None

    def catalog(self) -> list[ModelSpec]: ...
    def plan(self, req: CreateRequest, settings: Any, snapshots: Mapping[str, Mapping]) -> Plan: ...
    def codes(self) -> dict[str, Any]: ...
    def settings(self) -> dict[str, Any]: ...
    def parse_credential(self, raw: bytes) -> ParsedCredential: ...
    def deadline_cause(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str: ...
    def unclear_at_deadline(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str | None: ...
    def extras(self, snapshots: Mapping[str, Mapping]) -> dict: ...

    frames: FrameRules | None = None      # None = no frames: the core frame applies


class ProviderEngine(Protocol):
    """Worker only. `poll`, `reconcile` and `probe_proxy` exist exactly when `Caps` says so."""

    async def submit(self, ctx: RunCtx, job: JobView) -> SubmitResult: ...
    def fetch(self, ctx: RunCtx, out: Output) -> AsyncIterator[bytes]: ...   # an async generator; may raise OutputGone
    async def check_account(self, ctx: RunCtx) -> Health: ...
    async def run_command(self, ctx: RunCtx, kind: str, args: dict) -> dict: ...
    async def poll(self, ctx: RunCtx, job: JobView) -> PollResult: ...
    async def reconcile(self, ctx: RunCtx, job: JobView) -> Reconciled: ...
    async def probe_proxy(self, proxy: Proxy) -> ProxyVerdict: ...


# ---- the explicit registry ------------------------------------------------------------------------------------
_NAME = re.compile(r"[a-z][a-z0-9_]*")


class RegistryError(ValueError):
    """A spec that cannot be registered: a duplicate name, a duplicate model id or alias, a malformed spec."""


def _whole(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and v == int(v)


def _bounded_slots(slots: Any) -> bool:
    """An `int` setting with a whole `min >= 1` and a finite whole `max`. Read with getattr: a test stub holds plain
    objects."""
    low: Any = getattr(slots, "min", None)
    high: Any = getattr(slots, "max", None)
    return getattr(slots, "type", None) is int and _whole(low) and low >= 1 and _whole(high)


def _execution_values(setting: Any) -> bool:
    """A setting that may stand for `caps.execution`: a `str` setting whose rule lists every value it allows
    (`Rule.choices`), each one http or browser, so the admin can never show it as anything else. Read with getattr: a
    test stub holds plain objects."""
    choices = getattr(getattr(setting, "rule", None), "choices", None) or ()
    return getattr(setting, "type", None) is str and bool(choices) and set(choices) <= {"http", "browser"}


def _problems(spec: ProviderSpec) -> list[str]:
    bad = []
    if not _NAME.fullmatch(spec.name):
        bad.append("provider name %r must be lower case letters, digits and _" % spec.name)
    cap = getattr(spec, "max_prompt_chars", None)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        bad.append("%s: max_prompt_chars must be a positive whole number" % spec.name)
    keys = set(spec.settings())
    stray = sorted(k for k in keys if not k.startswith(spec.name + "."))
    if stray:
        bad.append("%s: settings outside its namespace: %s" % (spec.name, ", ".join(stray)))
    needed = ["claims_enabled", "render_deadline_s", "disabled_models"] + (
        ["browser_slots"] if spec.caps.execution == "browser" else [])
    missing = [spec.name + "." + n for n in needed if spec.name + "." + n not in keys]
    if missing:                      # the core reads these (the door reads disabled_models); a missing one breaks it
        bad.append("%s: settings the core reads are not declared: %s" % (spec.name, ", ".join(missing)))
    if spec.caps.execution == "browser" and spec.name + ".browser_slots" in keys:
        slots = spec.settings()[spec.name + ".browser_slots"]     # its bound is what keeps a browser worker in memory
        if not _bounded_slots(slots):
            bad.append("%s.browser_slots must be an int setting that declares a whole min >= 1 and a finite whole max"
                       % spec.name)
        else:
            try:
                slots.validate(slots.default)          # a bad default is a refusal here, not a traceback at start
            except ValueError as e:
                bad.append("%s.browser_slots default: %s" % (spec.name, e))
    for pool in spec.caps.pools:
        if pool.budget_setting is not None and pool.budget_setting not in keys:
            bad.append("%s: pool %s names the budget setting %s, which the spec does not declare"
                       % (spec.name, pool.name, pool.budget_setting))
    execution_setting = getattr(spec, "execution_setting", None)
    if execution_setting is not None and execution_setting not in keys:
        bad.append("%s: execution_setting names %s, which the spec does not declare" % (spec.name, execution_setting))
    elif execution_setting is not None and not _execution_values(spec.settings()[execution_setting]):
        bad.append("%s: execution_setting %s must be a str setting whose rule lists its choices, all of http and "
                   "browser" % (spec.name, execution_setting))
    for m in spec.catalog():
        if not m.operations:
            bad.append("%s: model %s has no operations" % (spec.name, m.id))
    for alert in getattr(spec, "rate_alerts", ()):
        # imported by name: the mypy gate follows imports, and the registry check needs only the code table
        table = importlib.import_module("relay_core.errors").RULES
        codes = (alert.code,) if isinstance(alert.code, str) else alert.code
        if alert.setting not in keys:
            bad.append("%s: rate alert names the setting %s, which the spec does not declare"
                       % (spec.name, alert.setting))
        for code in codes:
            if alert.source == "error_code" and code not in table and code not in spec.codes():
                bad.append("%s: rate alert %s watches the error code %s, which is not registered"
                           % (spec.name, alert.setting, code))
            if alert.source == "warning" and not code.startswith(spec.name + "."):
                bad.append("%s: rate alert %s watches the warning %s, outside the provider's namespace"
                           % (spec.name, alert.setting, code))
        if alert.source not in ("error_code", "warning"):
            bad.append("%s: rate alert %s has the unknown source %r" % (spec.name, alert.setting, alert.source))
    bad += _frame_problems(spec)
    return bad


def _frame_problems(spec: ProviderSpec) -> list[str]:
    """A provider with frames must ship a default that passes the grammar for every kind of its catalog, or neither the
    api nor the worker starts."""
    rules = getattr(spec, "frames", None)
    if rules is None:
        return []
    bad = []
    kinds = {m.media_type for m in spec.catalog()}
    for kind in sorted(kinds - set(rules.defaults)):
        bad.append("%s: no default frame for the %s models" % (spec.name, kind))
    for kind, text in rules.defaults.items():
        if kind not in frames.KINDS:
            bad.append("%s: a default frame for the unknown kind %r" % (spec.name, kind))
            continue
        try:
            frames.check_frame(text)
        except frames.FrameError as e:
            bad.append("%s: the shipped %s frame fails the grammar: %s" % (spec.name, kind, e))
    if not isinstance(rules.max_sent_chars, int) or rules.max_sent_chars < 1:
        bad.append("%s: max_sent_chars must be a positive whole number" % spec.name)
    return bad


def check_catalog(specs: Iterable[ProviderSpec]) -> dict[str, tuple[str, ModelSpec]]:
    """Model ids and aliases must be unique across every provider (and within one). Returns name -> (provider,
    model); a duplicate raises, which is how a worker or the API refuses to start."""
    seen: dict[str, tuple[str, ModelSpec]] = {}
    clashes = []
    for spec in specs:
        for model in spec.catalog():
            for name in model.names():
                if name in seen:
                    clashes.append("%r is used by %s and by %s" % (name, seen[name][0], spec.name))
                else:
                    seen[name] = (spec.name, model)
    if clashes:
        raise RegistryError("catalog names are not unique: " + "; ".join(clashes))
    return seen


class ProviderRegistry:
    """The specs a process knows. Registration is explicit (the app lists its providers); nothing is discovered."""

    def __init__(self) -> None:
        self._specs: dict[str, ProviderSpec] = {}
        self._models: dict[str, tuple[str, ModelSpec]] = {}

    def register(self, spec: ProviderSpec) -> None:
        if spec.name in self._specs:
            raise RegistryError("provider %s is already registered" % spec.name)
        bad = _problems(spec)
        if bad:
            raise RegistryError("; ".join(bad))
        models = check_catalog([*self._specs.values(), spec])   # raises before anything is stored
        self._specs[spec.name] = spec
        self._models = models

    def spec(self, name: str) -> ProviderSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise RegistryError("no provider %s" % name) from None

    def names(self) -> list[str]:
        return sorted(self._specs)

    def resolve(self, model: str) -> tuple[ProviderSpec, ModelSpec] | None:
        """The provider and catalog entry that own a public model id or alias; None when nobody does."""
        hit = self._models.get(model)
        return None if hit is None else (self._specs[hit[0]], hit[1])
