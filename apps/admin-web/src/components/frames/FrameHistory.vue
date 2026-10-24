<script setup lang="ts">
import { computed } from "vue";
import HintTip from "@/components/shared/HintTip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { longStamp, lockHistoryLine, newestFirst, versionTag } from "./frames-model";
import type { FramesController } from "./use-frames";

// "History · seedance-2.0" (Frames.dc.html): the versions of the open frame, newest first, the current one marked, each
// older one with Roll back (an icon button; a rollback saves a new version). The heading of a model with a lock history
// carries the whole history of the lock as a note. A phone leaves out who saved it.
const props = defineProps<{ frames: FramesController }>();
const { isPhone } = useBreakpoint();

const label = computed(() =>
  props.frames.shown?.target === "default" ? "default" : (props.frames.shown?.target ?? ""),
);
const currentId = computed(() => props.frames.info?.current?.id ?? null);
const versions = computed(() => newestFirst(props.frames.info?.history ?? []));
const lockNote = computed(() =>
  props.frames.shownModel && props.frames.info?.lock_history?.length
    ? lockHistoryLine(props.frames.info.lock_history)
    : "",
);
const when = (ms: number | null, by: string): string => (isPhone.value ? longStamp(ms) : `${longStamp(ms)} · ${by}`);
</script>

<template>
  <section class="fh" aria-labelledby="hist-title" data-testid="frame-history-card">
    <div class="fh__head">
      <h3 id="hist-title" class="fh__h3">
        <HintTip v-if="lockNote" :text="lockNote">History</HintTip><template v-else>History</template> ·
        {{ label }}
      </h3>
    </div>
    <p v-if="!versions.length" class="fh__empty">No versions yet</p>
    <ol v-else class="fh__list" data-testid="frame-history">
      <li v-for="v in versions" :key="v.id" :data-version="v.id">
        <span class="fh__tag">{{ versionTag(v) }}</span>
        <span class="fh__cur" :class="{ 'is-hidden': v.id !== currentId }">current</span>
        <span class="fh__when num">{{ when(v.created_ms, v.created_by) }}</span>
        <IconButton
          v-if="v.id !== currentId"
          icon="rollback"
          :label="`Roll back to version ${v.id}`"
          :disabled="frames.controlsOff"
          @click="frames.rollback(v.id)"
        />
        <span v-else class="fh__gap" aria-hidden="true" />
      </li>
    </ol>
  </section>
</template>

<style scoped>
.fh {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.fh__head {
  display: flex;
  align-items: center;
  padding: 14px 20px;
  border-bottom: 1px solid var(--rl-rule);
}
.fh__h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.fh__empty {
  margin: 0;
  padding: 18px 20px;
  font-size: 13px;
  color: var(--rl-muted);
}
.fh__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.fh__list li {
  display: grid;
  grid-template-columns: 44px 70px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 4px 12px 4px 20px;
  border-top: 1px solid var(--rl-rule);
}
.fh__list li:first-child {
  border-top: 0;
}
.fh__tag {
  font-family: var(--rl-mono);
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.fh__cur {
  display: inline-flex;
  align-items: center;
  justify-self: start;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.fh__cur.is-hidden {
  visibility: hidden;
}
.fh__when {
  min-width: 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.fh__gap {
  width: 32px;
}
@media (max-width: 767px) {
  .fh__list li {
    grid-template-columns: 40px 66px minmax(0, 1fr) auto;
    padding-right: 6px;
    padding-left: 16px;
  }
  .fh__gap {
    width: 44px;
  }
}
</style>
