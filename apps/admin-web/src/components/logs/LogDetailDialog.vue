<script setup lang="ts">
import type { LogDetail } from "@/api/logs";
import JsonBlock from "@/components/shared/JsonBlock.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { fmtTime } from "@/lib/format";

// One logged request: a header of facts, then the redacted request and the answer, each with Copy.
const open = defineModel<boolean>({ required: true });
defineProps<{ log: LogDetail | null }>();
</script>

<template>
  <el-dialog v-model="open" :title="`Request ${log?.id ?? ''}`" width="min(860px, 94vw)">
    <template v-if="log">
      <el-descriptions :column="2" border class="head" aria-label="Request facts">
        <el-descriptions-item label="Status"><StatusChip :status="log.status_code" /></el-descriptions-item>
        <el-descriptions-item label="Time">{{ fmtTime(log.at_ms) }}</el-descriptions-item>
        <el-descriptions-item label="Route" :span="2">
          <span class="mono">{{ log.route }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="Provider">{{ log.provider || "-" }}</el-descriptions-item>
        <el-descriptions-item label="Duration">{{ log.duration_ms }} ms</el-descriptions-item>
        <el-descriptions-item label="Key">{{ log.key_label || "-" }}</el-descriptions-item>
        <el-descriptions-item label="Error">
          <span v-if="log.error_code" class="mono">{{ log.error_code }}</span>
          <template v-else>-</template>
        </el-descriptions-item>
        <el-descriptions-item label="Job" :span="2">
          <template v-if="log.job_id">
            <span class="mono">{{ log.job_id }}</span> {{ log.job_title || "" }}
          </template>
          <template v-else>no job</template>
        </el-descriptions-item>
      </el-descriptions>
      <h4>Request</h4>
      <JsonBlock :value="log.request" />
      <h4>Answer</h4>
      <JsonBlock v-if="log.response !== null" :value="log.response" />
      <el-empty v-else description="(none kept)" :image-size="48" />
    </template>
    <template #footer>
      <el-button @click="open = false">Close</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.head {
  margin: 0 0 8px;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
h4 {
  margin: 16px 0 6px;
  font-size: 14px;
}
</style>
