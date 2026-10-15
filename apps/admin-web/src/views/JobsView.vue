<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from "vue";
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
import { createCursorStack } from "@/components/jobs/cursor-stack";
import { ALL, fromSelect, rememberedStatus, toSelect } from "@/components/jobs/job-filters";
import { jobChanges } from "@/components/jobs/job-changes";
import { isRefunded, progressStep, recheckToast, regenerateToast, shortId } from "@/components/jobs/job-text";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import { pickedIds, setPage, toggled } from "@/components/jobs/selection";
import CursorPager from "@/components/shared/CursorPager.vue";
import DataTable from "@/components/shared/DataTable.vue";
import ListToolbar from "@/components/shared/ListToolbar.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Tone } from "@/components/shared/status-tones";
import type { Column } from "@/components/shared/types";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { fmtTime } from "@/lib/format";
import { notify } from "@/lib/notify";
import { bulkAction } from "@/lib/operation-key";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";

// Newest first, 50 a page by keyset. The provider scope, the status filter (kept in memory) and the key filter all
// restart the paging. A job opens in the shared detail dialog through ui.openJob.
const route = useRoute();
const scope = useScopeStore();
const ui = useUiStore();

const columns: Column[] = [
  { key: "select", label: "", width: 44, align: "center" },
  { key: "progress", label: "Progress", width: 90 },
  { key: "title", label: "Title", minWidth: 120 },
  { key: "job", label: "Job", width: 100 },
  { key: "operation", label: "Operation", minWidth: 150 },
  { key: "status", label: "Status", minWidth: 120 },
  { key: "price", label: "Points", width: 80, align: "right" },
  { key: "outputs", label: "Results", width: 84, align: "right" },
  { key: "inputs", label: "Refs", width: 70, align: "right" },
  { key: "account_label", label: "Account", minWidth: 90 },
  { key: "key_label", label: "Key", minWidth: 80 },
  { key: "created", label: "Created", minWidth: 160 },
];

const items = shallowRef<JobSummary[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);
const stack = createCursorStack();
const { page, hasNext } = stack;
const latest = useLatest();

const status = rememberedStatus;
const keyId = ref("");
const keys = ref<KeyOption[]>([]);
const statusOptions = [ALL, ...JOB_STATUSES];

// The ticked jobs. Always read through pickedIds: only what is on the page is acted on.
const picked = ref<Set<string>>(new Set());
const ids = computed(() => pickedIds(items.value, picked.value));
const allOnPage = computed(() => items.value.length > 0 && ids.value.length === items.value.length);
const someOnPage = computed(() => ids.value.length > 0 && !allOnPage.value);

// The stats cells link to /jobs?status=done|failed; a key id may come the same way.
function applyQuery(): void {
  const wanted = route.query.status;
  if (typeof wanted === "string" && (JOB_STATUSES as readonly string[]).includes(wanted)) status.value = wanted;
  if (typeof route.query.key_id === "string") keyId.value = route.query.key_id;
}

// Loads page `at` (0-based). Only the newest answer counts; a failed page move leaves the cursor where it was.
async function load(at: number = stack.index.value): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listJobs({
      status: status.value,
      provider: scope.provider,
      keyId: keyId.value,
      cursor: stack.cursorAt(at),
    });
    if (!latest.isCurrent(ticket)) return;
    items.value = out.items;
    stack.arrive(at, out.next_cursor);
    // Only the ticks of jobs still on screen survive: another page starts with nothing ticked, a reload of this
    // page keeps its ticks.
    picked.value = new Set(pickedIds(out.items, picked.value));
    error.value = null;
    updatedAt.value = Date.now();
  } catch (err) {
    if (latest.isCurrent(ticket)) error.value = errorText(err);
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}

applyQuery();
watch([status, keyId, () => scope.provider], () => {
  stack.reset();
  picked.value = new Set();
  void load(0);
});
watch(() => route.query, applyQuery);
watch(jobChanges, () => void load());

onMounted(() => {
  void load(0);
  listKeyOptions().then(
    (out) => (keys.value = out.items),
    (err) => notify.error(errorText(err)),
  );
});

const tick = (id: string, on: boolean): void => {
  picked.value = toggled(picked.value, id, on);
};

const { busy: rechecking, run } = useBusy();
const recheck = () =>
  run(async () => {
    const sent = ids.value;
    if (!sent.length) {
      notify.error("Tick at least one job");
      return;
    }
    const out = await bulkRecheck(sent);
    picked.value = new Set();
    await load();
    const toast = recheckToast(out);
    notify[toast.type](toast.text);
  });

