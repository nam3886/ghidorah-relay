import { readFileSync } from "node:fs";
import { join } from "node:path";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App.vue";
import AttentionBanner from "@/components/layout/AttentionBanner.vue";
import PageHeader from "@/components/layout/PageHeader.vue";
import LoginView from "@/views/LoginView.vue";
import { ApiError } from "@/api/client";
import * as shell from "@/api/shell";
import type { AlertGroup } from "@/api/alerts";
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
  getNewestReply: vi.fn(),
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
vi.mock("@/components/jobs/JobDetailDrawer.vue", async () => {
  const { defineComponent, h } = await import("vue");
  return {
    default: defineComponent({ name: "JobDetailDrawer", render: () => h("div", { "data-testid": "job-dialog" }) }),
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

// An open alert group as the server lists it: the canvas's one (Dola's refusal, 4 open jobs from 3 keys).
const group = (over: Partial<AlertGroup> = {}): AlertGroup => ({
  kind: "unclear_reply",
  group: "g1",
  title: "Sorry, I can’t create that video",
  severity: "warning",
  provider: "dola",
  first_ms: 1,
  last_ms: 2,
  count: 6,
  open_count: 4,
  keys: 3,
  newest_id: 9,
  ...over,
});

// The banner is not part of the shell: Jobs draws it, so these tests mount the component itself.
async function openBanner() {
  sessionStorage.clear();
  sessionStorage.setItem(TOKEN_KEY, "t");
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createAppRouter(createMemoryHistory());
  await router.push("/jobs");
  const { useStatsStore } = await import("@/stores/stats");
  wrapper = mount(AttentionBanner, { global: { plugins: [pinia, router] }, attachTo: document.body });
  await useStatsStore().refresh();
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

  it("frames the page: the top bar first, then the view inside one main", async () => {
    const { wrapper } = await open("/jobs");
    const shellEl = wrapper.find(".app-shell");
    expect([...shellEl.element.children].map((el) => el.tagName)).toEqual(["HEADER", "MAIN"]);
    expect(wrapper.find("main h2").text()).toBe("Jobs");
    expect(wrapper.find("header nav").attributes("aria-label")).toBe("Main");
  });

  it("counts the unfinished jobs on the Jobs tab and the open groups on Alerts, only when above zero", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 4 }, () => group()) });
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

  it("never mounts the attention banner: the shell source does not import it", () => {
    const source = readFileSync(join(process.cwd(), "src/components/layout/AppShell.vue"), "utf8");
    expect(source).not.toContain("AttentionBanner");
  });

  it("draws no banner on any tab: Jobs renders it itself, after its band card", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group()] });
    const { wrapper, router } = await open("/jobs");
    for (const name of ["jobs", "accounts", "keys", "logs", "alerts", "frames", "settings", "test"]) {
      await router.push({ name });
      await flushPromises();
      expect(wrapper.find(".attention-banner").exists(), name).toBe(false);
    }
  });

  it("shows no banner when nothing needs attention", async () => {
    const { wrapper } = await openBanner();
    expect(wrapper.find(".attention-banner").exists()).toBe(false);
  });

  it("shows the open group count and words the first group, as drawn even beside a live condition", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ alerts: [{ kind: "drift", severity: "warning", message: "dola: 3 of 10 jobs in 24 h show x" }] }),
    );
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 3 }, () => group()) });
    const { wrapper } = await openBanner();
    const banner = wrapper.find(".attention-banner");
    expect(banner.attributes("role")).toBe("status");
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("3 open alert groups.");
    expect(wrapper.find('[data-testid="banner-detail"]').text()).toBe(
      "Dola replied “Sorry, I can’t create that video” to 4 jobs from 3 keys.",
    );
    expect(banner.text()).not.toContain("3 of 10 jobs");
    expect(wrapper.find('[data-testid="review-alerts"]').text()).toBe("Review in Alerts");
  });

  it("says group, not groups, for one open group", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group()] });
    const { wrapper } = await openBanner();
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("1 open alert group.");
  });

  it("with live conditions only, says Needs attention and still offers the link to Alerts", async () => {
    vi.mocked(shell.getStats).mockResolvedValue(
      stats({ alerts: [{ kind: "drift", severity: "warning", message: "dola: x" }] }),
    );
    const { wrapper } = await openBanner();
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("Needs attention.");
    expect(wrapper.find('[data-testid="review-alerts"]').text()).toBe("Review in Alerts");
  });

  it("draws the banner with a warn fill and no side border", () => {
    const source = readFileSync(join(process.cwd(), "src/components/layout/AttentionBanner.vue"), "utf8");
    expect(source).toContain("background: var(--rl-warn-bg);");
    expect(source).toContain("border-radius: 12px;");
    expect(source).not.toMatch(/border-(left|right|top|bottom)\s*:/);
  });

  it("draws one line: the bold sentence, the muted live condition and the link, with no list and no fold", async () => {
    const alert = (kind: string) => ({ kind, severity: "warning", message: `${kind} message` });
    vi.mocked(shell.getStats).mockResolvedValue(stats({ alerts: [alert("a1")] }));
    const { wrapper } = await openBanner();
    const banner = wrapper.find(".attention-banner");
    expect(banner.find('[data-testid="banner-headline"]').text()).toBe("Needs attention.");
    expect(banner.find('[data-testid="banner-detail"]').text()).toBe("a1 message");
    expect(banner.find("ul").exists()).toBe(false);
    expect(banner.find("button").exists()).toBe(false);
    expect(banner.find('[data-testid="review-alerts"]').exists()).toBe(true);
  });

  it("with several live conditions shows the first and the count of the rest", async () => {
    const alert = (kind: string) => ({ kind, severity: "warning", message: `${kind} message` });
    vi.mocked(shell.getStats).mockResolvedValue(stats({ alerts: [alert("a1"), alert("a2"), alert("a3")] }));
    const { wrapper } = await openBanner();
    expect(wrapper.find('[data-testid="banner-detail"]').text()).toBe("a1 message (+2 more)");
    expect(wrapper.text()).not.toContain("a2 message");
  });

  it("quotes the newest reply as it was stored, not the group's folded wording", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({
      items: [group({ title: "sorry, i can't create that video" })],
    });
    vi.mocked(shell.getNewestReply).mockResolvedValue("Sorry, I can’t\n create that video");
    const { wrapper } = await openBanner();
    expect(shell.getNewestReply).toHaveBeenCalledWith(expect.objectContaining({ kind: "unclear_reply", group: "g1" }));
    expect(wrapper.find('[data-testid="banner-detail"]').text()).toBe(
      "Dola replied “Sorry, I can’t create that video” to 4 jobs from 3 keys.",
    );
  });

  it("words the first open group when only open groups exist: provider, reply, open jobs and keys", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({
      items: [group({ provider: "vibes", title: "Busy, try later", open_count: 1, keys: 1 }), group()],
    });
    const { wrapper } = await openBanner();
    expect(wrapper.find('[data-testid="banner-headline"]').text()).toBe("2 open alert groups.");
    expect(wrapper.find('[data-testid="banner-detail"]').text()).toBe(
      "Vibes replied “Busy, try later” to 1 job from 1 key.",
    );
  });

  it("writes 200+ for a full page of open groups and opens Alerts from the banner", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: Array.from({ length: 200 }, () => group()) });
    const { wrapper, router } = await openBanner();
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

  it("shows All in the provider trigger, lists All then the catalog's providers and keeps the store's empty string for All", async () => {
    const { wrapper } = await open("/jobs");
    const { useScopeStore } = await import("@/stores/scope");
    const scope = useScopeStore();
    const trigger = wrapper.find('[data-testid="provider-filter"]');
    // the word Provider sits in front of the value
    expect(trigger.text()).toBe("ProviderAll");
    expect(trigger.attributes("aria-haspopup")).toBe("listbox");
    expect(trigger.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    await trigger.trigger("click");
    const list = wrapper.find('[role="listbox"]');
    expect(list.attributes("aria-label")).toBe("Provider");
    expect(trigger.attributes("aria-expanded")).toBe("true");
    const options = list.findAll('[role="option"]');
    expect(options.map((o) => o.text())).toEqual(["All", "dola", "vibes"]);
    expect(options.map((o) => o.attributes("aria-selected"))).toEqual(["true", "false", "false"]);
    await options[1]?.trigger("click");
    expect(scope.provider).toBe("dola");
    expect(trigger.text()).toBe("Providerdola");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    await trigger.trigger("click");
    expect(wrapper.findAll('[role="option"]').map((o) => o.attributes("aria-selected"))).toEqual([
      "false",
      "true",
      "false",
    ]);
    await wrapper.findAll('[role="option"]')[0]?.trigger("click");
    expect(scope.provider).toBe("");
    expect(trigger.text()).toBe("ProviderAll");
  });

  it("closes an open popover on Escape and on a click outside, and keeps one open at a time", async () => {
    const { wrapper } = await open("/jobs");
    await wrapper.find('[data-testid="provider-filter"]').trigger("click");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true);
    await wrapper.find('[data-testid="provider-filter"]').trigger("click");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    await wrapper.find('[data-testid="provider-filter"]').trigger("click");
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    expect(wrapper.find('[role="menu"]').exists()).toBe(true);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    expect(wrapper.find('[role="menu"]').exists()).toBe(true);
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    // a press inside the menu does not close it
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    wrapper.find('[role="menu"]').element.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await flushPromises();
    expect(wrapper.find('[role="menu"]').exists()).toBe(true);
  });

  it("lists the providers in the drawn order, dola, vibes, meta, then any other sorted", async () => {
    vi.mocked(shell.getModels).mockResolvedValue({
      data: [
        { id: "a", owned_by: "meta" },
        { id: "b", owned_by: "zeta" },
        { id: "c", owned_by: "dola" },
        { id: "d", owned_by: "vibes" },
        { id: "e", owned_by: "alpha" },
      ],
    });
    const { wrapper } = await open("/jobs");
    await wrapper.find('[data-testid="provider-filter"]').trigger("click");
    expect(wrapper.findAll('[role="option"]').map((o) => o.text())).toEqual([
      "All",
      "dola",
      "vibes",
      "meta",
      "alpha",
      "zeta",
    ]);
  });

  const press = async (el: Element, key: string) => {
    const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
    el.dispatchEvent(event);
    await flushPromises();
    return event;
  };

  it("moves the focus into the listbox on open, onto the chosen option, and walks it with Arrow, Home and End", async () => {
    const { wrapper } = await open("/jobs");
    const trigger = wrapper.find('[data-testid="provider-filter"]');
    await trigger.trigger("click");
    await flushPromises();
    const options = wrapper.findAll('[role="option"]').map((o) => o.element);
    expect(document.activeElement).toBe(options[0]);
    const down = await press(options[0]!, "ArrowDown");
    expect(document.activeElement).toBe(options[1]);
    expect(down.defaultPrevented).toBe(true);
    await press(options[1]!, "End");
    expect(document.activeElement).toBe(options.at(-1));
    await press(options.at(-1)!, "ArrowDown");
    expect(document.activeElement).toBe(options.at(-1));
    await press(options.at(-1)!, "ArrowUp");
    expect(document.activeElement).toBe(options.at(-2));
    await press(options.at(-2)!, "Home");
    expect(document.activeElement).toBe(options[0]);
    await press(options[0]!, "ArrowUp");
    expect(document.activeElement).toBe(options[0]);
    // a key it does not own is left alone
    const other = new KeyboardEvent("keydown", { key: "x", bubbles: true, cancelable: true });
    options[0]!.dispatchEvent(other);
    expect(other.defaultPrevented).toBe(false);
  });

  it("opens the listbox on the chosen option when one is set, and gives the focus back to the trigger on Escape and on a pick", async () => {
    const { wrapper } = await open("/jobs");
    const { useScopeStore } = await import("@/stores/scope");
    useScopeStore().provider = "vibes";
    await flushPromises();
    const trigger = wrapper.find('[data-testid="provider-filter"]');
    await trigger.trigger("click");
    await flushPromises();
    const chosen = wrapper.find('[role="option"][aria-selected="true"]').element;
    expect(chosen.textContent?.trim()).toBe("vibes");
    expect(document.activeElement).toBe(chosen);
    await press(document.body, "Escape");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    expect(document.activeElement).toBe(trigger.element);
    await trigger.trigger("click");
    await flushPromises();
    await wrapper.findAll('[role="option"]')[0]?.trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(trigger.element);
  });

  it("opens a popup with ArrowDown on its trigger, puts the focus on the first menu item and returns it on Escape", async () => {
    const { wrapper } = await open("/jobs");
    const trigger = wrapper.find('[data-testid="account-menu"]');
    (trigger.element as HTMLElement).focus();
    await press(trigger.element, "ArrowDown");
    expect(wrapper.find('[role="menu"]').exists()).toBe(true);
    const items = wrapper.findAll('[role="menuitem"]').map((i) => i.element);
    expect(document.activeElement).toBe(items[0]);
    await press(items[0]!, "End");
    expect(document.activeElement).toBe(items[1]);
    const escape = await press(items[1]!, "Escape");
    expect(escape.defaultPrevented).toBe(true);
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    expect(document.activeElement).toBe(trigger.element);
    // an Escape with nothing open is not handled, so it is left to the page
    const idle = await press(document.body, "Escape");
    expect(idle.defaultPrevented).toBe(false);
    // another key on the trigger opens nothing
    await press(trigger.element, "ArrowUp");
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
  });

  // happy-dom lays nothing out: the trigger's box and the popup's size are given here; the window is 1024 x 768
  const layOut = (trigger: () => DOMRect, popup: { width: number; height: number }) => {
    const rect = Element.prototype.getBoundingClientRect;
    const sizes = ["offsetWidth", "offsetHeight"].map(
      (k) => [k, Object.getOwnPropertyDescriptor(HTMLElement.prototype, k)] as const,
    );
    Element.prototype.getBoundingClientRect = function (this: Element) {
      return this.classList.contains("pop") ? trigger() : new DOMRect(0, 0, 0, 0);
    };
    for (const key of ["offsetWidth", "offsetHeight"] as const) {
      Object.defineProperty(HTMLElement.prototype, key, {
        configurable: true,
        get(this: HTMLElement) {
          return this.classList.contains("menu") ? popup[key === "offsetWidth" ? "width" : "height"] : 0;
        },
      });
    }
    return () => {
      Element.prototype.getBoundingClientRect = rect;
      for (const [key, size] of sizes) if (size) Object.defineProperty(HTMLElement.prototype, key, size);
    };
  };
  const placed = (wrapper: VueWrapper) => {
    const style = (wrapper.find(".menu").element as HTMLElement).style;
    return { left: style.left, top: style.top, maxHeight: style.maxHeight };
  };

  it("places an open popup by the popup rule, relative to its trigger, and again when the window is resized", async () => {
    let triggerLeft = 0;
    let height = 80;
    const restore = layOut(() => new DOMRect(triggerLeft, 10, 0, 34), {
      width: 200,
      get height() {
        return height;
      },
    });
    try {
      const { wrapper } = await open("/jobs");
      const opened = async (which: string, left: number) => {
        triggerLeft = left;
        await wrapper.find(`[data-testid="${which}"]`).trigger("click");
        await flushPromises();
        return placed(wrapper);
      };
      expect([window.innerWidth, window.innerHeight]).toEqual([1024, 768]);
      // drawn: the left edge on the trigger's, 6 px under it
      expect(await opened("provider-filter", 800)).toEqual({ left: "0px", top: "40px", maxHeight: "" });
      await wrapper.find('[data-testid="provider-filter"]').trigger("click");
      // 900 + 200 passes the window: its right edge on the trigger's
      expect((await opened("provider-filter", 900)).left).toBe("-200px");
      expect((await opened("account-menu", 900)).left).toBe("-200px");
      // the window changes while the menu is open: placed again; too high for the room below, it is cut to it
      triggerLeft = 100;
      height = 900;
      window.dispatchEvent(new Event("resize"));
      await flushPromises();
      expect(placed(wrapper)).toEqual({ left: "0px", top: "40px", maxHeight: `${768 - 8 - 50}px` });
    } finally {
      restore();
    }
  });

  it("on a phone draws the Provider list with its right edge on the trigger, as drawn, and with its left edge on it when that would pass the window's left edge", async () => {
    const { PHONE_QUERY } = await import("@/composables/use-breakpoint");
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === PHONE_QUERY,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    // the trigger is 100 px wide and the list 200
    let triggerLeft = 0;
    const restore = layOut(() => new DOMRect(triggerLeft, 10, 100, 44), { width: 200, height: 190 });
    try {
      const { wrapper } = await open("/jobs");
      const trigger = wrapper.find('[data-testid="provider-filter"]');
      triggerLeft = 150;
      await trigger.trigger("click");
      await flushPromises();
      expect(placed(wrapper).left).toBe("-100px");
      await trigger.trigger("click");
      triggerLeft = 40;
      await trigger.trigger("click");
      await flushPromises();
      expect(placed(wrapper).left).toBe("0px");
    } finally {
      restore();
    }
  });

  it("hides the scrollbar of the header's tab row only: the row keeps the drawn height and still scrolls", () => {
    const source = readFileSync(join(process.cwd(), "src/components/layout/TopBar.vue"), "utf8");
    expect(source).toMatch(/\.topbar__nav \{[^}]*overflow-x: auto;[^}]*scrollbar-width: none;\s*\}/);
    expect(source).toMatch(/\.topbar__nav::-webkit-scrollbar \{\s*display: none;\s*\}/);
  });

  it("draws the account menu as API docs, a separator, Sign out", async () => {
    const { wrapper } = await open("/jobs");
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    const menu = wrapper.find('[role="menu"]');
    expect(menu.attributes("aria-label")).toBe("Account");
    expect([...menu.element.children].map((el) => el.getAttribute("role"))).toEqual([
      "menuitem",
      "separator",
      "menuitem",
    ]);
    expect(menu.findAll('[role="menuitem"]').map((i) => i.text())).toEqual(["API docs", "Sign out"]);
  });

  it("opens the API reference in a new tab from the account menu, and nowhere else", async () => {
    const { wrapper } = await open("/jobs");
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    await wrapper.find('[data-testid="api-docs"]').trigger("click");
    expect(opened).toHaveBeenCalledTimes(1);
    expect(opened).toHaveBeenCalledWith("/reference", "_blank", "noopener");
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    expect(shell.logout).not.toHaveBeenCalled();
  });

  it("names the account menu after the user, with the first letter as the avatar", async () => {
    const { wrapper } = await open("/jobs");
    const button = wrapper.find('[data-testid="account-menu"]');
    expect(button.attributes("aria-label")).toBe("Account menu, owner");
    expect(button.attributes("aria-haspopup")).toBe("menu");
    expect(button.find(".account-avatar").text()).toBe("O");
  });

  it("toggles the theme and remembers it", async () => {
    const { wrapper } = await open("/jobs");
    const toggle = wrapper.find('[data-testid="theme-toggle"]');
    expect(toggle.attributes("aria-label")).toBe("Switch theme");
    await toggle.trigger("click");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    // the moon is drawn in both themes
    expect(wrapper.find('[data-testid="theme-toggle"] path').attributes("d")).toBe(
      "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
    );
    await wrapper.find('[data-testid="theme-toggle"]').trigger("click");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("signs out: calls the server, drops the session and shows the login page", async () => {
    vi.mocked(shell.logout).mockResolvedValue({});
    const { wrapper, router } = await open("/jobs");
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    await wrapper.find('[data-testid="sign-out"]').trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    await flushPromises();
    expect(shell.logout).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(wrapper.find('[data-testid="account-menu"]').exists()).toBe(false);
    expect(wrapper.find('input[name="username"]').exists()).toBe(true);
  });

  it("signs out through the menu item: server revoke first, then every sign-out hook", async () => {
    vi.mocked(shell.logout).mockResolvedValue({});
    const { useAuthStore } = await import("@/stores/auth");
    const { wrapper, router } = await open("/jobs");
    const hook = vi.fn();
    useAuthStore().onSignOut(hook);
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    await wrapper.find('[data-testid="sign-out"]').trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    expect(shell.logout).toHaveBeenCalledTimes(1);
    expect(hook).toHaveBeenCalledTimes(1);
    expect(vi.mocked(shell.logout).mock.invocationCallOrder[0]).toBeLessThan(hook.mock.invocationCallOrder[0] ?? 0);
  });

  it("still signs out locally when the server call fails", async () => {
    vi.mocked(shell.logout).mockRejectedValue(new Error("down"));
    const { wrapper, router } = await open("/jobs");
    await wrapper.find('[data-testid="account-menu"]').trigger("click");
    await wrapper.find('[data-testid="sign-out"]').trigger("click");
    await until(() => expect(router.currentRoute.value.name).toBe("login"));
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});

describe("the top bar", () => {
  it("marks the count of the current tab as primary and the Alerts count as warn", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group(), group()] });
    const { wrapper } = await open("/jobs");
    const jobs = wrapper.find('[data-testid="tab-count-jobs"]');
    const alerts = wrapper.find('[data-testid="tab-count-alerts"]');
    expect(jobs.classes()).toContain("tab-count--current");
    expect(jobs.classes()).not.toContain("tab-count--warn");
    expect(alerts.classes()).toContain("tab-count--warn");
    expect(alerts.classes()).not.toContain("tab-count--current");
  });

  it("moves the primary count with the current tab", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [group()] });
    const { wrapper, router } = await open("/jobs");
    await router.push("/alerts");
    await flushPromises();
    expect(wrapper.find('[data-testid="tab-count-jobs"]').classes()).not.toContain("tab-count--current");
    // Alerts keeps its warn pill, current or not
    expect(wrapper.find('[data-testid="tab-count-alerts"]').classes()).not.toContain("tab-count--current");
    expect(wrapper.find('[data-testid="tab-count-alerts"]').classes()).toContain("tab-count--warn");
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

  it("keeps the current tab in view: on a tab switch, and again when the counts arrive and widen the tabs before it", async () => {
    const shown: string[] = [];
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = function (this: HTMLElement) {
      shown.push(this.textContent?.trim() ?? "");
    };
    let answer: (value: Stats) => void = () => undefined;
    vi.mocked(shell.getStats).mockReturnValue(new Promise<Stats>((resolve) => (answer = resolve)));
    try {
      const { router } = await open("/test");
      expect(shown).toEqual(["Test"]);
      answer(stats());
      await flushPromises();
      expect(shown).toEqual(["Test", "Test"]);
      await router.push("/settings");
      await flushPromises();
      expect(shown).toEqual(["Test", "Test", "Settings"]);
    } finally {
      HTMLElement.prototype.scrollIntoView = original;
    }
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
      label: true,
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

  it("puts the submeta line under the subtitle and aligns the header to the top, only when it is given", () => {
    const w = mountHeader({ submeta: "Updated 10:00:00", actions: "<button>Refresh</button>" });
    expect(w.find(".page-header__text .page-header__submeta").text()).toBe("Updated 10:00:00");
    expect(w.classes()).toContain("page-header--top");
    const plain = mountHeader({ actions: "<button>Refresh</button>" });
    expect(plain.find(".page-header__submeta").exists()).toBe(false);
    expect(plain.classes()).not.toContain("page-header--top");
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

  // The page alone (no App around it), so it stays mounted after a successful sign-in has changed the route.
  async function mountLogin() {
    sessionStorage.clear();
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createAppRouter(createMemoryHistory());
    await router.push("/login");
    wrapper = mount(LoginView, { global: { plugins: [pinia, router] }, attachTo: document.body });
    await flushPromises();
    return wrapper;
  }

  it("clears the password after a failed sign-in, from the field and from the page's state", async () => {
    vi.mocked(shell.login).mockRejectedValue(
      new ApiError(401, { error: { code: "auth.invalid", message: "bad", param: null } }),
    );
    const login = await mountLogin();
    await submit(login, "owner", "secret-one");
    expect(shell.login).toHaveBeenCalledWith("owner", "secret-one");
    expect((login.find('input[name="password"]').element as HTMLInputElement).value).toBe("");
    expect((login.find('input[name="username"]').element as HTMLInputElement).value).toBe("owner");
    expect(login.find(".reveal").attributes("aria-pressed")).toBe("false");
  });

  it("clears the password after a successful sign-in too", async () => {
    vi.mocked(shell.login).mockResolvedValue({ token: "tok", expires_at: 1 });
    const login = await mountLogin();
    await login.find(".reveal").trigger("click");
    await submit(login, "owner", "secret-two");
    expect(sessionStorage.getItem(TOKEN_KEY)).toBe("tok");
    const field = login.find('input[name="password"]');
    expect((field.element as HTMLInputElement).value).toBe("");
    expect(field.attributes("type")).toBe("password");
  });

  it("draws no error box until a sign-in fails, then an alert linked to the invalid password field", async () => {
    vi.mocked(shell.login).mockRejectedValue(
      new ApiError(401, { error: { code: "auth.invalid", message: "bad", param: null } }),
    );
    const { wrapper } = await open("/login", false);
    const pass = wrapper.find('input[name="password"]');
    expect(wrapper.find("#loginError").exists()).toBe(false);
    expect(pass.attributes("aria-invalid")).toBeUndefined();
    expect(pass.attributes("aria-describedby")).toBeUndefined();
    await submit(wrapper, "owner", "nope");
    const box = wrapper.find("#loginError");
    expect(box.attributes("role")).toBe("alert");
    expect(box.text()).toBe("Wrong username or password");
    expect(pass.attributes("aria-invalid")).toBe("true");
    expect(pass.attributes("aria-describedby")).toBe("loginError");
    expect(pass.classes()).toContain("field-input--invalid");
  });

  it("shows and hides the password with a pressed button", async () => {
    const { wrapper } = await open("/login", false);
    const pass = wrapper.find('input[name="password"]');
    const reveal = wrapper.find(".reveal");
    expect(pass.attributes("type")).toBe("password");
    expect(reveal.attributes("aria-label")).toBe("Show password");
    expect(reveal.attributes("aria-pressed")).toBe("false");
    await reveal.trigger("click");
    expect(pass.attributes("type")).toBe("text");
    expect(reveal.attributes("aria-label")).toBe("Hide password");
    expect(reveal.attributes("aria-pressed")).toBe("true");
    await reveal.trigger("click");
    expect(pass.attributes("type")).toBe("password");
  });

  it("has its own theme toggle and the LAN-only footer, and no shell", async () => {
    const { wrapper } = await open("/login", false);
    expect(wrapper.find("header").exists()).toBe(false);
    expect(wrapper.find('[data-testid="theme-toggle"]').attributes("aria-label")).toBe("Switch theme");
    await wrapper.find('[data-testid="theme-toggle"]').trigger("click");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(wrapper.find(".login-foot").text()).toBe("Ghidorah admin · LAN only");
  });

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
