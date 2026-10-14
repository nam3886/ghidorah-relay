<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";
import { TABS } from "@/router";

// Element Plus tabs bound to the route. The panes stay empty: the routed view renders below the stats strip.
const route = useRoute();
const router = useRouter();

function go(name: string | number): void {
  void router.push({ name: String(name) });
}
</script>

<template>
  <nav class="tab-bar" aria-label="Sections">
    <el-tabs :model-value="String(route.name)" @tab-change="go">
      <el-tab-pane v-for="tab in TABS" :key="tab.name" :name="tab.name" :label="tab.label" />
    </el-tabs>
  </nav>
</template>

<style scoped>
.tab-bar {
  padding: 0 16px;
  background: var(--rl-surface);
  border-bottom: 1px solid var(--rl-line);
}
.tab-bar :deep(.el-tabs__header) {
  margin: 0;
}
.tab-bar :deep(.el-tabs__nav-wrap::after) {
  display: none;
}
</style>
