<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BrandLogo from "@/components/layout/BrandLogo.vue";
import { IconCheck, IconChevronDown, IconFileText, IconLogOut, IconMoon } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { placePopup, type PopupAlign } from "@/lib/popup-place";
import { useBusy } from "@/composables/use-busy";
import { TABS } from "@/router";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";
import { useUiStore } from "@/stores/ui";

// The top bar, as drawn in the canvas (specs/shell.md): logo, the eight tabs (Jobs, Accounts and Alerts carry a count),
// the Provider scope, the theme toggle and the account menu (API docs, Sign out). One row of 64 px from 1280 px up;
// below that two rows (logo and tools, then the tabs, which scroll sideways when they do not fit). The Provider list
// and the account menu are our own popovers, not Element Plus, so every size and colour is the drawn one.
const auth = useAuthStore();
const scope = useScopeStore();
const stats = useStatsStore();
const ui = useUiStore();
const route = useRoute();
const router = useRouter();
const { busy, run } = useBusy();
const { isPhone, isTablet } = useBreakpoint();
const stacked = computed(() => isPhone.value || isTablet.value);

const initial = computed(() => (auth.user || "?").charAt(0).toUpperCase());

// Three tabs carry a count: Jobs the jobs still to finish, Accounts "usable/available" (as the stats serve them),
// Alerts the open alert groups. The count is aria-hidden, so the tab's name stays the plain label.
const accountsCount = computed(() => {
  const a = stats.data?.accounts;
  return a && a.available > 0 ? `${a.usable}/${a.available}` : 0;
});
const counts = computed<Record<string, number | string>>(() => ({
  jobs: stats.data ? stats.data.jobs.in_progress + stats.data.jobs.queued : 0,
  accounts: accountsCount.value,
  alerts: stats.openGroups,
}));
// A number above 999 reads 999+.
function countText(value: number | string): string | number {
  return typeof value === "number" && value > 999 ? "999+" : value;
}

// The Provider listbox. The store keeps "" for All (every view reads that as "no provider"); the list shows the word.
const ALL = "All";
const providerLabel = computed(() => scope.provider || ALL);
// The drawn order is dola, vibes, meta; a provider the canvas does not know follows, sorted.
const PROVIDER_ORDER = ["dola", "vibes", "meta"];
const providerOptions = computed(() => {
  const known = PROVIDER_ORDER.filter((name) => scope.providers.includes(name));
  return [ALL, ...known, ...scope.providers.filter((name) => !PROVIDER_ORDER.includes(name))];
});
function pickProvider(name: string): void {
  scope.provider = name === ALL ? "" : name;
  closePopup();
}

// One popover at a time; a click outside or Escape closes it and gives the focus back to its trigger.
const open = ref<"provider" | "account" | null>(null);
const providerWrap = ref<HTMLElement | null>(null);
const accountWrap = ref<HTMLElement | null>(null);
const wrapOf = (which: "provider" | "account") => (which === "provider" ? providerWrap.value : accountWrap.value);
const ITEMS = '[role="option"], [role="menuitem"]';

// Where an open popup sits: the app's popup rule (lib/popup-place.ts), from the trigger edge each popup is drawn on.
// Both start at the trigger, as drawn, except the Provider list on a phone, which the canvas ends at it; the account
// trigger is at the right of the header, so the rule ends its menu at the trigger at every width. The popup is first
// measured at its own size, then placed inside the window (written relative to its trigger); again on a resize.
const align = computed<Record<"provider" | "account", PopupAlign>>(() => ({
  provider: isPhone.value ? "end" : "start",
  account: "start",
}));
const popupStyle = ref<Record<string, string>>({});
async function placeOpenPopup(): Promise<void> {
  const which = open.value;
  const wrap = which ? wrapOf(which) : null;
  if (!which || !wrap) return;
  popupStyle.value = {};
  await nextTick();
  const popup = wrap.querySelector<HTMLElement>('[role="listbox"], [role="menu"]');
  if (!popup) return;
  const at = wrap.getBoundingClientRect();
  const html = document.documentElement;
  const view = { width: html.clientWidth || window.innerWidth, height: html.clientHeight || window.innerHeight };
  const place = placePopup(at, { width: popup.offsetWidth, height: popup.offsetHeight }, view, align.value[which]);
  popupStyle.value = {
    left: `${place.left - at.left}px`,
    top: `${place.top - at.top}px`,
    ...(place.maxWidth === undefined ? {} : { maxWidth: `${place.maxWidth}px` }),
    ...(place.maxHeight === undefined ? {} : { maxHeight: `${place.maxHeight}px` }),
  };
}
function onResize(): void {
  void placeOpenPopup();
}

