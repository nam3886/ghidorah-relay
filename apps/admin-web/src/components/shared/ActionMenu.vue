<script setup lang="ts">
import { type Component, nextTick, onBeforeUnmount, ref } from "vue";
import { IconMoreVertical } from "@/components/shared/icons";

// The one action menu of the admin: a trigger button and the popover menu it opens (specs/shared.md section 11). The
// menu is drawn in the body, fixed under the trigger and right-aligned to it (above it when it would run past the
// bottom of the window), so a table that scrolls sideways never clips it. The trigger has `aria-expanded`; the menu
// takes the focus, the arrow keys walk the items that can be picked, and it closes on a pick, Escape, a click
// elsewhere and Tab; a scroll or a resize moves it with its trigger. `select` carries the key of the picked item.
export interface MenuItem {
  key: string;
  label: string;
  icon?: Component;
  danger?: boolean;
  // drawn but not pickable (aria-disabled)
  disabled?: boolean;
  // a separator line above the item
  separated?: boolean;
  // a faint word at the right of the item ("none yet", "24 accounts")
  hint?: string;
}

const props = withDefaults(
  defineProps<{
    items: MenuItem[];
    triggerLabel?: string;
    menuLabel?: string;
    // row: the account row menu; header: the page menu (tablet); headerPhone: the page menu on a phone
    variant?: "row" | "header" | "headerPhone";
    // the trigger's square side in px and its look
    size?: number;
    bordered?: boolean;
    inkText?: boolean;
    icon?: Component;
    iconSize?: number;
    // the trigger's tooltip (the Ledger icon-button tooltip, after 400 ms); none when empty
    tip?: string;
  }>(),
  {
    triggerLabel: "More",
    menuLabel: "Actions",
    variant: "row",
    size: 30,
    bordered: false,
    inkText: false,
    icon: IconMoreVertical,
    iconSize: 16,
    tip: "",
  },
);
const emit = defineEmits<{ select: [key: string] }>();

const open = ref(false);
const trigger = ref<HTMLButtonElement>();
const menu = ref<HTMLElement>();
const place = ref({ top: 0, right: 0 });

// Under the trigger; above it when the menu would run past the bottom of the window and there is room above. The page
// menus sit 6 px from their trigger, the row menu 8 px (B-States draws it 8 px under a row's More button).
function measure(): void {
  const GAP = props.variant === "row" ? 8 : 6;
  const rect = trigger.value?.getBoundingClientRect();
  if (!rect) return;
  const height = menu.value?.offsetHeight ?? 0;
  let top = rect.bottom + GAP;
  if (height && top + height > window.innerHeight && rect.top - GAP - height >= 0) top = rect.top - GAP - height;
  place.value = { top, right: Math.max(0, window.innerWidth - rect.right) };
}

function close(refocus = false): void {
  if (!open.value) return;
  open.value = false;
  document.removeEventListener("pointerdown", onOutside, true);
  window.removeEventListener("resize", onWindow);
  window.removeEventListener("scroll", onWindow, true);
  if (refocus) trigger.value?.focus();
}

function onOutside(event: Event): void {
  const target = event.target as Node | null;
  if (target && (menu.value?.contains(target) || trigger.value?.contains(target))) return;
  close();
}
// the menu follows its trigger: a scroll that arrives late (the page scrolled the trigger into view) must not shut it
const onWindow = (): void => measure();

const enabled = (): HTMLButtonElement[] => [
  ...(menu.value?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? []),
];

async function toggle(): Promise<void> {
  if (open.value) return close();
  measure();
  open.value = true;
  document.addEventListener("pointerdown", onOutside, true);
  window.addEventListener("resize", onWindow);
  window.addEventListener("scroll", onWindow, true);
  await nextTick();
  measure();
  enabled()[0]?.focus();
}

function pick(item: MenuItem): void {
  if (item.disabled) return;
  close(true);
  emit("select", item.key);
}

function onMenuKey(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    return close(true);
  }
  if (event.key === "Tab") return close();
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  const list = enabled();
  if (!list.length) return;
  const at = list.indexOf(document.activeElement as HTMLButtonElement);
  const step = event.key === "ArrowDown" ? 1 : -1;
  list[(at + step + list.length) % list.length]?.focus();
}

// the trigger's tooltip: after 400 ms of a mouse's hover (as IconButton), gone at once on leave and on a press
const tipOpen = ref(false);
let tipTimer: ReturnType<typeof setTimeout> | undefined;
function onTipEnter(event: PointerEvent): void {
  if (event.pointerType === "touch") return;
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => (tipOpen.value = true), 400);
}
function hideTip(): void {
  clearTimeout(tipTimer);
  tipOpen.value = false;
}

