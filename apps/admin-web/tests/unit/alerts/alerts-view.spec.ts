import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
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

beforeEach(() => {
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
    expect(rows[0]).toContain("one customer only: may be the customer's own words");
    expect(rows[1]).toContain("API read failure: no reply text was read");
    expect(rows[1]).not.toContain("one customer only");
    expect(rows[2]).not.toContain("one customer only");
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("marks an error group with the red left bar and a warning with the amber one, an info group with none", async () => {
    list.mockResolvedValue({
      items: [
        group({ group: "a", severity: "error" }),
        group({ group: "b", severity: "warning" }),
        group({ group: "c", severity: "info" }),
      ],
    });
    const w = await open();
    const classes = w.findAll("tbody tr").map((r) => r.classes().join(" "));
    expect(classes[0]).toContain("row-tone-bad");
    expect(classes[1]).toContain("row-tone-warn");
    expect(classes[2]).not.toContain("row-tone");
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
  const choose = (w: VueWrapper, which: 0 | 1, value: string) => {
    const select = w.findAllComponents({ name: "ElSelect" })[which]!;
    select.vm.$emit("update:modelValue", value);
    select.vm.$emit("change", value);
  };

  it("the Kind select shows All kinds, not the placeholder, and choosing All again sends no kind", async () => {
    const w = await open();
    const kind = () => w.findAllComponents({ name: "ElSelect" })[1]!;
    expect(kind().find(".el-select__placeholder").text()).toBe("All kinds");
    choose(w, 1, "drift");
    await settle();
    expect(list).toHaveBeenLastCalledWith("open", "drift", 200);
    choose(w, 1, "__all__");
    await settle();
    expect(list).toHaveBeenLastCalledWith("open", "", 200);
    expect(kind().find(".el-select__placeholder").text()).toBe("All kinds");
  });

  it("send the Status and then the Kind to the server, each in its own place", async () => {
    const w = await open();
    choose(w, 0, "acknowledged");
    await settle();
    expect(list).toHaveBeenLastCalledWith("acknowledged", "", 200);
    choose(w, 1, "drift");
    await settle();
    expect(list).toHaveBeenLastCalledWith("acknowledged", "drift", 200);
    choose(w, 0, "all");
    await settle();
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
    expect(w.findAll(".el-select__wrapper")).toHaveLength(2);
    expect(w.find(".el-select__wrapper.is-disabled").exists()).toBe(false);
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
    expect(panel.find('[data-testid="alert-outcomes"]').text()).toContain(
      "delivered 1, refunded 2, still open 0 (refused with: content.refused x2)",
    );
    expect(panel.find("img").exists()).toBe(false);
    await panel
      .findAll("button")
      .find((b) => b.text() === "abcdef01")!
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
    selects: w.findAll(".el-select__wrapper.is-disabled").length,
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
