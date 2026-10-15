<script setup lang="ts">
import { computed } from "vue";
import type { PublicJob } from "@/api/test";
import JsonBlock from "@/components/shared/JsonBlock.vue";
import { safeMediaUrl } from "@/lib/safe-media-url";

// The status line, the media of the job and its JSON. A media link is drawn only if it is a same-origin /m/ share link;
// any other (a script URL, another host) is shown as text, never as a source.
const props = defineProps<{ status: string; job: PublicJob | null; kind: string }>();

const media = computed(() =>
  [...(props.job?.result_urls ?? []), ...(props.job?.images ?? [])].map((url) => ({ url, safe: safeMediaUrl(url) })),
);
</script>

<template>
  <section class="test-result">
    <h3>Result</h3>
    <div class="test-result__status" role="status">{{ status }}</div>
    <div class="test-result__media">
      <template v-for="(m, i) in media" :key="i">
        <template v-if="m.safe">
          <img v-if="kind === 'image'" :src="m.safe" alt="" />
          <video v-else :src="m.safe" controls />
        </template>
        <p v-else class="test-result__unsafe">link not shown: {{ m.url }}</p>
      </template>
    </div>
    <JsonBlock v-if="job" :value="job" />
  </section>
</template>

<style scoped>
.test-result {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.test-result h3 {
  margin: 0;
  font-size: 16px;
}
.test-result__status {
  color: var(--rl-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.test-result__media {
  display: grid;
  gap: 8px;
}
.test-result__media img,
.test-result__media video {
  max-width: 100%;
  border-radius: var(--el-border-radius-base, 6px);
}
.test-result__unsafe {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
</style>
