<script setup lang="ts">
import { computed } from "vue";
import { IconChevronDown } from "./icons";

// The select trigger of the canvas (specs/shared.md 2.1): a button `Status All v` that opens a listbox in the popover
// surface. The label word lives inside the control. Element Plus's el-select has a fixed width and its own caret, so
// this is a button over el-dropdown, and the control is as wide as its text. `size` is the drawn height: the toolbar
// 34, the page header 36, the pager's page-size select 32 (and no chevron there); a phone makes the toolbar one 44.
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

const current = computed(() => props.options.find((o) => o.value === props.modelValue));
</script>

<template>
  <el-dropdown
    trigger="click"
    role="listbox"
    popper-class="rl-menu"
    placement="bottom-start"
    @command="(v: string | number) => emit('update:modelValue', v)"
  >
    <button type="button" class="filter-select" :class="`is-${props.size}`" :aria-label="props.ariaLabel">
      <span v-if="props.label" class="filter-select__label">{{ props.label }}</span>
      <span class="filter-select__value">{{ current?.label ?? "" }}</span>
      <IconChevronDown v-if="props.chevron" class="filter-select__chevron" :size="14" :stroke="2" />
    </button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          v-for="o in props.options"
          :key="o.value"
          :command="o.value"
          :class="{ 'is-current': o.value === props.modelValue }"
          :aria-selected="o.value === props.modelValue"
          >{{ o.label }}</el-dropdown-item
        >
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
.filter-select {
  display: inline-flex;
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
  white-space: nowrap;
  cursor: pointer;
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
    flex: none;
    height: 44px;
  }
}
</style>
