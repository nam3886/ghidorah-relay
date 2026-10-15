import { readFileSync } from "node:fs";
import { join } from "node:path";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App.vue";
import PageHeader from "@/components/layout/PageHeader.vue";
import { ApiError } from "@/api/client";
import * as shell from "@/api/shell";
import type { Stats } from "@/api/shell";
import { notify } from "@/lib/notify";
import { THEME_KEY, TOKEN_KEY, USER_KEY } from "@/lib/session";
import { createAppRouter } from "@/router";

vi.mock("@/api/shell", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getMe: vi.fn(),
  getModels: vi.fn(),
  getStats: vi.fn(),
  getOpenAlertGroups: vi.fn(),
}));

// The shell is tested against tiny named stand-ins for the routed views and the job dialog, so this spec does not
// depend on what a view draws or fetches. The name matters: KeepAlive picks the Test tab by its component name.
async function stubView(name: string, title: string) {
  const { defineComponent, h } = await import("vue");
  return { default: defineComponent({ name, render: () => h("section", [h("h2", title)]) }) };
}
vi.mock("@/views/JobsView.vue", () => stubView("JobsView", "Jobs"));
vi.mock("@/views/AccountsView.vue", () => stubView("AccountsView", "Accounts"));
vi.mock("@/views/KeysView.vue", () => stubView("KeysView", "API keys"));
vi.mock("@/views/LogsView.vue", () => stubView("LogsView", "Request logs"));
vi.mock("@/views/AlertsView.vue", () => stubView("AlertsView", "Alerts"));
vi.mock("@/views/FramesView.vue", () => stubView("FramesView", "Prompt frames"));
vi.mock("@/views/SettingsView.vue", () => stubView("SettingsView", "Settings"));
vi.mock("@/views/TestView.vue", () => stubView("TestView", "Test"));
vi.mock("@/components/jobs/JobDetailDialog.vue", async () => {
  const { defineComponent, h } = await import("vue");
  return {
    default: defineComponent({ name: "JobDetailDialog", render: () => h("div", { "data-testid": "job-dialog" }) }),
  };
});

const stats = (over: Partial<Stats> = {}): Stats => ({
  jobs: { queued: 2, in_progress: 3, done: 40, failed: 1, pre_job_failures_24h: 0 },
  made: { videos: 11, images: 29 },
  accounts: { usable: 4, available: 6, dead: 0, disabled: 1 },
  keys: 3,
  alerts: [],
  ...over,
});

let wrapper: VueWrapper | null = null;

// A route's view is a lazy import: the first one in a worker can take a while.
const until = (check: () => void) => vi.waitFor(check, { timeout: 15000 });

async function open(path: string, signedIn = true) {
  sessionStorage.clear();
  if (signedIn) {
    sessionStorage.setItem(TOKEN_KEY, "t");
    sessionStorage.setItem(USER_KEY, "owner");
  }
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createAppRouter(createMemoryHistory());
  await router.push(path);
  wrapper = mount(App, { global: { plugins: [pinia, router] }, attachTo: document.body });
  await flushPromises();
  return { router, wrapper };
}

