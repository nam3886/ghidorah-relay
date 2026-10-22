<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { IconCheck, IconCopy } from "@/components/shared/icons";
import { copyText, type CopyOutcome } from "@/components/shared/clipboard";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { notify } from "@/lib/notify";

// The two copy buttons of the drawer, drawn as the canvas does: `id` is the mono chip under the title, `prompt` the
// tiny "Copy prompt" button. Same outcomes as CopyButton: when no copy path worked the text is selected (selectFrom)
// and the owner is told what to press; an aborted copy (the drawer closed) touches nothing.
const props = defineProps<{
  variant: "id" | "prompt";
  text: string;
  label: string;
  selectFrom?: () => HTMLElement | null | undefined;
}>();
const emit = defineEmits<{ copied: [outcome: CopyOutcome] }>();
const { isPhone } = useBreakpoint();

const done = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
const abort = new AbortController();
onBeforeUnmount(() => {
  abort.abort();
  clearTimeout(timer);
});

async function copy(): Promise<void> {
  const outcome = await copyText(props.text, props.selectFrom, abort.signal);
  if (outcome === "aborted") return;
  if (outcome === "select") notify.info("Press Ctrl+C to copy the selected text");
  else if (outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
  else {
    done.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (done.value = false), 2000);
  }
  emit("copied", outcome);
}
</script>

<template>
  <button
    type="button"
    class="job-copy"
    :class="`job-copy--${props.variant}`"
    :aria-label="props.variant === 'id' ? props.label : undefined"
    @click="copy"
  >
    <template v-if="props.variant === 'id'">
      <span class="job-copy__text">{{ props.text }}</span>
      <!-- the phone chip draws its icon at 14 (P-JobDetail), the desktop one at 12 -->
      <IconCheck v-if="done" :size="isPhone ? 14 : 12" /><IconCopy v-else :size="isPhone ? 14 : 12" />
    </template>
    <template v-else><IconCopy class="job-copy__icon" :size="14" />{{ done ? "Copied" : props.label }}</template>
  </button>
</template>

<style scoped>
.job-copy {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--rl-line-strong);
  background: transparent;
  color: var(--rl-fg);
  cursor: pointer;
  font-family: inherit;
}
.job-copy:hover {
  background: var(--rl-raised);
}
.job-copy--id {
  gap: 6px;
  height: 24px;
  padding: 0 8px;
  border-radius: 8px;
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 12px;
}
.job-copy--prompt {
  height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  font-size: 12px;
}
/* the icon is drawn on a phone only (the desktop button is the words alone) */
.job-copy__icon {
  display: none;
}
@media (max-width: 767.98px) {
  .job-copy__icon {
    display: inline;
  }
  .job-copy--id {
    height: 44px;
    padding: 0 10px;
    border: 0;
  }
  .job-copy--prompt {
    gap: 6px;
    height: 44px;
    padding: 0 12px;
    border-radius: 10px;
    font-size: 13px;
  }
}
</style>
