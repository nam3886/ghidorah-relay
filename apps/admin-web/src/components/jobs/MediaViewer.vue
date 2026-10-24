<script lang="ts">
import type { JobMediaItem } from "@/api/jobs-detail";

export interface ViewerItem {
  item: JobMediaItem;
  src: string;
  kind: "image" | "video";
}
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { fetchDownload, type JobDetail } from "@/api/jobs-detail";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { useBusy } from "@/composables/use-busy";
import { saveBlob } from "./blob-actions";
import { fmtBytes, shortId } from "./job-text";

// The media viewer (MediaViewer.dc.html): the whole screen on a dark scrim in both themes, its bars following the theme.
// The top bar names the file and its facts, with Download · D and Close · Esc; Previous and Next sit at the sides.
// An image has the tool pill (zoom out, the zoom, zoom in, fit or actual size, rotate left and right) and the strip of
// every result; a video plays in our own controls (seek, play or pause, back 5 s, the time, mute, volume, speed, loop,
// full screen), so no browser menu ever opens. Keys: Escape closes, the arrows step, + and − zoom, D downloads, Space
// plays or pauses. Only same-origin media reach it (the drawer passes `src` from safeMediaUrl). Element Plus's dialog
// holds the focus and takes the next layer over the drawer.
const props = defineProps<{ job: JobDetail; items: ViewerItem[]; noun: string }>();
const open = defineModel<number | null>({ default: null });

const at = ref(0);
const current = computed(() => props.items[at.value] ?? null);
watch(
  open,
  (i) => {
    if (i !== null) at.value = i;
  },
  { immediate: true },
);
const visible = computed({
  get: () => open.value !== null,
  set: (v: boolean) => {
    if (!v) open.value = null;
  },
});

const EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/gif": "gif",
  "video/mp4": "mp4",
  "video/webm": "webm",
};
const fileName = (m: JobMediaItem): string =>
  `job-${shortId(props.job.id)}-${m.idx}${EXT[m.mime] ? `.${EXT[m.mime]}` : ""}`;
const facts = computed(() => {
  const c = current.value;
  if (!c) return "";
  const m = c.item;
  return [
    `${props.noun} ${at.value + 1} of ${props.items.length}`,
    m.width && m.height ? `${m.width}×${m.height}` : "",
    m.duration_s ? `${m.duration_s.toFixed(1)} s` : "",
    fmtBytes(m.size),
    props.job.model,
  ]
    .filter(Boolean)
    .join(" · ");
});

function step(by: number): void {
  const next = at.value + by;
  if (next < 0 || next >= props.items.length) return;
  at.value = next;
}

// ---- the image: zoom by steps of 25 % from 25 % to 400 % of the fitted size, rotate by quarter turns
const zoom = ref(1);
const turn = ref(0);
const img = ref<HTMLImageElement | null>(null);
watch(at, () => {
  zoom.value = 1;
  turn.value = 0;
});
const zoomBy = (d: number): void => {
  zoom.value = Math.min(4, Math.max(0.25, Math.round((zoom.value + d) * 100) / 100));
};
// fitted (100 %) or the picture's own pixels, and back
function fitOrActual(): void {
  const el = img.value;
  const actual = el && el.clientWidth ? el.naturalWidth / el.clientWidth : 1;
  zoom.value = zoom.value === 1 ? Math.min(4, Math.max(0.25, Math.round(actual * 100) / 100)) : 1;
}
const transform = computed(() => ({ transform: `scale(${zoom.value}) rotate(${turn.value * 90}deg)` }));