beforeEach(() => {
  // happy-dom answers matchMedia for a 1024 px window (a tablet): the shell tests start from the desktop layout.
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
  document.documentElement.classList.remove("dark");
  localStorage.clear();
  for (const fn of Object.values(shell)) (fn as ReturnType<typeof vi.fn>).mockReset();
  vi.mocked(shell.getMe).mockResolvedValue({ username: "owner" });
  vi.mocked(shell.getModels).mockResolvedValue({
    data: [
      { id: "a", owned_by: "vibes" },
      { id: "b", owned_by: "dola" },
    ],
  });
  vi.mocked(shell.getStats).mockResolvedValue(stats());
  vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [] });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the shell", () => {
  it("draws the eight tabs in order, Jobs selected, with the user's name", async () => {
    const { wrapper } = await open("/jobs");
    const tabs = wrapper.findAll('[role="tab"]');
    expect(tabs.map((t) => t.find(".tab-label").text())).toEqual([
      "Jobs",
      "Accounts",
      "API keys",
      "Request logs",
      "Alerts",
      "Prompt frames",
      "Settings",
      "Test",
    ]);
    expect(tabs[0]?.attributes("aria-selected")).toBe("true");
    expect(wrapper.find('[data-testid="whoami"]').text()).toBe("owner");
    expect(wrapper.find("h2").text()).toBe("Jobs");
  });

  it("opens the tab that was clicked", async () => {
    const { wrapper, router } = await open("/jobs");
    await wrapper.findAll('[role="tab"]')[2]?.trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("keys"));
    await until(() => expect(wrapper.find("h2").text()).toBe("API keys"));
  });

  it("reads the stats for the chosen provider and draws no stats strip on any tab", async () => {
    const { wrapper, router } = await open("/jobs");
    expect(shell.getStats).toHaveBeenCalledWith("");
    for (const name of ["jobs", "accounts", "keys", "logs", "alerts", "frames", "settings", "test"]) {
      await router.push({ name });
      await flushPromises();
      expect(wrapper.find(".stats-strip").exists(), name).toBe(false);
      expect(wrapper.find('[aria-label="Totals"]').exists(), name).toBe(false);
    }
  });

  it("reads the stats again on a tab switch", async () => {
    const { wrapper } = await open("/jobs");
    const before = vi.mocked(shell.getStats).mock.calls.length;
    await wrapper.findAll('[role="tab"]')[1]?.trigger("click");
    await until(() => expect(vi.mocked(shell.getStats).mock.calls.length).toBe(before + 1));
  });

  it("mounts the job detail through JobDetailDrawer, once, while signed in only", async () => {
    const first = await open("/jobs");
    expect(first.wrapper.findAll('[data-testid="job-dialog"]')).toHaveLength(1);
    first.wrapper.unmount();
    document.body.replaceChildren();
    const login = await open("/login", false);
    expect(login.wrapper.find('[data-testid="job-dialog"]').exists()).toBe(false);
  });

  it("frames the page: the top bar first, then the banner and the view inside one main", async () => {
    const { wrapper } = await open("/jobs");
    const shellEl = wrapper.find(".app-shell");
    expect([...shellEl.element.children].map((el) => el.tagName)).toEqual(["HEADER", "MAIN"]);
    expect(wrapper.find("main h2").text()).toBe("Jobs");
    expect(wrapper.find("header nav").attributes("aria-label")).toBe("Main");
  });

  it("counts the unfinished jobs on the Jobs tab and the open groups on Alerts, only when above zero", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 4 }, () => ({})) });
    const { wrapper } = await open("/jobs");
    expect(wrapper.find('[data-testid="tab-count-jobs"]').text()).toBe("5");
    expect(wrapper.find('[data-testid="tab-count-alerts"]').text()).toBe("4");
    expect(wrapper.find('[data-testid="tab-count-keys"]').exists()).toBe(false);
    // the count is not part of the tab's accessible name
    expect(wrapper.find('[data-testid="tab-count-jobs"]').attributes("aria-hidden")).toBe("true");
  });

  it("shows no count while the numbers are zero", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ jobs: { queued: 0, in_progress: 0, done: 1, failed: 0, pre_job_failures_24h: 0 } }),
    );
    const { wrapper } = await open("/jobs");
    expect(wrapper.find('[data-testid="tab-count-jobs"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="tab-count-alerts"]').exists()).toBe(false);
  });

  it("closes every toast when the tab changes, and not before", async () => {
    const clear = vi.spyOn(notify, "clear");
    const { router } = await open("/jobs");
    expect(clear).not.toHaveBeenCalled();
    await router.push("/accounts");
    await flushPromises();
    expect(clear).toHaveBeenCalledTimes(1);
    await router.push({ name: "accounts", query: { page: "2" } });
    await flushPromises();
    expect(clear).toHaveBeenCalledTimes(1);
    clear.mockRestore();
  });

  it("merges equal toasts into one, lets an error be closed and lets a success go by itself", async () => {
    notify.error("boom");
    notify.error("boom");
    notify.success("fine");
    await flushPromises();
    const toasts = [...document.body.querySelectorAll(".el-message")];
    expect(toasts.map((t) => t.querySelector(".el-message__content")?.textContent)).toEqual(["boom", "fine"]);
    expect(toasts[0]?.querySelector(".el-message__closeBtn")).not.toBeNull();
    expect(toasts[1]?.querySelector(".el-message__closeBtn")).toBeNull();
    notify.clear();
  });

  it("draws the banner above every view but Jobs, which draws its own", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [{}] });
    const { wrapper, router } = await open("/jobs");
    expect(wrapper.find(".attention-banner").exists()).toBe(false);
    for (const name of ["accounts", "keys", "logs", "alerts", "frames", "settings", "test"]) {
      await router.push({ name });
      await until(() => expect(wrapper.find(".attention-banner").exists(), name).toBe(true));
    }
    await router.push({ name: "jobs" });
    await until(() => expect(wrapper.find(".attention-banner").exists()).toBe(false));
  });

  it("shows no banner when nothing needs attention", async () => {
    const { wrapper } = await open("/accounts");
    expect(wrapper.find(".attention-banner").exists()).toBe(false);
  });

  it("shows the live alerts and the open group count, 200 as 200+", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ alerts: [{ kind: "drift", severity: "warning", message: "dola: 3 of 10 jobs in 24 h show x" }] }),
    );
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 3 }, () => ({})) });
    const { wrapper } = await open("/accounts");
    const banner = wrapper.find(".attention-banner");
    expect(banner.attributes("role")).toBe("status");
    expect(banner.text()).toContain("drift: dola: 3 of 10 jobs in 24 h show x");
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("3 open alert groups.");
    expect(wrapper.find('[data-testid="review-alerts"]').text()).toBe("Review in Alerts");
  });

  it("says group, not groups, for one open group", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [{}] });
    const { wrapper } = await open("/accounts");
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("1 open alert group.");
  });

  it("with live conditions only, says Needs attention and still offers the link to Alerts", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ alerts: [{ kind: "drift", severity: "warning", message: "dola: x" }] }),
    );
    const { wrapper } = await open("/accounts");
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("Needs attention.");
    expect(wrapper.find('[data-testid="review-alerts"]').text()).toBe("Review in Alerts");
  });

  it("draws the banner with a warn fill and no side border", () => {
    const source = readFileSync(join(process.cwd(), "src/components/layout/AttentionBanner.vue"), "utf8");
    expect(source).toContain("background: var(--rl-warn-bg);");
    expect(source).toContain("border-radius: 12px;");
    expect(source).not.toMatch(/border-(left|right|top|bottom)\s*:/);
  });

  it("folds three or more live conditions behind one count and keeps the group link in view", async () => {
    const alert = (kind: string) => ({ kind, severity: "warning", message: `${kind} message` });
    vi.mocked(shell.getStats).mockResolvedValue(stats({ alerts: [alert("a1"), alert("a2"), alert("a3")] }));
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [{}] });
    const { wrapper } = await open("/accounts");
    const banner = wrapper.find(".attention-banner");
    expect(banner.find('[data-testid="fold-alerts"]').text()).toContain("3 live conditions");
    expect(banner.text()).not.toContain("a1 message");
    expect(wrapper.find('[data-testid="review-alerts"]').exists()).toBe(true);
    await banner.find('[data-testid="fold-alerts"] button').trigger("click");
    expect(banner.text()).toContain("a1: a1 message");
    expect(banner.text()).toContain("a3: a3 message");
    await banner.find('[data-testid="fold-alerts"] button').trigger("click");
    expect(banner.text()).not.toContain("a2 message");
  });

  it("lists two live conditions in full, with no fold", async () => {
    const alert = (kind: string) => ({ kind, severity: "warning", message: `${kind} message` });
    vi.mocked(shell.getStats).mockResolvedValue(stats({ alerts: [alert("a1"), alert("a2")] }));
    const { wrapper } = await open("/accounts");
    expect(wrapper.find('[data-testid="fold-alerts"]').exists()).toBe(false);
    expect(wrapper.find(".attention-banner").text()).toContain("a2: a2 message");
  });

  it("writes 200+ for a full page of open groups and opens Alerts from the banner", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 200 }, () => ({})) });
    const { wrapper, router } = await open("/accounts");
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("200+ open alert groups.");
    await wrapper.find('[data-testid="review-alerts"]').trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("alerts"));
  });

  it("keeps the Test tab alive across tab switches", async () => {
    const { wrapper, router } = await open("/test");
    const first = wrapper.find("main section").element;
    await router.push("/jobs");
    await flushPromises();
    expect(wrapper.find("main h2").text()).toBe("Jobs");
    await router.push("/test");
    await flushPromises();
    expect(wrapper.find("main section").element).toBe(first);
  });

  it("does not keep the other tabs alive", async () => {
    const { wrapper, router } = await open("/jobs");
    const first = wrapper.find("main section").element;
    await router.push("/accounts");
    await flushPromises();
    await router.push("/jobs");
    await flushPromises();
    expect(wrapper.find("main section").element).not.toBe(first);
  });

  it("offers the providers of the catalog in the filter, sorted", async () => {
    await open("/jobs");
    const { useScopeStore } = await import("@/stores/scope");
    expect(useScopeStore().providers).toEqual(["dola", "vibes"]);
  });

  it("shows All, not the placeholder, in the provider filter, and keeps the store's empty string for All", async () => {
    const { wrapper } = await open("/jobs");
    const { useScopeStore } = await import("@/stores/scope");
    const scope = useScopeStore();
    const filter = wrapper.find('[data-testid="provider-filter"]');
    // the word Provider sits inside the select, in front of the value
    expect(filter.text()).toBe("ProviderAll");
    expect(filter.text()).not.toContain("Select");
    const select = wrapper.findComponent({ name: "ElSelect" });
    select.vm.$emit("update:modelValue", "dola");
    await flushPromises();
    expect(scope.provider).toBe("dola");
    expect(filter.text()).toBe("Providerdola");
    select.vm.$emit("update:modelValue", "__all__");
    await flushPromises();
    expect(scope.provider).toBe("");
    expect(filter.text()).toBe("ProviderAll");
  });

  it("opens the API reference in a new tab from the account menu, and nowhere else", async () => {
    const { wrapper } = await open("/jobs");
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    const menu = wrapper.findComponent({ name: "ElDropdown" });
    menu.vm.$emit("command", "docs");
    expect(opened).toHaveBeenCalledTimes(1);
    expect(opened).toHaveBeenCalledWith("/reference", "_blank", "noopener");
    menu.vm.$emit("command", "something-else");
    expect(opened).toHaveBeenCalledTimes(1);
    expect(shell.logout).not.toHaveBeenCalled();
  });

  it("names the account menu after the user, with the first letter as the avatar", async () => {
    const { wrapper } = await open("/jobs");
    const button = wrapper.find('[data-testid="account-menu"]');
    expect(button.attributes("aria-label")).toBe("Account menu, owner");
    expect(button.find(".account-avatar").text()).toBe("O");
  });

  it("toggles the theme and remembers it", async () => {
    const { wrapper } = await open("/jobs");
    const toggle = wrapper.find('[data-testid="theme-toggle"]');
    expect(toggle.attributes("aria-label")).toBe("Switch to dark theme");
    await toggle.trigger("click");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(wrapper.find('[data-testid="theme-toggle"]').attributes("aria-label")).toBe("Switch to light theme");
  });

  it("signs out: calls the server, drops the session and shows the login page", async () => {
    vi.mocked(shell.logout).mockResolvedValue({});
    const { wrapper, router } = await open("/jobs");
    // open the menu with a click on the account button, then click the item as a user does
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    await until(() => expect(document.body.querySelector('[data-testid="sign-out"]')).not.toBeNull());
    document.body.querySelector<HTMLElement>('[data-testid="sign-out"]')?.click();
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    await flushPromises();
    expect(shell.logout).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(wrapper.find('[data-testid="account-menu"]').exists()).toBe(false);
    expect(wrapper.find('input[name="username"]').exists()).toBe(true);
  });

  it("signs out through the menu command too: server revoke first, then every sign-out hook", async () => {
    vi.mocked(shell.logout).mockResolvedValue({});
    const { useAuthStore } = await import("@/stores/auth");
    const { wrapper, router } = await open("/jobs");
    const hook = vi.fn();
    useAuthStore().onSignOut(hook);
    wrapper.findComponent({ name: "ElDropdown" }).vm.$emit("command", "signout");
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    expect(shell.logout).toHaveBeenCalledTimes(1);
    expect(hook).toHaveBeenCalledTimes(1);
    expect(vi.mocked(shell.logout).mock.invocationCallOrder[0]).toBeLessThan(hook.mock.invocationCallOrder[0] ?? 0);
  });

  it("still signs out locally when the server call fails", async () => {
    vi.mocked(shell.logout).mockRejectedValue(new Error("down"));
    const { wrapper, router } = await open("/jobs");
    wrapper.findComponent({ name: "ElDropdown" }).vm.$emit("command", "signout");
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("the top bar", () => {
  it("marks the count of the current tab as primary and the Alerts count as warn", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [{}, {}] });
    const { wrapper } = await open("/jobs");
    const jobs = wrapper.find('[data-testid="tab-count-jobs"]');
    const alerts = wrapper.find('[data-testid="tab-count-alerts"]');
    expect(jobs.classes()).toContain("tab-count--current");
    expect(jobs.classes()).not.toContain("tab-count--warn");
    expect(alerts.classes()).toContain("tab-count--warn");
    expect(alerts.classes()).not.toContain("tab-count--current");
  });

  it("moves the primary count with the current tab", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [{}] });
    const { wrapper, router } = await open("/jobs");
    await router.push("/alerts");
    await flushPromises();
    expect(wrapper.find('[data-testid="tab-count-jobs"]').classes()).not.toContain("tab-count--current");
    expect(wrapper.find('[data-testid="tab-count-alerts"]').classes()).toContain("tab-count--current");
  });

  it("shows ready over total on the Accounts tab, and nothing without accounts", async () => {
    const { wrapper } = await open("/jobs");
    expect(wrapper.find('[data-testid="tab-count-accounts"]').text()).toBe("4/6");
    expect(wrapper.find('[data-testid="tab-count-accounts"]').attributes("aria-hidden")).toBe("true");
    wrapper.unmount();
    vi.mocked(shell.getStats).mockResolvedValue(stats({ accounts: { usable: 1, available: 1, dead: 0, disabled: 0 } }));
    const one = await open("/jobs");
    expect(one.wrapper.find('[data-testid="tab-count-accounts"]').text()).toBe("1/1");
    one.wrapper.unmount();
    vi.mocked(shell.getStats).mockResolvedValue(stats({ accounts: { usable: 0, available: 0, dead: 0, disabled: 0 } }));
    const none = await open("/jobs");
    expect(none.wrapper.find('[data-testid="tab-count-accounts"]').exists()).toBe(false);
  });

  it("shows 999+ for a count above 999 and the number itself at 999", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ jobs: { queued: 500, in_progress: 499, done: 0, failed: 0, pre_job_failures_24h: 0 } }),
    );
    const { wrapper } = await open("/jobs");
    expect(wrapper.find('[data-testid="tab-count-jobs"]').text()).toBe("999");
    wrapper.unmount();
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ jobs: { queued: 500, in_progress: 500, done: 0, failed: 0, pre_job_failures_24h: 0 } }),
    );
    const again = await open("/jobs");
    expect(again.wrapper.find('[data-testid="tab-count-jobs"]').text()).toBe("999+");
  });

  it("links the logo to Jobs and names it", async () => {
    const { wrapper, router } = await open("/accounts");
    const home = wrapper.find('a[aria-label="Ghidorah admin home"]');
    expect(home.exists()).toBe(true);
    expect(home.find("img").attributes("alt")).toBe("Ghidorah");
    await home.trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("jobs"));
  });

  it("is one row on a desktop, and two rows on a tablet and on a phone, with the user's name only on the desktop", async () => {
    const stub = (matching: string[]) =>
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: matching.includes(query),
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }));
    const { PHONE_QUERY, TABLET_QUERY } = await import("@/composables/use-breakpoint");
    const classesOf = async (matching: string[]) => {
      wrapper?.unmount();
      wrapper = null;
      document.body.replaceChildren();
      stub(matching);
      const view = await open("/jobs");
      const header = view.wrapper.find("header");
      const out = {
        stacked: header.classes().includes("topbar--stacked"),
        phone: header.classes().includes("topbar--phone"),
        name: view.wrapper.find('[data-testid="whoami"]').exists(),
        label: view.wrapper.find(".scope-label").exists(),
        main: view.wrapper
          .find("main")
          .classes()
          .filter((c) => c.startsWith("app-main--")),
      };
      return out;
    };
    expect(await classesOf([])).toEqual({ stacked: false, phone: false, name: true, label: true, main: [] });
    expect(await classesOf([TABLET_QUERY])).toEqual({
      stacked: true,
      phone: false,
      name: false,
      label: true,
      main: ["app-main--tablet"],
    });
    expect(await classesOf([PHONE_QUERY])).toEqual({
      stacked: true,
      phone: true,
      name: false,
      label: false,
      main: ["app-main--phone"],
    });
  });
});

