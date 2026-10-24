import { DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as alerts from "@/api/alerts";
import type { AlertDetail, AlertGroup, UnclearItem } from "@/api/alerts";
import { ApiError } from "@/api/client";
import * as shell from "@/api/shell";
import { EMPTY_WORDING_TITLE } from "@/components/alerts/alerts-model";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";
import AlertsView from "@/views/AlertsView.vue";

vi.mock("@/api/alerts", () => ({
  listAlertGroups: vi.fn(),
  getAlertGroup: vi.fn(),
  acknowledgeGroup: vi.fn(),
  getResolvedToday: vi.fn(),
  changeAlertRule: vi.fn(),
  listAlertRules: vi.fn(),
}));
const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/api/shell", () => ({ getStats: vi.fn(), getOpenAlertGroups: vi.fn(), getModels: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";

const group = (over: Partial<AlertGroup> = {}): AlertGroup => ({
  kind: "clamp",
  group: "g1",
  title: "Clamp on fake",
  severity: "error",
  provider: "fake",
  first_ms: 1,
  last_ms: 2,
  count: 3,
  open_count: 2,
  keys: 1,
  newest_id: 41,
  ...over,
});
const detail = (over: Partial<AlertDetail> = {}): AlertDetail => ({ ...group(), items: [], ...over });
const unclearItem = (over: Partial<UnclearItem> = {}): UnclearItem => ({
  id: 1,
  at_ms: 1,
  text: PLANTED,
  job_id: "abcdef0123456789",
  job_status: "done",
  job_error_code: null,
  account_label: null,
  key_id: "k1234567890",
  job_title: "A title",
  key_label: "Shop A",
  acknowledged_ms: null,
  acknowledged_by: null,
  ...over,
});

const list = vi.mocked(alerts.listAlertGroups);
let wrapper: VueWrapper | null = null;
let pinia = createPinia();

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
const settle = async () => {
  await flushPromises();
  await flushPromises();
};

async function open() {
  wrapper = mount(AlertsView, {
    global: { plugins: [pinia], stubs: { transition: false } },
    attachTo: document.body,
  });
  await settle();
  return wrapper;
}
const listButtons = (w: VueWrapper, text: string) =>
  w
    .findAll("button")
    // not in the detail, and not in el-table's hidden measuring copy of the columns (it renders them with an empty row)
    .filter(
      (b) =>
        (b.text() === text || b.attributes("aria-label") === text) &&
        !b.element.closest('[data-testid="alert-detail"], .hidden-columns'),
    );
// the detail is a drawer in <body>, outside the view's own wrapper
const db = () => new DOMWrapper(document.body);
// the drawer's root stays in <body> once it was drawn; its body exists only while it is open
const isOpen = () => db().find(".rl-drawer__body").exists();
const dr = () => db().find('[data-testid="alert-detail"]');
const detailButton = (text: string) =>
  dr()
    .findAll("button")
    .find((b) => b.text().startsWith(text))!;
const closeButton = () => db().find('button[aria-label="Close · Esc"]');
const rowsOf = (w: VueWrapper) => w.findAll('[data-testid="alert-group"]');
const titleButtons = (w: VueWrapper) => w.findAll('button[aria-label^="Details of"]');
const openFirst = async (w: VueWrapper) => {
  await titleButtons(w)[0]!.trigger("click");
  await settle();
};
// the drawer leaves with a transition and its body is destroyed after it
const goneAfterLeave = async () => {
  await new Promise((r) => setTimeout(r, 400));
  await flushPromises();
  return !isOpen();
};
const refresh = (w: VueWrapper) => listButtons(w, "Refresh")[0]!;
// The Status and Kind filters: FilterSelect fields, named by the label above them; their list opens in the body.
const filter = (w: VueWrapper, name: string) =>
  w
    .findAll('[role="combobox"]')
    .find((b) => document.getElementById(b.attributes("aria-labelledby") ?? "")?.textContent?.trim() === name)!;
const shown = (w: VueWrapper, name: string) => filter(w, name).find(".is-current").text();
const optionsOf = async (w: VueWrapper, name: string) => {
  await filter(w, name).trigger("click");
  const words = [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent?.trim());
  await filter(w, name).trigger("click");
  return words;
};

// happy-dom answers matchMedia for a 1024 px window (a tablet): each spec says which width it wants (desktop by default).
function showAt(width: number): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("max-width: 767px")
      ? width <= 767
      : query.includes("min-width: 768px")
        ? width >= 768 && width <= 1279
        : false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

beforeEach(() => {
  showAt(1440);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  pinia = createPinia();
  setActivePinia(pinia);
  for (const fn of [...Object.values(alerts), ...Object.values(shell), ...Object.values(notify)]) {
    (fn as ReturnType<typeof vi.fn>).mockReset();
  }
  list.mockResolvedValue({ items: [group()] });
  vi.mocked(shell.getStats).mockResolvedValue({
    jobs: { queued: 0, in_progress: 0, done: 0, failed: 0, pre_job_failures_24h: 0 },
    made: { videos: 0, images: 0 },
    accounts: { usable: 1, available: 1, dead: 0, disabled: 0 },
    keys: 1,
    alerts: [{ kind: "accounts_low", severity: "warning", message: "only 1 usable account" }],
  });
  vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [] });
  vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail());
  vi.mocked(alerts.getResolvedToday).mockResolvedValue({ count: 0, day_start_ms: 0, tz: "UTC", items: [] });
  push.mockReset();
  delete (window as unknown as { __pwned?: number }).__pwned;
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the provider strip", () => {
  beforeEach(() => {
    list.mockResolvedValue({
      items: [
        group({ group: "a", provider: "dola", title: "dola clamp" }),
        group({ group: "b", provider: "meta", title: "meta reply" }),
        group({ group: "c", provider: "dola", title: "dola drift" }),
      ],
    });
    vi.mocked(shell.getStats).mockResolvedValue({
      jobs: { queued: 0, in_progress: 0, done: 0, failed: 0, pre_job_failures_24h: 0 },
      made: { videos: 0, images: 0 },
      accounts: { usable: 1, available: 1, dead: 0, disabled: 0 },
      keys: 1,
      alerts: [
        { kind: "accounts_low", severity: "warning", message: "meta has 1 usable account", provider: "meta" },
        { kind: "disk_low", severity: "error", message: "2.0 GB free", provider: null },
      ],
    });
  });

  it("the rows and live conditions carry their provider tile, none is the relay", async () => {
    const w = await open();
    const rows = rowsOf(w);
    expect(rows[0]!.classes()).toContain("p-dola");
    expect(rows[0]!.find(".pchip").text()).toBe("dola");
    const live = w.findAll('[data-testid="alert-live"]');
    expect(live.map((li) => [li.find(".pchip").text(), li.find(".g-title").text()])).toEqual([
      ["relay", expect.stringMatching(/2\.0 GB free$/)],
      ["meta", "pool: meta has 1 usable account"],
    ]);
    expect(live[0]!.classes()).toContain("p-other");
  });

  it("a provider picked on another tab narrows nothing (Alerts has no strip)", async () => {
    const w = await open();
    const before = rowsOf(w).length;
    useScopeStore().provider = "dola";
    await settle();
    expect(rowsOf(w)).toHaveLength(before);
  });
});

