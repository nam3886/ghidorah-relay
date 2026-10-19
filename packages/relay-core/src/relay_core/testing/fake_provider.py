"""A provider that does no I/O, for proving the core against the protocol (contracts 5, 14.3).

`FakeSpec` is the pure half (catalog, plan, caps, settings) and `FakeEngine` the worker half. The engine plays a
script per job: a result, an exception, or an async callable, with or without `mark_sent` first; with no script it
completes in the session (`completion=hold`) or accepts and is polled (`detach`). Failpoints
(`fake.submit.start`, `fake.submit.sent`, `fake.poll`, `fake.reconcile`) let a test stop it at a named point.
"""

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from relay_core import failpoints
from relay_core.providers import (
    Accepted,
    AfterSent,
    Caps,
    Completed,
    Completion,
    CreateRequest,
    Dead,
    Done,
    FixedLimit,
    FrameRules,
    Health,
    InputError,
    JobView,
    ModelSpec,
    Output,
    ParsedCredential,
    Plan,
    PollResult,
    PoolSpec,
    Proxy,
    ProxyVerdict,
    RateAlert,
    Reconciled,
    StillUnknown,
    SubmitResult,
)
from relay_core.settings import REGISTRY, Spec

# Opt-in frames for a FakeSpec: the marker first, behind a fixed preamble, the way a real admin frame can be written.
FAKE_FRAMES = FrameRules(
    defaults={"video": "Fixed preamble for videos. {{tracking_code}} {{client_prompt}}",
              "image": "Fixed preamble for images. {{tracking_code}} {{client_prompt}}"},
    warnings=lambda kind, text: () if "single" in text.lower() else ("asks for no single result",),
    max_sent_chars=2000)

FAKE_MAX_PROMPT_CHARS = 1000      # small, so a test can reach it; below FAKE_FRAMES.max_sent_chars


