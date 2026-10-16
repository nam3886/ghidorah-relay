<script setup lang="ts">
// Prompt frames: the text relay sends is the customer's prompt inside one admin-written frame per model, or per kind
// (the default). All the state and the race rules are in components/frames/use-frames.ts.
import { computed, onBeforeUnmount, onMounted } from "vue";
import FrameEditor from "@/components/frames/FrameEditor.vue";
import FrameKindSection from "@/components/frames/FrameKindSection.vue";
import { NO_FRAMES_TEXT } from "@/components/frames/frames-model";
import { forgetFramesMemory, useFrames } from "@/components/frames/use-frames";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

const frames = useFrames();
const scope = useScopeStore();
const offSignOut = useAuthStore().onSignOut(forgetFramesMemory);

// The provider select is this view's own state. The global filter only decides where it starts, when valid.
onMounted(async () => {
  try {
    await scope.loadCatalog();
  } catch (err) {
    notify.error(errorText(err));
    return;
  }
  await frames.init(scope.providers, scope.provider);
});
onBeforeUnmount(() => {
  frames.dispose();
  offSignOut();
});

// Video first, as drawn; any other kind keeps the server order after it.
const kinds = computed(() =>
  [...(frames.data?.kinds ?? [])].sort((a, b) => Number(b.kind === "video") - Number(a.kind === "video")),
);

const onProvider = (event: Event): void => void frames.openProvider((event.target as HTMLSelectElement).value);
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
      <!-- The trigger is drawn by the label; the real control is the transparent select on top of it. -->
      <label class="frames__provider" :class="{ 'is-disabled': frames.opensOff }">
        <span class="frames__provider-label">Provider</span>
        <span class="frames__provider-value">{{ frames.provider }}</span>
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
        <select
          class="frames__provider-select"
          aria-label="Provider"
          :value="frames.provider"
          :disabled="frames.opensOff"
          @change="onProvider"
        >
          <option v-for="name in frames.providers" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
    </header>
    <p v-if="frames.noFrames" class="frames__none">{{ NO_FRAMES_TEXT }}</p>
    <div class="frames__body">
      <div class="frames__kinds">
        <FrameKindSection v-for="k in kinds" :key="k.kind" :frames="frames" :kind="k" />
      </div>
      <FrameEditor :frames="frames" />
    </div>
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
  margin: 0;
  color: var(--rl-muted);
  font-size: 13px;
}

/* Provider select: a label-shaped trigger with the real select laid over it, invisible */
.frames__provider {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-size: 13px;
  cursor: pointer;
}
.frames__provider.is-disabled {
  cursor: default;
  opacity: 0.6;
}
.frames__provider-label {
  color: var(--rl-muted);
}
.frames__provider-select {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: inherit;
  font: inherit;
}
.frames__provider:focus-within {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

.frames__body {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 20px;
}
.frames__kinds {
  flex: 1 1 560px;
  min-width: 0;
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
  .frames__body,
  .frames__kinds {
    flex-direction: column;
    flex-wrap: nowrap;
    width: 100%;
  }
  .frames__kinds {
    flex: none;
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
  .frames__provider {
    align-self: flex-start;
    height: 44px;
    padding: 0 14px;
  }
  .frames {
    gap: 16px;
  }
  .frames__body,
  .frames__kinds {
    flex-direction: column;
    flex-wrap: nowrap;
    width: 100%;
    gap: 16px;
  }
  .frames__kinds {
    flex: none;
  }
}
</style>