// The keyboard moves into the popup when it opens: the chosen option, else the first item. Arrow keys, Home and End
// move between items (they stop at the ends), Escape closes and gives the focus back. The popup is placed first, so
// the focus never scrolls the page towards a popup that has not been placed yet.
async function toggle(which: "provider" | "account"): Promise<void> {
  const opening = open.value !== which;
  open.value = opening ? which : null;
  if (!opening) return;
  await placeOpenPopup();
  await nextTick();
  const items = [...(wrapOf(which)?.querySelectorAll<HTMLElement>(ITEMS) ?? [])];
  (items.find((el) => el.getAttribute("aria-selected") === "true") ?? items[0])?.focus();
}
function closePopup(): void {
  const which = open.value;
  open.value = null;
  if (which) wrapOf(which)?.querySelector<HTMLElement>("button")?.focus();
}
function onTriggerKey(event: KeyboardEvent, which: "provider" | "account"): void {
  if (event.key !== "ArrowDown" || open.value === which) return;
  event.preventDefault();
  void toggle(which);
}
function onPopupKey(event: KeyboardEvent): void {
  const items = [...(event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>(ITEMS)];
  const at = items.indexOf(document.activeElement as HTMLElement);
  const next: Record<string, number> = {
    ArrowDown: at + 1,
    ArrowUp: at - 1,
    Home: 0,
    End: items.length - 1,
  };
  const target = next[event.key];
  if (target === undefined) return;
  event.preventDefault();
  items[target]?.focus();
}
function onPointerDown(event: Event): void {
  const target = event.target as Node | null;
  if (!target || !open.value) return;
  const wrap = open.value === "provider" ? providerWrap.value : accountWrap.value;
  if (wrap && !wrap.contains(target)) open.value = null;
}
function onKeydown(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !open.value) return;
  event.preventDefault();
  closePopup();
}
onMounted(() => {
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("keydown", onKeydown);
  window.addEventListener("resize", onResize);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onPointerDown);
  document.removeEventListener("keydown", onKeydown);
  window.removeEventListener("resize", onResize);
});

async function signOut(): Promise<void> {
  closePopup();
  await run(async () => {
    await auth.logout();
    await router.replace({ name: "login" });
  });
}
function openDocs(): void {
  closePopup();
  window.open("/reference", "_blank", "noopener");
}

