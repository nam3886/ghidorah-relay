<script setup lang="ts">
import { computed, ref } from "vue";
import { fetchMediaBlob, type JobDetail, type JobMediaItem } from "@/api/jobs-detail";
import { openBlob } from "@/components/jobs/blob-actions";
import { IconImage } from "@/components/shared/icons";
import { fmtBytes, jobParam } from "@/components/jobs/job-text";
import { useBusy } from "@/composables/use-busy";
import { safeMediaUrl } from "@/lib/safe-media-url";
import MediaViewer, { type ViewerItem } from "./MediaViewer.vue";

// The results (role output) or the references (role input) of a job, drawn as JobDetail.dc.html: results as a grid
// of tiles in their own shape (a video: its first frame on the dark media fill, a white play button and its length;
// the facts under it), references as 96 px tiles. A tile opens the media viewer on it (MediaViewer). A preview is drawn
// only from a same-origin /m/ share link (safeMediaUrl). An item with no usable link (revoked, expired, or a link that
// is not ours) is a plain tile with "Open", which fetches the file through the admin session as a blob. Downloads are
// the drawer's and the viewer's.
const props = defineProps<{ job: JobDetail; role: "output" | "input" }>();
const { busy, run } = useBusy();

interface Shown {
  item: JobMediaItem;
  src: string | null;
  kind: "image" | "video" | null;
  ratio: string;
  facts: string;
  badge: string;
}
const dims = (m: JobMediaItem): string => (m.width && m.height ? `${m.width}×${m.height}` : "");
// A tile takes its picture's shape: the stored size, else the job's ratio, else 2:3 (16:9 for a video).
function ratioOf(m: JobMediaItem, video: boolean): string {
  if (m.width && m.height) return `${m.width} / ${m.height}`;
  const asked = /^(\d+):(\d+)$/.exec(jobParam(props.job.params, "ratio", "aspect_ratio"));
  return asked ? `${asked[1]} / ${asked[2]}` : video ? "16 / 9" : "2 / 3";
}
const shown = computed<Shown[]>(() =>
  props.job.media
    .filter((m) => m.role === props.role)
    .map((item) => {
      const src = safeMediaUrl(item.url);
      const kind =
        src && item.mime.startsWith("image/") ? "image" : src && item.mime.startsWith("video/") ? "video" : null;
      const length = item.duration_s ? `${item.duration_s.toFixed(1)} s` : "";
      const facts = [length, dims(item), fmtBytes(item.size), item.mime.split("/")[1] ?? item.mime];
      return {
        item,
        src,
        kind,
        ratio: ratioOf(item, item.mime.startsWith("video/")),
        facts: facts.filter(Boolean).join(" · "),
        badge: length,
      };
    }),
);
// what the viewer can show: every item with a same-origin link, in order
const viewable = computed<ViewerItem[]>(() =>
  shown.value.filter((s) => s.kind).map((s) => ({ item: s.item, src: s.src!, kind: s.kind! })),
);
const noun = computed(() => (props.role === "output" ? "Result" : "Reference"));
const viewer = ref<number | null>(null);
const openViewer = (s: Shown): void => {
  viewer.value = viewable.value.findIndex((v) => v.item.idx === s.item.idx);
};
const open = (idx: number) => run(async () => openBlob(await fetchMediaBlob(props.job.id, props.role, idx)));
const number = (s: Shown): number => shown.value.indexOf(s) + 1;
</script>

<template>
  <div class="media" :data-role="props.role">
    <p v-if="!shown.length" class="media__none">None</p>
    <div v-else :class="props.role === 'output' ? 'media__grid' : 'media__refs'">
      <figure
        v-for="s in shown"
        :key="s.item.idx"
        class="media__item"
        :class="{ 'is-video': s.kind === 'video' }"
        :data-idx="s.item.idx"
      >
        <!-- a video: its first frame, muted and still, under the play button that opens the viewer -->
        <div
          v-if="s.kind === 'video'"
          class="media__tile media__tile--video"
          :style="props.role === 'output' ? { aspectRatio: s.ratio } : undefined"
        >
          <video class="media__poster" :src="s.src!" preload="metadata" muted playsinline tabindex="-1" />
          <button type="button" class="media__play" :aria-label="`Play video ${number(s)}`" @click="openViewer(s)">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5.5v13l11-6.5z" />
            </svg>
          </button>
          <span v-if="s.badge" class="num media__badge">{{ s.badge }}</span>
        </div>
        <button
          v-else-if="s.kind === 'image'"
          type="button"
          class="media__tile"
          :aria-label="`Open ${noun.toLowerCase()} ${number(s)} in the viewer`"
          :style="props.role === 'output' ? { aspectRatio: s.ratio } : undefined"
          @click="openViewer(s)"
        >
          <img class="media__picture" :src="s.src!" alt="" loading="lazy" />
        </button>
        <div
          v-else
          class="media__tile media__tile--plain"
          :style="props.role === 'output' ? { aspectRatio: s.ratio } : undefined"
        >
          <IconImage :size="22" :stroke="1.6" />
          <span class="num media__meta">{{ fmtBytes(s.item.size) }} · no preview</span>
          <button type="button" class="media__open" aria-label="Open" :disabled="busy" @click="open(s.item.idx)">
            Open
          </button>
        </div>
        <figcaption v-if="props.role === 'output'" class="num media__facts">{{ s.facts }}</figcaption>
      </figure>
    </div>
    <MediaViewer v-model="viewer" :job="job" :items="viewable" :noun="noun" />
  </div>
</template>

<style scoped>
.num {
  font-variant-numeric: tabular-nums;
}
.media__none {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.media__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.media__refs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.media__item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  margin: 0;
}
.media__tile {
  position: relative;
  display: grid;
  place-items: center;
  box-sizing: border-box;
  width: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  border-radius: 12px;
  background: var(--rl-tile-1);
  color: var(--rl-tile-ink);
  font: inherit;
  cursor: zoom-in;
}
.media__refs .media__tile {
  width: 96px;
  height: 96px;
  border: 1px solid var(--rl-line-strong);
  background: var(--p-gemini-soft);
  color: var(--p-gemini-ink);
}
.media__tile:focus-visible,
.media__play:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.media__picture {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.media__tile--video {
  background: var(--rl-media);
  cursor: default;
}
.media__poster {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  pointer-events: none;
}
.media__play {
  position: absolute;
  top: 50%;
  left: 50%;
  display: grid;
  place-items: center;
  box-sizing: border-box;
  width: 48px;
  height: 48px;
  padding: 0 0 0 3px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.92);
  color: #1a1b25;
  transform: translate(-50%, -50%);
  cursor: pointer;
}
.media__refs .media__play {
  width: 36px;
  height: 36px;
}
.media__badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}
.media__tile--plain {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 96px;
  cursor: default;
}
.media__meta {
  font-size: 11px;
}
.media__open {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.media__facts {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
</style>
