<script setup lang="ts">
import type { ScrollbarInstance } from "element-plus";
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import BottomNav from "@/components/layout/BottomNav.vue";
import ProviderStrip from "@/components/layout/ProviderStrip.vue";
import TopBar from "@/components/layout/TopBar.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";

// Everything around the routed view: the top bar, the provider strip (a route with meta providerStrip), the page frame
// (Ledger: centred, 1360 px at most, padding 28 px 32 px 48 px and 24 px gaps; 24 px all round and 20 px gaps on a
// tablet; 14 px 16 px 20 px and 14 px gaps on a phone) and, below the desktop, the bottom bar (BottomNav) with the
// tabs. Mounted only while signed in.
// On a tablet and a desktop the page scrolls in its own area under the bar and the strip (the style block below); on a
// phone the window scrolls, as the bar sticks.
const route = useRoute();
const auth = useAuthStore();
const scope = useScopeStore();
const stats = useStatsStore();
const { isPhone, isTablet } = useBreakpoint();
const scroller = ref<ScrollbarInstance | null>(null);
const main = ref<HTMLElement | null>(null);

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

// The scroll area takes the keyboard (Space, PageDown, Home, End) once it has focus, so focus is moved to it, the usual
// move of a single-page app. A tab link in the bar is not page content: after a click on a tab the page scrolls by key.
// A field the user is typing in keeps its focus.
function focusPage(): void {
  scroller.value?.wrapRef?.focus({ preventScroll: true });
}
function showTop(): void {
  scroller.value?.setScrollTop(0);
  if (!main.value?.contains(document.activeElement)) focusPage();
}
// The element that takes focus is named for a screen reader: a focusable region is announced as one. Element Plus puts
// no role or label on its wrap.
onMounted(() => {
  const wrap = scroller.value?.wrapRef;
  wrap?.setAttribute("role", "region");
  wrap?.setAttribute("aria-label", "Page content");
  showTop();
});
watch(() => route.name, showTop, { flush: "post" });
defineExpose({ focusPage });
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--bnav': isPhone || isTablet }">
    <TopBar />
    <ProviderStrip
      v-if="route.meta.providerStrip && (!route.meta.stripCompactOnly || isPhone || isTablet)"
      :mode="route.meta.providerStrip"
    />
    <el-scrollbar
      ref="scroller"
      class="app-scroll"
      wrap-class="app-scroll__wrap"
      view-class="app-scroll__view"
      always
      tabindex="-1"
    >
      <main ref="main" class="app-main" :class="{ 'app-main--tablet': isTablet, 'app-main--phone': isPhone }">
        <slot />
      </main>
    </el-scrollbar>
    <BottomNav v-if="isPhone || isTablet" :phone="isPhone" />
  </div>
</template>

<style scoped>
.app-main {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 24px;
  box-sizing: border-box;
  width: 100%;
  max-width: 1360px;
  min-width: 0;
  margin: 0 auto;
  padding: 28px 32px 48px;
}
.app-main--tablet {
  max-width: none;
  gap: 20px;
  padding: 24px;
}
.app-main--phone {
  max-width: none;
  gap: 14px;
  padding: 14px 16px 20px;
}
</style>

<!-- Not scoped: the scroll area's wrap and bars belong to Element Plus. Every selector starts with `.app-shell`, so it
     outranks Element Plus's one-class rules whatever the order of the sheets. -->
<style>
/* the frame is as high as the window; the bar keeps its height and the scroll area takes the rest */
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}
.app-shell > .topbar,
.app-shell > .pstrip,
.app-shell > .bnav {
  flex: none;
}
/* what a page's own bar fixed to the foot of the window (the Jobs bulk bar on a phone) sits on: the bottom bar */
.app-shell--bnav {
  --rl-bottom-bar: max(64px, calc(53px + env(safe-area-inset-bottom)));
}
/* the page's bar is the global scrollbar of the Ledger: 8 px with a 1 px clear edge, the thumb #d0d4de (#3a3d50 dark),
   darker under the pointer, always drawn, no track; it overlays the page, so the top bar and the content share one
   axis */
.app-shell .el-scrollbar.app-scroll {
  --el-scrollbar-opacity: 1;
  --el-scrollbar-bg-color: var(--rl-scroll-thumb);
  --el-scrollbar-hover-opacity: 1;
  --el-scrollbar-hover-bg-color: var(--rl-scroll-thumb-hover);
  flex: 1 1 0;
  min-height: 0;
}
.app-shell .app-scroll .el-scrollbar__bar.is-vertical {
  top: 0;
  right: 0;
  bottom: 0;
  width: 8px;
}
.app-shell .app-scroll .el-scrollbar__bar.is-horizontal {
  display: none;
}
.app-shell .app-scroll .el-scrollbar__thumb {
  box-sizing: border-box;
  border: 1px solid transparent;
  border-radius: 8px;
  background-clip: padding-box;
}
/* the app moves focus here by script on load and on a tab change, so the keys scroll the page; Chrome counts that as
   :focus-visible, and the page area draws no ring in any focus state (the global ring stays for controls) */
.app-shell .app-scroll__wrap:focus {
  outline: none;
}
/* positioned, so an out-of-flow element of a page (the Settings screen-reader line) belongs to the scroll area; else
   its containing block is the clipping root, which would get a scroll range of its own that scripts can move */
.app-shell .app-scroll__view {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

/* One block, no scroll area: a phone (the window scrolls under the sticky bar), print, and the test harness's whole
   page photos (print media). Overflow stays visible all the way up, or the scroll area would become the scroller of
   a sticky element inside the page (the Settings sub-navigation). */
@media (max-width: 767px), print {
  .app-shell {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }
  .app-shell .app-scroll.el-scrollbar {
    display: flex;
    flex: 1 0 auto;
    flex-direction: column;
    height: auto;
    overflow: visible;
  }
  .app-shell .app-scroll__wrap {
    height: auto;
    overflow: visible;
  }
  .app-shell .app-scroll__view {
    flex: 1 0 auto;
    min-height: 0;
  }
  .app-shell .app-scroll .el-scrollbar__bar {
    display: none;
  }
}
</style>
