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
VALUE_TABLE = f"{SRC}/components/settings/ValueTable.vue"
CONTAINER_LIMITS = f"{SRC}/components/settings/ContainerLimits.vue"
SETTINGS_VIEW = f"{SRC}/views/SettingsView.vue"

SPEC = "FAILED tests/unit/settings/limits.spec.ts > "
TABLE_TEXT = SPEC + ("the allowed text > shows the allowed text under the description in the table, and none for a "
                     "setting with no bound")
PHONE_TEXT = SPEC + ("the allowed text > shows the allowed text under the description in the phone list, and none for "
                     "a setting with no bound")
GROUPS = SPEC + ("the fixed limits card > is a table of Name, Value and Reason with one band per group in the "
                 "server's order")
PHONE_BLOCKS = SPEC + ("the fixed limits card > is one block per row under a heading per group on a phone, with no "
                       "table")
BOOT_TABLE = SPEC + "the boot-only card > is a table of Name and Value, an unset value as a dash"
MATRIX = SPEC + ("the containers card > has a column per service and a row per measure, then one per boot knob any "
                 "service reported")
NOT_REPORTED = SPEC + ("the containers card > says a worker that has not reported is not reported by this worker "
                       "version, and marks a silent one stale")
NOT_REPORTED_PHONE = SPEC + ("the containers card > is one block per service on a phone, with its stamp, its chip and "
                             "its own boot knobs")
REPORTED_AGO = SPEC + ("the containers card > says this process for the api and how long ago a worker reported, "
                       "under the service name")
STALE_CHIP = SPEC + ("the containers card > marks only a silent worker stale: a fresh one and the api carry no "
                     "chip")
EXACT_WORDS = SPEC + ("the containers card > shows an unreadable limit and a limit that is not set as the server's "
                      "exact words, apart from a dash")
DIMMED = SPEC + ("the containers card > reads not set for a value a service reported unset, and dims the dash of a "
                 "value it does not report")
NO_BOOT_BAND = SPEC + "the containers card > has no Boot values band when no service reports a boot value"
CARD_ORDER = SPEC + "the page > puts Change password first among the cards, ahead of Settings"
SIX_SECTIONS = SPEC + "the page > lists the six sections and moves the mark to Fixed limits on a click"
FIXED_ROWS = SPEC + "the page > shows the Fixed limits rows that the settings read brought"
CONTAINER_ROWS = SPEC + "the page > shows the Containers rows that the settings read brought"
NOTE = SPEC + "the page > states what a saved value does in the settings card note"


@mutation(SETTINGS_TABLE, '<span v-if="row.allowed" class="allowed">', '<span v-if="false" class="allowed">',
          expect=TABLE_TEXT)
@mutation(SETTINGS_TABLE, '<div v-if="row.allowed" class="allowed">', '<div v-if="false" class="allowed">',
          expect=PHONE_TEXT)
@mutation(VALUE_TABLE, '<th scope="rowgroup" :colspan="columns">{{ g.name }}</th>',
          '<th scope="rowgroup" :colspan="columns">{{ groups[0]!.name }}</th>', expect=GROUPS)
@mutation(VALUE_TABLE, '<h3 v-if="g.name" class="set-list__group">{{ g.name }}</h3>',
          '<h3 v-if="false" class="set-list__group">{{ g.name }}</h3>', expect=PHONE_BLOCKS)
@mutation(VALUE_TABLE, '<td>{{ row.value }}</td>', '<td>{{ row.name }}</td>', expect=GROUPS)
@mutation(VALUE_TABLE, '<th v-if="reasons" scope="col">Reason</th>', '', expect=GROUPS)
@mutation(SETTINGS_VIEW, "value: bootValue(row.value)", "value: String(row.value)", expect=BOOT_TABLE)
@mutation(CONTAINER_LIMITS, '<span v-if="!row.reported" class="set-svc__none">',
          '<span v-if="false" class="set-svc__none">', expect=NOT_REPORTED)
@mutation(CONTAINER_LIMITS, '<p v-if="!row.reported" class="set-block__none">',
          '<p v-if="false" class="set-block__none">', expect=NOT_REPORTED_PHONE)
@mutation(CONTAINER_LIMITS, "ago(row.reported_ms)", '"never"', expect=REPORTED_AGO)
@mutation(CONTAINER_LIMITS, "if (!line.boot) return bootValue(row[line.key as (typeof LIMITS)[number][1]]);",
          "if (!line.boot) return String(row[line.key as (typeof LIMITS)[number][1]]);", expect=MATRIX)
@mutation(CONTAINER_LIMITS, '? "not set" : String(value);', '? NONE : String(value);', expect=MATRIX)
@mutation(CONTAINER_LIMITS, "if (value === undefined) return NONE;", "", expect=MATRIX)
@mutation(CONTAINER_LIMITS, '{ name: "Boot values", lines: lines.value.filter((l) => l.boot) },', "", expect=MATRIX)
@mutation(CONTAINER_LIMITS, "].filter((g) => g.lines.length),", "],", expect=NO_BOOT_BAND)
@mutation(CONTAINER_LIMITS, ":class=\"{ 'set-table__none': cell(row, line) === NONE }\"", "", expect=DIMMED)
@mutation(CONTAINER_LIMITS, '<h4 class="set-list__group">{{ g.name }}</h4>', "", expect=NOT_REPORTED_PHONE)
@mutation(CONTAINER_LIMITS, '<template v-if="own(row, g).length">', '<template v-if="true">',
          expect=NOT_REPORTED_PHONE)
@mutation(CONTAINER_LIMITS, 'v-if="row.stale" status="stale" label="stale" tone="warn" />\n              </div>',
          'v-if="true" status="stale" label="stale" tone="warn" />\n              </div>', expect=STALE_CHIP)
@mutation(CONTAINER_LIMITS, "if (!line.boot) return bootValue(", "if (!line.boot) return String(", expect=EXACT_WORDS)
@mutation(SETTINGS_VIEW, '  { id: "limits", label: "Fixed limits" },\n', "", expect=SIX_SECTIONS)
@mutation(SETTINGS_VIEW, '  { id: "password", label: "Change password" },\n  { id: "settings", label: "Settings" },\n',
          '  { id: "settings", label: "Settings" },\n  { id: "password", label: "Change password" },\n',
          expect=SIX_SECTIONS)
@mutation(SETTINGS_VIEW, '        <PasswordForm />\n\n', "", expect=CARD_ORDER)
@mutation(SETTINGS_VIEW, "fixed.value = data.fixed;", "fixed.value = [];", expect=FIXED_ROWS)
@mutation(SETTINGS_VIEW, "containers.value = data.containers;", "containers.value = [];", expect=CONTAINER_ROWS)
@mutation(SETTINGS_VIEW, 'note="A saved value is kept in the database. Reset returns to the default."',
          'note="A saved value is kept in the database and wins over the environment."', expect=NOTE)
@pytest.mark.web_unit
def test_settings_limits_rules():
    vitest("tests/unit/settings/limits.spec.ts")