// ---- the video
const video = ref<HTMLVideoElement | null>(null);
const playing = ref(false);
const time = ref(0);
const length = ref(0);
const muted = ref(false);
const volume = ref(0.7);
const loop = ref(true);
const SPEEDS = [1, 1.5, 2, 0.5];
const speed = ref(1);
const figure = ref<HTMLElement | null>(null);
const clock = (s: number): string => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
watch(at, () => {
  playing.value = false;
  time.value = 0;
});
function toggle(): void {
  const v = video.value;
  if (!v) return;
  if (v.paused) void v.play?.();
  else v.pause();
}
function seek(event: Event): void {
  const v = video.value;
  if (v) v.currentTime = Number((event.target as HTMLInputElement).value);
}
function back5(): void {
  const v = video.value;
  if (v) v.currentTime = Math.max(0, v.currentTime - 5);
}
function setVolume(event: Event): void {
  volume.value = Number((event.target as HTMLInputElement).value);
  if (video.value) video.value.volume = volume.value;
  if (volume.value > 0) muted.value = false;
}
function nextSpeed(): void {
  speed.value = SPEEDS[(SPEEDS.indexOf(speed.value) + 1) % SPEEDS.length]!;
  if (video.value) video.value.playbackRate = speed.value;
}
function fullScreen(): void {
  void figure.value?.requestFullscreen?.();
}
const played = computed(() => (length.value ? (time.value / length.value) * 100 : 0));

// ---- download, keys
const { busy, run } = useBusy();
const download = () =>
  run(async () => {
    const c = current.value;
    if (c) saveBlob(await fetchDownload(props.job.id, c.item.idx), fileName(c.item));
  });

function onKey(event: KeyboardEvent): void {
  if (!visible.value || event.ctrlKey || event.metaKey || event.altKey) return;
  const el = event.target instanceof Element ? event.target : null;
  if (el?.closest("input[type=text], textarea")) return;
  const k = event.key;
  if (k === "ArrowLeft" && !el?.closest("input")) step(-1);
  else if (k === "ArrowRight" && !el?.closest("input")) step(1);
  else if ((k === "+" || k === "=") && current.value?.kind === "image") zoomBy(0.25);
  else if ((k === "-" || k === "−") && current.value?.kind === "image") zoomBy(-0.25);
  else if (k === "d" || k === "D") void download();
  else if (k === " " && current.value?.kind === "video" && !el?.closest("button")) {
    event.preventDefault();
    toggle();
  } else return;
}
watch(visible, (v) => {
  if (v) document.addEventListener("keydown", onKey);
  else document.removeEventListener("keydown", onKey);
});
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));

// the tool and player glyphs (MediaViewer.dc.html), 18 px at stroke 2
const G = {
  fit: ["M4 9V4h5", "M20 9V4h-5", "M4 15v5h5", "M20 15v5h-5"],
  rotLeft: ["M3 12a9 9 0 1 0 2.6-6.4", "M3 4v5h5"],
  rotRight: ["M21 12a9 9 0 1 1-2.6-6.4", "M21 4v5h-5"],
  play: ["M8 5.5v13l11-6.5z"],
  pause: ["M9 5v14", "M15 5v14"],
  back5: ["M3 12a9 9 0 1 0 2.6-6.4", "M3 4v5h5"],
  sound: ["M4 9v6h4l5 4V5L8 9z", "M16.5 8.5a5 5 0 0 1 0 7"],
  muted: ["M4 9v6h4l5 4V5L8 9z", "m22 9-6 6", "m16 9 6 6"],
  loop: ["M17 2l3 3-3 3", "M4 11V9a4 4 0 0 1 4-4h12", "M7 22l-3-3 3-3", "M20 13v2a4 4 0 0 1-4 4H4"],
  zoomIn: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "M8 11h6", "M11 8v6"],
  zoomOut: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "M8 11h6"],
};
</script>

