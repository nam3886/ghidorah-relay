"""Mutation markers for the hourly-stats client call and the jobs list's created_at window. Each mutant must turn the
Vitest spec it names red, with the exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py mutates.
A plain `check.sh` deselects these tests."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
SHELL = f"{SRC}/api/shell.ts"
JOBS = f"{SRC}/api/jobs.ts"

SHELL_SPEC = "FAILED tests/unit/shell-api.spec.ts > hourly stats > "
JOBS_SPEC = "FAILED tests/unit/jobs/jobs-api.spec.ts > list > "


@mutation(SHELL, 'request("/stats/hourly", { query: { provider } })', 'request("/stats", { query: { provider } })',
          expect=SHELL_SPEC + "asks /admin/stats/hourly for the provider")
@mutation(SHELL, 'request("/stats/hourly", { query: { provider } })', 'request("/stats/hourly")',
          expect=SHELL_SPEC + "asks /admin/stats/hourly for the provider")
@pytest.mark.web_unit
def test_hourly_stats_call():
    vitest("tests/unit/shell-api.spec.ts")


POOL_SPEC = "FAILED tests/unit/shell-api.spec.ts > pool stats > "


@mutation(SHELL, 'request("/stats/pool", { query: { provider } })', 'request("/stats", { query: { provider } })',
          expect=POOL_SPEC + "asks /admin/stats/pool for the provider")
@mutation(SHELL, 'request("/stats/pool", { query: { provider } })', 'request("/stats/pool")',
          expect=POOL_SPEC + "asks /admin/stats/pool for the provider")
@pytest.mark.web_unit
def test_pool_stats_call():
    vitest("tests/unit/shell-api.spec.ts")


@mutation(JOBS, "created_from_ms: q.createdFromMs,", "created_from_ms: q.createdToMs,",
          expect=JOBS_SPEC + "sends the created_at window as epoch ms")
@mutation(JOBS, "created_to_ms: q.createdToMs,", "", expect=JOBS_SPEC + "sends the created_at window as epoch ms")
@mutation(JOBS, "created_from_ms: q.createdFromMs,", "created_from_ms: q.createdFromMs ?? 0,",
          expect=JOBS_SPEC + "sends the created_at window as epoch ms")
@pytest.mark.web_unit
def test_jobs_created_window_params():
    vitest("tests/unit/jobs/jobs-api.spec.ts")
