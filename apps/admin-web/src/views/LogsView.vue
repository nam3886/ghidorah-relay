<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { useRoute } from "vue-router";
import { clearLogs, getLog, type LogDetail, listLogs, type LogRow } from "@/api/logs";
import PageHeader from "@/components/layout/PageHeader.vue";
import LogDetailDrawer from "@/components/logs/LogDetailDrawer.vue";
import LogsFilterForm from "@/components/logs/LogsFilterForm.vue";
import {
  clockTime,
  dayKey,
  dayNote,
  isLogStatus,
  isServerError,
  logQuery,
  PAGE_SIZE,
  thousands,
} from "@/components/logs/log-window";
import { useLogsFilters } from "@/components/logs/logs-filters";
import "@/components/logs/lane-controls.css";
import DataTable from "@/components/shared/DataTable.vue";
import { IconChevronRight, IconMoreCircles, IconTrashClear, IconTrashMenu } from "@/components/shared/icons";
import ListPager from "@/components/shared/ListPager.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Tone } from "@/components/shared/status-tones";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { type DayRow, groupByDay, isDayRow } from "@/lib/day-rows";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";

// Request logs: one row per request a known key made. The date window is the viewer's own midnights (half-open on the
// server). The filters are kept in memory across tab visits; the page offset starts at the first page on each visit.
const route = useRoute();
const scope = useScopeStore();
const ui = useUiStore();
const filters = useLogsFilters();
const { run } = useBusy();
const latest = useLatest();
const detailLatest = useLatest();
const { isPhone, isTablet } = useBreakpoint();

const items = shallowRef<LogRow[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = ref(PAGE_SIZE);
const loading = ref(false);
const error = ref<string | null>(null);

const detail = shallowRef<LogDetail | null>(null);
const detailOpen = ref(false);

// Only a server error fills its row; a client error is told by its amber code alone.
const rowTone = (row: LogRow): Tone | undefined => (isServerError(row.status_code) ? "bad" : undefined);

// The desktop columns; a tablet keeps five of them and folds the rest into those cells.
const columns = computed(() => {
  const tablet = isTablet.value;
  return [
    { key: "time", label: "Time", width: tablet ? 84 : 102 },
    { key: "status", label: "Status", width: tablet ? 92 : 96 },
    { key: "route", label: "Route", minWidth: tablet ? 120 : 200 },
    { key: "provider", label: "Provider", width: 88 },
    { key: "key", label: "Key", width: tablet ? 96 : 99 },
    { key: "job", label: "Job", width: 311 },
    { key: "error", label: "Error code", minWidth: 150 },
    { key: "duration", label: "Duration", align: "right" as const, width: tablet ? 96 : 100 },
  ];
});
const TABLET_COLUMNS = ["time", "status", "route", "key", "duration"];

// Day headers: the newest request's day first, then each earlier one. The note counts the rows on this page.
const dayTotals = shallowRef<Record<string, number>>({});
const rows = computed(() => {
  const grouped = groupByDay(items.value, { at: (r) => r.at_ms }).items;
  // each header counts its rows on this page against the day's total from the server
  let header: DayRow | null = null;
  let shown = 0;
  let key = "";
  const close = () => {
    if (header) header.note = dayNote(shown, dayTotals.value[key]);
  };
  for (const item of grouped) {
    if (isDayRow(item)) {
      close();
      header = item;
      shown = 0;
      key = "";
    } else {
      if (!key && item.at_ms) key = dayKey(item.at_ms);
      shown += 1;
    }
  }
  close();
  return grouped;
});

async function load(): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listLogs(logQuery(filters, scope.provider, offset.value, limit.value));
    if (!latest.isCurrent(ticket)) return;
    items.value = out.items;
    total.value = out.total;
    dayTotals.value = out.day_totals ?? {};
    error.value = null;
  } catch (err) {
    if (!latest.isCurrent(ticket)) return;
    error.value = errorText(err);
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}

// A stats cell links here with ?status=pre_job.
function applyRouteStatus(): void {
  const status = route.query.status;
  if (isLogStatus(status)) filters.status = status;
}
applyRouteStatus();
watch(
  () => route.query.status,
  () => {
    applyRouteStatus();
    search();
  },
);
void load();
watch(
  () => scope.provider,
  () => search(),
);

// The More menu of a tablet or phone: one item, Clear logs.
const menuOpen = ref(false);
const menuWrap = ref<HTMLElement | null>(null);
function onDocumentPointer(event: Event): void {
  if (menuOpen.value && !menuWrap.value?.contains(event.target as Node)) menuOpen.value = false;
}
function onDocumentKey(event: KeyboardEvent): void {
  if (event.key === "Escape") menuOpen.value = false;
}
onMounted(() => {
  document.addEventListener("pointerdown", onDocumentPointer);
  document.addEventListener("keydown", onDocumentKey);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocumentPointer);
  document.removeEventListener("keydown", onDocumentKey);
  latest.invalidate();
  detailLatest.invalidate();
});

// A new search starts at the first page; Refresh reads the same page again.
function search(): void {
  offset.value = 0;
  void load();
}

