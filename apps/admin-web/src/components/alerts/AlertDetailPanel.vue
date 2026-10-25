<script setup lang="ts">
import { computed } from "vue";
import type { AlertDetail, BillingItem, CostItem, PoolDriftItem, UnclearItem } from "@/api/alerts";
import { scopeName } from "@/components/keys/key-model";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
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

// The body of the open group's drawer (AlertDetail.dc.html): the sentence, the reply as the provider sent it, the facts
// as a two-column ledger, the rows of the group in a ruled box (an arrow scroller when it is wider than the drawer)
// and the outcomes line. The drawer owns the title, the chips and Acknowledge; a job asks the parent to open it.
// Every word of a reply, a title or a label is customer-influenced: shown as text only.
const props = defineProps<{ detail: AlertDetail; writing: boolean }>();
const emit = defineEmits<{ "open-job": [jobId: string] }>();

const unclear = computed(() => props.detail.kind === "unclear_reply");
const unclearRows = computed(() => props.detail.items as UnclearItem[]);
const billingRows = computed(() => props.detail.items as BillingItem[]);
const costRows = computed(() => props.detail.items as CostItem[]);
const poolRows = computed(() => props.detail.items as PoolDriftItem[]);
const facts = computed(() => groupFacts(props.detail));
// the newest reply as stored, for the quote; the read failure has none
const quote = computed(() =>
  unclear.value && !isEmptyWording(props.detail) ? (props.detail.reply ?? "").trim() || props.detail.title : "",
);

const acked = (i: { acknowledged_ms: number | null; acknowledged_by: string | null }): string =>
  `${i.acknowledged_by}, ${alertTime(i.acknowledged_ms)}`;
// the job's title, else its short id; the key's label, else its short id
const jobName = (r: { job_id: string; job_title: string | null }): string => r.job_title || r.job_id.slice(0, 8);
const keyName = (r: { key_id?: string | null; key_label: string | null }): string =>
  r.key_label || r.key_id?.slice(0, 8) || "—";
const account = (r: UnclearItem): string => (r.account_label === null ? "—" : r.account_label);
</script>

