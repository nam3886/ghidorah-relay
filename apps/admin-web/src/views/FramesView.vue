<script setup lang="ts">
// Prompt frames: the text relay sends is the customer's prompt inside one admin-written frame per model, or per kind
// (the default). All the state and the race rules are in components/frames/use-frames.ts.
import { computed, onBeforeUnmount, onMounted, watch, watchEffect } from "vue";
import FrameEditor from "@/components/frames/FrameEditor.vue";
import FrameKindSection from "@/components/frames/FrameKindSection.vue";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { NO_FRAMES_TEXT, TRACKING_MARKER } from "@/components/frames/frames-model";
import { forgetFramesMemory, useFrames } from "@/components/frames/use-frames";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

const frames = useFrames();
const scope = useScopeStore();
const offSignOut = useAuthStore().onSignOut(forgetFramesMemory);

// Leaving the page (or closing the tab) with an unsaved frame in the open drawer asks first; a write in flight keeps
// the page.
useRouteLeaveGuard(
  () => frames.open && frames.draft !== frames.loaded,
  () => frames.busy,
);

// The drawer is gone and Element Plus has sent focus back to the button that opened it. When that button no longer
// exists (a reload drew the rows again) focus goes to the page's scroll area, so it is never left on the document.
let opener: HTMLElement | null = null;
watch(
  () => frames.open,
  (isOpen) => {
    if (isOpen) opener = document.activeElement as HTMLElement | null;
  },
  { flush: "sync" },
);
function editorClosed(): void {
  if (opener?.isConnected && opener !== document.body) return;
  document.querySelector<HTMLElement>(".app-scroll__wrap")?.focus({ preventScroll: true });
}

// The provider strip (one provider at a time) picks the provider: it shares `only` with the Test page. On the way in
// the strip's provider is opened, else the one chosen last on this page, else the first; the strip then shows it. The
// strip is off while a write runs, as the old select was.
onMounted(async () => {
  try {
    await scope.loadCatalog();
  } catch (err) {
    notify.error(errorText(err));
    return;
  }
  // the strip's provider (picked here or on Test) goes before the one this page remembers
  if (scope.providers.includes(scope.only)) frames.provider = scope.only;
  await frames.init(scope.providers, scope.only);
  scope.only = frames.provider;
});
watch(
  () => scope.only,
  (name) => {
    if (name && frames.providers.includes(name) && name !== frames.provider) void frames.openProvider(name);
  },
);
watchEffect(() => (scope.stripLocked = frames.opensOff));
onBeforeUnmount(() => {
  frames.dispose();
  offSignOut();
  scope.stripLocked = false;
});

// Video first, as drawn; any other kind keeps the server order after it.
const kinds = computed(() =>
  [...(frames.data?.kinds ?? [])].sort((a, b) => Number(b.kind === "video") - Number(a.kind === "video")),
);
</script>

<template>
  <section class="frames">
    <header class="frames__head">
      <div class="frames__text">
        <h1 class="frames__title">Prompt frames</h1>
        <p class="frames__subtitle">
          The wrapper text around each customer prompt. A save applies only to jobs created after it.
        </p>
      </div>
    </header>
    <p v-if="frames.data?.verbatim" class="frames__verbatim" data-testid="frames-verbatim">
      {{ frames.listed }} sends the text as written: <code>{{ TRACKING_MARKER }}</code> adds nothing and the prompt
      keeps its line breaks.
    </p>
    <!-- a provider with no frames: the drawn empty card (12.6) -->
    <section v-if="frames.noFrames" class="frames__none" aria-label="Prompt frames, none">
      <p>{{ NO_FRAMES_TEXT }}</p>
    </section>
    <div class="frames__kinds">
      <FrameKindSection v-for="k in kinds" :key="k.kind" :frames="frames" :kind="k" />
    </div>
    <FrameEditor :frames="frames" @closed="editorClosed" />
  </section>
</template>

<style scoped>
.frames {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.frames__head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}
.frames__text {
  flex: 1 1 420px;
  min-width: 0;
}
.frames__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.frames__subtitle {
  margin: 4px 0 0;
  color: var(--rl-muted);
}
.frames__none {
  padding: 44px 24px 52px;
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  background: var(--rl-surface);
  text-align: center;
}
.frames__none p {
  margin: 0;
  color: var(--rl-muted);
}

.frames__verbatim {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.frames__verbatim code {
  font-family: var(--rl-mono);
  font-size: 12px;
}

.frames__kinds {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

@media (min-width: 768px) and (max-width: 1279px) {
  .frames__title {
    font-size: 24px;
    line-height: 32px;
    letter-spacing: -0.012em;
  }
  .frames__subtitle {
    margin-top: 2px;
  }
}
@media (max-width: 767px) {
  .frames__head {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
  }
  .frames__text {
    flex: none;
  }
  .frames__title {
    font-size: 22px;
    line-height: 30px;
    letter-spacing: -0.01em;
  }
  .frames__subtitle {
    margin-top: 2px;
  }
  .frames {
    gap: 16px;
  }
  .frames__kinds {
    gap: 16px;
  }
}
</style>
