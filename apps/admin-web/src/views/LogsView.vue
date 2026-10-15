<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { useRoute } from "vue-router";
import { View } from "@element-plus/icons-vue";
import { clearLogs, getLog, type LogDetail, listLogs, type LogRow } from "@/api/logs";
import LogDetailDialog from "@/components/logs/LogDetailDialog.vue";
import { isLogStatus, logQuery, PAGE_SIZE } from "@/components/logs/log-window";
import { useLogsFilters } from "@/components/logs/logs-filters";
import DataTable from "@/components/shared/DataTable.vue";
import ListToolbar from "@/components/shared/ListToolbar.vue";
import IconAction from "@/components/shared/IconAction.vue";
import ListPager from "@/components/shared/ListPager.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { httpTone, type Tone } from "@/components/shared/status-tones";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { fmtTime } from "@/lib/format";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";

// Request logs: one row per request a known key made. The date window is the viewer's own midnights (half-open on the
// server). The filters are kept in memory across tab visits; the page offset starts at the first page on each visit.
const route = useRoute();
const scope = useScopeStore();
const filters = useLogsFilters();
const { run } = useBusy();
const latest = useLatest();
const detailLatest = useLatest();

const items = shallowRef<LogRow[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = ref(PAGE_SIZE);
const loading = ref(false);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);

const detail = shallowRef<LogDetail | null>(null);
const detailOpen = ref(false);

// The left bar follows the request's own outcome: a server error is red, a client error amber, a fine one none.
const rowTone = (row: LogRow): Tone | undefined => {
  const tone = httpTone(row.status_code);
  return tone === "ok" ? undefined : tone;
};

const columns = [
  { key: "time", label: "Time", width: 170 },
  { key: "status", label: "Status", width: 90 },
  { key: "route", label: "Route", minWidth: 180 },
  { key: "provider", label: "Provider", width: 110 },
  { key: "key", label: "Key", width: 130 },
  { key: "job", label: "Job", minWidth: 160 },
  { key: "error", label: "Error", minWidth: 130 },
  { key: "duration", label: "Duration", align: "right" as const, width: 110 },
];

async function load(): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listLogs(logQuery(filters, scope.provider, offset.value, limit.value));
    if (!latest.isCurrent(ticket)) return;
    items.value = out.items;
    total.value = out.total;
    error.value = null;
    updatedAt.value = Date.now();
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
onBeforeUnmount(() => {
  latest.invalidate();
  detailLatest.invalidate();
});

// Each day field may be set alone. The store keeps the day strings the half-open window is built from ("" = unset);
// a cleared picker reports null.
const from = computed<string | null>({
  get: () => filters.from || null,
  set: (value) => (filters.from = value ?? ""),
});
const to = computed<string | null>({
  get: () => filters.to || null,
  set: (value) => (filters.to = value ?? ""),
});

// A new search starts at the first page; Refresh reads the same page again.
function search(): void {
  offset.value = 0;
  void load();
}

async function clear() {
  const ok = await confirmDialog({
    title: "Clear logs",
    message: "Clear every request log row? Jobs, the ledger and alerts are kept.",
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
</script>

<template>
  <section class="logs-view">
    <ListToolbar :loading="loading" :updated-at="updatedAt" @refresh="load">
      <h2 class="view-title">Request logs</h2>
      <el-input
        v-model="filters.q"
        class="q-field"
        placeholder="Job id, reference or title"
        aria-label="Search"
        clearable
        @keyup.enter="search"
      />
      <el-select
        v-model="filters.status"
        class="status-field"
        aria-label="Status"
        title="The request’s own outcome. The Jobs tab filters by job status."
        @change="search"
      >
        <el-option label="All" value="all" />
        <el-option label="OK (below 400)" value="ok" />
        <el-option label="Errors" value="error" />
        <el-option label="Failed before a job" value="pre_job" />
      </el-select>
      <el-date-picker
        v-model="from"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="From"
        aria-label="From"
        class="date-field"
        @change="search"
      />
      <el-date-picker
        v-model="to"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="To"
        aria-label="To"
        class="date-field"
        @change="search"
      />
      <el-button @click="search">Search</el-button>
      <template #actions>
        <el-button class="danger" @click="clear">Clear logs</el-button>
      </template>
    </ListToolbar>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      :error="error"
      empty-text="No log rows"
      row-clickable
      :row-tone="rowTone"
      @row-click="openDetail"
      @retry="load"
    >
      <template #cell-time="{ row }">{{ fmtTime(row.at_ms) }}</template>
      <template #cell-status="{ row }"><StatusChip :status="row.status_code" /></template>
      <template #cell-route="{ row }"
        ><span class="mono">{{ row.route }}</span></template
      >
      <template #cell-provider="{ row }">{{ row.provider || "-" }}</template>
      <template #cell-key="{ row }">{{ row.key_label || "-" }}</template>
      <template #cell-job="{ row }">
        <template v-if="row.job_id">
          <span class="mono">{{ row.job_id.slice(0, 8) }}</span>
          <div class="sub title">{{ row.job_title || "" }}</div>
        </template>
        <StatusChip v-else status="no job" />
      </template>
      <template #cell-error="{ row }">
        <span v-if="row.error_code" class="mono sub">{{ row.error_code }}</span>
        <template v-else>-</template>
      </template>
      <template #cell-duration="{ row }">{{ row.duration_ms }} ms</template>
      <template #actions="{ row }">
        <IconAction :icon="View" label="Details" @click="openDetail(row)" />
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

    <LogDetailDialog v-model="detailOpen" :log="detail" />
  </section>
</template>

<style scoped>
.view-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.q-field {
  width: 224px;
}
.status-field {
  width: 180px;
}
.date-field {
  width: 150px;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
.sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.title {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.danger {
  color: var(--rl-bad);
}
</style>