<template>
  <div id="alertDetail" class="ad">
    <p v-if="unclear" class="ad__sentence">{{ replyNote(detail.media) }}</p>
    <p v-if="oneCustomer(detail)" class="ad-note">{{ ONE_CUSTOMER_NOTE }}</p>

    <figure v-if="quote" class="ad-quote">
      <figcaption>The reply, as {{ detail.provider || "the provider" }} sent it</figcaption>
      <blockquote>“{{ quote }}”</blockquote>
    </figure>
    <p v-if="isEmptyWording(detail)" class="ad-failure" role="alert" data-testid="alert-read-failure">
      The provider's API gave no reply text, so there is no wording to read: this is an API read failure.
    </p>

    <dl class="ad-ledger">
      <div v-for="f in facts" :key="f.label" class="ledger__row">
        <dt>{{ f.label }}</dt>
        <dd class="ledger__value mono num" :class="{ 'tone-warn': f.tone === 'warn' }">{{ f.value }}</dd>
      </div>
    </dl>

    <ArrowScroller class="ad-box" table label="Rows of this group">
      <!-- the reply rows -->
      <table v-if="unclear" class="ad__table">
        <caption>
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
          <tr v-for="r in unclearRows" :key="r.id">
            <td class="ad-seen mono num">{{ alertTime(r.at_ms) }}</td>
            <td class="ad-jobcell">
              <button
                type="button"
                class="ad-job"
                :title="jobName(r)"
                :disabled="props.writing"
                @click="emit('open-job', r.job_id)"
              >
                {{ jobName(r) }}
              </button>
              <span class="ad-jobid mono">{{ r.job_id.slice(0, 8) }}</span>
            </td>
            <td class="ad-outcome">
              <StatusChip :status="outcomeStatus(r.job_status)" />
              <span v-if="r.job_error_code" class="ad-code mono">{{ r.job_error_code }}</span>
            </td>
            <td class="ad-acctkey">
              <span class="ad-acct mono">{{ account(r) }}</span>
              <span class="ad-key">{{ keyName(r) }}</span>
              <span v-if="r.acknowledged_ms !== null" class="ad-acked">acknowledged {{ acked(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- the rows of a cost mismatch: each job, what its upstream cost was expected to be and what it was -->
      <table v-else-if="detail.kind === 'cost_mismatch'" class="ad__table" data-testid="alert-cost-rows">
        <caption>
          Jobs whose upstream cost was not the expected one
        </caption>
        <thead>
          <tr>
            <th scope="col" class="is-first">When</th>
            <th scope="col">Job</th>
            <th scope="col">Expected → actual</th>
            <th scope="col" class="is-last">Acknowledged</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in costRows" :key="r.id">
            <td class="ad-seen mono num">{{ alertTime(r.at_ms) }}</td>
            <td class="ad-jobcell">
              <button
                type="button"
                class="ad-job"
                :title="jobName(r)"
                :disabled="props.writing"
                @click="emit('open-job', r.job_id)"
              >
                {{ jobName(r) }}
              </button>
              <span class="ad-jobid mono">{{ keyName(r) }}</span>
            </td>
            <td class="ad-acctkey">
              <span class="ad-acct mono num" data-testid="alert-cost-figures">{{ r.expected }} → {{ r.actual }}</span>
              <span v-if="r.detail" class="ad-key">source: {{ r.detail }}</span>
            </td>
            <td class="ad-ack">
              <span v-if="r.acknowledged_ms === null" class="ad-faint">not yet</span>
              <span v-else class="ad-acked">{{ acked(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- the rows of a pool drift: what the upstream reported against what the relay computed -->
      <table v-else-if="detail.kind === 'pool_drift'" class="ad__table" data-testid="alert-pool-rows">
        <caption>
          Reported balances far from the computed one
        </caption>
        <thead>
          <tr>
            <th scope="col" class="is-first">When</th>
            <th scope="col">Account · pool</th>
            <th scope="col">Reported · computed</th>
            <th scope="col" class="is-last">Acknowledged</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in poolRows" :key="r.id">
            <td class="ad-seen mono num">{{ alertTime(r.at_ms) }}</td>
            <td class="ad-acctkey">
              <span class="ad-acct mono">{{ r.account_label ?? r.account_id.slice(0, 8) }}</span>
              <span class="ad-key">{{ r.pool }}</span>
            </td>
            <td class="ad-acctkey">
              <span class="ad-acct mono num">reported {{ r.actual }} · computed {{ r.expected }}</span>
            </td>
            <td class="ad-ack">
              <span v-if="r.acknowledged_ms === null" class="ad-faint">not yet</span>
              <span v-else class="ad-acked">{{ acked(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- the rows of a billing group: the same box, with the wallet's numbers -->
      <table v-else class="ad__table">
        <caption>
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
          <tr v-for="r in billingRows" :key="r.id">
            <td class="ad-seen mono num">{{ alertTime(r.at_ms) }}</td>
            <td class="ad-outcome">
              <StatusChip :status="r.alert" tone="bad" />
              <span v-if="r.detail" class="ad-code mono">{{ r.detail }}</span>
            </td>
            <td class="ad-acctkey">
              <span class="ad-key">{{ scopeName(r.scope) }}</span>
              <span class="ad-acct mono num"
                >amount {{ r.amount }} · today {{ r.used_day }} · total {{ r.used_total }}</span
              >
            </td>
            <td class="ad-ack">
              <span v-if="r.acknowledged_ms === null" class="ad-faint">not yet</span>
              <span v-else class="ad-acked">{{ acked(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
    <p v-if="!detail.items.length" class="ad-none">No rows</p>

    <p v-if="unclear && detail.outcomes" class="ad__foot num" data-testid="alert-outcomes">
      {{ outcomesText(detail) }}
    </p>
    <p v-if="detail.items.length >= ROW_LIMIT" class="ad__foot" data-testid="alert-detail-more">
      Showing the newest {{ ROW_LIMIT }} rows; older rows are not listed.
    </p>
  </div>
</template>

<style scoped>
.ad {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.ad__sentence {
  margin: 0;
  font-size: 14px;
  line-height: 21px;
  color: var(--rl-body);
}
.ad-note,
.ad__foot {
  margin: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ad-quote {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 14px 16px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
}
.ad-quote figcaption {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ad-quote blockquote {
  margin: 0;
  font-size: 15px;
  line-height: 23px;
  overflow-wrap: anywhere;
}
.ad-failure {
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
  line-height: 19px;
}

/* the facts: two columns of rows ruled on the grid */
.ad-ledger {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.ledger__row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  margin: -1px 0 0 -1px;
  padding: 9px 14px;
  border-top: 1px solid var(--rl-rule);
  border-left: 1px solid var(--rl-rule);
}
.ledger__row dt {
  flex: none;
  font-size: 13px;
  color: var(--rl-faint);
}
.ledger__value {
  min-width: 0;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-fg);
  text-align: right;
  overflow-wrap: anywhere;
}
.ledger__value.tone-warn {
  color: var(--rl-warn);
}
@media (max-width: 767px) {
  .ad-ledger {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* the rows: a ruled box, its caption on top */
.ad-box {
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.ad__table {
  width: 100%;
  min-width: 560px;
  border-collapse: collapse;
}
.ad__table caption {
  caption-side: top;
  padding: 10px 14px;
  text-align: left;
  font-size: 13px;
  font-weight: 700;
}
.ad__table thead tr {
  text-align: left;
  font-size: 12px;
  color: var(--rl-faint);
  background: var(--rl-head);
}
.ad__table th {
  padding: 8px;
  font-weight: 600;
  white-space: nowrap;
}
.ad__table th.is-first {
  padding-left: 14px;
}
.ad__table th.is-last {
  padding-right: 14px;
}
.ad__table tbody tr {
  border-top: 1px solid var(--rl-rule);
  vertical-align: top;
}
.ad__table td {
  padding: 10px 8px;
}
.ad__table .ad-seen {
  padding-left: 14px;
  font-size: 12px;
  white-space: nowrap;
  color: var(--rl-muted);
}
.ad__table .ad-acctkey,
.ad__table .ad-ack {
  padding-right: 14px;
}
.ad-jobcell {
  max-width: 200px;
}
.ad-job {
  display: block;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-brand-ink);
  font: inherit;
  font-weight: 700;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.ad-job:hover:not(:disabled) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.ad-job:disabled {
  cursor: default;
}
.ad-jobid {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ad-code {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--rl-bad);
  overflow-wrap: anywhere;
}
.ad-acct {
  display: block;
  font-size: 12px;
}
.ad-key {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ad-acked {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--rl-ok);
}
.ad-faint {
  font-size: 12px;
  color: var(--rl-faint);
}
.ad-none {
  margin: 0;
  padding: 28px 16px;
  text-align: center;
  color: var(--rl-muted);
}
</style>
