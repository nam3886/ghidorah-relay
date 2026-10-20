import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { ApiError } from "@/api/client";
import * as api from "@/api/settings";
import ContainerLimits from "@/components/settings/ContainerLimits.vue";
import FixedLimits from "@/components/settings/FixedLimits.vue";
import SettingsTable from "@/components/settings/SettingsTable.vue";
import SettingsView from "@/views/SettingsView.vue";

vi.mock("@/api/settings");
vi.mock("@/api/shell", () => ({ getModels: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const NOW = Date.UTC(2026, 9, 20, 12, 0, 0);

const items = (): api.SettingItem[] => [
  {
    key: "dola.browser_slots",
    value: 4,
    source: "default",
    default: 4,
    description: "Browser sessions at once (live).",
    allowed: "1–12",
  },
  { key: "billing_label", value: "relay", source: "default", default: "relay", description: "A label", allowed: null },
];
const fixed = (): api.FixedLimit[] => [
  { group: "core", name: "jobs.engine.MAX_INFLIGHT", value: "32 jobs", reason: "submits one worker runs at once" },
  { group: "core", name: "leases.LEASE_TTL_S", value: "60 s", reason: "a lease not renewed is gone" },
  { group: "dola", name: "caps.max_attempts", value: "3 attempts", reason: "counted attempts before a job fails" },
];
const containers = (): api.ContainerRow[] => [
  {
    service: "api",
    provider: null,
    this_process: true,
    reported: true,
    stale: false,
    cpus: "1.5",
    memory: "2 GiB",
    pids: "512",
    shm: "64 MiB",
    boot: null,
    reported_ms: NOW - 3600_000,
    started_ms: NOW - 3600_000,
  },
  {
    service: "worker-dola",
    provider: "dola",
    this_process: false,
    reported: true,
    stale: false,
    cpus: "8",
    memory: "12 GiB",
    pids: "2048",
    shm: "1 GiB",
    boot: { WORKER_ID: "worker-dola", RELAY_CHROME_PATH: null },
    reported_ms: NOW - 120_000,
    started_ms: NOW - 7200_000,
  },
  {
    service: "worker-old",
    provider: "dola",
    this_process: false,
    reported: false,
    stale: true,
    cpus: null,
    memory: null,
    pids: null,
    shm: null,
    boot: null,
    reported_ms: NOW - 600_000,
    started_ms: NOW - 9000_000,
  },
  {
    service: "worker-odd",
    provider: "dola",
    this_process: false,
    reported: true,
    stale: false,
    cpus: "no limit",
    memory: "unreadable",
    pids: "2048",
    shm: "64 MiB",
    boot: null,
    reported_ms: NOW - 30_000,
    started_ms: NOW - 3600_000,
  },
];

let wrapper: VueWrapper | null = null;
function phone(isPhone: boolean): void {
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: isPhone && /max-width:\s*767/.test(q),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}
async function open() {
  setActivePinia(createPinia());
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/settings", name: "settings", component: { render: () => null } },
      { path: "/login", name: "login", component: { render: () => null } },
    ],
  });
  await router.push("/settings");
  wrapper = mount(SettingsView, { global: { plugins: [router] }, attachTo: document.body });
  await flushPromises();
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.mocked(api.getSettings).mockImplementation(async () => ({
    items: items(),
    env_only: [],
    fixed: fixed(),
    containers: containers(),
  }));
  vi.mocked(api.getPrices).mockResolvedValue({ items: [] });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the allowed text", () => {
  it("shows the allowed text under the description in the table, and none for a setting with no bound", () => {
    phone(false);
    wrapper = mount(SettingsTable, { props: { items: items(), loading: false, error: null, busy: false } });
    const lines = wrapper.findAll(".allowed").map((l) => l.text());
    expect(lines).toEqual(["Allowed: 1–12"]);
    expect(wrapper.find("tbody tr:first-child .first").text()).toContain("Allowed: 1–12");
    expect(wrapper.find("tbody tr:nth-child(2) .first").text()).not.toContain("Allowed");
  });

  it("shows the allowed text under the description in the phone list, and none for a setting with no bound", () => {
    phone(true);
    wrapper = mount(SettingsTable, { props: { items: items(), loading: false, error: null, busy: false } });
    expect(wrapper.findAll("ul.list li").length).toBe(2);
    expect(wrapper.findAll(".allowed").map((l) => l.text())).toEqual(["Allowed: 1–12"]);
    expect(wrapper.find("ul.list li:first-child").text()).toContain("Allowed: 1–12");
  });
});

