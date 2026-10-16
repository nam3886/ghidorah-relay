<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { useRoute } from "vue-router";
import {
  bulkRecheck,
  bulkRegenerate,
  JOB_STATUSES,
  type JobSummary,
  type KeyOption,
  listJobs,
  listKeyOptions,
} from "@/api/jobs";
import { getHourlyStats, type HourBucket } from "@/api/shell";
import AttentionBanner from "@/components/layout/AttentionBanner.vue";
import PageHeader from "@/components/layout/PageHeader.vue";
import HourBand from "@/components/jobs/HourBand.vue";
import { HOUR_MS, hourRange } from "@/components/jobs/hour-band";
import { PAGE_SIZE, PAGE_SIZES } from "@/components/jobs/paging";
import { ALL, fromSelect, rememberedStatus, toSelect } from "@/components/jobs/job-filters";
import { jobChanges } from "@/components/jobs/job-changes";
import { clock, isRunning, netPoints } from "@/components/jobs/job-row";
import { keyName, progressStep, recheckToast, regenerateToast, shortId } from "@/components/jobs/job-text";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import { pickedIds } from "@/components/jobs/selection";
import DataTable from "@/components/shared/DataTable.vue";
import { IconChevronDown, IconClose, IconRefresh } from "@/components/shared/icons";
import ListPager from "@/components/shared/ListPager.vue";
import MoneyCell from "@/components/shared/MoneyCell.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Tone } from "@/components/shared/status-tones";
import type { Column } from "@/components/shared/types";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { groupByDay } from "@/lib/day-rows";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { bulkAction } from "@/lib/operation-key";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";
import { useUiStore } from "@/stores/ui";

// Newest first, 10 a page by offset with the server's total, grouped by day. The provider scope, the status filter
// (kept in memory), the key filter and the hour filter (a column of the 24-hour band) all restart the paging. A job
// opens in the shared detail dialog through ui.openJob. The list and the band read again every 10 s while the tab is
// shown.
const route = useRoute();
const scope = useScopeStore();
const ui = useUiStore();
const stats = useStatsStore();
const auth = useAuthStore();
const { isPhone, isTablet } = useBreakpoint();

const POLL_MS = 10_000;

// The select's caret, drawn as the canvas draws it (a 14 px chevron) instead of Element Plus's arrow.
const SelectChevron = () => h(IconChevronDown, { size: 14, stroke: 2 });

// Tablet: the Job cell also carries the account and the key, so those columns are left out; widths are the drawn ones.
const TABLET_COLUMNS = ["time", "job", "status", "model", "net"];
const columns = computed<Column[]>(() =>
  isTablet.value
    ? [
        { key: "time", label: "Time", width: 76 },
        { key: "job", label: "Job" },
        { key: "status", label: "Status", width: 112 },
        { key: "model", label: "Model", width: 138 },
        { key: "net", label: "Net points", width: 84, align: "right" },
      ]
    : [
        { key: "time", label: "Time", minWidth: 90 },
        { key: "job", label: "Job", minWidth: 280 },
        { key: "status", label: "Status", minWidth: 150 },
        { key: "model", label: "Model", minWidth: 220 },
        { key: "account", label: "Account", minWidth: 100 },
        { key: "key", label: "Key", minWidth: 110 },
        { key: "outrefs", label: "Out · Refs", minWidth: 100, align: "right" },
        { key: "net", label: "Net points", minWidth: 110, align: "right" },
      ],
);

const items = shallowRef<JobSummary[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);
const offset = ref(0);
const limit = ref(PAGE_SIZE);
const total = ref(0);
const latest = useLatest();

const status = rememberedStatus;
const keyId = ref("");
const keys = ref<KeyOption[]>([]);
const statusOptions = [ALL, ...JOB_STATUSES];
// The start (epoch ms, one UTC hour) of the band column that filters the list; null = no hour filter.
const hour = ref<number | null>(null);

// The ticked jobs: what the bulk buttons act on is always the ticks on screen.
// - The table keeps its ticks when its rows are replaced (keep-selection), so it reports only real changes: a click,
//   or setSelection below. Every report arrives at once, inside the click or the call, and `picked` takes it as is.
//   No report is stale, so none is ignored and there is no window to wait out.
// - `picked` outlives a table drawn anew (an error hid it, Retry drew it again): reTick ticks it there once more.
// - An error hides the table: nothing is ticked on screen then, so nothing is acted on.
// Always read through pickedIds: only what is on the page is acted on, in table order.
const picked = ref<Set<string>>(new Set());
const ids = computed(() => (error.value ? [] : pickedIds(items.value, picked.value)));
const table = ref<{ setSelection: (ids: string[]) => void } | null>(null);

