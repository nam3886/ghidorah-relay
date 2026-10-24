<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { JobDetail } from "@/api/jobs-detail";
import type { PublicJob } from "@/api/test";
import { highlight } from "@/components/logs/log-json";
import { copyText } from "@/components/shared/clipboard";
import IconButton from "@/components/shared/IconButton.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { notify } from "@/lib/notify";
import { IconAlertCircleJoined, IconImage, IconPlay } from "@/components/shared/icons";
import { safeMediaUrl } from "@/lib/safe-media-url";
import { useUiStore } from "@/stores/ui";
import { jobLine } from "./use-generate";
import {
  doneLedger,
  EMPTY_RESULT,
  EMPTY_VIDEO_SLOT,
  IDLE,
  jobLedger,
  shortJobId,
  statusWord,
  stepOf,
  timelineSteps,
  tookText,
} from "./test-result-model";

// The status of the job and its media, in the three cards the design draws: nothing made yet (a title bar and the
// status line in the middle), a job on its way or failed (the step, the job id, its ledger and the empty video slot)
// and a done job (a title bar, the media with the ledger beside it). A media link is drawn only if it is a same-origin
// /m/ share link; any other (a script URL, another host) is shown as text, never as a source. A video is a box with a
// play button until it is played: nothing is fetched before. `loaded` is whether the key has models: an idle tab
// without them says how to start.
// `detail` is the admin detail of the same job (null until read): the timeline's times and the account; `unit` the
// unit of its model's pool, for the upstream figure.
const props = withDefaults(
  defineProps<{
    status: string;
    job: PublicJob | null;
    kind: string;
    loaded: boolean;
    detail?: JobDetail | null;
    unit?: string | null;
  }>(),
  { detail: null, unit: null },
);
const steps = computed(() => timelineSteps(props.detail, props.unit));
const ui = useUiStore();

const media = computed(() =>
  [...(props.job?.result_urls ?? []), ...(props.job?.images ?? [])].map((url) => ({ url, safe: safeMediaUrl(url) })),
);
// The viewer pages through the safe links only.
const previews = computed(() => media.value.flatMap((m) => (m.safe ? [m.safe] : [])));
const ledger = computed(() => (props.job ? jobLedger(props.job) : []));
const doneRows = computed(() => (props.job ? doneLedger(props.job) : []));
// The status line is the live region of the tab: the job line of a normal job is read out but not drawn (the pill says
// it), while a failed call, a sign-out or the idle hint are drawn.
const quiet = computed(() => !!props.job && props.status === jobLine(props.job));
const line = computed(() => (!props.job && !props.loaded && props.status === IDLE ? EMPTY_RESULT : props.status));
const jobError = computed(() => (typeof props.job?.error === "string" ? props.job.error : ""));
const errorCode = computed(() => (typeof props.job?.error_code === "string" ? props.job.error_code : ""));
// A job that is over has no empty slot to wait in; a done one has the card of its own.
const finished = computed(() => ["done", "failed", "completed"].includes(props.job?.status ?? ""));
const done = computed(() => ["done", "completed"].includes(props.job?.status ?? ""));
// The video being played (its index): none until its play button is pressed, and none again for the next job.
const playing = ref(-1);
watch(
  () => props.job?.id,
  () => (playing.value = -1),
);
const openJob = (): void => {
  if (props.job) ui.openJob(props.job.id);
};

// The head: what the job is (its kind and model) and, once it is over, how long it took.
// As drawn, `job 3a90cc5e · flow-01` once the admin detail names the account; before, its kind and model.
const subline = computed(() => {
  if (!props.job) return "";
  if (props.detail?.account_label) return `job ${shortJobId(props.job.id)} · ${props.detail.account_label}`;
  return [props.kind, typeof props.job.model === "string" ? props.job.model : ""].filter(Boolean).join(" · ");
});
const took = computed(() => (props.job ? tookText(props.job.created_at, props.job.finished_at) : ""));

