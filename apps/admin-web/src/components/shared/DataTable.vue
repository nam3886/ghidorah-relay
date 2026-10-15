<script setup lang="ts" generic="T extends object">
import { computed, ref } from "vue";
import type { Column } from "./types";
import type { Tone } from "./status-tones";

// One table for every list. Cells come from the column list (`row[key]`) or a `cell-<key>` slot; row actions go in
// the `actions` slot; the `empty-action` slot is the call to action of an empty list. States:
//   loading and no rows   -> skeleton
//   loading with rows     -> the old rows stay, dimmed (refreshing)
//   error                 -> an inline message with Retry (the stale rows are hidden: they would mislead)
//   no rows               -> the empty text and the action
const props = withDefaults(
  defineProps<{
    columns: Column[];
    rows: T[];
    loading?: boolean;
    error?: string | null;
    emptyText?: string;
    rowKey?: string;
    // a row that opens something: clicking it (not a button or a field inside it) emits `row-click`
    rowClickable?: boolean;
    // a left bar in the status colour for the rows that need a look (dead, failed, unknown)
    rowTone?: (row: T) => Tone | undefined;
    maxHeight?: string;
    skeletonRows?: number;
    // minimum width of the Actions column: Element Plus gives it about 110 px, which clips two or three buttons
    actionsWidth?: number | string;
    // a first column of checkboxes (the table's own selection column); `selection-change` carries the ticked rows
    selectable?: boolean;
    // new rows keep the ticks of the rows still there, matched by rowKey, with no report (Element Plus's
    // reserve-selection); without it new rows clear the ticks and report that. The ticks of rows that left stay in
    // the table's selection, unseen, until setSelection replaces them.
    keepSelection?: boolean;
  }>(),
  {
    loading: false,
    error: null,
    emptyText: "Nothing here yet",
    rowKey: "id",
    rowClickable: false,
    rowTone: undefined,
    maxHeight: undefined,
    skeletonRows: 6,
    actionsWidth: 200,
    selectable: false,
    keepSelection: false,
  },
);
const emit = defineEmits<{ retry: []; "row-click": [row: T]; "selection-change": [rows: T[]] }>();

const tableRef = ref<{ clearSelection: () => void; toggleRowSelection: (row: T, selected?: boolean) => void }>();

// Ticks exactly the rows whose rowKey is in `ids`. Each change is reported (selection-change) at once, inside this
// call, so the view's own list matches the ticks on screen before anything else can run.
function setSelection(ids: string[]): void {
  const table = tableRef.value;
  if (!table) return;
  table.clearSelection();
  const wanted = new Set(ids);
  for (const row of props.rows) {
    if (wanted.has(String((row as Record<string, unknown>)[props.rowKey]))) table.toggleRowSelection(row, true);
  }
}
defineExpose({ setSelection });

const refreshing = computed(() => props.loading && props.rows.length > 0 && !props.error);
const skeleton = computed(() => props.loading && props.rows.length === 0 && !props.error);
const state = computed(() =>
  props.error
    ? "error"
    : skeleton.value
      ? "loading"
      : refreshing.value
        ? "refreshing"
        : props.rows.length
          ? "ready"
          : "empty",
);

// Element Plus probes each column's slot once with an empty row and index -1; a real row has index 0 or more.
const isData = (index: number): boolean => index >= 0;

function rowClass({ row }: { row: T }): string {
  const tone = props.rowTone?.(row);
  return tone && tone !== "neutral" ? `row-tone row-tone-${tone}` : "";
}

function isEmpty(row: T, key: string): boolean {
  const value = (row as Record<string, unknown>)[key];
  return value === null || value === undefined || value === "";
}

function cellText(row: T, key: string): string {
  return isEmpty(row, key) ? "-" : String((row as Record<string, unknown>)[key]);
}

function onRowClick(row: T, _column: unknown, event: Event) {
  if (!props.rowClickable) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest("button, a, input, label, .el-switch, .el-checkbox, .el-dropdown")) return;
  emit("row-click", row);
}
</script>

