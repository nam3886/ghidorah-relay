<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { clearLogs, getLog, type LogDetail, listLogs, type LogRow } from "@/api/logs";
import { loadFailedText } from "@/components/jobs/job-text";
import LogDetailDrawer from "@/components/logs/LogDetailDrawer.vue";
import LogsDatesField from "@/components/logs/LogsDatesField.vue";
import LogsFilterForm from "@/components/logs/LogsFilterForm.vue";
import LogsStats from "@/components/logs/LogsStats.vue";
import LogsTable from "@/components/logs/LogsTable.vue";
import { isLogStatus, logQuery, PAGE_SIZE, thousands } from "@/components/logs/log-window";
import { useLogsFilters } from "@/components/logs/logs-filters";
import IconButton from "@/components/shared/IconButton.vue";
import BottomSheet from "@/components/shared/overlays/BottomSheet.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";

// Request logs: one row per request a known key made. The date window is the viewer's own midnights (half-open on the
// server). The filters are kept in memory across tab visits; the page offset starts at the first page on each visit.
// A desktop and a tablet page through the rows (Newer and Older); a phone loads older rows under the ones it has.
const route = useRoute();
const router = useRouter();
const scope = useScopeStore();
const ui = useUiStore();
const filters = useLogsFilters();
const { run } = useBusy();
const latest = useLatest();
const detailLatest = useLatest();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);

const items = shallowRef<LogRow[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = ref(PAGE_SIZE);
const loading = ref(false);
const error = ref<string | null>(null);

const detail = shallowRef<LogDetail | null>(null);
const detailOpen = ref(false);

// `more`: the next page is added under the rows on hand (a phone's Load older), else it replaces them
async function load(more = false): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listLogs(logQuery(filters, scope.provider, offset.value, limit.value));
    if (!latest.isCurrent(ticket)) return;
    items.value = more ? [...items.value, ...out.items] : out.items;
    total.value = out.total;
    error.value = null;
  } catch (err) {
    if (!latest.isCurrent(ticket)) return;
    error.value = loadFailedText(err, errorText(err), "the request logs");
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}

// A stats cell links here with ?status=pre_job.
function applyRouteStatus(): void {
  const status = route.query.status;
  if (isLogStatus(status)) filters.status = status;
}
filters.keepRangeWhole();
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
onBeforeUnmount(() => {
  latest.invalidate();
  detailLatest.invalidate();
});

// A new search starts at the first page.
function search(): void {
  offset.value = 0;
  void load();
}

// An empty list under a filter offers to clear it, as the Jobs list does (LogsStates, no match).
const filtered = computed(() => !!(filters.q.trim() || filters.status !== "all" || filters.from || filters.to));
function clearFilters(): void {
  filters.reset();
  search();
}

