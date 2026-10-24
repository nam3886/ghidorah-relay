<script setup lang="ts">
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { providerClass } from "@/lib/providers";
import { useProvidersStore } from "@/stores/providers";
import { kindTitle, ownCount } from "./frames-model";
import type { FramesController } from "./use-frames";

// The picker of a tablet and a phone (FramesTablet, FramesPhone): the provider chips on one scrolling line (a phone
// set of 5 or fewer would wrap), and under them the kinds as a segmented control. The desktop's left list becomes these
// two rows; the editor stacks below them.
const props = defineProps<{ frames: FramesController }>();
const { isPhone } = useBreakpoint();
const summary = useProvidersStore();

const paused = (name: string): boolean =>
  summary.data?.providers.find((p) => p.provider === name)?.claims_enabled === false;
const kindLabel = (name: string, own: number): string =>
  isPhone.value || !own ? kindTitle(name) : `${kindTitle(name)} · own ${own}`;
const pickProvider = (name: string): void => {
  if (!props.frames.opensOff) void props.frames.openProvider(name);
};
</script>

<template>
  <div class="fpk" data-testid="frames-picker">
    <ArrowScroller label="Provider" :count="frames.providers.length" :active-key="frames.provider">
      <button
        v-for="name in frames.providers"
        :key="name"
        type="button"
        class="fpk__chip"
        :class="providerClass(name)"
        :aria-pressed="name === frames.provider"
        :disabled="frames.opensOff"
        :data-testid="`provider-tab-${name}`"
        @click="pickProvider(name)"
      >
        <span class="fpk__dot" aria-hidden="true" />{{ paused(name) ? `${name} · paused` : name }}
      </button>
    </ArrowScroller>
    <div v-if="frames.data" class="fpk__field">
      <span class="fpk__label">Kind</span>
      <div class="fpk__seg" role="group" aria-label="Kind">
        <button
          v-for="k in frames.kinds"
          :key="k.kind"
          type="button"
          class="fpk__opt"
          :aria-pressed="k.kind === frames.shown?.kind"
          :disabled="frames.opensOff"
          :data-testid="`frame-kind-${k.kind}`"
          @click="frames.openKind(k.kind)"
        >
          {{ kindLabel(k.kind, ownCount(k)) }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fpk {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}
.fpk__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-body);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.fpk__chip[aria-pressed="true"] {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.fpk__chip:focus-visible,
.fpk__opt:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.fpk__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pc);
}
.fpk__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.fpk__label {
  font-size: 13px;
  font-weight: 700;
}
.fpk__seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 12px;
  background: var(--rl-track);
}
.fpk__opt {
  flex: 1;
  min-width: 0;
  height: 34px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.fpk__opt[aria-pressed="true"] {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.1);
}
.fpk__chip:disabled,
.fpk__opt:disabled {
  cursor: default;
}
@media (max-width: 767px) {
  .fpk__chip {
    height: 44px;
  }
  .fpk__opt {
    height: 38px;
  }
}
</style>