describe("the page header", () => {
  const mountHeader = (slots: Record<string, string> = {}) =>
    mount(PageHeader, { props: { title: "Jobs", subtitle: "Every job." }, slots });

  it("shows the title and the one muted sentence, and the meta and actions in their slots", () => {
    const w = mountHeader({ meta: "Updated 10:00:00", actions: "<button>Refresh</button>" });
    expect(w.find("h1").text()).toBe("Jobs");
    expect(w.find(".page-header__subtitle").text()).toBe("Every job.");
    expect(w.find(".page-header__meta").text()).toBe("Updated 10:00:00");
    expect(w.find(".page-header__actions button").text()).toBe("Refresh");
  });

  it("draws no sentence, no meta and no side when it has none", () => {
    const w = mount(PageHeader, { props: { title: "Jobs" } });
    expect(w.find(".page-header__subtitle").exists()).toBe(false);
    expect(w.find(".page-header__side").exists()).toBe(false);
  });

  it("leaves the meta line out on a phone but keeps the actions beside the title", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(max-width: 767px)",
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    const w = mountHeader({ meta: "Updated 10:00:00", actions: "<button>Refresh</button>" });
    expect(w.classes()).toContain("page-header--phone");
    expect(w.find(".page-header__meta").exists()).toBe(false);
    expect(w.find(".page-header__actions button").exists()).toBe(true);
  });

  it("with only a meta slot on a phone has no side at all", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(max-width: 767px)",
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    const w = mountHeader({ meta: "Updated 10:00:00" });
    expect(w.find(".page-header__side").exists()).toBe(false);
  });
});

