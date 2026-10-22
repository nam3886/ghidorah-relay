"""The estimate on a job's status answer: `progress`, `render_started_at`, `estimated_finish_at`.

What it estimates. The provider reports no percentage, so progress is a guess from time: how long this job has been
rendering against how long recent jobs of the same SHAPE took. A shape is the provider, the BASE catalog model and the
length in seconds. The ratio and style a client wrote into the variant name (`base-5s-16:9`, `base-5s-21:9`) are not
part of it: `jobs.model` stores the variant, so the samples are matched on the base model by its catalog names (the
exact names, and a name that extends one of them with `-`, unless a longer catalog name claims it: the rule of
`doors.resolve_model`). No migration, and the base is read from the live catalog.

The clock is `COALESCE(render_since, sent_at)`, the clock the render deadline, the reaper and the claim already use,
read inside the status query so the api host's clock cannot skew it. A re-check or a re-send restarts it, and so the
estimate restarts too.

The failure mode is to degrade. The median read runs on its own connection in its own short transaction with a
statement timeout and a guard timeout; whatever goes wrong the answer is still served, stage-only (`waiting` shows 5, no
finish time), nothing is cached, and the log carries the exception class only (a driver message can hold row text).
Cost is bounded by shapes, not polls: one read per shape per TTL (single flight), and the first value a process
computes for a job is pinned so a refresh never moves a job already shown. The api is one process; with more, only the
pin would be per process.

The estimate aggregates all keys' jobs of a shape, bucketed to 60 s, and the finish time is rounded to the minute: it
cannot isolate one customer's job.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg_pool import AsyncConnectionPool

from relay_core import db
from relay_core.providers import ModelSpec, ProviderSpec

log = logging.getLogger(__name__)

WAIT_FLOOR = 5
WAIT_CAP = 90
FETCH = 95
DONE = 100
WINDOW = 20                  # the newest finished jobs of a shape the median is taken over
MIN_SAMPLES = 5              # below this the median is not trusted: a fixed fallback moves progress, no finish time
AGE_DAYS = 30
SAMPLE_MAX_S = 9000          # the render deadline: a longer sample is a recovered or re-checked outlier
FALLBACK_S = {"video": 3900, "image": 90}       # measured medians: video 56 and 75 minutes, image 55 to 90 seconds
BUCKET_S = 60
TTL_S = 300
READ_TIMEOUT = "500ms"       # the statement timeout of the estimate read
GUARD_TIMEOUT_S = 1.5
SLOW_READ_S = 0.25
PIN_MAX = 4096

_RENDERED = ("waiting", "fetching", "done")


@dataclass(frozen=True)
class Expected:
    seconds: int
    measured: bool      # true only when the seconds come from at least MIN_SAMPLES finished jobs


@dataclass(frozen=True)
class Shape:
    """What a sample must match. `names` are the catalog names of the base model; `stems` those names plus `-`;
    `others` and `other_stems` the same for the catalog models whose names extend one of the base's: they win it."""
    provider: str
    media_type: str
    names: tuple[str, ...]
    stems: tuple[str, ...]
    others: tuple[str, ...]
    other_stems: tuple[str, ...]
    duration: int | float | None


def shape_of(spec: ProviderSpec, base: ModelSpec, duration: Any) -> Shape:
    """The shape of a job whose model resolved to `base`. A duration counts only when it is a JSON number."""
    longer = [n for m in spec.catalog() if m.id != base.id for n in m.names()
              if any(n.startswith(b + "-") for b in base.names())]      # a name the longer catalog name claims
    numeric = duration if isinstance(duration, int | float) and not isinstance(duration, bool) else None
    return Shape(spec.name, base.media_type, base.names(), tuple(n + "-" for n in base.names()), tuple(longer),
                 tuple(n + "-" for n in longer), numeric)


def progress_of(status: str, elapsed_s: float | None, expected_s: int | None) -> int | None:
    """0-100 from the DB status and the render clock; None for a failed job. An unknown status is a KeyError on purpose:
    a new status must be classified here."""
    if status in ("queued", "submitting", "unknown"):
        return 0
    if status == "fetching":
        return FETCH
    if status == "done":
        return DONE
    if status == "failed":
        return None
    if status == "waiting":
        if elapsed_s is None or expected_s is None:
            return WAIT_FLOOR
        return min(WAIT_CAP, WAIT_FLOOR + int((WAIT_CAP - WAIT_FLOOR) * max(0.0, elapsed_s / expected_s)))
    raise KeyError(status)


def render_started(status: str, clock: datetime | None) -> datetime | None:
    """The render clock once a render has begun; never before, whatever stale columns a requeued job holds."""
    return clock if status in _RENDERED else None


