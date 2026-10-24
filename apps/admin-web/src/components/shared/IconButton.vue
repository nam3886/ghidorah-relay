<script lang="ts">
export const SHOW_DELAY_MS = 400;
// ponytail: the canvas names no long-press time; 500 ms is the platform's usual one
export const LONG_PRESS_MS = 500;
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { IB_ICONS, type IbIconName } from "@/components/shared/icons/ledger-icons";

// The Ledger icon button (MenusToasts, "Icon buttons + tooltip"): a ghost 32 px square with a 16 px stroke icon at
// 1.75, radius 8; 44 px (radius 10) on a touch screen. Hover is the track fill, a destructive one turns red, focus is
// the 2 px brand ring. The tooltip is the button's name word for word (aria-label = tooltip text), with the shortcut
// after a middle dot when there is one ("Copy job id · C"); it shows after 400 ms on hover or keyboard focus, hides at
// once, and on touch a long press shows it while a tap runs the action. Element Plus places it in the top layer
// (teleported), flips it below near the top edge and keeps it inside the window.
const props = withDefaults(
  defineProps<{
    icon: IbIconName;
    label: string;
    shortcut?: string;
    danger?: boolean;
    disabled?: boolean;
    placement?: "top" | "bottom" | "top-end" | "bottom-end";
  }>(),
  { shortcut: "", danger: false, disabled: false, placement: "top" },
);
const emit = defineEmits<{ click: [event: MouseEvent] }>();

const text = computed(() => (props.shortcut ? `${props.label} · ${props.shortcut}` : props.label));
const open = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let pressed = false;

function later(ms: number, then: () => void): void {
  clearTimeout(timer);
  timer = setTimeout(then, ms);
}
function show(): void {
  later(SHOW_DELAY_MS, () => (open.value = true));
}
function hide(): void {
  clearTimeout(timer);
  open.value = false;
}
function onEnter(event: PointerEvent): void {
  if (event.pointerType !== "touch") show();
}
function onFocus(event: FocusEvent): void {
  const el = event.target as HTMLElement;
  // a click also focuses the button: only the keyboard's focus shows the tooltip
  let keyboard = true;
  try {
    keyboard = el.matches(":focus-visible");
  } catch {
    /* an engine without :focus-visible: treat every focus as the keyboard's */
  }
  if (keyboard) show();
}
// Touch: a long press shows the tooltip and swallows the click that follows; a tap runs the action.
function onDown(event: PointerEvent): void {
  if (event.pointerType !== "touch") return;
  pressed = false;
  later(LONG_PRESS_MS, () => {
    pressed = true;
    open.value = true;
  });
}
function onUp(event: PointerEvent): void {
  if (event.pointerType !== "touch") return;
  if (!pressed) hide();
}
function onClick(event: MouseEvent): void {
  if (pressed) {
    pressed = false;
    event.preventDefault();
    return;
  }
  hide();
  emit("click", event);
}
// the next touch anywhere closes a long-press tooltip
function onDocDown(): void {
  if (pressed || open.value) hide();
}
document.addEventListener("pointerdown", onDocDown, true);
onBeforeUnmount(() => {
  clearTimeout(timer);
  document.removeEventListener("pointerdown", onDocDown, true);
});
defineExpose({ open });
</script>

<template>
  <el-tooltip
    :visible="open"
    :content="text"
    :placement="props.placement"
    effect="dark"
    popper-class="rl-ib-tip"
    transition="rl-ib-tip-none"
    :offset="6"
  >
    <!-- a disabled button sends no pointer events in some browsers: the wrapper listens -->
    <span
      class="ib-wrap"
      :data-tip-open="open ? 'true' : undefined"
      @pointerenter="onEnter"
      @pointerleave="hide"
      @pointerdown="onDown"
      @pointerup="onUp"
      @pointercancel="hide"
      @contextmenu="pressed && $event.preventDefault()"
    >
      <button
        type="button"
        class="ib"
        :class="{ 'ib--danger': props.danger }"
        :aria-label="text"
        :disabled="props.disabled"
        @focus="onFocus"
        @blur="hide"
        @keydown.esc="hide"
        @click="onClick"
      >
        <PathIcon :paths="IB_ICONS[props.icon]" :size="16" :stroke="1.75" />
      </button>
    </span>
  </el-tooltip>
</template>

<style scoped>
.ib-wrap {
  display: inline-flex;
  flex: none;
}
.ib {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
  font: inherit;
}
.ib:hover {
  background: var(--rl-track);
  color: var(--rl-fg);
}
.ib--danger:hover {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.ib:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.ib:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  background: transparent;
}
@media (pointer: coarse) {
  .ib {
    width: 44px;
    height: 44px;
    border-radius: 10px;
  }
}
</style>

<!-- Not scoped: the tooltip is teleported to <body>. It outranks the shared skin's dark tooltip rule. -->
<style>
html:root .el-popper.is-dark.rl-ib-tip {
  padding: 5px 8px;
  border: 0;
  border-radius: 6px;
  background: var(--rl-tip);
  color: var(--rl-tip-fg);
  font: 600 12px/16px var(--rl-font);
  letter-spacing: 0;
  white-space: nowrap;
  box-shadow: var(--rl-tip-shadow);
}
html:root .el-popper.is-dark.rl-ib-tip .el-popper__arrow::before {
  border: 0;
  background: var(--rl-tip);
}
</style>