describe("the login page", () => {
  async function submit(wrapper: VueWrapper, username: string, password: string) {
    await wrapper.find('input[name="username"]').setValue(username);
    await wrapper.find('input[name="password"]').setValue(password);
    await wrapper.find("form").trigger("submit");
    await flushPromises();
  }

  it("signs in and opens Jobs", async () => {
    vi.mocked(shell.login).mockResolvedValue({ token: "tok", expires_at: 1 });
    const { wrapper, router } = await open("/login", false);
    await submit(wrapper, "owner", "a-long-password");
    expect(shell.login).toHaveBeenCalledWith("owner", "a-long-password");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("tok");
    expect(router.currentRoute.value.name).toBe("jobs");
  });

  it("says wrong username or password for a 401 and stays on the page", async () => {
    vi.mocked(shell.login).mockRejectedValue(
      new ApiError(401, { error: { code: "auth.invalid", message: "bad", param: null } }),
    );
    const { wrapper, router } = await open("/login", false);
    await submit(wrapper, "owner", "nope");
    expect(wrapper.text()).toContain("Wrong username or password");
    expect(router.currentRoute.value.name).toBe("login");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("shows the server text for too many attempts", async () => {
    vi.mocked(shell.login).mockRejectedValue(
      new ApiError(429, {
        error: { code: "auth.too_many_attempts", message: "Too many attempts, try again in 5 minutes", param: null },
      }),
    );
    const { wrapper } = await open("/login", false);
    await submit(wrapper, "owner", "nope");
    expect(wrapper.text()).toContain("Too many attempts, try again in 5 minutes");
  });

  it("skips the form when the stored session is still good", async () => {
    sessionStorage.setItem(TOKEN_KEY, "t");
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(createMemoryHistory());
    await router.push("/login");
    wrapper = mount(App, { global: { plugins: [pinia, router] }, attachTo: document.body });
    await flushPromises();
    expect(shell.getMe).toHaveBeenCalledWith(true);
    expect(router.currentRoute.value.name).toBe("jobs");
  });

  it("drops a dead stored session and stays on the form", async () => {
    vi.mocked(shell.getMe).mockRejectedValue(new ApiError(401, null));
    sessionStorage.setItem(TOKEN_KEY, "dead");
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(createMemoryHistory());
    await router.push("/login");
    wrapper = mount(App, { global: { plugins: [pinia, router] }, attachTo: document.body });
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("login");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