onBeforeUnmount(() => {
  clearTimeout(tipTimer);
  close();
});
</script>

<template>
  <el-tooltip
    :visible="tipOpen && !!props.tip && !open"
    :content="props.tip"
    placement="top"
    effect="dark"
    popper-class="rl-ib-tip"
    transition="rl-ib-tip-none"
    :offset="6"
  >
    <!-- the tooltip hangs on a wrapper (it writes its own aria-expanded on its trigger) and shows on a mouse's hover
         only: a press, the menu or a focus given back never leave it over what opens next -->
    <span class="menu-trigger-wrap" @pointerenter="onTipEnter" @pointerleave="hideTip" @pointerdown="hideTip">
      <button
        ref="trigger"
        type="button"
        class="menu-trigger"
        :class="{ 'is-open': open, 'is-bordered': props.bordered, 'is-ink': props.inkText }"
        :style="{ '--trigger': `${props.size}px` }"
        aria-haspopup="menu"
        :aria-expanded="open"
        :aria-label="props.triggerLabel"
        @click="toggle"
      >
        <component :is="props.icon" :size="props.iconSize" :stroke="2.4" />
      </button>
    </span>
  </el-tooltip>
  <Teleport to="body">
    <div
      v-if="open"
      ref="menu"
      role="menu"
      class="menu-pop"
      :class="`menu-pop--${props.variant}`"
      :style="{ top: `${place.top}px`, right: `${place.right}px` }"
      :aria-label="props.menuLabel"
      @keydown="onMenuKey"
    >
      <template v-for="item in props.items" :key="item.key">
        <div v-if="item.separated" role="separator" class="menu-pop__sep" />
        <button
          type="button"
          role="menuitem"
          class="menu-pop__item"
          :class="{ 'is-danger': item.danger, 'is-disabled': item.disabled }"
          :aria-disabled="item.disabled ? 'true' : undefined"
          @click="pick(item)"
        >
          <component :is="item.icon" :size="props.variant === 'header' ? 15 : 16" />{{ item.label }}
          <span v-if="item.hint" class="menu-pop__hint">{{ item.hint }}</span>
        </button>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.menu-trigger-wrap {
  display: inline-flex;
  flex: none;
}
.menu-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
  box-sizing: border-box;
  width: var(--trigger);
  height: var(--trigger);
  padding: 0;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.menu-trigger.is-bordered {
  border-color: var(--rl-line-strong);
  background: var(--rl-surface);
}
.menu-trigger.is-ink {
  color: var(--rl-fg);
}
.menu-trigger:hover,
.menu-trigger.is-open {
  background: var(--rl-raised);
}
.menu-trigger.is-open {
  border-color: var(--rl-line-strong);
}
.menu-pop {
  position: fixed;
  z-index: 50;
  box-sizing: border-box;
  width: 200px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  box-shadow: var(--rl-shadow-pop);
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-fg);
}
.menu-pop--row {
  padding: 6px;
}
.menu-pop--header {
  padding: 6px;
  border-color: var(--rl-line-strong);
}
.menu-pop--headerPhone {
  padding: 4px;
  border-color: var(--rl-line-strong);
  font-size: 14px;
}
.menu-pop__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-fg);
  /* the family and size of the menu; the line height stays the button's own (normal), as drawn */
  font-family: inherit;
  font-size: inherit;
  text-align: left;
  cursor: pointer;
}
.menu-pop--header .menu-pop__item {
  gap: 8px;
  height: 40px;
}
.menu-pop--headerPhone .menu-pop__item {
  height: 44px;
  padding: 0 12px;
}
.menu-pop--row .menu-pop__item :deep(svg) {
  color: var(--rl-muted);
}
.menu-pop__item:hover {
  background: var(--rl-raised);
}
.menu-pop__item.is-danger,
.menu-pop__item.is-danger :deep(svg) {
  color: var(--rl-bad);
}
.menu-pop__item.is-danger:hover {
  background: var(--rl-bad-bg);
}
.menu-pop__item.is-disabled,
.menu-pop__item.is-disabled :deep(svg) {
  color: var(--rl-faint);
  cursor: default;
}
.menu-pop__item.is-disabled:hover {
  background: transparent;
}
.menu-pop__hint {
  margin-left: auto;
  color: var(--rl-faint);
  font-size: 12px;
}
.menu-pop__sep {
  height: 1px;
  margin: 6px 4px;
  background: var(--rl-line);
}
</style>
