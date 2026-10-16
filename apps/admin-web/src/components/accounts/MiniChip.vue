<script setup lang="ts">
import type { Tone } from "@/components/shared/status-tones";

// The 20 px chip of the account rows: the drift flag and the proxy check (a 5 px dot, then the words). The 24 px
// status pill is the shared StatusChip.
defineProps<{ tone: Tone }>();
</script>

<template>
  <span class="mini-chip" :class="`tone-${tone}`" :data-tone="tone"
    ><i class="mini-chip__dot" aria-hidden="true" /><slot
  /></span>
</template>

<style scoped>
.mini-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 20px;
  padding: 0 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
  line-height: 16px;
  white-space: nowrap;
  color: var(--chip-fg);
  background: var(--chip-bg);
}
.mini-chip__dot {
  width: 5px;
  height: 5px;
  border-radius: 3px;
  background: currentcolor;
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
