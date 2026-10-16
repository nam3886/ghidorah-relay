<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import { acknowledgeGroup, type AlertDetail, type AlertGroup, getAlertGroup, listAlertGroups } from "@/api/alerts";
import { getStats, type LiveAlert } from "@/api/shell";
import PageHeader from "@/components/layout/PageHeader.vue";
import AlertDetailPanel from "@/components/alerts/AlertDetailPanel.vue";
import {
  ackToast,
  type AlertAsk,
  ALERT_KINDS,
  alertTime,
  GROUP_LIMIT,
  groupTitle,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  ROW_LIMIT,
} from "@/components/alerts/alerts-model";
import "@/components/logs/lane-controls.css";
import DataTable from "@/components/shared/DataTable.vue";
import { IconRefresh } from "@/components/shared/icons";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
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
const { isPhone, isTablet } = useBreakpoint();

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
type GroupRow = (typeof groupRows.value)[number];

const columns = computed(() =>
  isTablet.value
    ? [
        { key: "severity", label: "Severity", width: 110 },
        { key: "title", label: "Alert", minWidth: 240 },
        { key: "openrows", label: "Open / Rows", align: "right" as const, width: 120 },
      ]
    : [
        { key: "severity", label: "Severity", width: 110 },
        { key: "title", label: "Alert", minWidth: 280 },
        { key: "provider", label: "Provider", width: 100 },
        { key: "first", label: "First seen", width: 140 },
        { key: "last", label: "Last seen", width: 140 },
        { key: "count", label: "Rows", align: "right" as const, width: 80 },
        { key: "open_count", label: "Open", align: "right" as const, width: 80 },
        { key: "keys", label: "Keys", align: "right" as const, width: 80 },
      ],
);

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

const updatedText = computed(() =>
  updatedAt.value ? new Date(updatedAt.value).toLocaleTimeString("en-GB", { hour12: false }) : null,
);

const STATUS_CHOICES = [
  { value: "all", label: "All" },
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
] as const;

// The group whose detail is open, as the table and the phone list mark it.
const isOpened = (g: AlertGroup): boolean => asked.value.open?.kind === g.kind && asked.value.open.group === g.group;
const rowClass = ({ row }: { row: GroupRow }): string =>
  [row.open_count === 0 ? "a-dim" : "", isOpened(row) ? "a-selected" : ""].filter(Boolean).join(" ");

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

const groupCount = computed(() => `${groups.value.length} ${groups.value.length === 1 ? "group" : "groups"}`);
</script>

