<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { fetchDownload, getJob, type JobDetail, renameJob } from "@/api/jobs-detail";
import { saveBlob } from "@/components/jobs/blob-actions";
import { announceJobChange } from "@/components/jobs/job-changes";
import { isRunning, statusPill } from "@/components/jobs/job-row";
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
import JobLedger, { type LedgerRow } from "@/components/jobs/JobLedger.vue";
import JobMedia from "@/components/jobs/JobMedia.vue";
import JobRaw from "@/components/jobs/JobRaw.vue";
import JobTechnical from "@/components/jobs/JobTechnical.vue";
import JobTimeline from "@/components/jobs/JobTimeline.vue";
import { copyText } from "@/components/shared/clipboard";
import { drawerPanelId, drawerTabId } from "@/components/shared/drawer-tab-ids";
import IconButton from "@/components/shared/IconButton.vue";
import { IconAlertCircleJoined, IconAlertTriangle, IconCheck } from "@/components/shared/icons";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { providerClass } from "@/lib/providers";
import { safeMediaUrl } from "@/lib/safe-media-url";
import { useUiStore } from "@/stores/ui";

// The job detail (JobDetail.dc.html, JobDetailTabs, JobDetailTablet, JobDetailPhone), mounted once in the shell; Jobs
// and Alerts open it through ui.openJob(id). A right-hand drawer on a desktop, the width less a 48 px gutter on a
// tablet, the whole screen on a phone (Back only, the sections as a segmented control, the facts in cards, Regenerate…
// and Download all at its foot). Its head: Back, the title, Close · Esc; the status pill, the provider chip, one mono
// line and the id chip; the Overview, Timeline (with its count) and Technical tabs. Keys while it is open: D downloads
// every result, R re-checks the job. Every string from the server (title, prompt, error, events, params, account
// names) is drawn as text. A later answer for a job that is no longer the open one is dropped.
// `backLabel`: what Back says, the page it goes back to (the shell passes it)
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

const outputs = computed(() => job.value?.media.filter((m) => m.role === "output") ?? []);
const references = computed(() => job.value?.media.filter((m) => m.role === "input") ?? []);
// The detail answer has no `outputs` count of its own: the stored results are the count, the list's number a fallback.
const outputCount = computed(() => outputs.value.length || Number(param("count", "n")) || job.value?.outputs || 0);
const events = computed(() => newestFirst(job.value?.events ?? []));
const failed = computed(() => job.value?.status === "failed");
const done = computed(() => job.value?.status === "done");
const finished = computed(() => done.value || failed.value);
const pill = computed(() => (job.value ? statusPill(job.value.status) : { text: "", tone: "neutral" }));

const TABS = computed(() => [
  { key: "overview", label: "Overview", count: 0 },
  { key: "timeline", label: "Timeline", count: events.value.length },
  { key: "technical", label: "Technical", count: 0 },
]);
function moveTab(from: number, by: number, event: KeyboardEvent): void {
  const next = TABS.value[(from + by + TABS.value.length) % TABS.value.length]!;
  tab.value = next.key;
  const list = (event.currentTarget as HTMLElement).parentElement;
  list?.querySelectorAll<HTMLElement>("[role='tab']")[TABS.value.indexOf(next)]?.focus();
}

// "t2v · seedance-2.0 · 2 outputs": the count once the job has ended
const context = computed(() => {
  const j = job.value;
  if (!j) return "";
  const line = `${j.operation} · ${j.model}`;
  return finished.value ? `${line} · ${plural(outputCount.value, "output")}` : line;
});
// the phone's one line under the title: "Done · dola · t2v · seedance-2.0 · 9c41e7b2"
const phoneSub = computed(() => {
  const j = job.value;
  return j ? [pill.value.text, j.provider, j.operation, j.model, shortId(j.id)].join(" · ") : "";
});

// "Delivered 4 images" + "· net charged 1 point · 1 min 35 s".
const delivered = computed(() => {
  const j = job.value;
  if (!j) return { lead: "", rest: "" };
  const noun = j.media_type === "image" ? "image" : j.media_type === "video" ? "video" : "file";
  const took = j.created_ms && j.finished_ms ? ` · ${fmtTook(j.finished_ms - j.created_ms)}` : "";
  return {
    lead: `Delivered ${plural(outputCount.value, noun)}`,
    rest: `· net charged ${plural(j.net_charged, "point")}${took}`,
  };
});
// A job on its way, in the done banner's pattern: what it is doing, then its step, what it has charged and how long
// it has run so far. "Fetching the result" + "· step 3 of 4 · charged 2 points · 4 min so far".
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