// The answer as the API sent it, coloured; a phone shows it behind a button (TestPhone, "Show the JSON answer").
const { isPhone } = useBreakpoint();
const answer = computed(() => (props.job ? JSON.stringify(props.job, null, 2) : ""));
const answerLines = computed(() => highlight(answer.value));
const showAnswer = ref(false);
const answerPre = ref<HTMLElement | null>(null);
const copied = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
const abort = new AbortController();
onBeforeUnmount(() => {
  abort.abort();
  clearTimeout(timer);
});
async function copyAnswer(): Promise<void> {
  const outcome = await copyText(answer.value, () => answerPre.value, abort.signal);
  if (outcome === "aborted") return;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    copied.value = true;
    notify.success("Copied the JSON answer");
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  }
}
</script>

<template>
  <section class="result" :class="{ 'result--bar': !job || done }" aria-labelledby="result-title">
    <div class="result__head">
      <div class="result__title">
        <h2 id="result-title">Result</h2>
        <span v-if="subline" class="result__sub mono">{{ subline }}</span>
      </div>
      <span v-if="took" class="result__took num">{{ took }}</span>
      <StatusChip
        v-if="job"
        :status="statusWord(job)"
        :step="stepOf(job)"
        :tone="
          job.status === 'failed'
            ? 'bad'
            : job.status === 'done'
              ? 'ok'
              : statusWord(job) === 'unknown'
                ? 'warn'
                : 'info'
        "
      />
    </div>

    <!-- the steps of the job, from the admin detail: filled once reached, the last one solid -->
    <ol v-if="job && steps.length" class="result__timeline" data-testid="test-timeline">
      <li
        v-for="(s, i) in steps"
        :key="s.label"
        class="tl"
        :class="{ 'is-reached': s.reached, 'is-bad': s.bad, 'is-last': i === steps.length - 1 }"
      >
        <span class="tl__rail" aria-hidden="true"><span class="tl__dot" /><span class="tl__line" /></span>
        <span class="tl__body">
          <span class="tl__top"
            ><span class="tl__label">{{ s.label }}</span
            ><span class="tl__time num">{{ s.time }}</span></span
          >
          <span v-if="s.note" class="tl__note num">{{ s.note }}</span>
        </span>
      </li>
    </ol>

    <div v-if="!job" class="result__empty">
      <p class="test-result__status" role="status">{{ line }}</p>
    </div>

    <div v-else-if="done" class="result__done">
      <div class="test-result__media">
        <template v-for="(m, i) in media" :key="i">
          <template v-if="m.safe">
            <!-- an image sits in the drawn media box; a broken one shows the canvas's image glyph -->
            <div v-if="kind === 'image'" class="result__frame">
              <el-image
                :src="m.safe"
                :preview-src-list="previews"
                :initial-index="previews.indexOf(m.safe)"
                fit="contain"
                preview-teleported
                class="result__image"
              >
                <template #error><IconImage class="result__broken" :size="22" :stroke="1.6" /></template>
              </el-image>
            </div>
            <div v-else class="result__frame">
              <video v-if="playing === i" :src="m.safe" controls autoplay class="result__video" />
              <button v-else type="button" class="result__play" aria-label="Play video" @click="playing = i">
                <IconPlay :size="22" />
              </button>
            </div>
          </template>
          <p v-else class="test-result__unsafe">link not shown: {{ m.url }}</p>
        </template>
      </div>
      <dl class="result__ledger result__ledger--done">
        <div v-for="row in doneRows" :key="row.label" class="result__row">
          <dt>{{ row.label }}</dt>
          <span class="result__leader" aria-hidden="true" />
          <dd>
            <a
              v-if="row.label === 'Job'"
              class="result__job"
              href="#"
              :aria-label="`Open job ${row.value}`"
              @click.prevent="openJob"
              >{{ row.value }}</a
            >
            <template v-else>{{ row.value }}</template>
          </dd>
        </div>
      </dl>
      <p class="test-result__status" :class="{ 'sr-only': quiet }" role="status">{{ line }}</p>
    </div>

    <template v-else>
      <a class="result__id" href="#" :aria-label="`Open job ${shortJobId(job.id)}`" @click.prevent="openJob">{{
        shortJobId(job.id)
      }}</a>
      <p class="test-result__status" :class="{ 'sr-only': quiet }" role="status">{{ line }}</p>
      <!-- the job drawer's failed banner: the code in bold, then the reason -->
      <div v-if="jobError" class="result__error" role="alert">
        <IconAlertCircleJoined :size="16" :stroke="2.2" />
        <span class="result__error-text"
          ><strong
            >Failed<template v-if="errorCode"> · {{ errorCode }}</template></strong
          >
          — {{ jobError }}</span
        >
      </div>
      <div class="result__body">
        <dl v-if="ledger.length" class="result__ledger">
          <div v-for="row in ledger" :key="row.label" class="result__row">
            <dt>{{ row.label }}</dt>
            <span class="result__leader" aria-hidden="true" />
            <dd>
              <template v-if="row.was"
                ><s class="result__was">{{ row.was }}</s
                >{{ " → " }}</template
              >{{ row.value
              }}<template v-if="row.note"
                >{{ " " }}<span class="result__note">{{ row.note }}</span></template
              >
            </dd>
          </div>
        </dl>
        <div class="test-result__media">
          <div
            v-if="kind === 'video' && !finished && !media.length"
            class="result__slot"
            role="img"
            aria-label="Empty video slot"
          >
            <span>{{ EMPTY_VIDEO_SLOT }}</span>
          </div>
        </div>
      </div>
    </template>

    <div v-if="job" class="result__answer">
      <div class="result__answer-head">
        <span class="result__answer-title">Response</span>
        <span class="result__sub mono">application/json</span>
        <span class="result__copy">
          <IconButton
            v-if="!isPhone || showAnswer"
            :icon="copied ? 'check' : 'copy'"
            label="Copy JSON answer"
            placement="top-end"
            @click="copyAnswer"
          />
        </span>
      </div>
      <button v-if="isPhone && !showAnswer" type="button" class="result__show" @click="showAnswer = true">
        Show the JSON answer
      </button>
      <pre
        v-else
        ref="answerPre"
        class="result__json mono"
        tabindex="0"
      ><template v-for="(l, i) in answerLines" :key="i"><span v-for="(part, j) in l" :key="j" :class="part.kind">{{ part.text }}</span>{{ "\n" }}</template></pre>
    </div>
  </section>
