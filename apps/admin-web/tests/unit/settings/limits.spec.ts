import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { ApiError } from "@/api/client";
import * as api from "@/api/settings";
import * as shell from "@/api/shell";
import ContainerLimits from "@/components/settings/ContainerLimits.vue";
import SettingField from "@/components/settings/SettingField.vue";
import ValueTable from "@/components/settings/ValueTable.vue";
import { useScopeStore } from "@/stores/scope";
import SettingsView from "@/views/SettingsView.vue";

vi.mock("@/api/settings");
vi.mock("@/api/shell", () => ({ getModels: vi.fn(), getProvidersSummary: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const NOW = Date.UTC(2026, 9, 20, 12, 0, 0);

const items = (): api.SettingItem[] => [
  {
    key: "dola.browser_slots",
    group: "dola",
    basic: true,
    type: "int",
    value: 4,
    source: "default",
    default: 4,
    description: "Browser sessions at once (live).",
    allowed: "1–12",
  },
  {
    key: "billing_label",
    group: "core",
    basic: false,
    type: "str",
    value: "relay",
    source: "default",
    default: "relay",
    description: "A label",
    allowed: null,
  },
];
const fixed = (): api.FixedLimit[] => [
  { group: "core", name: "jobs.engine.MAX_INFLIGHT", value: "32 jobs", reason: "submits one worker runs at once" },
  { group: "core", name: "leases.LEASE_TTL_S", value: "60 s", reason: "a lease not renewed is gone" },
  { group: "dola", name: "caps.max_attempts", value: "3 attempts", reason: "counted attempts before a job fails" },
];
const bootRows = (): api.EnvOnlyItem[] => [
  { name: "RELAY_BIND", value: "0.0.0.0:8100" },
  { name: "RELAY_CHROME_PATH", value: null },
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
    env_only: bootRows(),
    fixed: fixed(),
    containers: containers(),
  }));
  vi.mocked(api.getPrices).mockResolvedValue({ items: [] });
  vi.mocked(shell.getModels).mockResolvedValue({ data: [] });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the allowed text", () => {
  const field = (item: api.SettingItem) =>
    mount(SettingField, { props: { item, draft: String(item.value), edited: false, busy: false } });

  it("shows the allowed text under the description of a field, and none for a setting with no bound", () => {
    const [bounded, free] = items();
    wrapper = field(bounded!);
    expect(wrapper.findAll(".allowed").map((l) => l.text())).toEqual(["Allowed: 1–12"]);
    expect(wrapper.text()).toContain("Browser sessions at once (live).");
    wrapper.unmount();
    wrapper = field(free!);
    expect(wrapper.find(".allowed").exists()).toBe(false);
  });

  it("shows the allowed text of every bounded setting on its tab", async () => {
    phone(true);
    await open();
    useScopeStore().provider = "dola";
    await flushPromises();
    expect(wrapper!.findAll("#settings .allowed").map((l) => l.text())).toEqual(["Allowed: 1–12"]);
    expect(wrapper!.find('#settings .field[data-key="dola.browser_slots"]').text()).toContain("Allowed: 1–12");
  });
});

const cells = (tr: { findAll: (s: string) => { text: () => string }[] }) => tr.findAll("th, td").map((c) => c.text());

