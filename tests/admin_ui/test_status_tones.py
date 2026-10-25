"""The status chips of the Vue admin show one tone per status, from one JSON registry. This test reads every status the
backend can return from the backend itself and fails when the registry does not know one, so a new status cannot
reach the page without a tone."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "packages" / "relay-core" / "src" / "relay_core"
REGISTRY = ROOT / "apps" / "admin-web" / "src" / "components" / "shared" / "status-tones.json"
TONES = {"ok", "info", "warn", "bad", "neutral"}


def check_set(migration: str, column: str) -> set[str]:
    """The values of `CHECK (<column> IN (...))` in a migration."""
    text = (CORE / "db" / "migrations" / migration).read_text()
    found = re.search(r"CHECK \(%s IN\s*\(([^)]*)\)" % column, text)
    assert found, "no CHECK set for %s in %s" % (column, migration)
    return set(re.findall(r"'(\w+)'", found.group(1)))


def backend_statuses() -> dict[str, set[str]]:
    alerts = (CORE / "alerts.py").read_text()
    source = re.search(r"def source[\s\S]*?return ([^\n]*)", (CORE / "settings.py").read_text())
    assert source, "Settings.source not found"
    return {
        "job status": check_set("0003_jobs.sql", "status"),
        "account state": check_set("0004_accounts.sql", "state"),
        "command status": check_set("0005_operations.sql", "status"),
        "alert kind": check_set("0026_pool_drift_alerts.sql", "alert")
        | set(re.findall(r"'(\w+)' AS kind", alerts)),
        "alert severity": set(re.findall(r"'(\w+)' AS severity", alerts)),
        "setting source": set(re.findall(r'"(\w+)"', source.group(1))),
    }


def uncovered(statuses: dict[str, set[str]], registry: dict) -> list[str]:
    return sorted("%s: %s" % (name, value) for name, values in statuses.items() for value in values
                  if value not in registry)


def test_the_registry_has_a_known_tone_and_a_label_for_every_entry():
    registry = json.loads(REGISTRY.read_text())
    for status, entry in registry.items():
        assert status == status.lower(), status
        assert entry["tone"] in TONES, status
        assert entry["label"].strip(), status


def test_the_extractors_find_the_whole_sets():
    found = backend_statuses()
    assert found["job status"] == {"queued", "submitting", "unknown", "waiting", "fetching", "done", "failed"}
    assert found["account state"] == {"available", "dead", "disabled"}
    assert found["command status"] == {"queued", "running", "done", "failed"}
    assert found["alert kind"] == {"clamp", "drift", "cost_mismatch", "pool_drift", "unclear_reply"}
    assert found["alert severity"] == {"error", "warning"}
    assert found["setting source"] == {"db", "default"}


def test_the_registry_covers_every_status_the_backend_can_return():
    registry = json.loads(REGISTRY.read_text())
    assert uncovered(backend_statuses(), registry) == []


@pytest.mark.parametrize("planted", ["job status", "account state", "command status", "alert kind"])
def test_a_new_backend_status_fails_the_coverage_check(planted):
    registry = json.loads(REGISTRY.read_text())
    statuses = backend_statuses()
    statuses[planted] = statuses[planted] | {"brand_new"}
    assert uncovered(statuses, registry) == ["%s: brand_new" % planted]
