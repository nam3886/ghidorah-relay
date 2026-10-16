<script setup lang="ts">
import { computed } from "vue";
import type { AlertDetail, BillingItem, UnclearItem } from "@/api/alerts";
import DataTable from "@/components/shared/DataTable.vue";
import LedgerList, { type LedgerItem } from "@/components/shared/LedgerList.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { fmtTime } from "@/lib/format";
import { useUiStore } from "@/stores/ui";
import "@/components/logs/lane-controls.css";
import {
  alertTime,
  groupFacts,
  groupTitle,
  isEmptyWording,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  outcomesText,
  outcomeStatus,
  REPLY_NOTE,
  ROW_LIMIT,
} from "./alerts-model";

// The open group, drawn inline under the group list (a card on a desktop and a tablet, a region between the group items
// on a phone): its facts, the rows of the group and, for an unclear wording, how the jobs ended. Acknowledge sends
// the group as shown (the parent reads `newest_id` from it). A job opens the shared job drawer through the ui store.
// `writing`: a write is in flight, nothing can be clicked; `locked`: a view is loading, so only Close (which asks for
// another view) works.
const props = defineProps<{ detail: AlertDetail; writing: boolean; locked: boolean }>();
const emit = defineEmits<{ ack: [detail: AlertDetail]; close: [] }>();

const ui = useUiStore();
const { isPhone, isTablet } = useBreakpoint();
const unclear = computed(() => props.detail.kind === "unclear_reply");
const unclearRows = computed(() => props.detail.items as UnclearItem[]);
const billingRows = computed(() => props.detail.items as BillingItem[]);
const facts = computed<LedgerItem[]>(() =>
  groupFacts(props.detail).map((f) => ({ label: f.label, value: f.value, tone: f.tone, mono: true })),
);

const unclearColumns = computed(() => [
  { key: "at", label: "Seen", width: 150 },
  { key: "text", label: "Stored reply", minWidth: 240 },
  { key: "job", label: "Job", minWidth: 150 },
  { key: "outcome", label: "Outcome", minWidth: 170 },
  { key: "account", label: isTablet.value ? "Account · key" : "Account", width: isTablet.value ? 140 : 110 },
  { key: "key", label: "Key", width: 110 },
  { key: "ack", label: "Acknowledged", minWidth: 150 },
]);
const UNCLEAR_TABLET = ["at", "job", "outcome", "account"];
const billingColumns = [
  { key: "at", label: "When", width: 150 },
  { key: "alert", label: "Alert", width: 100 },
  { key: "wallet", label: "Wallet", minWidth: 140 },
  { key: "amount", label: "Amount", align: "right" as const, width: 100 },
  { key: "used_day", label: "Used today", align: "right" as const, width: 110 },
  { key: "used_total", label: "Used total", align: "right" as const, width: 110 },
  { key: "detail", label: "Detail", minWidth: 160 },
  { key: "ack", label: "Acknowledged", minWidth: 150 },
];

const acked = (i: { acknowledged_ms: number | null; acknowledged_by: string | null }): string =>
  `${i.acknowledged_by}, ${fmtTime(i.acknowledged_ms)}`;
// the job's title, else its short id; the key's label, else its short id
const jobName = (r: { job_id: string; job_title: string | null }): string => r.job_title || r.job_id.slice(0, 8);
const keyName = (r: { key_id?: string; key_label: string | null }): string =>
  r.key_label || r.key_id?.slice(0, 8) || "—";
</script>