async function clear() {
  menuOpen.value = false;
  const ok = await confirmDialog({
    title: "Clear every request log row?",
    message: "Jobs, the ledger and alerts are kept.",
    confirmText: "Clear logs",
    danger: true,
  });
  if (!ok) return;
  await run(async () => {
    const out = await clearLogs();
    notify.success(`Cleared ${out.deleted} rows`);
    offset.value = 0;
    await load();
  });
}

async function openDetail(row: LogRow) {
  const ticket = detailLatest.next();
  await run(async () => {
    const log = await getLog(row.id);
    if (!detailLatest.isCurrent(ticket)) return;
    detail.value = log;
    detailOpen.value = true;
  });
}

// The job link of the drawer closes it, then the one shared job drawer opens (one drawer at a time).
function openJobFromDrawer(id: string): void {
  detailOpen.value = false;
  ui.openJob(id);
}

const errorTone = (row: LogRow): string => (isServerError(row.status_code) ? "is-bad" : "is-muted");
</script>

<template>
  <section class="logs-view">
    <PageHeader title="Request logs" subtitle="Every API request and its HTTP outcome. Jobs are listed separately.">
      <template #actions>
        <button v-if="!isPhone && !isTablet" type="button" class="kla-btn kla-btn--danger" @click="clear">
          <IconTrashClear :size="15" />Clear logs
        </button>
        <div v-else ref="menuWrap" class="logs-more">
          <button
            type="button"
            class="kla-btn logs-more__btn"
            :class="{ 'is-phone': isPhone }"
            aria-label="More actions"
            aria-haspopup="menu"
            :aria-expanded="menuOpen"
            @click="menuOpen = !menuOpen"
          >
            <IconMoreCircles :size="18" />
          </button>
          <div v-if="menuOpen" class="logs-more__menu" :class="{ 'is-phone': isPhone }" role="menu">
            <button type="button" role="menuitem" class="logs-more__item" @click="clear">
              <IconTrashMenu :size="16" />Clear logs
            </button>
          </div>
        </div>
      </template>
    </PageHeader>

    <section class="kla-card logs-card" aria-label="Request logs">
      <LogsFilterForm @search="search" />
      <DataTable
        class="logs-table"
        :actions-width="isTablet ? 40 : 48"
        :columns="columns"
        :rows="rows"
        :loading="loading"
        :error="error"
        empty-text="No log rows"
        row-clickable
        tone-fill
        :row-tone="rowTone"
        :tablet-columns="TABLET_COLUMNS"
        :row-label="(r: LogRow) => `Open request ${r.id}`"
        @row-click="openDetail"
        @retry="load"
      >
        <template #cell-time="{ row }"
          ><span class="l-time kla-mono">{{ clockTime(row.at_ms) }}</span></template
        >
        <template #cell-status="{ row }">
          <StatusChip :status="row.status_code" class="l-pill" />
          <span v-if="isTablet && row.error_code" class="l-code kla-mono" :class="errorTone(row)">{{
            row.error_code
          }}</span>
        </template>
        <template #cell-route="{ row }">
          <span class="l-route kla-mono">{{ row.route }}</span>
          <template v-if="isTablet">
            <button v-if="row.job_id" type="button" class="l-job-link" @click="ui.openJob(row.job_id)">
              {{ row.job_title || row.job_id.slice(0, 8) }}
            </button>
            <span v-else class="l-nojob">no job</span>
          </template>
        </template>
        <template #cell-provider="{ row }"
          ><span class="l-muted">{{ row.provider || "—" }}</span></template
        >
        <template #cell-key="{ row }">{{ row.key_label || "—" }}</template>
        <template #cell-job="{ row }">
          <div class="l-job-cell">
            <template v-if="row.job_id">
              <button type="button" class="l-job-link" @click="ui.openJob(row.job_id)">
                {{ row.job_title || row.job_id.slice(0, 8) }}
              </button>
              <span class="l-job-id kla-mono">{{ row.job_id.slice(0, 8) }}</span>
            </template>
            <span v-else class="l-nojob">no job</span>
          </div>
        </template>
        <!-- always an element: an empty slot would show the table's dash, and the design leaves the cell empty -->
        <template #cell-error="{ row }">
          <span v-if="row.error_code" class="l-err kla-mono" :class="errorTone(row)">{{ row.error_code }}</span>
          <span v-else class="l-err-none" />
        </template>
        <template #cell-duration="{ row }"
          ><span class="l-dur kla-mono">{{ thousands(row.duration_ms) }}{{ isTablet ? " ms" : "" }}</span></template
        >
        <template #actions="{ row }">
          <button
            type="button"
            class="kla-icon-btn l-open"
            :aria-label="`Open details of request ${row.id}`"
            @click="openDetail(row)"
          >
            <IconChevronRight :size="16" />
          </button>
        </template>

        <!-- the phone list: four short lines per request -->
        <template #row="{ row }">
          <div class="p-req">
            <div class="p-req__line">
              <StatusChip :status="row.status_code" class="l-pill" />
              <span class="p-req__route kla-mono">{{ row.route }}</span>
            </div>
            <div class="p-req__job">
              <template v-if="row.job_id">{{ row.job_title || row.job_id.slice(0, 8) }}</template>
              <span v-else class="l-nojob">no job</span>
            </div>
            <div class="p-req__meta">
              <span class="p-req__when"
                ><span class="kla-mono">{{ clockTime(row.at_ms) }}</span> · {{ row.key_label || "—" }}</span
              >
              <span class="p-req__dur kla-mono">{{ thousands(row.duration_ms) }} ms</span>
            </div>
            <div v-if="row.error_code" class="p-req__err kla-mono" :class="errorTone(row)">{{ row.error_code }}</div>
          </div>
        </template>
      </DataTable>
      <ListPager
        :offset="offset"
        :limit="limit"
        :total="total"
        @move="
          (o: number) => {
            offset = o;
            void load();
          }
        "
        @size="
          (s: number) => {
            limit = s;
            search();
          }
        "
      />
    </section>

    <LogDetailDrawer v-model="detailOpen" :log="detail" @open-job="openJobFromDrawer" />
  </section>
