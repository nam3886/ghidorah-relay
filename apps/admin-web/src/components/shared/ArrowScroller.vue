<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { CHEVRONS_LEFT, CHEVRONS_RIGHT } from "@/components/shared/icons/ledger-icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { vWheelX } from "./wheel-scroll";

// The arrow scroller (ScrollerSpec): a chip, tab or picker row stays on one line at every size and scrolls sideways.
// No scrollbar, 4 px of padding above and below (rings and borders are never cut), a 24 px fade on the side that has
// more, and a double-chevron arrow laid over that fade (a 32 px circle in a 44 px hit area). The arrows are always in
// the DOM and fade in or out over 150 ms (hidden: opacity 0, pointer-events none, aria-hidden, tabindex -1), so the row
// never moves. An arrow shows once 8 px or more remain on its side and hides once less than 2 px remain (in between
// it keeps its state); the ends are found by an IntersectionObserver on two 1 px sentinels, never by scroll events. An
// arrow click scrolls about 80% of the visible width, smoothly; the active item (`activeKey` changes) scrolls into view.
// A vertical wheel over the row scrolls it sideways until an end, then the page (v-wheel-x, wheel-scroll.ts).
// No row shows a scrollbar, a wide table included (the owner, over ScrollerSpec's thin bar under a table).
// The gap between items is `--asc-gap` (8 px; a caller sets that, never `gap`): the sentinels cancel exactly it, so the
// first and last items sit on the row's padding at its two ends.
// `table`: a wide table: no gap and no padding, and its arrows stick to the middle of the window.
// `count`: on a phone a set of 5 items or fewer wraps onto two lines instead (no arrows).
const props = withDefaults(defineProps<{ label: string; table?: boolean; count?: number; activeKey?: unknown }>(), {
  table: false,
  count: undefined,
  activeKey: undefined,
});

const { isPhone } = useBreakpoint();
const wraps = computed(() => !props.table && isPhone.value && props.count !== undefined && props.count <= 5);

const row = ref<HTMLElement | null>(null);
const startEl = ref<HTMLElement | null>(null);
const endEl = ref<HTMLElement | null>(null);
const left = ref(false);
const right = ref(false);

// The two thresholds as two observers on the same sentinels: `far` sees a sentinel within 7 px of the edge (out of it,
// 8 px or more remain: show), `near` within 1 px (in it, less than 2 px remain: hide).
const seen = { far: { start: true, end: true }, near: { start: true, end: true } };
function decide(): void {
  if (!seen.far.start) left.value = true;
  else if (seen.near.start) left.value = false;
  if (!seen.far.end) right.value = true;
  else if (seen.near.end) right.value = false;
}
let observers: IntersectionObserver[] = [];
function watchEnds(): void {
  stop();
  if (wraps.value || !row.value || !startEl.value || !endEl.value) return;
  if (typeof IntersectionObserver !== "function") return;
  const start = startEl.value;
  for (const [name, margin] of [
    ["far", "0px 7px 0px 7px"],
    ["near", "0px 1px 0px 1px"],
  ] as const) {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen[name][e.target === start ? "start" : "end"] = e.isIntersecting;
        decide();
      },
      { root: row.value, rootMargin: margin, threshold: 0 },
    );
    io.observe(start);
    io.observe(endEl.value);
    observers.push(io);
  }
}
function stop(): void {
  for (const io of observers) io.disconnect();
  observers = [];
  left.value = false;
  right.value = false;
  seen.far = { start: true, end: true };
  seen.near = { start: true, end: true };
}

function by(direction: -1 | 1): void {
  const el = row.value;
  if (!el) return;
  el.scrollBy({ left: direction * Math.round(el.clientWidth * 0.8), behavior: "smooth" });
}

const ACTIVE = '[aria-current="page"], [aria-current="true"], [aria-pressed="true"], [aria-selected="true"]';
// Only the row scrolls, sideways: scrollIntoView would also scroll the page to a row below the fold (Jobs on a short
// window jumped down to its chips when it opened).
async function revealActive(): Promise<void> {
  await nextTick();
  const el = row.value;
  const active = el?.querySelector<HTMLElement>(ACTIVE);
  if (!el || !active) return;
  // the row's padding box: a segmented track keeps its inset round the chosen item
  const edge = el.getBoundingClientRect();
  const pad = getComputedStyle(el);
  const box = {
    left: edge.left + (parseFloat(pad.paddingLeft) || 0),
    right: edge.right - (parseFloat(pad.paddingRight) || 0),
  };
  const item = active.getBoundingClientRect();
  if (item.left < box.left) el.scrollLeft -= box.left - item.left;
  else if (item.right > box.right) el.scrollLeft += item.right - box.right;
}

