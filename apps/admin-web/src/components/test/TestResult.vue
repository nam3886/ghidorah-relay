<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { PublicJob } from "@/api/test";
import StatusChip from "@/components/shared/StatusChip.vue";
import { IconAlertCircleJoined, IconPlay } from "@/components/shared/icons";
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
} from "./test-result-model";

// The status of the job and its media, in the three cards the design draws: nothing made yet (a title bar and the
// status line in the middle), a job on its way or failed (the step, the job id, its ledger and the empty video slot)
// and a done job (a title bar, the media with the ledger beside it). A media link is drawn only if it is a same-origin
// /m/ share link; any other (a script URL, another host) is shown as text, never as a source. A video is a box with a
// play button until it is played: nothing is fetched before. `loaded` is whether the key has models: an idle tab
// without them says how to start.
const props = defineProps<{ status: string; job: PublicJob | null; kind: string; loaded: boolean }>();
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
</script>

<template>
  <section class="result" :class="{ 'result--bar': !job || done }" aria-labelledby="result-title">
    <div class="result__head">
      <h2 id="result-title">Result</h2>
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

    <div v-if="!job" class="result__empty">
      <p class="test-result__status" role="status">{{ line }}</p>
    </div>

    <div v-else-if="done" class="result__done">
      <div class="test-result__media">
        <template v-for="(m, i) in media" :key="i">
          <template v-if="m.safe">
            <el-image
              v-if="kind === 'image'"
              :src="m.safe"
              :preview-src-list="previews"
              :initial-index="previews.indexOf(m.safe)"
              fit="contain"
              preview-teleported
              class="result__image"
            />
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
            <dd>{{ row.value }}</dd>
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
  </section>
</template>

<style scoped>
.result {
  flex: 1 1 320px;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.result__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.result h2 {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
/* nothing made yet, and a done job: a title bar over the body */
.result--bar {
  gap: 0;
  padding: 0;
  overflow: hidden;
}
.result--bar .result__head {
  padding: 14px 16px;
  border-bottom: 1px solid var(--rl-line);
}
.result--bar h2 {
  line-height: 20px;
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
  font-weight: 600;
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
}
.result__ledger {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-raised);
  font-size: 13px;
}
.result__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.result__row dt {
  color: var(--rl-muted);
  white-space: nowrap;
}
.result__leader {
  flex: 1;
  border-bottom: 1px dotted var(--rl-line-strong);
  transform: translateY(-4px);
}
.result__row dd {
  margin: 0;
  font-family: var(--rl-mono);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
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
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-raised);
  text-align: center;
  font-size: 13px;
  color: var(--rl-muted);
}

/* a done job: the media and the ledger side by side, wrapping under each other when the card is narrow */
.result__done {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 16px;
}
.result__done .test-result__media {
  display: contents;
}
.result__frame,
.result__image {
  flex: 1 1 280px;
  min-width: 0;
}
.result__frame {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-raised);
}
.result__play {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  padding: 0;
  border: 0;
  border-radius: 26px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  cursor: pointer;
}
.result__play:hover {
  background: var(--rl-primary-hover);
}
.result__video {
  width: 100%;
  height: 100%;
}
.result__image {
  border-radius: 12px;
}
.result__ledger--done {
  flex: 1 1 200px;
  align-self: center;
  padding: 0;
  border-radius: 0;
  background: none;
}
/* the job id in the ledger opens the job, a quiet link as the job titles of the list */
.result__job {
  color: inherit;
  text-decoration: none;
}
.result__job:hover {
  color: var(--rl-primary);
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

@media (min-width: 768px) and (max-width: 1279px) {
  .result {
    flex: none;
    width: 100%;
  }
  .result__body {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: flex-start;
  }
  .result__body .result__ledger {
    flex: 1 1 260px;
  }
  .result__body .test-result__media {
    flex: 1 1 320px;
  }
}
@media (max-width: 767px) {
  .result {
    flex: none;
    width: 100%;
  }
  .result:not(.result--bar) {
    padding: 16px;
  }
  .result__id {
    display: flex;
    align-items: center;
    min-height: 44px;
  }
}
</style>
