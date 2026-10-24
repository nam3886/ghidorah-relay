<script setup lang="ts">
// The Ledger bottom sheet (JobsTablet `bottom`, the phone boards): it rises from the foot of the screen, at most 92 %
// high, with a 20 px top radius, the surface and the sheet shadow over the scrim. Its head: the grab handle (40 by 5,
// radius 3), an optional Back, the title (17/24 800, one line) with an optional sub line (12/17 faint), and Close · Esc
// as a 44 px icon button; ruled off from the body. The body (14 px 16 px 20 px, 14 px gaps) scrolls; the `footer` slot
// sits on the head fill above the safe area. Element Plus keeps the focus inside and closes it on Escape and the scrim.
import IconButton from "@/components/shared/IconButton.vue";

const props = withDefaults(
  defineProps<{ modelValue: boolean; title: string; sub?: string; backLabel?: string; testid?: string }>(),
  { sub: "", backLabel: "", testid: undefined },
);
const emit = defineEmits<{ "update:modelValue": [open: boolean]; back: []; closed: [] }>();
const close = (): void => emit("update:modelValue", false);
</script>

<template>
  <el-drawer
    :model-value="props.modelValue"
    class="rl-sheet"
    modal-class="rl-drawer-scrim"
    direction="btt"
    size="auto"
    :show-close="false"
    :data-testid="props.testid"
    destroy-on-close
    append-to-body
    @update:model-value="(open: boolean) => emit('update:modelValue', open)"
    @closed="emit('closed')"
  >
    <template #header="{ titleId }">
      <div class="rl-sheet__head">
        <span class="rl-sheet__handle" aria-hidden="true"></span>
        <div class="rl-sheet__bar">
          <IconButton
            v-if="props.backLabel"
            icon="back"
            :label="props.backLabel"
            placement="bottom"
            @click="emit('back')"
          />
          <div class="rl-sheet__titles">
            <h2 :id="titleId" class="rl-sheet__title">{{ props.title }}</h2>
            <span v-if="props.sub" class="rl-sheet__sub">{{ props.sub }}</span>
          </div>
          <IconButton icon="close" label="Close" shortcut="Esc" placement="bottom-end" @click="close" />
        </div>
      </div>
    </template>
    <div class="rl-sheet__body"><slot /></div>
    <template v-if="$slots.footer" #footer>
      <div class="rl-sheet__foot"><slot name="footer" /></div>
    </template>
  </el-drawer>
</template>

<!-- Not scoped: the sheet is teleported to <body> and its parts are Element Plus's own. -->
<style>
html:root .el-drawer.rl-sheet {
  display: flex;
  flex-direction: column;
  max-height: 92%;
  overflow: hidden;
  border-radius: 20px 20px 0 0;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-sheet);
}
html:root .rl-sheet .el-drawer__header {
  flex: none;
  margin: 0;
  padding: 10px 8px;
  border-bottom: 1px solid var(--rl-rule);
}
.rl-sheet__head {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  min-width: 0;
}
.rl-sheet__handle {
  align-self: center;
  width: 40px;
  height: 5px;
  border-radius: 3px;
  background: var(--rl-handle);
}
.rl-sheet__bar {
  display: flex;
  align-items: center;
  gap: 4px;
}
.rl-sheet__titles {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  padding: 0 4px;
}
.rl-sheet__title {
  margin: 0;
  font-size: 17px;
  line-height: 24px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--rl-fg);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rl-sheet__sub {
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-faint);
}
/* the close and back buttons are the 44 px touch size here at every pointer */
html:root .rl-sheet__bar .ib {
  width: 44px;
  height: 44px;
  border-radius: 12px;
}
html:root .rl-sheet .el-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0;
}
.rl-sheet__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px 16px 20px;
}
html:root .rl-sheet .el-drawer__footer {
  flex: none;
  padding: 0;
}
.rl-sheet__foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px max(20px, env(safe-area-inset-bottom));
  border-top: 1px solid var(--rl-rule);
  background: var(--rl-head);
}
</style>