</template>

<style scoped>
/* the Result card (Test.dc.html): the head over a rule (title and what the job is, the time taken, the pill), the
   body, then the Response with its Copy icon over the coloured JSON */
.result {
  flex: 1 1 440px;
  max-width: 560px;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.result__head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--rl-rule);
}
.result__title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  margin-right: auto;
}
.result h2 {
  margin: 0;
  font-size: 16px;
  line-height: 22px;
  font-weight: 700;
}
.result__sub,
.result__took {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.result__took {
  flex: none;
}
/* the timeline (Test.dc.html): a 20 px dot per step on a 2 px rail, the label and its time on one line */
.result__timeline {
  list-style: none;
  margin: 0;
  padding: 18px 20px 6px;
}
.tl {
  display: flex;
  gap: 12px;
}
.tl__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: none;
}
.tl__dot {
  box-sizing: border-box;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--rl-line);
  background: var(--rl-surface);
}
.tl.is-reached .tl__dot {
  border: 0;
  background: var(--rl-ok-bg);
  box-shadow:
    inset 0 0 0 6px var(--rl-ok-bg),
    inset 0 0 0 10px var(--rl-ok);
}
.tl.is-reached.is-last .tl__dot {
  background: var(--rl-ok);
  box-shadow: none;
}
.tl.is-bad .tl__dot {
  background: var(--rl-bad);
  box-shadow: none;
}
.tl__line {
  flex: 1;
  width: 2px;
  min-height: 18px;
  margin: 2px 0;
  background: var(--rl-rule);
}
.tl.is-last .tl__line {
  display: none;
}
.tl__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
  padding-bottom: 12px;
}
.tl__top {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.tl__label {
  font-weight: 700;
  overflow-wrap: anywhere;
}
.tl:not(.is-reached) .tl__label {
  color: var(--rl-faint);
}
.tl.is-bad .tl__label {
  color: var(--rl-bad);
}
.tl__time {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
  white-space: nowrap;
}
.tl__note {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.result__empty {
  padding: 44px 24px 52px;
  text-align: center;
}
.result__empty .test-result__status {
  font-size: 14px;
}
.result__id {
  align-self: flex-start;
  margin: 16px 20px 0;
  color: var(--rl-link);
  font-family: var(--rl-mono);
  font-size: 13px;
  text-underline-offset: 3px;
}
.test-result__status {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.result__id ~ .test-result__status:not(.sr-only) {
  margin: 8px 20px 0;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.result__error {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 16px 20px 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  font-size: 13px;
  line-height: 20px;
}
.result__error svg {
  flex: none;
  margin-top: 2px;
  color: var(--rl-bad);
}
.result__error strong {
  font-weight: 700;
  color: var(--rl-bad);
}
.result__error-text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.result__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 20px 20px;
}
/* the facts: a ruled ledger, label at the left and value at the right */
.result__ledger {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.result__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  box-sizing: border-box;
  padding: 10px 14px;
  border-top: 1px solid var(--rl-rule);
}
.result__row:first-child {
  border-top: 0;
}
.result__row dt {
  flex: none;
  font-size: 13px;
  color: var(--rl-faint);
}
.result__leader {
  display: none;
}
.result__row dd {
  min-width: 0;
  margin: 0;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-weight: 600;
  text-align: right;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.result__was {
  color: var(--rl-muted);
}
.result__note {
  color: var(--rl-ok);
}
.test-result__media {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.result__slot {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 16 / 9;
  padding: 16px;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
  background: var(--rl-head);
  text-align: center;
  font-size: 13px;
  color: var(--rl-muted);
}

/* a done job: the media, then its ledger */
.result__done {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 16px 20px 20px;
}
.result__done .test-result__media {
  display: contents;
}
.result__frame {
  flex: 1 1 280px;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border-radius: 12px;
  background: var(--rl-media);
}
.result__play {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  padding: 0 0 0 3px;
  box-sizing: border-box;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.92);
  color: #1a1b25;
  cursor: pointer;
}
.result__video {
  width: 100%;
  height: 100%;
}
.result__image {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}
.result__broken {
  color: var(--rl-muted);
}
.result__ledger--done {
  flex: 1 1 100%;
}
.result__job {
  color: var(--rl-link);
  text-decoration: none;
}
.result__job:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.test-result__unsafe {
  flex-basis: 100%;
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.result__done .test-result__status:not(.sr-only) {
  flex-basis: 100%;
}

/* the answer */
.result__answer {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--rl-rule);
}
.result__answer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
}
.result__copy {
  display: flex;
  margin-left: auto;
}
.result__answer-title {
  font-weight: 700;
}
.result__json {
  margin: 0 20px 20px;
  max-height: 360px;
  padding: 14px 16px;
  overflow: auto;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
  color: var(--rl-body);
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  --rl-json-num: #c2410c;
}
:global(html.dark) .result__json {
  --rl-json-num: #fdba74;
}
.result__json .key {
  color: var(--rl-brand-ink);
}
.result__json .str {
  color: var(--rl-ok);
}
.result__json .num {
  color: var(--rl-json-num);
}
.result__json .lit {
  color: var(--rl-neutral);
  font-style: italic;
}
.result__json .pun {
  color: var(--rl-faint);
}
.result__show {
  box-sizing: border-box;
  height: 44px;
  margin: 0 16px 16px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

/* a tablet and a phone: the card takes the column under the form */
@media (max-width: 1279px) {
  .result {
    flex: none;
    width: 100%;
    max-width: none;
  }
}
@media (max-width: 767px) {
  .result__head,
  .result__answer-head {
    padding-left: 16px;
    padding-right: 16px;
  }
  .result__body,
  .result__done {
    padding: 16px;
  }
  .result__id {
    display: flex;
    align-items: center;
    min-height: 44px;
    margin: 8px 16px 0;
  }
  .result__error {
    margin: 16px 16px 0;
  }
  .result__json {
    margin: 0 16px 16px;
  }
}
</style>
