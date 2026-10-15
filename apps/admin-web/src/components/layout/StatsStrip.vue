<script setup lang="ts">
import { computed } from "vue";
import { statCells } from "@/lib/stat-cells";
import { useStatsStore } from "@/stores/stats";

// One surface; each cell links to the tab it counts. Numbers are red only when non-zero and bad.
const stats = useStatsStore();
const cells = computed(() => (stats.data ? statCells(stats.data) : []));
</script>

<template>
  <section class="stats-strip" aria-label="Totals" :aria-busy="stats.loading">
    <template v-if="cells.length">
      <router-link
        v-for="cell in cells"
        :key="cell.key"
        :to="cell.to"
        class="stat-cell"
        :title="cell.title"
        :data-testid="`stat-${cell.key}`"
      >
        <span class="stat-label">{{ cell.label }}</span>
        <span class="stat-value" :class="{ 'stat-value--bad': cell.danger }">{{ cell.value }}</span>
      </router-link>
    </template>
    <el-skeleton v-else animated :rows="1" class="stats-skeleton" />
  </section>
</template>

<style scoped>
.stats-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  margin: 12px 16px 0;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base);
}
.stat-cell {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 2px;
  min-width: 0;
  padding: 8px 12px;
  color: inherit;
  text-decoration: none;
  border-right: 1px solid var(--rl-line);
  transition: background 120ms;
}
.stat-cell:last-child {
  border-right: none;
}
.stat-cell:hover {
  background: var(--rl-surface-2);
}
.stat-label {
  overflow: hidden;
  font-size: 12px;
  color: var(--rl-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stat-value {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat-value--bad {
  color: var(--rl-bad);
}
.stats-skeleton {
  padding: 8px 12px;
}
</style>