const onSelection = (rows: JobSummary[]): void => {
  picked.value = new Set(rows.map((r) => r.id));
};
// Once the new rows are drawn, the table ticks exactly `picked`: it drops the unseen ticks of jobs that left the page
// and ticks again a table drawn anew. This runs right after the render, before any click can come in, provided it is
// called after the changes that render (nextTick waits only for a render already pending).
const reTick = (): Promise<void> => nextTick(() => table.value?.setSelection([...picked.value]));
const clearPicked = (): void => {
  picked.value = new Set();
  table.value?.setSelection([]);
};

// The stats cells link to /jobs?status=done|failed; a key id may come the same way.
function applyQuery(): void {
  const wanted = route.query.status;
  if (typeof wanted === "string" && (JOB_STATUSES as readonly string[]).includes(wanted)) status.value = wanted;
  if (typeof route.query.key_id === "string") keyId.value = route.query.key_id;
}

// Loads the page that starts at `at` (the one on screen by default, so a reload after a bulk action stays on it). Only
// the newest answer counts; a failed page move leaves the pager where it was. A list that shrank under the page on
// screen comes back empty, and the pager (el-pagination) then moves itself to its last page.
async function load(at: number = offset.value): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listJobs({
      status: status.value,
      provider: scope.provider,
      keyId: keyId.value,
      createdFromMs: hour.value ?? undefined,
      createdToMs: hour.value === null ? undefined : hour.value + HOUR_MS,
      offset: at,
      limit: limit.value,
    });
    if (!latest.isCurrent(ticket)) return;
    // Another page starts with nothing ticked, even a job that moved onto it; a reload of this page keeps the ticks
    // of the jobs still on it.
    if (at !== offset.value) clearPicked();
    items.value = out.items;
    total.value = out.total;
    offset.value = at;
    picked.value = new Set(pickedIds(out.items, picked.value));
    error.value = null;
    updatedAt.value = Date.now();
    // Last, after every change of this load: its nextTick then runs after the render they cause (the table an error
    // hid is drawn again by error = null), or at once when nothing needs drawing.
    void reTick();
  } catch (err) {
    if (latest.isCurrent(ticket)) error.value = errorText(err);
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}

// The 24 columns of the band follow the provider scope; a failed read keeps the columns on screen.
const hours = shallowRef<HourBucket[]>([]);
const hoursLatest = useLatest();
async function loadHours(): Promise<void> {
  const ticket = hoursLatest.next();
  try {
    const out = await getHourlyStats(scope.provider);
    if (hoursLatest.isCurrent(ticket)) hours.value = out.hours;
  } catch (err) {
    if (hoursLatest.isCurrent(ticket)) notify.error(errorText(err));
  }
}

// A new page size starts again from the first page.
function onSize(size: number): void {
  limit.value = size;
  clearPicked();
  void load(0);
}

applyQuery();
// A filter drops the ticks at once, on the table too: the old rows still on screen until the new list arrives show
// none.
watch([status, keyId, hour, () => scope.provider], () => {
  offset.value = 0;
  clearPicked();
  void load(0);
});
watch(
  () => scope.provider,
  () => void loadHours(),
);
watch(() => route.query, applyQuery);
watch(jobChanges, () => void load());

// Reads again while the tab is shown; a read still under way is not overtaken (it would draw an older page).
let timer: ReturnType<typeof setInterval> | undefined;
function refreshAll(): void {
  void load();
  void loadHours();
  void stats.refresh();
}
function poll(): void {
  if (document.hidden || loading.value) return;
  refreshAll();
}
const onVisible = (): void => {
  if (!document.hidden) poll();
};
const forgetSignOut = auth.onSignOut(() => {
  hour.value = null;
});

onMounted(() => {
  void load(0);
  void loadHours();
  listKeyOptions().then(
    (out) => (keys.value = out.items),
    (err) => notify.error(errorText(err)),
  );
  timer = setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", onVisible);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  document.removeEventListener("visibilitychange", onVisible);
  forgetSignOut();
});

