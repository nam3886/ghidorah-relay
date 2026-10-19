<script setup lang="ts">
import { computed } from "vue";
import type { AlertDetail, BillingItem, UnclearItem } from "@/api/alerts";
import LedgerList, { type LedgerItem } from "@/components/shared/LedgerList.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import "@/components/logs/lane-controls.css";
import "./alert-buttons.css";
import {
  alertTime,
  groupFacts,
  isEmptyWording,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  outcomesText,
  outcomeStatus,
  replyNote,
  ROW_LIMIT,
} from "./alerts-model";

// The body of the open group's drawer: its facts, the rows of the group and, for an unclear wording, how the jobs
// ended. The drawer owns the title, the severity chip and Acknowledge (its footer); a job asks the parent to open it.
// The markup is the canvas's (B-, T-, P-Alerts): plain tables, as wide as their content; a list on a phone.
const props = defineProps<{ detail: AlertDetail }>();
const emit = defineEmits<{ "open-job": [jobId: string] }>();

const { isPhone, isTablet } = useBreakpoint();
const unclear = computed(() => props.detail.kind === "unclear_reply");
const unclearRows = computed(() => props.detail.items as UnclearItem[]);
const billingRows = computed(() => props.detail.items as BillingItem[]);
const facts = computed<LedgerItem[]>(() =>
  groupFacts(props.detail).map((f) => ({ label: f.label, value: f.value, tone: f.tone, mono: true })),
);

const acked = (i: { acknowledged_ms: number | null; acknowledged_by: string | null }): string =>
  `${i.acknowledged_by}, ${alertTime(i.acknowledged_ms)}`;
// the job's title, else its short id; the key's label, else its short id
const jobName = (r: { job_id: string; job_title: string | null }): string => r.job_title || r.job_id.slice(0, 8);
const keyName = (r: { key_id?: string; key_label: string | null }): string =>
  r.key_label || r.key_id?.slice(0, 8) || "—";
const account = (r: UnclearItem): string => (r.account_label === null ? "—" : r.account_label);
</script>