// A failure leaves the selection as it was: the retry of the same selection sends the same key, and a different
// selection is a different action with its own key.
async function regenerate(): Promise<void> {
  const out = await bulkRegenerate(ids.value);
  picked.value = new Set();
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

const rowTone = (job: JobSummary): Tone | undefined =>
  job.status === "failed" ? "bad" : job.status === "unknown" ? "warn" : undefined;
const barTone = (s: string): string => (s === "failed" ? "bad" : s === "done" ? "ok" : "info");
</script>

<template>
  <section class="jobs">
    <ListToolbar :loading="loading" :updated-at="updatedAt" @refresh="load()">
      <el-select
        :model-value="toSelect(status)"
        class="jobs__filter"
        aria-label="Status"
        @update:model-value="(v: string) => (status = fromSelect(v))"
      >
        <el-option v-for="s in statusOptions" :key="s" :label="s === ALL ? 'All statuses' : s" :value="s" />
      </el-select>
      <el-select
        :model-value="toSelect(keyId)"
        class="jobs__filter"
        aria-label="Key"
        @update:model-value="(v: string) => (keyId = fromSelect(v))"
      >
        <el-option label="All keys" :value="ALL" />
        <el-option v-for="k in keys" :key="k.id" :label="k.label" :value="k.id" />
      </el-select>
    </ListToolbar>

    <div class="jobs__bulk">
      <el-checkbox
        :model-value="allOnPage"
        :indeterminate="someOnPage"
        :disabled="!items.length"
        @change="(on) => (picked = setPage(items, Boolean(on)))"
        >Select all</el-checkbox
      >
      <span class="jobs__count">{{ ids.length }} selected</span>
      <el-button :disabled="!ids.length || rechecking" :loading="rechecking" @click="recheck"
        >Re-check selected</el-button
      >
      <RegenerateControl
        :action="bulkAction(ids)"
        label="Regenerate selected"
        :disabled="!ids.length"
        :confirm="regenerateConfirm"
        :perform="regenerate"
      />
    </div>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      :error="error"
      empty-text="No jobs"
      row-clickable
      :actions-width="96"
      :row-tone="rowTone"
      @retry="load()"
      @row-click="(job) => ui.openJob(job.id)"
    >
      <template #cell-select="{ row }">
        <el-checkbox
          :model-value="picked.has(row.id)"
          :aria-label="`Select job ${shortId(row.id)}`"
          @change="(on) => tick(row.id, Boolean(on))"
        />
      </template>
      <template #cell-progress="{ row }">
        <div
          class="bar"
          role="progressbar"
          aria-valuemin="0"
          aria-valuemax="4"
          :aria-valuenow="progressStep(row.status)"
          :aria-label="`Progress: ${row.status}`"
        >
          <div
            class="bar__fill"
            :class="`bar__fill--${barTone(row.status)}`"
            :style="{ width: `${progressStep(row.status) * 25}%` }"
          />
        </div>
      </template>
      <template #cell-title="{ row }">
        <div class="title" :title="row.title ?? ''">{{ row.title || "(untitled)" }}</div>
      </template>
      <template #cell-job="{ row }">
        <span class="mono">{{ shortId(row.id) }}</span>
        <div class="muted">{{ row.provider }}</div>
      </template>
      <template #cell-operation="{ row }">
        {{ row.operation }} · {{ row.model }}
        <StatusChip v-if="row.model_substituted" status="model differs" />
      </template>
      <template #cell-status="{ row }">
        <div class="chips">
          <StatusChip :status="row.status" />
          <!-- a refund is an outcome, not a warning: neutral -->
          <StatusChip v-if="isRefunded(row)" status="refunded" tone="neutral" />
        </div>
        <div v-if="row.error_code" class="error-code">{{ row.error_code }}</div>
      </template>
      <template #cell-created="{ row }">{{ fmtTime(row.created_ms) }}</template>
      <template #actions="{ row }">
        <el-button size="small" @click="ui.openJob(row.id)">Details</el-button>
      </template>
    </DataTable>

    <CursorPager :page="page" :has-next="hasNext" @prev="load(stack.prevIndex())" @next="load(stack.nextIndex())" />
  </section>
</template>

<style scoped>
.jobs__filter {
  width: 10rem;
}
.jobs__bulk {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.jobs__count {
  color: var(--rl-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.bar {
  width: 5rem;
  height: 6px;
  background: var(--rl-line);
  border-radius: 3px;
}
.bar__fill {
  height: 6px;
  border-radius: 3px;
}
.bar__fill--ok {
  background: var(--rl-ok);
}
.bar__fill--bad {
  background: var(--rl-bad);
}
.bar__fill--info {
  background: var(--rl-info);
}
.title {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
.muted {
  color: var(--rl-muted);
  font-size: 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
/* an error code is a fact, not a status: monospace and muted; only the chip carries colour */
.error-code {
  margin-top: 2px;
  color: var(--rl-muted);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
</style>