const { busy: rechecking, run } = useBusy();
const recheck = () =>
  run(async () => {
    const sent = ids.value;
    if (!sent.length) {
      notify.error("Tick at least one job");
      return;
    }
    const out = await bulkRecheck(sent);
    clearPicked();
    await load();
    const toast = recheckToast(out);
    notify[toast.type](toast.text);
  });

// Runs once the confirm is accepted, on the ticks on screen then: while the confirm is open the table cannot be
// clicked, so only a reload can change them, and only by dropping jobs (one that left the page, or every tick when
// the reload failed). A failure leaves the selection as it was: the retry of the same selection sends the same key,
// and a different selection is a different action with its own key.
async function regenerate(): Promise<void> {
  if (!ids.value.length) {
    notify.error("Tick at least one job");
    return;
  }
  const out = await bulkRegenerate(ids.value);
  clearPicked();
  await load();
  const toast = regenerateToast(out);
  notify[toast.type](toast.text);
}

const regenerateConfirm = computed(() => ({
  title: "Regenerate",
  message: `Regenerate ${ids.value.length} job(s)? Each makes a NEW job at today’s price and charges its key.`,
  confirmText: "Regenerate",
  danger: true,
}));

// A failed job is tinted; an unknown one is not (the design draws only the failed fill).
const rowTone = (job: JobSummary): Tone | undefined => (job.status === "failed" ? "bad" : undefined);

// Day headers: the note is the jobs of that day on this page.
const grouped = computed(() => groupByDay(items.value, { at: (j) => j.created_ms, note: (n) => `${n} jobs shown` }));

const filtered = computed(() => !!(status.value || keyId.value || hour.value !== null));
function clearFilters(): void {
  status.value = "";
  keyId.value = "";
  hour.value = null;
}

const updatedText = computed(() =>
  updatedAt.value ? new Date(updatedAt.value).toLocaleTimeString("en-GB", { hour12: false }) : null,
);
const subtitle = computed(() =>
  isPhone.value ? "Every generation job across keys, by day." : "Every generation job across keys, grouped by day.",
);
</script>

