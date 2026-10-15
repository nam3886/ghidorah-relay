import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App.vue";
import { ApiError } from "@/api/client";
import * as shell from "@/api/shell";
import type { Stats } from "@/api/shell";
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
  return { default: defineComponent({ name: "JobDetailDialog", render: () => h("div") }) };
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
    expect(tabs.map((t) => t.text())).toEqual([
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

  it("draws the stats strip with the dead and disabled counts and reads it for the chosen provider", async () => {
    const { wrapper } = await open("/jobs");
    const text = (key: string) => wrapper.find(`[data-testid="stat-${key}"]`).text();
    expect(text("in-progress")).toBe("In progress5");
    expect(text("completed")).toBe("Completed40");
    expect(text("accounts-ready")).toBe("Accounts ready4 / 6");
    expect(text("accounts-dead")).toBe("Accounts dead0");
    expect(text("accounts-disabled")).toBe("Accounts disabled1");
    expect(wrapper.find('[data-testid="stat-failed"] .stat-value--bad').exists()).toBe(true);
    expect(wrapper.find('[data-testid="stat-accounts-dead"] .stat-value--bad').exists()).toBe(false);
    expect(shell.getStats).toHaveBeenCalledWith("");
  });

  it("reads the strip again on a tab switch", async () => {
    const { wrapper } = await open("/jobs");
    const before = vi.mocked(shell.getStats).mock.calls.length;
    await wrapper.findAll('[role="tab"]')[1]?.trigger("click");
    await until(() => expect(vi.mocked(shell.getStats).mock.calls.length).toBe(before + 1));
  });

  it("shows no banner when nothing needs attention", async () => {
    const { wrapper } = await open("/jobs");
    expect(wrapper.find(".attention-banner").exists()).toBe(false);
  });

  it("shows the live alerts and the open group count, 200 as 200+", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ alerts: [{ kind: "drift", severity: "warning", message: "dola: 3 of 10 jobs in 24 h show x" }] }),
    );
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 3 }, () => ({})) });
    const { wrapper } = await open("/jobs");
    const banner = wrapper.find(".attention-banner");
    expect(banner.text()).toContain("Needs attention");
    expect(banner.text()).toContain("drift: dola: 3 of 10 jobs in 24 h show x");
    expect(wrapper.find('[data-testid="open-groups"]').text()).toContain("3 open alert group(s)");
  });

  it("writes 200+ for a full page of open groups and opens Alerts from the banner", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 200 }, () => ({})) });
    const { wrapper, router } = await open("/jobs");
    expect(wrapper.find('[data-testid="open-groups"]').text()).toContain("200+ open alert group(s)");
    await wrapper.find('[data-testid="open-groups"] button').trigger("click");
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
    expect(filter.text()).toBe("All");
    expect(filter.text()).not.toContain("Select");
    const select = wrapper.findComponent({ name: "ElSelect" });
    select.vm.$emit("update:modelValue", "dola");
    await flushPromises();
    expect(scope.provider).toBe("dola");
    expect(filter.text()).toBe("dola");
    select.vm.$emit("update:modelValue", "__all__");
    await flushPromises();
    expect(scope.provider).toBe("");
    expect(filter.text()).toBe("All");
  });

  it("has an API docs link that opens the reference in a new tab", async () => {
    const { wrapper } = await open("/jobs");
    const link = wrapper.find('a[href="/reference"]');
    expect(link.text()).toBe("API docs");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toContain("noopener");
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
    await wrapper.find('[data-testid="sign-out"]').trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    await flushPromises();
    expect(shell.logout).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(wrapper.find('[data-testid="sign-out"]').exists()).toBe(false);
    expect(wrapper.find('input[name="username"]').exists()).toBe(true);
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
