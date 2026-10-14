<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useStatsStore } from "@/stores/stats";

// One calm banner: the live alert conditions (they vanish by themselves) and the count of open alert groups.
const stats = useStatsStore();
const router = useRouter();

const alerts = computed(() => stats.data?.alerts ?? []);
const visible = computed(() => alerts.value.length > 0 || stats.openGroups > 0);
// 200 is the page cap of the groups call.
const openGroupsText = computed(() => `${stats.openGroups === 200 ? "200+" : stats.openGroups} open alert group(s)`);
</script>

<template>
  <el-alert v-if="visible" class="attention-banner" type="warning" :closable="false" show-icon title="Needs attention">
    <ul class="attention-list">
      <li v-for="(alert, index) in alerts" :key="index">
        <strong>{{ alert.kind }}</strong
        >: {{ alert.message }}
      </li>
      <li v-if="stats.openGroups > 0" data-testid="open-groups">
        {{ openGroupsText }}
        <el-button link type="primary" size="small" @click="router.push({ name: 'alerts' })"
          >Open the Alerts tab</el-button
        >
      </li>
    </ul>
  </el-alert>
</template>

<style scoped>
.attention-banner {
  margin: 12px 16px 0;
  width: auto;
}
.attention-list {
  margin: 4px 0 0;
  padding-left: 18px;
}
</style>
