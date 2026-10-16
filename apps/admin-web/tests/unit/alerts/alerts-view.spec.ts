import { type DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as alerts from "@/api/alerts";
import type { AlertDetail, AlertGroup, UnclearItem } from "@/api/alerts";
import { ApiError } from "@/api/client";
import * as shell from "@/api/shell";
import { EMPTY_WORDING_TITLE } from "@/components/alerts/alerts-model";
import { notify } from "@/lib/notify";
import { useUiStore } from "@/stores/ui";
import AlertsView from "@/views/AlertsView.vue";

vi.mock("@/api/alerts", () => ({ listAlertGroups: vi.fn(), getAlertGroup: vi.fn(), acknowledgeGroup: vi.fn() }));
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
  wrapper = mount(AlertsView, { global: { plugins: [pinia] }, attachTo: document.body });
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
const detailButton = (w: VueWrapper, text: string) =>
  w.findAll('[data-testid="alert-detail"] button').find((b) => b.text() === text)!;
const refresh = (w: VueWrapper) => listButtons(w, "Refresh")[0]!;
const statusSelect = (w: VueWrapper) => w.find<HTMLSelectElement>('select[aria-label="Status"]');
const kindSelect = (w: VueWrapper) => w.find<HTMLSelectElement>('select[aria-label="Kind"]');

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
  delete (window as unknown as { __pwned?: number }).__pwned;
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the lists", () => {
  it("asks for the open groups, 200 of them, with the live conditions beside", async () => {
    const w = await open();
    expect(list).toHaveBeenCalledWith("open", "", 200);
    expect(w.find('[data-testid="alerts-live"]').text()).toContain("accounts_low: only 1 usable account");
    expect(w.find("tbody tr").text()).toContain("Clamp on fake");
    // the Updated line sits under the title on a phone only; on a desktop it is in the header
    expect(w.find(".a-updated").exists()).toBe(false);
    expect(w.find(".page-header__meta").text()).toMatch(/^Updated \d\d:\d\d:\d\d$/);
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
    const rows = w.findAll("tbody tr").map((r) => r.text());
    expect(rows[0]).toContain(PLANTED);
    expect(rows[0]).toContain("One customer only: may be the customer’s own words.");
    expect(rows[1]).toContain("API read failure: no reply text was read");
    expect(rows[1]).not.toContain("One customer only");
    expect(rows[2]).not.toContain("One customer only");
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("tells the severity by its chip: error is bad, warning is warn, anything else neutral; no row bar", async () => {
    list.mockResolvedValue({
      items: [
        group({ group: "a", severity: "error" }),
        group({ group: "b", severity: "warning" }),
        group({ group: "c", severity: "info" }),
      ],
    });
    const w = await open();
    const rows = w.findAll("tbody tr");
    expect(rows.map((r) => r.find("[data-tone]").attributes("data-tone"))).toEqual(["bad", "warn", "neutral"]);
    expect(rows.map((r) => r.classes().join(" ")).join(" ")).not.toContain("row-tone");
  });

  it("dims a group with nothing open, marks the opened one, and counts the groups", async () => {
    list.mockResolvedValue({
      items: [group({ group: "a", open_count: 4 }), group({ group: "b", open_count: 0 })],
    });
    const w = await open();
    const rows = w.findAll("tbody tr");
    expect(rows.map((r) => r.classes().includes("a-dim"))).toEqual([false, true]);
    expect(w.find('[data-testid="alerts-count"]').text()).toBe("2 groups");
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    expect(w.findAll("tbody tr").map((r) => r.classes().includes("a-selected"))).toEqual([true, false]);
    list.mockResolvedValue({ items: [group()] });
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-count"]').text()).toBe("1 group");
  });

  it("a group colours its open count warn while rows are open and faint at 0", async () => {
    list.mockResolvedValue({
      items: [group({ group: "a", open_count: 4 }), group({ group: "b", open_count: 0 })],
    });
    const w = await open();
    const open_ = w.findAll("tbody .g-open").map((c) => [c.text(), c.classes().includes("is-faint")]);
    expect(open_).toEqual([
      ["4", false],
      ["0", true],
    ]);
  });

  it("names the live conditions as the app says them and says so when there are none", async () => {
    const w = await open();
    expect(w.text()).toContain("Computed now. These cannot be acknowledged; they clear when the condition does.");
    vi.mocked(shell.getStats).mockResolvedValue({
      ...(await shell.getStats("")),
      alerts: [],
    });
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alerts-live"]').exists()).toBe(false);
    expect(w.text()).toContain("No live condition right now.");
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
  const choose = async (select: DOMWrapper<HTMLSelectElement>, value: string) => {
    await select.setValue(value);
    await settle();
  };

  it("the selects offer the canvas words: Status All, Open, Acknowledged; Kind All and the three kinds", async () => {
    const w = await open();
    expect(
      statusSelect(w)
        .findAll("option")
        .map((o) => [o.text(), o.element.value]),
    ).toEqual([
      ["All", "all"],
      ["Open", "open"],
      ["Acknowledged", "acknowledged"],
    ]);
    expect(
      kindSelect(w)
        .findAll("option")
        .map((o) => o.text()),
    ).toEqual(["All", "clamp", "drift", "unclear_reply"]);
    expect(statusSelect(w).element.value).toBe("open");
    expect(kindSelect(w).element.value).toBe("");
  });

  it("choosing the Kind and then All again sends no kind", async () => {
    const w = await open();
    await choose(kindSelect(w), "drift");
    expect(list).toHaveBeenLastCalledWith("open", "drift", 200);
    await choose(kindSelect(w), "");
    expect(list).toHaveBeenLastCalledWith("open", "", 200);
  });

  it("send the Status and then the Kind to the server, each in its own place", async () => {
    const w = await open();
    await choose(statusSelect(w), "acknowledged");
    expect(list).toHaveBeenLastCalledWith("acknowledged", "", 200);
    await choose(kindSelect(w), "drift");
    expect(list).toHaveBeenLastCalledWith("acknowledged", "drift", 200);
    await choose(statusSelect(w), "all");
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
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
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
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    slow.reject(new ApiError(500, { error: { message: "old failure" } }));
    await settle();
    expect(w.find(".data-table__error").exists()).toBe(false);
    expect(w.text()).toContain("second answer");
    expect(notify.error).not.toHaveBeenCalled();
  });

  it("the newest failure is shown", async () => {
    const w = await open();
    list.mockRejectedValueOnce(new ApiError(500, { error: { message: "the list failed" } }));
    await refresh(w).trigger("click");
    await settle();
    expect(w.find(".data-table__error").text()).toContain("the list failed");
    expect(notify.error).toHaveBeenCalledWith("the list failed");
  });

  it("while a view loads, Acknowledge is locked but the filters are not", async () => {
    const w = await open();
    const slow = deferred<{ items: AlertGroup[] }>();
    list.mockReturnValueOnce(slow.promise);
    await refresh(w).trigger("click");
    await settle();
    expect(listButtons(w, "Acknowledge")[0]!.attributes("disabled")).toBeDefined();
    expect(w.findAll("select")).toHaveLength(2);
    expect(w.findAll("select:disabled")).toHaveLength(0);
    slow.resolve({ items: [group()] });
    await settle();
    expect(listButtons(w, "Acknowledge")[0]!.attributes("disabled")).toBeUndefined();
  });
});

describe("the detail", () => {
  it("opens from Details, shows the outcomes and opens a job through the ui store", async () => {
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
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    expect(alerts.getAlertGroup).toHaveBeenCalledWith({ kind: "clamp", group: "g1" }, 100);
    const panel = w.find('[data-testid="alert-detail"]');
    expect(panel.find('[data-testid="alert-detail-title"]').text()).toBe(PLANTED);
    expect(panel.find('[data-testid="alert-outcomes"]').text()).toBe("1 delivered after the reply, 2 refunded.");
    expect(panel.find("img").exists()).toBe(false);
    await panel
      .findAll("button")
      .find((b) => b.text() === "A title")!
      .trigger("click");
    expect(useUiStore(pinia).openJobId).toBe("abcdef0123456789");
  });

  it("Refresh keeps the open detail", async () => {
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-detail"]').exists()).toBe(true);
    expect(alerts.getAlertGroup).toHaveBeenCalledTimes(2);
  });

  it("a detail that cannot be read is closed with a toast and the list stays whole", async () => {
    vi.mocked(alerts.getAlertGroup).mockRejectedValue(new ApiError(404, { error: { message: "no such group" } }));
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-detail"]').exists()).toBe(false);
    expect(w.find("tbody tr").text()).toContain("Clamp on fake");
    expect(notify.error).toHaveBeenCalledWith("no such group");
    await refresh(w).trigger("click");
    await settle();
    expect(alerts.getAlertGroup).toHaveBeenCalledTimes(1);
  });

  it("says the detail is cut at 100 rows, and only then", async () => {
    const rows = (n: number) => Array.from({ length: n }, (_, i) => unclearItem({ id: i + 1 }));
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(99) }));
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-detail"]').exists()).toBe(true);
    expect(w.find('[data-testid="alert-detail-more"]').exists()).toBe(false);
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(100) }));
    await refresh(w).trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-detail-more"]').text()).toBe(
      "Showing the newest 100 rows; older rows are not listed.",
    );
  });

  it("Close reloads the list", async () => {
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    const before = list.mock.calls.length;
    await detailButton(w, "Close").trigger("click");
    await settle();
    expect(list.mock.calls.length).toBe(before + 1);
    expect(w.find('[data-testid="alert-detail"]').exists()).toBe(false);
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
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    await detailButton(w, "Acknowledge").trigger("click");
    await settle();
    expect(vi.mocked(alerts.acknowledgeGroup).mock.calls[0]![1]).toBe(77);
    expect(notify.success).toHaveBeenCalledWith("Nothing new to acknowledge");
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
    selects: w.findAll("select:disabled").length,
    details: listButtons(w, "Details")[0]!.attributes("disabled") !== undefined,
    listAck: listButtons(w, "Acknowledge")[0]!.attributes("disabled") !== undefined,
    close: detailButton(w, "Close").attributes("disabled") !== undefined,
    detailAck: detailButton(w, "Acknowledge").attributes("disabled") !== undefined,
  });
  const openDetailThenAcknowledge = async (answer: Promise<{ acknowledged: number }>) => {
    vi.mocked(alerts.acknowledgeGroup).mockReturnValue(answer);
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    expect(controls(w)).toEqual({ selects: 0, details: false, listAck: false, close: false, detailAck: false });
    await listButtons(w, "Acknowledge")[0]!.trigger("click");
    await settle();
    return w;
  };

  it("while an acknowledge is in flight, the selects, Details, Close and both Acknowledge buttons are off", async () => {
    const slow = deferred<{ acknowledged: number }>();
    const w = await openDetailThenAcknowledge(slow.promise);
    expect(controls(w)).toEqual({ selects: 2, details: true, listAck: true, close: true, detailAck: true });
    slow.resolve({ acknowledged: 1 });
    await settle();
    expect(controls(w)).toEqual({ selects: 0, details: false, listAck: false, close: false, detailAck: false });
  });

  it("after a failed acknowledge every control is on again", async () => {
    const slow = deferred<{ acknowledged: number }>();
    const w = await openDetailThenAcknowledge(slow.promise);
    expect(controls(w).selects).toBe(2);
    slow.reject(new ApiError(404, { error: { message: "group gone" } }));
    await settle();
    expect(notify.error).toHaveBeenCalledWith("group gone");
    expect(controls(w)).toEqual({ selects: 0, details: false, listAck: false, close: false, detailAck: false });
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
      }),
    );
  const openIt = async () => {
    withReplies();
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    return w.find('[data-testid="alert-detail"]');
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
    expect(panel.text()).toContain(
      "The reply is stored as the job’s answer; the job outcome decides what the customer got.",
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
    expect(rows[0]!.text()).toContain("not yet");
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
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    const text = w.find('[data-testid="alert-detail"] tbody tr').text();
    expect(text).toContain("admin,");
    expect(text).not.toContain("not yet");
  });

  it("a billing group has no reply sentence and no delivered line", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "clamp", items: [] }));
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    const panel = w.find('[data-testid="alert-detail"]');
    expect(panel.text()).not.toContain("The reply is stored");
    expect(panel.find('[data-testid="alert-outcomes"]').exists()).toBe(false);
  });

  it("an opened group with nothing open has no Acknowledge in its detail", async () => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ open_count: 0 }));
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    expect(w.findAll('[data-testid="alert-detail"] button').map((b) => b.text())).toEqual(["Close"]);
  });
});

