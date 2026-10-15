"""Mutation markers for the Jobs selection: what a bulk button sends is always the ticks on screen. The table keeps its
ticks when its rows are replaced (keep-selection), so every report it makes is a real change, made at once, and the
view takes each as is; the view ticks its copy again once new rows are drawn (rows that left drop out, a table drawn
anew after an error gets its ticks back), clears the ticks on the table too for another page or a filter, shows none
while an error hides the table, and a Regenerate confirmed with nothing ticked sends nothing. Each mutant must turn the
Vitest spec red, with the exact failed test; `webunit.vitest` runs the specs and scripts/mutate.py does the mutating.
A plain `check.sh` deselects these tests."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
VIEW = f"{SRC}/views/JobsView.vue"
TABLE = f"{SRC}/components/shared/DataTable.vue"
VIEW_SPEC = "tests/unit/jobs/jobs-view.spec.ts"
TABLE_SPEC = "tests/unit/shared/data-table-selection.spec.ts"
FV = "FAILED " + VIEW_SPEC + " > what is sent is what is ticked on screen > "
FS = "FAILED " + VIEW_SPEC + " > selection > "
FT = "FAILED " + TABLE_SPEC + " > DataTable selection > "

KEEP_ON_VIEW = "      keep-selection\n"
KEEP_ON_TABLE = ':reserve-selection="props.keepSelection"'
HIDDEN_BY_ERROR = "error.value ? [] : "
RE_TICK = "table.value?.setSelection([...picked.value])"
RE_TICK_CALL = "    void reTick();\n  } catch"
# the success path of load(), whose reTick comes last; the mutant calls it before the changes that redraw the table
LOAD_TAIL = ("    picked.value = new Set(pickedIds(out.items, picked.value));\n"
             "    error.value = null;\n"
             "    updatedAt.value = Date.now();\n"
             "    // Last, after every change of this load: its nextTick then runs after the render they cause "
             "(the table an error\n"
             "    // hid is drawn again by error = null), or at once when nothing needs drawing.\n"
             "    void reTick();\n")
RE_TICK_FIRST = ("    picked.value = new Set(pickedIds(out.items, picked.value));\n"
                 "    void reTick();\n"
                 "    error.value = null;\n"
                 "    updatedAt.value = Date.now();\n")
PRUNE = "picked.value = new Set(pickedIds(out.items, picked.value));"
PAGE_CLEAR = "if (at !== offset.value) clearPicked();"
SIZE_CLEAR = "  limit.value = size;\n  clearPicked();\n"
CLEAR_TABLE = "table.value?.setSelection([]);"
CLEAR_COPY = "picked.value = new Set();"
NOTHING_TICKED = "if (!ids.value.length) {"


@mutation(VIEW, KEEP_ON_VIEW, "",
          expect=FV + "a tick right after a reload counts at once: "
                      "Regenerate selected sends exactly the ticks on screen")
@mutation(TABLE, KEEP_ON_TABLE, ':reserve-selection="false"',
          expect=FT + "keepSelection: new rows keep the ticks of the rows still there, by rowKey, and report nothing")
@mutation(VIEW, HIDDEN_BY_ERROR, "",
          expect=FV + "an error hides the table: nothing can be sent until Retry draws the ticks again, "
                      "and then they are what is sent")
@mutation(VIEW, RE_TICK, "void picked.value",
          expect=FV + "an error hides the table: nothing can be sent until Retry draws the ticks again, "
                      "and then they are what is sent")
@mutation(VIEW, RE_TICK_CALL, "  } catch",
          expect=FV + "an error hides the table: nothing can be sent until Retry draws the ticks again, "
                      "and then they are what is sent")
@mutation(VIEW, LOAD_TAIL, RE_TICK_FIRST,
          expect=FS + "the ticks come back on the table after an error hid it and Retry drew it again")
@mutation(VIEW, PRUNE, "picked.value = picked.value;",
          expect=FV + "a job that left the page while an error hid the table does not come back ticked")
@mutation(VIEW, PAGE_CLEAR, "",
          expect=FV + "a page change starts with nothing ticked, even a job that moved onto it; "
                      "a tick there is what is sent")
@mutation(VIEW, SIZE_CLEAR, "  limit.value = size;\n",
          expect=FV + "a page-size change unticks the rows at once, before the new page arrives")
@mutation(VIEW, CLEAR_TABLE, "void 0;",
          expect=FV + "a filter change unticks the old rows at once, before the new list arrives")
@mutation(VIEW, CLEAR_COPY, "void 0;",
          expect=FV + "a filter change while an error hides the table: the new list starts with nothing ticked")
@mutation(VIEW, NOTHING_TICKED, "if (false) {",
          expect=FV + "a confirm answered after a failed reload hid the table sends nothing")
@pytest.mark.web_unit
def test_a_bulk_action_sends_exactly_the_ticks_on_screen():
    vitest(VIEW_SPEC, TABLE_SPEC)