// The current tab stays in view when the tabs scroll sideways (tablet and phone): on a tab switch, and when a count
// arrives or changes, as the counts widen the tabs before it (they come after the bar is drawn); and once the web fonts
// have loaded, as they change the width of every tab (the bar can be drawn in the fallback font first). That late one
// is skipped when the user has moved the strip since (a wheel, a touch, a press or a key on it, or the focus entering
// it, which scrolls the focused tab into view: a scroll we start ourselves sends none of these), and when the bar is
// gone.
const navEl = ref<HTMLElement | null>(null);
let userMovedTabs = false;
let unmounted = false;
function onTabsMoved(): void {
  userMovedTabs = true;
}
async function revealCurrent(): Promise<void> {
  await nextTick();
  const current = navEl.value?.querySelector<HTMLElement>('[aria-current="page"]');
  if (current && typeof current.scrollIntoView === "function") {
    current.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}
onMounted(() => {
  void revealCurrent();
  void document.fonts?.ready.then(() => {
    if (unmounted || userMovedTabs) return;
    void revealCurrent();
  });
});
onBeforeUnmount(() => {
  unmounted = true;
});
watch([() => route.name, () => TABS.map((tab) => counts.value[tab.name]).join(" ")], revealCurrent);
</script>

<template>
  <header class="topbar" :class="{ 'topbar--stacked': stacked, 'topbar--phone': isPhone }">
    <div class="topbar__row">
      <router-link class="topbar__home" :to="{ name: 'jobs' }" aria-label="Ghidorah admin home">
        <BrandLogo />
      </router-link>

      <nav
        ref="navEl"
        class="topbar__nav"
        aria-label="Main"
        @wheel.passive="onTabsMoved"
        @touchstart.passive="onTabsMoved"
        @pointerdown="onTabsMoved"
        @keydown="onTabsMoved"
        @focusin="onTabsMoved"
      >
        <ul class="tabs" role="tablist">
          <li v-for="tab in TABS" :key="tab.name" role="presentation">
            <router-link
              class="tab"
              :class="{ 'tab--current': route.name === tab.name }"
              :to="{ name: tab.name }"
              role="tab"
              :aria-selected="route.name === tab.name"
              :aria-current="route.name === tab.name ? 'page' : undefined"
            >
              <span class="tab-label">{{ tab.label }}</span>
              <span
                v-if="counts[tab.name]"
                class="tab-count num"
                :class="{
                  'tab-count--current': route.name === tab.name && tab.name !== 'alerts',
                  'tab-count--warn': tab.name === 'alerts',
                }"
                :data-testid="`tab-count-${tab.name}`"
                aria-hidden="true"
                >{{ countText(counts[tab.name] ?? 0) }}</span
              >
            </router-link>
          </li>
        </ul>
      </nav>

      <div class="topbar__tools">
        <div ref="providerWrap" class="pop pop--provider">
          <button
            type="button"
            class="scope-trigger"
            :class="{ 'is-open': open === 'provider' }"
            aria-haspopup="listbox"
            :aria-expanded="open === 'provider'"
            :aria-label="isPhone ? `Provider scope: ${providerLabel}` : undefined"
            data-testid="provider-filter"
            @click="toggle('provider')"
            @keydown="onTriggerKey($event, 'provider')"
          >
            <span class="scope-label">Provider</span>
            <span class="scope-value">{{ providerLabel }}</span>
            <IconChevronDown :size="14" :stroke="2" />
          </button>
          <div
            v-if="open === 'provider'"
            class="menu menu--provider"
            :style="popupStyle"
            role="listbox"
            aria-label="Provider"
            @keydown="onPopupKey"
          >
            <button
              v-for="name in providerOptions"
              :key="name"
              type="button"
              class="menu-item"
              :class="{ 'is-selected': name === providerLabel }"
              role="option"
              tabindex="-1"
              :aria-selected="name === providerLabel"
              @click="pickProvider(name)"
            >
              <span class="menu-item__text">{{ name }}</span>
              <IconCheck v-if="name === providerLabel" class="menu-item__check" :size="16" :stroke="2" />
            </button>
          </div>
        </div>

        <button
          type="button"
          class="theme-toggle"
          aria-label="Switch theme"
          data-testid="theme-toggle"
          @click="ui.toggleTheme()"
        >
          <IconMoon :size="isPhone ? 18 : 16" />
        </button>

        <div ref="accountWrap" class="pop pop--account">
          <button
            type="button"
            class="account-button"
            :class="{ 'is-open': open === 'account' }"
            :aria-label="`Account menu, ${auth.user}`"
            aria-haspopup="menu"
            :aria-expanded="open === 'account'"
            :disabled="busy"
            data-testid="account-menu"
            @click="toggle('account')"
            @keydown="onTriggerKey($event, 'account')"
          >
            <span class="account-avatar" aria-hidden="true">{{ initial }}</span>
            <span v-if="!stacked" class="account-name" data-testid="whoami">{{ auth.user }}</span>
            <IconChevronDown v-if="!stacked" class="account-chevron" :size="14" :stroke="2" />
          </button>
          <div
            v-if="open === 'account'"
            class="menu menu--account"
            :style="popupStyle"
            role="menu"
            aria-label="Account"
            @keydown="onPopupKey"
          >
            <button
              type="button"
              class="menu-item"
              role="menuitem"
              tabindex="-1"
              data-testid="api-docs"
              @click="openDocs"
            >
              <IconFileText class="menu-item__icon" :size="16" />
              <span class="menu-item__text">API docs</span>
            </button>
            <div class="menu-separator" role="separator"></div>
            <button
              type="button"
              class="menu-item"
              role="menuitem"
              tabindex="-1"
              data-testid="sign-out"
              @click="signOut"
            >
              <IconLogOut class="menu-item__icon" :size="16" />
              <span class="menu-item__text">Sign out</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  --ctl: 34px;
  --tab-height: 64px;
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--rl-topbar);
  border-bottom: 1px solid var(--rl-line);
}
/* Desktop: one row, 1360 px at most, 28 px of padding, 64 px high. */
.topbar__row {
  box-sizing: border-box;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 28px;
  max-width: 1360px;
  min-height: 64px;
  margin: 0 auto;
  padding: 0 28px;
}
.topbar__home {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
}
.topbar__nav {
  flex: 1 1 560px;
  min-width: 0;
  overflow-x: auto;
  /* the tab row scrolls without a bar, as drawn (on a desktop browser narrower than the tabs a bar would add 15 px
     under them): touch, trackpad and shift-wheel still scroll it, and the current tab is kept in view */
  scrollbar-width: none;
}
.topbar__nav::-webkit-scrollbar {
  display: none;
}
.tabs {
  display: flex;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
  white-space: nowrap;
}
.tab {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 6px;
  height: var(--tab-height);
  padding: 0 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--rl-muted);
  text-decoration: none;
  border-bottom: 2px solid transparent;
}
.tab:hover {
  color: var(--rl-fg);
}
.tab--current,
.tab--current:hover {
  font-weight: 600;
  color: var(--rl-fg);
  border-bottom-color: var(--rl-primary);
}
.tab-count {
  padding: 0 6px;
  font-size: 11px;
  line-height: 16px;
  border-radius: 8px;
  color: var(--rl-muted);
  background: var(--rl-neutral-bg);
}
.tab-count--warn {
  color: var(--rl-warn);
  background: var(--rl-warn-bg);
}
.tab-count--current {
  color: var(--rl-on-primary);
  background: var(--rl-primary);
}

