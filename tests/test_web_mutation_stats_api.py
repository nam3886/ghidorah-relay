"""Mutation markers for the jobs list's created_at window. Each mutant must turn the
Vitest spec it names red, with the exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py mutates.
A plain `check.sh` deselects these tests."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
JOBS = f"{SRC}/api/jobs.ts"

JOBS_SPEC = "FAILED tests/unit/jobs/jobs-api.spec.ts > list > "


@mutation(JOBS, "created_from_ms: q.createdFromMs,", "created_from_ms: q.createdToMs,",
          expect=JOBS_SPEC + "sends the created_at window as epoch ms")
@mutation(JOBS, "created_to_ms: q.createdToMs,", "", expect=JOBS_SPEC + "sends the created_at window as epoch ms")
@mutation(JOBS, "created_from_ms: q.createdFromMs,", "created_from_ms: q.createdFromMs ?? 0,",
          expect=JOBS_SPEC + "sends the created_at window as epoch ms")
@pytest.mark.web_unit
def test_jobs_created_window_params():
    vitest("tests/unit/jobs/jobs-api.spec.ts")
