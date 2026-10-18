<script setup lang="ts" generic="T extends object">
import { computed, onBeforeUnmount, onMounted, ref, useSlots, watch } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { daySpanMethod, isDayRow, type DayRow } from "@/lib/day-rows";
import { IconAlertCircle } from "./icons";
import type { Column } from "./types";
import type { Tone } from "./status-tones";

// One table for every list. Cells come from the column list (`row[key]`) or a `cell-<key>` slot; row actions go in
// the `actions` slot; the `empty-action` slot is the call to action of an empty list. States:
//   loading and no rows   -> skeleton
//   loading with rows     -> the old rows stay, dimmed (refreshing)
//   error                 -> an inline message with Retry (the stale rows are hidden: they would mislead)
//   no rows               -> the empty text and the action
// Opt-in additions (a view that passes none of them gets the plain table):
//   tabletColumns  keys shown from 768 to 1279 px (the other fields fold into a cell the view draws)
//   row slot       the phone layout (< 768 px): a stacked row list instead of the table, with the same selection,
//                  row click and day headers; the table is not rendered then, and `actions` is not shown
//   toneFill       a row tone fills the whole row instead of the inset bar
//   spanMethod / rowClassName   passed to el-table
// Day rows (lib/day-rows.ts) may be mixed into `rows`: they draw a header across the table, are never selectable and
// never reach `selection-change`, `row-click` or a slot.
const props = withDefaults(
  defineProps<{
    columns: Column[];
    rows: Array<T | DayRow>;
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
    tabletColumns?: string[];
    toneFill?: boolean;
    spanMethod?: (args: {
      row: T | DayRow;
      column: unknown;
      rowIndex: number;
      columnIndex: number;
    }) => number[] | { rowspan: number; colspan: number } | undefined;
    rowClassName?: (args: { row: T; rowIndex: number }) => string;
    // phone layout: the accessible names of a row's open area and of its checkbox
    rowLabel?: (row: T) => string;
    selectLabel?: (row: T) => string;
    // phone layout: an extra class on the open area of every row (`.data-table__main`), so a view sets the padding
    // of its own row without an override of ours
    mainClass?: string;
    // the columns the loading skeleton draws, by key (the Jobs list draws the canvas's four: Time 84, Job, Status 110,
    // Net points 84); every visible column when left out
    skeletonColumns?: string[];
  }>(),
  {
    loading: false,
    error: null,
    emptyText: "Nothing here yet",
    rowKey: "id",
    rowClickable: false,
    rowTone: undefined,
    maxHeight: undefined,
    skeletonRows: 5,
    actionsWidth: 200,
    selectable: false,
    keepSelection: false,
    tabletColumns: undefined,
    toneFill: false,
    spanMethod: undefined,
    rowClassName: undefined,
    rowLabel: undefined,
    selectLabel: undefined,
    mainClass: undefined,
    skeletonColumns: undefined,
  },
);
// rows-drawn: the table's rows were (re)drawn, for a view that measures them (the Jobs columns); nothing else listens
const emit = defineEmits<{
  retry: [];
  "row-click": [row: T];
  "selection-change": [rows: T[]];
  "rows-drawn": [];
}>();
const slots = useSlots();
const { isPhone, isTablet } = useBreakpoint();

const phoneList = computed(() => isPhone.value && !!slots.row);
const visibleColumns = computed(() =>
  isTablet.value && props.tabletColumns
    ? props.columns.filter((c) => props.tabletColumns!.includes(c.key))
    : props.columns,
);
const skeletonCols = computed(() =>
  props.skeletonColumns
    ? visibleColumns.value.filter((c) => props.skeletonColumns!.includes(c.key))
    : visibleColumns.value,
);
// the bar a skeleton cell draws: the column's own, else by position (a time first, a title second, a net last, pills)
type Bar = NonNullable<Column["skeleton"]>;
function barOf(c: Column, ci: number): Bar {
  if (c.skeleton) return c.skeleton;
  return ci === 0 ? "time" : ci === 1 ? "title" : ci === skeletonCols.value.length - 1 ? "net" : "pill";
}
const columnCount = computed(() => (props.selectable ? 1 : 0) + visibleColumns.value.length + (slots.actions ? 1 : 0));
const hasDays = computed(() => props.rows.some(isDayRow));
const span = computed(
  () => props.spanMethod ?? (hasDays.value ? daySpanMethod(columnCount.value, props.selectable ? 1 : 0) : undefined),
);

