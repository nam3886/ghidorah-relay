<script setup lang="ts">
// Prompt frames: the text relay sends is the customer's prompt inside one admin-written frame per model, or per kind
// (the default). All the state and the race rules are in components/frames/use-frames.ts.
import { onBeforeUnmount, onMounted } from "vue";
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
</script>

<template>
  <section class="frames">
    <header class="frames__head">
      <h3>Prompt frames</h3>
      <el-select
        :model-value="frames.provider"
        aria-label="Provider"
        class="frames__provider"
        size="small"
        :disabled="frames.opensOff"
        @change="frames.openProvider"
      >
        <el-option v-for="name in frames.providers" :key="name" :label="name" :value="name" />
      </el-select>
    </header>
    <p class="frames__note">
      A save applies only to jobs created after it. A model with its own frame follows no default; a locked model keeps
      its own when a default is applied to all.
    </p>
    <p v-if="frames.noFrames" class="frames__none">{{ NO_FRAMES_TEXT }}</p>
    <FrameKindSection v-for="k in frames.data?.kinds ?? []" :key="k.kind" :frames="frames" :kind="k" />
    <FrameEditor :frames="frames" />
  </section>
</template>

<style scoped>
.frames {
  display: grid;
  gap: 16px;
}
.frames__head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.frames__head h3 {
  margin: 0;
  font-size: 16px;
}
.frames__provider {
  width: 180px;
}
.frames__note,
.frames__none {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
</style>
