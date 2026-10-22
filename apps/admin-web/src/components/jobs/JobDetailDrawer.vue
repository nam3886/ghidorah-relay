<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { fetchDownload, getJob, type JobDetail, renameJob } from "@/api/jobs-detail";
import { saveBlob } from "@/components/jobs/blob-actions";
import { announceJobChange } from "@/components/jobs/job-changes";
import { isRunning } from "@/components/jobs/job-row";
import {
  fmtDayClock,
  fmtTook,
  isRefunded,
  jobParam,
  keyName,
  loadFailedText,
  newestFirst,
  plural,
  progressStep,
  shortId,
} from "@/components/jobs/job-text";
import JobActions from "@/components/jobs/JobActions.vue";
import JobCopy from "@/components/jobs/JobCopy.vue";
import JobDebugFiles from "@/components/jobs/JobDebugFiles.vue";
import {
  IconAlertCircle,
  IconAlertCircleJoined,
  IconAlertTriangle,
  IconCheck,
  IconDownload,
} from "@/components/shared/icons";
import JobMedia from "@/components/jobs/JobMedia.vue";
import JobTechnical from "@/components/jobs/JobTechnical.vue";
import JobTimeline from "@/components/jobs/JobTimeline.vue";
import type { CopyOutcome } from "@/components/shared/clipboard";
import DetailDrawer from "@/components/shared/DetailDrawer.vue";
import DrawerPanel from "@/components/shared/DrawerPanel.vue";
import DrawerTabs from "@/components/shared/DrawerTabs.vue";
import LedgerList, { type LedgerItem } from "@/components/shared/LedgerList.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useUiStore } from "@/stores/ui";

// Mounted once in the shell; Jobs and Alerts open it through ui.openJob(id). Every string from the server (title,
// prompt, error, events, params, account names) is drawn as text. A later answer for a job that is no longer the open
// one is dropped. The drawer's name is "Job <8-char id>"; its visible heading is the job's title.
// `backLabel`: what the phone sheet's Back button says, the page it goes back to (the shell passes it)
const props = withDefaults(defineProps<{ backLabel?: string }>(), { backLabel: "Back to Jobs" });
const ui = useUiStore();
const { isPhone } = useBreakpoint();
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
const tab = ref("overview");

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
    else error.value = loadFailedText(err, errorText(err), "the job");
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
    tab.value = "overview";
    if (id) void load(id);
  },
  { immediate: true },
);

// Re-reads the job that is open now (an action started on another one must not bring it back).
const refresh = (): void => {
  if (ui.openJobId) void load(ui.openJobId);
};

const name = computed(() => `Job ${shortId(job.value?.id ?? ui.openJobId ?? "")}`);
const heading = computed(() => (job.value ? job.value.title?.trim() || "(untitled)" : name.value));

const title = ref("");
watch([() => job.value?.id, () => job.value?.title], () => (title.value = job.value?.title ?? ""), { immediate: true });
const { busy: renaming, run: runRename } = useBusy();
const rename = () =>
  runRename(async () => {
    if (!job.value) return;
    await renameJob(job.value.id, title.value);
    announceJobChange();
    notify.success("Renamed");
    refresh();
  });

const promptBlock = ref<HTMLElement | null>(null);

// JobCopy already says what to do when no copy path worked; success gets the toast.
function onCopied(outcome: CopyOutcome): void {
  if (outcome === "clipboard" || outcome === "exec") notify.success("Prompt copied");
}

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "timeline", label: "Timeline" },
  { key: "technical", label: "Technical" },
];

const outputs = computed(() => job.value?.media.filter((m) => m.role === "output") ?? []);
const references = computed(() => job.value?.media.filter((m) => m.role === "input") ?? []);
// The detail answer has no `outputs` count of its own: the stored results are the count, the list's number a fallback.
const outputCount = computed(() => outputs.value.length || Number(param("count", "n")) || job.value?.outputs || 0);
const events = computed(() => newestFirst(job.value?.events ?? []));
const failed = computed(() => job.value?.status === "failed");
const done = computed(() => job.value?.status === "done");
// The action row (the phone's footer) holds Download (results), Re-check (a job that can be) or Regenerate… (a done
// job; a failed one has its Actions card): JobActions' rules. A job with none of them draws no row and no empty
// footer (a queued, submitting or fetching job).
const hasRow = computed(() => Boolean(outputs.value.length || job.value?.can_recheck || done.value));