// Money (JobDetail): Charged, Refunded, Upstream cost, Margin. The upstream cost is what the upstream took (else the
// expected cost of a done job); the margin is the server's net points less that cost, a credit or a use counted as a
// point (Settings says the same). A cost the server does not know shows the dash.
const money = computed(() => {
  const j = job.value;
  if (!j) return [];
  const back = Math.max(0, j.price - j.net_charged);
  const cost = j.upstream_cost ?? null;
  const margin = j.margin ?? null;
  const seconds =
    j.media_type === "video"
      ? param("duration", "duration_s", "seconds") ||
        asText(outputs.value[0]?.duration_s && Math.round(outputs.value[0].duration_s))
      : "";
  return [
    // "seedance-2.0 · 10 s": the model and, for a video, its length
    {
      label: "Charged",
      value: `${j.price} pts`,
      note: [j.model, seconds ? `${seconds} s` : ""].filter(Boolean).join(" · "),
      tone: "",
    },
    {
      label: "Refunded",
      value: `${back} pts`,
      note: isRefunded(j) ? "refunded" : back ? "partly refunded" : "nothing failed",
      tone: "",
    },
    {
      label: "Upstream cost",
      value: cost === null ? "—" : plural(cost, "credit"),
      note: cost === null ? "not known yet" : [j.provider, j.pool, j.pool ? "pool" : ""].filter(Boolean).join(" "),
      tone: "",
    },
    {
      label: "Margin",
      value: margin === null ? "—" : `${margin > 0 ? "+" : margin < 0 ? "−" : ""}${Math.abs(margin)} pts`,
      note: margin === null ? "needs the upstream cost" : "after upstream",
      tone: margin === null ? "" : margin < 0 ? "bad" : "ok",
    },
  ];
});
// the phone's Money card (JobDetailPhone) names the pool's unit in the value: "1 video credit"
const moneyRows = computed<LedgerRow[]>(() =>
  money.value.map((m) => ({
    label: m.label,
    value:
      m.label === "Upstream cost" && job.value?.pool && job.value.upstream_cost != null
        ? plural(job.value.upstream_cost, `${job.value.pool} credit`)
        : m.value,
    tone: (m.tone || undefined) as LedgerRow["tone"],
  })),
);