</template>

<style scoped>
.logs-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
@media (max-width: 1279px) {
  .logs-view {
    gap: 16px;
  }
}
.logs-card {
  min-width: 0;
}
.logs-table {
  border-top: 1px solid var(--rl-line);
}
.logs-table :deep(.el-table) {
  min-width: 1080px;
}
.logs-table.is-tablet :deep(.el-table) {
  min-width: 0;
}
.logs-table :deep(td.el-table__cell) {
  padding: 0;
}
.logs-table.is-tablet :deep(td.el-table__cell) {
  padding: 8px 0;
  vertical-align: top;
}
.logs-table :deep(td.el-table__cell:first-child .cell),
.logs-table :deep(th.el-table__cell:first-child .cell) {
  padding-left: 16px;
}
.logs-table :deep(.el-table__row > td.el-table__cell:first-child) {
  height: 48px;
}
.logs-table :deep(td.data-table__actions),
.logs-table :deep(th.data-table__actions) {
  width: 48px;
}
.logs-table :deep(th.data-table__actions .cell) {
  font-size: 0;
}
.logs-table :deep(td.data-table__actions .cell) {
  padding: 0 12px 0 0;
}
.l-time {
  font-size: 13px;
  color: var(--rl-muted);
  white-space: nowrap;
}
.is-tablet .l-time {
  font-size: 12px;
  line-height: 24px;
}
.l-route {
  font-size: 12px;
  white-space: nowrap;
}
.is-tablet .l-route {
  display: block;
  line-height: 24px;
}
.l-pill {
  font-variant-numeric: tabular-nums;
}
.l-muted {
  font-size: 13px;
  color: var(--rl-muted);
}
.l-job-cell {
  padding: 6px 0;
}
.is-tablet .l-job-cell {
  padding: 0;
}
.l-job-link {
  display: block;
  max-width: 280px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.is-tablet .l-job-link {
  font-size: 13px;
}
.l-job-link:hover {
  color: var(--rl-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.l-job-id {
  display: block;
  font-size: 12px;
  color: var(--rl-faint);
}
.l-nojob {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 8px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 11px;
  line-height: 16px;
}
.l-err {
  font-size: 12px;
  white-space: nowrap;
}
.l-code {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  line-height: 16px;
  overflow-wrap: anywhere;
}
.is-bad {
  color: var(--rl-bad);
}
.is-muted {
  color: var(--rl-muted);
}
.l-dur {
  font-size: 13px;
  white-space: nowrap;
}
.is-tablet .l-dur {
  font-size: 12px;
  line-height: 24px;
}
.l-open {
  color: var(--rl-muted);
}

/* the More menu (tablet, phone) */
.logs-more {
  position: relative;
  flex: none;
}
.logs-more__btn {
  width: 40px;
  height: 40px;
  padding: 0;
}
.logs-more__btn.is-phone {
  width: 44px;
  height: 44px;
}
.logs-more__menu {
  position: absolute;
  top: 46px;
  right: 0;
  z-index: 5;
  box-sizing: border-box;
  width: 200px;
  padding: 6px;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-surface);
  box-shadow: 0 12px 32px rgb(10 6 20 / 18%);
}
.logs-more__menu.is-phone {
  top: 50px;
}
.logs-more__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-bad);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}
.is-phone .logs-more__item {
  height: 44px;
}
.logs-more__item:hover {
  background: var(--rl-bad-bg);
}

/* phone */
.logs-card :deep(.data-table__main) {
  padding: 12px 16px !important;
}
.p-req {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.p-req__line {
  display: flex;
  align-items: center;
  gap: 10px;
}
.p-req__line :deep(.status-chip) {
  flex: none;
}
.p-req__route {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.p-req__job {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.p-req__meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.p-req__when {
  font-size: 13px;
  color: var(--rl-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.p-req__when .kla-mono {
  font-size: 12px;
}
.p-req__dur {
  flex: none;
  font-size: 13px;
}
.p-req__err {
  font-size: 12px;
}
</style>
