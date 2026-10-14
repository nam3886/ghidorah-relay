<script setup lang="ts">
import { computed } from "vue";
import { fetchDownload, fetchMediaBlob, type JobDetail, type JobMediaItem } from "@/api/jobs-detail";
import { openBlob, saveBlob } from "@/components/jobs/blob-actions";
import { shortId } from "@/components/jobs/job-text";
import { useBusy } from "@/composables/use-busy";
import { safeMediaUrl } from "@/lib/safe-media-url";

// The results (role output) or the references (role input) of a job. A preview is drawn only from a same-origin /m/
// share link (safeMediaUrl). An item with no usable link (revoked, expired, or a link that is not ours) has no
// preview and gets "Open", which fetches the file through the admin session as a blob.
const props = defineProps<{ job: JobDetail; role: "output" | "input" }>();
const { busy, run } = useBusy();

const items = computed(() => props.job.media.filter((m) => m.role === props.role));

interface Shown {
  item: JobMediaItem;
  src: string | null;
  kind: "image" | "video" | null;
  meta: string;
}
const shown = computed<Shown[]>(() =>
  items.value.map((item) => {
    const src = safeMediaUrl(item.url);
    const kind =
      src && item.mime.startsWith("image/") ? "image" : src && item.mime.startsWith("video/") ? "video" : null;
    const meta = `${item.mime} · ${item.size} B${item.duration_s ? ` · ${item.duration_s}s` : ""}`;
    return { item, src, kind, meta };
  }),
);

const download = (idx: number) =>
  run(async () => saveBlob(await fetchDownload(props.job.id, idx), `job-${shortId(props.job.id)}-${idx}`));
const open = (idx: number) => run(async () => openBlob(await fetchMediaBlob(props.job.id, props.role, idx)));
</script>

<template>
  <p v-if="!shown.length" class="media-none">None</p>
  <div v-else class="media-list">
    <figure v-for="s in shown" :key="s.item.idx" class="media-item" :data-idx="s.item.idx">
      <img v-if="s.kind === 'image'" class="media-item__view" :src="s.src!" alt="" />
      <video v-else-if="s.kind === 'video'" class="media-item__view" :src="s.src!" controls preload="metadata" />
      <span v-else class="media-item__none">no preview</span>
      <figcaption class="media-item__meta">{{ s.meta }}</figcaption>
      <div class="media-item__actions">
        <el-button v-if="!s.kind" size="small" :disabled="busy" @click="open(s.item.idx)">Open</el-button>
        <el-button v-if="props.role === 'output'" size="small" :disabled="busy" @click="download(s.item.idx)"
          >Download</el-button
        >
      </div>
    </figure>
  </div>
</template>

<style scoped>
.media-none,
.media-item__none,
.media-item__meta {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
.media-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.media-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
}
.media-item__view {
  max-height: 12rem;
  max-width: 100%;
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.media-item__actions {
  display: flex;
  gap: 6px;
}
</style>