const context = computed(() => {
  const j = job.value;
  if (!j) return "";
  // the count once the job has ended (a job on its way has no results to count); the phone sheet draws the line
  // without it (it has no room for it beside the id chip)
  const line = `${j.operation} · ${j.model}`;
  return isPhone.value || !(done.value || failed.value) ? line : `${line} · ${plural(outputCount.value, "output")}`;
});
// The header's chip: a job on its way shows its step instead of the dot, as the list does ("3/4 fetching").
const step = computed(() =>
  job.value && isRunning(job.value.status) ? `${progressStep(job.value.status)}/4` : undefined,
);

// "Delivered 4 images" + "net charged 1 point · 1 min 35 s".
const delivered = computed(() => {
  const j = job.value;
  if (!j) return { lead: "", rest: "" };
  const count = outputCount.value;
  const noun = j.media_type === "image" ? "image" : j.media_type === "video" ? "video" : "file";
  const took = j.created_ms && j.finished_ms ? ` · ${fmtTook(j.finished_ms - j.created_ms)}` : "";
  return { lead: `Delivered ${plural(count, noun)}`, rest: `· net charged ${plural(j.net_charged, "point")}${took}` };
});
// A job on its way, in the done banner's pattern: what it is doing, then its step (as the list's bar), what it has
// charged and how long it has run so far. "Fetching the result" + "· step 3 of 4 · charged 2 points · 4 min so far".
const onItsWay = computed(() => {
  const j = job.value;
  if (!j) return { lead: "", rest: "" };
  const provider = j.provider.charAt(0).toUpperCase() + j.provider.slice(1);
  const leads: Record<string, string> = {
    queued: "Waiting for an account",
    submitting: `Sending it to ${j.provider}`,
    waiting: `${provider} is making it`,
    fetching: "Fetching the result",
    unknown: `${provider} has not confirmed it yet`,
  };
  const facts = [
    isRunning(j.status) ? `step ${progressStep(j.status)} of 4` : "",
    j.net_charged ? `charged ${plural(j.net_charged, "point")}` : "nothing charged yet",
    j.created_ms ? `${fmtTook(Date.now() - j.created_ms)} so far` : "",
  ];
  return {
    lead: leads[j.status] ?? j.status.charAt(0).toUpperCase() + j.status.slice(1),
    rest: `· ${facts.filter(Boolean).join(" · ")}`,
  };
});

const asText = (v: unknown): string => (v === undefined || v === null || v === "" ? "" : String(v));
const param = (...names: string[]): string => jobParam(job.value?.params, ...names);

// The ledger of the Overview. A refund shows the price struck, an arrow and what was kept.
const summary = computed<LedgerItem[]>(() => {
  const j = job.value;
  if (!j) return [];
  const ratio = param("ratio", "aspect_ratio");
  const length =
    param("duration", "duration_s", "seconds") ||
    asText(outputs.value[0]?.duration_s && Math.round(outputs.value[0].duration_s));
  const count = param("count", "n") || String(outputCount.value);
  // a refund is the list's rule (a failed job that was charged and paid back); a lower net on another job is not
  const refunded = isRefunded(j);
  return [
    { label: "Operation", value: j.operation },
    { label: "Model", value: j.model_substituted ? `${j.model} (model differs)` : j.model, mono: true },
    j.media_type === "video"
      ? { label: "Media", value: [j.media_type, length ? `${length} s` : "", ratio].filter(Boolean).join(" · ") }
      : { label: "Ratio · count", value: [ratio, count].filter(Boolean).join(" · ") },
    refunded
      ? { label: "Points", was: String(j.price), value: j.net_charged, note: "refunded" }
      : {
          label: "Points",
          value: j.net_charged === j.price ? `${j.net_charged} charged · 0 refunded` : `${j.net_charged} charged`,
        },
    { label: "Account", value: asText(j.account_label) || "—", mono: true },
    // named as the list names it: the label, else the prefix, else the start of the id
    { label: "Key", value: keyName({ id: j.key_id, label: j.key_label, prefix: j.key_prefix }) },
    { label: "Created", value: fmtDayClock(j.created_ms), mono: true },
    { label: "Finished", value: fmtDayClock(j.finished_ms), mono: true },
  ];
});

