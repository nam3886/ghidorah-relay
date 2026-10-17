<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { IconCheck, IconChevronDown } from "./icons";

// The filter select of the canvas, everywhere a list is filtered (specs/shared.md 2.1) and in the pager (page size).
// The trigger is the drawn button `Status All v`: the label word, the value and a 14 px chevron inside one control as
// wide as its text. The open list is the drawn Provider listbox of B-States (shell.md 7): the popover surface, items
// 36 px and 13 px (40 and 14 on a tablet, 44 and 14 on a phone), the chosen item on the primary tint, weight 600, with
// a check at its right. Our own popover, not Element Plus's or the browser's: those cannot be drawn so.
// - The trigger is a select-only combobox (aria-expanded, aria-controls, aria-activedescendant): the focus stays on it.
//   Enter, Space, ArrowDown and ArrowUp open the list; in it ArrowDown/ArrowUp/Home/End move, Enter and Space choose,
//   Escape closes, Tab chooses nothing and moves on, typing jumps to the next option starting with what was typed.
// - The list is drawn in the body, fixed under the trigger and left-aligned to it (right-aligned when it would run past
//   the right edge of the window, above the trigger when it would run past the bottom), so a card that hides overflow
//   or a filter row that scrolls sideways never clips it. While open it follows the trigger on every frame (a scroll, a
//   resize, or the page moving under it), so it never covers the trigger; a click elsewhere closes it. `size` is the trigger height: toolbar 34 (44 on a phone), page header 36, pager 32 (no chevron there).
export interface SelectOption {
  value: string | number;
  label: string;
}

const props = withDefaults(
  defineProps<{
    modelValue: string | number;
    options: SelectOption[];
    // the word inside the control, before the value: `Status`
    label?: string;
    // the accessible name when there is no label word
    ariaLabel?: string;
    size?: "toolbar" | "header" | "pager";
    chevron?: boolean;
  }>(),
  { label: undefined, ariaLabel: undefined, size: "toolbar", chevron: true },
);
const emit = defineEmits<{ "update:modelValue": [value: string | number] }>();
const { isPhone, isTablet } = useBreakpoint();

const id = useId();
const labelId = `${id}-label`;
const listId = `${id}-list`;
const optionId = (i: number): string => `${id}-opt-${i}`;

const current = computed(() => props.options.find((o) => o.value === props.modelValue));
const chosenIndex = computed(() => props.options.findIndex((o) => o.value === props.modelValue));

const open = ref(false);
const active = ref(-1);
// the focus ring on the active item is drawn only while the keyboard moves it (a click opens the list as drawn)
const byKeyboard = ref(false);
const trigger = ref<HTMLButtonElement>();
const list = ref<HTMLElement>();
const place = ref<{ top: number; left?: number; right?: number; minWidth: number }>({ top: 0, left: 0, minWidth: 0 });
const GAP = 6;

// Under the trigger, left-aligned; right-aligned when the list would pass the window's right edge; above the trigger
// when it would pass the bottom and there is room above. Never narrower than the trigger. Runs on every frame while
// open, so the place is written only when it changed.
function measure(): void {
  const rect = trigger.value?.getBoundingClientRect();
  if (!rect) return;
  const width = list.value?.offsetWidth ?? 0;
  const height = list.value?.offsetHeight ?? 0;
  let top = rect.bottom + GAP;
  if (height && top + height > window.innerHeight && rect.top - GAP - height >= 0) top = rect.top - GAP - height;
  const tooWide = width && rect.left + width > window.innerWidth;
  // the drawn width (180 at 1440, 200 below), or the trigger's when that is wider
  const minWidth = Math.max(rect.width, isPhone.value || isTablet.value ? 200 : 180);
  const next = tooWide
    ? { top, right: Math.max(0, window.innerWidth - rect.right), minWidth }
    : { top, left: rect.left, minWidth };
  const was = place.value;
  if (next.top !== was.top || next.left !== was.left || next.right !== was.right || next.minWidth !== was.minWidth)
    place.value = next;
}

