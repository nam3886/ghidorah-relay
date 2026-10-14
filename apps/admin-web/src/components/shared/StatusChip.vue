<script setup lang="ts">
import { computed } from "vue";
import { labelOf, toneOf, type Tone } from "./status-tones";

// `status` is looked up in the tone registry (a number is an HTTP code); `label` replaces the shown word and `tone`
// replaces the lookup, for the rare chip whose word is not the status.
const props = defineProps<{ status: string | number; label?: string; tone?: Tone }>();
const resolved = computed<Tone>(() => props.tone ?? toneOf(props.status));
const text = computed(() => props.label ?? labelOf(props.status));
</script>

<template>
  <span class="status-chip" :class="`tone-${resolved}`" :data-tone="resolved"
    ><i class="dot" aria-hidden="true" />{{ text }}</span
  >
</template>

<style scoped>
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--el-border-radius-base, 6px);
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentcolor;
}
.tone-ok {
  color: var(--rl-ok);
  background: var(--rl-ok-bg);
}
.tone-info {
  color: var(--rl-info);
  background: var(--rl-info-bg);
}
.tone-warn {
  color: var(--rl-warn);
  background: var(--rl-warn-bg);
}
.tone-bad {
  color: var(--rl-bad);
  background: var(--rl-bad-bg);
}
.tone-neutral {
  color: var(--rl-neutral);
  background: var(--rl-neutral-bg);
}
</style>
