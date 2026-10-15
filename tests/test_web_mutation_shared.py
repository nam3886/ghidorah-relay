"""Mutation markers of the shared admin components (tone registry and clipboard): each breaks one rule in the TypeScript
source and demands that its Vitest spec turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
CLIPBOARD = "apps/admin-web/src/components/shared/clipboard.ts"
TONES = "apps/admin-web/src/components/shared/status-tones.ts"
TABLE = "apps/admin-web/src/components/shared/DataTable.vue"
CLIPBOARD_SPEC = "tests/unit/shared/clipboard.spec.ts"
TONES_SPEC = "tests/unit/shared/status-tones.spec.ts"


@mutation(CLIPBOARD, "window.isSecureContext && navigator.clipboard?.writeText", "navigator.clipboard?.writeText",
          expect="on plain http copies through a hidden textarea")
@mutation(CLIPBOARD, "await navigator.clipboard.writeText(text);", "void text;",
          expect="uses the clipboard API on a secure context and nothing else")
@mutation(CLIPBOARD, 'if (execCopy(text)) return "exec"', 'if (false) return "exec"',
          expect="on plain http copies through a hidden textarea")
@mutation(CLIPBOARD, "previous?.focus?.()", "void previous", expect="gives the focus back")
@mutation(CLIPBOARD, "  if (signal?.aborted) return \"aborted\";\n  if (execCopy(text))",
          "  if (execCopy(text))", expect="does not start the fallback")
@mutation(CLIPBOARD, 'area.value = "";', "", expect="scrubs the fallback textarea")
@mutation(CLIPBOARD, "window.getSelection()?.removeAllRanges();\n    area.remove();", "area.remove();",
          expect="scrubs the fallback textarea")
@pytest.mark.web_unit
def test_the_clipboard_paths_keep_their_order_and_their_cleanup():
    vitest(CLIPBOARD_SPEC)


@mutation("apps/admin-web/src/components/shared/CopyButton.vue", "abort.abort();", "", expect="leaves no textarea")
@pytest.mark.web_unit
def test_a_copy_pending_at_unmount_does_nothing():
    vitest("tests/unit/shared/small-components.spec.ts")


@mutation(TABLE, "props.loading && props.rows.length > 0 && !props.error",
          "props.loading && props.rows.length > 5 && !props.error", expect="refreshing keeps the old rows")
@mutation(TABLE, "index >= 0", "index >= -1",
          expect="never renders the user's slots for Element Plus's empty probe row")
@mutation(TABLE, "maxHeight: undefined,", 'maxHeight: "calc(100vh - 16rem)",',
          expect="has no inner scroll unless the caller sets a max height")
@mutation(TABLE, ':max-height="props.maxHeight"', ':max-height="undefined"',
          expect="has no inner scroll unless the caller sets a max height")
@mutation(TABLE, "return value === null || value === undefined || value === \"\";",
          "return value === undefined || value === \"\";", expect="draws an empty value as a quiet dash")
@mutation(TABLE, "actionsWidth: 200,", "actionsWidth: 100,", expect="gives the Actions column room")
@pytest.mark.web_unit
def test_the_table_keeps_old_rows_while_refreshing():
    vitest("tests/unit/shared/data-table.spec.ts")


PAGER = "apps/admin-web/src/components/shared/ListPager.vue"


@mutation(TABLE, 'v-if="props.selectable"', 'v-if="!props.selectable"',
          expect="has no checkbox column unless selectable")
@mutation(TABLE, "table.clearSelection();", "void table;",
          expect="setSelection ticks exactly the given ids and replaces an earlier selection")
@mutation(TABLE, "wanted.has(String((row as Record<string, unknown>)[props.rowKey]))", "true",
          expect="setSelection ticks exactly the given ids and replaces an earlier selection")
@pytest.mark.web_unit
def test_the_table_selection_column_and_setselection():
    vitest("tests/unit/shared/data-table-selection.spec.ts")


@mutation(PAGER, "emit(\"move\", (p - 1) * props.limit)", "emit(\"move\", p * props.limit)",
          expect="clicking a page emits move with that page's offset")
@mutation(PAGER, "Math.floor(props.offset / props.limit) + 1", "Math.floor(props.offset / props.limit)",
          expect="the active page follows the offset")
@mutation(PAGER, "if (resizing.value) return;", "", expect="a size change emits size and never a move")
@mutation(PAGER, "props.hasNext ? props.page + 1 : props.page", "props.page + 1",
          expect="Prev is disabled on page 1, Next when there is no next page")
@mutation(PAGER, '"total, sizes, prev, pager, next, jumper"', '"total, sizes, prev, pager, next"',
          expect="the full layout has the size select and the jumper")
@mutation(PAGER, "limit: 10, total: 0", "limit: 50, total: 0",
          expect="the full layout has the size select and the jumper")
@mutation(PAGER, "(max-width: 639px)", "(max-width: 100px)", expect="under 640 px the layout is compact")
@mutation(PAGER, "props.sizes.includes(props.limit)", "true",
          expect="a limit that is not in the list is added to the sizes")
@pytest.mark.web_unit
def test_the_pager_offsets_sizes_and_cursor_buttons():
    vitest("tests/unit/shared/list-pager.spec.ts")


@mutation("apps/admin-web/src/components/shared/OffsetPager.vue", "Math.max(0, props.offset - props.limit)",
          "props.offset - props.limit", expect="moves by one page and never below zero")
@mutation("apps/admin-web/src/components/shared/CursorPager.vue", "props.page <= 1", "props.page < 1",
          expect="shows the page, disables Prev")
@pytest.mark.web_unit
def test_the_pagers_keep_their_bounds():
    vitest("tests/unit/shared/small-components.spec.ts")


@mutation("apps/admin-web/src/components/shared/confirm-dialog.ts",
          'if (err === "cancel" || err === "close") return false', 'if (err === "cancel") return false',
          expect="resolves false on close")
@pytest.mark.web_unit
def test_a_confirm_closed_with_escape_is_a_no():
    vitest("tests/unit/shared/confirm-dialog.spec.ts")


@mutation(f"{SRC}/lib/format.ts", '.replace(", ", `,${NO_BREAK_SPACE}`)', '.replace(", ", ", ")',
          expect="never offers a line break")
@pytest.mark.web_unit
def test_a_time_never_breaks_across_two_lines():
    vitest("tests/unit/format.spec.ts")


@mutation(f"{SRC}/lib/stat-cells.ts", '"Failed pre-job (24 h)"', '"Failed before a job was created (24 h)"',
          expect="keeps every label short for one line")
@pytest.mark.web_unit
def test_a_strip_label_stays_short():
    vitest("tests/unit/stat-cells.spec.ts")


@mutation(TONES, "return code < 400 ?", "return code <= 400 ?", expect="maps an HTTP code by its class")
@mutation(TONES, ".trim().toLowerCase()", ".trim()", expect="ignores case and surrounding spaces")
@mutation(TONES, 'if (status === null || status === undefined) return "neutral";',
          'if (false) return "neutral";', expect="tolerates a missing status")
@mutation(TONES, 'if (status === null || status === undefined) return "-";', 'if (false) return "-";',
          expect="tolerates a missing status")
@mutation(TONES, '?? "neutral"', '?? "ok"', expect="falls back to neutral")
@pytest.mark.web_unit
def test_the_tone_lookup_keeps_its_rules():
    vitest(TONES_SPEC)


STATUS_CHIP = "apps/admin-web/src/components/shared/StatusChip.vue"


@mutation(STATUS_CHIP, 'bad: "danger"', 'bad: "warning"', expect="is an el-tag whose type follows the tone")
@mutation(STATUS_CHIP, 'warn: "warning"', 'warn: "danger"', expect="is an el-tag whose type follows the tone")
@mutation(STATUS_CHIP, 'ok: "success"', 'ok: "info"', expect="is an el-tag whose type follows the tone")
@pytest.mark.web_unit
def test_the_status_chip_tag_type_follows_the_tone():
    vitest("tests/unit/shared/small-components.spec.ts")
