import { createRouter, createWebHashHistory, type RouteRecordRaw, type RouterHistory } from "vue-router";
import { getToken } from "@/lib/session";

export type StripMode = "all" | "only";

declare module "vue-router" {
  interface RouteMeta {
    // Shown in the tab bar.
    label?: string;
    // Reachable without a session (the login page).
    public?: boolean;
    // Filtered by the provider strip under the top bar (ProviderStrip) instead of the top bar's Provider list, in one
    // of its modes: "all" (All, then each provider: a filter) or "only" (the providers alone: the page works on one at a time).
    providerStrip?: StripMode;
    // The strip only below the desktop: the page draws its own provider picker at 1280 px and up (Jobs: the cards).
    stripCompactOnly?: boolean;
  }
}

// The tab bar, in display order. Jobs is first and the default.
export const TABS = [
  { name: "jobs", label: "Jobs", component: () => import("@/views/JobsView.vue") },
  { name: "accounts", label: "Accounts", component: () => import("@/views/AccountsView.vue") },
  { name: "keys", label: "API keys", component: () => import("@/views/KeysView.vue") },
  { name: "logs", label: "Request logs", component: () => import("@/views/LogsView.vue") },
  { name: "alerts", label: "Alerts", component: () => import("@/views/AlertsView.vue") },
  { name: "frames", label: "Prompt frames", component: () => import("@/views/FramesView.vue") },
  { name: "settings", label: "Settings", component: () => import("@/views/SettingsView.vue") },
  { name: "test", label: "Test", component: () => import("@/views/TestView.vue") },
] as const;

// The tabs whose provider filter is the strip, and its mode there: only Jobs, below the desktop, where it is the one
// provider filter (the B boards draw no strip; Keys and Alerts have none). Accounts, Request logs, Prompt frames,
// Settings and Test draw their own provider chips, tabs or lists.
const STRIP_TABS: Record<string, StripMode> = {
  jobs: "all",
};

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: { name: "jobs" } },
  { path: "/login", name: "login", component: () => import("@/views/LoginView.vue"), meta: { public: true } },
  ...TABS.map((tab) => ({
    path: `/${tab.name}`,
    name: tab.name,
    component: tab.component,
    meta: { label: tab.label, providerStrip: STRIP_TABS[tab.name], stripCompactOnly: tab.name === "jobs" },
  })),
  // Anything else (a mistyped hash) lands on the default tab.
  { path: "/:rest(.*)*", redirect: { name: "jobs" } },
];

// Hash mode: the old bookmarks were a bare tab id (`/manage#jobs`). vue-router reads a hash without a leading slash as
// that path (`#jobs` is `/jobs`) and rewrites the address bar to `#/jobs` on the first navigation, so the eight old
// bookmarks land on the new routes through the same route list, with no second list to keep in step.
export function createAppRouter(history: RouterHistory = createWebHashHistory()) {
  const router = createRouter({ history, routes });
  // The session token is the one source of truth, read from storage (the client clears it on a 401).
  router.beforeEach((to) => {
    if (!to.meta.public && !getToken()) return { name: "login" };
    return true;
  });
  return router;
}