describe("the fixed limits card", () => {
  it("lists one block per group in the server's order, each row with its name, value and reason", () => {
    wrapper = mount(FixedLimits, { props: { rows: fixed(), loading: false, error: null } });
    expect(wrapper.findAll("h3").map((h) => h.text())).toEqual(["core", "dola"]);
    const blocks = wrapper.findAll("section");
    expect(blocks[0]!.findAll("dt").map((d) => d.text())).toEqual(["jobs.engine.MAX_INFLIGHT", "leases.LEASE_TTL_S"]);
    expect(blocks[0]!.text()).toContain("32 jobs");
    expect(blocks[0]!.text()).toContain("submits one worker runs at once");
    expect(blocks[1]!.findAll("dt").map((d) => d.text())).toEqual(["caps.max_attempts"]);
    expect(blocks[1]!.text()).toContain("3 attempts");
  });

  it("reads None when empty, shows the error with Retry and draws bars while the first read runs", async () => {
    wrapper = mount(FixedLimits, { props: { rows: [], loading: false, error: null } });
    expect(wrapper.text()).toContain("None");
    await wrapper.setProps({ error: "Could not load the settings: boom." });
    expect(wrapper.find('[role="alert"]').text()).toContain("Could not load the settings: boom.");
    await wrapper.find("button.state__retry").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
    await wrapper.setProps({ error: null, loading: true });
    expect(wrapper.find(".set-skel").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("None");
  });
});

describe("the containers card", () => {
  it("says this process for the api and shows a worker's four texts with how long ago it reported", () => {
    wrapper = mount(ContainerLimits, { props: { rows: containers(), loading: false, error: null } });
    const api_ = wrapper.find('[data-service="api"]');
    expect(api_.text()).toContain("this process, read at start");
    expect(api_.text()).toContain("1.5");
    expect(api_.text()).toContain("512");
    const worker = wrapper.find('[data-service="worker-dola"]');
    expect(worker.text()).toContain("seen 2 min ago");
    expect(worker.text()).not.toContain("reported");
    expect(worker.find(".containers__when").attributes("title")).toMatch(/20\/10\/2026/);
    expect(worker.findAll("dd").map((d) => d.text())).toEqual(["8", "12 GiB", "2048", "1 GiB", "worker-dola", "-"]);
    expect(worker.findAll("dt").map((d) => d.text())).toEqual([
      "CPUs",
      "Memory",
      "PIDs",
      "Shared memory (/dev/shm)",
      "WORKER_ID",
      "RELAY_CHROME_PATH",
    ]);
  });

  it("says a worker that has not reported is not reported by this worker version, and marks a silent one stale", () => {
    wrapper = mount(ContainerLimits, { props: { rows: containers(), loading: false, error: null } });
    const old = wrapper.find('[data-service="worker-old"]');
    expect(old.text()).toContain("Not reported by this worker version");
    expect(old.find("dl").exists()).toBe(false);
    expect(old.text()).toContain("stale");
    expect(old.text()).toContain("seen 10 min ago");
  });

  it("marks only a silent worker stale: a fresh one and the api carry no chip", () => {
    wrapper = mount(ContainerLimits, { props: { rows: containers(), loading: false, error: null } });
    const stale = wrapper
      .findAll(".containers__block")
      .map((b) => [b.attributes("data-service"), b.find(".status-chip").exists()]);
    expect(stale).toEqual([
      ["api", false],
      ["worker-dola", false],
      ["worker-old", true],
      ["worker-odd", false],
    ]);
  });

  it("shows an unreadable limit and a limit that is not set as the server's exact words", () => {
    wrapper = mount(ContainerLimits, { props: { rows: containers(), loading: false, error: null } });
    const odd = wrapper.find('[data-service="worker-odd"]');
    const value = (label: string) =>
      odd
        .findAll(".set-leader__row")
        .find((r) => r.find("dt").text() === label)!
        .find("dd")
        .text();
    expect(value("CPUs")).toBe("no limit");
    expect(value("Memory")).toBe("unreadable");
    expect(value("PIDs")).toBe("2048");
    expect(value("Shared memory (/dev/shm)")).toBe("64 MiB");
  });

  it("reads None when empty and shows the error with Retry", async () => {
    wrapper = mount(ContainerLimits, { props: { rows: [], loading: false, error: null } });
    expect(wrapper.text()).toContain("None");
    await wrapper.setProps({ error: "Could not load the settings: boom." });
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    await wrapper.find("button.state__retry").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
    await wrapper.setProps({ error: null, loading: true });
    expect(wrapper.find(".set-skel").exists()).toBe(true);
  });
});

describe("the page", () => {
  it("lists the six sections and moves the mark to Fixed limits on a click", async () => {
    phone(false);
    await open();
    const nav = wrapper!.find('nav[aria-label="Settings sections"]');
    expect(nav.findAll("a").map((a) => a.text())).toEqual([
      "Settings",
      "Boot-only values",
      "Measured prices",
      "Change password",
      "Fixed limits",
      "Containers",
    ]);
    vi.stubGlobal("scrollTo", vi.fn());
    await nav.findAll("a")[4]!.trigger("click");
    const marks = wrapper!.findAll('nav[aria-label="Settings sections"] a').map((a) => a.attributes("aria-current"));
    expect(marks).toEqual([undefined, undefined, undefined, undefined, "true", undefined]);
  });

  it("shows the Fixed limits rows that the settings read brought", async () => {
    phone(false);
    await open();
    const card = wrapper!.find("#limits");
    expect(card.find("h2").text()).toBe("Fixed limits");
    expect(card.text()).toContain("Constants in the code that shape behaviour.");
    expect(card.text()).toContain("jobs.engine.MAX_INFLIGHT");
    expect(card.text()).toContain("caps.max_attempts");
  });

  it("shows the Containers rows that the settings read brought", async () => {
    phone(false);
    await open();
    const card = wrapper!.find("#containers");
    expect(card.find("h2").text()).toBe("Containers");
    expect(card.text()).toContain("worker-dola");
    expect(card.text()).toContain("12 GiB");
  });

  it("states what a saved value does in the settings card note", async () => {
    phone(false);
    await open();
    expect(wrapper!.find("#settings").text()).toContain(
      "A saved value is kept in the database. Reset returns to the default.",
    );
  });

  it("shows the failed read in both new cards, with Retry", async () => {
    phone(false);
    vi.mocked(api.getSettings).mockRejectedValue(
      new ApiError(500, { error: { code: "server.error", message: "db down" } }),
    );
    await open();
    for (const id of ["#limits", "#containers"]) {
      expect(wrapper!.find(id).find('[role="alert"]').text()).toContain("Could not load the settings");
    }
  });
});
