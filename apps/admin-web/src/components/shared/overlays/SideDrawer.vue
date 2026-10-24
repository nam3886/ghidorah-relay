<script setup lang="ts">
import { watch } from "vue";
import IconButton from "@/components/shared/IconButton.vue";
import { askDiscard, cancelPending } from "@/components/shared/use-leave-guard";
import { useBreakpoint } from "@/composables/use-breakpoint";

// The Ledger drawer (AccountDetail; JobsTablet `drawer`): a right-hand panel, min(640 px, 100%) wide on a desktop and
// 48 px short of the left edge on a tablet or phone, on the surface with the lift shadow over the scrim. Its head: an
// optional Back, the title (18/26, 500; `mono` for an id) and Close · Esc, then the `sub` slot (chips and one line of
// context) under the title, ruled off from the body. The body (20 px 24 px 28 px, 22 px gaps) scrolls; the `footer`
// slot sits on the head fill under a rule. Element Plus keeps the focus inside and closes it on Escape and the scrim.
// Opt-in: the `tabs` slot (a tab row at the foot of the head, on its rule: JobDetail), `sheet` (on a phone the drawer
// is the whole screen with Back alone, JobDetailPhone) and `panelClass` (a class on the panel, for a page's own rules;
// `is-sheet-head` with `full` gives the phone form the sheet head, ImportPhone). `headingTestid` marks the title.
// A form (KeyForm, AccountEdit, AccountImport): `width` is its desktop width (520 or 560 px); `full` makes it the whole
// screen on a phone with no Back (KeyCreatePhone, ImportPhone); `dirty`, `busy` and `discardText` are its guard: every
// way out (Close, Back, Escape, the scrim and the footer's `close`) asks "Discard what you typed?" (one box at a time)
// while something typed would be lost, and is inert while a write is in flight (`busy`, also for a detail: Alerts).
const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    title: string;
    mono?: boolean;
    backLabel?: string;
    testid?: string;
    sheet?: boolean;
    panelClass?: string;
    headingTestid?: string;
    width?: string;
    full?: boolean;
    dirty?: boolean;
    busy?: boolean;
    discardText?: string;
  }>(),
  {
    mono: false,
    backLabel: "",
    testid: undefined,
    sheet: false,
    panelClass: "",
    headingTestid: undefined,
    width: "",
    full: false,
    dirty: false,
    busy: false,
    discardText: "Discard what you typed?",
  },
);
const emit = defineEmits<{ "update:modelValue": [open: boolean]; back: []; closed: [] }>();
const { isPhone, isTablet } = useBreakpoint();

// the question this drawer put up is still open: a second way out has nothing more to add
let asking = false;
// "Discard what you typed?" (Keep or Discard). The callers ask only when something typed would be lost, so a plain way
// out decides at once and tells the parent in the same tick.
async function ask(): Promise<boolean> {
  asking = true;
  try {
    return await askDiscard(props.discardText);
  } finally {
    asking = false;
  }
}
// Every way out tells the parent first, which closes the drawer through its model, so it has heard of the close before
// `closed` (Element Plus's own close emits `closed` before the model's update)
async function close(): Promise<void> {
  if (props.busy || asking) return;
  if (props.dirty && !(await ask())) return;
  emit("update:modelValue", false);
}
// Back is a way out too: the guard first, then the page hears it
async function back(): Promise<void> {
  if (props.busy || asking) return;
  if (props.dirty && !(await ask())) return;
  emit("back");
}
// a caller whose Back is a way out calls this guarded close from its `back` handler
defineExpose({ close });
// the page closed the drawer itself (a save that worked) while this drawer's question was up: the question goes, as keep
watch(
  () => props.modelValue,
  (open) => {
    if (!open && asking) cancelPending();
  },
);
const fullPhone = (): boolean => (props.full || props.sheet) && isPhone.value;
</script>

<template>
  <el-drawer
    :model-value="props.modelValue"
    :class="['rl-drawer', props.panelClass, { 'is-sheet': props.sheet && isPhone, 'is-full': props.full && isPhone }]"
    modal-class="rl-drawer-scrim"
    direction="rtl"
    :size="fullPhone() ? '100%' : isPhone || isTablet ? 'calc(100% - 48px)' : props.width || 'min(640px, 100%)'"
    :show-close="false"
    :data-testid="props.testid"
    :before-close="() => void close()"
    destroy-on-close
    append-to-body
    @closed="emit('closed')"
  >
    <template #header="{ titleId }">
      <div class="rl-drawer__head" :class="{ 'has-tabs': !!$slots.tabs }">
        <div class="rl-drawer__bar">
          <IconButton
            v-if="props.backLabel && !(props.full && isPhone)"
            icon="back"
            :label="props.backLabel"
            placement="bottom"
            :disabled="props.busy"
            @click="back"
          />
          <h2 :id="titleId" class="rl-drawer__title" :class="{ mono: props.mono }" :data-testid="props.headingTestid">
            <slot name="title">{{ props.title }}</slot>
          </h2>
          <IconButton
            v-if="!(props.sheet && isPhone)"
            icon="close"
            label="Close"
            shortcut="Esc"
            placement="bottom-end"
            :disabled="props.busy"
            @click="close"
          />
        </div>
        <div
          v-if="$slots.sub"
          class="rl-drawer__sub"
          :class="{ 'has-back': !!props.backLabel && !(props.full && isPhone) }"
        >
          <slot name="sub" />
        </div>
        <div v-if="$slots.tabs" class="rl-drawer__tabs" :class="{ 'has-back': !!props.backLabel }">
          <slot name="tabs" />
        </div>
      </div>
    </template>
    <div class="rl-drawer__body"><slot /></div>
    <template v-if="$slots.footer" #footer>
      <div class="rl-drawer__foot"><slot name="footer" :close="close" /></div>
    </template>
  </el-drawer>
</template>

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
/* the phone form with the sheet head (ImportPhone: `full` and the panel class is-sheet-head): the whole screen, the
   sheet head (10 px 8px, 17/24 800, one line, 44 px buttons), the body 14 px 16px 20 px, the footer buttons sharing
   the row at 44 px */
html:root .el-drawer.rl-drawer.is-full.is-sheet-head {
  box-shadow: none;
}
html:root .rl-drawer.is-full.is-sheet-head .el-drawer__header {
  padding: 10px 8px;
  border-bottom-color: var(--rl-rule);
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__head {
  gap: 0;
  padding-bottom: 0;
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__bar {
  gap: 4px;
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__title {
  padding: 0 4px;
  font-size: 17px;
  line-height: 24px;
  font-weight: 800;
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__sub {
  padding: 0 4px;
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-faint);
}
html:root .rl-drawer.is-full.is-sheet-head .rl-drawer__bar .ib {
  width: 44px;
  height: 44px;
  border-radius: 12px;
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__body {
  gap: 14px;
  padding: 14px 16px 20px;
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__foot {
  padding: 12px 16px max(20px, env(safe-area-inset-bottom));
}
.rl-drawer.is-full.is-sheet-head .rl-drawer__foot > button {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
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
