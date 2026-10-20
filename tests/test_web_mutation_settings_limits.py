"""Mutation markers for the Settings page's limits: the allowed text under a setting, the Fixed limits card, the
Containers card and the sub-navigation that reaches them. Each mutant must turn the Vitest spec it names red, with the
exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py does the mutating. A plain `check.sh`
deselects these tests. The expected failed test lines are built the way the runner reads them: `<spec prefix> +
"<describe> > <test title>"`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
SETTINGS_TABLE = f"{SRC}/components/settings/SettingsTable.vue"
FIXED_LIMITS = f"{SRC}/components/settings/FixedLimits.vue"
CONTAINER_LIMITS = f"{SRC}/components/settings/ContainerLimits.vue"
SETTINGS_VIEW = f"{SRC}/views/SettingsView.vue"

SPEC = "FAILED tests/unit/settings/limits.spec.ts > "
TABLE_TEXT = SPEC + ("the allowed text > shows the allowed text under the description in the table, and none for a "
                     "setting with no bound")
PHONE_TEXT = SPEC + ("the allowed text > shows the allowed text under the description in the phone list, and none for "
                     "a setting with no bound")
GROUPS = SPEC + ("the fixed limits card > lists one block per group in the server's order, each row with its name, "
                 "value and reason")
NOT_REPORTED = SPEC + ("the containers card > says a worker that has not reported is not reported by this worker "
                       "version, and marks a silent one stale")
REPORTED_AGO = SPEC + ("the containers card > says this process for the api and shows a worker's four texts with how "
                       "long ago it reported")
SIX_SECTIONS = SPEC + "the page > lists the six sections and moves the mark to Fixed limits on a click"
FIXED_ROWS = SPEC + "the page > shows the Fixed limits rows that the settings read brought"
CONTAINER_ROWS = SPEC + "the page > shows the Containers rows that the settings read brought"
NOTE = SPEC + "the page > states what a saved value does in the settings card note"


@mutation(SETTINGS_TABLE, '<span v-if="row.allowed" class="allowed">', '<span v-if="false" class="allowed">',
          expect=TABLE_TEXT)
@mutation(SETTINGS_TABLE, '<div v-if="row.allowed" class="allowed">', '<div v-if="false" class="allowed">',
          expect=PHONE_TEXT)
@mutation(FIXED_LIMITS, '<h3 class="limits__name">{{ g.name }}</h3>',
          '<h3 class="limits__name">{{ groups[0]!.name }}</h3>', expect=GROUPS)
@mutation(CONTAINER_LIMITS, '<p v-if="!row.reported" class="containers__none">',
          '<p v-if="false" class="containers__none">', expect=NOT_REPORTED)
@mutation(CONTAINER_LIMITS, "ago(row.reported_ms)", '"never"', expect=REPORTED_AGO)
@mutation(SETTINGS_VIEW, '  { id: "limits", label: "Fixed limits" },\n', "", expect=SIX_SECTIONS)
@mutation(SETTINGS_VIEW, "fixed.value = data.fixed;", "fixed.value = [];", expect=FIXED_ROWS)
@mutation(SETTINGS_VIEW, "containers.value = data.containers;", "containers.value = [];", expect=CONTAINER_ROWS)
@mutation(SETTINGS_VIEW, 'note="A saved value is kept in the database. Reset returns to the default."',
          'note="A saved value is kept in the database and wins over the environment."', expect=NOTE)
@pytest.mark.web_unit
def test_settings_limits_rules():
    vitest("tests/unit/settings/limits.spec.ts")
