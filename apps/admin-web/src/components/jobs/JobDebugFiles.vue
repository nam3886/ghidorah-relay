<script setup lang="ts">
import { ref, watch } from "vue";
import { fetchDebug, type JobDetail } from "@/api/jobs-detail";
import { openBlob } from "@/components/jobs/blob-actions";
import { useBusy } from "@/composables/use-busy";

// Debug files are captured upstream pages: a raster image opens in a new tab as a blob, anything else is shown as text
// (never parsed as markup). They are only ever fetched through the admin session. Drawn as JobDetailTabs: small mono
// buttons on the head fill.
const props = defineProps<{ job: JobDetail }>();
const { busy, run } = useBusy();
const text = ref<string | null>(null);

// Another job's detail starts without the last file's text.
watch(
  () => props.job.id,
  () => (text.value = null),
);

const open = (idx: number) =>
  run(async () => {
    const file = await fetchDebug(props.job.id, idx);
    if (file.kind === "image") openBlob(file.blob);
    else text.value = file.text;
  });
</script>

<template>
  <p v-if="!props.job.debug_files.length" class="debug-none">None</p>
  <div v-else class="debug">
    <div class="debug-buttons">
      <button
        v-for="d in props.job.debug_files"
        :key="d.idx"
        type="button"
        class="mono debug-btn"
        :disabled="busy"
        @click="open(d.idx)"
      >
        File {{ d.idx }} ({{ d.mime }})
      </button>
    </div>
    <pre v-if="text !== null" class="debug-text">{{ text }}</pre>
  </div>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.debug-none {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.debug {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.debug-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.debug-btn {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-head);
  color: var(--rl-body);
  font-size: 12px;
  cursor: pointer;
}
.debug-btn:hover {
  background: var(--rl-raised);
}
.debug-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.debug-btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.debug-text {
  margin: 0;
  max-height: 18rem;
  padding: 12px 14px;
  overflow: auto;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
  color: var(--rl-body);
  font-family: var(--rl-mono);
  font-size: 12px;
  line-height: 20px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