<template>
  <div class="data-table" :class="{ 'is-refreshing': refreshing }" :aria-busy="props.loading" :data-state="state">
    <el-alert
      v-if="props.error"
      class="data-table__error"
      type="error"
      show-icon
      :closable="false"
      :title="props.error"
    >
      <el-button @click="emit('retry')">Retry</el-button>
    </el-alert>
    <div v-else-if="skeleton" class="data-table__skeleton">
      <el-skeleton :rows="props.skeletonRows" animated />
    </div>
    <el-table
      v-else
      ref="tableRef"
      :data="props.rows"
      :row-key="props.rowKey"
      :max-height="props.maxHeight"
      :row-class-name="rowClass"
      :class="{ 'is-clickable': props.rowClickable }"
      @row-click="onRowClick"
      @selection-change="(rows: T[]) => emit('selection-change', rows)"
    >
      <el-table-column v-if="props.selectable" type="selection" :width="44" :reserve-selection="props.keepSelection" />
      <el-table-column
        v-for="c in props.columns"
        :key="c.key"
        :label="c.label"
        :width="c.width"
        :min-width="c.minWidth"
        :align="c.align ?? 'left'"
        :class-name="c.align === 'right' ? 'is-numeric' : undefined"
      >
        <!-- Element Plus calls this slot once with an empty row and $index -1 to read the column's children: that
             probe is not data, so the user's slot never sees it. -->
        <template #default="{ row, $index }">
          <template v-if="isData($index)">
            <slot :name="`cell-${c.key}`" :row="row as T" :index="$index"
              ><span :class="{ 'data-table__none': isEmpty(row as T, c.key) }">{{
                cellText(row as T, c.key)
              }}</span></slot
            >
          </template>
        </template>
      </el-table-column>
      <el-table-column
        v-if="$slots.actions"
        label="Actions"
        align="right"
        class-name="data-table__actions"
        :min-width="props.actionsWidth"
      >
        <template #default="{ row, $index }">
          <template v-if="isData($index)">
            <slot name="actions" :row="row as T" />
          </template>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty :description="props.emptyText" :image-size="60">
          <slot name="empty-action" />
        </el-empty>
      </template>
    </el-table>
  </div>
</template>

<style scoped>
/* a wide table scrolls inside its own box, never the page */
.data-table {
  overflow-x: auto;
  /* the table's own minimum width must not widen a grid or flex parent: it scrolls here instead */
  contain: inline-size;
}
.data-table :deep(.el-table) {
  min-width: 640px;
}
.data-table__error :deep(.el-alert__content) {
  display: flex;
  align-items: center;
  gap: 12px;
}
.data-table__skeleton {
  padding: 16px;
}
.data-table__none {
  color: var(--rl-muted);
  opacity: 0.6;
}
.is-refreshing :deep(.el-table) {
  opacity: 0.55;
  transition: opacity 0.15s;
}
:deep(.el-table th.el-table__cell) {
  background: var(--rl-surface-2);
}
:deep(.el-table .is-numeric .cell) {
  font-variant-numeric: tabular-nums;
}
:deep(.el-table .el-table__row:hover > td.el-table__cell) {
  background: var(--rl-surface-2);
}
:deep(.el-table.is-clickable .el-table__row) {
  cursor: pointer;
}
:deep(.el-table .row-tone > td.el-table__cell:first-child) {
  box-shadow: inset 2px 0 0 var(--row-tone-color);
}
:deep(.el-table .row-tone-bad) {
  --row-tone-color: var(--rl-bad);
}
:deep(.el-table .row-tone-warn) {
  --row-tone-color: var(--rl-warn);
}
:deep(.el-table .row-tone-info) {
  --row-tone-color: var(--rl-info);
}
:deep(.el-table .row-tone-ok) {
  --row-tone-color: var(--rl-ok);
}
</style>
