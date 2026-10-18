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
  alertTitle,
  GROUP_LIMIT,
  liveWord,
  loadFailedText,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  ROW_LIMIT,
} from "@/components/alerts/alerts-model";
import "@/components/alerts/alert-buttons.css";
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
//
// The group list is drawn as the canvas draws it: a plain table (its columns as wide as their content, which an
// Element Plus table cannot do) on a desktop and a tablet, a list of blocks on a phone with the open detail right
// under its group. The loading, error and empty states are the shared table's (B-States).
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
// A group is named by its kind and its own key; a list wants one unique id per row.
const groupRows = computed(() => groups.value.map((g) => ({ ...g, id: `${g.kind}/${g.group}` })));
type GroupRow = (typeof groupRows.value)[number];

// The columns the shared table's loading skeleton draws (the list itself is the plain table below); a phone draws
// the tablet's three, as eight do not fit.
const columns = computed(() =>
  isTablet.value || isPhone.value
    ? [
        { key: "severity", label: "Severity" },
        { key: "title", label: "Alert" },
        { key: "openrows", label: "Open / Rows", align: "right" as const },
      ]
    : [
        { key: "severity", label: "Severity" },
        { key: "title", label: "Alert" },
        { key: "provider", label: "Provider" },
        { key: "first", label: "First seen" },
        { key: "last", label: "Last seen" },
        { key: "count", label: "Rows", align: "right" as const },
        { key: "open_count", label: "Open", align: "right" as const },
        { key: "keys", label: "Keys", align: "right" as const },
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
      error.value = loadFailedText(err, errorText(err));
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

// The state of the list: the shared table draws loading (nothing yet), the error and the empty list; the rows are
// drawn here, dimmed while the next answer is on its way.
const listState = computed(() =>
  error.value ? "error" : groups.value.length ? (loading.value > 0 ? "refreshing" : "ready") : "other",
);
// The phone list breaks after the opened group: its detail region sits between it and the next group, as drawn.
const openedAt = computed(() => (opened.value ? groupRows.value.findIndex((g) => isOpened(g)) : -1));
const phoneHead = computed(() => (openedAt.value < 0 ? groupRows.value : groupRows.value.slice(0, openedAt.value + 1)));
const phoneTail = computed(() => (openedAt.value < 0 ? [] : groupRows.value.slice(openedAt.value + 1)));

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
  <section class="alerts-view" :class="{ 'is-tablet': isTablet, 'is-phone': isPhone }">
    <PageHeader title="Alerts" subtitle="Acknowledging marks rows as seen; a later row reopens the group.">
      <template v-if="updatedText" #meta>Updated {{ updatedText }}</template>
      <template v-if="isPhone && updatedText" #submeta
        ><span class="a-updated">Updated {{ updatedText }}</span></template
      >
      <template #actions>
        <button v-if="!isPhone" type="button" class="al-btn a-refresh" :disabled="loading > 0" @click="reload">
          <IconRefresh :size="15" />Refresh
        </button>
        <button
          v-else
          type="button"
          class="al-btn a-refresh-icon"
          aria-label="Refresh"
          :disabled="loading > 0"
          @click="reload"
        >
          <IconRefresh :size="18" />
        </button>
      </template>
    </PageHeader>

    <section class="kla-card live" aria-label="Live conditions">
      <div class="live__head">
        <h2>Live conditions</h2>
        <span class="live__note">Computed now. These cannot be acknowledged; they clear when the condition does.</span>
      </div>
      <ul v-if="live.length" class="live__list" data-testid="alerts-live">
        <li v-for="a in live" :key="`${a.kind}:${a.message}`">
          <StatusChip :status="a.severity" class="live__pill" />
          <span class="live__text"
            ><span class="live__kind">{{ liveWord(a.kind) }}:</span> {{ a.message }}</span
          >
        </li>
      </ul>
      <p v-else-if="updatedAt" class="live__none">No live condition right now.</p>
    </section>

    <section class="kla-card groups" aria-label="Alert groups">
      <div class="gt">
        <component :is="isPhone ? 'label' : 'div'" :class="isPhone ? 'gt__chip' : 'gt__field'">
          <component :is="isPhone ? 'span' : 'label'" class="gt__label" for="alerts-status">Status</component>
          <select
            id="alerts-status"
            v-model="asked.status"
            class="gt__select"
            aria-label="Status"
            :disabled="writing"
            @change="reload"
          >
            <option v-for="o in STATUS_CHOICES" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </component>
        <component :is="isPhone ? 'label' : 'div'" :class="isPhone ? 'gt__chip' : 'gt__field'">
          <component :is="isPhone ? 'span' : 'label'" class="gt__label" for="alerts-kind">Kind</component>
          <select
            id="alerts-kind"
            v-model="asked.kind"
            class="gt__select"
            aria-label="Kind"
            :disabled="writing"
            @change="reload"
          >
            <option value="">All</option>
            <option v-for="k in ALERT_KINDS" :key="k" :value="k">{{ k }}</option>
          </select>
        </component>
        <span v-if="!isPhone" class="gt__spacer" />
        <span class="gt__count" data-testid="alerts-count">{{ groupCount }}</span>
      </div>

      <DataTable
        v-if="listState === 'error' || listState === 'other'"
        :columns="columns"
        :rows="[]"
        :loading="loading > 0"
        :error="error"
        empty-text="No alert groups with this filter"
        @retry="reload"
      />

      <!-- the phone list: a block per group, the open detail right under its group -->
      <div v-else-if="isPhone" class="p-lists" :data-state="listState">
        <ul class="p-list">
          <li
            v-for="g in phoneHead"
            :key="g.id"
            class="p-group"
            :class="{ 'a-dim': g.open_count === 0, 'is-selected': isOpened(g) }"
          >
            <StatusChip :status="g.severity" class="p-group__pill" />
            <span class="p-group__title">{{ alertTitle(g) }}</span>
            <span class="p-group__kind">{{ g.kind }}</span>
            <span v-if="oneCustomer(g)" class="p-group__note">{{ ONE_CUSTOMER_NOTE }}</span>
            <span class="p-group__range">first {{ alertTime(g.first_ms) }} → last {{ alertTime(g.last_ms) }}</span>
            <span class="p-group__counts"
              ><span>rows {{ g.count }}</span
              ><span aria-hidden="true">·</span
              ><span class="p-group__open" :class="{ 'is-faint': g.open_count === 0 }">open {{ g.open_count }}</span
              ><span aria-hidden="true">·</span><span>keys {{ g.keys }}</span></span
            >
            <span class="p-group__actions">
              <button
                type="button"
                class="al-btn al-link p-group__btn"
                :aria-expanded="isOpened(g)"
                :disabled="writing"
                :aria-label="`Details of ${alertTitle(g)}`"
                @click="openGroup(g)"
              >
                Details
              </button>
              <button
                v-if="g.open_count > 0"
                type="button"
                class="al-btn p-group__btn"
                :disabled="locked"
                @click="acknowledge(g)"
              >
                Acknowledge
              </button>
            </span>
          </li>
        </ul>
        <AlertDetailPanel
          v-if="opened && openedAt >= 0"
          :detail="opened"
          :writing="writing"
          :locked="locked"
          @ack="acknowledge"
          @close="closeDetail"
        />
        <ul v-if="phoneTail.length" class="p-list">
          <li
            v-for="g in phoneTail"
            :key="g.id"
            class="p-group"
            :class="{ 'a-dim': g.open_count === 0, 'is-selected': isOpened(g) }"
          >
            <StatusChip :status="g.severity" class="p-group__pill" />
            <span class="p-group__title">{{ alertTitle(g) }}</span>
            <span class="p-group__kind">{{ g.kind }}</span>
            <span v-if="oneCustomer(g)" class="p-group__note">{{ ONE_CUSTOMER_NOTE }}</span>
            <span class="p-group__range">first {{ alertTime(g.first_ms) }} → last {{ alertTime(g.last_ms) }}</span>
            <span class="p-group__counts"
              ><span>rows {{ g.count }}</span
              ><span aria-hidden="true">·</span
              ><span class="p-group__open" :class="{ 'is-faint': g.open_count === 0 }">open {{ g.open_count }}</span
              ><span aria-hidden="true">·</span><span>keys {{ g.keys }}</span></span
            >
            <span class="p-group__actions">
              <button
                type="button"
                class="al-btn al-link p-group__btn"
                :aria-expanded="isOpened(g)"
                :disabled="writing"
                :aria-label="`Details of ${alertTitle(g)}`"
                @click="openGroup(g)"
              >
                Details
              </button>
              <button
                v-if="g.open_count > 0"
                type="button"
                class="al-btn p-group__btn"
                :disabled="locked"
                @click="acknowledge(g)"
              >
                Acknowledge
              </button>
            </span>
          </li>
        </ul>
      </div>

      <!-- the tablet table: Severity, Alert (with the kind, provider and span under the title), Open / Rows -->
      <table v-else-if="isTablet" class="g-table" :data-state="listState">
        <caption class="a-caption">
          Alert groups
        </caption>
        <thead>
          <tr>
            <th scope="col" class="g-th-sev">Severity</th>
            <th scope="col">Alert</th>
            <th scope="col" class="is-right">Open / Rows</th>
            <th scope="col" class="g-th-actions is-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="g in groupRows" :key="g.id" class="g-row" :class="rowClass({ row: g })">
            <td class="g-sev"><StatusChip :status="g.severity" /></td>
            <td class="g-alert">
              <span class="g-title">{{ alertTitle(g) }}</span>
              <span class="g-kind">{{ g.kind }} · {{ g.provider }}</span>
              <span class="g-range">{{ alertTime(g.first_ms) }} → {{ alertTime(g.last_ms) }}</span>
              <span v-if="oneCustomer(g)" class="g-note">{{ ONE_CUSTOMER_NOTE }}</span>
            </td>
            <td class="g-openrows">
              <span class="g-open" :class="{ 'is-faint': g.open_count === 0 }">{{ g.open_count }}</span> /
              {{ g.count }}
            </td>
            <td class="g-actions">
              <button
                type="button"
                class="al-btn al-link g-details"
                :aria-expanded="isOpened(g)"
                :disabled="writing"
                :aria-label="`Details of ${alertTitle(g)}`"
                @click="openGroup(g)"
              >
                Details</button
              >&#32;<button
                v-if="g.open_count > 0"
                type="button"
                class="al-btn g-ack"
                :disabled="locked"
                @click="acknowledge(g)"
              >
                Acknowledge
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- the desktop table, 1040 px at least, scrolling inside its own box -->
      <div v-else class="g-scroll">
        <table class="g-table" :data-state="listState">
          <thead>
            <tr>
              <th scope="col" class="g-th-sev">Severity</th>
              <th scope="col">Alert</th>
              <th scope="col">Provider</th>
              <th scope="col">First seen</th>
              <th scope="col">Last seen</th>
              <th scope="col" class="is-right">Rows</th>
              <th scope="col" class="is-right">Open</th>
              <th scope="col" class="is-right">Keys</th>
              <th scope="col" class="g-th-actions is-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in groupRows" :key="g.id" class="g-row" :class="rowClass({ row: g })">
              <td class="g-sev"><StatusChip :status="g.severity" /></td>
              <td class="g-alert">
                <span class="g-title">{{ alertTitle(g) }}</span>
                <span class="g-kind">{{ g.kind }}</span>
                <span v-if="oneCustomer(g)" class="g-note">{{ ONE_CUSTOMER_NOTE }}</span>
              </td>
              <td class="g-provider">{{ g.provider }}</td>
              <td class="g-time">{{ alertTime(g.first_ms) }}</td>
              <td class="g-time">{{ alertTime(g.last_ms) }}</td>
              <td class="g-num">{{ g.count }}</td>
              <td class="g-num g-open" :class="{ 'is-faint': g.open_count === 0 }">{{ g.open_count }}</td>
              <td class="g-num">{{ g.keys }}</td>
              <td class="g-actions">
                <button
                  type="button"
                  class="al-btn al-link g-details"
                  :aria-expanded="isOpened(g)"
                  :disabled="writing"
                  :aria-label="`Details of ${alertTitle(g)}`"
                  @click="openGroup(g)"
                >
                  Details</button
                >&#32;<button
                  v-if="g.open_count > 0"
                  type="button"
                  class="al-btn g-ack"
                  :disabled="locked"
                  @click="acknowledge(g)"
                >
                  Acknowledge
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
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
/* every value below is the canvas's own (B-, T-, P-Alerts; specs/alerts.md) */
.alerts-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.alerts-view.is-phone {
  gap: 16px;
}

.a-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
}
.a-refresh-icon {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
}
.a-updated {
  display: block;
  line-height: 20px;
}
.a-caption {
  position: absolute;
  left: -9999px;
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
.live__note {
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
  box-sizing: border-box;
  min-width: 64px;
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
.is-tablet .live__head,
.is-phone .live__head {
  display: block;
}
.is-tablet .live__list li {
  flex-wrap: nowrap;
}
.is-tablet .live__text {
  min-width: 0;
}
.is-phone .live__note {
  display: block;
  margin-top: 2px;
  line-height: 16px;
}
.is-phone .live__list li {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 6px;
  min-height: 72px;
  padding: 12px 16px;
}
.is-phone .live__pill {
  min-width: 0;
}
.is-phone .live__text {
  flex: none;
  min-width: 0;
}

/* the toolbar: the native selects, labelled above them (a chip with the word inside on a phone), and the count */
.gt {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  padding: 14px 16px;
}
.gt__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.gt__label {
  font-size: 12px;
  color: var(--rl-muted);
}
.gt__select {
  box-sizing: border-box;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-family: inherit;
  font-size: 13px;
}
.gt__spacer {
  flex: 1;
}
.gt__count {
  font-size: 13px;
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
.is-tablet .gt {
  flex-wrap: nowrap;
}
.is-tablet .gt__select {
  height: 36px;
}
.is-tablet .gt__count {
  line-height: 36px;
}
.is-phone .gt {
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  padding: 12px;
}
.gt__chip {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  height: 44px;
  padding: 0 6px 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  font-size: 13px;
}
.gt__chip .gt__label {
  font-size: 13px;
}
.gt__chip .gt__select {
  height: 40px;
  padding: revert;
  border: 0;
  border-radius: revert;
  background: transparent;
}
.is-phone .gt__count {
  flex: none;
  padding: 0 4px;
}

/* refreshing: the rows dim while the next answer is on its way, the header stays (B-States, refreshing) */
[data-state="refreshing"] tbody,
[data-state="refreshing"] .p-list {
  opacity: 0.6;
  transition: opacity 0.15s;
}

/* the group table */
.g-scroll {
  overflow-x: auto;
}
.g-table {
  width: 100%;
  min-width: 1040px;
  border-collapse: separate;
  border-spacing: 0;
}
.is-tablet .g-table {
  min-width: 0;
}
.g-table thead tr {
  text-align: left;
  font-size: 12px;
  color: var(--rl-muted);
}
.g-table th {
  height: 38px;
  padding: 0 12px;
  background: var(--rl-raised);
  font-weight: 500;
}
.g-table th.is-right {
  text-align: right;
}
.g-table .g-th-sev {
  padding: 0 12px 0 16px;
}
.g-table .g-th-actions {
  padding: 0 16px 0 12px;
}
.g-table td {
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-line);
}
.g-row.a-dim {
  opacity: 0.55;
}
.g-row.a-selected td {
  background: var(--rl-primary-soft);
}
.g-row:hover td {
  background: var(--rl-raised);
}
.g-table .g-sev {
  height: 64px;
  padding: 0 12px 0 16px;
  white-space: nowrap;
}
.g-table .g-alert {
  max-width: 380px;
  padding: 8px 12px;
}
.g-title {
  display: block;
  font-weight: 500;
}
.g-kind {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-faint);
}
.g-note {
  display: block;
  font-size: 12px;
  color: var(--rl-muted);
}
.g-provider {
  font-size: 13px;
  color: var(--rl-muted);
}
.g-time {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.g-num {
  text-align: right;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.g-open {
  font-weight: 500;
  color: var(--rl-warn);
}
.g-open.is-faint {
  color: var(--rl-faint);
}
.g-table .g-actions {
  padding: 0 16px 0 12px;
  text-align: right;
  white-space: nowrap;
}
.g-details {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 10px;
  line-height: 20px;
}
.g-ack {
  height: 32px;
  margin-left: 4px;
  padding: 0 10px;
}
.is-tablet .g-table th {
  padding: 0 8px;
}
.is-tablet .g-table .g-th-sev {
  padding: 0 8px 0 16px;
}
.is-tablet .g-table .g-th-actions {
  padding: 0 16px 0 8px;
}
.is-tablet .g-table th.is-right {
  white-space: nowrap;
}
.is-tablet .g-table td {
  vertical-align: top;
}
.is-tablet .g-table .g-sev {
  height: auto;
  padding: 12px 8px 12px 16px;
}
.is-tablet .g-table .g-alert {
  max-width: none;
  padding: 10px 8px;
}
.is-tablet .g-kind,
.is-tablet .g-range {
  display: block;
  font-family: var(--rl-mono);
  font-size: 12px;
}
.g-range {
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
.is-tablet .g-table .g-openrows {
  padding: 12px 8px;
  text-align: right;
  font-family: var(--rl-mono);
  font-size: 13px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.is-tablet .g-table .g-actions {
  padding: 8px 16px 8px 8px;
}
.alerts-more {
  margin: 0;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--rl-muted);
}

/* the phone list */
.p-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.p-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
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
.p-group__title {
  font-weight: 500;
}
.p-group__kind {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-faint);
}
.p-group__note {
  font-size: 12px;
  color: var(--rl-muted);
}
.p-group__range {
  font-size: 13px;
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
.p-group__counts {
  display: flex;
  gap: 6px;
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
.p-group__open {
  color: var(--rl-warn);
}
.p-group__open.is-faint {
  color: var(--rl-faint);
}
.p-group__actions {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
.p-group__btn {
  flex: 1;
  height: 44px;
  font-size: 14px;
}
/* the canvas draws Details as a link of 44 px plus its border (46 high), Acknowledge as a 44 px button */
.p-group__btn.al-link {
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: content-box;
  padding: 0;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  line-height: 20px;
}
.p-group__btn.al-link:hover {
  background: var(--rl-raised);
}
</style>
