<script setup lang="ts">
import IconButton from "@/components/shared/IconButton.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";

// The Ledger drawer (AccountDetail; JobsTablet `drawer`): a right-hand panel, min(640 px, 100%) wide on a desktop and
// 48 px short of the left edge on a tablet or phone, on the surface with the lift shadow over the scrim. Its head: an
// optional Back, the title (18/26, 500; `mono` for an id) and Close · Esc, then the `sub` slot (chips and one line of
// context) under the title, ruled off from the body. The body (20 px 24 px 28 px, 22 px gaps) scrolls; the `footer`
// slot sits on the head fill under a rule. Element Plus keeps the focus inside and closes it on Escape and the scrim.
// Opt-in: the `tabs` slot (a tab row at the foot of the head, on its rule: JobDetail), `sheet` (on a phone the drawer
// is the whole screen with Back alone, JobDetailPhone) and `panelClass` (a class on the panel, for a page's own rules).
const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    title: string;
    mono?: boolean;
    backLabel?: string;
    testid?: string;
    sheet?: boolean;
    panelClass?: string;
  }>(),
  { mono: false, backLabel: "", testid: undefined, sheet: false, panelClass: "" },
);
const emit = defineEmits<{ "update:modelValue": [open: boolean]; back: []; closed: [] }>();
const { isPhone, isTablet } = useBreakpoint();
const close = (): void => emit("update:modelValue", false);
</script>

<template>
  <el-drawer
    :model-value="props.modelValue"
    :class="['rl-drawer', props.panelClass, { 'is-sheet': props.sheet && isPhone }]"
    modal-class="rl-drawer-scrim"
    direction="rtl"
    :size="props.sheet && isPhone ? '100%' : isPhone || isTablet ? 'calc(100% - 48px)' : 'min(640px, 100%)'"
    :show-close="false"
    :data-testid="props.testid"
    destroy-on-close
    append-to-body
    @update:model-value="(open: boolean) => emit('update:modelValue', open)"
    @closed="emit('closed')"
  >
    <template #header="{ titleId }">
      <div class="rl-drawer__head" :class="{ 'has-tabs': !!$slots.tabs }">
        <div class="rl-drawer__bar">
          <IconButton
            v-if="props.backLabel"
            icon="back"
            :label="props.backLabel"
            placement="bottom"
            @click="emit('back')"
          />
          <h2 :id="titleId" class="rl-drawer__title" :class="{ mono: props.mono }">{{ props.title }}</h2>
          <IconButton
            v-if="!(props.sheet && isPhone)"
            icon="close"
            label="Close"
            shortcut="Esc"
            placement="bottom-end"
            @click="close"
          />
        </div>
        <div v-if="$slots.sub" class="rl-drawer__sub" :class="{ 'has-back': !!props.backLabel }">
          <slot name="sub" />
        </div>
        <div v-if="$slots.tabs" class="rl-drawer__tabs" :class="{ 'has-back': !!props.backLabel }">
          <slot name="tabs" />
        </div>
      </div>
    </template>
    <div class="rl-drawer__body"><slot /></div>
    <template v-if="$slots.footer" #footer>
      <div class="rl-drawer__foot"><slot name="footer" /></div>
    </template>
  </el-drawer>
</template>

<!-- Not scoped: the drawer is teleported to <body> and its parts are Element Plus's own. -->
<style>
html:root .el-drawer.rl-drawer {
  display: flex;
  flex-direction: column;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-drawer);
}
html:root .rl-drawer-scrim {
  background: var(--rl-scrim);
}
html:root .rl-drawer .el-drawer__header {
  flex: none;
  margin: 0;
  padding: 16px 20px 0 16px;
  border-bottom: 1px solid var(--rl-line);
}
.rl-drawer__head {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  min-width: 0;
  padding-bottom: 14px;
}
.rl-drawer__head.has-tabs {
  padding-bottom: 0;
}
.rl-drawer__tabs.has-back {
  padding-left: 36px;
}
.rl-drawer__bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rl-drawer__title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 18px;
  line-height: 26px;
  font-weight: 500;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.rl-drawer__sub {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.rl-drawer__sub.has-back {
  padding-left: 44px;
}
html:root .rl-drawer .el-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0;
}
.rl-drawer__body {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 20px 24px 28px;
}
html:root .rl-drawer .el-drawer__footer {
  flex: none;
  padding: 0;
}
.rl-drawer__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 24px;
  border-top: 1px solid var(--rl-rule);
  background: var(--rl-head);
}
</style>
