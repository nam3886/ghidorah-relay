<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import BrandLogo from "@/components/layout/BrandLogo.vue";
import { useNavCounts } from "@/components/layout/nav-counts";
import { IconChevronDown, IconFileText, IconLogOut, IconMoon } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { placePopup, type PopupAlign } from "@/lib/popup-place";
import { useBusy } from "@/composables/use-busy";
import { TABS } from "@/router";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

// The top bar of the Ledger shell (Ledger, JobsTablet, MenusToasts). Desktop (1280 px up): one 60 px row, the logo,
// the eight tabs (the current one dark with a 2 px brand underline; Accounts carries "usable/total" in a purple pill,
// Alerts the open count in an amber pill), then Live and the account menu. Tablet and phone: the tabs move to the
// bottom bar (BottomNav); the bar keeps the logo, the page title, Live (tablet) and the avatar, 60 px high on a tablet
// and 56 px on a phone. The account menu (Signed in as, API docs, Dark theme, Sign out) is our own popover.
const auth = useAuthStore();
const ui = useUiStore();
const route = useRoute();
const router = useRouter();
const { busy, run } = useBusy();
const { isPhone, isTablet } = useBreakpoint();
// below the desktop the tabs live in the bottom bar
const stacked = computed(() => isPhone.value || isTablet.value);

const initial = computed(() => (auth.user || "?").charAt(0).toUpperCase());
const title = computed(() => (route.meta.label as string | undefined) ?? "");

// The tab counts (nav-counts.ts); aria-hidden, so the tab's name stays the plain label.
const { counts, countText } = useNavCounts();

// The popover; a click outside or Escape closes it and gives the focus back to its trigger.
type Popup = "account";
const open = ref<Popup | null>(null);
const accountWrap = ref<HTMLElement | null>(null);
const wrapOf = (which: Popup) => (which === "account" ? accountWrap.value : null);
const ITEMS = '[role="menuitem"]';

// Where an open popup sits: the app's popup rule (lib/popup-place.ts), from the trigger edge it is drawn on. The
// account trigger is at the right of the header, so the rule ends its menu at the trigger at every width. The popup is
// first measured at its own size, then placed inside the window (written relative to its trigger); again on a resize.
const align: Record<Popup, PopupAlign> = { account: "start" };
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
  const place = placePopup(at, { width: popup.offsetWidth, height: popup.offsetHeight }, view, align[which]);
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

// The keyboard moves into the popup when it opens: its first item. Arrow keys, Home and End
// move between items (they stop at the ends), Escape closes and gives the focus back. The popup is placed first, so
// the focus never scrolls the page towards a popup that has not been placed yet.
async function toggle(which: Popup): Promise<void> {
  const opening = open.value !== which;
  open.value = opening ? which : null;
  if (!opening) return;
  await placeOpenPopup();
  await nextTick();
  const items = [...(wrapOf(which)?.querySelectorAll<HTMLElement>(ITEMS) ?? [])];
  items[0]?.focus();
}
function closePopup(): void {
  const which = open.value;
  open.value = null;
  if (which) wrapOf(which)?.querySelector<HTMLElement>("button")?.focus();
}
function onTriggerKey(event: KeyboardEvent, which: Popup): void {
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
  const wrap = wrapOf(open.value);
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
</script>

<template>
  <header class="topbar" :class="{ 'topbar--stacked': stacked, 'topbar--phone': isPhone }">
    <router-link class="topbar__home" :to="{ name: 'jobs' }" aria-label="Ghidorah admin home">
      <BrandLogo />
    </router-link>

    <span v-if="stacked" class="topbar__title" data-testid="page-title">{{ title }}</span>

    <nav v-else class="topbar__nav" aria-label="Main">
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
              :class="tab.name === 'alerts' ? 'tab-count--warn' : 'tab-count--brand'"
              :data-testid="`tab-count-${tab.name}`"
              aria-hidden="true"
              >{{ countText(counts[tab.name] ?? 0) }}</span
            >
          </router-link>
        </li>
      </ul>
    </nav>

    <div class="topbar__tools">
      <span v-if="!isPhone" class="live" data-testid="live"
        ><span class="live__dot" aria-hidden="true"></span>Live</span
      >

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
          <span class="menu-head" role="presentation"
            >Signed in as <b>{{ auth.user }}</b> · LAN only</span
          >
          <button
            type="button"
            class="menu-item"
            role="menuitem"
            tabindex="-1"
            data-testid="api-docs"
            @click="openDocs"
          >
            <IconFileText class="menu-item__icon" :size="16" :stroke="2" />
            <span class="menu-item__text">API docs</span>
          </button>
          <button
            type="button"
            class="menu-item"
            role="menuitem"
            tabindex="-1"
            data-testid="theme-toggle"
            @click="ui.toggleTheme()"
          >
            <IconMoon class="menu-item__icon" :size="16" :stroke="2" />
            <span class="menu-item__text">Dark theme</span>
            <span class="menu-item__hint">{{ ui.theme === "dark" ? "on" : "off" }}</span>
          </button>
          <div class="menu-separator" role="separator"></div>
          <button type="button" class="menu-item" role="menuitem" tabindex="-1" data-testid="sign-out" @click="signOut">
            <IconLogOut class="menu-item__icon" :size="16" :stroke="2" />
            <span class="menu-item__text">Sign out</span>
          </button>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
/* Desktop: one 60 px row (61 with its hairline), 32 px of padding, the surface. */
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 28px;
  min-height: 60px;
  padding: 0 32px;
  background: var(--rl-topbar);
  border-bottom: 1px solid var(--rl-line);
}
.topbar__home {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
}
.topbar__nav {
  min-width: 0;
}
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.tab {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  height: 60px;
  padding: 0 10px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--rl-muted);
  text-decoration: none;
  border-bottom: 2px solid transparent;
}
.tab:hover {
  color: var(--rl-fg);
}
.tab--current,
.tab--current:hover {
  color: var(--rl-fg);
  border-bottom-color: var(--rl-brand);
}
.tab-count {
  display: inline-flex;
  align-items: center;
  height: 20px;
  margin-left: 6px;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
}
.tab-count--brand {
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.tab-count--warn {
  background: var(--rl-alert-badge-bg);
  color: var(--rl-alert-badge);
}

.topbar__tools {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.live {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--rl-muted);
}
.live__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--rl-live);
}
.pop {
  position: relative;
}

