<script setup lang="ts">
import type { LogDetail } from "@/api/logs";
import JsonBlock from "@/components/shared/JsonBlock.vue";
import StatusChip from "@/components/shared/StatusChip.vue";

// One logged request: the redacted request and the answer, each with Copy.
const open = defineModel<boolean>({ required: true });
defineProps<{ log: LogDetail | null }>();
</script>

<template>
  <el-dialog v-model="open" :title="`Request ${log?.id ?? ''}`" width="min(860px, 94vw)">
    <template v-if="log">
      <p class="head">
        <StatusChip :status="log.status_code" /> <span class="mono">{{ log.route }}</span>
      </p>
      <h4>Request</h4>
      <JsonBlock :value="log.request" />
      <h4>Answer</h4>
      <JsonBlock v-if="log.response !== null" :value="log.response" />
      <p v-else class="none">(none kept)</p>
    </template>
    <template #footer>
      <el-button @click="open = false">Close</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
h4 {
  margin: 16px 0 6px;
  font-size: 13px;
}
.none {
  margin: 0;
  color: var(--rl-muted);
}
</style>