<template>
  <section class="jobs" :class="{ 'jobs--phone': isPhone, 'jobs--tablet': isTablet }">
    <PageHeader title="Jobs" :subtitle="subtitle">
      <template #meta>
        <span v-if="updatedText">Updated {{ updatedText }} · auto every 10 s</span>
      </template>
      <template #actions>
        <el-button
          class="jobs__refresh"
          :loading="loading"
          :aria-label="isPhone ? `Refresh${updatedText ? `, updated ${updatedText}` : ''}` : undefined"
          @click="refreshAll"
        >
          <IconRefresh :size="isPhone ? 18 : 15" />
          <template v-if="!isPhone">Refresh</template>
        </el-button>
      </template>
    </PageHeader>

    <HourBand :hours="hours" :stats="stats.data" @pick="(ms: number) => (hour = ms)" />

    <AttentionBanner />

    <section class="card" aria-label="Job list">
      <div class="toolbar">
        <el-select
          :model-value="toSelect(status)"
          class="jobs__filter"
          :suffix-icon="SelectChevron"
          aria-label="Status"
          @update:model-value="(v: string) => (status = fromSelect(v))"
        >
          <template #prefix><span class="jobs__filter-label">Status</span></template>
          <el-option v-for="s in statusOptions" :key="s" :label="s === ALL ? 'All' : s" :value="s" />
        </el-select>
        <el-select
          :model-value="toSelect(keyId)"
          class="jobs__filter"
          :suffix-icon="SelectChevron"
          aria-label="Key"
          @update:model-value="(v: string) => (keyId = fromSelect(v))"
        >
          <template #prefix><span class="jobs__filter-label">Key</span></template>
          <el-option label="All" :value="ALL" />
          <el-option v-for="k in keys" :key="k.id" :label="keyName(k)" :value="k.id" />
        </el-select>
        <span v-if="hour !== null" class="chip" data-testid="hour-chip">
          {{ hourRange(hour) }}
          <button type="button" class="chip__clear" aria-label="Clear the hour filter" @click="hour = null">
            <IconClose :size="isPhone ? 14 : 12" :stroke="2.2" />
          </button>
        </span>
        <div
          v-show="!isPhone || ids.length"
          class="jobs__bulk"
          :role="isPhone ? 'region' : undefined"
          :aria-label="isPhone ? 'Bulk actions' : undefined"
        >
          <span v-show="ids.length" class="num jobs__count">{{ ids.length }} selected</span>
          <el-button :disabled="!ids.length || rechecking" :loading="rechecking" @click="recheck">Re-check</el-button>
          <RegenerateControl
            :action="bulkAction(ids)"
            label="Regenerate…"
            :disabled="!ids.length"
            :confirm="regenerateConfirm"
            :perform="regenerate"
          />
        </div>
      </div>

      <DataTable
        ref="table"
        class="jobs__table"
        :columns="columns"
        :tablet-columns="TABLET_COLUMNS"
        :rows="grouped.items"
        :loading="loading"
        :error="error"
        :empty-text="filtered ? 'No jobs match these filters.' : 'No jobs yet.'"
        row-clickable
        selectable
        keep-selection
        tone-fill
        :row-tone="rowTone"
        :row-label="(job: JobSummary) => `Open job ${shortId(job.id)}, ${job.title || '(untitled)'}`"
        :select-label="(job: JobSummary) => `Select job ${shortId(job.id)}`"
        @retry="load()"
        @row-click="(job) => ui.openJob(job.id)"
        @selection-change="onSelection"
      >
        <template #cell-time="{ row }">
          <span class="num mono jobs__time">{{ clock(row.created_ms) }}</span>
        </template>
        <template #cell-job="{ row }">
          <a class="title-link" href="#" :title="row.title ?? ''" @click.prevent="ui.openJob(row.id)">{{
            row.title || "(untitled)"
          }}</a>
          <span v-if="isTablet" class="mono jobs__id"
            >{{ shortId(row.id) }} · {{ row.account_label || "—" }} · {{ row.key_label || shortId(row.key_id) }}</span
          >
          <span v-else class="mono jobs__id">{{ shortId(row.id) }}</span>
        </template>
        <template #cell-status="{ row }">
          <StatusChip
            :status="row.status"
            :step="isRunning(row.status) ? `${progressStep(row.status)}/4` : undefined"
          />
          <span v-if="row.error_code" class="mono jobs__code">{{ row.error_code }}</span>
        </template>
        <template #cell-model="{ row }">
          <span class="jobs__kind">{{ row.media_type }}</span> {{ row.model }}
          <span v-if="row.model_substituted" class="jobs__differs">model differs</span>
        </template>
        <template #cell-account="{ row }">
          <span class="mono jobs__account">{{ row.account_label || "—" }}</span>
        </template>
        <template #cell-key="{ row }">{{ row.key_label || shortId(row.key_id) }}</template>
        <template #cell-outrefs="{ row }">
          <span class="num mono jobs__outrefs">{{ row.outputs }} · {{ row.inputs }}</span>
        </template>
        <template #cell-net="{ row }">
          <MoneyCell :price="row.price" :net="netPoints(row)" />
        </template>
        <template #row="{ row }">
          <div class="prow">
            <div class="prow__top">
              <span class="prow__title">{{ row.title || "(untitled)" }}</span>
              <StatusChip
                :status="row.status"
                :step="isRunning(row.status) ? `${progressStep(row.status)}/4` : undefined"
              />
            </div>
            <div class="mono prow__ids">{{ shortId(row.id) }} · {{ row.model }} · {{ row.account_label || "—" }}</div>
            <div class="prow__bottom">
              <span class="num mono prow__meta"
                >{{ clock(row.created_ms) }}<span class="prow__key"> · {{ row.key_label || shortId(row.key_id) }}</span
                ><span v-if="row.error_code" class="prow__code"> · {{ row.error_code }}</span></span
              >
              <MoneyCell :price="row.price" :net="netPoints(row)" />
            </div>
          </div>
        </template>
        <template v-if="filtered" #empty-action>
          <el-button @click="clearFilters">Clear filters</el-button>
        </template>
      </DataTable>

      <ListPager
        :offset="offset"
        :limit="limit"
        :total="total"
        :sizes="PAGE_SIZES"
        @move="(o: number) => load(o)"
        @size="onSize"
      />
    </section>
    <!-- the phone's bulk bar is fixed to the bottom of the screen; this keeps the last rows clear of it -->
    <div v-if="isPhone && ids.length" class="jobs__bar-space" aria-hidden="true" />
  </section>
</template>

<style scoped>
.jobs {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}
.jobs--phone {
  gap: 16px;
}
.card {
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  overflow: hidden;
}

