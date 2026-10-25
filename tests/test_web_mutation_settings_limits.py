"""Mutation markers for the Settings page's limits: the allowed text under a setting, the Fixed limits card, the
Containers card and the General card's links that reach them. Each mutant must turn the Vitest spec it names red,
with the exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py does the mutating. A plain `check.sh`
deselects these tests. The expected failed test lines are built the way the runner reads them: `<spec prefix> +
"<describe> > <test title>"`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
CONTAINER_LIMITS = f"{SRC}/components/settings/ContainerLimits.vue"
SETTINGS_VIEW = f"{SRC}/views/SettingsView.vue"

SPEC = "FAILED tests/unit/settings/limits.spec.ts > "
BOOT_TABLE = SPEC + "the boot-only card > is a table of Name and Value, an unset value as a dash"
MATRIX = SPEC + "the containers card > has a column per service and a row per measure under one band"
REPORTED_AGO = SPEC + ("the containers card > says this process for the api and how long ago a worker reported, "
                       "under the service name")
FIVE_SECTIONS = SPEC + ("the page > lists the five sections in the General card and moves the mark to Fixed limits on "
                        "a click")
FIXED_ROWS = SPEC + "the page > shows the Fixed limits rows that the settings read brought"
CONTAINER_ROWS = SPEC + "the page > shows the Containers rows that the settings read brought"


@mutation(SETTINGS_VIEW, "value: bootValue(row.value)", "value: String(row.value)", expect=BOOT_TABLE)
@mutation(CONTAINER_LIMITS, "ago(row.reported_ms)", '"never"', expect=REPORTED_AGO)
@mutation(CONTAINER_LIMITS, "  bootValue(row[line.key as (typeof LIMITS)[number][1]]);",
          "  String(row[line.key as (typeof LIMITS)[number][1]]);", expect=MATRIX)
@mutation(SETTINGS_VIEW, '  { id: "limits", label: "Fixed limits" },\n', "", expect=FIVE_SECTIONS)
@mutation(SETTINGS_VIEW,
          '  { id: "password", label: "Change password" },\n  { id: "boot", label: "Boot-only values" },\n',
          '  { id: "boot", label: "Boot-only values" },\n  { id: "password", label: "Change password" },\n',
          expect=FIVE_SECTIONS)
@mutation(SETTINGS_VIEW, "fixed.value = data.fixed;", "fixed.value = [];", expect=FIXED_ROWS)
@mutation(SETTINGS_VIEW, "containers.value = data.containers;", "containers.value = [];", expect=CONTAINER_ROWS)
@pytest.mark.web_unit
def test_settings_limits_rules():
    vitest("tests/unit/settings/limits.spec.ts")
