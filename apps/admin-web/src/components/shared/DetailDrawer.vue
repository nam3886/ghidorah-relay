<script setup lang="ts">
import { watch } from "vue";
import { IconArrowLeft, IconClose, IconMoon } from "./icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useUiStore } from "@/stores/ui";
import { askDiscard, cancelPending } from "./use-leave-guard";

// The one right-hand drawer of every detail (job, request, alert...), drawn as specs/shared.md section 8: a 620 px
// panel with the scrim over the whole page; a full-screen sheet on a phone (8.1: a Back button, the title as h1, a
// theme toggle, no scrim). The body is destroyed on close, so nothing of a closed detail lingers (its state, timers,
// media). The dialog's accessible name is `title`. Slots: `chips` (status chip and one line of context under the
// title), `tabs` (a tab row under the header), default (the scrolling body) and `footer` (sticky at the bottom; it is
// given `close`, the guarded close, for a Cancel button).
//
// Closing. The X, Back, Escape, the scrim and the footer's `close` all go through one `requestClose`:
//  - `busy` (a write is in flight): nothing happens, whatever the path;
//  - not `dirty`: it emits `update:modelValue(false)` at once;
//  - `dirty`: it asks "Discard what you typed?" (one box at a time, see use-leave-guard) and emits only on Discard.
// A parent that sets the model to false itself (a save that worked) is not guarded, and a box still up is closed.
// With none of the three props the drawer closes at once, as it always did. A form passes `closeOnScrim` false: a
// click beside it never closes it.
const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    title: string;
    testid?: string;
    // the visible heading; the accessible name stays `title`
    heading?: string;
    // the phone sheet's Back button names where it goes: `Back to Jobs`
    backLabel?: string;
    // something was typed that a close would lose
    dirty?: boolean;
    // a write is in flight: every way to close is inert
    busy?: boolean;
    // what the discard box says
    discardText?: string;
    closeOnScrim?: boolean;
  }>(),
  {
    testid: undefined,
    heading: undefined,
    backLabel: "Back",
    dirty: false,
    busy: false,
    discardText: "Discard what you typed?",
    closeOnScrim: true,
  },
);
const emit = defineEmits<{ "update:modelValue": [open: boolean]; closed: [] }>();
const { isPhone } = useBreakpoint();
// looked up at the click: the store exists in the app, and a desktop drawer never needs it
const toggleTheme = (): void => useUiStore().toggleTheme();

// the box this drawer put up is still open: a second way to close (X, then Escape) has nothing more to add
let asking = false;
async function requestClose(): Promise<void> {
  if (props.busy || asking) return;
  if (props.dirty) {
    asking = true;
    try {
      if (!(await askDiscard(props.discardText))) return;
    } finally {
      asking = false;
    }
  }
  emit("update:modelValue", false);
}
// the parent closed the drawer (a save that worked) while the box was up: the box goes, as "keep"
watch(
  () => props.modelValue,
  (open) => {
    if (!open) cancelPending();
  },
);
</script>

<template>
  <el-drawer
    :model-value="props.modelValue"
    class="detail-drawer"
    :class="{ 'is-sheet': isPhone }"
    direction="rtl"
    :size="isPhone ? '100%' : '620px'"
    :title="props.title"
    :show-close="false"
    :with-header="true"
    :data-testid="props.testid"
    :close-on-click-modal="props.closeOnScrim"
    :before-close="() => void requestClose()"
    destroy-on-close
    append-to-body
    @closed="emit('closed')"
  >
    <template #header>
      <div class="detail-drawer__head">
        <button
          v-if="isPhone"
          type="button"
          class="detail-drawer__icon is-back"
          :aria-label="props.backLabel"
          @click="requestClose"
        >
          <IconArrowLeft :size="20" />
        </button>
        <component :is="isPhone ? 'h1' : 'h2'" class="detail-drawer__title">{{
          props.heading ?? props.title
        }}</component>
        <button
          v-if="isPhone"
          type="button"
          class="detail-drawer__icon is-muted is-theme"
          aria-label="Theme"
          @click="toggleTheme"
        >
          <IconMoon :size="18" />
        </button>
        <button
          v-else
          type="button"
          class="detail-drawer__icon is-muted is-close"
          aria-label="Close"
          @click="requestClose"
        >
          <IconClose :size="18" :stroke="2" />
        </button>
        <div v-if="$slots.chips" class="detail-drawer__chips"><slot name="chips" /></div>
        <div v-if="$slots.tabs" class="detail-drawer__tabs"><slot name="tabs" /></div>
      </div>
    </template>
    <div class="detail-drawer__body"><slot /></div>
    <template v-if="$slots.footer" #footer>
      <div class="detail-drawer__foot"><slot name="footer" :close="requestClose" /></div>
    </template>
  </el-drawer>
