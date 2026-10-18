<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from "vue";
import { useZIndex } from "element-plus";
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
// - `variant="field"` is the canvas's labelled select (B/T/P-Alerts toolbar), drawn as the box's Chrome draws a
//   native select element, but our own control (a native one looks and opens differently on every system): the label
//   above a bordered box 34 high (36 on a tablet) holding the value and Chrome's 10 x 7 arrow, as wide as its longest
//   option; on a phone the canvas's chip, the word inside a 44 px box and the value in a borderless control. The open
//   list is the same listbox.
// - `appearance="none"` (field only) is the canvas's select with `appearance: none` (B-AccountImport): no look of the
//   browser's, the page's 14 px chevron 12 px in from the end instead of Chrome's arrow, and no chip on a phone; the
//   page that draws it so sets its height and words.
export interface SelectOption {
  value: string | number;
  label: string;
}

const props = withDefaults(
  defineProps<{
    modelValue: string | number;
    options: SelectOption[];
    // the word inside the control, before the value: `Status` (above it in the field variant)
    label?: string;
    // the accessible name when there is no label word
    ariaLabel?: string;
    size?: "toolbar" | "header" | "pager";
    chevron?: boolean;
    variant?: "chip" | "field";
    appearance?: "auto" | "none";
    disabled?: boolean;
    // the field variant as a form's field (B/T/P-Test): it fills its column, the box 36 high with 14 px text and the
    // label 500 with 6 px under it, 44 high on a phone and never the chip
    block?: boolean;
  }>(),
  {
    label: undefined,
    ariaLabel: undefined,
    size: "toolbar",
    chevron: true,
    variant: "chip",
    appearance: "auto",
    disabled: false,
    block: false,
  },
);
const emit = defineEmits<{ "update:modelValue": [value: string | number] }>();
const { isPhone, isTablet } = useBreakpoint();

const id = useId();
const labelId = `${id}-label`;
const listId = `${id}-list`;
const triggerId = `${id}-trigger`;
const optionId = (i: number): string => `${id}-opt-${i}`;
const field = computed(() => props.variant === "field");
// the field the page draws itself (`appearance="none"`), and the field drawn as the canvas's phone chip (never a
// form's `block` field)
const drawn = computed(() => field.value && props.appearance === "none");
const chip = computed(() => field.value && !drawn.value && !props.block && isPhone.value);

const current = computed(() => props.options.find((o) => o.value === props.modelValue));
const chosenIndex = computed(() => props.options.findIndex((o) => o.value === props.modelValue));

const open = ref(false);
const active = ref(-1);
// the focus ring on the active item is drawn only while the keyboard moves it (a click opens the list as drawn)
const byKeyboard = ref(false);
const trigger = ref<HTMLButtonElement>();
// the field variant's own box (the label and the trigger); on a phone it is the drawn chip, so the list hangs from it
const box = ref<HTMLElement>();
const list = ref<HTMLElement>();
const place = ref<{ top: number; left?: number; right?: number; minWidth: number }>({ top: 0, left: 0, minWidth: 0 });
const GAP = 6;

// Under the trigger, left-aligned; right-aligned when the list would pass the window's right edge; above the trigger
// when it would pass the bottom and there is room above. Never narrower than the trigger. Runs on every frame while
// open, so the place is written only when it changed.
function measure(): void {
  const rect = (chip.value ? box.value : trigger.value)?.getBoundingClientRect();
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
  if (target && (list.value?.contains(target) || trigger.value?.contains(target) || box.value?.contains(target)))
    return;
  close();
}

// The open list takes Element Plus's next z-index, as its own popups do: inside a dialog (the key form's Mode) it is drawn
// over the dialog, never under it.
const { nextZIndex } = useZIndex();
const listZ = ref<number>();

