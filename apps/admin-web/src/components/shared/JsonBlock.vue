<script setup lang="ts">
import { computed, ref } from "vue";
import CopyButton from "./CopyButton.vue";

// Pretty-printed JSON in a scrollable mono block, with a Copy button. A string is shown as it is (the server sends
// some bodies already as text). Shown with text interpolation only: the content is server data.
const props = withDefaults(defineProps<{ value: unknown; copy?: boolean }>(), { copy: true });

const text = computed(() => {
  if (props.value === undefined) return "";
  if (typeof props.value === "string") return props.value;
  try {
    return JSON.stringify(props.value, null, 2) ?? String(props.value);
  } catch {
    return String(props.value);
  }
});
const pre = ref<HTMLElement | null>(null);
</script>

<template>
  <div class="json-block">
    <pre ref="pre" class="json-block__body">{{ text }}</pre>
    <CopyButton v-if="props.copy" class="json-block__copy" :text="text" :select-from="() => pre" />
  </div>
</template>

<style scoped>
.json-block {
  position: relative;
}
.json-block__body {
  margin: 0;
  padding: 12px;
  max-height: 24rem;
  overflow: auto;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--rl-surface-2);
  border-radius: var(--el-border-radius-base, 6px);
}
.json-block__copy {
  position: absolute;
  top: 8px;
  right: 8px;
}
</style>
