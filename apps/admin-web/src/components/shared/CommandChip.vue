<script setup lang="ts">
// The chip line under a command button (specs/shared.md section 3.1): `running…` with a spinner, `ok · 20:55:07` or
// `error · <message>`. The control above it is the disabled secondary button; the view stacks them (column, gap 8).
// The chip never grows past the room it is given: a long error ends in "…" and its title holds the whole text.
import { computed } from "vue";

const props = defineProps<{
  state: "running" | "ok" | "error";
  // the time of an ok result, shown in mono
  time?: string;
  // the reason of an error result, plain text
  message?: string;
}>();
const errorText = computed(() => (props.message ? `error · ${props.message}` : "error"));
</script>

<template>
  <span
    class="command-chip"
    :class="`is-${props.state}`"
    :data-state="props.state"
    role="status"
    :title="props.state === 'error' ? errorText : undefined"
  >
    <template v-if="props.state === 'running'">
      <span class="command-chip__spinner" aria-hidden="true"><i /></span>running…
    </template>
    <template v-else-if="props.state === 'ok'">
      <i class="command-chip__dot" aria-hidden="true" />ok ·
      <span v-if="props.time" class="command-chip__time">{{ props.time }}</span>
    </template>
    <template v-else>
      <i class="command-chip__dot" aria-hidden="true" /><span class="command-chip__text">{{ errorText }}</span>
    </template>
  </span>
</template>

<style scoped>
.command-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  box-sizing: border-box;
}
.command-chip > :not(.command-chip__text) {
  flex: none;
}
/* only an error's words can be long: the chip stays in its room and they end in "…" */
.is-error {
  max-width: 100%;
}
.command-chip__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.is-running {
  gap: 8px;
  padding: 0 10px 0 6px;
  color: var(--rl-info);
  background: var(--rl-info-bg);
}
.is-ok {
  color: var(--rl-ok);
  background: var(--rl-ok-bg);
}
.is-error {
  color: var(--rl-bad);
  background: var(--rl-bad-bg);
}
.command-chip__dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: currentcolor;
}
.command-chip__spinner {
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 12px;
  height: 12px;
  border: 2px solid var(--rl-info);
  border-radius: 6px;
}
.command-chip__spinner i {
  width: 4px;
  height: 4px;
  border-radius: 2px;
  background: var(--rl-info);
}
.command-chip__time {
  font-family: var(--rl-mono, "JetBrains Mono", ui-monospace, monospace);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
</style>
