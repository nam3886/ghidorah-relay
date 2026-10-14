<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { getJob, type JobDetail, renameJob } from "@/api/jobs-detail";
import { announceJobChange } from "@/components/jobs/job-changes";
import { shortId } from "@/components/jobs/job-text";
import JobActions from "@/components/jobs/JobActions.vue";
import JobDebugFiles from "@/components/jobs/JobDebugFiles.vue";
import JobMedia from "@/components/jobs/JobMedia.vue";
import type { CopyOutcome } from "@/components/shared/clipboard";
import CopyButton from "@/components/shared/CopyButton.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { toneOf } from "@/components/shared/status-tones";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { fmtTime } from "@/lib/format";
import { notify } from "@/lib/notify";
import { useUiStore } from "@/stores/ui";

// Mounted once in the shell; Jobs and Alerts open it through ui.openJob(id). Every string from the server (title,
// prompt, error, events, params, account names) is drawn as text. A later answer for a job that is no longer the open
// one is dropped.
const ui = useUiStore();
const visible = computed({
  get: () => ui.openJobId !== null,
  set: (open: boolean) => {
    if (!open) ui.closeJob();
  },
});

const job = ref<JobDetail | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const latest = useLatest();

async function load(id: string): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await getJob(id);
    if (!latest.isCurrent(ticket)) return;
    job.value = out;
    error.value = null;
  } catch (err) {
    if (!latest.isCurrent(ticket)) return;
    if (job.value) notify.error(errorText(err));
    else error.value = errorText(err);
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}

watch(
  () => ui.openJobId,
  (id) => {
    latest.invalidate();
    job.value = null;
    error.value = null;
    loading.value = false;
    if (id) void load(id);
  },
  { immediate: true },
);

// Re-reads the job that is open now (an action started on another one must not bring it back).
const refresh = (): void => {
  if (ui.openJobId) void load(ui.openJobId);
};

const heading = computed(() => `Job ${shortId(job.value?.id ?? ui.openJobId ?? "")}`);

const title = ref("");
watch([() => job.value?.id, () => job.value?.title], () => (title.value = job.value?.title ?? ""), { immediate: true });
const { busy: renaming, run } = useBusy();
const rename = () =>
  run(async () => {
    if (!job.value) return;
    await renameJob(job.value.id, title.value);
    announceJobChange();
    notify.success("Renamed");
    refresh();
  });

const promptBlock = ref<HTMLElement | null>(null);

// CopyButton already says what to do when no copy path worked; success gets the toast.
function onCopied(outcome: CopyOutcome): void {
  if (outcome === "clipboard" || outcome === "exec") notify.success("Prompt copied");
}

const conclusionTone = computed(() => (job.value ? toneOf(job.value.status) : "neutral"));

const technical = computed<[string, string][]>(() => {
  const j = job.value;
  if (!j) return [];
  const text = (v: unknown): string => (v === null || v === undefined ? "-" : String(v));
  return [
    ["Job id", text(j.id)],
    ["Provider / pool", `${j.provider} / ${text(j.pool)}`],
    ["Wallet", text(j.wallet_kind)],
    ["Key", text(j.key_label)],
    ["Account", text(j.account_label)],
    ["External ref", text(j.external_ref)],
    ["Attempts / claims", `${j.attempts} / ${j.claims}`],
    ["Account cost", text(j.account_cost)],
    ["Upstream price", text(j.upstream_price)],
    ["Upstream model", text(j.upstream_model)],
    ["Completion", text(j.completion)],
    ["Sent", fmtTime(j.sent_ms)],
    ["Updated", fmtTime(j.updated_ms)],
    ["Callback", j.callback.status ? `${j.callback.status} (${j.callback.attempts} attempts)` : "-"],
    ["Prompt frame version", text(j.frame_id)],
    ["Params", JSON.stringify(j.params)],
  ];
});
</script>