<template>
  <!-- phone: the sheet's body -->
  <div v-if="isPhone" id="alertDetail" class="ad-phone">
    <LedgerList class="ad-phone__ledger" :items="facts" />
    <p v-if="oneCustomer(detail)" class="ad-note">{{ ONE_CUSTOMER_NOTE }}</p>
    <p v-if="isEmptyWording(detail)" class="ad-failure" role="alert" data-testid="alert-read-failure">
      The provider's API gave no reply text, so there is no wording to read: this is an API read failure.
    </p>
    <ul v-if="unclear" class="ad-phone__rows">
      <li v-for="r in unclearRows" :key="r.id">
        <span class="ad-phone__row1">
          <span class="ad-time">{{ alertTime(r.at_ms) }}</span>
          <StatusChip :status="outcomeStatus(r.job_status)" />
        </span>
        <button type="button" class="ad-job" @click="emit('open-job', r.job_id)">{{ jobName(r) }}</button>
        <span v-if="r.job_error_code" class="ad-code">{{ r.job_error_code }}</span>
        <span class="ad-muted"
          ><span class="ad-acct">{{ account(r) }}</span> · {{ keyName(r)
          }}<template v-if="r.acknowledged_ms !== null"> · {{ acked(r) }}</template></span
        >
      </li>
    </ul>
    <ul v-else class="ad-phone__rows">
      <li v-for="r in billingRows" :key="r.id">
        <span class="ad-phone__row1">
          <span class="ad-time">{{ alertTime(r.at_ms) }}</span>
          <StatusChip :status="r.alert" tone="bad" />
        </span>
        <span class="ad-muted">{{ r.provider }} / {{ r.kind }}</span>
        <span class="ad-muted ad-mono"
          >amount {{ r.amount }} · today {{ r.used_day }} · total {{ r.used_total
          }}<template v-if="r.detail"> · {{ r.detail }}</template></span
        >
        <span v-if="r.acknowledged_ms !== null" class="ad-muted">{{ acked(r) }}</span>
      </li>
    </ul>
    <p v-if="unclear && detail.outcomes" class="ad-foot" data-testid="alert-outcomes">{{ outcomesText(detail) }}</p>
    <p v-if="detail.items.length >= ROW_LIMIT" class="ad-foot" data-testid="alert-detail-more">
      Showing the newest {{ ROW_LIMIT }} rows; older rows are not listed.
    </p>
  </div>

  <!-- desktop and tablet -->
  <div v-else id="alertDetail" class="ad" :class="{ 'is-tablet': isTablet }">
    <p v-if="unclear" class="ad__sentence">{{ replyNote(detail.media) }}</p>
    <p v-if="oneCustomer(detail)" class="ad-note">{{ ONE_CUSTOMER_NOTE }}</p>
    <LedgerList class="ad__panel" :items="facts" />
    <p v-if="isEmptyWording(detail)" class="ad-failure ad-failure--card" role="alert" data-testid="alert-read-failure">
      The provider's API gave no reply text, so there is no wording to read: this is an API read failure.
    </p>

    <!-- the reply rows: desktop seven columns in a scrolling box of 1040 px at least, tablet four -->
    <template v-if="unclear">
      <div v-if="!isTablet" class="ad__scroll">
        <table class="ad__table">
          <caption class="ad-caption">
            Unclear-reply rows of this group
          </caption>
          <thead>
            <tr>
              <th scope="col" class="is-first">Seen</th>
              <th scope="col">Stored reply</th>
              <th scope="col">Job</th>
              <th scope="col">Outcome</th>
              <th scope="col">Account</th>
              <th scope="col">Key</th>
              <th scope="col" class="is-last">Acknowledged</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in unclearRows" :key="r.id" class="ad-row">
              <td class="ad-seen">{{ alertTime(r.at_ms) }}</td>
              <td class="ad-reply">
                <span class="ad-reply__text">{{ r.text === "" ? "(no text)" : r.text }}</span>
              </td>
              <td class="ad-jobcell">
                <button type="button" class="ad-job" @click="emit('open-job', r.job_id)">{{ jobName(r) }}</button>
                <span class="ad-jobid">{{ r.job_id.slice(0, 8) }}</span>
              </td>
              <td class="ad-outcome">
                <StatusChip :status="outcomeStatus(r.job_status)" />
                <span v-if="r.job_error_code" class="ad-code">{{ r.job_error_code }}</span>
              </td>
              <td class="ad-acct">{{ account(r) }}</td>
              <td class="ad-key">{{ keyName(r) }}</td>
              <td class="ad-ack">{{ r.acknowledged_ms === null ? "not yet" : acked(r) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <table v-else class="ad__table">
        <caption class="ad-caption">
          Unclear-reply rows of this group
        </caption>
        <thead>
          <tr>
            <th scope="col" class="is-first">Seen</th>
            <th scope="col">Job</th>
            <th scope="col">Outcome</th>
            <th scope="col" class="is-last">Account · key</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in unclearRows" :key="r.id" class="ad-row">
            <td class="ad-seen">{{ alertTime(r.at_ms) }}</td>
            <td class="ad-jobcell">
              <button type="button" class="ad-job" @click="emit('open-job', r.job_id)">{{ jobName(r) }}</button>
              <span class="ad-jobid">{{ r.job_id.slice(0, 8) }}</span>
            </td>
            <td class="ad-outcome">
              <StatusChip :status="outcomeStatus(r.job_status)" />
              <span v-if="r.job_error_code" class="ad-code">{{ r.job_error_code }}</span>
            </td>
            <td class="ad-acctkey">
              <span class="ad-acct">{{ account(r) }}</span>
              <span class="ad-key">{{ keyName(r) }}</span>
              <span v-if="r.acknowledged_ms !== null" class="ad-acked">{{ acked(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- the rows of a billing group (not drawn: the reply table's frame, with the billing list's facts) -->
    <template v-else>
      <div v-if="!isTablet" class="ad__scroll">
        <table class="ad__table">
          <caption class="ad-caption">
            Rows of this group
          </caption>
          <thead>
            <tr>
              <th scope="col" class="is-first">When</th>
              <th scope="col">Alert</th>
              <th scope="col">Wallet</th>
              <th scope="col" class="is-right">Amount</th>
              <th scope="col" class="is-right">Used today</th>
              <th scope="col" class="is-right">Used total</th>
              <th scope="col">Detail</th>
              <th scope="col" class="is-last">Acknowledged</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in billingRows" :key="r.id" class="ad-row">
              <td class="ad-seen">{{ alertTime(r.at_ms) }}</td>
              <td class="ad-outcome"><StatusChip :status="r.alert" tone="bad" /></td>
              <td class="ad-key">{{ r.provider }} / {{ r.kind }}</td>
              <td class="ad-num">{{ r.amount }}</td>
              <td class="ad-num">{{ r.used_day }}</td>
              <td class="ad-num">{{ r.used_total }}</td>
              <td class="ad-detail">{{ r.detail === null ? "—" : r.detail }}</td>
              <td class="ad-ack">{{ r.acknowledged_ms === null ? "not yet" : acked(r) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <table v-else class="ad__table">
        <caption class="ad-caption">
          Rows of this group
        </caption>
        <thead>
          <tr>
            <th scope="col" class="is-first">When</th>
            <th scope="col">Alert</th>
            <th scope="col">Wallet</th>
            <th scope="col" class="is-last">Acknowledged</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in billingRows" :key="r.id" class="ad-row">
            <td class="ad-seen">{{ alertTime(r.at_ms) }}</td>
            <td class="ad-outcome">
              <StatusChip :status="r.alert" tone="bad" />
              <span v-if="r.detail" class="ad-code">{{ r.detail }}</span>
            </td>
            <td class="ad-acctkey">
              <span class="ad-key">{{ r.provider }} / {{ r.kind }}</span>
              <span class="ad-acct">amount {{ r.amount }} · today {{ r.used_day }} · total {{ r.used_total }}</span>
            </td>
            <td class="ad-ack">{{ r.acknowledged_ms === null ? "not yet" : acked(r) }}</td>
          </tr>
        </tbody>
      </table>
    </template>
    <p v-if="!detail.items.length" class="ad-none">No rows</p>

    <p v-if="unclear && detail.outcomes" class="ad__foot" data-testid="alert-outcomes">{{ outcomesText(detail) }}</p>
    <p v-if="detail.items.length >= ROW_LIMIT" class="ad__foot" data-testid="alert-detail-more">
      Showing the newest {{ ROW_LIMIT }} rows; older rows are not listed.
    </p>
  </div>
</template>

<style scoped>
/* every value below is the canvas's own (B-, T-, P-Alerts; specs/alerts.md 4 to 6) */
.ad {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.ad__sentence {
  margin: 0;
  color: var(--rl-muted);
}
.ad .ad__panel {
  flex: none;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 6px 40px;
  margin: 0;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--rl-raised);
}
.ad-caption {
  position: absolute;
  left: -9999px;
}
.ad__scroll {
  overflow-x: auto;
  border-top: 1px solid var(--rl-line);
}
.ad__table {
  width: 100%;
  min-width: 1040px;
  border-collapse: separate;
  border-spacing: 0;
}
.ad__table thead tr {
  text-align: left;
  font-size: 12px;
  color: var(--rl-muted);
}
.ad__table th {
  height: 38px;
  padding: 0 12px;
  background: var(--rl-raised);
  font-weight: 500;
}
.ad__table th.is-first {
  padding: 0 12px 0 16px;
}
.ad__table th.is-last {
  padding: 0 16px 0 12px;
}
.ad__table th.is-right {
  text-align: right;
}
.ad__table td {
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-line);
}
.ad-row:hover td {
  background: var(--rl-raised);
}
.ad__table .ad-seen {
  height: 56px;
  padding: 0 12px 0 16px;
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.ad__table .ad-reply {
  max-width: 300px;
  font-size: 13px;
}
.ad-reply__text {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ad__table .ad-jobcell {
  max-width: 260px;
  padding: 8px 12px;
}
.ad-job {
  display: block;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
/* in dark the canvas's `.th-dark a` outranks `.title-link`: the link colour, primary on hover */
html.dark .ad-job {
  color: var(--rl-link);
}
html.dark .ad-job:hover {
  color: var(--rl-primary);
}
.ad-job:hover {
  color: var(--rl-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.ad-jobid {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-faint);
}
.ad__table .ad-outcome {
  padding: 6px 12px;
  white-space: nowrap;
}
.ad-code {
  display: block;
  margin-top: 3px;
  font-family: var(--rl-mono);
  font-size: 11px;
  color: var(--rl-muted);
}
.ad__table .ad-acct {
  font-family: var(--rl-mono);
  font-size: 12px;
}
.ad__table .ad-key {
  font-size: 13px;
  white-space: nowrap;
}
.ad__table .ad-num {
  text-align: right;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.ad__table .ad-detail {
  font-size: 13px;
  color: var(--rl-muted);
}
.ad__table .ad-ack {
  padding: 0 16px 0 12px;
  font-size: 13px;
  color: var(--rl-faint);
}
.ad-none {
  margin: 0;
  padding: 44px 16px 52px;
  border-top: 1px solid var(--rl-line);
  text-align: center;
  color: var(--rl-muted);
}
.ad__foot {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.ad-note {
  margin: 0;
  font-size: 12px;
  color: var(--rl-muted);
}
.ad-failure {
  margin: 0;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  font-size: 13px;
}

/* tablet: a two-column panel, the four-column table top-aligned */
.ad.is-tablet .ad__panel {
  grid-template-columns: 1fr 1fr;
  gap: 6px 32px;
}
.is-tablet .ad__table {
  min-width: 0;
  border-top: 1px solid var(--rl-line);
}
.is-tablet .ad__table th {
  padding: 0 8px;
}
.is-tablet .ad__table th.is-first {
  padding: 0 8px 0 16px;
}
.is-tablet .ad__table th.is-last {
  padding: 0 16px 0 8px;
}
.is-tablet .ad__table td {
  vertical-align: top;
}
.is-tablet .ad__table .ad-seen {
  height: auto;
  padding: 12px 8px 12px 16px;
}
.is-tablet .ad__table .ad-jobcell {
  max-width: none;
  padding: 10px 8px;
}
.is-tablet .ad-job {
  white-space: normal;
}
.is-tablet .ad__table .ad-outcome {
  padding: 8px;
  white-space: normal;
}
.is-tablet .ad-code {
  overflow-wrap: anywhere;
}
.is-tablet .ad__table .ad-acctkey {
  padding: 10px 16px 10px 8px;
  white-space: nowrap;
}
.is-tablet .ad-acctkey .ad-acct {
  display: block;
  font-family: var(--rl-mono);
  font-size: 12px;
}
.is-tablet .ad-acctkey .ad-key {
  font-size: 13px;
  color: var(--rl-muted);
}
.ad-acked {
  display: block;
  font-size: 13px;
  color: var(--rl-faint);
}
.is-tablet .ad__table .ad-ack {
  padding: 10px 16px 10px 8px;
}

/* phone */
.ad-phone {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.ad-phone .ad-phone__ledger {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-raised);
}
.ad-phone__ledger :deep(.ledger__row) {
  min-height: 22px;
}
.ad-phone .ad-failure {
  margin: 0;
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
.ad-time {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
.ad-phone__rows .ad-job {
  display: flex;
  align-items: center;
  max-width: none;
  min-height: 44px;
  white-space: normal;
}
.ad-phone__rows .ad-code {
  margin-top: 0;
}
.ad-muted {
  font-size: 13px;
  color: var(--rl-muted);
}
.ad-mono {
  font-family: var(--rl-mono);
  font-size: 12px;
}
.ad-phone__rows .ad-acct {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-fg);
}
.ad-foot {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
</style>
