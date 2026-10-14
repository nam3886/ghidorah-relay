<script setup lang="ts">
import { ref, watch } from "vue";
import { fetchDebug, type JobDetail } from "@/api/jobs-detail";
import { openBlob } from "@/components/jobs/blob-actions";
import { useBusy } from "@/composables/use-busy";

// Debug files are captured upstream pages: a raster image opens in a new tab as a blob, anything else is shown as text
// (never parsed as markup). They are only ever fetched through the admin session.
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
  <div v-else>
    <div class="debug-buttons">
      <el-button v-for="d in props.job.debug_files" :key="d.idx" size="small" :disabled="busy" @click="open(d.idx)"
        >File {{ d.idx }} ({{ d.mime }})</el-button
      >
    </div>
    <pre v-if="text !== null" class="debug-text">{{ text }}</pre>
  </div>
</template>

<style scoped>
.debug-none {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
.debug-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.debug-text {
  margin: 8px 0 0;
  padding: 8px;
  max-height: 18rem;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--rl-surface-2);
  border-radius: var(--el-border-radius-base, 6px);
}
</style>