// The list is fixed in the body, so anything that moves the trigger (a scroll, a resize, a banner or a band that
// arrives above it after the list opened) would leave it behind, over the trigger: it is placed again on every frame.
let frame = 0;
function follow(): void {
  if (!open.value) return;
  measure();
  frame = requestAnimationFrame(follow);
}
function onOutside(event: Event): void {
  const target = event.target as Node | null;
  if (target && (list.value?.contains(target) || trigger.value?.contains(target))) return;
  close();
}

async function show(at: number): Promise<void> {
  if (open.value) return;
  active.value = at;
  open.value = true;
  document.addEventListener("pointerdown", onOutside, true);
  await nextTick();
  measure();
  // the first pass knew the list's own width; the second places it at the width it now has
  await nextTick();
  measure();
  reveal();
  if (open.value) frame = requestAnimationFrame(follow);
}

function close(): void {
  if (!open.value) return;
  open.value = false;
  active.value = -1;
  document.removeEventListener("pointerdown", onOutside, true);
  cancelAnimationFrame(frame);
}

function choose(index: number): void {
  const option = props.options[index];
  close();
  trigger.value?.focus();
  if (option && option.value !== props.modelValue) emit("update:modelValue", option.value);
}

function reveal(): void {
  if (active.value < 0) return;
  (list.value?.children[active.value] as HTMLElement | undefined)?.scrollIntoView?.({ block: "nearest" });
}

function moveTo(index: number): void {
  if (!props.options.length) return;
  active.value = Math.min(props.options.length - 1, Math.max(0, index));
  reveal();
}

// Type-to-select: the letters typed within 500 ms of each other. A word is matched from the active option; one letter,
// or the same letter again, goes on to the next option that starts with it.
let typed = "";
let typedAt = 0;
function typeAhead(letter: string): void {
  const now = Date.now();
  typed = now - typedAt > 500 ? letter : typed + letter;
  typedAt = now;
  const repeat = [...typed].every((c) => c === typed[0]);
  const word = (repeat ? letter : typed).toLowerCase();
  const count = props.options.length;
  const from = Math.max(open.value ? active.value : chosenIndex.value, 0);
  for (let step = repeat ? 1 : 0; step <= count; step++) {
    const at = (from + step) % count;
    if (props.options[at]?.label.toLowerCase().startsWith(word)) {
      if (open.value) moveTo(at);
      else void show(at);
      return;
    }
  }
}

function onKey(event: KeyboardEvent): void {
  byKeyboard.value = true;
  const start = chosenIndex.value >= 0 ? chosenIndex.value : 0;
  if (!open.value) {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      void show(start);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      typeAhead(event.key);
    }
    return;
  }
  const moves: Record<string, number> = {
    ArrowDown: active.value + 1,
    ArrowUp: active.value - 1,
    Home: 0,
    End: props.options.length - 1,
    PageDown: active.value + 10,
    PageUp: active.value - 10,
  };
  if (event.key in moves) {
    event.preventDefault();
    moveTo(moves[event.key] ?? 0);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    choose(active.value);
  } else if (event.key === "Escape") {
    event.preventDefault();
    close();
  } else if (event.key === "Tab") {
    close();
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    typeAhead(event.key);
  }
}

function onClick(): void {
  byKeyboard.value = false;
  if (open.value) close();
  else void show(chosenIndex.value >= 0 ? chosenIndex.value : 0);
}

const listClass = computed(() => (isPhone.value ? "is-phone" : isTablet.value ? "is-tablet" : "is-desktop"));
onBeforeUnmount(close);
</script>