describe("the lists", () => {
  it("asks for the open groups, 200 of them, with the live conditions beside", async () => {
    const w = await open();
    expect(list).toHaveBeenCalledWith("open", "", 200);
    expect(w.find('[data-testid="alert-live"]').text()).toContain("pool: only 1 usable account");
    expect(rowsOf(w)[0]!.text()).toContain("Clamp on fake");
    // the summary counts what is open: the clamp (critical) and the live pool condition (warning)
    expect(w.find('[data-testid="alerts-summary"]').text()).toBe("2 open: 1 critical, 1 warning.");
  });

  it("draws titles and live messages as text, rewrites the empty wording and marks one customer", async () => {
    list.mockResolvedValue({
      items: [
        group({ group: "a", kind: "unclear_reply", title: PLANTED, keys: 1 }),
        group({ group: "b", kind: "unclear_reply", title: EMPTY_WORDING_TITLE, keys: 1 }),
        group({ group: "c", kind: "unclear_reply", title: "shared words", keys: 2 }),
      ],
    });
    vi.mocked(shell.getStats).mockResolvedValue({
      ...(await shell.getStats("")),
      alerts: [{ kind: PLANTED, severity: "error", message: PLANTED }],
    });
    const w = await open();
    const rows = rowsOf(w).map((r) => r.text());
    expect(rows[0]).toContain(PLANTED);
    expect(rows[0]).toContain("One customer only: may be the customer’s own words.");
    expect(rows[1]).toContain("API read failure: no reply text was read");
    expect(rows[1]).not.toContain("One customer only");
    expect(rows[2]).not.toContain("One customer only");
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("sorts the groups into the Critical, Warnings and Info cards, in that order", async () => {
    list.mockResolvedValue({
      items: [
        group({ group: "a", severity: "error" }),
        group({ group: "b", severity: "warning" }),
        group({ group: "c", severity: "info" }),
      ],
    });
    const w = await open();
    // the Resolved card follows the severity cards
    const cards = w.findAll("section[aria-label]").map((c) => c.attributes("aria-label"));
    expect(cards.filter((c) => c !== "Resolved")).toEqual(["Critical", "Warnings", "Info"]);
    expect(w.find('[data-testid="alerts-card-error"]').findAll('[data-testid="alert-group"]')).toHaveLength(1);
    expect(w.find('[data-testid="alerts-card-info"]').findAll('[data-testid="alert-group"]')).toHaveLength(1);
  });

  it("dims a group with nothing open, marks the opened one, and counts the groups", async () => {
    list.mockResolvedValue({
      items: [group({ group: "a", open_count: 4 }), group({ group: "b", open_count: 0 })],
    });
    const w = await open();
    const rows = rowsOf(w);
    expect(rows.map((r) => r.classes().includes("a-dim"))).toEqual([false, true]);
    expect(w.find('[data-testid="alerts-count"]').text()).toBe("2 groups");
    await openFirst(w);
    expect(rowsOf(w).map((r) => r.classes().includes("a-selected"))).toEqual([true, false]);
    list.mockResolvedValue({ items: [group()] });
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-count"]').text()).toBe("1 group");
  });

  it("a group says its open rows and keys; one with nothing open says Acknowledged", async () => {
    list.mockResolvedValue({
      items: [group({ group: "a", open_count: 4, count: 6 }), group({ group: "b", open_count: 0 })],
    });
    const w = await open();
    const rows = rowsOf(w);
    expect(rows[0]!.find(".g-kind").text()).toBe("clamp · 4 of 6 rows open · 1 key");
    expect(rows[0]!.find(".state").exists()).toBe(false);
    expect(rows[1]!.find(".state").text()).toBe("Acknowledged");
  });

  it("names the live conditions as the app says them, with no Acknowledge", async () => {
    const w = await open();
    const item = w.find('[data-testid="alert-live"]');
    expect(item.text()).toContain("Computed now: it cannot be acknowledged and clears when the condition does.");
    // its one button mutes it; nothing acknowledges it
    expect(item.findAll("button").map((b) => b.text())).toEqual(["Mute 1 h"]);
    vi.mocked(shell.getStats).mockResolvedValue({
      ...(await shell.getStats("")),
      alerts: [],
    });
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-live"]').exists()).toBe(false);
  });

  it("an empty list says so, and a provider picked on another tab is no filter here", async () => {
    list.mockResolvedValue({ items: [] });
    vi.mocked(shell.getStats).mockResolvedValue({ ...(await shell.getStats("")), alerts: [] });
    const w = await open();
    expect(w.find('[data-testid="alerts-empty"]').text()).toContain("No alerts");
    useScopeStore().provider = "dola";
    await settle();
    expect(w.find('[data-testid="alerts-empty"]').text()).toContain("No alerts");
  });

  it("says the list is cut at 200 groups, and only then", async () => {
    const w = await open();
    expect(w.find('[data-testid="alerts-more"]').exists()).toBe(false);
    list.mockResolvedValue({ items: Array.from({ length: 200 }, (_, i) => group({ group: `g${i}` })) });
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-more"]').text()).toBe(
      "Showing the newest 200 groups; older groups are not listed.",
    );
  });
});

describe("the filters", () => {
  const choose = async (w: VueWrapper, name: string, word: string) => {
    await filter(w, name).trigger("click");
    const option = [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(
      (o) => o.textContent?.trim() === word,
    )!;
    option.click();
    await settle();
  };

  it("the selects offer the canvas words: Status All, Open, Acknowledged; Kind All and the three kinds", async () => {
    const w = await open();
    expect(await optionsOf(w, "Status")).toEqual(["All", "Open", "Acknowledged"]);
    expect(await optionsOf(w, "Kind")).toEqual(["All", "clamp", "drift", "unclear_reply"]);
    expect(shown(w, "Status")).toBe("Open");
    expect(shown(w, "Kind")).toBe("All");
    // no native select: the field variant of the shared FilterSelect
    expect(w.find("select").exists()).toBe(false);
  });

  it("choosing the Kind and then All again sends no kind", async () => {
    const w = await open();
    await choose(w, "Kind", "drift");
    expect(list).toHaveBeenLastCalledWith("open", "drift", 200);
    await choose(w, "Kind", "All");
    expect(list).toHaveBeenLastCalledWith("open", "", 200);
  });

  it("send the Status and then the Kind to the server, each in its own place", async () => {
    const w = await open();
    await choose(w, "Status", "Acknowledged");
    expect(list).toHaveBeenLastCalledWith("acknowledged", "", 200);
    expect(shown(w, "Status")).toBe("Acknowledged");
    await choose(w, "Kind", "drift");
    expect(list).toHaveBeenLastCalledWith("acknowledged", "drift", 200);
    await choose(w, "Status", "All");
    expect(list).toHaveBeenLastCalledWith("all", "drift", 200);
  });
});

describe("the generation guard", () => {
  it("a slow first answer that arrives after the second is dropped", async () => {
    const w = await open();
    // a Refresh that is slow, then Details of a group (Refresh itself is locked while it loads)
    const slow = deferred<{ items: AlertGroup[] }>();
    list.mockReturnValueOnce(slow.promise);
    await refresh(w).trigger("click");
    list.mockResolvedValueOnce({ items: [group({ group: "second", title: "second answer" })] });
    await openFirst(w);
    expect(w.text()).toContain("second answer");
    slow.resolve({ items: [group({ group: "first", title: "first answer" })] });
    await settle();
    expect(w.text()).toContain("second answer");
    expect(w.text()).not.toContain("first answer");
  });

  it("a slow first failure that arrives after the second answer shows nothing", async () => {
    const w = await open();
    const slow = deferred<{ items: AlertGroup[] }>();
    list.mockReturnValueOnce(slow.promise);
    await refresh(w).trigger("click");
    list.mockResolvedValueOnce({ items: [group({ title: "second answer" })] });
    await openFirst(w);
    slow.reject(new ApiError(500, { error: { message: "old failure" } }));
    await settle();
    expect(w.find('[data-testid="alerts-error"]').exists()).toBe(false);
    expect(w.text()).toContain("second answer");
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("the newest failure is shown", async () => {
    const w = await open();
    list.mockRejectedValueOnce(new ApiError(500, { error: { message: "the list failed" } }));
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-error"]').text()).toContain("the list failed");
    expect(notify.error).toHaveBeenCalledWith("the list failed");
  });

  it("the failed list is worded as the canvas words one: who answered what", async () => {
    const w = await open();
    list.mockRejectedValueOnce(new ApiError(502, { error: { message: "bad gateway" } }));
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-error-text"]').text()).toBe(
      "Could not load the alerts: the relay answered 502 (bad gateway).",
    );
    list.mockRejectedValueOnce(new Error("no answer"));
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-error-text"]').text()).toBe(
      "Could not load the alerts: network error, please try again.",
    );
  });

  it("while a view loads, Acknowledge is locked but the filters are not", async () => {
    const w = await open();
    const slow = deferred<{ items: AlertGroup[] }>();
    list.mockReturnValueOnce(slow.promise);
    await refresh(w).trigger("click");
    await settle();
    expect(listButtons(w, "Acknowledge")[0]!.attributes("disabled")).toBeDefined();
    expect(w.findAll('[role="combobox"]')).toHaveLength(2);
    expect(w.findAll('[role="combobox"]:disabled')).toHaveLength(0);
    slow.resolve({ items: [group()] });
    await settle();
    expect(listButtons(w, "Acknowledge")[0]!.attributes("disabled")).toBeUndefined();
  });
});

describe("the detail", () => {
  it("opens from the title button, shows the outcomes and opens a job through the ui store", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({
        kind: "unclear_reply",
        group: "words",
        title: PLANTED,
        items: [unclearItem()],
        outcomes: { done: 1, failed: 2, open: 0 },
        failed_codes: { "content.refused": 2 },
      }),
    );
    const w = await open();
    await openFirst(w);
    expect(alerts.getAlertGroup).toHaveBeenCalledWith({ kind: "clamp", group: "g1" }, 100);
    const panel = dr();
    expect(db().find('[data-testid="alert-detail-title"]').text()).toBe(`Fake replied “${PLANTED}”`);
    expect(panel.find('[data-testid="alert-outcomes"]').text()).toBe("1 delivered after the reply, 2 refunded.");
    expect(panel.find("img").exists()).toBe(false);
    await panel
      .findAll("button")
      .find((b) => b.text() === "A title")!
      .trigger("click");
    // the job opens once the detail drawer has left
    expect(await goneAfterLeave()).toBe(true);
    expect(useUiStore(pinia).openJobId).toBe("abcdef0123456789");
  });

  it("Refresh keeps the open detail", async () => {
    const w = await open();
    await openFirst(w);
    await refresh(w).trigger("click");
    await settle();
    expect(isOpen()).toBe(true);
    expect(alerts.getAlertGroup).toHaveBeenCalledTimes(2);
  });

  it("a detail that cannot be read keeps the drawer open with the error inside, and the list stays whole", async () => {
    vi.mocked(alerts.getAlertGroup).mockRejectedValue(new ApiError(404, { error: { message: "no such group" } }));
    const w = await open();
    await openFirst(w);
    expect(isOpen()).toBe(true);
    expect(db().find('[data-testid="alert-detail-error"]').attributes("role")).toBe("alert");
    expect(db().find('[data-testid="alert-detail-error"]').text()).toBe("no such group");
    expect(dr().find("#alertDetail").exists()).toBe(false);
    expect(rowsOf(w)[0]!.text()).toContain("Clamp on fake");
    // closing it asks for the list again and drops the error
    await closeButton().trigger("click");
    await settle();
    expect(await goneAfterLeave()).toBe(true);
    await openFirst(w);
    expect(alerts.getAlertGroup).toHaveBeenCalledTimes(2);
  });

  it("a list read that fails while the group is asked for says so in the drawer, not Loading for ever", async () => {
    const w = await open();
    list.mockRejectedValueOnce(new ApiError(500, { error: { message: "the list failed" } }));
    await openFirst(w);
    expect(isOpen()).toBe(true);
    const alert = dr().find('[role="alert"]');
    expect(alert.exists()).toBe(true);
    expect(alert.text()).toContain("the list failed");
    expect(dr().find('[role="status"]').exists()).toBe(false);
  });

  it("a failed read stays in the drawer while it leaves, and a reopened group starts from a clean drawer", async () => {
    vi.mocked(alerts.getAlertGroup).mockRejectedValueOnce(new ApiError(404, { error: { message: "no such group" } }));
    const w = await open();
    await openFirst(w);
    await closeButton().trigger("click");
    // leaving: the error is still what the drawer says, not a flash of Loading
    expect(dr().find('[role="alert"]').exists()).toBe(true);
    expect(dr().find('[role="status"]').exists()).toBe(false);
    // the close's own reload answers while the drawer is still leaving: it asks for no group and changes nothing
    for (let i = 0; i < 12; i++) await Promise.resolve();
    expect(dr().find('[role="alert"]').exists()).toBe(true);
    expect(dr().find('[role="status"]').exists()).toBe(false);
    await settle();
    expect(await goneAfterLeave()).toBe(true);
    const slow = deferred<AlertDetail>();
    vi.mocked(alerts.getAlertGroup).mockReturnValue(slow.promise);
    await openFirst(w);
    expect(dr().find('[role="alert"]').exists()).toBe(false);
  });

  it("opening another group at once does not show the last group's title while it reads", async () => {
    const w = await open();
    await openFirst(w);
    expect(db().find('[data-testid="alert-detail-title"]').text()).toBe("Clamp on fake");
    await closeButton().trigger("click");
    const slow = deferred<AlertDetail>();
    vi.mocked(alerts.getAlertGroup).mockReturnValue(slow.promise);
    await titleButtons(w)[0]!.trigger("click");
    await settle();
    expect(db().find('[data-testid="alert-detail-title"]').text()).not.toBe("Clamp on fake");
    expect(dr().find('[role="status"]').exists()).toBe(true);
  });

  it("a job asked for and then a group opened again before the drawer left opens no job", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: [unclearItem()] }));
    const w = await open();
    await openFirst(w);
    await dr()
      .findAll("button")
      .find((b) => b.text() === "A title")!
      .trigger("click");
    await titleButtons(w)[0]!.trigger("click");
    await settle();
    await closeButton().trigger("click");
    expect(await goneAfterLeave()).toBe(true);
    expect(useUiStore(pinia).openJobId).toBeNull();
  });

  it("a job link does nothing while an acknowledge runs: the drawer stays", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: [unclearItem()] }));
    const slow = deferred<{ acknowledged: number }>();
    vi.mocked(alerts.acknowledgeGroup).mockReturnValue(slow.promise);
    const w = await open();
    await openFirst(w);
    await detailButton("Acknowledge").trigger("click");
    await settle();
    const jobLink = dr()
      .findAll("button")
      .find((b) => b.text() === "A title")!;
    expect(jobLink.attributes("disabled")).toBeDefined();
    // the button is off, and the view refuses the request as well
    w.findComponent({ name: "AlertDetailPanel" }).vm.$emit("open-job", "abcdef0123456789");
    await settle();
    expect(await goneAfterLeave()).toBe(false);
    expect(useUiStore(pinia).openJobId).toBeNull();
    slow.resolve({ acknowledged: 1 });
    await settle();
  });

  it("a phone's job links are off while an acknowledge runs, and on again after", async () => {
    showAt(390);
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: [unclearItem()] }));
    const slow = deferred<{ acknowledged: number }>();
    vi.mocked(alerts.acknowledgeGroup).mockReturnValue(slow.promise);
    const w = await open();
    await openFirst(w);
    const link = () =>
      dr()
        .findAll("button")
        .find((b) => b.text() === "A title")!;
    expect(link().attributes("disabled")).toBeUndefined();
    await detailButton("Acknowledge").trigger("click");
    await settle();
    expect(link().attributes("disabled")).toBeDefined();
    slow.resolve({ acknowledged: 1 });
    await settle();
    expect(link().attributes("disabled")).toBeUndefined();
  });

  it("when the group leaves the list and the drawer is closed, focus goes to the page's scroll area", async () => {
    const wrap = document.createElement("div");
    wrap.className = "app-scroll__wrap";
    wrap.tabIndex = -1;
    document.body.append(wrap);
    vi.mocked(alerts.acknowledgeGroup).mockResolvedValue({ acknowledged: 2 });
    const w = await open();
    const title = titleButtons(w)[0]!.element as HTMLElement;
    title.focus();
    await openFirst(w);
    // the acknowledge empties the Open list: the opener is gone
    list.mockResolvedValue({ items: [] });
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ open_count: 0 }));
    await detailButton("Acknowledge").trigger("click");
    await settle();
    expect(title.isConnected).toBe(false);
    await closeButton().trigger("click");
    await settle();
    expect(await goneAfterLeave()).toBe(true);
    expect(document.activeElement).toBe(wrap);
  });

  it("the footer's Acknowledge says how many rows it marks", async () => {
    const w = await open();
    await openFirst(w);
    // as drawn: Mute 1 h at the left, Open Jobs filtered and Acknowledge at the right
    expect(
      dr()
        .findAll(".rl-drawer__foot button")
        .map((b) => b.text()),
    ).toEqual(["Mute 1 h", "Open Jobs filtered", "Acknowledge 2 rows"]);
  });

  it("a tablet row opens the detail on a click beside its buttons", async () => {
    showAt(768);
    const w = await open();
    await w.find('[data-testid="alert-group"] .g-kind').trigger("click");
    await settle();
    expect(isOpen()).toBe(true);
  });

  it("shows a loading body while the read runs, then the detail; the heading never blanks", async () => {
    const slow = deferred<AlertDetail>();
    vi.mocked(alerts.getAlertGroup).mockReturnValue(slow.promise);
    const w = await open();
    await openFirst(w);
    expect(isOpen()).toBe(true);
    expect(dr().find('[role="status"]').text()).toContain("Loading");
    expect(dr().find("#alertDetail").exists()).toBe(false);
    expect(db().find('[data-testid="alert-detail-title"]').text()).not.toBe("");
    slow.resolve(detail());
    await settle();
    expect(dr().find('[role="status"]').exists()).toBe(false);
    expect(dr().find("#alertDetail").exists()).toBe(true);
    expect(db().find('[data-testid="alert-detail-title"]').text()).toBe("Clamp on fake");
  });

  it("closing keeps the last detail until the drawer is gone, and never draws the panel without one", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const w = await open();
    await openFirst(w);
    await closeButton().trigger("click");
    // no timer has run: the drawer is leaving and still draws what it drew
    expect(db().find('[data-testid="alert-detail-title"]').text()).toBe("Clamp on fake");
    expect(dr().find("#alertDetail").exists()).toBe(true);
    await settle();
    expect(await goneAfterLeave()).toBe(true);
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("the drawer's chip tells the group's severity", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ severity: "warning" }));
    const w = await open();
    await openFirst(w);
    expect(dr().find(".rl-drawer__sub [data-tone]").attributes("data-tone")).toBe("warn");
    expect(dr().find(".rl-drawer__sub [data-tone]").text()).toBe("Warning");
  });

  it("the title is a button named Details of ..., with no aria-expanded anywhere", async () => {
    const w = await open();
    const title = titleButtons(w)[0]!;
    expect(title.attributes("aria-label")).toBe("Details of Clamp on fake");
    expect(title.attributes("aria-haspopup")).toBe("dialog");
    expect(w.findAll("button").some((b) => b.text() === "Details")).toBe(false);
    expect(w.find('[data-testid="alert-group"] [aria-expanded]').exists()).toBe(false);
  });

  it("a click on the row opens the detail; a click on its Acknowledge does not", async () => {
    vi.mocked(alerts.acknowledgeGroup).mockResolvedValue({ acknowledged: 1 });
    const w = await open();
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    expect(alerts.getAlertGroup).not.toHaveBeenCalled();
    expect(isOpen()).toBe(false);
    await w.find('[data-testid="alert-group"] .g-kind').trigger("click");
    await settle();
    expect(alerts.getAlertGroup).toHaveBeenCalledWith({ kind: "clamp", group: "g1" }, 100);
    expect(isOpen()).toBe(true);
  });

  it("a click on the title button opens the detail once: the row does not open it a second time", async () => {
    const w = await open();
    await openFirst(w);
    expect(alerts.getAlertGroup).toHaveBeenCalledTimes(1);
  });

  it("a phone block opens the detail on a click beside its buttons", async () => {
    showAt(390);
    const w = await open();
    await w.find('[data-testid="alert-group"] .g-kind').trigger("click");
    await settle();
    expect(isOpen()).toBe(true);
  });

  it("opening a job closes this drawer first and opens the job once it is gone", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: [unclearItem()] }));
    const w = await open();
    await openFirst(w);
    await dr()
      .findAll("button")
      .find((b) => b.text() === "A title")!
      .trigger("click");
    // the drawer is leaving: the job does not open on top of it
    expect(useUiStore(pinia).openJobId).toBeNull();
    expect(await goneAfterLeave()).toBe(true);
    expect(useUiStore(pinia).openJobId).toBe("abcdef0123456789");
  });

  it("an unlabelled key is named by the prefix the API sends", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({ kind: "unclear_reply", items: [unclearItem({ key_label: "relay_ab12" })] }),
    );
    const w = await open();
    await openFirst(w);
    expect(dr().find("tbody tr").text()).toContain("relay_ab12");
  });

  it("says the detail is cut at 100 rows, and only then", async () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => unclearItem({ id: i + 1 }));
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(99) }));
    const w = await open();
    await openFirst(w);
    expect(isOpen()).toBe(true);
    expect(db().find('[data-testid="alert-detail-more"]').exists()).toBe(false);
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(100) }));
    await refresh(w).trigger("click");
    await settle();
    expect(db().find('[data-testid="alert-detail-more"]').text()).toBe(
      "Showing the newest 100 rows; older rows are not listed.",
    );
  });

  it("Close reloads the list", async () => {
    const w = await open();
    await openFirst(w);
    const before = list.mock.calls.length;
    await closeButton().trigger("click");
    await settle();
    expect(list.mock.calls.length).toBe(before + 1);
    expect(await goneAfterLeave()).toBe(true);
  });
});