<template>
  <section class="alerts-view">
    <PageHeader title="Alerts" subtitle="Acknowledging marks rows as seen; a later row reopens the group.">
      <template v-if="updatedText" #meta>Updated {{ updatedText }}</template>
      <template #actions>
        <button v-if="!isPhone" type="button" class="kla-btn" :disabled="loading > 0" @click="reload">
          <IconRefresh :size="15" />Refresh
        </button>
        <button
          v-else
          type="button"
          class="kla-icon-btn kla-icon-btn--lg a-refresh"
          aria-label="Refresh"
          :disabled="loading > 0"
          @click="reload"
        >
          <IconRefresh :size="18" />
        </button>
      </template>
    </PageHeader>
    <span v-if="isPhone && updatedText" class="a-updated kla-mono">Updated {{ updatedText }}</span>

    <section class="kla-card live" aria-label="Live conditions">
      <header class="live__head">
        <h2>Live conditions</h2>
        <span>Computed now. These cannot be acknowledged; they clear when the condition does.</span>
      </header>
      <ul v-if="live.length" class="live__list" data-testid="alerts-live">
        <li v-for="a in live" :key="`${a.kind}:${a.message}`">
          <StatusChip :status="a.severity" class="live__pill" />
          <span class="live__text"
            ><span class="live__kind">{{ a.kind }}:</span> {{ a.message }}</span
          >
        </li>
      </ul>
      <p v-else class="live__none">No live condition right now.</p>
    </section>

    <section class="kla-card groups" aria-label="Alert groups">
      <div class="gt">
        <component :is="isPhone ? 'label' : 'div'" :class="isPhone ? 'kla-chip-field' : 'kla-field'">
          <component :is="isPhone ? 'span' : 'label'" :class="isPhone ? '' : 'kla-field__label'" for="alerts-status"
            >Status</component
          >
          <select
            id="alerts-status"
            v-model="asked.status"
            class="kla-control"
            :class="{ 'kla-control--tall': isTablet }"
            aria-label="Status"
            :disabled="writing"
            @change="reload"
          >
            <option v-for="o in STATUS_CHOICES" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </component>
        <component :is="isPhone ? 'label' : 'div'" :class="isPhone ? 'kla-chip-field' : 'kla-field'">
          <component :is="isPhone ? 'span' : 'label'" :class="isPhone ? '' : 'kla-field__label'" for="alerts-kind"
            >Kind</component
          >
          <select
            id="alerts-kind"
            v-model="asked.kind"
            class="kla-control"
            :class="{ 'kla-control--tall': isTablet }"
            aria-label="Kind"
            :disabled="writing"
            @change="reload"
          >
            <option value="">All</option>
            <option v-for="k in ALERT_KINDS" :key="k" :value="k">{{ k }}</option>
          </select>
        </component>
        <span v-if="!isPhone" class="gt__spacer" />
        <span class="gt__count kla-mono" data-testid="alerts-count">{{ groupCount }}</span>
      </div>

      <DataTable
        class="groups-table"
        :actions-width="210"
        :columns="columns"
        :rows="groupRows"
        :loading="loading > 0"
        :error="error"
        empty-text="No alert groups with this filter"
        :row-class-name="rowClass"
        @retry="reload"
      >
        <template #cell-severity="{ row }"><StatusChip :status="row.severity" /></template>
        <template #cell-title="{ row }">
          <span class="g-title">{{ groupTitle(row) }}</span>
          <span class="g-kind kla-mono"
            >{{ row.kind }}<template v-if="isTablet"> · {{ row.provider }}</template></span
          >
          <span v-if="isTablet" class="g-range kla-mono"
            >{{ alertTime(row.first_ms) }} → {{ alertTime(row.last_ms) }}</span
          >
          <span v-if="oneCustomer(row)" class="g-note">{{ ONE_CUSTOMER_NOTE }}</span>
        </template>
        <template #cell-provider="{ row }"
          ><span class="g-muted">{{ row.provider }}</span></template
        >
        <template #cell-first="{ row }"
          ><span class="g-time kla-mono">{{ alertTime(row.first_ms) }}</span></template
        >
        <template #cell-last="{ row }"
          ><span class="g-time kla-mono">{{ alertTime(row.last_ms) }}</span></template
        >
        <template #cell-count="{ row }"
          ><span class="kla-mono g-num">{{ row.count }}</span></template
        >
        <template #cell-open_count="{ row }"
          ><span class="kla-mono g-num g-open" :class="{ 'is-faint': row.open_count === 0 }">{{
            row.open_count
          }}</span></template
        >
        <template #cell-keys="{ row }"
          ><span class="kla-mono g-num">{{ row.keys }}</span></template
        >
        <template #cell-openrows="{ row }">
          <span class="kla-mono g-num"
            ><span class="g-open" :class="{ 'is-faint': row.open_count === 0 }">{{ row.open_count }}</span> /
            {{ row.count }}</span
          >
        </template>
        <template #actions="{ row }">
          <div class="g-actions">
            <button
              type="button"
              class="kla-btn kla-btn--link kla-btn--small g-details"
              :aria-expanded="isOpened(row)"
              :disabled="writing"
              :aria-label="`Details of ${groupTitle(row)}`"
              @click="openGroup(row)"
            >
              Details
            </button>
            <button
              v-if="row.open_count > 0"
              type="button"
              class="kla-btn kla-btn--small g-ack"
              :disabled="locked"
              @click="acknowledge(row)"
            >
              Acknowledge
            </button>
          </div>
        </template>

        <!-- the phone list: a block per group, the open detail right under its group -->
        <template #row="{ row }">
          <div class="p-group" :class="{ 'a-dim': row.open_count === 0, 'is-selected': isOpened(row) }">
            <StatusChip :status="row.severity" class="p-group__pill" />
            <span class="g-title">{{ groupTitle(row) }}</span>
            <span class="g-kind kla-mono">{{ row.kind }}</span>
            <span v-if="oneCustomer(row)" class="g-note">{{ ONE_CUSTOMER_NOTE }}</span>
            <span class="p-group__range kla-mono"
              >first {{ alertTime(row.first_ms) }} → last {{ alertTime(row.last_ms) }}</span
            >
            <span class="p-group__counts kla-mono"
              >rows {{ row.count }} <i aria-hidden="true">·</i>
              <span class="g-open" :class="{ 'is-faint': row.open_count === 0 }">open {{ row.open_count }}</span>
              <i aria-hidden="true">·</i> keys {{ row.keys }}</span
            >
            <div class="p-group__actions">
              <button
                type="button"
                class="kla-btn kla-btn--fit p-group__btn"
                :aria-expanded="isOpened(row)"
                :disabled="writing"
                :aria-label="`Details of ${groupTitle(row)}`"
                @click="openGroup(row)"
              >
                Details
              </button>
              <button
                v-if="row.open_count > 0"
                type="button"
                class="kla-btn kla-btn--fit p-group__btn"
                :disabled="locked"
                @click="acknowledge(row)"
              >
                Acknowledge
              </button>
            </div>
          </div>
          <AlertDetailPanel
            v-if="opened && isOpened(row)"
            :detail="opened"
            :writing="writing"
            :locked="locked"
            @ack="acknowledge"
            @close="closeDetail"
          />
        </template>
      </DataTable>
      <p v-if="groups.length >= GROUP_LIMIT" class="alerts-more" data-testid="alerts-more">
        Showing the newest {{ GROUP_LIMIT }} groups; older groups are not listed.
      </p>
    </section>

    <AlertDetailPanel
      v-if="opened && !isPhone"
      :detail="opened"
      :writing="writing"
      :locked="locked"
      @ack="acknowledge"
      @close="closeDetail"
    />
  </section>