</template>

<!-- not scoped: the drawer is teleported to <body> and its panel is Element Plus's own element -->
<style>
.detail-drawer.el-drawer {
  --el-drawer-padding-primary: 0px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-drawer, -16px 0 48px rgb(10 6 20 / 28%));
}
.detail-drawer .el-drawer__header {
  display: block;
  margin: 0;
  padding: 0;
}
.detail-drawer .el-drawer__body {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.detail-drawer .el-drawer__footer {
  padding: 0;
}
.detail-drawer__head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 8px 12px;
  padding: 20px 24px 0;
  border-bottom: 1px solid var(--rl-line);
}
.detail-drawer__title {
  grid-column: 1;
  grid-row: 1;
  margin: 0;
  color: var(--rl-fg);
  font-size: 20px;
  line-height: 28px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.detail-drawer__chips {
  grid-column: 1;
  grid-row: 2;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  color: var(--rl-muted);
}
.detail-drawer__tabs {
  grid-column: 1 / -1;
  grid-row: 3;
  margin-top: 4px;
}
.detail-drawer__icon.is-close {
  grid-column: 2;
  grid-row: 1 / span 2;
}
.detail-drawer__icon {
  flex: none;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  cursor: pointer;
}
.detail-drawer__icon.is-muted {
  color: var(--rl-muted);
}
.detail-drawer__icon:hover {
  background: var(--rl-raised);
}
.detail-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 20px 24px 24px;
}
.detail-drawer__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 24px;
  background: var(--rl-surface);
  border-top: 1px solid var(--rl-line-strong);
}

/* buttons sit at the right of a desktop drawer */
.detail-drawer:not(.is-sheet) .detail-drawer__foot {
  justify-content: flex-end;
}

/* the phone sheet: the whole screen on the surface colour, no scrim, no shadow */
.detail-drawer.is-sheet.el-drawer {
  box-shadow: none;
}
.detail-drawer.is-sheet .detail-drawer__head {
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0 4px;
  padding: 4px 8px 0 4px;
}
.detail-drawer.is-sheet .is-back {
  grid-column: 1;
  grid-row: 1;
}
.detail-drawer.is-sheet .detail-drawer__title {
  grid-column: 2;
}
.detail-drawer.is-sheet .is-theme {
  grid-column: 3;
  grid-row: 1;
}
.detail-drawer.is-sheet .detail-drawer__icon {
  width: 44px;
  height: 44px;
}
.detail-drawer.is-sheet .detail-drawer__title {
  overflow: hidden;
  font-size: 18px;
  line-height: 26px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-drawer.is-sheet .detail-drawer__chips {
  grid-column: 1 / -1;
  padding: 0 8px 4px 12px;
}
.detail-drawer.is-sheet .detail-drawer__tabs {
  margin-top: 0;
}
.detail-drawer.is-sheet .detail-drawer__body {
  gap: 16px;
  padding: 16px 16px 24px;
}
/* the sheet's footer sticks to the bottom of the screen, over the body (specs/shared.md 8.1) */
.detail-drawer.is-sheet .detail-drawer__foot {
  position: sticky;
  bottom: 0;
  z-index: 5;
  padding: 10px 16px;
}
.detail-drawer.is-sheet .detail-drawer__foot {
  flex-wrap: wrap;
}
.detail-drawer.is-sheet .detail-drawer__foot .el-button {
  padding: 0 14px;
}
/* every footer button, whatever its class (el-button, kla-btn, acct-btn...), is 44 px high on the sheet */
.detail-drawer.is-sheet .detail-drawer__foot button {
  min-height: 44px;
  height: 44px;
}
</style>
