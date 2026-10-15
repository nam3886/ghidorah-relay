<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import { acknowledgeGroup, type AlertDetail, type AlertGroup, getAlertGroup, listAlertGroups } from "@/api/alerts";
import { getStats, type LiveAlert } from "@/api/shell";
import AlertDetailPanel from "@/components/alerts/AlertDetailPanel.vue";
import {
  ackToast,
  type AlertAsk,
  ALERT_KINDS,
  GROUP_LIMIT,
  groupTitle,
  oneCustomer,
  ROW_LIMIT,
} from "@/components/alerts/alerts-model";
import { ALL, fromSelect, toSelect } from "@/components/jobs/job-filters";
import DataTable from "@/components/shared/DataTable.vue";
import ListToolbar from "@/components/shared/ListToolbar.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Tone } from "@/components/shared/status-tones";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { fmtTime } from "@/lib/format";
import { notify } from "@/lib/notify";
import { useStatsStore } from "@/stores/stats";

// Alerts: every stored alert kind grouped by the hub API, with an acknowledge per group, and beside them the live
// conditions the dashboard computes (no acknowledge: they clear themselves). The groups ignore the provider filter.
// Titles and reply texts hold customer-influenced words, so every one reaches the page as text.
//
// `asked` is what the operator asked for last (the filters and the group whose detail is open); what is drawn (`groups`,
// `live`, `opened`) comes from one answer. A write takes its group from what was drawn, never from `asked`. Every
// load takes the next ticket, and an answer or a failure counts only if no later load started since it was asked.
const stats = useStatsStore();
const { run } = useBusy();
const latest = useLatest();

const asked = ref<AlertAsk>({ status: "open", kind: "", open: null });
const groups = shallowRef<AlertGroup[]>([]);
const live = shallowRef<LiveAlert[]>([]);
const opened = shallowRef<AlertDetail | null>(null);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);
const loading = ref(0);
const writing = ref(false);
// While a view loads, only the controls that ask for another view stay usable: they are how it is replaced. While a
// write is in flight nothing is.
const locked = computed(() => writing.value || loading.value > 0);
// A group is named by its kind and its own key; the table wants one unique id per row.
const groupRows = computed(() => groups.value.map((g) => ({ ...g, id: `${g.kind}/${g.group}` })));

const columns = [
  { key: "severity", label: "Severity", width: 110 },
  { key: "title", label: "Alert", minWidth: 280 },
  { key: "provider", label: "Provider", width: 110 },
  { key: "first", label: "First seen", width: 170 },
  { key: "last", label: "Last seen", width: 170 },
  { key: "count", label: "Rows", align: "right" as const, width: 80 },
  { key: "open_count", label: "Open", align: "right" as const, width: 80 },
  { key: "keys", label: "Keys", align: "right" as const, width: 80 },
];

interface View {
  groups: AlertGroup[];
  live: LiveAlert[];
  opened: AlertDetail | null;
  detailError: unknown;
}

// The list, the live conditions and the open detail, read together so they are drawn together. A detail that cannot
// be read does not fail the rest.
async function read(at: AlertAsk): Promise<View> {
  const [list, stat] = await Promise.all([listAlertGroups(at.status, at.kind, GROUP_LIMIT), getStats("")]);
  let detail: AlertDetail | null = null;
  let detailError: unknown = null;
  if (at.open) {
    try {
      detail = await getAlertGroup(at.open, ROW_LIMIT);
    } catch (err) {
      detailError = err;
    }
  }
  return { groups: list.items, live: stat.alerts, opened: detail, detailError };
}

function draw(view: View): void {
  groups.value = view.groups;
  live.value = view.live;
  opened.value = view.opened;
  error.value = null;
  updatedAt.value = Date.now();
  if (view.detailError) {
    // the detail could not be read: leave it closed, so the list is whole and there is nothing to be stuck on
    asked.value = { ...asked.value, open: null };
    notify.error(errorText(view.detailError));
  }
}

// Throws the failure of the newest load; a dropped (stale) answer or failure returns quietly.
async function load(): Promise<void> {
  const ticket = latest.next();
  const at = { ...asked.value };
  loading.value++;
  try {
    let view: View;
    try {
      view = await read(at);
    } catch (err) {
      if (!latest.isCurrent(ticket)) return;
      error.value = errorText(err);
      throw err;
    }
    if (!latest.isCurrent(ticket)) return;
    draw(view);
  } finally {
    loading.value--;
  }
}

void run(load);
onBeforeUnmount(() => latest.invalidate());

const reload = () => run(load);

// The select holds a stand-in for "all kinds" (an empty value would show the placeholder instead of the label).
const kindChoice = computed({
  get: () => toSelect(asked.value.kind),
  set: (value: string) => {
    asked.value = { ...asked.value, kind: fromSelect(value) };
    void reload();
  },
});