const keyOf = (row: T | DayRow): string =>
  isDayRow(row) ? row.id : String((row as Record<string, unknown>)[props.rowKey]);
const dataRows = computed(() => props.rows.filter((r): r is T => !isDayRow(r)));

// the ticked rows' keys: the table's selection (or the phone list's own) mirrored here, so a row can be drawn selected
const selectedKeys = ref<ReadonlySet<string>>(new Set());
const tableRef = ref<{ clearSelection: () => void; toggleRowSelection: (row: T, selected?: boolean) => void }>();

function publish(rows: Array<T | DayRow>): void {
  // a day row can never be a selection, whatever the table says
  const picked = rows.filter((r): r is T => !isDayRow(r));
  selectedKeys.value = new Set(picked.map(keyOf));
  emit("selection-change", picked);
}

function onTableSelection(rows: Array<T | DayRow>): void {
  publish(rows);
}

// Ticks exactly the rows whose rowKey is in `ids`. Each change is reported (selection-change) at once, inside this
// call, so the view's own list matches the ticks on screen before anything else can run. Day rows are skipped.
function setSelection(ids: string[]): void {
  const wanted = new Set(ids);
  const hits = dataRows.value.filter((r) => wanted.has(keyOf(r)));
  if (phoneList.value) {
    publish(hits);
    return;
  }
  const table = tableRef.value;
  if (!table) return;
  table.clearSelection();
  for (const row of hits) table.toggleRowSelection(row, true);
}
defineExpose({ setSelection });

// Element Plus draws the table's checkboxes with no accessible name. As the canvas names them, the header box is
// "Select all on this page" and a row's box is `selectLabel(row)` ("Select job 5a0f3c88"). The rows are drawn in
// the order of `rows` (a day row has no box), and Element Plus redraws them on its own schedule, so the names are put
// on again whenever the table's body changes.
const root = ref<HTMLElement>();
function nameChecks(): void {
  const el = root.value;
  if (!el || !props.selectable) return;
  el.querySelector(".el-table__header .el-table-column--selection input")?.setAttribute(
    "aria-label",
    "Select all on this page",
  );
  el.querySelectorAll<HTMLTableRowElement>(".el-table__body tbody > tr").forEach((tr, i) => {
    const row = props.rows[i];
    const box = tr.querySelector("td.el-table-column--selection input");
    if (box && row && !isDayRow(row)) box.setAttribute("aria-label", props.selectLabel?.(row) ?? "Select row");
  });
}
// The canvas draws the select-all box empty or ticked, never mixed: ticked when every row on the page is, empty
// otherwise. Element Plus draws a part-ticked page as mixed (the box's indeterminate flag, an is-indeterminate class
// and aria-checked="mixed"), so that is taken off again each time Element Plus puts it on; those attribute changes
// are what is watched. An element other than the box that says "mixed" stops posing as a second checkbox too. A click
// does what Element Plus does: a part-ticked page is ticked whole, a whole one cleared.
const MIXED = ["role", "aria-checked", "tabindex"];
function flatSelectAll(): void {
  const check = root.value?.querySelector(".el-table__header .el-table-column--selection .el-checkbox");
  const box = check?.querySelector("input");
  if (!check || !box) return;
  if (box.indeterminate) box.indeterminate = false;
  for (const el of [check, ...check.querySelectorAll("*")]) {
    if (el.classList.contains("is-indeterminate")) el.classList.remove("is-indeterminate");
    if (el.getAttribute("aria-checked") !== "mixed") continue;
    for (const name of el === box ? ["aria-checked"] : MIXED) el.removeAttribute(name);
  }
}
let watcher: MutationObserver | undefined;
onMounted(() => {
  if (!root.value) return;
  watcher = new MutationObserver((records) => {
    if (records.some((r) => r.type === "childList")) {
      nameChecks();
      emit("rows-drawn");
    }
    flatSelectAll();
  });
  watcher.observe(root.value, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", ...MIXED],
  });
  nameChecks();
  flatSelectAll();
});
onBeforeUnmount(() => watcher?.disconnect());

