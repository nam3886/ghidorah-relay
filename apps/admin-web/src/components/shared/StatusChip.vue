<script setup lang="ts">
import { computed } from "vue";
import { labelOf, toneOf, type Tone } from "./status-tones";

// `status` is looked up in the tone registry (a number is an HTTP code); `label` replaces the shown word and `tone`
// replaces the lookup, for the rare chip whose word is not the status. The chip is an el-tag; the tone picks its type.
const props = defineProps<{ status: string | number | null | undefined; label?: string; tone?: Tone }>();
const resolved = computed<Tone>(() => props.tone ?? toneOf(props.status));
const text = computed(() => props.label ?? labelOf(props.status));
const TAG_TYPE = { ok: "success", info: "info", warn: "warning", bad: "danger", neutral: "info" } as const;
</script>

<template>
  <el-tag
    class="status-chip"
    :class="`tone-${resolved}`"
    :type="TAG_TYPE[resolved]"
    effect="light"
    disable-transitions
    :data-tone="resolved"
  >
    <i class="dot" aria-hidden="true" />{{ text }}
  </el-tag>
</template>

<style scoped>
/* The tag's own padding and colours stay; only the dot and the grey of "neutral" (the registry's neutral is not blue) */
.status-chip :deep(.el-tag__content) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentcolor;
}
.tone-neutral {
  --el-tag-text-color: var(--rl-neutral);
  --el-tag-bg-color: var(--rl-neutral-bg);
  --el-tag-border-color: var(--rl-line);
}
</style>