describe("acknowledge", () => {
  it("from the list sends the newest row that was shown, then toasts, reloads and refreshes the banner", async () => {
    vi.mocked(alerts.acknowledgeGroup).mockResolvedValue({ acknowledged: 2 });
    const w = await open();
    const before = list.mock.calls.length;
    const banner = vi.mocked(shell.getOpenAlertGroups).mock.calls.length;
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    expect(alerts.acknowledgeGroup).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "clamp", group: "g1", newest_id: 41 }),
      41,
    );
    expect(notify.success).toHaveBeenCalledWith("Acknowledged 2 row(s)");
    expect(list.mock.calls.length).toBe(before + 1);
    expect(vi.mocked(shell.getOpenAlertGroups).mock.calls.length).toBe(banner + 1);
  });

  it("from the open detail sends the detail's own newest row", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ newest_id: 77 }));
    vi.mocked(alerts.acknowledgeGroup).mockResolvedValue({ acknowledged: 0 });
    const w = await open();
    await openFirst(w);
    await detailButton("Acknowledge").trigger("click");
    await settle();
    expect(vi.mocked(alerts.acknowledgeGroup).mock.calls[0]![1]).toBe(77);
    expect(notify.success).toHaveBeenCalledWith("Nothing new to acknowledge");
  });

  it("Acknowledge in the drawer footer and on the row go through one write each", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ newest_id: 77 }));
    vi.mocked(alerts.acknowledgeGroup).mockResolvedValue({ acknowledged: 1 });
    const w = await open();
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    expect(alerts.acknowledgeGroup).toHaveBeenCalledTimes(1);
    await openFirst(w);
    await detailButton("Acknowledge").trigger("click");
    await settle();
    expect(alerts.acknowledgeGroup).toHaveBeenCalledTimes(2);
    expect(notify.success).toHaveBeenCalledTimes(2);
  });

  it("a group with nothing open has no Acknowledge", async () => {
    list.mockResolvedValue({ items: [group({ open_count: 0 })] });
    const w = await open();
    expect(listButtons(w, "Acknowledge")).toHaveLength(0);
  });

  it("an acknowledge whose reload fails says both", async () => {
    vi.mocked(alerts.acknowledgeGroup).mockResolvedValue({ acknowledged: 1 });
    const w = await open();
    list.mockRejectedValueOnce(new ApiError(500, { error: { message: "reload broke" } }));
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    expect(notify.error).toHaveBeenCalledWith(
      "Acknowledged 1 row(s), but the list could not be reloaded: reload broke",
    );
  });

  // Everything the operator could press while a write is in flight, read from the page.
  const controls = (w: VueWrapper) => ({
    selects: w.findAll('[role="combobox"]:disabled').length,
    details: titleButtons(w)[0]!.attributes("disabled") !== undefined,
    listAck: listButtons(w, "Acknowledge")[0]!.attributes("disabled") !== undefined,
    detailAck: detailButton("Acknowledge").attributes("disabled") !== undefined,
  });
  const openDetailThenAcknowledge = async (answer: Promise<{ acknowledged: number }>) => {
    vi.mocked(alerts.acknowledgeGroup).mockReturnValue(answer);
    const w = await open();
    await openFirst(w);
    expect(controls(w)).toEqual({ selects: 0, details: false, listAck: false, detailAck: false });
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    return w;
  };

  it("while an acknowledge is in flight, the selects, the titles and both Acknowledge buttons are off, and X and Escape are inert", async () => {
    const slow = deferred<{ acknowledged: number }>();
    const w = await openDetailThenAcknowledge(slow.promise);
    expect(controls(w)).toEqual({ selects: 2, details: true, listAck: true, detailAck: true });
    // a click on the row opens nothing while the write runs
    const reads = vi.mocked(alerts.getAlertGroup).mock.calls.length;
    await w.find('[data-testid="alert-group"] .g-kind').trigger("click");
    await settle();
    expect(vi.mocked(alerts.getAlertGroup).mock.calls.length).toBe(reads);
    const loads = list.mock.calls.length;
    await closeButton().trigger("click");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await settle();
    expect(await goneAfterLeave()).toBe(false);
    expect(list.mock.calls.length).toBe(loads);
    slow.resolve({ acknowledged: 1 });
    await settle();
    expect(controls(w)).toEqual({ selects: 0, details: false, listAck: false, detailAck: false });
  });

  it("after a failed acknowledge every control is on again", async () => {
    const slow = deferred<{ acknowledged: number }>();
    const w = await openDetailThenAcknowledge(slow.promise);
    expect(controls(w).selects).toBe(2);
    slow.reject(new ApiError(404, { error: { message: "group gone" } }));
    await settle();
    expect(notify.error).toHaveBeenCalledWith("group gone");
    expect(controls(w)).toEqual({ selects: 0, details: false, listAck: false, detailAck: false });
  });

  it("a failed acknowledge toasts the error and does not reload or refresh the banner", async () => {
    vi.mocked(alerts.acknowledgeGroup).mockRejectedValue(new ApiError(404, { error: { message: "group gone" } }));
    const w = await open();
    const before = list.mock.calls.length;
    const banner = vi.mocked(shell.getOpenAlertGroups).mock.calls.length;
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    expect(notify.error).toHaveBeenCalledWith("group gone");
    expect(list.mock.calls.length).toBe(before);
    expect(vi.mocked(shell.getOpenAlertGroups).mock.calls.length).toBe(banner);
  });
});