describe("the fixed limits card", () => {
  const mountFixed = () =>
    mount(ValueTable, { props: { rows: fixed(), loading: false, error: null, label: "Fixed limits", reasons: true } });

  it("is a table of Name, Value and Reason with one band per group in the server's order", () => {
    phone(false);
    wrapper = mountFixed();
    expect(wrapper.findAll("thead th").map((h) => h.text())).toEqual(["Name", "Value", "Reason"]);
    expect(wrapper.findAll("tr.set-table__group").map((r) => r.text())).toEqual(["core", "dola"]);
    const rows = wrapper.findAll("tbody tr:not(.set-table__group)").map(cells);
    expect(rows).toEqual([
      ["jobs.engine.MAX_INFLIGHT", "32 jobs", "submits one worker runs at once"],
      ["leases.LEASE_TTL_S", "60 s", "a lease not renewed is gone"],
      ["caps.max_attempts", "3 attempts", "counted attempts before a job fails"],
    ]);
  });

  it("reads None when empty, shows the error with Retry and draws bars while the first read runs", async () => {
    phone(false);
    wrapper = mount(ValueTable, { props: { rows: [], loading: false, error: null, label: "Fixed limits" } });
    expect(wrapper.text()).toContain("None");
    await wrapper.setProps({ error: "Could not load the settings: boom." });
    expect(wrapper.find('[role="alert"]').text()).toContain("Could not load the settings: boom.");
    await wrapper.find("button.state__retry").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
    await wrapper.setProps({ error: null, loading: true });
    expect(wrapper.find(".set-skel").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("None");
  });

  it("is one block per row under a heading per group on a phone, with no table", () => {
    phone(true);
    wrapper = mountFixed();
    expect(wrapper.find("table").exists()).toBe(false);
    expect(wrapper.findAll("h3").map((h) => h.text())).toEqual(["core", "dola"]);
    const first = wrapper.findAll("ul")[0]!.findAll("li")[0]!;
    expect(first.findAll("span").map((c) => c.text())).toEqual([
      "jobs.engine.MAX_INFLIGHT",
      "32 jobs",
      "submits one worker runs at once",
    ]);
    expect(wrapper.findAll("li")).toHaveLength(3);
  });
});

describe("the boot-only card", () => {
  it("is a table of Name and Value, an unset value as a dash", async () => {
    phone(false);
    await open();
    const card = wrapper!.find("#boot");
    expect(card.findAll("thead th").map((h) => h.text())).toEqual(["Name", "Value"]);
    expect(card.findAll("tbody tr").map(cells)).toEqual([
      ["RELAY_BIND", "0.0.0.0:8100"],
      ["RELAY_CHROME_PATH", "-"],
    ]);
  });

  it("is one block per row on a phone", async () => {
    phone(true);
    await open();
    const card = wrapper!.find("#boot");
    expect(card.find("table").exists()).toBe(false);
    expect(card.findAll("li").map((li) => li.findAll("span").map((c) => c.text()))).toEqual([
      ["RELAY_BIND", "0.0.0.0:8100"],
      ["RELAY_CHROME_PATH", "-"],
    ]);
  });
});

describe("the containers card", () => {
  const mountContainers = () => mount(ContainerLimits, { props: { rows: containers(), loading: false, error: null } });

  it("has a column per service and a row per measure, then one per boot knob any service reported", () => {
    phone(false);
    wrapper = mountContainers();
    expect(wrapper.find("thead th").text()).toBe("Name");
    expect(wrapper.findAll("thead th[data-service]").map((h) => h.attributes("data-service"))).toEqual([
      "api",
      "worker-dola",
      "worker-old",
      "worker-odd",
    ]);
    const rows = wrapper.findAll("tbody tr").map(cells);
    expect(rows).toEqual([
      ["Container limits"],
      ["CPUs", "1.5", "8", "-", "no limit"],
      ["Memory", "2 GiB", "12 GiB", "-", "unreadable"],
      ["PIDs", "512", "2048", "-", "2048"],
      ["Shared memory (/dev/shm)", "64 MiB", "1 GiB", "-", "64 MiB"],
      ["Boot values"],
      ["WORKER_ID", "-", "worker-dola", "-", "-"],
      ["RELAY_CHROME_PATH", "-", "not set", "-", "-"],
    ]);
  });

  it("reads not set for a value a service reported unset, and dims the dash of a value it does not report", () => {
    phone(false);
    wrapper = mountContainers();
    const knob = wrapper.findAll("tbody tr").find((tr) => tr.find("th").text() === "RELAY_CHROME_PATH")!;
    const cell = (service: string) => knob.find(`td[data-service="${service}"]`);
    expect(cell("worker-dola").text()).toBe("not set");
    expect(cell("worker-dola").classes()).not.toContain("set-table__none");
    expect(cell("api").text()).toBe("-");
    expect(cell("api").classes()).toContain("set-table__none");
  });

  it("has no Boot values band when no service reports a boot value", () => {
    phone(false);
    wrapper = mount(ContainerLimits, { props: { rows: [containers()[0]!], loading: false, error: null } });
    expect(wrapper.findAll("tbody tr.set-table__group").map((tr) => tr.text())).toEqual(["Container limits"]);
  });

  it("says this process for the api and how long ago a worker reported, under the service name", () => {
    phone(false);
    wrapper = mountContainers();
    const head = (service: string) => wrapper!.find(`thead th[data-service="${service}"]`);
    expect(head("api").text()).toContain("this process, read at start");
    expect(head("worker-dola").text()).toContain("seen 2 min ago");
    expect(head("worker-dola").text()).not.toContain("reported");
    expect(head("worker-dola").find(".set-svc__when").attributes("title")).toMatch(/20\/10\/2026/);
  });

  it("says a worker that has not reported is not reported by this worker version, and marks a silent one stale", () => {
    phone(false);
    wrapper = mountContainers();
    const old = wrapper.find('thead th[data-service="worker-old"]');
    expect(old.text()).toContain("Not reported by this worker version");
    expect(old.text()).toContain("stale");
    expect(old.text()).toContain("seen 10 min ago");
    expect(wrapper.find('thead th[data-service="worker-dola"]').text()).not.toContain("Not reported");
  });

  it("marks only a silent worker stale: a fresh one and the api carry no chip", () => {
    phone(false);
    wrapper = mountContainers();
    const stale = wrapper
      .findAll("thead th[data-service]")
      .map((h) => [h.attributes("data-service"), h.find(".status-chip").exists()]);
    expect(stale).toEqual([
      ["api", false],
      ["worker-dola", false],
      ["worker-old", true],
      ["worker-odd", false],
    ]);
  });

  it("shows an unreadable limit and a limit that is not set as the server's exact words, apart from a dash", () => {
    phone(false);
    wrapper = mountContainers();
    const odd = wrapper
      .findAll("tbody tr:not(.set-table__group)")
      .map((tr) => tr.findAll('td[data-service="worker-odd"]')[0]!.text());
    expect(odd.slice(0, 4)).toEqual(["no limit", "unreadable", "2048", "64 MiB"]);
    const old = wrapper
      .findAll("tbody tr:not(.set-table__group)")
      .map((tr) => tr.findAll('td[data-service="worker-old"]')[0]!.text());
    expect(old.slice(0, 4)).toEqual(["-", "-", "-", "-"]);
  });

  it("is one block per service on a phone, with its stamp, its chip and its own boot knobs", () => {
    phone(true);
    wrapper = mountContainers();
    expect(wrapper.find("table").exists()).toBe(false);
    const worker = wrapper.find('section[data-service="worker-dola"]');
    expect(worker.text()).toContain("seen 2 min ago");
    expect(worker.findAll("li").map((li) => li.findAll("span").map((c) => c.text()))).toEqual([
      ["CPUs", "8"],
      ["Memory", "12 GiB"],
      ["PIDs", "2048"],
      ["Shared memory (/dev/shm)", "1 GiB"],
      ["WORKER_ID", "worker-dola"],
      ["RELAY_CHROME_PATH", "not set"],
    ]);
    expect(worker.findAll(".set-list__group").map((h) => h.text())).toEqual(["Container limits", "Boot values"]);
    expect(
      wrapper
        .find('section[data-service="api"]')
        .findAll(".set-list__group")
        .map((h) => h.text()),
    ).toEqual(["Container limits"]);
    const old = wrapper.find('section[data-service="worker-old"]');
    expect(old.text()).toContain("Not reported by this worker version");
    expect(old.find("ul").exists()).toBe(false);
    expect(old.text()).toContain("stale");
    expect(wrapper.find('section[data-service="api"]').text()).toContain("this process, read at start");
  });

  it("reads None when empty and shows the error with Retry", async () => {
    phone(false);
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
  const links = () => wrapper!.findAll('[data-testid="settings-card-core"] .glinks a');

  it("lists the five sections in the General card and moves the mark to Fixed limits on a click", async () => {
    phone(false);
    await open();
    expect(links().map((a) => a.text())).toEqual([
      "Change password",
      "Boot-only values",
      "Measured prices",
      "Fixed limits",
      "Containers",
    ]);
    vi.stubGlobal("scrollTo", vi.fn());
    await links()[3]!.trigger("click");
    expect(links().map((a) => a.attributes("aria-current"))).toEqual([
      undefined,
      undefined,
      undefined,
      "true",
      undefined,
    ]);
  });

  it("puts the settings first, then Change password ahead of the other sections", async () => {
    phone(false);
    await open();
    expect(wrapper!.findAll(".settings > section").map((c) => c.attributes("id"))).toEqual([
      "settings",
      "password",
      "boot",
      "prices",
      "limits",
      "containers",
    ]);
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
