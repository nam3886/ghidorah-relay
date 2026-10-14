<script setup lang="ts">
import { reactive, watch } from "vue";
import type { SettingItem } from "@/api/settings";
import DataTable from "@/components/shared/DataTable.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Column } from "@/components/shared/types";
import { valueText } from "./settings-value";

// The live settings. Each value is an input with its own draft: it is "edited" (and Save is enabled) only while the
// draft differs from the stored value. The view does the calls and reloads; a reload gives every draft its stored value.
const props = defineProps<{ items: SettingItem[]; loading: boolean; error: string | null; busy: boolean }>();
const emit = defineEmits<{ save: [key: string, text: string]; reset: [key: string]; retry: [] }>();

const columns: Column[] = [
  { key: "key", label: "Setting", minWidth: 260 },
  { key: "value", label: "Value", minWidth: 200 },
  { key: "source", label: "Source", width: 110 },
  { key: "default", label: "Default", minWidth: 120 },
];

const drafts = reactive<Record<string, string>>({});
watch(
  () => props.items,
  (items) => {
    for (const key of Object.keys(drafts)) delete drafts[key];
    for (const item of items) drafts[item.key] = valueText(item);
  },
  { immediate: true },
);

const edited = (item: SettingItem): boolean => drafts[item.key] !== valueText(item);
</script>

<template>
  <DataTable
    :columns="columns"
    :rows="items"
    :loading="loading"
    :error="error"
    row-key="key"
    empty-text="No settings"
    max-height="460px"
    @retry="emit('retry')"
  >
    <template #cell-key="{ row }">
      <div class="setting-key">{{ row.key }}</div>
      <div class="setting-description">{{ row.description }}</div>
    </template>
    <template #cell-value="{ row }">
      <div class="setting-value">
        <el-input
          v-model="drafts[row.key]"
          size="small"
          class="setting-input"
          :aria-label="`Value of ${row.key}`"
          :disabled="busy"
          @keyup.enter="edited(row) && emit('save', row.key, drafts[row.key] ?? '')"
        />
        <span v-if="edited(row)" class="setting-edited">edited</span>
      </div>
    </template>
    <!-- el-table also calls a column slot once with an empty row to measure it: draw nothing for that -->
    <template #cell-source="{ row }"><StatusChip v-if="row.source" :status="row.source" /></template>
    <template #cell-default="{ row }">
      <span class="setting-default">{{ JSON.stringify(row.default) }}</span>
    </template>
    <template #actions="{ row }">
      <el-button
        size="small"
        type="primary"
        :disabled="busy || !edited(row)"
        :aria-label="`Save ${row.key}`"
        @click="emit('save', row.key, drafts[row.key] ?? '')"
        >Save</el-button
      >
      <el-button size="small" :disabled="busy" :aria-label="`Reset ${row.key}`" @click="emit('reset', row.key)"
        >Reset</el-button
      >
    </template>
  </DataTable>
</template>

<style scoped>
.setting-key,
.setting-default {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
.setting-description {
  max-width: 420px;
  color: var(--rl-muted);
  font-size: 12px;
  white-space: normal;
}
.setting-value {
  display: flex;
  align-items: center;
  gap: 8px;
}
.setting-input {
  width: 180px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.setting-edited {
  color: var(--rl-warn);
  font-size: 12px;
}
</style>
