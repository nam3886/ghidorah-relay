<script setup lang="ts">
import { computed } from "vue";
import type { PublicJob } from "@/api/test";
import StatusChip from "@/components/shared/StatusChip.vue";
import { safeMediaUrl } from "@/lib/safe-media-url";
import { useUiStore } from "@/stores/ui";
import { jobLine } from "./use-generate";
import { EMPTY_RESULT, EMPTY_VIDEO_SLOT, IDLE, jobLedger, shortJobId, statusWord, stepOf } from "./test-result-model";

// The status of the job and its media. A media link is drawn only if it is a same-origin /m/ share link; any other (a
// script URL, another host) is shown as text, never as a source. `loaded` is whether the key has models: an idle tab
// without them says how to start.
const props = defineProps<{ status: string; job: PublicJob | null; kind: string; loaded: boolean }>();
const ui = useUiStore();

const media = computed(() =>
  [...(props.job?.result_urls ?? []), ...(props.job?.images ?? [])].map((url) => ({ url, safe: safeMediaUrl(url) })),
);
// The viewer pages through the safe links only.
const previews = computed(() => media.value.flatMap((m) => (m.safe ? [m.safe] : [])));
const ledger = computed(() => (props.job ? jobLedger(props.job) : []));
// The status line is the live region of the tab: the job line of a normal job is read out but not drawn (the pill says
// it), while a failed call, a sign-out or the idle hint are drawn.
const quiet = computed(() => !!props.job && props.status === jobLine(props.job));
const line = computed(() => (!props.job && !props.loaded && props.status === IDLE ? EMPTY_RESULT : props.status));
const jobError = computed(() => (typeof props.job?.error === "string" ? props.job.error : ""));
// A job that is over has no empty slot to wait in.
const finished = computed(() => ["done", "failed", "completed"].includes(props.job?.status ?? ""));
</script>

<template>
  <section class="result" aria-labelledby="result-title">
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
    <a
      v-if="job"
      class="result__id"
      href="#"
      :aria-label="`Open job ${shortJobId(job.id)}`"
      @click.prevent="ui.openJob(job.id)"
      >{{ shortJobId(job.id) }}</a
    >
    <p class="test-result__status" :class="{ result__idle: !job, 'sr-only': quiet }" role="status">{{ line }}</p>
    <p v-if="jobError" class="result__error" role="alert">{{ jobError }}</p>

    <div v-if="job" class="result__body">
      <dl v-if="ledger.length" class="result__ledger">
        <div v-for="row in ledger" :key="row.label" class="result__row">
          <dt>{{ row.label }}</dt>
          <span class="result__leader" aria-hidden="true" />
          <dd>{{ row.value }}</dd>
        </div>
      </dl>
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
            <video v-else :src="m.safe" controls class="result__video" />
          </template>
          <p v-else class="test-result__unsafe">link not shown: {{ m.url }}</p>
        </template>
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
.result__idle {
  padding: 28px 8px;
  text-align: center;
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
  margin: 0;
  padding: 8px 12px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
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
.result__video,
.result__image {
  width: 100%;
  max-width: 100%;
  border-radius: 12px;
}
.test-result__unsafe {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
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
  .result__ledger {
    flex: 1 1 260px;
  }
  .test-result__media {
    flex: 1 1 320px;
  }
}
@media (max-width: 767px) {
  .result {
    flex: none;
    width: 100%;
    padding: 16px;
  }
  .result__id {
    display: flex;
    align-items: center;
    min-height: 44px;
  }
}
</style>