onMounted(() => {
  watchEnds();
  void revealActive();
});
watch(wraps, async () => {
  await nextTick();
  watchEnds();
});
watch(() => props.activeKey, revealActive);
onBeforeUnmount(stop);
defineExpose({ left, right, by, revealActive });
</script>

<template>
  <div
    class="asc"
    :class="{ 'asc--table': props.table, 'asc--wrap': wraps, 'asc--l': left, 'asc--r': right }"
    data-testid="arrow-scroller"
  >
    <div ref="row" v-wheel-x class="asc__row" role="group" :aria-label="props.label">
      <span ref="startEl" class="asc__sentinel asc__sentinel--start" aria-hidden="true"></span>
      <slot />
      <span ref="endEl" class="asc__sentinel asc__sentinel--end" aria-hidden="true"></span>
    </div>
    <template v-if="!wraps">
      <div class="asc__col asc__col--left">
        <button
          type="button"
          class="asc__arrow"
          :class="{ 'is-on': left }"
          :aria-label="`Scroll ${props.label.toLowerCase()} left`"
          :aria-hidden="left ? undefined : 'true'"
          :tabindex="left ? 0 : -1"
          data-testid="scroll-left"
          @click="by(-1)"
        >
          <span class="asc__dot"><PathIcon :paths="CHEVRONS_LEFT" :size="16" :stroke="2.2" /></span>
        </button>
      </div>
      <div class="asc__col asc__col--right">
        <button
          type="button"
          class="asc__arrow"
          :class="{ 'is-on': right }"
          :aria-label="`Scroll ${props.label.toLowerCase()} right`"
          :aria-hidden="right ? undefined : 'true'"
          :tabindex="right ? 0 : -1"
          data-testid="scroll-right"
          @click="by(1)"
        >
          <span class="asc__dot"><PathIcon :paths="CHEVRONS_RIGHT" :size="16" :stroke="2.2" /></span>
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.asc {
  position: relative;
  min-width: 0;
}
.asc__row {
  --asc-gap: 8px;
  display: flex;
  align-items: center;
  gap: var(--asc-gap);
  padding: 4px 0;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
}
.asc__row::-webkit-scrollbar {
  display: none;
}
.asc--l .asc__row {
  -webkit-mask-image: linear-gradient(to right, transparent 0, #000 24px);
  mask-image: linear-gradient(to right, transparent 0, #000 24px);
}
.asc--r .asc__row {
  -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 24px), transparent 100%);
  mask-image: linear-gradient(to right, #000 calc(100% - 24px), transparent 100%);
}
.asc--l.asc--r .asc__row {
  -webkit-mask-image: linear-gradient(to right, transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%);
  mask-image: linear-gradient(to right, transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%);
}
/* 1 px, the negative margin cancels the gap, so they take no room */
.asc__sentinel {
  flex: none;
  width: 1px;
  align-self: stretch;
}
.asc__sentinel--start {
  margin-right: calc(-1px - var(--asc-gap));
}
.asc__sentinel--end {
  margin-left: calc(-1px - var(--asc-gap));
}
.asc__row > :deep(*) {
  flex: none;
}

/* the arrows: a column per side over the fade; the button is centred on the row */
.asc__col {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  width: 44px;
  pointer-events: none;
}
.asc__col--left {
  left: -6px;
}
.asc__col--right {
  right: -6px;
}
.asc__arrow {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition: opacity 150ms ease;
}
.asc__arrow.is-on {
  opacity: 1;
  pointer-events: auto;
}
.asc__dot {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  color: var(--rl-fg);
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-arrow);
}

/* a wide table: no gap or padding; the arrows stick to the middle of the window on a table taller than it (sticky both
   ways inside a full-height column) */
.asc--table .asc__row {
  --asc-gap: 0px;
  padding: 0;
  align-items: stretch;
}
.asc--table .asc__row > :deep(*:not(.asc__sentinel)) {
  flex: 1 0 auto;
}
.asc--table .asc__arrow {
  position: sticky;
  top: calc(50% - 22px);
  bottom: calc(50% - 22px);
}

/* a phone set of 5 or fewer wraps onto two lines */
.asc--wrap .asc__row {
  flex-wrap: wrap;
  overflow: visible;
}
</style>