describe("the detail as the design draws it", () => {
  const reply = (over: Partial<UnclearItem> = {}) => unclearItem({ text: "Sorry, I can’t create that video", ...over });
  const withReplies = () =>
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({
        kind: "unclear_reply",
        group: "words",
        title: "Dola replied “Sorry, I can’t create that video”",
        severity: "warning",
        provider: "dola",
        count: 6,
        open_count: 4,
        keys: 3,
        items: [
          reply({ id: 1, job_status: "done", account_label: "n11-03" }),
          reply({ id: 2, job_status: "failed", job_error_code: "dola.likeness_refused", account_label: "n11-06" }),
        ],
        outcomes: { done: 3, failed: 1, open: 0 },
        media: "video",
      }),
    );
  const openIt = async () => {
    withReplies();
    const w = await open();
    await openFirst(w);
    return dr();
  };

  it("lists the seven facts of the group, Open in the warning colour", async () => {
    const panel = await openIt();
    const rows = panel.findAll(".ledger__row").map((r) => [r.find("dt").text(), r.find("dd").text()]);
    expect(rows.map((r) => r[0])).toEqual(["Kind", "Provider", "First seen", "Last seen", "Rows", "Open", "Keys"]);
    expect(rows.find((r) => r[0] === "Kind")![1]).toBe("unclear_reply");
    expect(rows.find((r) => r[0] === "Rows")![1]).toBe("6");
    expect(rows.find((r) => r[0] === "Open")![1]).toBe("4");
    expect(panel.findAll(".ledger__value.tone-warn").map((v) => v.text())).toEqual(["4"]);
  });

  it("says what the reply is and what decides the outcome, and counts delivered and refunded", async () => {
    const panel = await openIt();
    expect(panel.find(".ad__sentence").text()).toBe(
      "The reply is stored as the video’s answer; the job outcome decides what the customer got.",
    );
    expect(panel.find('[data-testid="alert-outcomes"]').text()).toBe("3 delivered after the reply, 1 refunded.");
  });

  it("a failed job reads refunded, a done one done, and its code sits under the chip", async () => {
    const panel = await openIt();
    const rows = panel.findAll("tbody tr");
    expect(rows[0]!.find("[data-tone]").text()).toBe("done");
    expect(rows[0]!.find("[data-tone]").attributes("data-tone")).toBe("ok");
    expect(rows[1]!.find("[data-tone]").text()).toBe("refunded");
    expect(rows[1]!.find("[data-tone]").attributes("data-tone")).toBe("neutral");
    expect(rows[1]!.text()).toContain("dola.likeness_refused");
    // the compact table writes who and when only once a row is acknowledged
    expect(rows[0]!.find(".ad-acked").exists()).toBe(false);
    // the reply as the provider sent it, quoted above the facts
    expect(panel.find(".ad-quote").text()).toContain("The reply, as dola sent it");
  });

  it("an acknowledged row shows who and when instead of not yet", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({
        kind: "unclear_reply",
        items: [reply({ acknowledged_ms: 5, acknowledged_by: "admin" })],
        outcomes: { done: 1, failed: 0, open: 0 },
      }),
    );
    const w = await open();
    await openFirst(w);
    const text = dr().find("tbody tr").text();
    expect(text).toContain("admin,");
    expect(text).not.toContain("not yet");
  });

  it("a billing group has no reply sentence and no delivered line", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "clamp", items: [] }));
    const w = await open();
    await openFirst(w);
    const panel = dr();
    expect(panel.text()).not.toContain("The reply is stored");
    expect(panel.find('[data-testid="alert-outcomes"]').exists()).toBe(false);
  });

  it("an opened group with nothing open has no Acknowledge in its detail", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ open_count: 0 }));
    const w = await open();
    await openFirst(w);
    expect(
      dr()
        .findAll("button")
        .map((b) => b.text()),
    ).not.toContain("Acknowledge");
  });
});

