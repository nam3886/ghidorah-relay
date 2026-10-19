"""The fixed-limits registry: every row reads the constant the code runs with, a provider's rows come from its
`Caps` and its own `limits`, and the text the admin reads is the value with its unit."""

import importlib
import inspect
import re

from relay_core import budget
from relay_core.jobs import engine, reaper
from relay_core.jobs.worker import Worker
from relay_core.limits import CORE, caps_limits, fixed_limits, value_text
from relay_core.providers import ProviderRegistry
from relay_core.testing.fake_provider import FAKE_FRAMES, FakeSpec
from relay_core.testing.mutation import mutation

LIMITS = "packages/relay-core/src/relay_core/limits.py"
PROVIDER_ROWS = 'getattr(spec, "limits", ())'


def registry(*specs) -> ProviderRegistry:
    r = ProviderRegistry()
    for spec in specs:
        r.register(spec)
    return r


def test_every_core_row_is_the_live_constant_with_a_reason_and_a_unique_name():
    names = [f.name for f in CORE]
    assert len(names) == len(set(names)), "a constant is listed once"
    for f in CORE:
        module, const = f.name.rsplit(".", 1)
        assert getattr(importlib.import_module("relay_core." + module), const) == f.value, f.name
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", const), f.name
        assert f.reason.strip() and "\n" not in f.reason, f.name
        assert not isinstance(f.value, bool), f.name


def test_the_rows_a_reader_cannot_get_wrong_are_listed_with_their_known_values():
    rows = {f.name: f.value for f in CORE}
    assert rows["jobs.engine.MAX_INFLIGHT"] == 32 and rows["jobs.poller.MAX_INFLIGHT"] == 16
    assert rows["leases.LEASE_TTL_S"] == 60.0 and rows["budget.RAMP_PERIOD_S"] == 60.0
    assert rows["jobs.webhooks.RETRY_AFTER_S"] == (30, 300)
    assert rows["leases.COMMAND_HARD_S"]["recover"] == 600


def test_the_reaper_waits_three_beats_and_the_worker_defaults_are_the_constants_it_lists():
    assert reaper.OWNER_GONE_S == 3 * engine.BEAT_S
    params = inspect.signature(Worker.__init__).parameters
    assert params["beat_s"].default == engine.BEAT_S
    assert params["reaper_interval_s"].default == reaper.INTERVAL_S
    assert inspect.signature(budget.BrowserBudget.__init__).parameters["ramp_period_s"].default == budget.RAMP_PERIOD_S


def test_the_value_text_carries_the_unit():
    assert value_text(720, "s") == "720 s"
    assert value_text(0.5) == "0.5"
    assert value_text(12 * 1024 * 1024, "bytes") == "12 MiB"
    assert value_text(16 * 1024, "bytes") == "16 KiB"
    assert value_text(1000, "bytes") == "1000 bytes"
    assert value_text((30, 300), "s") == "30, 300 s"
    assert value_text({"recover": 600, "check": 180}, "s") == "recover 600 s, check 180 s"
    assert value_text({"max_bytes": 12 * 1024 * 1024, "timeout_s": 20.0}) == "max_bytes 12 MiB, timeout_s 20 s"
    assert value_text("no limit") == "no limit" and value_text("never", "s") == "never"
    assert value_text(32, "jobs") == "32 jobs" and value_text(7) == "7"


def test_a_providers_caps_become_rows_with_a_reason_each():
    rows = {f.name: f for f in caps_limits(FakeSpec(account_slots=2, max_attempts=4))}
    assert [(n, f.value) for n, f in rows.items()] == [
        ("caps.account_slots", 2), ("caps.max_attempts", 4), ("caps.suspect_threshold", 3),
        ("caps.proxy_rest_s", 1800), ("caps.check_interval_s", 21600), ("caps.catalog_refresh_s", "never"),
        ("max_prompt_chars", 1000)]
    assert all(f.reason for f in rows.values())
    assert caps_limits(FakeSpec(account_slots=None))[0].value == "no limit"
    assert "frames.max_sent_chars" not in rows


def test_a_provider_with_frames_lists_the_longest_text_it_is_sent():
    rows = {f.name: f.value for f in caps_limits(FakeSpec(frames=FAKE_FRAMES))}
    assert rows["frames.max_sent_chars"] == FAKE_FRAMES.max_sent_chars


@mutation(LIMITS, PROVIDER_ROWS, "()", expect="fake.demo_bound")
def test_the_fixed_limits_hold_the_core_first_then_each_provider_with_its_own_rows():
    out = fixed_limits(registry(FakeSpec("zeta"), FakeSpec("alpha")))
    groups = [row["group"] for row in out]
    assert groups[0] == "core" and groups[-1] == "zeta"
    assert [g for i, g in enumerate(groups) if i == 0 or g != groups[i - 1]] == ["core", "alpha", "zeta"]
    assert all(set(row) == {"group", "name", "value", "reason"} for row in out)
    own = [row for row in out if row["group"] == "alpha" and row["name"].startswith("alpha.")]
    assert own == [{"group": "alpha", "name": "alpha.demo_bound", "value": "7 items",
                    "reason": "A bound of the fake provider, for tests."}], "fake.demo_bound"
    assert {"group": "core", "name": "jobs.engine.MAX_INFLIGHT", "value": "32 jobs",
            "reason": "submits one worker runs at once"} in out


def test_a_spec_that_declares_no_limits_adds_only_its_caps_rows():
    spec = FakeSpec()
    del spec.limits
    names = [row["name"] for row in fixed_limits(registry(spec)) if row["group"] == "fake"]
    assert names[0] == "caps.account_slots" and "fake.demo_bound" not in names

