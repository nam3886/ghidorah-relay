<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useNavCounts } from "@/components/layout/nav-counts";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { NAV_ICONS, type NavIconName } from "@/components/shared/icons/ledger-icons";
import BottomSheet from "@/components/shared/overlays/BottomSheet.vue";
import { TABS } from "@/router";

// The bottom bar of a tablet and a phone (JobsTablet `tabbar`): sticky at the foot of the page above the safe area,
// the surface under a hairline, 64 px at least. A tablet shows all eight tabs; a phone shows Jobs, Accounts, Logs and
// Alerts, and More opens a bottom sheet with the rest (More is the current tab while one of those is open). Each tab is
// a 22 px glyph over an 11 px label (its short name; the accessible name is the full one); the current one is in the
// brand ink with a 2 px brand line along its top. Badges as in the top bar: Accounts usable/total, Alerts the count.
const props = defineProps<{ phone: boolean }>();
const route = useRoute();
const { counts, countText } = useNavCounts();

const SHORT: Record<string, string> = { keys: "Keys", logs: "Logs", frames: "Frames" };
const PHONE_TABS = ["jobs", "accounts", "logs", "alerts"];
const tabs = computed(() =>
  TABS.filter((t) => !props.phone || PHONE_TABS.includes(t.name)).map((t) => ({
    name: t.name,
    label: t.label,
    short: SHORT[t.name] ?? t.label,
    icon: t.name as NavIconName,
  })),
);
const more = computed(() => TABS.filter((t) => !PHONE_TABS.includes(t.name)));
const inMore = computed(() => props.phone && more.value.some((t) => t.name === route.name));

const sheet = ref(false);
watch(
  () => route.name,
  () => (sheet.value = false),
);
</script>

<template>
  <nav class="bnav" :class="{ 'bnav--phone': props.phone }" aria-label="Main">
    <ul class="bnav__list" role="tablist">
      <li v-for="tab in tabs" :key="tab.name" role="presentation">
        <router-link
          class="bnav__tab"
          :class="{ 'is-current': route.name === tab.name }"
          :to="{ name: tab.name }"
          role="tab"
          :aria-label="tab.label"
          :aria-selected="route.name === tab.name"
          :aria-current="route.name === tab.name ? 'page' : undefined"
        >
          <span class="bnav__glyph">
            <PathIcon :paths="NAV_ICONS[tab.icon]" :size="22" :stroke="2" />
            <span
              v-if="counts[tab.name]"
              class="bnav__badge num"
              :class="tab.name === 'alerts' ? 'is-warn' : 'is-brand'"
              :data-testid="`bnav-count-${tab.name}`"
              aria-hidden="true"
              >{{ countText(counts[tab.name] ?? 0) }}</span
            >
          </span>
          <span class="bnav__label">{{ tab.short }}</span>
        </router-link>
      </li>
      <li v-if="props.phone" role="presentation">
        <button
          type="button"
          class="bnav__tab"
          :class="{ 'is-current': inMore }"
          role="tab"
          aria-label="More"
          aria-haspopup="dialog"
          :aria-selected="inMore"
          :aria-expanded="sheet"
          data-testid="bnav-more"
          @click="sheet = true"
        >
          <span class="bnav__glyph"><PathIcon :paths="NAV_ICONS.more" :size="22" :stroke="2.4" /></span>
          <span class="bnav__label">More</span>
        </button>
      </li>
    </ul>
  </nav>
  <BottomSheet v-if="props.phone" v-model="sheet" title="More" testid="bnav-sheet">
    <ul class="bnav-more">
      <li v-for="tab in more" :key="tab.name">
        <router-link
          class="bnav-more__item"
          :class="{ 'is-current': route.name === tab.name }"
          :to="{ name: tab.name }"
          :aria-current="route.name === tab.name ? 'page' : undefined"
          @click="sheet = false"
        >
          <PathIcon :paths="NAV_ICONS[tab.name as NavIconName]" :size="22" :stroke="2" />
          {{ tab.label }}
        </router-link>
      </li>
    </ul>
  </BottomSheet>
</template>

<style scoped>
.bnav {
  position: sticky;
  bottom: 0;
  z-index: 10;
  flex: none;
  margin-top: auto;
  box-sizing: border-box;
  min-height: 64px;
  padding: 0 4px max(4px, env(safe-area-inset-bottom));
  background: var(--rl-surface);
  border-top: 1px solid var(--rl-line);
}
.bnav__list {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  margin: 0;
  padding: 0;
  list-style: none;
}
.bnav--phone .bnav__list {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.bnav__tab {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  width: 100%;
  min-height: 52px;
  height: 100%;
  padding: 6px 0 0;
  border: 0;
  background: transparent;
  font: inherit;
  font-size: 11px;
  line-height: 14px;
  font-weight: 600;
  color: var(--rl-muted);
  text-decoration: none;
  cursor: pointer;
}
.bnav__tab.is-current {
  font-weight: 700;
  color: var(--rl-brand-ink);
  box-shadow: inset 0 2px 0 var(--rl-brand);
}
.bnav__glyph {
  position: relative;
  display: block;
  width: 22px;
  height: 22px;
}
.bnav__glyph > svg {
  display: block;
}
.bnav__label {
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.bnav__badge {
  position: absolute;
  top: -6px;
  left: 14px;
  box-sizing: border-box;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 10px;
  line-height: 18px;
  font-weight: 800;
  text-align: center;
  white-space: nowrap;
  box-shadow: 0 0 0 2px var(--rl-surface);
}
.bnav__badge.is-brand {
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.bnav__badge.is-warn {
  background: var(--rl-alert-badge-bg);
  color: var(--rl-alert-badge);
}

.bnav-more {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}
.bnav-more__item {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 48px;
  padding: 0 8px;
  border-radius: 10px;
  font-weight: 600;
  color: var(--rl-fg);
  text-decoration: none;
}
.bnav-more__item:hover {
  background: var(--rl-menu-hover);
}
.bnav-more__item.is-current {
  color: var(--rl-brand-ink);
  background: var(--rl-primary-soft);
}
</style>