// rows that left the list (a reload, another page) drop out of the phone list's selection, as the table does; with
// keepSelection the ticks stay and nothing is reported, as with the table's reserve-selection
watch(dataRows, (rows) => {
  if (!phoneList.value || props.keepSelection || selectedKeys.value.size === 0) return;
  const present = rows.filter((r) => selectedKeys.value.has(keyOf(r)));
  if (present.length !== selectedKeys.value.size) publish(present);
});
// the two layouts keep separate selections: switching between them starts clean
watch(phoneList, () => {
  if (selectedKeys.value.size) publish([]);
});

function toggleRow(row: T, on: boolean): void {
  const target = keyOf(row);
  publish(dataRows.value.filter((r) => (keyOf(r) === target ? on : selectedKeys.value.has(keyOf(r)))));
}

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

function toneClasses(row: T): string {
  const tone = props.rowTone?.(row);
  if (!tone || tone === "neutral") return "";
  return `row-tone row-tone-${tone}${props.toneFill ? " row-tone-fill" : ""}`;
}

function rowClasses(row: T, rowIndex: number): string {
  return [
    toneClasses(row),
    selectedKeys.value.has(keyOf(row)) ? "is-selected" : "",
    props.rowClassName?.({ row, rowIndex }) ?? "",
  ]
    .filter(Boolean)
    .join(" ");
}

function rowClass({ row, rowIndex }: { row: T | DayRow; rowIndex: number }): string {
  return isDayRow(row) ? "is-day" : rowClasses(row, rowIndex);
}

function isEmpty(row: T, key: string): boolean {
  const value = (row as Record<string, unknown>)[key];
  return value === null || value === undefined || value === "";
}

function cellText(row: T, key: string): string {
  return isEmpty(row, key) ? "-" : String((row as Record<string, unknown>)[key]);
}

const INTERACTIVE = "button, a, input, label, .el-switch, .el-checkbox, .el-dropdown";

function onRowClick(row: T | DayRow, _column: unknown, event: Event) {
  if (!props.rowClickable || isDayRow(row)) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest(INTERACTIVE)) return;
  emit("row-click", row);
}

function onListClick(row: T, event: MouseEvent) {
  if (!props.rowClickable) return;
  if ((event.target as HTMLElement | null)?.closest(INTERACTIVE)) return;
  emit("row-click", row);
}

function onListKey(row: T, event: KeyboardEvent) {
  if (event.target !== event.currentTarget) return;
  event.preventDefault();
  emit("row-click", row);
}
</script>