<template>
  <el-dialog v-model="visible" :title="heading" width="min(960px, 94vw)" destroy-on-close>
    <div v-if="error" class="job-error" role="alert">
      <span>{{ error }}</span>
      <el-button size="small" @click="refresh">Retry</el-button>
    </div>
    <el-skeleton v-else-if="!job" :rows="8" animated />
    <div v-else class="job" :class="{ 'is-refreshing': loading }" :aria-busy="loading">
      <div class="conclusion" :class="`conclusion--${conclusionTone}`" data-section="conclusion">
        <div>
          <StatusChip :status="job.status" />
          {{ job.operation }} · {{ job.model }} · net charged {{ job.net_charged }}
        </div>
        <div v-if="job.error_code">
          <strong>{{ job.error_code }}</strong
          ><template v-if="job.error">: {{ job.error }}</template>
        </div>
        <div v-if="job.warnings && job.warnings.length">warnings: {{ JSON.stringify(job.warnings) }}</div>
      </div>

      <section class="job-section" data-section="title">
        <h4>Title</h4>
        <div class="row">
          <el-input v-model="title" maxlength="2000" aria-label="Title" @keyup.enter="rename" />
          <el-button :disabled="renaming" @click="rename">Rename</el-button>
        </div>
      </section>

      <section class="job-section" data-section="prompt">
        <h4>Prompt</h4>
        <pre ref="promptBlock" class="block">{{ job.prompt }}</pre>
        <CopyButton :text="job.prompt" label="Copy prompt" :select-from="() => promptBlock" @copied="onCopied" />
      </section>

      <section class="job-section" data-section="actions">
        <h4>Actions</h4>
        <JobActions :job="job" @reload="refresh" />
      </section>

      <section class="job-section" data-section="results">
        <h4>Results</h4>
        <JobMedia :job="job" role="output" />
      </section>

      <section class="job-section" data-section="references">
        <h4>References</h4>
        <JobMedia :job="job" role="input" />
      </section>

      <section class="job-section" data-section="debug">
        <h4>Debug files</h4>
        <JobDebugFiles :job="job" />
      </section>

      <section class="job-section" data-section="timeline">
        <h4>Timeline</h4>
        <p v-if="!job.events.length" class="muted">No events</p>
        <ol v-else class="timeline">
          <li v-for="e in job.events" :key="e.id">
            <span class="muted">{{ fmtTime(e.at_ms) }}</span>
            <StatusChip :status="e.source" :label="e.source" :tone="e.stage === 'recovered' ? 'ok' : 'neutral'" />
            <strong>{{ e.stage }}</strong> {{ e.text }}
          </li>
        </ol>
      </section>

      <section class="job-section" data-section="technical">
        <h4>Technical</h4>
        <dl class="facts">
          <template v-for="[label, value] in technical" :key="label">
            <dt>{{ label }}</dt>
            <dd>{{ value }}</dd>
          </template>
        </dl>
        <p class="muted">Text sent upstream (the prompt in its frame, with this job's marker)</p>
        <pre class="block" data-field="sent-text">{{ job.sent_text === null ? "-" : job.sent_text }}</pre>
      </section>
    </div>
    <template #footer>
      <el-button @click="visible = false">Close</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.job {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.job.is-refreshing {
  opacity: 0.6;
}
.job-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  color: var(--rl-bad);
  background: var(--rl-bad-bg);
  border-radius: var(--el-border-radius-base, 6px);
}
.conclusion {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  font-size: 14px;
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.conclusion--ok {
  color: var(--rl-ok);
  background: var(--rl-ok-bg);
}
.conclusion--bad {
  color: var(--rl-bad);
  background: var(--rl-bad-bg);
}
.conclusion--warn,
.conclusion--info {
  color: var(--rl-warn);
  background: var(--rl-warn-bg);
}
.job-section h4 {
  margin: 0 0 8px;
  font-size: 14px;
}
.row {
  display: flex;
  gap: 8px;
}
.block {
  margin: 0 0 8px;
  padding: 8px;
  max-height: 10rem;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--rl-surface-2);
  border-radius: var(--el-border-radius-base, 6px);
}
.muted {
  margin: 0 0 4px;
  color: var(--rl-muted);
  font-size: 12px;
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  font-size: 12px;
  list-style: none;
}
.timeline li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.facts {
  display: grid;
  grid-template-columns: 11rem 1fr;
  gap: 4px 12px;
  margin: 0 0 8px;
  font-size: 12px;
}
.facts dt {
  color: var(--rl-muted);
}
.facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
