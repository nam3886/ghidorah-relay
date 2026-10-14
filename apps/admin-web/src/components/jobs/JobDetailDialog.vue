<script setup lang="ts">
import { computed } from "vue";
import { useUiStore } from "@/stores/ui";

// Mounted once in the shell; Jobs and Alerts open it through ui.openJob(id). Replaced by the Jobs round.
const ui = useUiStore();
const visible = computed({
  get: () => ui.openJobId !== null,
  set: (open: boolean) => {
    if (!open) ui.closeJob();
  },
});
</script>

<template>
  <el-dialog v-model="visible" title="Job detail" width="min(960px, 94vw)" destroy-on-close>
    <section data-placeholder="relay-admin-placeholder">
      <p>Job {{ ui.openJobId }}</p>
    </section>
    <template #footer>
      <el-button @click="visible = false">Close</el-button>
    </template>
  </el-dialog>
</template>