/* header buttons: secondary, 36 high; icon-only 44 square on a phone */
.jobs__refresh {
  height: 36px;
  padding: 0 14px;
  gap: 6px;
  border-radius: 10px;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-size: 13px;
  font-weight: 400;
}
.jobs__refresh:hover,
.jobs__refresh:focus {
  background: var(--rl-raised);
  color: var(--rl-fg);
  border-color: var(--rl-line-strong);
}
.jobs__refresh > :deep(span) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.jobs--phone .jobs__refresh {
  width: 44px;
  height: 44px;
  padding: 0;
  flex: none;
}

/* toolbar */
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 14px 16px;
}
.jobs--phone .toolbar {
  flex-wrap: nowrap;
  overflow-x: auto;
  padding: 10px 16px;
  white-space: nowrap;
  border-bottom: 1px solid var(--rl-line);
}
.jobs__filter {
  width: auto;
  flex: none;
}
.jobs__filter :deep(.el-select__wrapper) {
  min-height: 34px;
  height: 34px;
  padding: 0 12px;
  gap: 6px;
  border-radius: 10px;
  background: var(--rl-surface);
  box-shadow: 0 0 0 1px var(--rl-line-strong) inset;
  font-size: 13px;
  line-height: 18px;
}
.jobs__filter :deep(.el-select__prefix) {
  margin-right: 0;
}
.jobs__filter :deep(.el-select__selection) {
  flex: none;
}
/* the chosen word takes its own width, so the control fits it: "Status All v" */
.jobs__filter :deep(.el-select__placeholder) {
  position: static;
  transform: none;
  width: auto;
  color: var(--rl-fg);
}
/* the readonly input stays in the accessibility tree (it is the combobox) but takes no room */
.jobs__filter :deep(.el-select__input) {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: 0;
  opacity: 0;
  pointer-events: none;
}
.jobs__filter :deep(.el-select__wrapper:has(input:focus-visible)) {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.jobs__filter :deep(.el-select__suffix) {
  margin-left: 0;
}
.jobs--phone .jobs__filter :deep(.el-select__wrapper) {
  min-height: 44px;
  height: 44px;
}
.jobs__filter-label {
  color: var(--rl-muted);
}

/* the hour filter chip */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 6px 0 10px;
  border-radius: 14px;
  background: var(--rl-primary-soft);
  color: var(--rl-fg);
  font-size: 12px;
  flex: none;
}
.chip__clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.jobs--phone .chip {
  gap: 4px;
  height: 44px;
  padding: 0 4px 0 12px;
  border-radius: 22px;
  font-size: 13px;
}
.jobs--phone .chip__clear {
  width: 44px;
  height: 44px;
  border-radius: 22px;
}

/* bulk actions: right of the filters; on a phone a bar fixed to the bottom of the screen */
.jobs__bulk {
  display: flex;
  flex: 1 1 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.jobs--tablet .jobs__count {
  margin-right: 4px;
}
.jobs__count {
  font-size: 13px;
  color: var(--rl-muted);
}
.jobs__bulk :deep(.el-button) {
  height: 34px;
  margin: 0;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-size: 13px;
  font-weight: 400;
}
.jobs__bulk :deep(.el-button:hover:not(.is-disabled)),
.jobs__bulk :deep(.el-button:focus:not(.is-disabled)) {
  background: var(--rl-raised);
  color: var(--rl-fg);
  border-color: var(--rl-line-strong);
}
.jobs__bulk :deep(.el-button.is-disabled) {
  background: var(--rl-raised);
  border-color: var(--rl-line-strong);
  color: var(--rl-faint);
}
/* Regenerate: a danger outline */
.jobs__bulk :deep(.regenerate .el-button) {
  border-color: var(--rl-bad);
  background: transparent;
  color: var(--rl-bad);
}
.jobs__bulk :deep(.regenerate .el-button:hover:not(.is-disabled)),
.jobs__bulk :deep(.regenerate .el-button:focus:not(.is-disabled)) {
  background: transparent;
  color: var(--rl-bad);
  border-color: var(--rl-bad);
}
.jobs__bulk :deep(.regenerate .el-button.is-disabled) {
  background: transparent;
  border-color: var(--rl-bad);
  color: var(--rl-bad);
  opacity: 0.45;
}
.jobs--phone .jobs__bulk {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 5;
  box-sizing: border-box;
  flex-wrap: nowrap;
  justify-content: flex-start;
  padding: 10px 16px;
  background: var(--rl-surface);
  border-top: 1px solid var(--rl-line-strong);
}
.jobs--phone .jobs__bulk .jobs__count {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--rl-fg);
  white-space: nowrap;
}
.jobs--phone .jobs__bulk :deep(.el-button) {
  height: 44px;
}
.jobs__bar-space {
  height: 64px;
}