// Download all: every result, one file each, named job-<id8>-<idx>.
const { busy: downloading, run: runDownload } = useBusy();
const downloadAll = () =>
  runDownload(async () => {
    if (!job.value) return;
    for (const m of outputs.value)
      saveBlob(await fetchDownload(job.value.id, m.idx), `job-${shortId(job.value.id)}-${m.idx}`);
  });
const downloadLabel = computed(() => (outputs.value.length > 1 ? "Download all" : "Download"));
</script>

<template>
  <DetailDrawer v-model="visible" :title="name" :heading="heading" :back-label="props.backLabel" testid="job-drawer">
    <template v-if="job" #chips>
      <StatusChip :status="job.status" :step="step" />
      <span>{{ context }}</span>
      <JobCopy variant="id" :text="shortId(job.id)" :label="`Copy job id ${shortId(job.id)}`" />
    </template>
    <template #tabs>
      <DrawerTabs v-model="tab" :tabs="TABS" id-prefix="job" label="Job sections" />
    </template>

    <!-- a failed read and the loading bars take the list's drawn patterns (B-States: error, loading) -->
    <div v-if="error" class="jd-error" role="alert">
      <IconAlertCircle class="jd-error__icon" :size="18" />
      <span class="jd-error__text">{{ error }}</span>
      <button type="button" class="jd-retry" @click="refresh">Retry</button>
    </div>
    <div v-else-if="!job" class="jd-skeleton" data-state="loading" aria-busy="true" aria-label="Loading the job">
      <span v-for="n in 8" :key="n" class="jd-skeleton__bar" />
    </div>

    <div v-else class="jd" :class="{ 'is-refreshing': loading }" :aria-busy="loading">
      <DrawerPanel id-prefix="job" name="overview" :selected="tab" class="jd-panel">
        <template v-if="tab === 'overview'">
          <div v-if="done" class="jd-banner jd-banner--ok" data-section="conclusion">
            <IconCheck :size="16" :stroke="2.2" />
            <!-- the phone wraps the two as one line of text (P-JobDetail); the desktop sets them 10 px apart -->
            <span v-if="isPhone"
              ><strong>{{ delivered.lead }}</strong> <span class="jd-num jd-muted">{{ delivered.rest }}</span></span
            >
            <template v-else>
              <strong>{{ delivered.lead }}</strong>
              <span class="jd-num jd-muted">{{ delivered.rest }}</span>
            </template>
          </div>
          <div v-else-if="failed" class="jd-banner jd-banner--bad" role="alert" data-section="conclusion">
            <IconAlertCircleJoined :size="16" :stroke="2.2" />
            <span class="jd-banner__text"
              ><strong
                >Failed<template v-if="job.error_code"> · {{ job.error_code }}</template></strong
              ><template v-if="job.error"> — {{ job.error }}</template></span
            >
          </div>
          <div v-else class="jd-banner jd-banner--plain" data-section="conclusion">
            <span v-if="isPhone"
              ><strong>{{ onItsWay.lead }}</strong> <span class="jd-num jd-muted">{{ onItsWay.rest }}</span></span
            >
            <template v-else>
              <strong>{{ onItsWay.lead }}</strong>
              <span class="jd-num jd-muted">{{ onItsWay.rest }}</span>
            </template>
          </div>
          <div v-if="job.warnings && job.warnings.length" class="jd-banner jd-banner--warn" data-section="warnings">
            <IconAlertTriangle :size="16" :stroke="2.2" />
            <span class="jd-banner__text">warnings: {{ JSON.stringify(job.warnings) }}</span>
          </div>

          <section v-if="outputs.length" data-section="results" aria-label="Results">
            <JobMedia :job="job" role="output" />
          </section>
          <section v-if="references.length" class="jd-section" data-section="references" aria-labelledby="jd-refs">
            <h3 id="jd-refs" class="jd-h">
              References <span class="jd-num jd-muted">· {{ references.length }}</span>
            </h3>
            <JobMedia :job="job" role="input" />
          </section>

          <div v-if="!failed && !isPhone && hasRow" data-section="actions">
            <JobActions :job="job" zone="row" @reload="refresh">
              <template v-if="outputs.length" #before>
                <button type="button" class="jd-primary" :disabled="downloading" @click="downloadAll">
                  <IconDownload :size="15" :stroke="2" />{{ downloadLabel }}
                </button>
              </template>
            </JobActions>
          </div>

          <div class="jd-summary" data-section="summary"><LedgerList :items="summary" variant="drawer" /></div>

          <div v-if="failed" data-section="actions">
            <JobActions :job="job" zone="card" @reload="refresh" />
          </div>

          <section class="jd-section" data-section="prompt" aria-labelledby="jd-prompt">
            <div class="jd-prompt__head">
              <h3 id="jd-prompt" class="jd-h">Prompt</h3>
              <JobCopy
                variant="prompt"
                :text="job.prompt"
                :label="isPhone ? 'Copy' : 'Copy prompt'"
                :select-from="() => promptBlock"
                @copied="onCopied"
              />
            </div>
            <p ref="promptBlock" class="jd-prompt__text">{{ job.prompt }}</p>
          </section>
        </template>
      </DrawerPanel>

      <DrawerPanel id-prefix="job" name="timeline" :selected="tab" class="jd-panel">
        <section v-if="tab === 'timeline'" data-section="timeline" aria-label="Timeline">
          <JobTimeline :events="events" />
        </section>
      </DrawerPanel>

      <DrawerPanel id-prefix="job" name="technical" :selected="tab" class="jd-panel">
        <template v-if="tab === 'technical'">
          <section data-section="technical" aria-label="Technical">
            <JobTechnical :job="job" :phone="isPhone" />
          </section>
          <section class="jd-section" data-section="title" aria-labelledby="jd-title">
            <h3 id="jd-title" class="jd-h">Title</h3>
            <div class="jd-line">
              <input
                v-model="title"
                class="jd-input"
                type="text"
                maxlength="2000"
                placeholder="(untitled)"
                aria-label="Title"
                @keyup.enter="rename"
              />
              <button type="button" class="jd-btn" :disabled="renaming" @click="rename">Rename</button>
            </div>
          </section>
          <section data-section="more-actions" aria-label="More actions">
            <JobActions :job="job" zone="more" @reload="refresh" />
          </section>
          <section class="jd-section" data-section="debug" aria-labelledby="jd-debug">
            <h3 id="jd-debug" class="jd-h">Debug files</h3>
            <JobDebugFiles :job="job" />
          </section>
          <section class="jd-section" data-section="sent" aria-labelledby="jd-sent">
            <h3 id="jd-sent" class="jd-h">Text sent upstream</h3>
            <p class="jd-muted jd-note">The prompt in its frame, with this job's marker.</p>
            <pre class="jd-pre" data-field="sent-text">{{ job.sent_text === null ? "—" : job.sent_text }}</pre>
          </section>
        </template>
      </DrawerPanel>
    </div>

    <template v-if="job && isPhone && !failed && hasRow" #footer>
      <JobActions :job="job" zone="row" @reload="refresh">
        <template v-if="outputs.length" #before>
          <button type="button" class="jd-primary jd-primary--phone" :disabled="downloading" @click="downloadAll">
            <IconDownload :size="16" :stroke="2" />{{ downloadLabel }}
          </button>
        </template>
      </JobActions>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.jd {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.jd-summary :deep(.ledger) {
  /* the board sets its rows in the body line height (20), so a row is 28 apart */
  line-height: 20px;
}
.jd-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.jd.is-refreshing {
  opacity: 0.6;
}
.jd-num {
  font-variant-numeric: tabular-nums;
}
.jd-muted {
  color: var(--rl-muted);
}
.jd-error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
}
.jd-error__icon {
  flex: none;
  color: var(--rl-bad);
}
.jd-error__text {
  flex: 1;
  min-width: 220px;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
/* Retry: the danger outline, 32 high, 13/500, raised on hover (B-States, error) */
.jd-retry {
  box-sizing: border-box;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--rl-bad);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-bad);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.jd-retry:hover {
  background: var(--rl-raised);
}
/* the loading bars: raised, 12 high, radius 6, no animation, at the list skeleton's widths (B-States, loading) */
.jd-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.jd-skeleton__bar {
  height: 12px;
  border-radius: 6px;
  background: var(--rl-raised);
}
.jd-skeleton__bar:nth-child(5n + 1) {
  width: 62%;
}
.jd-skeleton__bar:nth-child(5n + 2) {
  width: 48%;
}
.jd-skeleton__bar:nth-child(5n + 3) {
  width: 71%;
}
.jd-skeleton__bar:nth-child(5n + 4) {
  width: 39%;
}
.jd-skeleton__bar:nth-child(5n) {
  width: 56%;
}
.jd-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
}
.jd-banner--ok {
  background: var(--rl-ok-bg);
}
.jd-banner--ok strong {
  flex: none;
}
.jd-banner--ok svg {
  color: var(--rl-ok);
}
.jd-banner--bad {
  align-items: flex-start;
  padding: 12px 14px;
  background: var(--rl-bad-bg);
  line-height: 20px;
}
.jd-banner--bad svg {
  flex: none;
  margin-top: 2px;
  color: var(--rl-bad);
}
.jd-banner--bad strong {
  color: var(--rl-bad);
}
.jd-banner--plain {
  background: var(--rl-neutral-bg);
}
.jd-banner strong {
  font-weight: 600;
}
.jd-banner__text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.jd-banner--warn {
  background: var(--rl-warn-bg);
}
.jd-banner--warn svg {
  flex: none;
  color: var(--rl-warn);
}
.jd-banner--warn .jd-banner__text {
  color: var(--rl-fg);
}
.jd-warn {
  margin: 0;
  font-size: 12px;
  color: var(--rl-warn);
}
.jd-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.jd-h {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}
.jd-h .jd-muted {
  font-weight: 400;
}
.jd-prompt__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.jd-prompt__text {
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-raised);
  line-height: 22px;
  /* the prompt's own line breaks are kept; a line wraps as the canvas's paragraph does (no space hangs at its end) */
  white-space: pre-line;
  overflow-wrap: anywhere;
}
.jd-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.jd-primary:hover {
  background: var(--rl-primary-hover);
}
.jd-primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.jd-primary--phone {
  flex: 1;
  height: 44px;
  font-size: 14px;
}
.jd-line {
  display: flex;
  gap: 8px;
}
.jd-input {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 12px;
  box-sizing: border-box;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-family: inherit;
  font-size: 13px;
}
.jd-btn {
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
.jd-btn:hover {
  background: var(--rl-raised);
}
.jd-note {
  margin: 0;
  font-size: 12px;
}
.jd-pre {
  margin: 0;
  padding: 12px 14px;
  max-height: 14rem;
  overflow: auto;
  border-radius: 12px;
  background: var(--rl-raised);
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 767.98px) {
  .jd,
  .jd-panel {
    gap: 16px;
  }
  .jd-banner--ok {
    align-items: flex-start;
  }
  .jd-banner--ok svg {
    flex: none;
    margin-top: 2px;
  }
}
</style>
