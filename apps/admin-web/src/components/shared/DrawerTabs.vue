<script setup lang="ts">
import { drawerPanelId, drawerTabId } from "./drawer-tab-ids";

// The tab row of a drawer (specs/shared.md section 8): buttons with a 2 px underline, the current one dark and 600.
// Arrow keys move between tabs; the view shows the body of `modelValue` in DrawerPanels that share `idPrefix`. A phone makes each tab 44 high.
export interface DrawerTab {
  key: string;
  label: string;
}

const props = withDefaults(defineProps<{ tabs: DrawerTab[]; modelValue: string; idPrefix: string; label?: string }>(), {
  label: "Sections",
});
const emit = defineEmits<{ "update:modelValue": [key: string] }>();

function move(from: number, step: number, event: KeyboardEvent): void {
  const next = props.tabs[(from + step + props.tabs.length) % props.tabs.length];
  if (!next) return;
  emit("update:modelValue", next.key);
  const list = (event.currentTarget as HTMLElement).parentElement;
  list?.querySelectorAll<HTMLElement>("[role='tab']")[props.tabs.indexOf(next)]?.focus();
}
</script>

<template>
  <div class="drawer-tabs" role="tablist" :aria-label="props.label">
    <button
      v-for="(t, i) in props.tabs"
      :key="t.key"
      type="button"
      role="tab"
      :id="drawerTabId(props.idPrefix, t.key)"
      :aria-controls="drawerPanelId(props.idPrefix, t.key)"
      class="drawer-tabs__tab"
      :class="{ 'is-selected': t.key === props.modelValue }"
      :aria-selected="t.key === props.modelValue"
      :tabindex="t.key === props.modelValue ? 0 : -1"
      @click="emit('update:modelValue', t.key)"
      @keydown.right.prevent="move(i, 1, $event)"
      @keydown.left.prevent="move(i, -1, $event)"
    >
      {{ t.label }}
    </button>
  </div>
</template>

<style scoped>
.drawer-tabs {
  display: flex;
  gap: 20px;
}
.drawer-tabs__tab {
  height: 40px;
  padding: 0 2px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  /* the canvas's tab is a plain button: the browser's own line height */
  line-height: normal;
  cursor: pointer;
}
.drawer-tabs__tab.is-selected {
  border-bottom-color: var(--rl-primary);
  color: var(--rl-fg);
  font-weight: 600;
}
@media (max-width: 767.98px) {
  .drawer-tabs {
    padding: 0 12px;
  }
  .drawer-tabs__tab {
    height: 44px;
  }
}
</style>
