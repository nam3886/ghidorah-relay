<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
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

// The strip counts jobs and accounts, so it belongs to those two tabs; the banner is global.
const STRIP_ROUTES = new Set(["jobs", "accounts"]);
const showStrip = computed(() => STRIP_ROUTES.has(String(route.name)));

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

// A toast belongs to the tab that raised it: the next tab starts without the old ones.
watch(
  () => route.name,
  () => notify.clear(),
);
</script>

<template>
  <div class="app-shell">
    <div class="app-chrome">
      <AppHeader />
      <TabBar />
    </div>
    <StatsStrip v-if="showStrip" />
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
/* The header and the tabs stay in view on a long page. */
.app-chrome {
  position: sticky;
  top: 0;
  z-index: 10;
}
.app-main {
  padding: 12px 16px 24px;
  min-width: 0;
}
@media (max-width: 639px) {
  .app-main {
    padding: 8px 8px 16px;
  }
}
</style>