describe("the tablet and the phone", () => {
  it("a tablet stacks a group: the severity and provider chips with the count and since, then the title and line", async () => {
    showAt(768);
    list.mockResolvedValue({ items: [group({ kind: "unclear_reply", provider: "dola", count: 6, open_count: 4 })] });
    const w = await open();
    const row = rowsOf(w)[0]!;
    expect(row.find(".sev").text()).toBe("Critical");
    expect(row.find(".pchip").text()).toBe("dola");
    expect(row.find(".av-item__right").text()).toMatch(/^×6 · since /);
    expect(row.find(".g-kind").text()).toBe("unclear_reply · 4 of 6 rows open · 1 key");
    expect(row.find(".av-meta").exists()).toBe(false);
  });

  it("a phone has the icon Refresh and the summary under the header, and one block per group", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [group({ group: "a", title: "first" }), group({ group: "b", title: "second" })] });
    const w = await open();
    expect(w.find("table").exists()).toBe(false);
    expect(w.find('button[aria-label="Refresh"]').exists()).toBe(true);
    expect(w.findAll("button").some((b) => b.text() === "Refresh")).toBe(false);
    expect(w.find('[data-testid="alerts-summary"]').text()).toBe("3 open: 2 critical, 1 warning.");
    expect(rowsOf(w)).toHaveLength(2);
    expect(rowsOf(w)[0]!.find(".av-item__right").text()).toBe("×3");
    expect(rowsOf(w)[0]!.text()).toContain("first");
  });

  it("a phone opens the detail in the drawer, with the group's title as its heading and Acknowledge in its footer", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [group({ group: "a", title: "first" }), group({ group: "b", title: "second" })] });
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ group: "a", title: "first", open_count: 2 }));
    const w = await open();
    await openFirst(w);
    expect(db().find('[data-testid="alert-detail-title"]').text()).toBe("first");
    expect(dr().find('button[aria-label="Back to Alerts"]').exists()).toBe(true);
    expect(dr().find(".ad-ack").text()).toBe("Acknowledge 2 rows");
  });

  it("a phone says the detail is cut at 100 rows, and only then", async () => {
    showAt(390);
    const rows = (n: number) => Array.from({ length: n }, (_, i) => unclearItem({ id: i + 1 }));
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(99) }));
    const w = await open();
    await openFirst(w);
    expect(isOpen()).toBe(true);
    expect(db().find('[data-testid="alert-detail-more"]').exists()).toBe(false);
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(100) }));
    await w.find('button[aria-label="Refresh"]').trigger("click");
    await settle();
    expect(db().find('[data-testid="alert-detail-more"]').text()).toBe(
      "Showing the newest 100 rows; older rows are not listed.",
    );
  });
});