def finish_of(status: str, clock: datetime | None, elapsed_s: float | None,
              expected: Expected | None) -> datetime | None:
    """The clock plus a MEASURED expected time, to the minute, only while waiting and not overdue. Never from the fixed
    fallback: no promise without data."""
    if status == "waiting" and clock is not None and elapsed_s is not None and expected is not None \
            and expected.measured and elapsed_s < expected.seconds:
        return (clock + timedelta(seconds=expected.seconds + 30)).replace(second=0, microsecond=0)
    return None


def bucket(median_s: float) -> int:
    """The median rounded half up to a whole BUCKET_S (Python's round() would go to the even one), at least one."""
    return max(BUCKET_S, BUCKET_S * int(median_s / BUCKET_S + 0.5))


# Each condition is its own literal so a mutation marker can name exactly one.
_SAMPLES = (
    "SELECT count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY d) "
    "FROM (SELECT EXTRACT(EPOCH FROM j.finished_at - COALESCE(j.render_since, j.sent_at))::float8 AS d "
    "FROM jobs j WHERE j.provider = %(provider)s "
    "AND j.status = 'done' "
    "AND (j.model = ANY(%(names)s) "
    "OR EXISTS (SELECT 1 FROM unnest(%(stems)s::text[]) s WHERE starts_with(j.model, s))) "
    "AND NOT (j.model = ANY(%(others)s) "
    "OR EXISTS (SELECT 1 FROM unnest(%(other_stems)s::text[]) o WHERE starts_with(j.model, o))) "
    "AND (CASE WHEN jsonb_typeof(j.params -> 'duration') = 'number' THEN (j.params ->> 'duration')::numeric END) "
    "IS NOT DISTINCT FROM %(duration)s::numeric "
    "AND COALESCE(j.render_since, j.sent_at) IS NOT NULL "
    "AND j.finished_at > clock_timestamp() - make_interval(days => %(age_days)s) "
    "AND j.finished_at > COALESCE(j.render_since, j.sent_at) "
    "AND j.finished_at <= COALESCE(j.render_since, j.sent_at) + make_interval(secs => %(max_s)s) "
    "ORDER BY j.finished_at DESC LIMIT %(window)s) s")


async def _render_samples(conn, shape: Shape) -> tuple[int, float | None]:
    """(how many, median seconds) over the newest WINDOW comparable finished jobs."""
    row = await (await conn.execute(_SAMPLES, {
        "provider": shape.provider, "names": list(shape.names), "stems": list(shape.stems),
        "others": list(shape.others), "other_stems": list(shape.other_stems), "duration": shape.duration,
        "age_days": AGE_DAYS, "max_s": SAMPLE_MAX_S, "window": WINDOW})).fetchone()
    return row[0], row[1]


async def _read_shape(pool: AsyncConnectionPool, shape: Shape) -> Expected:
    started = time.monotonic()
    async with asyncio.timeout(GUARD_TIMEOUT_S), db.tx(pool) as conn:
        await conn.execute("SELECT set_config('statement_timeout', %s, true)", (READ_TIMEOUT,))
        n, median = await _render_samples(conn, shape)
    measured = n >= MIN_SAMPLES
    value = Expected(bucket(median) if measured else FALLBACK_S[shape.media_type], measured)
    if time.monotonic() - started > SLOW_READ_S:
        log.warning("render estimate read slow: %.0f ms", (time.monotonic() - started) * 1000)
    return value


_now = time.monotonic     # a test replaces it to move past the TTL
_SHAPES: dict[Shape, tuple[Expected, float]] = {}
_PINNED: OrderedDict[Any, Expected] = OrderedDict()
_INFLIGHT: dict[Shape, asyncio.Future] = {}


def _pin(job_id, value: Expected) -> Expected:
    _PINNED[job_id] = value
    _PINNED.move_to_end(job_id)
    while len(_PINNED) > PIN_MAX:
        _PINNED.popitem(last=False)
    return value


async def expected_for(pool: AsyncConnectionPool, shape: Shape, job_id) -> Expected | None:
    """The expected render time of a shape for this job, or None when the read degraded. Never raises (a cancel
    passes). A failure is logged by class and never cached."""
    if job_id in _PINNED:
        _PINNED.move_to_end(job_id)
        return _PINNED[job_id]
    cached = _SHAPES.get(shape)
    if cached is not None and _now() < cached[1]:
        return _pin(job_id, cached[0])
    if shape in _INFLIGHT:
        joined = await asyncio.shield(_INFLIGHT[shape])
        return None if joined is None else _pin(job_id, joined)
    flight: asyncio.Future = asyncio.get_running_loop().create_future()
    _INFLIGHT[shape] = flight
    value = None
    try:
        value = await _read_shape(pool, shape)
        _SHAPES[shape] = (value, _now() + TTL_S)
    except Exception as exc:
        log.warning("render estimate read failed: %s", type(exc).__name__)
    finally:
        del _INFLIGHT[shape]
        flight.set_result(value)
    return None if value is None else _pin(job_id, value)
