<script setup lang="ts">
import type { Component } from "vue";
import { IconMoreVertical } from "./icons";

// The row More button and its popover menu (specs/shared.md section 11): a 32 px icon button that opens a menu of
// icon + label items on the popover surface. A `danger` item is red and sits after a separator. The caller passes the
// icon components from ./icons; `select` carries the item's key.
export interface MenuItem {
  key: string;
  label: string;
  icon?: Component;
  danger?: boolean;
  // draws the separator before the item
  divided?: boolean;
}

const props = withDefaults(defineProps<{ items: MenuItem[]; label?: string }>(), { label: "More" });
const emit = defineEmits<{ select: [key: string] }>();
</script>

<template>
  <el-dropdown
    trigger="click"
    popper-class="rl-menu"
    placement="bottom-end"
    @command="(key: string) => emit('select', key)"
  >
    <button type="button" class="action-menu__trigger" :aria-label="props.label">
      <IconMoreVertical :size="16" :stroke="2.4" />
    </button>
    <template #dropdown>
      <el-dropdown-menu aria-label="Actions">
        <el-dropdown-item
          v-for="item in props.items"
          :key="item.key"
          :command="item.key"
          :divided="item.divided"
          :class="{ 'is-danger': item.danger }"
        >
          <span v-if="item.icon" class="rl-menu__icon"><component :is="item.icon" :size="16" /></span>
          {{ item.label }}
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
.action-menu__trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  cursor: pointer;
}
.action-menu__trigger[aria-expanded="true"] {
  background: var(--rl-raised);
}
</style>
