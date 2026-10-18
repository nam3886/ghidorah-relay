<script setup lang="ts">
import { computed, ref } from "vue";
import { fetchMediaBlob, type JobDetail, type JobMediaItem } from "@/api/jobs-detail";
import { openBlob } from "@/components/jobs/blob-actions";
import {
  IconChevronLeft,
  IconChevronRight,
  IconClose,
  IconFit,
  IconImage,
  IconPlayFlat,
  IconRefresh,
  IconRotateLeft,
  IconZoomIn,
  IconZoomOut,
} from "@/components/shared/icons";
import { fmtBytes } from "@/components/jobs/job-text";
import { useBusy } from "@/composables/use-busy";
import { safeMediaUrl } from "@/lib/safe-media-url";

// The results (role output) or the references (role input) of a job, drawn as the canvas does: image tiles (2:3) that
// open the viewer, and 16:9 video cards. A preview is drawn only from a same-origin /m/ share link (safeMediaUrl). An
// item with no usable link (revoked, expired, or a link that is not ours) is a plain tile with "Open", which fetches
// the file through the admin session as a blob. Downloads are the drawer's: they sit in its action row.
const props = defineProps<{ job: JobDetail; role: "output" | "input" }>();
const { busy, run } = useBusy();

interface Shown {
  item: JobMediaItem;
  src: string | null;
  kind: "image" | "video" | null;
  size: string;
  ratio: string;
  facts: string[];
  badge: string;
}
const dims = (m: JobMediaItem): string => (m.width && m.height ? `${m.width}×${m.height}` : "");
// A tile takes its picture's shape: the stored size, else the job's ratio, else the canvas's 2:3.
function ratioOf(m: JobMediaItem): string {
  if (m.width && m.height) return `${m.width} / ${m.height}`;
  const asked = /^(\d+):(\d+)$/.exec(String((props.job.params as { ratio?: unknown } | null)?.ratio ?? ""));
  return asked ? `${asked[1]} / ${asked[2]}` : "2 / 3";
}
const clock = (s: number): string => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

const shown = computed<Shown[]>(() =>
  props.job.media
    .filter((m) => m.role === props.role)
    .map((item) => {
      const src = safeMediaUrl(item.url);
      const kind =
        src && item.mime.startsWith("image/") ? "image" : src && item.mime.startsWith("video/") ? "video" : null;
      const facts = [
        item.mime,
        fmtBytes(item.size),
        dims(item),
        item.duration_s ? `${Number(item.duration_s.toFixed(2))} s` : "",
      ];
      return {
        item,
        src,
        kind,
        size: dims(item),
        ratio: ratioOf(item),
        facts: facts.filter(Boolean),
        badge: item.duration_s ? clock(item.duration_s) : "",
      };
    }),
);
const tiles = computed(() => shown.value.filter((s) => s.kind !== "video"));
const videos = computed(() => shown.value.filter((s) => s.kind === "video"));

// Only same-origin links reach `src`, so the viewer never loads anything else.
const previews = computed(() => shown.value.filter((s) => s.kind === "image").map((s) => s.src!));

const noun = computed(() => (props.role === "output" ? "image" : "reference"));
// the index in `previews` the viewer is opened on, null while it is shut; `showing`, the one it shows now
const viewer = ref<number | null>(null);
const showing = ref(0);
const viewerRef = ref<{ setActiveItem: (index: number) => void } | null>(null);
function openViewer(index: number): void {
  viewer.value = index;
  showing.value = index;
}
// the viewer's own previous / next (it wraps around)
const step = (by: number): void => viewerRef.value?.setActiveItem(showing.value + by);
const open = (idx: number) => run(async () => openBlob(await fetchMediaBlob(props.job.id, props.role, idx)));

// A video plays from its own overlay button; the native controls come with it.
const started = ref<Set<number>>(new Set());
const players = ref<Record<number, HTMLVideoElement | null>>({});
function play(idx: number): void {
  started.value = new Set(started.value).add(idx);
  void players.value[idx]?.play?.();
}
</script>