.topbar__tools {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pop {
  position: relative;
}

/* Provider trigger. */
.scope-trigger {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: var(--ctl);
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  cursor: pointer;
}
.scope-trigger.is-open {
  background: var(--rl-raised);
}
.scope-label {
  color: var(--rl-muted);
}

/* Theme toggle. */
.theme-toggle {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  width: var(--ctl);
  height: var(--ctl);
  padding: 1px 6px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.theme-toggle:hover {
  background: var(--rl-raised);
}

/* Account trigger. */
.account-button {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--ctl);
  padding: 0 8px 0 4px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  cursor: pointer;
}
.account-button.is-open {
  background: var(--rl-raised);
}
.account-button:disabled {
  cursor: progress;
  opacity: 0.6;
}
.account-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 14px;
  background: var(--rl-primary-soft);
  color: var(--rl-primary);
  font-size: 12px;
  font-weight: 600;
}
.account-chevron {
  flex: none;
}

/* Popovers. */
/* placed by the popup rule once measured; this is where it is measured, under the trigger's left edge */
.menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 2;
  overflow-y: auto;
  box-sizing: border-box;
  padding: 6px;
  border: 1px solid var(--rl-line);
  border-radius: 12px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-pop);
}
.menu--account {
  width: 200px;
}
.menu--provider {
  width: 180px;
}
.menu-item {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  text-align: left;
  cursor: pointer;
}
.menu-item:hover {
  background: var(--rl-raised);
}
.menu-item.is-selected {
  background: var(--rl-primary-soft);
  font-weight: 600;
}
.menu-item__text {
  flex: 1;
}
.menu-item__icon {
  flex: none;
  color: var(--rl-muted);
}
.menu-item__check {
  flex: none;
  color: var(--rl-primary);
}
.menu-separator {
  height: 1px;
  margin: 6px 4px;
  background: var(--rl-line);
}

/* Below 1280 px: row 1 is the logo and the tools, row 2 the tabs (own top border). */
.topbar--stacked .topbar__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "home tools"
    "nav nav";
  gap: 0 8px;
  max-width: none;
  min-height: 0;
  margin: 0;
  padding: 0;
}
.topbar--stacked .topbar__home {
  grid-area: home;
  min-width: 0;
  height: 60px;
  padding-left: 24px;
}
.topbar--stacked .topbar__tools {
  grid-area: tools;
  padding-right: 16px;
}
.topbar--stacked .topbar__nav {
  grid-area: nav;
  border-top: 1px solid var(--rl-line);
}
.topbar--stacked .tabs {
  padding: 0 14px;
}
.topbar--stacked {
  --ctl: 36px;
  --tab-height: 48px;
}
/* the browser's own button padding, as the canvas draws it: it squeezes the 30 px avatar to 28 x 30 in the 40 px
   button (a phone's 44 px button leaves its 32 px avatar whole) */
.topbar--stacked .account-button {
  width: 40px;
  height: 40px;
  padding: 1px 6px;
  justify-content: center;
}
.topbar--stacked .account-avatar {
  width: 30px;
  height: 30px;
  border-radius: 15px;
}
.topbar--stacked .menu--account {
  width: 220px;
}
.topbar--stacked .menu--provider {
  width: 200px;
}
.topbar--stacked .menu-item {
  height: 40px;
  font-size: 14px;
}

/* Phone: 56 px brand row, 44 px tabs, 44 px controls. */
.topbar--phone {
  --ctl: 44px;
  --tab-height: 44px;
}
.topbar--phone .topbar__home {
  height: 56px;
  padding-left: 16px;
}
.topbar--phone .topbar__tools {
  padding-right: 8px;
}
.topbar--phone .tabs {
  padding: 0 8px;
}
.topbar--phone .scope-trigger {
  gap: 4px;
  padding: 0 10px;
}
.topbar--phone .theme-toggle {
  border: 0;
}
.topbar--phone .account-button {
  width: 44px;
  height: 44px;
}
.topbar--phone .account-avatar {
  width: 32px;
  height: 32px;
  border-radius: 16px;
  font-size: 13px;
}
.topbar--phone .menu-item {
  height: 44px;
}
</style>