describe("the tablet and the phone", () => {
  it("a tablet shows Severity, Alert and Open / Rows, with the kind, provider and span under the title", async () => {
    showAt(768);
    list.mockResolvedValue({ items: [group({ kind: "unclear_reply", provider: "dola", count: 6, open_count: 4 })] });
    const w = await open();
    expect(w.findAll("thead th").map((h) => h.text())).toEqual(["Severity", "Alert", "Open / Rows", "Actions"]);
    const row = w.find("tbody tr");
    expect(row.find(".g-kind").text()).toBe("unclear_reply · dola");
    expect(row.find(".g-range").text()).toContain("→");
    expect(row.find(".g-open").text()).toBe("4");
    expect(row.text()).toContain("4 / 6");
  });

  it("a phone has an icon Refresh and the Updated line under the header, and one block per group", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [group({ group: "a", title: "first" }), group({ group: "b", title: "second" })] });
    const w = await open();
    expect(w.find("table").exists()).toBe(false);
    expect(w.find('button[aria-label="Refresh"]').exists()).toBe(true);
    expect(w.findAll("button").some((b) => b.text() === "Refresh")).toBe(false);
    expect(w.find(".a-updated").text()).toMatch(/^Updated \d\d:\d\d:\d\d$/);
    expect(w.findAll(".p-group")).toHaveLength(2);
    expect(w.find(".p-group").text()).toContain("first");
  });

  it("a phone opens the detail right under its group and closes it from the Acknowledge-less region", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [group({ group: "a", title: "first" }), group({ group: "b", title: "second" })] });
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ group: "a", title: "first", open_count: 2 }));
    const w = await open();
    await w.find('.p-group button[aria-label^="Details"]').trigger("click");
    await settle();
    const items = w.findAll(".data-table__item");
    expect(items[0]!.find('[data-testid="alert-detail"]').exists()).toBe(true);
    expect(items[1]!.find('[data-testid="alert-detail"]').exists()).toBe(false);
    expect(w.find('[data-testid="alert-detail"]').element.tagName).toBe("DIV");
    expect(w.find('[data-testid="alert-detail-title"]').text()).toBe("Group summary and rows");
    expect(w.findAll('[data-testid="alert-detail"] button').map((b) => b.text())).toEqual(["Acknowledge"]);
  });

  it("a phone says the detail is cut at 100 rows, and only then", async () => {
    showAt(390);
    const rows = (n: number) => Array.from({ length: n }, (_, i) => unclearItem({ id: i + 1 }));
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(99) }));
    const w = await open();
    await w.find('.p-group button[aria-label^="Details"]').trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-detail"]').exists()).toBe(true);
    expect(w.find('[data-testid="alert-detail-more"]').exists()).toBe(false);
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(detail({ kind: "unclear_reply", items: rows(100) }));
    await w.find('button[aria-label="Refresh"]').trigger("click");
    await settle();
    expect(w.find('[data-testid="alert-detail-more"]').text()).toBe(
      "Showing the newest 100 rows; older rows are not listed.",
    );
  });

  it("a phone lists the reply rows as blocks: time, outcome chip, job link, code, account and key", async () => {
    showAt(390);
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({
        kind: "unclear_reply",
        items: [
          unclearItem({ job_status: "failed", job_error_code: "dola.likeness_refused", account_label: "n11-06" }),
        ],
        outcomes: { done: 0, failed: 1, open: 0 },
      }),
    );
    const w = await open();
    await w.find('.p-group button[aria-label^="Details"]').trigger("click");
    await settle();
    const item = w.find('[data-testid="alert-detail"] .ad-phone__rows li');
    expect(item.find("[data-tone]").text()).toBe("refunded");
    expect(item.find(".ad-job").text()).toBe("A title");
    expect(item.find(".ad-code").text()).toBe("dola.likeness_refused");
    expect(item.text()).toContain("n11-06");
    expect(item.text()).toContain("Shop A");
  });
});

describe("job titles and key labels in the detail", () => {
  const openDetail = async (items: UnclearItem[]) => {
    vi.mocked(alerts.getAlertGroup).mockResolvedValue(
      detail({ kind: "unclear_reply", items, outcomes: { done: items.length, failed: 0, open: 0 } }),
    );
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    return w.find('[data-testid="alert-detail"] tbody tr');
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

  it("a desktop draws the job's title and, under it, its short id", async () => {
    replies();
    const w = await open();
    await listButtons(w, "Details")[0]!.trigger("click");
    await settle();
    const cell = w.find('[data-testid="alert-detail"] tbody tr');
    expect(cell.find(".ad-job").text()).toBe("A title");
    expect(cell.find(".ad-jobid").text()).toBe("abcdef01");
  });

  it("a phone draws the title alone: the board has no id there", async () => {
    showAt(390);
    replies();
    const w = await open();
    await w.find('.p-group button[aria-label^="Details"]').trigger("click");
    await settle();
    const item = w.find('[data-testid="alert-detail"] .ad-phone__rows li');
    expect(item.find(".ad-job").text()).toBe("A title");
    expect(item.find(".ad-jobid").exists()).toBe(false);
    expect(item.text()).not.toContain("abcdef01");
  });
});
