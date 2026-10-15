<script setup lang="ts">
import { Edit } from "@element-plus/icons-vue";
import type { FrameKind } from "@/api/frames";
import IconAction from "@/components/shared/IconAction.vue";
import DataTable from "@/components/shared/DataTable.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Column } from "@/components/shared/types";
import { DEFAULT_TARGET, lockLine, versionLine } from "./frames-model";
import type { FramesController } from "./use-frames";

// One kind (image, video): its default frame and a table of its models. The default can be edited, reset to the shipped
// text when it differs from it, and applied to every model that is not kept on its own frame.
defineProps<{ frames: FramesController; kind: FrameKind }>();

const columns: Column[] = [
  { key: "model", label: "Model", minWidth: 180 },
  { key: "source", label: "Source", width: 110 },
  { key: "effective", label: "Effective version", minWidth: 240 },
  { key: "locked", label: "Keep own", minWidth: 220 },
];
</script>

<template>
  <section class="frame-kind" :data-frame-kind="kind.kind">
    <header class="frame-kind__head">
      <h3>{{ kind.kind }}</h3>
      <span class="frame-kind__version">Default: {{ versionLine(kind.default) }}</span>
      <template v-if="kind.shipped_differs">
        <StatusChip status="shipped differs" label="differs from the shipped default" tone="warn" />
        <el-button :disabled="frames.controlsOff" @click="frames.resetShipped(kind.kind)"
          >Reset to the shipped default</el-button
        >
      </template>
      <span class="frame-kind__grow" />
      <IconAction
        :icon="Edit"
        label="Edit the default"
        :disabled="frames.opensOff"
        @click="frames.edit(kind.kind, DEFAULT_TARGET)"
      />
    </header>
    <DataTable :columns="columns" :rows="kind.models" row-key="model" empty-text="No models">
      <template #cell-model="{ row }">
        <span class="frame-kind__mono">{{ row.model }}</span>
      </template>
      <!-- el-table also calls a column slot once with an empty row to measure it: draw nothing for that -->
      <template #cell-source="{ row }"><StatusChip v-if="row.source" :status="row.source" /></template>
      <template #cell-effective="{ row }">
        <span class="frame-kind__small">{{ versionLine(row.effective) }}</span>
      </template>
      <template #cell-locked="{ row }">
        <div class="frame-kind__lock">
          <el-checkbox
            :model-value="row.locked"
            :disabled="frames.controlsOff"
            :aria-label="`Keep own frame of ${row.model}`"
            @change="(value: string | number | boolean) => frames.setLock(kind.kind, row.model, Boolean(value))"
            >keep own</el-checkbox
          >
          <span class="frame-kind__small frame-kind__muted">{{ lockLine(row.lock) }}</span>
        </div>
      </template>
      <template #actions="{ row }">
        <IconAction
          :icon="Edit"
          :label="`Edit ${row.model}`"
          :disabled="frames.opensOff"
          @click="frames.edit(kind.kind, row.model)"
        />
      </template>
    </DataTable>
  </section>
</template>

<style scoped>
.frame-kind {
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.frame-kind__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--rl-line);
}
.frame-kind__head h3 {
  margin: 0;
  font-size: 16px;
}
.frame-kind__version,
.frame-kind__small {
  font-size: 12px;
}
.frame-kind__grow {
  flex: 1;
}
.frame-kind__mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
/* the checkbox and its history on one line: a one-line row stays one line */
.frame-kind__lock {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0 12px;
}
.frame-kind__muted {
  color: var(--rl-muted);
}
</style>