// A group that is an error stands out with the left bar, a warning with the amber one.
const rowTone = (g: AlertGroup): Tone | undefined =>
  g.severity === "error" ? "bad" : g.severity === "warning" ? "warn" : undefined;

function openGroup(g: AlertGroup) {
  asked.value = { ...asked.value, open: { kind: g.kind, group: g.group } };
  void reload();
}

// Closing also reloads the list, so the table always matches the filters even if one was changed while a load ran.
function closeDetail() {
  asked.value = { ...asked.value, open: null };
  opened.value = null;
  void reload();
}

// `g` is the group as it was drawn, from the list or from the open detail: its newest row is the one the operator saw.
async function acknowledge(g: AlertGroup) {
  await run(async () => {
    writing.value = true;
    try {
      const result = await acknowledgeGroup(g, g.newest_id);
      let failure: unknown = null;
      try {
        await load();
      } catch (err) {
        failure = err;
      }
      const said = ackToast(result.acknowledged);
      if (failure) notify.error(`${said}, but the list could not be reloaded: ${errorText(failure)}`);
      else notify.success(said);
    } finally {
      writing.value = false;
    }
    // the banner counts the open groups
    await stats.refresh();
  });
}
</script>

<template>
  <section class="alerts-view">
    <ListToolbar :loading="loading > 0" :updated-at="updatedAt" @refresh="reload">
      <h2 class="view-title">Alerts</h2>
      <el-select v-model="asked.status" class="field" aria-label="Status" :disabled="writing" @change="reload">
        <el-option label="open" value="open" />
        <el-option label="acknowledged" value="acknowledged" />
        <el-option label="all" value="all" />
      </el-select>
      <el-select v-model="kindChoice" class="field" aria-label="Kind" :disabled="writing">
        <el-option label="All kinds" :value="ALL" />
        <el-option v-for="k in ALERT_KINDS" :key="k" :label="k" :value="k" />
      </el-select>
    </ListToolbar>
    <p class="help">
      Acknowledging a group marks the rows you saw; a row that arrives later reopens it. Which replies count as unclear
      is decided in code: new wording is a code change, there is nothing to edit here.
    </p>

    <section class="panel" aria-label="Live conditions">
      <h3>Live conditions</h3>
      <p class="sub">Computed now; they clear themselves, so there is nothing to acknowledge.</p>
      <ul v-if="live.length" class="live" data-testid="alerts-live">
        <li v-for="a in live" :key="`${a.kind}:${a.message}`">
          <StatusChip :status="a.severity" /> <strong>{{ a.kind }}</strong
          >: {{ a.message }}
        </li>
      </ul>
      <p v-else class="sub">No live condition right now</p>
    </section>

    <DataTable
      :columns="columns"
      :rows="groupRows"
      :loading="loading > 0"
      :error="error"
      empty-text="No alert groups with this filter"
      :row-tone="rowTone"
      @retry="reload"
    >
      <template #cell-severity="{ row }"><StatusChip :status="row.severity" /></template>
      <template #cell-title="{ row }">
        <div class="title">{{ groupTitle(row) }}</div>
        <div class="sub">{{ row.kind }}</div>
        <div v-if="oneCustomer(row)" class="note">one customer only: may be the customer's own words</div>
      </template>
      <template #cell-first="{ row }">{{ fmtTime(row.first_ms) }}</template>
      <template #cell-last="{ row }">{{ fmtTime(row.last_ms) }}</template>
      <template #actions="{ row }">
        <el-button size="small" :disabled="writing" @click="openGroup(row)">Details</el-button>
        <el-button v-if="row.open_count > 0" size="small" :disabled="locked" @click="acknowledge(row)"
          >Acknowledge</el-button
        >
      </template>
    </DataTable>
    <p v-if="groups.length >= GROUP_LIMIT" class="sub" data-testid="alerts-more">
      Showing the newest {{ GROUP_LIMIT }} groups; older groups are not listed.
    </p>

    <AlertDetailPanel
      v-if="opened"
      :detail="opened"
      :writing="writing"
      :locked="locked"
      @ack="acknowledge"
      @close="closeDetail"
    />
  </section>
</template>

<style scoped>
.view-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.field {
  width: 150px;
}
.help,
.sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.panel {
  margin-bottom: 16px;
  padding: 12px 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.panel h3 {
  margin: 0 0 4px;
  font-size: 14px;
}
.live {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}
.live li {
  margin: 4px 0;
}
.title {
  font-weight: 600;
  overflow-wrap: anywhere;
}
.note {
  color: var(--rl-warn);
  font-size: 12px;
}
</style>
