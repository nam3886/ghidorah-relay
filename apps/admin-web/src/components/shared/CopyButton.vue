<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { notify } from "@/lib/notify";
import { copyText, type CopyOutcome } from "./clipboard";
import { IconCheck, IconCopy } from "./icons";

// `variant` is the drawn box (specs/shared.md section 2):
//   button  the secondary button, 36 high (the global size applies unless a caller sets `size`)
//   tiny    the drawer's `Copy prompt`: 28 high, radius 8, 12 px
//   chip    the copy-id chip: 24 high, radius 8, mono 12, the id as its label
const props = withDefaults(
  defineProps<{
    text: string | (() => string);
    label?: string;
    variant?: "button" | "tiny" | "chip";
    // deprecated alias of `variant` for callers that still pass a size: small is tiny, default is button
    size?: "small" | "default";
    // the element the text sits in: selected when no copy path works, so the user can press Ctrl+C
    selectFrom?: () => HTMLElement | null | undefined;
  }>(),
  { label: "Copy", variant: undefined, size: undefined, selectFrom: undefined },
);
const emit = defineEmits<{ copied: [outcome: CopyOutcome] }>();

const shape = computed(() => props.variant ?? (props.size === "small" ? "tiny" : "button"));
const copied = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;

// aborted on unmount: a copy still pending then must not touch the clipboard fallback, toast or emit
const abort = new AbortController();
onBeforeUnmount(() => {
  abort.abort();
  clearTimeout(timer);
});

async function copy() {
  const value = typeof props.text === "function" ? props.text() : props.text;
  const outcome = await copyText(value, props.selectFrom, abort.signal);
  if (outcome === "aborted") return;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    copied.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (copied.value = false), 2000);
  }
  emit("copied", outcome);
}
</script>

<template>
  <button type="button" class="copy-button" :class="`is-${shape}`" @click="copy">
    <component :is="copied ? IconCheck : IconCopy" :size="shape === 'chip' ? 12 : 14" />{{ copied ? "Copied" : label }}
  </button>
</template>

<style scoped>
.copy-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.copy-button:hover {
  background: var(--rl-raised);
}
.copy-button.is-tiny {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  background: transparent;
  font-size: 12px;
}
.copy-button.is-chip {
  height: 24px;
  padding: 0 8px;
  border-radius: 8px;
  background: transparent;
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 12px;
}
.copy-button.is-tiny:hover,
.copy-button.is-chip:hover {
  background: var(--rl-raised);
}
@media (max-width: 767.98px) {
  .copy-button.is-chip {
    height: 44px;
    padding: 0 10px;
    border: 0;
  }
}
</style>
