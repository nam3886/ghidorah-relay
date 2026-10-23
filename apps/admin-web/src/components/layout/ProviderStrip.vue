<script setup lang="ts">
import { computed } from "vue";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { byProviderOrder, providerClass } from "@/lib/providers";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";

// The provider strip under the top bar: THE provider filter of the page that shows it (the top bar's Provider list is
// hidden there). "All", then every registered provider, "paused" when it takes no new job (`<provider>.claims_enabled`
// off). No counts: the Jobs today table shows running, done and failed. It sets the shared scope store. Until the
// summary arrives the catalog's providers are drawn.
const scope = useScopeStore();
const summary = useProvidersStore();
const { isPhone, isTablet } = useBreakpoint();

const tabs = computed<Array<{ name: string; paused: boolean }>>(() => {
  const rows = summary.data?.providers;
  if (!rows) return byProviderOrder(scope.providers).map((name) => ({ name, paused: false }));
  const paused = new Map(rows.map((p) => [p.provider, !p.claims_enabled]));
  return byProviderOrder([...paused.keys()]).map((name) => ({ name, paused: paused.get(name)! }));
});
</script>

<template>
  <nav class="pstrip" :class="{ 'pstrip--tablet': isTablet, 'pstrip--phone': isPhone }" aria-label="Provider">
    <div class="pstrip__row">
      <button
        type="button"
        class="ptab"
        :class="{ 'is-on': scope.provider === '' }"
        :aria-pressed="scope.provider === ''"
        aria-label="All providers"
        data-testid="provider-tab-all"
        @click="scope.provider = ''"
      >
        All
      </button>
      <button
        v-for="t in tabs"
        :key="t.name"
        type="button"
        class="ptab"
        :class="[providerClass(t.name), { 'is-on': scope.provider === t.name }]"
        :aria-pressed="scope.provider === t.name"
        :aria-label="t.paused ? `${t.name}, paused` : t.name"
        :data-testid="`provider-tab-${t.name}`"
        @click="scope.provider = t.name"
      >
        <ProviderTile :provider="t.name" />
        {{ t.name }}
        <span v-if="t.paused" class="ptab__paused">paused</span>
      </button>
    </div>
  </nav>
</template>

<style scoped>
.pstrip {
  flex: none;
  background: var(--rl-topbar);
  border-bottom: 1px solid var(--rl-line);
}
/* the row lines up with the page frame (AppShell: 1360 px at most, 28 px in; 24 on a tablet, 16 on a phone) */
.pstrip__row {
  box-sizing: border-box;
  display: flex;
  gap: 4px;
  max-width: 1360px;
  margin: 0 auto;
  padding: 0 28px;
  overflow-x: auto;
  scrollbar-width: none;
}
.pstrip--tablet .pstrip__row {
  max-width: none;
  padding: 0 24px;
}
.pstrip--phone .pstrip__row {
  max-width: none;
  padding: 0 16px;
}
.ptab {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  padding: 12px 14px 9px;
  border: 0;
  border-bottom: 3px solid transparent;
  background: none;
  color: var(--rl-muted);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
}
.pstrip--phone .ptab {
  min-height: 44px;
  padding: 10px 10px 7px;
}
.ptab:hover {
  color: var(--rl-fg);
}
/* the chosen tab: its ink, an underline in its tone and its tint behind; "All" in the text colour */
.ptab.is-on {
  border-bottom-color: var(--pc, var(--rl-fg));
  background: var(--ps, var(--rl-raised));
  color: var(--pi, var(--rl-fg));
  font-weight: 600;
}
.ptab__paused {
  font-size: 11px;
  font-weight: 600;
  color: var(--rl-warn);
}
</style>