<template>
  <!-- phone: a region between the group items -->
  <div
    v-if="isPhone"
    id="alertDetail"
    class="ad-phone"
    role="region"
    aria-label="Alert detail"
    data-testid="alert-detail"
  >
    <h3 class="ad-phone__title" data-testid="alert-detail-title">
      {{ unclear ? "Group summary and reply rows" : "Group summary and rows" }}
    </h3>
    <LedgerList class="ad-phone__ledger" :items="facts" />
    <p v-if="oneCustomer(detail)" class="ad-note">{{ ONE_CUSTOMER_NOTE }}</p>
    <p v-if="isEmptyWording(detail)" class="ad-failure" role="alert" data-testid="alert-read-failure">
      The provider's API gave no reply text, so there is no wording to read: this is an API read failure.
    </p>
    <ul v-if="unclear" class="ad-phone__rows">
      <li v-for="r in unclearRows" :key="r.id">
        <div class="ad-phone__row1">
          <span class="ad-time kla-mono">{{ alertTime(r.at_ms) }}</span>
          <StatusChip :status="outcomeStatus(r.job_status)" />
        </div>
        <button type="button" class="ad-job" @click="ui.openJob(r.job_id)">{{ jobName(r) }}</button>
        <span v-if="r.job_error_code" class="ad-code kla-mono">{{ r.job_error_code }}</span>
        <div class="ad-muted">
          <span class="kla-mono ad-fg">{{ r.account_label === null ? "—" : r.account_label }}</span> ·
          {{ keyName(r) }}
          <template v-if="r.acknowledged_ms !== null"> · {{ acked(r) }}</template>
        </div>
      </li>
    </ul>
    <ul v-else class="ad-phone__rows">
      <li v-for="r in billingRows" :key="r.id">
        <div class="ad-phone__row1">
          <span class="ad-time kla-mono">{{ alertTime(r.at_ms) }}</span>
          <StatusChip :status="r.alert" tone="bad" />
        </div>
        <div class="ad-muted">{{ r.provider }} / {{ r.kind }}</div>
        <div class="ad-muted kla-mono">
          amount {{ r.amount }} · today {{ r.used_day }} · total {{ r.used_total
          }}<template v-if="r.detail"> · {{ r.detail }}</template>
        </div>
        <div v-if="r.acknowledged_ms !== null" class="ad-muted">{{ acked(r) }}</div>
      </li>
    </ul>
    <p v-if="unclear && detail.outcomes" class="ad-foot" data-testid="alert-outcomes">{{ outcomesText(detail) }}</p>
    <p v-if="detail.items.length >= ROW_LIMIT" class="ad-foot" data-testid="alert-detail-more">
      Showing the newest {{ ROW_LIMIT }} rows; older rows are not listed.
    </p>
    <button
      v-if="detail.open_count > 0"
      type="button"
      class="kla-btn kla-btn--primary ad-phone__ack"
      :aria-disabled="locked"
      :disabled="locked"
      @click="emit('ack', detail)"
    >
      Acknowledge
    </button>
  </div>

  <!-- desktop and tablet: a card -->
  <section
    v-else
    id="alertDetail"
    class="kla-card ad"
    role="region"
    aria-label="Alert detail"
    data-testid="alert-detail"
  >
    <header class="ad__head" :class="{ 'is-tablet': isTablet }">
      <div class="ad__left">
        <div class="ad__titlerow">
          <h2 class="ad__title" data-testid="alert-detail-title">{{ groupTitle(detail) }}</h2>
          <StatusChip :status="detail.severity" />
        </div>
        <p v-if="unclear" class="ad__sentence">{{ REPLY_NOTE }}</p>
        <p v-if="oneCustomer(detail)" class="ad-note">{{ ONE_CUSTOMER_NOTE }}</p>
      </div>
      <div class="ad__buttons">
        <button type="button" class="kla-btn" :disabled="writing" @click="emit('close')">Close</button>
        <button
          v-if="detail.open_count > 0"
          type="button"
          class="kla-btn kla-btn--primary"
          :disabled="locked"
          @click="emit('ack', detail)"
        >
          Acknowledge
        </button>
      </div>
    </header>

    <div class="ad__panel" :class="{ 'is-tablet': isTablet }">
      <LedgerList v-for="f in facts" :key="f.label" :items="[f]" />
    </div>
    <p v-if="isEmptyWording(detail)" class="ad-failure ad-failure--card" role="alert" data-testid="alert-read-failure">
      The provider's API gave no reply text, so there is no wording to read: this is an API read failure.
    </p>

    <DataTable
      v-if="unclear"
      class="ad__table"
      :columns="unclearColumns"
      :rows="unclearRows"
      :tablet-columns="UNCLEAR_TABLET"
      empty-text="No rows"
    >
      <template #cell-at="{ row }"
        ><span class="ad-time kla-mono">{{ alertTime(row.at_ms) }}</span></template
      >
      <template #cell-text="{ row }"
        ><span class="ad-reply">{{ row.text === "" ? "(no text)" : row.text }}</span></template
      >
      <template #cell-job="{ row }">
        <template v-if="row.job_id">
          <button type="button" class="ad-job" @click="ui.openJob(row.job_id)">{{ jobName(row) }}</button>
          <span class="ad-jobid kla-mono">{{ row.job_id.slice(0, 8) }}</span>
        </template>
      </template>
      <template #cell-outcome="{ row }">
        <StatusChip :status="outcomeStatus(row.job_status)" />
        <span v-if="row.job_error_code" class="ad-code kla-mono">{{ row.job_error_code }}</span>
      </template>
      <template #cell-account="{ row }">
        <span class="kla-mono ad-acct">{{ row.account_label === null ? "—" : row.account_label }}</span>
        <span v-if="isTablet" class="ad-key">{{ keyName(row) }}</span>
      </template>
      <template #cell-key="{ row }"
        ><span class="ad-key">{{ keyName(row) }}</span></template
      >
      <template #cell-ack="{ row }">
        <span v-if="row.acknowledged_ms === null" class="ad-faint">not yet</span>
        <template v-else>{{ acked(row) }}</template>
      </template>
    </DataTable>
    <DataTable v-else class="ad__table" :columns="billingColumns" :rows="billingRows" empty-text="No rows">
      <template #cell-at="{ row }"
        ><span class="ad-time kla-mono">{{ alertTime(row.at_ms) }}</span></template
      >
      <template #cell-alert="{ row }"><StatusChip :status="row.alert" tone="bad" /></template>
      <template #cell-wallet="{ row }">{{ row.provider }} / {{ row.kind }}</template>
      <template #cell-detail="{ row }">{{ row.detail === null ? "—" : row.detail }}</template>
      <template #cell-ack="{ row }">
        <span v-if="row.acknowledged_ms === null" class="ad-faint">not yet</span>
        <template v-else>{{ acked(row) }}</template>
      </template>
    </DataTable>

    <p v-if="unclear && detail.outcomes" class="ad__foot" data-testid="alert-outcomes">{{ outcomesText(detail) }}</p>
    <p v-if="detail.items.length >= ROW_LIMIT" class="ad__foot" data-testid="alert-detail-more">
      Showing the newest {{ ROW_LIMIT }} rows; older rows are not listed.
    </p>
  </section>
