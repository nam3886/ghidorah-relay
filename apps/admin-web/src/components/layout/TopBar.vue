<script setup lang="ts">
import { ArrowDown, Moon, Sunny } from "@element-plus/icons-vue";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import BrandLogo from "@/components/layout/BrandLogo.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { TABS } from "@/router";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";
import { useUiStore } from "@/stores/ui";

// The top bar: logo, the eight tabs (Jobs and Alerts carry a count), the Provider scope, the theme toggle and the
// account menu (API docs, Sign out). One row of 64 px from 1280 px up; below that two rows (logo and tools, then the
// tabs, which scroll sideways when they do not fit).
const auth = useAuthStore();
const scope = useScopeStore();
const stats = useStatsStore();
const ui = useUiStore();
const route = useRoute();
const router = useRouter();
const { busy, run } = useBusy();
const { isPhone, isTablet } = useBreakpoint();
const stacked = computed(() => isPhone.value || isTablet.value);

const isDark = computed(() => ui.theme === "dark");
const initial = computed(() => (auth.user || "?").charAt(0).toUpperCase());

// Three tabs carry a count: Jobs the jobs still to finish, Accounts "ready / total" (usable of available, as the stats
// serve them), Alerts the open alert groups. The count is aria-hidden, so the tab's name stays the plain label.
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

function go(name: string | number): void {
  void router.push({ name: String(name) });
}

// Element Plus shows the placeholder, not the option label, for a value of "": the select holds a sentinel for All and
// the store keeps its "" (every view reads that as "no provider").
const ALL = "__all__";
const providerChoice = computed({
  get: () => scope.provider || ALL,
  set: (value: string) => {
    scope.provider = value === ALL ? "" : value;
  },
});

async function signOut(): Promise<void> {
  await run(async () => {
    await auth.logout();
    await router.replace({ name: "login" });
  });
}

function onMenu(command: string | number | object): void {
  if (command === "docs") window.open("/reference", "_blank", "noopener");
  else if (command === "signout") void signOut();
}
</script>

<template>
  <header class="topbar" :class="{ 'topbar--stacked': stacked, 'topbar--phone': isPhone }">
    <router-link class="topbar__home" :to="{ name: 'jobs' }" aria-label="Ghidorah admin home">
      <BrandLogo />
    </router-link>

    <nav class="topbar__nav" aria-label="Main">
      <el-tabs :model-value="String(route.name)" @tab-change="go">
        <el-tab-pane v-for="tab in TABS" :key="tab.name" :name="tab.name">
          <template #label>
            <span class="tab-label">{{ tab.label }}</span>
            <span
              v-if="counts[tab.name]"
              class="tab-count"
              :class="{ 'tab-count--current': route.name === tab.name, 'tab-count--warn': tab.name === 'alerts' }"
              :data-testid="`tab-count-${tab.name}`"
              aria-hidden="true"
              >{{ countText(counts[tab.name] ?? 0) }}</span
            >
          </template>
        </el-tab-pane>
      </el-tabs>
    </nav>

    <div class="topbar__tools">
      <el-select
        v-model="providerChoice"
        class="scope-select"
        :class="{ 'scope-select--active': scope.provider !== '' }"
        aria-label="Provider filter"
        data-testid="provider-filter"
      >
        <template v-if="!isPhone" #prefix>
          <span class="scope-label">Provider</span>
        </template>
        <el-option label="All" :value="ALL" />
        <el-option v-for="name in scope.providers" :key="name" :label="name" :value="name" />
      </el-select>
      <el-button
        class="tool-button"
        :icon="isDark ? Sunny : Moon"
        :title="isDark ? 'Switch to light theme' : 'Switch to dark theme'"
        :aria-label="isDark ? 'Switch to light theme' : 'Switch to dark theme'"
        data-testid="theme-toggle"
        @click="ui.toggleTheme()"
      />
      <el-dropdown trigger="click" placement="bottom-end" @command="onMenu">
        <button
          type="button"
          class="account-button"
          :aria-label="`Account menu, ${auth.user}`"
          :disabled="busy"
          data-testid="account-menu"
        >
          <span class="account-avatar" aria-hidden="true">{{ initial }}</span>
          <span v-if="!stacked" class="account-name" data-testid="whoami">{{ auth.user }}</span>
          <el-icon v-if="!stacked" class="account-chevron" :size="14" aria-hidden="true"><ArrowDown /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="docs">API docs</el-dropdown-item>
            <el-dropdown-item command="signout"><span data-testid="sign-out">Sign out</span></el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style scoped>