/* The account trigger: avatar, name and chevron on a desktop (MenusToasts "Top bar · account menu"). */
.account-button {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px 0 6px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  line-height: normal;
  cursor: pointer;
}
.account-button.is-open,
.account-button:hover {
  background: var(--rl-menu-hover);
}
.account-button:disabled {
  cursor: progress;
  opacity: 0.6;
}
.account-avatar {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
  font-size: 11px;
  font-weight: 700;
}
.account-chevron {
  flex: none;
}

/* The popover: 220 px, 6 px of padding, radius 12, the menu surface with the lift. */
.menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 2;
  overflow-y: auto;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 220px;
  padding: 6px;
  border-radius: 12px;
  background: var(--rl-pop);
  box-shadow: var(--rl-shadow-pop);
}
.menu-head {
  margin-bottom: 4px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--rl-menu-rule);
  font-size: 12px;
  color: var(--rl-faint);
}
.menu-head b {
  color: var(--rl-fg);
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
  font-weight: 600;
  line-height: normal;
  text-align: left;
  cursor: pointer;
}
.menu-item:hover {
  background: var(--rl-menu-hover);
}
.menu-item__text {
  flex: 1;
}
.menu-item__icon {
  flex: none;
}
.menu-item__hint {
  margin-left: auto;
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-faint);
}
.menu-separator {
  height: 1px;
  margin: 4px 6px;
  background: var(--rl-menu-rule);
}

/* Tablet: 60 px, 24 px in from the left, 16 px from the right; the logo, the page title, Live and the avatar. */
.topbar--stacked {
  flex-wrap: nowrap;
  gap: 10px;
  height: 60px;
  min-height: 0;
  padding: 0 16px 0 24px;
}
.topbar__title {
  min-width: 0;
  overflow: hidden;
  font-size: 18px;
  line-height: 28px;
  font-weight: 800;
  letter-spacing: -0.02em;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--rl-fg);
}
.topbar--stacked .topbar__tools {
  gap: 12px;
}
/* the avatar alone: a 30 px circle in a 44 px target */
.topbar--stacked .account-button {
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
}
.topbar--stacked .account-avatar {
  width: 30px;
  height: 30px;
  font-size: 12px;
}
.topbar--stacked .menu-item {
  height: 44px;
}

/* Phone: 56 px, 16 px in from the left and 6 px from the right; the title is 20 px. */
.topbar--phone {
  height: 56px;
  padding: 0 6px 0 16px;
}
.topbar--phone .topbar__title {
  font-size: 20px;
}
</style>
