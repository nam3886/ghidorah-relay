<script setup lang="ts">
import { computed } from "vue";
import { providerClass } from "@/lib/providers";
import { useProvidersStore } from "@/stores/providers";
import { kindTitle, ownCount } from "./frames-model";
import type { FramesController } from "./use-frames";

// The left list of the desktop page (Frames.dc.html): a group per provider with its colour dot, its note and a chevron,
// the open one listing its kinds (name and where the text comes from: "own 2" or "default"), the current kind on the
// brand tint. Only the open provider's kinds are known, so a closed group's note is "paused" or nothing. Picking a
// provider or a kind over an unsaved draft asks first (the controller does).
const props = defineProps<{ frames: FramesController }>();
const summary = useProvidersStore();

const paused = (name: string): boolean =>
  summary.data?.providers.find((p) => p.provider === name)?.claims_enabled === false;
const note = (name: string): string => {
  if (paused(name)) return "paused";
  if (name !== props.frames.provider || !props.frames.data) return "";
  const n = props.frames.data.kinds.length;
  return `${n} ${n === 1 ? "kind" : "kinds"}`;
};
const isOpen = (name: string): boolean => name === props.frames.provider;
const kindNote = (own: number): string => (own ? `own ${own}` : "default");

// the open provider stays open; another one is picked (the controller asks first over an unsaved draft)
function pick(name: string): void {
  if (!props.frames.opensOff && name !== props.frames.provider) void props.frames.openProvider(name);
}
const current = computed(() => props.frames.shown?.kind ?? "");
</script>

<template>
  <nav class="fsn" aria-label="Providers and kinds" data-testid="frames-nav">
    <div v-for="name in frames.providers" :key="name" class="fsn__group" :class="providerClass(name)">
      <button
        type="button"
        class="fsn__prov"
        :aria-expanded="isOpen(name)"
        :disabled="frames.opensOff"
        :data-testid="`provider-tab-${name}`"
        @click="pick(name)"
      >
        <span class="fsn__dot" aria-hidden="true" />
        <span class="fsn__name">{{ name }}</span>
        <span class="fsn__note" :class="{ 'is-paused': paused(name) }">{{ note(name) }}</span>
        <svg
          class="fsn__chev"
          :class="{ 'is-shut': !isOpen(name) }"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      <div v-if="isOpen(name) && frames.data" class="fsn__kinds">
        <button
          v-for="k in frames.kinds"
          :key="k.kind"
          type="button"
          class="fsn__kind"
          :class="{ 'is-current': k.kind === current }"
          :aria-current="k.kind === current ? 'page' : undefined"
          :disabled="frames.opensOff"
          :data-testid="`frame-kind-${k.kind}`"
          @click="frames.openKind(k.kind)"
        >
          <span class="fsn__kname">{{ kindTitle(k.kind) }}</span>
          <span class="fsn__pill" :class="{ 'is-own': ownCount(k) > 0 }">{{ kindNote(ownCount(k)) }}</span>
        </button>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.fsn {
  flex: 1 1 260px;
  max-width: 300px;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  padding: 8px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.fsn__group + .fsn__group {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--rl-rule);
}
.fsn__prov {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 40px;
  padding: 0 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.fsn__prov:hover:not(:disabled) {
  background: var(--rl-menu-hover);
}
.fsn__prov:focus-visible,
.fsn__kind:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.fsn__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pc);
}
.fsn__name {
  font-weight: 700;
}
.fsn__note {
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-faint);
}
.fsn__note.is-paused {
  color: var(--rl-warn);
}
.fsn__chev {
  flex: none;
  color: var(--rl-faint);
}
.fsn__chev.is-shut {
  transform: rotate(-90deg);
}
.fsn__kinds {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 0 8px;
}
.fsn__kind {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 40px;
  padding: 0 10px 0 26px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}
.fsn__kind:hover:not(:disabled):not(.is-current) {
  background: var(--rl-menu-hover);
}
.fsn__kind.is-current {
  background: var(--rl-primary-soft);
  box-shadow: inset 0 0 0 1px var(--rl-brand);
}
.fsn__kname {
  flex: 1;
  min-width: 0;
}
.fsn__pill {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.fsn__pill.is-own {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
button:disabled {
  cursor: default;
}
</style>