/* One row at 1280 px and up: logo, tabs, tools. The content is centred at 1360 px with 28 px of padding. */
.topbar {
  --tab-height: 64px;
  position: sticky;
  top: 0;
  z-index: 10;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  grid-template-areas: "home nav tools";
  grid-template-rows: var(--tab-height);
  align-items: center;
  column-gap: 24px;
  padding-inline: max(28px, calc((100% - 1360px) / 2 + 28px));
  background: var(--rl-topbar);
  border-bottom: 1px solid var(--rl-line);
}
.topbar__home {
  grid-area: home;
  display: flex;
  align-items: center;
  text-decoration: none;
}
.topbar__nav {
  grid-area: nav;
  min-width: 0;
}
.topbar__tools {
  grid-area: tools;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Below 1280 px: row 1 is the logo and the tools (60 px, 56 px on a phone), row 2 the tabs (48 px, 44 px). */
.topbar--stacked {
  --tab-height: 48px;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "home tools"
    "nav nav";
  grid-template-rows: 60px auto;
  column-gap: 8px;
  padding-inline: 0;
}
.topbar--stacked .topbar__home {
  padding-left: 24px;
}
.topbar--stacked .topbar__tools {
  padding-right: 16px;
}
.topbar--stacked .topbar__nav {
  padding: 0 14px;
  border-top: 1px solid var(--rl-line);
}
.topbar--phone {
  --tab-height: 44px;
  grid-template-rows: 56px auto;
}
.topbar--phone .topbar__home {
  padding-left: 16px;
}
.topbar--phone .topbar__tools {
  gap: 0;
  padding-right: 8px;
}
.topbar--phone .topbar__nav {
  padding: 0 8px;
}

/* Tabs: the Element Plus tabs, drawn as the design has them. */
.topbar__nav :deep(.el-tabs) {
  --el-tabs-header-height: var(--tab-height);
}
.topbar__nav :deep(.el-tabs__header) {
  margin: 0;
}
.topbar__nav :deep(.el-tabs__nav-wrap::after) {
  display: none;
}
.topbar__nav :deep(.el-tabs__item) {
  padding: 0 10px;
  margin-right: 4px;
  font-size: 14px;
}
.tab-label {
  white-space: nowrap;
}
.tab-count {
  margin-left: 6px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 16px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  border-radius: 8px;
  color: var(--rl-muted);
  background: var(--rl-neutral-bg);
}
.tab-count--warn {
  color: var(--rl-warn);
  background: var(--rl-warn-bg);
}
.tab-count--current {
  color: #ffffff;
  background: var(--rl-primary);
}

/* Tools. */
.scope-select {
  width: 160px;
}
.topbar--stacked .scope-select {
  width: 160px;
}
.topbar--phone .scope-select {
  width: 92px;
}
.scope-label {
  color: var(--rl-muted);
}
.scope-select--active :deep(.el-select__wrapper) {
  background: var(--rl-primary-soft);
}
.tool-button {
  --el-button-bg-color: transparent;
  --el-button-text-color: var(--rl-muted);
  width: var(--el-component-size);
  padding: 0;
}
.topbar--phone .tool-button {
  border-color: transparent;
}
.account-button {
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--el-component-size);
  padding: 0 8px 0 4px;
  border: 0;
  border-radius: var(--el-border-radius-base);
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.topbar--stacked .account-button {
  min-width: 40px;
  justify-content: center;
  padding: 0;
}
.topbar--phone .account-button {
  min-width: 44px;
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
  color: var(--rl-focus);
  font-size: 12px;
  font-weight: 600;
}
.topbar--stacked .account-avatar {
  width: 30px;
  height: 30px;
  border-radius: 15px;
}
.topbar--phone .account-avatar {
  width: 32px;
  height: 32px;
  border-radius: 16px;
  font-size: 13px;
}
.account-chevron {
  color: var(--rl-muted);
}
</style>
