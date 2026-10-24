<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import FrameEditor from "@/components/frames/FrameEditor.vue";
import FrameHistory from "@/components/frames/FrameHistory.vue";
import FrameModels from "@/components/frames/FrameModels.vue";
import FramePicker from "@/components/frames/FramePicker.vue";
import FrameSideNav from "@/components/frames/FrameSideNav.vue";
import { NO_FRAMES_TEXT } from "@/components/frames/frames-model";
import { forgetFramesMemory, useFrames } from "@/components/frames/use-frames";
import PageHeader from "@/components/layout/PageHeader.vue";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";

// Prompt frames: the text relay sends is the customer's prompt inside one admin-written frame per model, or per kind
// (the default). The page is a list of providers and kinds, the editor of the frame picked and, under it, the models of
// the kind and the history of the frame. A desktop has the list on the left; a tablet and a phone have two rows of
// chips instead. All the state and the race rules are in components/frames/use-frames.ts.
const frames = useFrames();
const scope = useScopeStore();
const summary = useProvidersStore();
const { isPhone, isTablet } = useBreakpoint();
const stacked = computed(() => isPhone.value || isTablet.value);
const offSignOut = useAuthStore().onSignOut(forgetFramesMemory);

// Leaving the page (or closing the tab) with an unsaved frame asks first; a write in flight keeps the page.
useRouteLeaveGuard(
  () => frames.dirty,
  () => frames.busy,
);

// The provider the Test page was on (`scope.only`) goes before the one this page remembers; the one picked here is
// the one Test opens on.
onMounted(async () => {
  try {
    await scope.loadCatalog();
  } catch (err) {
    notify.error(errorText(err));
    return;
  }
  if (!summary.data) void summary.refresh();
  if (scope.providers.includes(scope.only)) frames.provider = scope.only;
  await frames.init(scope.providers, scope.only);
  scope.only = frames.provider;
});
watch(
  () => frames.provider,
  (name) => {
    if (name) scope.only = name;
  },
);
onBeforeUnmount(() => {
  frames.dispose();
  offSignOut();
});

const paused = computed(
  () => summary.data?.providers.find((p) => p.provider === frames.provider)?.claims_enabled === false,
);
const subtitle =
  "Text wrapped around the customer's prompt before it reaches the provider. Pick a kind on the left; edit it on the right.";
</script>

<template>
  <section class="frames" :class="{ 'frames--stacked': stacked, 'frames--phone': isPhone }">
    <PageHeader v-if="!stacked" title="Prompt frames" :subtitle="subtitle" />
    <!-- a tablet and a phone draw the title in the top bar: the page keeps its heading for a screen reader -->
    <h1 v-else class="frames__sr">Prompt frames</h1>

    <FramePicker v-if="stacked && frames.providers.length" :frames="frames" />

    <div class="frames__layout">
      <FrameSideNav v-if="!stacked && frames.providers.length" :frames="frames" />

      <div class="frames__main">
        <div v-if="paused" class="frames__banner is-brand" role="status" data-testid="frames-paused">
          <span class="frames__banner-text"
            ><b>{{ frames.provider }}</b> is paused. Its frames can still be edited; they apply when jobs flow
            again.</span
          >
          <a class="frames__banner-btn" href="#/settings">Open Settings</a>
        </div>
        <section v-if="frames.failed" class="frames__box" role="alert" data-testid="frames-failed">
          <h2>Could not load the prompt frames</h2>
          <p>{{ frames.failed }}</p>
          <button type="button" class="frames__retry" :disabled="frames.loading > 0" @click="frames.reload">
            Retry
          </button>
        </section>
        <section v-else-if="frames.noFrames" class="frames__box" aria-label="Prompt frames, none">
          <p>{{ NO_FRAMES_TEXT }}</p>
        </section>
        <template v-else>
          <FrameEditor :frames="frames" />
          <div class="frames__pair">
            <FrameModels :frames="frames" />
            <FrameHistory :frames="frames" />
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.frames {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.frames--stacked {
  gap: 20px;
}
.frames__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
.frames--phone {
  gap: 14px;
  /* clear of the bar fixed above the tab bar */
  padding-bottom: 72px;
}
.frames__layout {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 20px;
}
.frames__main {
  flex: 999 1 640px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.frames--stacked .frames__layout {
  display: block;
}
.frames--stacked .frames__main {
  gap: 20px;
}
.frames--phone .frames__main {
  gap: 14px;
}
.frames__pair {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr));
  gap: 20px;
  align-items: start;
}
.frames--stacked .frames__pair {
  grid-template-columns: minmax(0, 1fr);
}
.frames--phone .frames__pair {
  gap: 14px;
}
.frames__box {
  padding: 44px 24px 52px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
  text-align: center;
}
.frames__box h2 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
}
.frames__box p {
  margin: 0;
  color: var(--rl-muted);
}
.frames__retry {
  height: 36px;
  margin-top: 14px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.frames__retry:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.frames__banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  padding: 12px 14px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 19px;
}
.frames__banner.is-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.frames__banner-text {
  flex: 1;
  min-width: 0;
}
.frames__banner-btn {
  display: inline-flex;
  flex: none;
  align-items: center;
  height: 26px;
  box-sizing: border-box;
  padding: 0 10px;
  text-decoration: none;
  border: 1px solid currentcolor;
  border-radius: 7px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
</style>