describe("job titles and key labels in the detail", () => {
  const openDetail = async (items: UnclearItem[]) => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({ kind: "unclear_reply", items, outcomes: { done: items.length, failed: 0, open: 0 } }),
    );
    const w = await open();
    await openFirst(w);
    return dr().find("tbody tr");
  };

  it("the job is its title with the short id under it, the key is its label", async () => {
    const row = await openDetail([unclearItem()]);
    expect(row.find(".ad-job").text()).toBe("A title");
    expect(row.find(".ad-jobid").text()).toBe("abcdef01");
    expect(row.text()).toContain("Shop A");
    expect(row.text()).not.toContain("k1234567");
  });

  it("without a title or a label the short ids stand in, and a title is text", async () => {
    const row = await openDetail([unclearItem({ job_title: null, key_label: null })]);
    expect(row.find(".ad-job").text()).toBe("abcdef01");
    expect(row.text()).toContain("k1234567");
  });

  it("a job title is text", async () => {
    const planted = await openDetail([unclearItem({ job_title: PLANTED })]);
    expect(planted.find(".ad-job").text()).toBe(PLANTED);
    expect(planted.find("img").exists()).toBe(false);
  });
});

describe("what a row of the reply list draws on each width", () => {
  const replies = () =>
    vi
      .mocked(alerts.getAlertGroup)
      .mockResolvedValue(
        detail({ kind: "unclear_reply", items: [unclearItem()], outcomes: { done: 1, failed: 0, open: 0 } }),
      );

  for (const width of [1440, 390]) {
    it(`a ${width} px window draws the job's title and, under it, its short id`, async () => {
      showAt(width);
      replies();
      const w = await open();
      await openFirst(w);
      const cell = dr().find("tbody tr");
      expect(cell.find(".ad-job").text()).toBe("A title");
      expect(cell.find(".ad-jobid").text()).toBe("abcdef01");
    });
  }
});

