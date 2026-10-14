<script setup lang="ts">
import { computed } from "vue";
import type { AlertDetail, BillingItem, UnclearItem } from "@/api/alerts";
import DataTable from "@/components/shared/DataTable.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { fmtTime } from "@/lib/format";
import { useUiStore } from "@/stores/ui";
import { groupTitle, isEmptyWording, oneCustomer, outcomesText, ROW_LIMIT, summaryText } from "./alerts-model";

// The open group: its summary, the outcomes of an unclear wording and its rows. Acknowledge sends the group as shown
// (the parent reads `newest_id` from it). A job opens the shared job detail through the ui store. `writing`: a write
// is in flight, nothing can be clicked; `locked`: a view is loading, so only Close (which asks for another view) works.
const props = defineProps<{ detail: AlertDetail; writing: boolean; locked: boolean }>();
defineEmits<{ ack: [detail: AlertDetail]; close: [] }>();

const ui = useUiStore();
const unclear = computed(() => props.detail.kind === "unclear_reply");
const unclearRows = computed(() => props.detail.items as UnclearItem[]);
const billingRows = computed(() => props.detail.items as BillingItem[]);

const unclearColumns = [
  { key: "at", label: "Seen", width: 170 },
  { key: "text", label: "Stored reply", minWidth: 240 },
  { key: "job", label: "Job", width: 110 },
  { key: "outcome", label: "Outcome", minWidth: 150 },
  { key: "account", label: "Account", width: 130 },
  { key: "key", label: "Key", width: 100 },
  { key: "ack", label: "Acknowledged", minWidth: 190 },
];
const billingColumns = [
  { key: "at", label: "When", width: 170 },
  { key: "alert", label: "Alert", width: 100 },
  { key: "wallet", label: "Wallet", minWidth: 140 },
  { key: "amount", label: "Amount", align: "right" as const, width: 100 },
  { key: "used_day", label: "Used today", align: "right" as const, width: 110 },
  { key: "used_total", label: "Used total", align: "right" as const, width: 110 },
  { key: "detail", label: "Detail", minWidth: 160 },
  { key: "ack", label: "Acknowledged", minWidth: 190 },
];

const acked = (i: { acknowledged_ms: number | null; acknowledged_by: string | null }): string =>
  `${i.acknowledged_by}, ${fmtTime(i.acknowledged_ms)}`;
</script>

<template>
  <section class="detail" aria-label="Alert detail" data-testid="alert-detail">
    <header class="detail__head">
      <h3 class="detail__title" data-testid="alert-detail-title">{{ groupTitle(detail) }}</h3>
      <StatusChip :status="detail.severity" />
      <span class="grow" />
      <el-button v-if="detail.open_count > 0" :disabled="locked" @click="$emit('ack', detail)">Acknowledge</el-button>
      <el-button :disabled="writing" @click="$emit('close')">Close</el-button>
    </header>
    <p class="sub">{{ summaryText(detail) }}</p>
    <p v-if="oneCustomer(detail)" class="note">one customer only: may be the customer's own words</p>
    <p v-if="isEmptyWording(detail)" class="bad" data-testid="alert-read-failure">
      The provider's API gave no reply text, so there is no wording to read: this is an API read failure.
    </p>
    <p v-if="unclear && detail.outcomes" data-testid="alert-outcomes">{{ outcomesText(detail) }}</p>

    <DataTable v-if="unclear" :columns="unclearColumns" :rows="unclearRows" empty-text="No rows" max-height="24rem">
      <template #cell-at="{ row }">{{ fmtTime(row.at_ms) }}</template>
      <template #cell-text="{ row }"
        ><span class="reply">{{ row.text === "" ? "(no text)" : row.text }}</span></template
      >
      <template #cell-job="{ row }">
        <el-button v-if="row.job_id" link type="primary" @click="ui.openJob(row.job_id)">{{
          row.job_id.slice(0, 8)
        }}</el-button>
      </template>
      <template #cell-outcome="{ row }">
        <StatusChip :status="row.job_status" />
        <span v-if="row.job_error_code" class="sub gap-left">{{ row.job_error_code }}</span>
      </template>
      <template #cell-account="{ row }">{{ row.account_label === null ? "-" : row.account_label }}</template>
      <template #cell-key="{ row }"
        ><span class="mono">{{ row.key_id?.slice(0, 8) }}</span></template
      >
      <template #cell-ack="{ row }">
        <StatusChip v-if="row.acknowledged_ms === null" status="open" />
        <template v-else>{{ acked(row) }}</template>
      </template>
    </DataTable>
    <DataTable v-else :columns="billingColumns" :rows="billingRows" empty-text="No rows" max-height="24rem">
      <template #cell-at="{ row }">{{ fmtTime(row.at_ms) }}</template>
      <template #cell-alert="{ row }"><StatusChip :status="row.alert" tone="bad" /></template>
      <template #cell-wallet="{ row }">{{ row.provider }} / {{ row.kind }}</template>
      <template #cell-detail="{ row }">{{ row.detail === null ? "-" : row.detail }}</template>
      <template #cell-ack="{ row }">
        <StatusChip v-if="row.acknowledged_ms === null" status="open" />
        <template v-else>{{ acked(row) }}</template>
      </template>
    </DataTable>
    <p v-if="detail.items.length >= ROW_LIMIT" class="sub" data-testid="alert-detail-more">
      Showing the newest {{ ROW_LIMIT }} rows; older rows are not listed.
    </p>
  </section>
</template>

<style scoped>
.detail {
  margin-top: 16px;
  padding: 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.detail__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.detail__title {
  margin: 0;
  font-size: 16px;
  overflow-wrap: anywhere;
}
.grow {
  flex: 1;
}
.sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.note {
  margin: 0;
  color: var(--rl-warn);
  font-size: 12px;
}
.bad {
  color: var(--rl-bad);
}
.reply {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
.gap-left {
  margin-left: 6px;
}
</style>
