<script setup lang="ts">
import type { Component } from "vue";

// An icon-only button with a tooltip. `label` is both the tooltip and the accessible name (aria-label), so tests
// find it with getByRole("button", { name: label }). The size comes from the global ElConfigProvider.
// A disabled button gets no pointer events, so the tooltip sits on a wrapper.
const props = withDefaults(
  defineProps<{
    icon: Component;
    label: string;
    type?: "default" | "primary" | "success" | "warning" | "danger" | "info";
    disabled?: boolean;
    loading?: boolean;
  }>(),
  { type: "default", disabled: false, loading: false },
);
const emit = defineEmits<{ click: [event: MouseEvent] }>();
</script>

<template>
  <el-tooltip :content="props.label" placement="top" :show-after="300">
    <span class="icon-action">
      <el-button
        text
        :type="props.type"
        :disabled="props.disabled"
        :loading="props.loading"
        :aria-label="props.label"
        @click="emit('click', $event)"
      >
        <el-icon v-if="!props.loading"><component :is="props.icon" /></el-icon>
      </el-button>
    </span>
  </el-tooltip>
</template>

<style scoped>
.icon-action {
  display: inline-flex;
}
.icon-action :deep(.el-button) {
  padding: 4px 6px;
}
</style>