<template>
  <el-dialog
    v-model="visible"
    class="rl-viewer"
    modal-class="rl-viewer-scrim"
    fullscreen
    append-to-body
    destroy-on-close
    :show-close="false"
    :aria-label="current?.kind === 'video' ? 'Video player' : 'Image viewer'"
    data-testid="media-viewer"
  >
    <template #header>
      <div v-if="current" class="mv__top">
        <div class="mv__name">
          <span class="mono mv__file">{{ fileName(current.item) }}</span>
          <span class="num mv__facts">{{ facts }}</span>
        </div>
        <span class="mv__actions">
          <IconButton
            icon="download"
            label="Download"
            shortcut="D"
            placement="bottom"
            :disabled="busy"
            @click="download"
          />
          <IconButton
            icon="close"
            label="Close"
            shortcut="Esc"
            placement="bottom-end"
            :glyph="18"
            @click="visible = false"
          />
        </span>
      </div>
    </template>

    <div v-if="current" class="mv">
      <div class="mv__stage">
        <IconButton
          icon="prev"
          :label="current.kind === 'video' ? 'Previous result' : `Previous ${noun.toLowerCase()}`"
          :glyph="22"
          :disabled="at === 0"
          @click="step(-1)"
        />
        <div v-if="current.kind === 'image'" class="mv__frame">
          <img
            ref="img"
            :key="current.src"
            class="mv__img"
            :src="current.src"
            :alt="`${job.title || 'Result'}, ${noun.toLowerCase()} ${at + 1} of ${items.length}`"
            :style="transform"
          />
        </div>
        <figure v-else ref="figure" class="mv__player">
          <video
            ref="video"
            :key="current.src"
            class="mv__video"
            :src="current.src"
            :loop="loop"
            :muted="muted"
            preload="metadata"
            playsinline
            @click="toggle"
            @play="playing = true"
            @pause="playing = false"
            @timeupdate="time = ($event.target as HTMLVideoElement).currentTime"
            @loadedmetadata="length = ($event.target as HTMLVideoElement).duration || 0"
          />
          <div class="mv__controls">
            <input
              class="mv__seek"
              type="range"
              min="0"
              :max="length || 0"
              step="0.1"
              :value="time"
              aria-label="Seek"
              :aria-valuetext="`${clock(time)} of ${clock(length)}`"
              :style="{ '--played': `${played}%` }"
              @input="seek"
            />
            <div class="mv__row">
              <IconButton
                icon="next"
                :paths="playing ? G.pause : G.play"
                :glyph="18"
                :label="playing ? 'Pause' : 'Play'"
                @click="toggle"
              />
              <IconButton icon="rotate" :paths="G.back5" :glyph="18" label="Back 5 seconds" @click="back5" />
              <span class="num mv__time">{{ clock(time) }} / {{ clock(length) }}</span>
              <span class="mv__gap" />
              <IconButton
                icon="next"
                :paths="muted ? G.muted : G.sound"
                :glyph="18"
                :label="muted ? 'Unmute' : 'Mute'"
                @click="muted = !muted"
              />
              <input
                class="mv__vol"
                type="range"
                min="0"
                max="1"
                step="0.05"
                :value="muted ? 0 : volume"
                aria-label="Volume"
                :style="{ '--played': `${(muted ? 0 : volume) * 100}%` }"
                @input="setVolume"
              />
              <button type="button" class="num mv__speed" aria-label="Playback speed" @click="nextSpeed">
                {{ speed }}×
              </button>
              <IconButton
                icon="rotate"
                :paths="G.loop"
                :glyph="18"
                label="Loop"
                :toggled="loop"
                @click="loop = !loop"
              />
              <IconButton
                icon="expand"
                :paths="G.fit"
                :glyph="18"
                label="Full screen"
                placement="top-end"
                @click="fullScreen"
              />
            </div>
          </div>
        </figure>
        <IconButton
          icon="next"
          :label="current.kind === 'video' ? 'Next result' : `Next ${noun.toLowerCase()}`"
          :glyph="22"
          :disabled="at >= items.length - 1"
          @click="step(1)"
        />
      </div>

      <div class="mv__foot">
        <div v-if="current.kind === 'image'" class="mv__tools" role="toolbar" aria-label="Image tools">
          <IconButton
            icon="zoomOut"
            :paths="G.zoomOut"
            :glyph="18"
            label="Zoom out"
            shortcut="−"
            @click="zoomBy(-0.25)"
          />
          <span class="num mv__zoom">{{ Math.round(zoom * 100) }}%</span>
          <IconButton icon="zoomIn" :paths="G.zoomIn" :glyph="18" label="Zoom in" shortcut="+" @click="zoomBy(0.25)" />
          <span class="mv__sep" aria-hidden="true" />
          <IconButton icon="expand" :paths="G.fit" :glyph="18" label="Fit or actual size" @click="fitOrActual" />
          <span class="mv__sep" aria-hidden="true" />
          <IconButton icon="rotate" :paths="G.rotLeft" :glyph="18" label="Rotate left" @click="turn--" />
          <IconButton icon="rotate" :paths="G.rotRight" :glyph="18" label="Rotate right" @click="turn++" />
        </div>
        <div v-if="items.length > 1" class="mv__thumbs">
          <ArrowScroller label="All results" :active-key="at">
            <button
              v-for="(it, i) in items"
              :key="it.item.idx"
              type="button"
              class="mv__thumb"
              :class="{ 'is-on': i === at }"
              :aria-label="`Show ${noun.toLowerCase()} ${i + 1}`"
              :aria-current="i === at ? 'true' : undefined"
              @click="at = i"
            >
              <img v-if="it.kind === 'image'" :src="it.src" alt="" />
              <span v-else class="mv__thumb-video" aria-hidden="true">▶</span>
            </button>
          </ArrowScroller>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.mv__top {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
}
.mv__name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.mv__file {
  font-size: 15px;
  font-weight: 500;
  color: #ffffff;
  overflow-wrap: anywhere;
}
.mv__facts {
  font-size: 12px;
  color: var(--mv-meta);
}
.mv__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.mv {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.mv__stage {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  min-height: 0;
  padding: 0 24px;
}
.mv__frame {
  display: grid;
  flex: 1;
  place-items: center;
  align-self: stretch;
  min-width: 0;
  overflow: hidden;
}
.mv__img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  border-radius: 6px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  transition: transform 120ms ease;
}
.mv__player {
  position: relative;
  width: 1040px;
  max-width: 100%;
  max-height: 100%;
  aspect-ratio: 16 / 9;
  margin: 0;
  overflow: hidden;
  border-radius: 12px;
  background: #000;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}
.mv__video {
  display: block;
  width: 100%;
  height: 100%;
  cursor: pointer;
}
.mv__controls {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 28px 16px 10px;
  background: rgba(0, 0, 0, 0.62);
}
.mv__row {
  display: flex;
  align-items: center;
  gap: 4px;
}
.mv__time {
  margin-left: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  white-space: nowrap;
}
.mv__gap {
  flex: 1;
}
.mv__speed {
  height: 28px;
  padding: 0 8px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 6px;
  background: transparent;
  color: #ffffff;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.mv__speed:focus-visible {
  outline: 2px solid #ffffff;
  outline-offset: 2px;
}
/* the seek bar and the volume: our own track, the played part in brand, no browser chrome */
.mv__seek,
.mv__vol {
  appearance: none;
  height: 6px;
  margin: 0;
  border-radius: 3px;
  background: linear-gradient(to right, var(--mv-played) var(--played), rgba(255, 255, 255, 0.22) var(--played));
  cursor: pointer;
}
.mv__seek {
  width: 100%;
}
.mv__vol {
  width: 72px;
  height: 4px;
  margin: 0 6px;
  background: linear-gradient(to right, #ffffff var(--played), rgba(255, 255, 255, 0.3) var(--played));
}
.mv__seek::-webkit-slider-thumb,
.mv__vol::-webkit-slider-thumb {
  appearance: none;
  width: 14px;
  height: 14px;
  border: 0;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 0 0 4px rgba(139, 92, 246, 0.35);
}
.mv__vol::-webkit-slider-thumb {
  width: 10px;
  height: 10px;
  box-shadow: none;
}
.mv__seek::-moz-range-thumb,
.mv__vol::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border: 0;
  border-radius: 50%;
  background: #ffffff;
}
.mv__seek:focus-visible,
.mv__vol:focus-visible {
  outline: 2px solid #ffffff;
  outline-offset: 4px;
}
.mv__foot {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 12px 24px 24px;
}
.mv__tools {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 999px;
  background: var(--mv-bar);
  color: var(--mv-bar-fg);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
}
.mv__zoom {
  min-width: 48px;
  font-size: 13px;
  font-weight: 700;
  text-align: center;
}
.mv__sep {
  width: 1px;
  height: 20px;
  margin: 0 4px;
  background: var(--mv-line);
}
.mv__thumbs {
  max-width: 100%;
}
.mv__thumb {
  flex: none;
  width: 52px;
  height: 52px;
  padding: 2px;
  border: 2px solid transparent;
  border-radius: 10px;
  background: transparent;
  opacity: 0.6;
  cursor: pointer;
}
.mv__thumb.is-on {
  border-color: #ffffff;
  opacity: 1;
}
.mv__thumb img,
.mv__thumb-video {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  border-radius: 7px;
  object-fit: cover;
  background: #2b2440;
  color: #ffffff;
  font-size: 14px;
}

/* the round buttons on the scrim, the tools in their pill, the player's ghost controls */
.mv__actions :deep(.ib),
.mv__stage > :deep(.ib-wrap .ib) {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--mv-bar);
  color: var(--mv-bar-fg);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
}
.mv__stage > :deep(.ib-wrap .ib) {
  width: 48px;
  height: 48px;
}
.mv__actions :deep(.ib:hover),
.mv__stage > :deep(.ib-wrap .ib:hover) {
  background: var(--mv-hover);
  color: var(--mv-bar-fg);
}
.mv__stage > :deep(.ib-wrap .ib:disabled) {
  opacity: 0.35;
  background: var(--mv-bar);
}
.mv__tools :deep(.ib) {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  color: var(--mv-bar-fg);
}
.mv__tools :deep(.ib:hover) {
  background: var(--mv-hover);
  color: var(--mv-bar-fg);
}
.mv__row :deep(.ib) {
  width: 36px;
  height: 36px;
  color: #ffffff;
}
.mv__row :deep(.ib:hover),
.mv__row :deep(.ib[aria-pressed="true"]) {
  background: rgba(255, 255, 255, 0.14);
  color: #ffffff;
}
@media (max-width: 767px) {
  .mv__stage {
    gap: 8px;
    padding: 0 8px;
  }
  .mv__stage > :deep(.ib-wrap .ib) {
    width: 44px;
    height: 44px;
  }
  .mv__top {
    padding: 12px 12px 12px 16px;
  }
  .mv__vol {
    display: none;
  }
}
</style>

