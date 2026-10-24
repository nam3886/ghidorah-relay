"""Mutation markers for the Jobs selection: what a bulk button sends is always the ticks on screen. The view owns the
ticks and the table only reports a click, so a table drawn anew (after an error) shows them as they are; a load keeps
the ticks of the jobs still on the page and another page starts with none, a filter or a page size drops them at once,
none count while an error hides the table, and a Regenerate confirmed with nothing ticked sends nothing. Each mutant
must turn the Vitest spec red, with the exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py does the
mutating. A plain `check.sh` deselects these tests."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
VIEW = f"{SRC}/views/JobsView.vue"
VIEW_SPEC = "tests/unit/jobs/jobs-view.spec.ts"
FV = "FAILED " + VIEW_SPEC + " > what is sent is what is ticked on screen (desktop) > "

HIDDEN_BY_ERROR = "error.value ? [] : "
PRUNE = "picked.value = new Set(pickedIds(out.items, picked.value));"
PAGE_CLEAR = "if (at !== offset.value) clearPicked();"
SIZE_CLEAR = "  limit.value = size;\n  clearPicked();\n"
FILTER_CLEAR = "  offset.value = 0;\n  clearPicked();\n"
CLEAR_COPY = "picked.value = new Set();"
NOTHING_TICKED = "if (!ids.value.length) {"


@mutation(VIEW, HIDDEN_BY_ERROR, "",
          expect=FV + "an error hides the table: nothing can be sent until Retry draws the ticks again, "
                      "and then they are what is sent")
@mutation(VIEW, PRUNE, "picked.value = picked.value;",
          expect=FV + "a job that left the page while an error hid the table does not come back ticked")
@mutation(VIEW, PAGE_CLEAR, "",
          expect=FV + "a page change starts with nothing ticked, even a job that moved onto it; "
                      "a tick there is what is sent")
@mutation(VIEW, SIZE_CLEAR, "  limit.value = size;\n",
          expect=FV + "a page-size change unticks the rows at once, before the new page arrives")
@mutation(VIEW, FILTER_CLEAR, "  offset.value = 0;\n",
          expect=FV + "a filter change unticks the old rows at once, before the new list arrives")
@mutation(VIEW, CLEAR_COPY, "void 0;",
          expect=FV + "a filter change while an error hides the table: the new list starts with nothing ticked")
@mutation(VIEW, NOTHING_TICKED, "if (false) {",
          expect=FV + "a confirm answered after a failed reload hid the table sends nothing")
@pytest.mark.web_unit
def test_a_bulk_action_sends_exactly_the_ticks_on_screen():
    vitest(VIEW_SPEC)


# ---- the guards around the bulk request: the confirm, the lock against a second click, the body ------------------
CONTROL = f"{SRC}/components/jobs/RegenerateControl.vue"
API = f"{SRC}/api/jobs.ts"
API_SPEC = "tests/unit/jobs/jobs-api.spec.ts"
FB = "FAILED " + VIEW_SPEC + " > bulk regenerate > "
FA = "FAILED " + API_SPEC + " > bulk actions > "


@mutation(CONTROL, "if (!(await confirmDialog(props.confirm))) return;", "",
          expect=FB + "asks first, naming the count, today's price and the charge")
@mutation(CONTROL, "const { busy, run } = useBusy();",
          "const busy = ref(false);\nconst run = (a: () => unknown) => a();",
          expect=FB + "a second click while the confirm is open asks nothing more")
@mutation(API, '{ method: "POST", body: { job_ids: jobIds } }', '{ method: "POST", body: { job_ids: [] } }',
          expect=FA + "re-check posts the job ids")
@mutation(API, "body: { job_ids: jobIds, operation_key: operationKey },",
          "body: { job_ids: [], operation_key: operationKey },",
          expect=FA + "regenerate posts the ids with one key, kept under the action bulk:<sorted ids>")
@mutation(API, "body: { job_ids: jobIds, operation_key: operationKey },",
          "body: { job_ids: jobIds },",
          expect=FA + "regenerate posts the ids with one key, kept under the action bulk:<sorted ids>")
@pytest.mark.web_unit
def test_a_bulk_regenerate_asks_first_locks_against_a_second_click_and_sends_the_ticked_body():
    vitest(VIEW_SPEC, API_SPEC)
