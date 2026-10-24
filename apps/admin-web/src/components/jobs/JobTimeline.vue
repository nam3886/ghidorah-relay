<script setup lang="ts">
import type { JobEvent } from "@/api/jobs-detail";
import { eventTone, fmtClock } from "@/components/jobs/job-text";

// The events of a job, newest first (the caller sorts), as JobDetailTabs draws the Timeline tab: the time in mono, a
// 10 px dot in the tone of the stage ringed off the surface on a 2 px rail, then the stage word in its tone (a plain
// stage in the text colour) and its text. `compact` is the phone's Latest steps card: the time at the end of the row.
// Everything is drawn as text.
const props = withDefaults(defineProps<{ events: readonly JobEvent[]; compact?: boolean }>(), { compact: false });
</script>

<template>
  <p v-if="!props.events.length" class="tl__none">No events</p>
  <ol v-else-if="props.compact" class="tl tl--compact">
    <li v-for="e in props.events" :key="e.id" class="tl__crow" :data-tone="eventTone(e)">
      <span class="tl__dot" :class="`tone-${eventTone(e)}`" />
      <span class="tl__text">
        <b class="tl__stage" :class="`tone-${eventTone(e)}`">{{ e.stage }}</b>
        <span class="num tl__what">{{ e.text }}</span>
      </span>
      <span class="num mono tl__at">{{ fmtClock(e.at_ms) }}</span>
    </li>
  </ol>
  <ol v-else class="tl">
    <li v-for="(e, i) in props.events" :key="e.id" class="tl__row" :data-tone="eventTone(e)">
      <span class="num mono tl__clock">{{ fmtClock(e.at_ms) }}</span>
      <span class="tl__rail">
        <span class="tl__dot is-ringed" :class="`tone-${eventTone(e)}`" />
        <span v-if="i < props.events.length - 1" class="tl__line" />
      </span>
      <span class="tl__text tl__text--pad">
        <b class="tl__stage" :class="`tone-${eventTone(e)}`">{{ e.stage }}</b>
        <span class="num tl__what">{{ e.text }}</span>
      </span>
    </li>
  </ol>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.tl {
  margin: 0;
  padding: 0;
  list-style: none;
}
.tl__none {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.tl__row {
  display: grid;
  grid-template-columns: 72px 20px minmax(0, 1fr);
  gap: 0 12px;
}
.tl__clock {
  font-size: 12px;
  line-height: 20px;
  color: var(--rl-faint);
}
.tl__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.tl__dot {
  flex: none;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--rl-faint);
}
.tl__dot.is-ringed {
  box-shadow:
    0 0 0 3px var(--rl-surface),
    0 0 0 4px var(--rl-rule);
}
.tl__line {
  flex: 1;
  width: 2px;
  margin-top: 4px;
  background: var(--rl-rule);
}
.tl__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.tl__text--pad {
  padding-bottom: 16px;
}
.tl__stage {
  font-size: 13px;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.tl__what {
  font-size: 13px;
  line-height: 19px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.tone-ok.tl__dot {
  background: var(--rl-ok);
}
.tone-info.tl__dot {
  background: var(--rl-info);
}
.tone-warn.tl__dot {
  background: var(--rl-warn);
}
.tone-bad.tl__dot {
  background: var(--rl-bad);
}
.tone-ok.tl__stage {
  color: var(--rl-ok);
}
.tone-info.tl__stage {
  color: var(--rl-info);
}
.tone-warn.tl__stage {
  color: var(--rl-warn);
}
.tone-bad.tl__stage {
  color: var(--rl-bad);
}
.tl--compact {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tl__crow {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.tl__crow .tl__text {
  flex: 1;
  gap: 1px;
}
.tl__crow .tl__what {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.tl__at {
  flex: none;
  font-size: 11px;
  color: var(--rl-faint);
}
</style>
