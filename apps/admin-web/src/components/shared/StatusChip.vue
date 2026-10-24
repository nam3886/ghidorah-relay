<script setup lang="ts">
import { computed } from "vue";
import { labelOf, toneOf, type Tone } from "./status-tones";

// `status` is looked up in the tone registry (a number is an HTTP code); `label` replaces the shown word and `tone`
// replaces the lookup, for the rare chip whose word is not the status. `step` ("2/4") replaces the dot with the
// position of a running job. The chip is a plain 24 px pill (an el-tag cannot draw the canvas pill); the tone picks
// its colours.
const props = defineProps<{
  status: string | number | null | undefined;
  label?: string;
  tone?: Tone;
  step?: string;
}>();
const resolved = computed<Tone>(() => props.tone ?? toneOf(props.status));
const text = computed(() => props.label ?? labelOf(props.status));
</script>

<template>
  <span class="status-chip" :class="`tone-${resolved}`" :data-tone="resolved">
    <span v-if="props.step" class="step">{{ props.step }}</span>
    <i v-else class="dot" aria-hidden="true" />{{ text }}
  </span>
</template>

<style scoped>
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  color: var(--chip-fg);
  background: var(--chip-bg);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: var(--chip-fg);
}
.step {
  font-family: var(--rl-mono, "JetBrains Mono", ui-monospace, monospace);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.tone-ok {
  --chip-fg: var(--rl-ok);
  --chip-bg: var(--rl-ok-bg);
}
.tone-warn {
  --chip-fg: var(--rl-warn);
  --chip-bg: var(--rl-warn-bg);
}
.tone-bad {
  --chip-fg: var(--rl-bad);
  --chip-bg: var(--rl-bad-bg);
}
.tone-info {
  --chip-fg: var(--rl-info);
  --chip-bg: var(--rl-info-bg);
}
.tone-neutral {
  --chip-fg: var(--rl-neutral);
  --chip-bg: var(--rl-neutral-bg);
}
</style>