</template>

<style scoped>
.alerts-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
@media (max-width: 1279px) {
  .alerts-view {
    gap: 16px;
  }
}
.a-updated {
  margin-top: -12px;
  font-size: 12px;
  color: var(--rl-faint);
}
.a-refresh {
  border: 1px solid var(--rl-line-strong);
  color: var(--rl-fg);
}

/* live conditions */
.live__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 14px 16px 10px;
}
.live__head h2 {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.live__head span {
  font-size: 12px;
  color: var(--rl-faint);
}
.live__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.live__list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  box-sizing: border-box;
  min-height: 44px;
  padding: 10px 16px;
  border-top: 1px solid var(--rl-line);
}
.live__pill {
  min-width: 64px;
  box-sizing: border-box;
}
.live__text {
  flex: 1;
  min-width: 240px;
}
.live__kind {
  color: var(--rl-muted);
}
.live__none {
  margin: 0;
  padding: 10px 16px 14px;
  border-top: 1px solid var(--rl-line);
  color: var(--rl-muted);
}
@media (max-width: 1279px) {
  .live__head {
    display: block;
  }
  .live__head span {
    display: block;
  }
  .live__text {
    min-width: 0;
  }
}
@media (max-width: 767px) {
  .live__head {
    padding: 14px 16px 10px;
  }
  .live__head span {
    margin: 2px 0 0;
    line-height: 16px;
  }
  .live__list li {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    min-height: 72px;
    padding: 12px 16px;
  }
}

/* groups */
.groups {
  min-width: 0;
}
.gt {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  padding: 14px 16px;
}
.gt__spacer {
  flex: 1;
}
.gt__count {
  font-size: 13px;
  color: var(--rl-muted);
}
@media (max-width: 1279px) {
  .gt {
    flex-wrap: nowrap;
  }
  .gt__count {
    line-height: 36px;
  }
}
@media (max-width: 767px) {
  .gt {
    align-items: center;
    gap: 8px;
    padding: 12px;
    overflow-x: auto;
  }
  .gt__count {
    flex: none;
    padding: 0 4px;
    line-height: 20px;
  }
}
.groups-table {
  border-top: 1px solid var(--rl-line);
}
.groups-table :deep(.el-table) {
  min-width: 1040px;
}
.groups-table.is-tablet :deep(.el-table) {
  min-width: 0;
}
.groups-table :deep(td.el-table__cell) {
  padding: 8px 0;
  vertical-align: top;
}
.groups-table :deep(td.el-table__cell:first-child .cell),
.groups-table :deep(th.el-table__cell:first-child .cell) {
  padding-left: 16px;
}
.groups-table :deep(td.el-table__cell:last-child .cell),
.groups-table :deep(th.el-table__cell:last-child .cell) {
  padding-right: 16px;
}
.groups-table :deep(tr.a-dim) {
  opacity: 0.55;
}
.groups-table :deep(tr.a-selected > td.el-table__cell) {
  background: var(--rl-primary-soft);
}
.g-title {
  display: block;
  max-width: 380px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.g-kind,
.g-range {
  display: block;
  font-size: 12px;
}
.g-kind {
  color: var(--rl-faint);
}
.g-range,
.g-time {
  font-size: 12px;
  color: var(--rl-muted);
  white-space: nowrap;
}
.g-note {
  display: block;
  font-size: 12px;
  color: var(--rl-muted);
}
.g-muted {
  font-size: 13px;
  color: var(--rl-muted);
}
.g-num {
  font-size: 13px;
  white-space: nowrap;
}
.g-open {
  font-weight: 500;
  color: var(--rl-warn);
}
.g-open.is-faint {
  color: var(--rl-faint);
}
.g-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  white-space: nowrap;
}
.g-ack {
  margin-left: 4px;
}
.alerts-more {
  margin: 0;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--rl-muted);
}

/* phone */
.groups :deep(.data-table__main) {
  padding: 0 !important;
  align-items: stretch;
  justify-content: flex-start;
}
.groups :deep(.data-table__item) {
  display: block;
  border-top: 1px solid var(--rl-line);
}
.p-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 16px;
}
.p-group.a-dim {
  opacity: 0.55;
}
.p-group.is-selected {
  background: var(--rl-primary-soft);
}
.p-group__pill {
  align-self: flex-start;
}
.p-group__range {
  font-size: 13px;
  color: var(--rl-muted);
}
.p-group__counts {
  display: flex;
  gap: 6px;
  font-size: 12px;
  color: var(--rl-muted);
}
.p-group__counts i {
  font-style: normal;
}
.p-group__actions {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
.p-group__btn {
  flex: 1;
}
</style>