describe("the names of the groups, as the canvas writes them", () => {
  const reply = group({
    kind: "unclear_reply",
    group: "0123456789abcdef",
    title: "sorry, i can't create that video",
    provider: "dola",
    reply: "Sorry, I can’t create that video",
  });
  const clamp = group({
    group: "k1.dola",
    title: "clamp: dola cap of key k1",
    provider: "dola",
    key_label: "Shop A",
  });
  const SAID = "Dola replied “Sorry, I can’t create that video”";

  it("the list and the detail name a reply group by its newest reply, a billing group by its wallet and key", async () => {
    list.mockResolvedValue({ items: [reply, clamp] });
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ ...reply, items: [] }));
    const w = await open();
    expect(w.findAll('[data-testid="alert-group"] .g-title').map((t) => t.text())).toEqual([
      SAID,
      "dola cap of key Shop A",
    ]);
    await listButtons(w, `Details of ${SAID}`)[0]!.trigger("click");
    await settle();
    expect(db().find('[data-testid="alert-detail-title"]').text()).toBe(SAID);
  });

  it("a phone names them the same way", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [reply, clamp] });
    const w = await open();
    expect(w.findAll('[data-testid="alert-group"] .g-title').map((t) => t.text())).toEqual([
      SAID,
      "dola cap of key Shop A",
    ]);
  });
});

