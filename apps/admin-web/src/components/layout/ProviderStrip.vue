<script setup lang="ts">
import { computed, onMounted } from "vue";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { byProviderOrder, providerClass } from "@/lib/providers";
import type { StripMode } from "@/router";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";

// The provider strip under the top bar: THE provider filter of the page that shows it (the top bar's Provider list is
// hidden there). Every registered provider, "paused" when it takes no new job (`<provider>.claims_enabled` off). No
// counts: the Jobs today table shows running, done and failed. Its mode is the route's (meta providerStrip):
// - "all": "All" first; it sets the shared filter `provider` (Jobs, Accounts, API keys, Request logs, Alerts).
// - "only": the providers alone, for a page that works on one at a time (Prompt frames, Test); it sets `only`, and is
//   off while the page holds `stripLocked` (a write in flight).
// Until the summary arrives the catalog's providers are drawn; a page that does not read the summary itself has the
// strip read it.
const props = withDefaults(defineProps<{ mode?: StripMode }>(), { mode: "all" });
const scope = useScopeStore();
const summary = useProvidersStore();
const { isPhone, isTablet } = useBreakpoint();

onMounted(() => {
  if (!summary.data) void summary.refresh();
});

const tabs = computed<Array<{ name: string; paused: boolean }>>(() => {
  const rows = summary.data?.providers;
  if (!rows) return byProviderOrder(scope.providers).map((name) => ({ name, paused: false }));
  const paused = new Map(rows.map((p) => [p.provider, !p.claims_enabled]));
  return byProviderOrder([...paused.keys()]).map((name) => ({ name, paused: paused.get(name)! }));
});
const onAll = computed(() => scope.provider === "");
const isOn = (name: string): boolean => (props.mode === "only" ? scope.only : scope.provider) === name;
function choose(name: string): void {
  if (props.mode === "only") {
    scope.only = name;
    return;
  }
  scope.provider = name;
}
</script>

<template>
  <nav
    class="pstrip"
    :class="{ 'pstrip--tablet': isTablet, 'pstrip--phone': isPhone }"
    aria-label="Provider"
    :data-mode="mode"
  >
    <div class="pstrip__row">
      <ArrowScroller label="Provider">
        <button
          v-if="mode !== 'only'"
          type="button"
          class="ptab"
          :class="{ 'is-on': onAll }"
          :aria-pressed="onAll"
          aria-label="All providers"
          data-testid="provider-tab-all"
          @click="choose('')"
        >
          All
        </button>
        <button
          v-for="t in tabs"
          :key="t.name"
          type="button"
          class="ptab"
          :class="[providerClass(t.name), { 'is-on': isOn(t.name) }]"
          :aria-pressed="isOn(t.name)"
          :aria-label="t.paused ? `${t.name}, paused` : t.name"
          :disabled="mode === 'only' && scope.stripLocked"
          :data-testid="`provider-tab-${t.name}`"
          @click="choose(t.name)"
        >
          <ProviderTile :provider="t.name" />
          {{ t.name }}
          <span v-if="t.paused" class="ptab__paused">paused</span>
        </button>
      </ArrowScroller>
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
}
/* the tabs sit on the strip's bottom line: the scroller keeps no padding of its own here */
.pstrip__row :deep(.asc__row) {
  gap: 4px;
  padding: 0;
  align-items: stretch;
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
.ptab:hover:not(:disabled) {
  color: var(--rl-fg);
}
/* the chosen tab: its ink, an underline in its tone and its tint behind; "All" in the text colour */
.ptab:disabled {
  cursor: default;
  opacity: 0.6;
}
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