async function clear() {
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

// Paging: Showing a–b of N, Newer and Older (a desktop and a tablet); a phone's Load older adds the next page.
const first = computed(() => (items.value.length ? offset.value + 1 : 0));
const last = computed(() => offset.value + items.value.length);
const newer = () => {
  offset.value = Math.max(0, offset.value - limit.value);
  void load();
};
const older = () => {
  offset.value += limit.value;
  void load();
};
const more = () => {
  offset.value += limit.value;
  void load(true);
};

// The day numbers of the design: off the page until the API serves them (LogsStats says so when it is shown).
const statsServed = false;

// A tablet or phone folds the search and the dates behind two buttons of the head.
const searchOpen = ref(false);
// A phone's actions go to the top bar when the shell drew its target (the view alone, in a spec, keeps them here).
const barThere = ref(false);
onMounted(() => (barThere.value = !!document.getElementById("topbar-actions")));
const inBar = computed(() => isPhone.value && barThere.value);
const filtersOpen = ref(false);
const showSearch = computed(() => searchOpen.value || !!filters.q.trim());
async function toggleSearch(): Promise<void> {
  searchOpen.value = !searchOpen.value;
  if (!searchOpen.value) return;
  await nextTick();
  document.getElementById("logs-q")?.focus();
}
</script>

<template>
  <section class="logs-view">
    <div class="lv-head" :class="{ 'is-compact': compact, 'is-bar': inBar }">
      <div v-if="!compact" class="lv-head__text">
        <h1>Request logs</h1>
        <p>Every call to the relay API, newest first.</p>
      </div>
      <h1 v-else class="sr-only">Request logs</h1>
      <!-- a phone draws its actions in the top bar (LogsPhone), the shell's teleport target -->
      <Teleport to="#topbar-actions" :disabled="!inBar" defer>
        <span class="lv-head__actions">
          <!-- a tablet draws the two as text buttons at the head of the page (LogsTablet), a phone as icons -->
          <template v-if="isTablet">
            <button
              type="button"
              class="lv-text-btn"
              aria-label="Search requests"
              :aria-expanded="showSearch"
              @click="toggleSearch"
            >
              Search requests
            </button>
            <button
              type="button"
              class="lv-text-btn"
              aria-label="Filters: dates"
              aria-haspopup="dialog"
              @click="filtersOpen = true"
            >
              Filters: dates
            </button>
          </template>
          <IconButton v-if="isPhone" icon="search" label="Search requests" placement="bottom" @click="toggleSearch" />
          <IconButton
            v-if="isPhone"
            icon="filter"
            label="Filters: dates"
            placement="bottom"
            @click="filtersOpen = true"
          />
          <IconButton icon="delete" label="Clear logs" danger placement="bottom-end" @click="clear" />
        </span>
      </Teleport>
    </div>

    <LogsStats v-if="statsServed" />

    <section class="lv-card" aria-label="Request log list">
      <LogsFilterForm :search-open="showSearch" @search="search" />
      <LogsTable
        :rows="items"
        :loading="loading"
        :error="error"
        :filtered="filtered"
        @open="openDetail"
        @job="ui.openJob"
        @retry="() => load()"
        @clear-filters="clearFilters"
        @send-test="router.push({ name: 'test' })"
      />
      <div v-if="!isPhone && !error && total > 0" class="lv-foot">
        <span class="lv-foot__text"
          >Showing {{ thousands(first) }}–{{ thousands(last) }} of {{ thousands(total) }}</span
        >
        <span class="lv-foot__btns">
          <IconButton icon="prev" label="Newer page" :disabled="offset === 0 || loading" @click="newer" />
          <IconButton icon="next" label="Older page" :disabled="last >= total || loading" @click="older" />
        </span>
      </div>
    </section>

    <button
      v-if="isPhone && !error && items.length > 0 && items.length < total"
      type="button"
      class="lv-more"
      :disabled="loading"
      @click="more"
    >
      Load older
    </button>

    <BottomSheet v-model="filtersOpen" title="Filters" sub="Dates" testid="logs-filters-sheet">
      <LogsDatesField label="Dates" @search="search" />
      <template #footer>
        <button type="button" class="lv-done" @click="filtersOpen = false">Done</button>
      </template>
    </BottomSheet>

    <LogDetailDrawer v-model="detailOpen" :log="detail" @open-job="openJobFromDrawer" />
  </section>
</template>

<style scoped>
.logs-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
@media (max-width: 767px) {
  .logs-view {
    gap: 14px;
  }
}

/* the head: the title and one muted sentence, the icon buttons at the right */
.lv-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}
.lv-head__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.lv-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.lv-head p {
  margin: 0;
  color: var(--rl-muted);
}
.lv-head__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.lv-head.is-compact {
  justify-content: flex-end;
  gap: 0;
}
/* the actions moved to the top bar: the head keeps only the hidden heading and takes no row */
.lv-head.is-bar {
  display: contents;
}
.lv-head__actions {
  align-items: center;
}
/* the tablet's secondary text buttons: 40 high, padding 0 16, radius 10, 700 */
.lv-text-btn {
  box-sizing: border-box;
  height: 40px;
  padding: 0 16px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.lv-text-btn:hover {
  background: var(--rl-raised);
}
.lv-text-btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

.lv-card {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.lv-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  border-top: 1px solid var(--rl-rule);
}
.lv-foot__text {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
  font-variant-numeric: tabular-nums;
}
.lv-foot__btns {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

/* the phone's Load older, and the sheet's Done: full-width text buttons at the touch height */
.lv-more,
.lv-done {
  box-sizing: border-box;
  width: 100%;
  height: 44px;
  border-radius: 10px;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.lv-more {
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
}
.lv-more:disabled {
  opacity: 0.5;
  cursor: default;
}
.lv-done {
  border: 0;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
</style>
