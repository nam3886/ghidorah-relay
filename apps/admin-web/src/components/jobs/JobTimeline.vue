<script setup lang="ts">
import type { JobEvent } from "@/api/jobs-detail";
import { eventTone, fmtClock } from "@/components/jobs/job-text";

// The events of a job, newest first (the caller sorts), as the canvas draws them: a hollow 12 px dot in the tone of
// the stage on a 2 px rail, the time in mono, then the stage word and its text. Everything is drawn as text.
const props = defineProps<{ events: readonly JobEvent[] }>();
</script>

<template>
  <p v-if="!props.events.length" class="timeline__none">No events</p>
  <ol v-else class="timeline">
    <li v-for="e in props.events" :key="e.id" class="timeline__item" :data-tone="eventTone(e)">
      <div class="timeline__rail">
        <span class="timeline__dot" :class="`tone-${eventTone(e)}`" />
        <span class="timeline__line" />
      </div>
      <div class="timeline__text">
        <span class="timeline__time">{{ fmtClock(e.at_ms) }}</span>
        <span class="timeline__what"
          ><strong>{{ e.stage }}</strong> <span class="timeline__muted">{{ e.text }}</span></span
        >
      </div>
    </li>
  </ol>
</template>

<style scoped>
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}
.timeline__none {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.timeline__item {
  display: flex;
  gap: 14px;
}
.timeline__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 12px;
}
.timeline__dot {
  width: 12px;
  height: 12px;
  margin-top: 4px;
  box-sizing: border-box;
  border: 2px solid var(--rl-neutral);
  border-radius: 6px;
  background: var(--rl-surface);
}
.timeline__dot.tone-ok {
  border-color: var(--rl-ok);
}
.timeline__dot.tone-info {
  border-color: var(--rl-info);
}
.timeline__dot.tone-warn {
  border-color: var(--rl-warn);
}
.timeline__dot.tone-bad {
  border-color: var(--rl-bad);
}
.timeline__line {
  flex: 1;
  width: 2px;
  min-height: 22px;
  margin: 4px 0;
  background: var(--rl-line-strong);
}
.timeline__text {
  flex: 1;
  min-width: 0;
  padding-bottom: 18px;
}
.timeline__time {
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--rl-faint);
}
.timeline__what {
  display: block;
  overflow-wrap: anywhere;
}
.timeline__what strong {
  font-weight: 600;
}
.timeline__muted {
  color: var(--rl-muted);
}
</style>