// The facts of the Overview (the ledger under Money).
const facts = computed<LedgerRow[]>(() => {
  const j = job.value;
  if (!j) return [];
  const ratio = param("ratio", "aspect_ratio");
  const length =
    param("duration", "duration_s", "seconds") ||
    asText(outputs.value[0]?.duration_s && Math.round(outputs.value[0].duration_s));
  const count = param("count", "n") || String(outputCount.value);
  const media =
    j.media_type === "video"
      ? [j.media_type, length ? `${length} s` : "", ratio].filter(Boolean).join(" · ")
      : [j.media_type, ratio, count ? `×${count}` : ""].filter(Boolean).join(" · ");
  const callback = j.callback.status ? [j.callback.status, plural(j.callback.attempts ?? 0, "try")].join(" · ") : "";
  const rows: LedgerRow[] = [
    { label: "Operation", value: j.operation },
    { label: "Model", value: j.model_substituted ? `${j.model} (model differs)` : j.model, mono: true },
    { label: "Media", value: media },
    { label: "Account", value: asText(j.account_label) || "—", mono: true },
    { label: "Proxy", value: asText(j.proxy) || "—", mono: true },
    // named as the list names it: the label, else the prefix, else the start of the id
    {
      label: "Key",
      value: [
        keyName({ id: j.key_id, label: j.key_label, prefix: j.key_prefix }),
        j.key_label?.trim() ? j.key_prefix : "",
      ]
        .filter(Boolean)
        .join(" · "),
    },
    { label: "Provider task id", value: asText(j.provider_task_id) || "—", mono: true },
    { label: "Conversation", value: asText(j.conversation) || "—", mono: true },
    { label: "Prompt frame", value: asText(j.frame_id) && `${j.media_type} v${j.frame_id}`, mono: true },
    { label: "Webhook", value: callback },
    { label: "Created", value: fmtDayClock(j.created_ms), mono: true },
    { label: "Finished", value: fmtDayClock(j.finished_ms), mono: true },
  ];
  return rows.filter((r) => r.value !== "");
});
// Diagnostics (JobDetailTabs): what the provider recorded about this job (its own state, redacted by the server), one
// row a field named in words ("send_path" reads "Send path"), a value that is not text as compact JSON.
const diagnostics = computed<LedgerRow[]>(() =>
  Object.entries(job.value?.diagnostics ?? {}).map(([key, v]) => ({
    label: (key.charAt(0).toUpperCase() + key.slice(1)).replace(/_/g, " "),
    value: typeof v === "string" ? v : JSON.stringify(v),
    mono: true,
  })),
);
// the phone's Details card: the facts that are not in its head line
// The phone's Details card (JobDetailPhone): Model, Media, Account, Key (its name), Task id, Finished.
const phoneFacts = computed<LedgerRow[]>(() => {
  const j = job.value;
  if (!j) return [];
  const pick = (label: string): LedgerRow | undefined => facts.value.find((r) => r.label === label);
  const rows: (LedgerRow | undefined)[] = [
    pick("Model"),
    pick("Media"),
    pick("Account"),
    { label: "Key", value: keyName({ id: j.key_id, prefix: j.key_prefix, label: j.key_label }) },
    j.provider_task_id ? { label: "Task id", value: j.provider_task_id, mono: true } : undefined,
    pick("Finished"),
  ];
  return rows.filter((r): r is LedgerRow => !!r);
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

// Copy share link: the same-origin /m/ link of every result, one a line.
const links = computed(() =>
  outputs.value
    .map((m) => safeMediaUrl(m.url))
    .filter((u): u is string => !!u)
    .map((u) => new URL(u, window.location.origin).href),
);
async function copy(text: string, what: string): Promise<void> {
  const outcome = await copyText(text);
  if (outcome === "clipboard" || outcome === "exec") notify.success(`${what} copied`);
  else notify.error("Could not copy: select the text and copy it by hand");
}

// D and R: only while the drawer is the top layer and no field has the keys
const actions = ref<{ recheck: () => unknown } | null>(null);
function onKey(event: KeyboardEvent): void {
  if (!visible.value || !job.value || event.ctrlKey || event.metaKey || event.altKey) return;
  const el = event.target instanceof Element ? event.target : null;
  if (el?.closest("input, textarea, select, [contenteditable=true]")) return;
  if (document.querySelector(".el-dialog.rl-viewer, .el-message-box")) return;
  const k = event.key.toLowerCase();
  if (k === "d" && outputs.value.length) void downloadAll();
  else if (k === "r") actions.value?.recheck();
}
onMounted(() => document.addEventListener("keydown", onKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));
</script>

<template>
  <SideDrawer
    v-model="visible"
    :title="heading"
    :back-label="props.backLabel"
    testid="job-drawer"
    panel-class="rl-job-drawer"
    sheet
    @back="visible = false"
  >
    <template v-if="job" #sub>
      <span v-if="isPhone" class="jd-phone-sub">{{ phoneSub }}</span>
      <template v-else>
        <span class="jd-pill" :class="`tone-${pill.tone}`">{{ pill.text }}</span>
        <span class="jd-chip" :class="providerClass(job.provider)">{{ job.provider }}</span>
        <span class="mono jd-help">{{ context }}</span>
        <JobCopy :text="shortId(job.id)" :label="`Copy job id ${shortId(job.id)}`" />
      </template>
    </template>
    <template v-if="!isPhone" #tabs>
      <div class="jd-tabs" role="tablist" aria-label="Job sections">
        <button
          v-for="(t, i) in TABS"
          :id="drawerTabId('job', t.key)"
          :key="t.key"
          type="button"
          role="tab"
          class="jd-tab"
          :class="{ 'is-on': tab === t.key }"
          :aria-selected="tab === t.key"
          :aria-controls="drawerPanelId('job', t.key)"
          :tabindex="tab === t.key ? 0 : -1"
          @click="tab = t.key"
          @keydown.right.prevent="moveTab(i, 1, $event)"
          @keydown.left.prevent="moveTab(i, -1, $event)"
        >
          {{ t.label }}<span v-if="t.count" class="num jd-tab__count">{{ t.count }}</span>
        </button>
      </div>
    </template>

    <!-- a failed read and the loading bars take the list's patterns -->
    <div v-if="error" class="jd-error" role="alert">
      <IconAlertCircleJoined :size="18" :stroke="2" />
      <span class="jd-error__text">{{ error }}</span>
      <button type="button" class="jd-btn" @click="refresh">Retry</button>
    </div>
    <div v-else-if="!job" class="jd-skeleton" data-state="loading" aria-busy="true" aria-label="Loading the job">
      <span v-for="n in 8" :key="n" class="jd-skeleton__bar" />
    </div>

    <div v-else class="jd" :class="{ 'is-refreshing': loading, 'is-phone': isPhone }" :aria-busy="loading">
      <!-- a phone: the sections as a segmented control -->
      <span v-if="isPhone" class="jd-seg__label" aria-hidden="true">Job sections</span>
      <div v-if="isPhone" class="jd-seg" role="tablist" aria-label="Job sections">
        <button
          v-for="(t, i) in TABS"
          :id="drawerTabId('job', t.key)"
          :key="t.key"
          type="button"
          role="tab"
          class="jd-seg__btn"
          :class="{ 'is-on': tab === t.key }"
          :aria-selected="tab === t.key"
          :aria-controls="drawerPanelId('job', t.key)"
          :tabindex="tab === t.key ? 0 : -1"
          @click="tab = t.key"
          @keydown.right.prevent="moveTab(i, 1, $event)"
          @keydown.left.prevent="moveTab(i, -1, $event)"
        >
          {{ t.label }}
        </button>
      </div>

      <div
        v-if="tab === 'overview'"
        :id="drawerPanelId('job', 'overview')"
        role="tabpanel"
        :aria-labelledby="drawerTabId('job', 'overview')"
        class="jd-panel"
      >
        <div v-if="done" class="jd-banner is-ok" role="status" data-section="conclusion">
          <IconCheck v-if="!isPhone" :size="16" :stroke="2.4" />
          <span
            ><b>{{ delivered.lead }}</b> <span class="num">{{ delivered.rest }}</span></span
          >
        </div>
        <div v-else-if="failed" class="jd-banner is-bad" role="alert" data-section="conclusion">
          <IconAlertCircleJoined :size="16" :stroke="2.2" />
          <span class="jd-banner__text"
            ><b
              >Failed<template v-if="job.error_code"> · {{ job.error_code }}</template></b
            ><template v-if="job.error"> — {{ job.error }}</template></span
          >
        </div>
        <div v-else class="jd-banner is-plain" role="status" data-section="conclusion">
          <span
            ><b>{{ onItsWay.lead }}</b> <span class="num">{{ onItsWay.rest }}</span></span
          >
        </div>
        <div v-if="job.warnings && job.warnings.length" class="jd-banner is-warn" data-section="warnings">
          <IconAlertTriangle :size="16" :stroke="2.2" />
          <span class="jd-banner__text">warnings: {{ JSON.stringify(job.warnings) }}</span>
        </div>

        <section v-if="outputs.length" class="jd-section" data-section="results" aria-labelledby="jd-res">
          <h3 id="jd-res" class="jd-h" :class="{ 'sr-only': isPhone }">
            Results <span class="num jd-h__n">· {{ outputs.length }}</span>
          </h3>
          <JobMedia :job="job" role="output" />
        </section>
        <section v-if="references.length" class="jd-section" data-section="references" aria-labelledby="jd-refs">
          <h3 id="jd-refs" class="jd-h">
            References <span class="num jd-h__n">· {{ references.length }}</span>
          </h3>
          <JobMedia :job="job" role="input" />
        </section>

        <div
          v-if="(outputs.length && !isPhone) || job.can_recheck || (finished && !isPhone) || links.length"
          data-section="actions"
        >
          <JobActions ref="actions" :job="job" zone="row" :with-regenerate="!isPhone" @reload="refresh">
            <template v-if="outputs.length && !isPhone" #before>
              <IconButton
                icon="download"
                :label="downloadLabel"
                shortcut="D"
                :disabled="downloading"
                @click="downloadAll"
              />
            </template>
            <template v-if="links.length" #after>
              <IconButton icon="link" label="Copy share link" @click="copy(links.join('\n'), 'Share link')" />
            </template>
          </JobActions>
        </div>

        <section v-if="isPhone" class="jd-card" data-section="money" aria-labelledby="jd-money">
          <h3 id="jd-money" class="jd-h">Money</h3>
          <JobLedger :items="moneyRows" stacked />
        </section>
        <section v-else class="jd-money" data-section="money" aria-labelledby="jd-money">
          <h3 id="jd-money" class="jd-h">Money</h3>
          <div class="jd-money__grid">
            <div v-for="m in money" :key="m.label" class="jd-money__cell">
              <span class="jd-help">{{ m.label }}</span>
              <span class="num jd-money__value" :class="m.tone && `is-${m.tone}`">{{ m.value }}</span>
              <span class="num jd-help">{{ m.note }}</span>
            </div>
          </div>
        </section>

        <section v-if="isPhone" class="jd-card" data-section="summary" aria-labelledby="jd-details">
          <h3 id="jd-details" class="jd-h">Details</h3>
          <JobLedger :items="phoneFacts" stacked />
        </section>
        <div v-else data-section="summary"><JobLedger :items="facts" /></div>

        <section class="jd-section" :class="{ 'jd-card': isPhone }" data-section="prompt" aria-labelledby="jd-prompt">
          <div class="jd-prompt__head">
            <h3 id="jd-prompt" class="jd-h">Prompt</h3>
            <IconButton icon="copy" label="Copy prompt" placement="top-end" @click="copy(job.prompt, 'Prompt')" />
          </div>
          <p class="jd-prompt__text">{{ job.prompt }}</p>
        </section>

        <section
          class="jd-section"
          :class="{ 'jd-card': isPhone }"
          data-section="raw"
          aria-label="Raw request and response"
        >
          <JobRaw
            title="Raw request"
            :raw="job.raw_request"
            empty="The request log kept no body for this job."
            @copy="(t: string) => copy(t, 'Raw request')"
          />
          <JobRaw
            title="Raw response"
            :raw="job.raw_response"
            empty="No answer to show."
            @copy="(t: string) => copy(t, 'Raw response')"
          />
        </section>

        <section v-if="isPhone && events.length" class="jd-card" data-section="steps" aria-labelledby="jd-steps">
          <h3 id="jd-steps" class="jd-h">Latest steps</h3>
          <JobTimeline :events="events.slice(0, 4)" compact />
        </section>
      </div>

      <div
        v-else-if="tab === 'timeline'"
        :id="drawerPanelId('job', 'timeline')"
        role="tabpanel"
        :aria-labelledby="drawerTabId('job', 'timeline')"
        class="jd-panel"
      >
        <section data-section="timeline" aria-label="Timeline">
          <JobTimeline :events="events" />
        </section>
      </div>

      <div
        v-else
        :id="drawerPanelId('job', 'technical')"
        role="tabpanel"
        :aria-labelledby="drawerTabId('job', 'technical')"
        class="jd-panel"
      >
        <section data-section="technical" aria-label="Technical">
          <JobTechnical :job="job" :phone="isPhone" />
        </section>
        <section class="jd-section" data-section="diagnostics" aria-labelledby="jd-diag">
          <h3 id="jd-diag" class="jd-h">Diagnostics</h3>
          <JobLedger
            v-if="diagnostics.length"
            :items="diagnostics"
            wide
            small
            :stacked="isPhone"
            testid="job-diagnostics"
          />
          <span v-else class="jd-help">{{ job.provider }} recorded nothing for this job.</span>
        </section>
        <section class="jd-section" data-section="title" aria-labelledby="jd-title">
          <label id="jd-title" for="jd-title-input" class="jd-h">Title</label>
          <div class="jd-line">
            <input
              id="jd-title-input"
              v-model="title"
              class="jd-input"
              type="text"
              maxlength="2000"
              placeholder="(untitled)"
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
          <span class="jd-help">The prompt in its frame, with this job's marker.</span>
          <pre class="jd-pre" data-field="sent-text">{{ job.sent_text === null ? "—" : job.sent_text }}</pre>
        </section>
      </div>
    </div>

    <template v-if="job && isPhone && (finished || outputs.length)" #footer>
      <JobActions :job="job" zone="footer" @reload="refresh" />
      <button v-if="outputs.length" type="button" class="jd-primary" :disabled="downloading" @click="downloadAll">
        {{ downloadLabel }}
      </button>
    </template>
  </SideDrawer>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.jd-help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.jd-phone-sub {
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.jd-pill {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.tone-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.tone-neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.tone-ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.jd-chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  background: var(--ps);
  color: var(--pi);
  font-size: 12px;
  font-weight: 700;
}

/* the tabs at the foot of the head */
.jd-tabs {
  display: flex;
  gap: 4px;
}
.jd-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  margin-bottom: -1px;
  padding: 0 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.jd-tab.is-on {
  border-bottom-color: var(--rl-brand);
  color: var(--rl-fg);
  font-weight: 700;
}
.jd-tab:focus-visible,
.jd-seg__btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: -2px;
}
.jd-tab__count {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--rl-track);
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 600;
}
.jd-seg__label {
  margin-bottom: -6px;
  font-size: 13px;
  font-weight: 700;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.jd-seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 12px;
  background: var(--rl-track);
}
.jd-seg__btn {
  flex: 1;
  min-width: 0;
  height: 38px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.jd-seg__btn.is-on {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.1);
}

.jd,
.jd-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.jd.is-phone,
.jd.is-phone .jd-panel {
  gap: 14px;
}
.jd.is-refreshing {
  opacity: 0.6;
}
.jd-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.jd-h {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.jd-h__n {
  font-weight: 600;
  color: var(--rl-faint);
}
.jd-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  line-height: 20px;
}
.jd-banner svg {
  flex: none;
}
.jd-banner.is-ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.jd-banner.is-bad {
  align-items: flex-start;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.jd-banner.is-bad svg {
  margin-top: 2px;
}
.jd-banner.is-plain {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.jd-banner.is-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.jd-banner__text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.jd-money {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
}
.jd-money__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.jd-money__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.jd-money__value {
  font-size: 20px;
  line-height: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.jd-money__value.is-ok {
  color: var(--rl-ok);
}
.jd-money__value.is-bad {
  color: var(--rl-bad);
}
.jd-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.jd-prompt__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.jd-prompt__text {
  margin: 0;
  padding: 12px 14px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
  color: var(--rl-body);
  line-height: 22px;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
.jd-card .jd-prompt__text {
  padding: 0;
  border: 0;
  background: transparent;
  line-height: 1.55;
}
.jd-line {
  display: flex;
  gap: 8px;
}
.jd-input {
  flex: 1;
  min-width: 0;
  height: 36px;
  box-sizing: border-box;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
}
.jd-input:focus {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.jd-btn {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.jd-btn:hover {
  background: var(--rl-head);
}
.jd-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.jd-btn:focus-visible,
.jd-primary:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.jd-pre {
  margin: 0;
  max-height: 160px;
  padding: 12px 14px;
  overflow: auto;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
  color: var(--rl-body);
  font-family: var(--rl-mono);
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.jd-primary {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.jd-primary:disabled {
  opacity: 0.6;
}
.jd-error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.jd-error__text {
  flex: 1;
  min-width: 200px;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.jd-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.jd-skeleton__bar {
  height: 12px;
  border-radius: 6px;
  background: var(--rl-skel);
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
@media (max-width: 767px) {
  .jd-input,
  .jd-btn {
    height: 44px;
  }
}
</style>

<!-- Not scoped: the drawer is teleported. The job drawer's title is 800 (JobDetail); a phone sheet's 17 px and the
     page fill under its cards, its foot on the surface. -->
<style>
html:root .rl-job-drawer .rl-drawer__title {
  font-weight: 800;
  letter-spacing: -0.01em;
}
html:root .rl-job-drawer.is-sheet .el-drawer__header {
  padding: 10px 8px 10px;
  border-bottom-color: var(--rl-rule);
}
html:root .rl-job-drawer.is-sheet .rl-drawer__head {
  gap: 2px;
  padding-bottom: 0;
}
html:root .rl-job-drawer.is-sheet .rl-drawer__title {
  font-size: 17px;
  line-height: 24px;
}
html:root .rl-job-drawer.is-sheet .rl-drawer__sub {
  padding: 0 4px 0 48px;
}
html:root .rl-job-drawer.is-sheet .el-drawer__body {
  background: var(--rl-bg);
}
html:root .rl-job-drawer.is-sheet .rl-drawer__body {
  padding: 14px 16px 20px;
}
html:root .rl-job-drawer.is-sheet .rl-drawer__foot {
  padding: 12px 16px 20px;
}
</style>
