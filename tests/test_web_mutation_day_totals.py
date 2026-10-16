"""Mutation markers for the Jobs list's per-day totals call (`tz` query param, `day_totals` in the page). Each mutant
must turn the Vitest spec it names red; `webunit.vitest` runs the spec and scripts/mutate.py mutates. A plain
`check.sh` deselects these tests."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

JOBS = "apps/admin-web/src/api/jobs.ts"
JOBS_SPEC = "FAILED tests/unit/jobs/jobs-api.spec.ts > list > "
SENDS_TZ = "sends the display zone as tz, and nothing when it is not set"


@mutation(JOBS, "      tz: q.tz,\n", "", expect=JOBS_SPEC + SENDS_TZ)
@mutation(JOBS, "      tz: q.tz,\n", '      tz: q.tz ?? "UTC",\n', expect=JOBS_SPEC + SENDS_TZ)
@pytest.mark.web_unit
def test_jobs_tz_param():
    vitest("tests/unit/jobs/jobs-api.spec.ts")