<template>
  <div class="media" :data-role="props.role">
    <p v-if="!shown.length" class="media__none">None</p>
    <div v-if="tiles.length" class="media__grid">
      <template v-for="(s, i) in tiles" :key="s.item.idx">
        <!-- the tile is a button, as drawn: named and reachable by the keyboard whether its picture loads or not
             (Element Plus puts a name given to el-image on its inner picture, which a broken tile does not have) -->
        <button
          v-if="s.kind === 'image'"
          type="button"
          class="media__tile"
          :class="`media__tile--${(i % 4) + 1}`"
          :title="`${s.item.mime} · ${fmtBytes(s.item.size)}`"
          :aria-label="`Open ${noun} ${i + 1} in the viewer`"
          :style="{ aspectRatio: s.ratio }"
          :data-idx="s.item.idx"
          @click="openViewer(previews.indexOf(s.src!))"
        >
          <el-image class="media__picture" :src="s.src!" fit="cover">
            <template #placeholder>
              <span class="media__ph"
                ><IconImage :size="22" :stroke="1.6" /><span class="media__meta">{{ s.size }}</span></span
              >
            </template>
            <template #error>
              <span class="media__ph"
                ><IconImage :size="22" :stroke="1.6" /><span class="media__meta">{{ s.size }}</span></span
              >
            </template>
          </el-image>
        </button>
        <div
          v-else
          class="media__tile media__tile--plain"
          :class="`media__tile--${(i % 4) + 1}`"
          :style="{ aspectRatio: s.ratio }"
          :data-idx="s.item.idx"
        >
          <span class="media__ph">
            <IconImage :size="22" :stroke="1.6" />
            <span class="media__meta">{{ s.size }}</span>
            <span class="media__note">no preview</span>
            <button type="button" class="media__open" aria-label="Open" :disabled="busy" @click="open(s.item.idx)">
              Open
            </button>
          </span>
        </div>
      </template>
    </div>
    <!-- named as drawn: the card `Result`, its button `Play video` (numbered only when there are several) -->
    <figure
      v-for="(s, n) in videos"
      :key="s.item.idx"
      class="media__video"
      :aria-label="videos.length > 1 ? `Result ${n + 1}` : 'Result'"
      :data-idx="s.item.idx"
    >
      <div class="media__frame">
        <video
          :ref="(el) => (players[s.item.idx] = el as HTMLVideoElement | null)"
          :src="s.src!"
          :controls="started.has(s.item.idx)"
          preload="metadata"
        />
        <template v-if="!started.has(s.item.idx)">
          <button
            type="button"
            class="media__play"
            :aria-label="videos.length > 1 ? `Play video ${n + 1}` : 'Play video'"
            @click="play(s.item.idx)"
          >
            <IconPlayFlat :size="22" />
          </button>
          <span v-if="s.badge" class="media__badge">{{ s.badge }}</span>
        </template>
      </div>
      <figcaption class="media__facts">
        <span v-for="f in s.facts" :key="f">{{ f }}</span>
      </figcaption>
    </figure>
    <!-- the viewer of every previewable image, opened on the tile pressed. Its buttons are ours, in the canvas's
         glyphs (the toolbar through its slot; close and the arrows in its body, Element Plus's own hidden below) -->
    <el-image-viewer
      v-if="viewer !== null"
      ref="viewerRef"
      :url-list="previews"
      :initial-index="viewer"
      teleported
      hide-on-click-modal
      @close="viewer = null"
      @switch="(index: number) => (showing = index)"
    >
      <template #toolbar="{ actions, reset }">
        <button type="button" class="viewer__tool" aria-label="Zoom out" @click="actions('zoomOut')">
          <IconZoomOut :size="18" />
        </button>
        <button type="button" class="viewer__tool" aria-label="Zoom in" @click="actions('zoomIn')">
          <IconZoomIn :size="18" />
        </button>
        <span class="viewer__divider" aria-hidden="true" />
        <button type="button" class="viewer__tool" aria-label="Fit or actual size" @click="reset">
          <IconFit :size="18" />
        </button>
        <span class="viewer__divider" aria-hidden="true" />
        <button type="button" class="viewer__tool" aria-label="Rotate left" @click="actions('anticlockwise')">
          <IconRotateLeft :size="18" />
        </button>
        <button type="button" class="viewer__tool" aria-label="Rotate right" @click="actions('clockwise')">
          <IconRefresh :size="18" />
        </button>
      </template>
      <button type="button" class="viewer__btn viewer__close" aria-label="Close" @click="viewer = null">
        <IconClose :size="18" :stroke="2" />
      </button>
      <template v-if="previews.length > 1">
        <button type="button" class="viewer__btn viewer__prev" aria-label="Previous image" @click="step(-1)">
          <IconChevronLeft :size="20" />
        </button>
        <button type="button" class="viewer__btn viewer__next" aria-label="Next image" @click="step(1)">
          <IconChevronRight :size="20" />
        </button>
      </template>
    </el-image-viewer>
  </div>
</template>

<style scoped>
.media {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.media__none {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.media__grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.media__tile {
  display: block;
  aspect-ratio: 2 / 3;
  width: 100%;
  padding: 0;
  border: 0;
  border-radius: 12px;
  background: var(--rl-tile-1);
  color: var(--rl-tile-ink);
  font: inherit;
  overflow: hidden;
  cursor: zoom-in;
}
.media__picture {
  display: block;
  width: 100%;
  height: 100%;
}
.media__tile:hover {
  filter: brightness(0.97);
}
.media__tile--2 {
  background: var(--rl-tile-2);
}
.media__tile--3 {
  background: var(--rl-tile-3);
}
.media__tile--4 {
  background: var(--rl-tile-4);
}
.media__tile--plain {
  cursor: default;
}
/* the canvas's tile is a button: the browser's own line height under its icon and size */
.media__ph {
  width: 100%;
  height: 100%;
  line-height: normal;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.media__meta {
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.media__note {
  font-size: 11px;
}
.media__open {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: transparent;
  color: var(--rl-fg);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.media__open:hover {
  background: var(--rl-raised);
}
.media__video {
  margin: 0;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  overflow: hidden;
}
.media__frame {
  position: relative;
  aspect-ratio: 16 / 9;
  background: var(--rl-media);
}
.media__frame video {
  width: 100%;
  height: 100%;
  display: block;
}
.media__play {
  position: absolute;
  inset: 0;
  margin: auto;
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 28px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  cursor: pointer;
}
.media__badge {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--rl-media-badge);
  color: var(--rl-on-primary);
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.media__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  padding: 10px 14px;
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--rl-muted);
}
@media (max-width: 1279.98px) {
  .media__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-width: 420px;
  }
}
/* The viewer's buttons: close and the arrows as the menu surface's icon buttons (44 square, hairline, radius 10, the
   overlay shadow, raised on hover); the toolbar's as ghost icon buttons (32 square, radius 8) */
.viewer__btn {
  position: absolute;
  z-index: 1;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  border: 1px solid var(--rl-line);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  box-shadow: var(--rl-shadow-pop, 0 12px 32px rgb(10 6 20 / 18%));
  cursor: pointer;
}
.viewer__btn:hover,
.viewer__tool:hover {
  background: var(--rl-raised);
}
.viewer__close {
  top: 40px;
  right: 40px;
}
.viewer__prev,
.viewer__next {
  top: 50%;
  transform: translateY(-50%);
}
.viewer__prev {
  left: 40px;
}
.viewer__next {
  right: 40px;
}
.viewer__tool {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-fg);
  cursor: pointer;
}
.viewer__divider {
  width: 1px;
  height: 20px;
  background: var(--rl-line);
}
</style>

<!-- not scoped: Element Plus's own close and arrows, in its glyphs, are hidden in this viewer, which draws its own -->
<style>
.el-image-viewer__wrapper:has(.viewer__close) .el-image-viewer__close,
.el-image-viewer__wrapper:has(.viewer__close) .el-image-viewer__prev,
.el-image-viewer__wrapper:has(.viewer__close) .el-image-viewer__next {
  display: none;
}
.el-image-viewer__wrapper:has(.viewer__close) .el-image-viewer__actions {
  width: auto;
}
</style>