<template>
  <div
    ref="root"
    class="data-table"
    :class="{
      'is-refreshing': refreshing,
      'is-tablet': isTablet,
      'is-tone-fill': props.toneFill,
      'has-selection': props.selectable,
    }"
    :aria-busy="props.loading"
    :data-state="state"
  >
    <div v-if="props.error" class="data-table__error-wrap">
      <div class="data-table__error" role="alert">
        <IconAlertCircle class="data-table__error-icon" :size="18" />
        <span class="data-table__error-text">{{ props.error }}</span>
        <button type="button" class="data-table__retry" @click="emit('retry')">Retry</button>
      </div>
    </div>
    <table
      v-else-if="skeleton"
      class="data-table__skeleton"
      :class="{ 'is-drawn': props.skeletonColumns }"
      aria-hidden="true"
    >
      <thead>
        <tr>
          <th v-for="c in skeletonCols" :key="c.key" :class="{ 'is-right': c.align === 'right' }">{{ c.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in props.skeletonRows" :key="r">
          <td v-for="(c, ci) in skeletonCols" :key="c.key" :class="{ 'is-right': c.align === 'right' }">
            <i v-if="barOf(c, ci) === 'time'" class="skel skel--time" />
            <template v-else-if="barOf(c, ci) === 'title' || barOf(c, ci) === 'text'">
              <i :class="`skel skel--title-${(r - 1) % 5}`" />
              <i v-if="barOf(c, ci) === 'title'" class="skel skel--sub" />
            </template>
            <i v-else-if="barOf(c, ci) === 'net'" class="skel skel--net" />
            <i v-else :class="`skel skel--pill-${(r - 1) % 5}`" />
          </td>
        </tr>
      </tbody>
    </table>
    <template v-else-if="phoneList">
      <ul v-if="props.rows.length" class="data-table__list">
        <template v-for="(item, index) in props.rows" :key="keyOf(item)">
          <li v-if="isDayRow(item)" class="data-table__day">
            <div class="data-table__day-label" data-testid="day-row">
              <slot name="day" :row="item"
                >{{ item.label }} <span v-if="item.note" class="data-table__day-note">· {{ item.note }}</span></slot
              >
            </div>
          </li>
          <li v-else class="data-table__item" :class="rowClasses(item as T, index)">
            <label v-if="props.selectable" class="data-table__check">
              <input
                type="checkbox"
                :checked="selectedKeys.has(keyOf(item))"
                :aria-label="props.selectLabel?.(item as T) ?? 'Select row'"
                @change="toggleRow(item as T, ($event.target as HTMLInputElement).checked)"
              />
            </label>
            <div
              class="data-table__main"
              :class="props.mainClass"
              :role="props.rowClickable ? 'button' : undefined"
              :tabindex="props.rowClickable ? 0 : undefined"
              :aria-label="props.rowClickable ? props.rowLabel?.(item as T) : undefined"
              @click="onListClick(item as T, $event)"
              @keydown.enter="props.rowClickable && onListKey(item as T, $event)"
              @keydown.space="props.rowClickable && onListKey(item as T, $event)"
            >
              <slot name="row" :row="item as T" :index="index" />
            </div>
          </li>
        </template>
      </ul>
      <div v-else class="data-table__empty">
        <p class="data-table__empty-text">{{ props.emptyText }}</p>
        <slot name="empty-action" />
      </div>
    </template>
    <el-table
      v-else
      ref="tableRef"
      :data="props.rows"
      :row-key="keyOf"
      :max-height="props.maxHeight"
      :row-class-name="rowClass"
      :span-method="span"
      :class="{ 'is-clickable': props.rowClickable }"
      @row-click="onRowClick"
      @selection-change="onTableSelection"
    >
      <el-table-column
        v-if="props.selectable"
        type="selection"
        :width="isTablet ? 44 : 56"
        :reserve-selection="props.keepSelection"
        :selectable="(row: T | DayRow) => !isDayRow(row)"
      />
      <el-table-column
        v-for="(c, ci) in visibleColumns"
        :key="c.key"
        :label="c.label"
        :width="c.width"
        :min-width="c.minWidth"
        :align="c.align ?? 'left'"
        :class-name="
          [c.align === 'right' ? 'is-numeric' : '', c.className ?? ''].filter(Boolean).join(' ') || undefined
        "
      >
        <!-- Element Plus calls this slot once with an empty row and $index -1 to read the column's children: that
             probe is not data, so the user's slot never sees it. A day row draws its header in the first column. -->
        <template #default="{ row, $index }">
          <template v-if="isData($index)">
            <template v-if="isDayRow(row)">
              <div v-if="ci === 0" class="data-table__day-label" data-testid="day-row">
                <slot name="day" :row="row as DayRow"
                  >{{ (row as DayRow).label }}
                  <span v-if="(row as DayRow).note" class="data-table__day-note"
                    >· {{ (row as DayRow).note }}</span
                  ></slot
                >
              </div>
            </template>
            <slot v-else :name="`cell-${c.key}`" :row="row as T" :index="$index"
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
          <template v-if="isData($index) && !isDayRow(row)">
            <slot name="actions" :row="row as T" />
          </template>
        </template>
      </el-table-column>
      <template #empty>
        <div class="data-table__empty">
          <p class="data-table__empty-text">{{ props.emptyText }}</p>
          <slot name="empty-action" />
        </div>
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
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: var(--rl-raised);
}
/* a tablet shows its priority columns and fits the width: no minimum */
.data-table.is-tablet :deep(.el-table) {
  min-width: 0;
}
/* the card around the table draws the outer edge; the table has no lines of its own but the row dividers */
.data-table :deep(.el-table::before),
.data-table :deep(.el-table__inner-wrapper::before) {
  display: none;
}
:deep(.el-table .el-table__cell) {
  padding: 0;
}
:deep(.el-table th.el-table__cell) {
  height: 38px;
  border-bottom: 0;
  background: var(--rl-raised);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 500;
}
:deep(.el-table th.el-table__cell > .cell) {
  line-height: 20px;
}
:deep(.el-table td.el-table__cell) {
  height: 56px;
  border-bottom: 1px solid var(--rl-line);
}
:deep(.el-table .cell) {
  padding: 0 12px;
  line-height: 20px;
}
.is-tablet :deep(.el-table .cell) {
  padding: 0 8px;
}
:deep(.el-table .el-table__cell:first-child .cell) {
  padding-left: 16px;
}
:deep(.el-table .el-table__cell:last-child .cell) {
  padding-right: 16px;
}
/* the selection cell: the browser's 15 px box on the line of the text, 16 px in from the edge, as the canvas puts a
   bare input in the cell (14 px text in the body, 12 px in the header) */
:deep(.el-table .el-table-column--selection .cell) {
  display: block;
  height: auto;
  padding: 0 0 0 16px;
  line-height: 20px;
}
:deep(.el-table td.el-table-column--selection .cell) {
  font-size: 14px;
}
:deep(.el-table td.is-numeric .cell) {
  font-variant-numeric: tabular-nums;
}
:deep(.el-table .el-table__row:hover > td.el-table__cell) {
  background: var(--rl-raised);
}
:deep(.el-table.is-clickable .el-table__row) {
  cursor: pointer;
}
/* tone: an inset bar on the first cell, or (toneFill) the whole row. Only a failed row is tinted in the canvas: the
   fill of every other tone is empty, an unknown row included. */
:deep(.el-table .row-tone > td.el-table__cell:first-child) {
  box-shadow: inset 2px 0 0 var(--row-tone-color);
}
.is-tone-fill :deep(.el-table .row-tone > td.el-table__cell:first-child) {
  box-shadow: none;
}
:deep(.el-table .row-tone-bad),
.data-table__item.row-tone-bad {
  --row-tone-color: var(--rl-bad);
  --row-tone-fill: var(--rl-bad-bg);
}
:deep(.el-table .row-tone-warn),
.data-table__item.row-tone-warn {
  --row-tone-color: var(--rl-warn);
  --row-tone-fill: transparent;
}
:deep(.el-table .row-tone-info),
.data-table__item.row-tone-info {
  --row-tone-color: var(--rl-info);
  --row-tone-fill: transparent;
}
:deep(.el-table .row-tone-ok),
.data-table__item.row-tone-ok {
  --row-tone-color: var(--rl-ok);
  --row-tone-fill: transparent;
}
:deep(.el-table .is-selected > td.el-table__cell) {
  background: var(--rl-primary-soft);
}
.data-table__item.is-selected {
  background: var(--rl-primary-soft);
}
.data-table__item.row-tone-fill {
  background: var(--row-tone-fill);
}
.data-table__item.row-tone-fill.is-selected {
  background: var(--rl-primary-soft);
}
:deep(.el-table .row-tone-fill > td.el-table__cell) {
  background: var(--row-tone-fill);
}
:deep(.el-table .row-tone-fill.is-selected > td.el-table__cell) {
  background: var(--rl-primary-soft);
}
:deep(.el-table .row-tone-fill:hover > td.el-table__cell) {
  background: var(--rl-raised);
}
/* day header rows: one cell across the whole table (daySpanMethod), the label 16 px in, as the canvas's
   `th colspan` with `padding 14px 16px 6px`. `tr` keeps these rules above a view's own cell padding */
:deep(.el-table tr.is-day > td.el-table__cell),
:deep(.el-table tr.is-day:hover > td.el-table__cell) {
  height: auto;
  padding: 0;
  background: transparent;
  cursor: default;
}
:deep(.el-table tr.is-day > td.el-table__cell > .cell) {
  padding: 14px 16px 6px;
  line-height: 20px;
}
.data-table__day-label {
  font-size: 12px;
  line-height: 20px;
  font-weight: 600;
  color: var(--rl-muted);
  white-space: nowrap;
}
.data-table__day-note {
  font-weight: 400;
  color: var(--rl-faint);
  font-variant-numeric: tabular-nums;
}
.data-table__none {
  color: var(--rl-muted);
  opacity: 0.6;
}
/* refreshing: the rows dim, the header stays at full strength */
.is-refreshing :deep(.el-table__body-wrapper),
.is-refreshing .data-table__list {
  opacity: 0.6;
  transition: opacity 0.15s;
}
/* states: the empty list is the canvas's block (no table header over it, a hairline above it) */
.data-table[data-state="empty"] :deep(.el-table__header-wrapper) {
  display: none;
}
:deep(.el-table__empty-block) {
  display: block;
  min-height: 0;
  border-top: 1px solid var(--rl-line);
}
:deep(.el-table__empty-text) {
  width: auto;
  line-height: inherit;
  color: inherit;
}
.data-table__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 44px 16px 52px;
  text-align: center;
}
.data-table__empty-text {
  margin: 0;
  font-size: 14px;
  color: var(--rl-muted);
}
.data-table__error-wrap {
  padding: 0 16px 16px;
}
.data-table__error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
}
.data-table__error-icon {
  flex: none;
  color: var(--rl-bad);
}
.data-table__error-text {
  flex: 1;
  min-width: 220px;
  color: var(--rl-fg);
}
/* Retry: the danger outline, 32 high, 13/500, raised on hover (B-States, error) */
.data-table__retry {
  box-sizing: border-box;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--rl-bad);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-bad);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  font-weight: 500;
  cursor: pointer;
}
.data-table__retry:hover {
  background: var(--rl-raised);
}
/* the skeleton: a table of the same columns, 5 rows of 56 px, flat bars (the canvas draws no shimmer) */
.data-table__skeleton {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
}
.data-table__skeleton th {
  height: 38px;
  padding: 0 12px;
  background: var(--rl-raised);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
}
.data-table__skeleton th:first-child,
.data-table__skeleton td:first-child {
  width: 84px;
  padding-left: 16px;
}
.data-table__skeleton th:last-child,
.data-table__skeleton td:last-child {
  width: 84px;
  padding-right: 16px;
}
.data-table__skeleton th.is-right {
  text-align: right;
}
.data-table__skeleton.is-drawn th:nth-child(3) {
  width: 110px;
}
.data-table__skeleton td {
  height: 56px;
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-line);
}
.skel {
  display: block;
  background: var(--rl-raised);
}
.skel--time {
  width: 52px;
  height: 12px;
  border-radius: 6px;
}
.skel--title-0,
.skel--title-1,
.skel--title-2,
.skel--title-3,
.skel--title-4 {
  height: 12px;
  border-radius: 6px;
}
.skel--title-0 {
  width: 62%;
}
.skel--title-1 {
  width: 48%;
}
.skel--title-2 {
  width: 71%;
}
.skel--title-3 {
  width: 39%;
}
.skel--title-4 {
  width: 56%;
}
.skel--sub {
  width: 64px;
  height: 10px;
  margin-top: 8px;
  border-radius: 5px;
}
.skel--pill-0,
.skel--pill-1,
.skel--pill-2,
.skel--pill-3,
.skel--pill-4 {
  height: 22px;
  border-radius: 11px;
}
.skel--pill-0 {
  width: 72px;
}
.skel--pill-1 {
  width: 84px;
}
.skel--pill-2 {
  width: 64px;
}
.skel--pill-3 {
  width: 78px;
}
.skel--pill-4 {
  width: 70px;
}
.skel--net {
  width: 22px;
  height: 12px;
  margin-left: auto;
  border-radius: 6px;
}
/* phone list: stacked rows, whole row tappable, 44 px targets */
.data-table__list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.data-table__day {
  padding: 14px 16px 6px;
  border-bottom: 1px solid var(--rl-line);
}
.data-table__item {
  display: flex;
  align-items: stretch;
  min-height: 72px;
  border-bottom: 1px solid var(--rl-line);
}
.data-table__check {
  flex: none;
  width: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.data-table__check input {
  width: 20px;
  height: 20px;
  margin: 0;
  accent-color: var(--rl-primary);
}
.data-table__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  padding: 10px 16px 10px 0;
}
.data-table__main[role="button"] {
  cursor: pointer;
}
.data-table__item:not(:has(.data-table__check)) .data-table__main {
  padding-left: 16px;
}
</style>
