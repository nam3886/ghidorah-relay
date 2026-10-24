<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { IconCheck, IconCopy } from "@/components/shared/icons";
import { copyText } from "@/components/shared/clipboard";
import { notify } from "@/lib/notify";

// The job id chip under the drawer's title (JobDetail `idChip`): the 8-character id in mono with a 12 px copy glyph,
// 24 high on the head fill. Same outcomes as CopyButton: when no copy path worked the owner is told what to do; an
// aborted copy (the drawer closed) touches nothing.
const props = defineProps<{ text: string; label: string }>();

const done = ref(false);
const chip = ref<HTMLElement | null>(null);
let timer: ReturnType<typeof setTimeout> | undefined;
const abort = new AbortController();
onBeforeUnmount(() => {
  abort.abort();
  clearTimeout(timer);
});

async function copy(): Promise<void> {
  const outcome = await copyText(props.text, () => chip.value, abort.signal);
  if (outcome === "aborted") return;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    notify.success("Job id copied");
    done.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (done.value = false), 2000);
  }
}
</script>

<template>
  <button type="button" class="job-copy" :aria-label="props.label" @click="copy">
    <span ref="chip" class="mono">{{ props.text }}</span>
    <IconCheck v-if="done" :size="12" :stroke="2" /><IconCopy v-else :size="12" :stroke="2" />
  </button>
</template>

<style scoped>
.job-copy {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 6px;
  background: var(--rl-head);
  color: var(--rl-body);
  font-size: 12px;
  cursor: pointer;
}
.job-copy:hover {
  background: var(--rl-raised);
}
.job-copy:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.mono {
  font-family: var(--rl-mono);
}
</style>