describe("mute, rules, Open Jobs filtered and Resolved today", () => {
  const HOUR = 3_600_000;

  it("Mute 1 h mutes the row's kind for 60 minutes; a muted row shows its end and is not counted", async () => {
    const end = new Date(2026, 9, 24, 15, 27).getTime();
    vi.mocked(alerts.changeAlertRule).mockResolvedValue({
      enabled: true,
      muted_until_ms: end,
      updated_by: "ann",
      updated_ms: 1,
    });
    const w = await open();
    expect(w.find('[data-testid="alerts-summary"]').text()).toBe("2 open: 1 critical, 1 warning.");
    list.mockResolvedValue({ items: [group({ muted_until_ms: end })] });
    await listButtons(w, "Mute clamp for 1 hour")[0]!.trigger("click");
    await settle();
    expect(alerts.changeAlertRule).toHaveBeenCalledWith("stored", "clamp", { mute_minutes: 60 });
    const row = w.find('[data-testid="alert-group"]');
    expect(row.find('[data-testid="alert-muted"]').text()).toMatch(/^Muted until (15:27|\d+ Oct 15:27)$/);
    expect(row.find('[data-testid="alert-mute"]').exists()).toBe(false);
    expect(w.find('[data-testid="alerts-summary"]').text()).toBe("1 open: 1 warning.");
  });

  it("a live condition can be muted, and a muted one from the stats is listed with its end, never counted", async () => {
    vi.mocked(shell.getStats).mockResolvedValue({
      jobs: { queued: 0, in_progress: 0, done: 0, failed: 0, pre_job_failures_24h: 0 },
      made: { videos: 0, images: 0 },
      accounts: { usable: 1, available: 1, dead: 0, disabled: 0 },
      keys: 1,
      alerts: [{ kind: "accounts_low", severity: "warning", message: "only 1 usable account" }],
      muted_alerts: [{ kind: "disk_low", severity: "error", message: "2 GB free", muted_until_ms: Date.now() + HOUR }],
    });
    vi.mocked(alerts.changeAlertRule).mockResolvedValue({
      enabled: true,
      muted_until_ms: Date.now() + HOUR,
      updated_by: "ann",
      updated_ms: 1,
    });
    const w = await open();
    const live = w.findAll('[data-testid="alert-live"]');
    expect(live).toHaveLength(2);
    expect(live.filter((r) => r.find('[data-testid="alert-muted"]').exists())).toHaveLength(1);
    expect(w.find('[data-testid="alerts-summary"]').text()).toBe("2 open: 1 critical, 1 warning.");
    await listButtons(w, "Mute accounts_low for 1 hour")[0]!.trigger("click");
    await settle();
    expect(alerts.changeAlertRule).toHaveBeenCalledWith("live", "accounts_low", { mute_minutes: 60 });
  });

  it("Open Jobs filtered opens Jobs on the group's provider and key", async () => {
    // a points-wallet group (`<key>.*`): its key, and no provider (the wallet is every provider's)
    list.mockResolvedValue({ items: [group({ group: "k1.*" })] });
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ group: "k1.*" }));
    const w = await open();
    await titleButtons(w)[0]!.trigger("click");
    await settle();
    const go = dr()
      .findAll("button")
      .find((b) => b.text() === "Open Jobs filtered")!;
    expect(go.exists()).toBe(true);
    await go.trigger("click");
    await settle();
    expect(useScopeStore().provider).toBe("");
    expect(push).toHaveBeenCalledWith({ name: "jobs", query: { key_id: "k1" } });
    void w;
  });

  it("Resolved today counts the groups acknowledged today and lists them when opened", async () => {
    vi.mocked(alerts.getResolvedToday).mockResolvedValue({
      count: 1,
      day_start_ms: 0,
      tz: "UTC",
      items: [
        {
          ...group({ kind: "drift", group: "k1.dola", title: "drift of key", open_count: 0 }),
          key_label: "Shop A",
          resolved_ms: new Date(2026, 9, 24, 13, 12).getTime(),
          resolved_by: "ann",
          resolved_rows: 2,
        },
      ],
    });
    const w = await open();
    const card = w.find('[data-testid="alerts-resolved"]');
    expect(card.find('[data-testid="alerts-resolved-count"]').text()).toBe("1");
    expect(card.text()).toContain("Latest: dola cap of key Shop A acknowledged at 13:12 by ann");
    expect(card.findAll('[data-testid="alert-resolved"]')).toHaveLength(0);
    await card.find("button[aria-expanded]").trigger("click");
    expect(card.findAll('[data-testid="alert-resolved"]')).toHaveLength(1);
  });

  it("the head opens the alert rules", async () => {
    vi.mocked(alerts.listAlertRules).mockResolvedValue({
      items: [
        {
          source: "live",
          kind: "accounts_low",
          severity: "warning",
          text: "A provider has fewer usable accounts than the threshold.",
          fixed: null,
          thresholds: [{ setting: "alert_accounts_low", value: 3 }],
          money: false,
          max_mute_minutes: 10080,
          enabled: true,
          muted_until_ms: null,
          updated_by: null,
          updated_ms: null,
        },
      ],
    });
    const w = await open();
    await listButtons(w, "Alert rules")[0]!.trigger("click");
    await settle();
    const rule = db().find('[data-testid="alert-rule"]');
    expect(rule.text()).toContain("accounts_low");
    expect((rule.find("input").element as HTMLInputElement).value).toBe("3");
  });
});
