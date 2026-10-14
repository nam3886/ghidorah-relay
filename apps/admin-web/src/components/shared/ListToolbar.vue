<script setup lang="ts">
import { computed } from "vue";

// The row above a list: filters on the left (default slot), then "Updated hh:mm:ss", an auto-refresh switch when the
// view asks for one, Refresh, and the `actions` slot for the buttons of the view (Import, Clear logs...).
const props = withDefaults(
  defineProps<{
    loading?: boolean;
    // epoch ms of the last successful load
    updatedAt?: number | null;
    // undefined hides the switch; a boolean shows it (v-model:auto-refresh)
    autoRefresh?: boolean;
    autoRefreshLabel?: string;
  }>(),
  { loading: false, updatedAt: null, autoRefresh: undefined, autoRefreshLabel: "Auto-refresh" },
);
const emit = defineEmits<{ refresh: []; "update:autoRefresh": [value: boolean] }>();

const updated = computed(() =>
  props.updatedAt ? new Date(props.updatedAt).toLocaleTimeString("en-GB", { hour12: false }) : null,
);
</script>

<template>
  <div class="list-toolbar">
    <div class="list-toolbar__filters"><slot /></div>
    <div class="list-toolbar__right">
      <span v-if="updated" class="list-toolbar__note">Updated {{ updated }}</span>
      <template v-if="props.autoRefresh !== undefined">
        <el-switch
          :model-value="props.autoRefresh"
          :aria-label="props.autoRefreshLabel"
          @update:model-value="(v: string | number | boolean) => emit('update:autoRefresh', Boolean(v))"
        />
        <span class="list-toolbar__note">{{ props.autoRefreshLabel }}</span>
      </template>
      <el-button :loading="props.loading" @click="emit('refresh')">Refresh</el-button>
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.list-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin-bottom: 12px;
}
.list-toolbar__filters,
.list-toolbar__right {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.list-toolbar__note {
  color: var(--rl-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
</style>