<template>
  <button
    ref="trigger"
    type="button"
    role="combobox"
    class="filter-select"
    :class="[`is-${props.size}`, { 'is-open': open }]"
    aria-haspopup="listbox"
    :aria-expanded="open"
    :aria-controls="listId"
    :aria-activedescendant="open && active >= 0 ? optionId(active) : undefined"
    :aria-labelledby="props.label ? labelId : undefined"
    :aria-label="props.label ? undefined : props.ariaLabel"
    @click="onClick"
    @keydown="onKey"
  >
    <span v-if="props.label" :id="labelId" class="filter-select__label">{{ props.label }}</span>
    <span class="filter-select__value">{{ current?.label ?? "" }}</span>
    <IconChevronDown v-if="props.chevron" class="filter-select__chevron" :size="14" :stroke="2" />
    <!-- inside the trigger so the component has one root (a class given to it lands here); drawn in the body -->
    <Teleport to="body">
      <div
        v-if="open"
        :id="listId"
        ref="list"
        role="listbox"
        class="filter-list"
        :class="[listClass, { 'is-keyboard': byKeyboard }]"
        :aria-labelledby="props.label ? labelId : undefined"
        :aria-label="props.label ? undefined : props.ariaLabel"
        :style="{
          top: `${place.top}px`,
          left: place.left === undefined ? undefined : `${place.left}px`,
          right: place.right === undefined ? undefined : `${place.right}px`,
          minWidth: `${place.minWidth}px`,
        }"
      >
        <div
          v-for="(o, i) in props.options"
          :id="optionId(i)"
          :key="o.value"
          role="option"
          class="filter-list__item"
          :class="{ 'is-selected': o.value === props.modelValue, 'is-active': i === active }"
          :aria-selected="o.value === props.modelValue"
          @pointerdown.prevent
          @pointermove="
            active = i;
            byKeyboard = false;
          "
          @click="choose(i)"
        >
          <span class="filter-list__text">{{ o.label }}</span>
          <IconCheck v-if="o.value === props.modelValue" class="filter-list__check" :size="16" :stroke="2" />
        </div>
      </div>
    </Teleport>
  </button>
</template>

<style scoped>
.filter-select {
  box-sizing: border-box;
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  white-space: nowrap;
  cursor: pointer;
}
.filter-select.is-open {
  background: var(--rl-raised);
}
.filter-select.is-header {
  height: 36px;
}
.filter-select.is-pager {
  height: 32px;
  padding: 0 10px;
}
.filter-select__label {
  color: var(--rl-muted);
}
.filter-select__chevron {
  flex: none;
}
@media (max-width: 767.98px) {
  .filter-select.is-toolbar {
    height: 44px;
  }
}

/* the drawn listbox: 180 wide at 1440, 200 below; items 36/13 px, 40/14 on a tablet, 44/14 on a phone */
.filter-list {
  position: fixed;
  z-index: 50;
  box-sizing: border-box;
  width: max-content;
  max-width: min(360px, calc(100vw - 32px));
  max-height: min(420px, calc(100vh - 32px));
  overflow-y: auto;
  padding: 6px;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-pop);
  color: var(--rl-fg);
  font-size: 13px;
  line-height: 20px;
}
.filter-list.is-tablet,
.filter-list.is-phone {
  font-size: 14px;
}
.filter-list__item {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border-radius: 8px;
  font-weight: 400;
  line-height: normal;
  cursor: pointer;
  white-space: nowrap;
}
.filter-list.is-tablet .filter-list__item {
  height: 40px;
}
.filter-list.is-phone .filter-list__item {
  height: 44px;
}
.filter-list__item.is-active {
  background: var(--rl-raised);
}
.filter-list__item.is-selected {
  background: var(--rl-primary-soft);
  font-weight: 600;
}
/* the keyboard's place on the chosen item: the focus ring drawn inside it */
.filter-list.is-keyboard .filter-list__item.is-selected.is-active {
  box-shadow: inset 0 0 0 2px var(--rl-focus);
}
.filter-list__text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.filter-list__check {
  flex: none;
  color: var(--rl-primary);
}
</style>
