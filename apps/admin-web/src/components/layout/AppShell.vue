<script setup lang="ts">
import { onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import AppHeader from "@/components/layout/AppHeader.vue";
import AttentionBanner from "@/components/layout/AttentionBanner.vue";
import StatsStrip from "@/components/layout/StatsStrip.vue";
import TabBar from "@/components/layout/TabBar.vue";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";

// Everything around the routed view: header, tabs, stats strip, banner. Mounted only while signed in.
const route = useRoute();
const auth = useAuthStore();
const scope = useScopeStore();
const stats = useStatsStore();

onMounted(async () => {
  try {
    await Promise.all([auth.loadUser(), scope.loadCatalog()]);
  } catch (err) {
    notify.error(errorText(err));
  }
});

// The strip is read again on every tab switch and whenever the provider filter changes.
watch(
  () => [route.name, scope.provider],
  () => void stats.refresh(),
  { immediate: true },
);
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <TabBar />
    <StatsStrip />
    <AttentionBanner />
    <main class="app-main">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}
.app-main {
  padding: 12px 16px 24px;
}
</style>