/* the table */
.jobs__table :deep(.el-table) {
  --el-table-border-color: var(--rl-line);
  --el-table-bg-color: var(--rl-surface);
  --el-table-tr-bg-color: transparent;
  font-size: 13px;
}
.jobs:not(.jobs--tablet) .jobs__table :deep(.el-table) {
  min-width: 1040px;
}
.jobs__table :deep(.el-table th.el-table__cell) {
  height: 38px;
  padding: 0;
  border-bottom: 0;
}
.jobs__table :deep(.el-table td.el-table__cell) {
  height: 56px;
  padding: 0;
}
.jobs__table :deep(.el-table .cell) {
  padding: 8px 12px;
  line-height: 18px;
  word-break: normal;
}
.jobs__table :deep(.el-table th .cell) {
  padding: 0 12px;
}
.jobs__table :deep(.el-table .el-table-column--selection .cell) {
  padding: 0 0 0 16px;
}
.jobs__table :deep(.el-table td.is-numeric:last-child .cell),
.jobs__table :deep(.el-table th.is-numeric:last-child .cell) {
  padding: 8px 16px 8px 12px;
}
.jobs__table :deep(.el-table th.is-numeric:last-child .cell) {
  padding: 0 16px 0 12px;
}
.jobs--tablet .jobs__table :deep(.el-table .cell) {
  padding: 8px;
}
.jobs--tablet .jobs__table :deep(.el-table th .cell) {
  padding: 0 8px;
}
.jobs--tablet .jobs__table :deep(.el-table .el-table-column--selection .cell) {
  padding: 0 0 0 16px;
}
.jobs--tablet .jobs__table :deep(.el-table td.is-numeric:last-child .cell) {
  padding: 8px 16px 8px 8px;
}
.jobs--tablet .jobs__table :deep(.el-table th.is-numeric:last-child .cell) {
  padding: 0 16px 0 8px;
}
/* a day header runs across the whole table: its label starts at the left edge, over the selection column */
.jobs__table :deep(.el-table .is-day .cell) {
  padding: 14px 16px 6px;
  overflow: visible;
}
.jobs__table :deep(.el-table .is-day td.el-table__cell) {
  height: auto;
}
.jobs__table :deep(.el-table .is-day .data-table__day-label) {
  margin-left: -44px;
}
.title-link {
  display: block;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--rl-fg);
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
}
.title-link:hover {
  color: var(--rl-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.jobs--tablet .title-link {
  max-width: none;
}
.mono {
  font-family: var(--rl-mono);
}
.jobs__id {
  display: block;
  font-size: 12px;
  color: var(--rl-faint);
}
.jobs--tablet .jobs__id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jobs__time {
  font-size: 13px;
  color: var(--rl-muted);
  white-space: nowrap;
}
.jobs--tablet .jobs__time {
  font-size: 12px;
}
.jobs__code {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  color: var(--rl-bad);
}
.jobs--tablet .jobs__code {
  overflow: hidden;
  text-overflow: ellipsis;
}
.jobs__kind {
  color: var(--rl-muted);
}
.jobs__differs {
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-size: 11px;
}
.jobs--tablet .jobs__differs {
  display: inline-block;
  margin: 2px 0 0;
  padding: 0 6px;
  line-height: 16px;
}
.jobs__table :deep(.el-table td:has(.jobs__kind) .cell) {
  white-space: nowrap;
}
.jobs--tablet .jobs__table :deep(.el-table td:has(.jobs__kind) .cell) {
  padding: 6px 8px;
  white-space: normal;
  overflow-wrap: anywhere;
}
.jobs__account {
  font-size: 12px;
}
.jobs__outrefs {
  font-size: 12px;
  color: var(--rl-muted);
}

/* the phone's rows */
.prow {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.prow__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.prow__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
  color: var(--rl-fg);
}
.prow__top :deep(.status-chip) {
  flex: none;
}
.prow__ids {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--rl-faint);
}
.prow__bottom {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.prow__meta {
  font-size: 12px;
  color: var(--rl-muted);
  text-align: left;
}
.prow__key {
  font-family: var(--rl-font);
}
.prow__code {
  color: var(--rl-bad);
}
.prow__bottom :deep(.money) {
  flex: none;
}
</style>
