<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { useStatsStore } from "@/stores/stats";

// One calm banner: the live alert conditions (they vanish by themselves) and the count of open alert groups.
// More than two conditions fold behind one line with a count, so the banner never pushes the page down.
const FOLD_ABOVE = 2;
const stats = useStatsStore();
const router = useRouter();

const alerts = computed(() => stats.data?.alerts ?? []);
const visible = computed(() => alerts.value.length > 0 || stats.openGroups > 0);
const foldable = computed(() => alerts.value.length > FOLD_ABOVE);
const expanded = ref(false);
const showAlerts = computed(() => !foldable.value || expanded.value);
// 200 is the page cap of the groups call.
const openGroupsText = computed(() => `${stats.openGroups === 200 ? "200+" : stats.openGroups} open alert group(s)`);
</script>

<template>
  <el-alert v-if="visible" class="attention-banner" type="warning" :closable="false" show-icon title="Needs attention">
    <ul class="attention-list">
      <li v-if="foldable" data-testid="fold-alerts">
        {{ alerts.length }} live conditions
        <el-button link class="banner-link" :aria-expanded="expanded" @click="expanded = !expanded">{{
          expanded ? "Hide" : "Show"
        }}</el-button>
      </li>
      <template v-if="showAlerts">
        <li v-for="(alert, index) in alerts" :key="index">
          <strong>{{ alert.kind }}</strong
          >: {{ alert.message }}
        </li>
      </template>
      <li v-if="stats.openGroups > 0" data-testid="open-groups">
        {{ openGroupsText }}
        <el-button link class="banner-link" @click="router.push({ name: 'alerts' })">Open the Alerts tab</el-button>
      </li>
    </ul>
  </el-alert>
</template>

<style scoped>
.attention-banner {
  align-items: flex-start;
  margin: 12px 16px 0;
  padding: 12px 16px;
  width: auto;
  color: var(--rl-warn);
  background: var(--rl-warn-bg);
  border: 1px solid color-mix(in srgb, var(--rl-warn) 30%, transparent);
  border-left: 3px solid var(--rl-warn);
}
.attention-banner :deep(.el-alert__icon) {
  margin-top: 1px;
}
.attention-banner :deep(.el-alert__title) {
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
}
.attention-list {
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
  line-height: 20px;
}
/* A link on the warn fill takes the warn colour (the accent fails contrast there) and is underlined. */
.attention-banner .banner-link {
  color: var(--rl-warn);
  font-size: inherit;
  font-weight: 600;
  text-decoration: underline;
}
.attention-banner .banner-link:hover,
.attention-banner .banner-link:focus-visible {
  color: var(--rl-warn);
  opacity: 0.8;
}
</style>
