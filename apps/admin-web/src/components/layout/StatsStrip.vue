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
        <!-- the value is already formatted text ("1,234", "-"): the formatter hands it through unchanged -->
        <el-statistic :value="0" :formatter="() => cell.value" :class="{ 'stat-value--bad': cell.danger }">
          <template #title
            ><span class="stat-label">{{ cell.label }}</span></template
          >
        </el-statistic>
      </router-link>
    </template>
    <el-skeleton v-else animated :rows="1" class="stats-skeleton" />
  </section>
</template>

<style scoped>
/* cells wrap into rows: two or three across on a phone, all in one line on a wide screen */
.stats-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  margin: 12px 16px 0;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base);
}
.stat-cell {
  min-width: 0;
  padding: 8px 12px;
  color: inherit;
  text-decoration: none;
  border-right: 1px solid var(--rl-line);
  transition: background 120ms;
}
.stat-cell:hover {
  background: var(--rl-surface-2);
}
.stat-label {
  display: block;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stat-cell :deep(.el-statistic__content) {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat-value--bad :deep(.el-statistic__content) {
  color: var(--rl-bad);
}
.stats-skeleton {
  padding: 8px 12px;
}
</style>
