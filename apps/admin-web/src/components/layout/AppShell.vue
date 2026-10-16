<script setup lang="ts">
import { onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import TopBar from "@/components/layout/TopBar.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";

// Everything around the routed view: the top bar and the page frame (centred, 1360 px at most, padding 28
// px, 24 px on a tablet, 16 px on a phone). Mounted only while signed in.
const route = useRoute();
const auth = useAuthStore();
const scope = useScopeStore();
const stats = useStatsStore();
const { isPhone, isTablet } = useBreakpoint();

onMounted(async () => {
  try {
    await Promise.all([auth.loadUser(), scope.loadCatalog()]);
  } catch (err) {
    notify.error(errorText(err));
  }
});

// The numbers behind the tab counts and the banner are read again on every tab switch and whenever the provider
// filter changes.
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
    <TopBar />
    <main class="app-main" :class="{ 'app-main--tablet': isTablet, 'app-main--phone': isPhone }">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}
.app-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 20px;
  box-sizing: border-box;
  width: 100%;
  max-width: 1360px;
  min-width: 0;
  margin: 0 auto;
  padding: 28px 28px 48px;
}
.app-main--tablet {
  max-width: none;
  padding: 24px 24px 40px;
}
.app-main--phone {
  max-width: none;
  gap: 16px;
  padding: 16px 16px 24px;
}
</style>
