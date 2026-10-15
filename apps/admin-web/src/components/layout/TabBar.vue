<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { TABS } from "@/router";
import { useStatsStore } from "@/stores/stats";

// Element Plus tabs bound to the route. The panes stay empty: the routed view renders below the stats strip.
// Two tabs carry a count: Jobs the jobs still to finish, Alerts the open alert groups. The count is aria-hidden, so
// the tab's name stays the plain label.
const route = useRoute();
const router = useRouter();
const stats = useStatsStore();

const counts = computed<Record<string, number>>(() => ({
  jobs: stats.data ? stats.data.jobs.in_progress + stats.data.jobs.queued : 0,
  alerts: stats.openGroups,
}));

function go(name: string | number): void {
  void router.push({ name: String(name) });
}
</script>

<template>
  <nav class="tab-bar" aria-label="Sections">
    <el-tabs :model-value="String(route.name)" @tab-change="go">
      <el-tab-pane v-for="tab in TABS" :key="tab.name" :name="tab.name">
        <template #label>
          <span class="tab-label">{{ tab.label }}</span>
          <el-badge
            v-if="counts[tab.name]"
            class="tab-count"
            type="info"
            :value="counts[tab.name]"
            :max="999"
            :data-testid="`tab-count-${tab.name}`"
            aria-hidden="true"
          />
        </template>
      </el-tab-pane>
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
.tab-bar :deep(.el-tabs__item) {
  padding: 0 12px;
}
.tab-bar :deep(.el-tabs__item:first-child) {
  padding-left: 0;
}
.tab-bar :deep(.el-tabs__item:hover) {
  color: var(--rl-fg);
}
.tab-bar :deep(.el-tabs__item.is-active) {
  color: var(--el-color-primary);
}
/* an inline count beside the label (the badge normally floats at a corner) */
.tab-count {
  margin-left: 6px;
  vertical-align: middle;
}
.tab-count :deep(.el-badge__content) {
  position: static;
  transform: none;
  font-variant-numeric: tabular-nums;
  color: var(--rl-muted);
  background: var(--rl-surface-2);
  border: 1px solid var(--rl-line);
}
</style>
