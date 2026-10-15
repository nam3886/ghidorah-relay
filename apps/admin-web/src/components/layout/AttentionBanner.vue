<script setup lang="ts">
import { Warning } from "@element-plus/icons-vue";
import { computed, ref } from "vue";
import { useStatsStore } from "@/stores/stats";

// One calm banner on every tab when there is something: the open alert groups and the live alert conditions (they
// vanish by themselves). More than two conditions fold behind one line with a count, so the banner never pushes the
// page down. No coloured side border (DESIGN section 6).
const FOLD_ABOVE = 2;
const stats = useStatsStore();

const alerts = computed(() => stats.data?.alerts ?? []);
const visible = computed(() => alerts.value.length > 0 || stats.openGroups > 0);
const foldable = computed(() => alerts.value.length > FOLD_ABOVE);
const expanded = ref(false);
const showAlerts = computed(() => !foldable.value || expanded.value);
// 200 is the page cap of the groups call.
const groupCount = computed(() => (stats.openGroups === 200 ? "200+" : String(stats.openGroups)));
const headline = computed(() =>
  stats.openGroups > 0
    ? `${groupCount.value} open alert ${stats.openGroups === 1 ? "group" : "groups"}.`
    : "Needs attention.",
);
</script>

<template>
  <div v-if="visible" class="attention-banner" role="status">
    <el-icon class="attention-icon" :size="18" aria-hidden="true"><Warning /></el-icon>
    <div class="attention-body">
      <strong class="attention-head" data-testid="banner-headline">{{ headline }}</strong>
      <ul v-if="alerts.length" class="attention-list">
        <li v-if="foldable" data-testid="fold-alerts">
          {{ alerts.length }} live conditions
          <el-button link class="banner-link" :aria-expanded="expanded" @click="expanded = !expanded">{{
            expanded ? "Hide" : "Show"
          }}</el-button>
        </li>
        <template v-if="showAlerts">
          <li v-for="(alert, index) in alerts" :key="index">{{ alert.kind }}: {{ alert.message }}</li>
        </template>
      </ul>
    </div>
    <router-link class="banner-link" :to="{ name: 'alerts' }" data-testid="review-alerts">
      Review in Alerts
    </router-link>
  </div>
</template>

<style scoped>
.attention-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-warn-bg);
  color: var(--rl-fg);
}
.attention-icon {
  flex: none;
  color: var(--rl-warn);
}
.attention-body {
  flex: 1;
  min-width: 240px;
}
.attention-head {
  font-weight: 600;
}
.attention-list {
  margin: 2px 0 0;
  padding: 0;
  list-style: none;
  color: var(--rl-muted);
}
/* A link on the warn fill takes the warn colour (the primary fails contrast there) and is underlined. */
.attention-banner .banner-link {
  color: var(--rl-warn);
  font-size: inherit;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.attention-banner .banner-link:hover {
  color: var(--rl-warn);
  opacity: 0.8;
}
</style>