</template>

<style scoped>
.ad__head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 14px;
}
.ad__head.is-tablet {
  padding: 18px 16px 14px;
}
.ad__left {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1 1 320px;
  min-width: 0;
}
.ad__titlerow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.ad__title {
  margin: 0;
  font-size: 20px;
  line-height: 28px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.ad__sentence {
  margin: 0;
  color: var(--rl-muted);
}
.ad__buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ad__panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 6px 40px;
  margin: 0 20px 16px;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--rl-raised);
}
.ad__panel.is-tablet {
  grid-template-columns: 1fr 1fr;
  gap: 6px 32px;
  margin: 0 16px 16px;
}
.ad__table {
  border-top: 1px solid var(--rl-line);
}
.ad__table :deep(.el-table) {
  min-width: 1040px;
}
.ad__table.is-tablet :deep(.el-table) {
  min-width: 0;
}
.ad__table :deep(td.el-table__cell) {
  padding: 8px 0;
}
.ad__table :deep(td.el-table__cell:first-child .cell),
.ad__table :deep(th.el-table__cell:first-child .cell) {
  padding-left: 20px;
}
.ad__table.is-tablet :deep(td.el-table__cell:first-child .cell),
.ad__table.is-tablet :deep(th.el-table__cell:first-child .cell) {
  padding-left: 16px;
}
.ad__table :deep(td.el-table__cell:last-child .cell),
.ad__table :deep(th.el-table__cell:last-child .cell) {
  padding-right: 20px;
}
.ad__foot {
  margin: 0;
  padding: 14px 20px;
  border-top: 1px solid var(--rl-line);
  font-size: 13px;
  color: var(--rl-muted);
}
.is-tablet ~ .ad__foot {
  padding: 14px 16px;
}
.ad-note {
  margin: 0;
  font-size: 12px;
  color: var(--rl-muted);
}
.ad-failure {
  margin: 0 20px 16px;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  font-size: 13px;
}
.ad-time {
  font-size: 12px;
  color: var(--rl-muted);
  white-space: nowrap;
}
.ad-reply {
  display: block;
  max-width: 300px;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ad-job {
  display: block;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-family: var(--rl-mono);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.ad-job:hover {
  color: var(--rl-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.ad-jobid {
  display: block;
  font-size: 12px;
  color: var(--rl-faint);
}
.ad-code {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.ad-acct {
  display: block;
  font-size: 12px;
}
.ad-key {
  display: block;
  font-size: 13px;
  color: var(--rl-muted);
}
.ad-faint {
  font-size: 13px;
  color: var(--rl-faint);
}

/* phone */
.ad-phone {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  border-top: 1px solid var(--rl-line);
}
.ad-phone__title {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.ad-phone__ledger {
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-raised);
}
.ad-phone__ledger :deep(.ledger__row) {
  min-height: 22px;
}
.ad-phone__rows {
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--rl-line);
}
.ad-phone__rows li {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 0;
  border-bottom: 1px solid var(--rl-line);
}
.ad-phone__row1 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.ad-phone__rows .ad-job {
  display: flex;
  align-items: center;
  min-height: 44px;
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
}
.ad-muted {
  font-size: 13px;
  color: var(--rl-muted);
}
.ad-fg {
  color: var(--rl-fg);
}
.ad-foot {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.ad-phone__ack {
  width: 100%;
  height: 48px;
  font-size: 14px;
}
</style>
