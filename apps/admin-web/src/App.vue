<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import JobDetailDialog from "@/components/jobs/JobDetailDialog.vue";
import AppShell from "@/components/layout/AppShell.vue";

const route = useRoute();
// Before the first navigation ends nothing is matched: draw nothing, so the shell never starts for a signed-out user.
const ready = computed(() => route.matched.length > 0);

// Toasts: one stack, capped, identical messages merged.
const messageConfig = { max: 4, grouping: true, duration: 2500 };
</script>

<template>
  <el-config-provider size="small" :message="messageConfig">
    <template v-if="ready">
      <router-view v-if="route.meta.public" />
      <template v-else>
        <AppShell>
          <router-view v-slot="{ Component }">
            <!-- The Test tab stays alive so a running job, the loaded models and the form survive tab switches. -->
            <keep-alive include="TestView">
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </AppShell>
        <JobDetailDialog />
      </template>
    </template>
  </el-config-provider>
</template>