<!-- Not scoped: the dialog is Element Plus's, teleported to <body>. The scrim and the bars, light and dark. -->
<style>
html:root .rl-viewer-scrim {
  --mv-scrim: #14151d;
  background: var(--mv-scrim);
}
html.dark:root .rl-viewer-scrim {
  --mv-scrim: #05060a;
}
html:root .el-dialog.rl-viewer {
  --mv-bar: #ffffff;
  --mv-bar-fg: #1a1b25;
  --mv-hover: #f2f3f7;
  --mv-line: #e0e3ea;
  --mv-meta: #c3c6d4;
  --mv-played: #8b5cf6;
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  background: transparent;
  box-shadow: none;
  color: #ffffff;
}
html.dark:root .el-dialog.rl-viewer {
  --mv-bar: #1b1d28;
  --mv-bar-fg: #ecedf3;
  --mv-hover: #262838;
  --mv-line: #2e3142;
  --mv-meta: #a3a6b8;
  --mv-played: #a78bfa;
}
html:root .el-dialog.rl-viewer .el-dialog__header {
  flex: none;
  margin: 0;
  padding: 0;
}
html:root .el-dialog.rl-viewer .el-dialog__body {
  flex: 1;
  min-height: 0;
  padding: 0;
  color: #ffffff;
}
</style>