async function show(at: number): Promise<void> {
  if (open.value) return;
  active.value = at;
  listZ.value = nextZIndex();
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

// A press in the list keeps the focus on the trigger, so the trigger's keydown is the only one that sees a key while the
// list is open: an option, and the padding around the options too (a press there used to move the focus off the
// trigger, and the next Escape went on to a dialog around the select and closed it). A press in the scrollbar gutter
// (past the content box: clientWidth and clientHeight leave the scrollbar out) is left alone, so a long list can still
// be dragged by its scrollbar; wheel and touch scrolling do not go through a press.
function onListPress(event: PointerEvent): void {
  const el = event.currentTarget as HTMLElement;
  const box = el.getBoundingClientRect();
  const x = event.clientX - box.left - el.clientLeft;
  const y = event.clientY - box.top - el.clientTop;
  if (x < el.clientWidth && y < el.clientHeight) event.preventDefault();
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
    // the list's own Escape: a dialog around it (which closes on Escape from the document) stays open
    event.preventDefault();
    event.stopPropagation();
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

// A select is as wide as its longest option on a whole pixel: Chrome takes the floor of what the page measures (95.2
// is 95 on B-Alerts, 88.3 is 88, and 117.6 is 117 on B-Logs, the case that tells the floor from the nearest pixel), so
// the control beside it starts on a whole pixel too: the field's value box takes that width. Measured without it,
// once the fonts are in and whenever the options change.
const valueBox = ref<HTMLElement>();
const valueWidth = ref<number>();
async function sizeValue(): Promise<void> {
  if (!field.value) return;
  valueWidth.value = undefined;
  await nextTick();
  const width = valueBox.value?.getBoundingClientRect().width;
  if (width) valueWidth.value = Math.floor(width);
}
onMounted(() => {
  void sizeValue();
  void document.fonts?.ready.then(sizeValue);
});
watch(() => props.options, sizeValue);

// The chip's root is its trigger; the field's root is its box, with the label and the trigger inside (one root
// element either way, so a class given to the component lands on it). The trigger's attributes and handlers: on the
// root for the chip, on the inner button for the field.
const triggerAttrs = computed(() => ({
  id: triggerId,
  type: "button" as const,
  role: "combobox",
  "aria-haspopup": "listbox" as const,
  "aria-expanded": open.value,
  "aria-controls": listId,
  "aria-activedescendant": open.value && active.value >= 0 ? optionId(active.value) : undefined,
  // a given ariaLabel names the control even beside a label word: two fields with one word ("dola video Mode")
  "aria-labelledby": props.label && !props.ariaLabel ? labelId : undefined,
  "aria-label": props.ariaLabel,
  disabled: props.disabled || undefined,
}));
const triggerEvents = { click: onClick, keydown: onKey };
const fieldClass = computed(() => ({
  "is-chip": chip.value,
  "is-block": props.block,
  "is-tall": isTablet.value && !drawn.value,
  "is-drawn": drawn.value,
  "is-open": open.value,
  "is-disabled": props.disabled,
}));
function setRoot(el: unknown): void {
  if (field.value) box.value = (el as HTMLElement | null) ?? undefined;
  else trigger.value = (el as HTMLButtonElement | null) ?? undefined;
}
</script>

<template>
  <component
    :is="field ? 'div' : 'button'"
    :ref="setRoot"
    v-bind="field ? {} : triggerAttrs"
    :class="field ? ['filter-field', fieldClass] : ['filter-select', [`is-${props.size}`, { 'is-open': open }]]"
    v-on="field ? {} : triggerEvents"
  >
    <template v-if="field">
      <label :id="labelId" :for="triggerId" class="filter-field__label">{{ props.label }}</label>
      <button
        ref="trigger"
        v-bind="triggerAttrs"
        class="filter-select is-field"
        :class="{ 'is-open': open }"
        v-on="triggerEvents"
      >
        <!-- every option in one cell, only the current one seen: the box is as wide as its longest option, as a
             select is -->
        <span ref="valueBox" class="filter-field__value" :style="valueWidth ? { width: `${valueWidth}px` } : undefined"
          ><span
            v-for="o in props.options"
            :key="o.value"
            :class="{ 'is-current': o.value === props.modelValue }"
            :aria-hidden="o.value === props.modelValue ? undefined : 'true'"
            >{{ o.label }}</span
          ></span
        >
        <IconChevronDown v-if="drawn" class="filter-field__chevron" :size="14" :stroke="2" />
        <svg v-else class="filter-field__arrow" width="12" height="9" viewBox="0 0 12 9" aria-hidden="true">
          <path d="M2 2l4 4 4-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="miter" />
        </svg>
      </button>
    </template>
    <template v-else>
      <span v-if="props.label" :id="labelId" class="filter-select__label">{{ props.label }}</span>
      <span class="filter-select__value">{{ current?.label ?? "" }}</span>
      <IconChevronDown v-if="props.chevron" class="filter-select__chevron" :size="14" :stroke="2" />
    </template>
    <!-- inside the root so the component has one root (a class given to it lands there); drawn in the body -->
    <Teleport to="body">
      <div
        v-if="open"
        :id="listId"
        ref="list"
        role="listbox"
        class="filter-list"
        :class="[listClass, { 'is-keyboard': byKeyboard }]"
        :aria-labelledby="props.label && !props.ariaLabel ? labelId : undefined"
        :aria-label="props.ariaLabel"
        :style="{
          top: `${place.top}px`,
          left: place.left === undefined ? undefined : `${place.left}px`,
          right: place.right === undefined ? undefined : `${place.right}px`,
          minWidth: `${place.minWidth}px`,
          zIndex: listZ,
        }"
        @pointerdown="onListPress"
      >
        <div
          v-for="(o, i) in props.options"
          :id="optionId(i)"
          :key="o.value"
          role="option"
          class="filter-list__item"
          :class="{ 'is-selected': o.value === props.modelValue, 'is-active': i === active }"
          :aria-selected="o.value === props.modelValue"
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
  </component>
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

/* The field: the canvas's labelled native select as the box's Chrome draws it (measured on B/T/P-Alerts). The label
   12 px muted above; the box 34 high (36 on a tablet) with the author padding 10, the value 4 px further in (the
   select's own inner start), as wide as its longest option plus the select's own room for the arrow at the end; the
   arrow 2 px in from the padding edge, centred, in the text colour. On a phone the canvas's chip: the word inside a
   44 px box (padding 0 6 0 12), the value in a borderless control 40 high with no author padding. */
.filter-field {
  --field-pad: 10px;
  --field-arrow: 17px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.filter-field__label {
  font-size: 12px;
  line-height: 20px;
  color: var(--rl-muted);
}
.filter-select.is-field {
  position: relative;
  display: flex;
  gap: 0;
  padding: 0 calc(var(--field-pad) + var(--field-arrow)) 0 calc(var(--field-pad) + 4px);
  text-align: left;
}
.filter-field.is-tall .filter-select.is-field {
  height: 36px;
}
.filter-field__value {
  display: grid;
}
.filter-field__value > span {
  grid-area: 1 / 1;
}
.filter-field__value > span:not(.is-current) {
  visibility: hidden;
}
.filter-field__arrow {
  position: absolute;
  top: calc(50% - 4px);
  right: 2px;
}
/* `appearance="none"`: the box has no inner start of its own and room at its end for the 14 px chevron 12 px in from
   its outer edge (11 from the padding edge it is placed in: the 1 px border lies outside it) */
.filter-field.is-drawn .filter-select.is-field {
  padding: 0 36px 0 12px;
}
.filter-field__chevron {
  position: absolute;
  top: calc(50% - 7px);
  right: 11px;
  color: var(--rl-muted);
}
.filter-select.is-field:disabled {
  background: var(--rl-raised);
  color: var(--rl-faint);
  cursor: default;
}
.filter-field.is-chip {
  --field-pad: 0px;
  box-sizing: border-box;
  flex: none;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  height: 44px;
  padding: 0 6px 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
}
.filter-field.is-chip .filter-field__label {
  font-size: 13px;
}
.filter-field.is-chip .filter-select.is-field {
  height: 40px;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.filter-field.is-chip.is-open {
  background: var(--rl-raised);
}
/* A form's field (B/T/P-Test, `block`): the select of the form as Chrome draws it there, filling its column; the box 36
   high (44 on a phone, never the chip) with 14 px text, the label 500 with 6 px under it. */
.filter-field.is-block {
  gap: 6px;
}
.filter-field.is-block .filter-field__label {
  font-weight: 500;
}
.filter-field.is-block .filter-select.is-field {
  height: 36px;
  font-size: 14px;
}
@media (max-width: 767.98px) {
  .filter-field.is-block .filter-select.is-field {
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