class FakeSpec:
    """The fake provider's spec. `name` is a parameter so a test can run two of them. `frames` is None (the core frame
    applies) unless a test passes rules, for instance `FAKE_FRAMES`. `unclear` is the text `unclear_at_deadline` reports
    (None: the fake explains nothing)."""

    def __init__(self, name: str = "fake", *, execution: str = "http", account_slots: int | None = 1,
                 max_attempts: int = 3, has_reconcile: bool = True, frames: FrameRules | None = None,
                 unclear: str | None = None,
                 max_prompt_chars: int = FAKE_MAX_PROMPT_CHARS):
        self.name = name
        self.frames = frames
        self.max_prompt_chars = max_prompt_chars
        self.unclear = unclear
        self.caps = Caps(
            execution=execution, wallet_kinds=("video", "image"),
            pools=(PoolSpec("video", "UTC", name + ".video_budget"), PoolSpec("image", "UTC")),
            account_slots=account_slots, max_attempts=max_attempts, suspect_threshold=3,
            strike_effect=Dead("error_threshold"), has_poll=True, has_reconcile=has_reconcile)
        self.limits = (FixedLimit(name + ".demo_bound", 7, "A bound of the fake provider, for tests.", "items"),)
        self.rate_alerts = (RateAlert(name + ".alert_drift_rate", name + ".drift_demo", "warning"),
                            RateAlert(name + ".alert_deadline_rate", "render.deadline", "error_code"))

    def catalog(self) -> list[ModelSpec]:
        return [ModelSpec(self.name + "-video", "video", ("generate",), prices={"default": 10}),
                ModelSpec(self.name + "-image", "image", ("generate",), prices={"default": 1})]

    def plan(self, req: CreateRequest, settings: Any, snapshots: Mapping[str, Mapping]) -> Plan:
        """Options steer the plan: `account_cost`, `completion` (hold|detach), `submit_timeout_s`, `price`;
        `refuse` (a platform code, with `param`) makes it refuse the request as a real spec does."""
        video = req.model.endswith("-video")
        o = req.options
        if o.get("refuse"):
            raise InputError(o["refuse"], o.get("param"))
        return Plan(
            operation="generate", media_type="video" if video else "image", model=req.model,
            params={"mime": "video/mp4" if video else "image/png", "completion": o.get("completion", "detach")},
            completion=Completion(o.get("completion", "detach")), after_sent=AfterSent.RECONCILE,
            wallet_kind="video" if video else "image", price=o.get("price", 10 if video else 1),
            pool="video" if video else "image", account_cost=o.get("account_cost", 1),
            submit_timeout_s=o.get("submit_timeout_s", 60))

    def codes(self) -> dict[str, Any]:
        return {}

    def settings(self) -> dict[str, Any]:
        n = self.name
        return {n + ".claims_enabled": Spec(n + ".claims_enabled", bool, True, "Drain mode off."),
                n + ".video_budget": Spec(n + ".video_budget", int, 4, "Daily video jobs per account."),
                n + ".render_deadline_s": Spec(n + ".render_deadline_s", int, 9000, "Render deadline."),
                n + ".alert_drift_rate": Spec(n + ".alert_drift_rate", float, 0.5, "Drift alert (warning)."),
                n + ".alert_deadline_rate": Spec(n + ".alert_deadline_rate", float, 0.5, "Drift alert (deadline)."),
                n + ".browser_slots": Spec(n + ".browser_slots", int, 4, "Browser sessions at once (browser only).",
                                             min=1, max=30)}

    def parse_credential(self, raw: bytes) -> ParsedCredential:
        return ParsedCredential(blob=raw, hint="fake")

    def deadline_cause(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str:
        return "render.deadline"

    def unclear_at_deadline(self, upstream_text: str, provider_state: Mapping[str, Any]) -> str | None:
        return self.unclear

    def extras(self, snapshots: Mapping[str, Mapping]) -> dict:
        return {}


class FakeSettings:
    """The settings view the store reads: overrides, then the fake spec's defaults, then the core registry's."""

    def __init__(self, spec: FakeSpec | None = None, **overrides: Any):
        self._values = {s.key: s.default for s in REGISTRY.specs.values()}
        self._values.update({k: s.default for k, s in (spec or FakeSpec()).settings().items()})
        self._values.update({k.replace("__", "."): v for k, v in overrides.items()})

    def get(self, key: str) -> Any:
        return self._values[key]

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value


@dataclass(frozen=True)
class Step:
    """One scripted submit. `send=True`: mark_sent runs first, as a real engine does right before the irreversible
    action; `send=False` is a failure that happens before it. `delay_s` stalls (a hung browser)."""
    outcome: SubmitResult | BaseException | Callable[[Any, JobView], Awaitable[SubmitResult]]
    send: bool = True
    delay_s: float = 0.0


def sent(result: SubmitResult | BaseException, **kw) -> Step:
    return Step(result, True, **kw)


def unsent(result: SubmitResult | BaseException, **kw) -> Step:
    return Step(result, False, **kw)


class FakeCtx:
    """A RunCtx for the engine. Each member delegates to a callable the test wires to the store, else does nothing."""

    def __init__(self, account=None, *, mark_sent=None, fence=None, report_ref=None, event=None, settings=None):
        self.account, self.browser_token, self.settings, self.log = account, None, settings, None
        self._hooks = {"mark_sent": mark_sent, "fence": fence, "report_ref": report_ref, "event": event}
        self.marked = 0

    async def _call(self, name: str, *args) -> None:
        if self._hooks[name] is not None:
            await self._hooks[name](*args)

    async def fence(self) -> None:
        await self._call("fence")

    async def mark_sent(self) -> None:
        await self._call("mark_sent")
        self.marked += 1

    async def report_ref(self, ref: str, state: Mapping[str, Any]) -> None:
        await self._call("report_ref", ref, state)

    async def event(self, text: str, stage: str, source: str) -> None:
        await self._call("event", text, stage, source)

    async def evidence(self, name: str, data: bytes, mime: str) -> None:
        return None


class FakeEngine:
    """The worker half. Scripts are keyed by job id (str), by prompt, or by "*" (any job); each call consumes
    one entry and the last entry repeats. `calls` records (method, job id) for the test to read."""

    def __init__(self) -> None:
        self._submit: dict[str, deque[Step]] = defaultdict(deque)
        self._poll: dict[str, deque[PollResult]] = defaultdict(deque)
        self._reconcile: dict[str, deque[Reconciled]] = defaultdict(deque)
        self.calls: list[tuple[str, str]] = []
        self.texts: list[tuple[str, str, str | None]] = []     # (method, job id, the sent_text it was given)
        self.health = Health(state="alive")

    def script_submit(self, key, *steps: Step) -> None:
        self._submit[str(key)].extend(steps)

    def script_poll(self, key, *results: PollResult) -> None:
        self._poll[str(key)].extend(results)

    def script_reconcile(self, key, *results: Reconciled) -> None:
        self._reconcile[str(key)].extend(results)

    @staticmethod
    def _next(table: dict[str, deque], job: JobView):
        for key in (str(job.id), job.prompt, "*"):
            queue = table.get(key)
            if queue:
                return queue.popleft() if len(queue) > 1 else queue[0]
        return None

    def outputs(self, job: JobView, n: int = 1) -> tuple[Output, ...]:
        mime = job.params.get("mime", "video/mp4")
        return tuple(Output("fake://%s/%d" % (job.id, i), mime, i, "up-%s-%d" % (job.id, i)) for i in range(n))

    async def submit(self, ctx, job: JobView) -> SubmitResult:
        self.calls.append(("submit", str(job.id)))
        self.texts.append(("submit", str(job.id), getattr(job, "sent_text", None)))
        await failpoints.hit("fake.submit.start")
        step = self._next(self._submit, job)
        if step is None:
            step = sent(SubmitResult(Completed(self.outputs(job)) if job.params.get("completion") == "hold"
                                     else Accepted("fake-ref-%s" % job.id)))
        if step.send:
            await ctx.mark_sent()
        await failpoints.hit("fake.submit.sent")
        if step.delay_s:
            await asyncio.sleep(step.delay_s)
        if isinstance(step.outcome, BaseException):
            raise step.outcome
        if callable(step.outcome):
            return await step.outcome(ctx, job)
        return step.outcome

    async def poll(self, ctx, job: JobView) -> PollResult:
        self.calls.append(("poll", str(job.id)))
        self.texts.append(("poll", str(job.id), getattr(job, "sent_text", None)))
        await failpoints.hit("fake.poll")
        return self._next(self._poll, job) or PollResult(Done(self.outputs(job)))

    async def reconcile(self, ctx, job: JobView) -> Reconciled:
        self.calls.append(("reconcile", str(job.id)))
        self.texts.append(("reconcile", str(job.id), getattr(job, "sent_text", None)))
        await failpoints.hit("fake.reconcile")
        return self._next(self._reconcile, job) or StillUnknown(answered=True)    # the read worked, it found nothing

    async def fetch(self, ctx, out: Output):
        yield b"fake-bytes:" + out.ref.encode()

    async def check_account(self, ctx) -> Health:
        return self.health

    async def run_command(self, ctx, kind: str, args: dict) -> dict:
        return {"ok": True, "kind": kind}

    async def probe_proxy(self, proxy: Proxy) -> ProxyVerdict:
        return ProxyVerdict(True)
