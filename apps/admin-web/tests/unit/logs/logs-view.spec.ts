import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import * as logs from "@/api/logs";
import type { LogRow } from "@/api/logs";
import { dayAfter, dayStart } from "@/components/logs/log-window";
import * as confirm from "@/components/shared/confirm-dialog";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import LogsView from "@/views/LogsView.vue";

vi.mock("@/api/logs", () => ({ listLogs: vi.fn(), getLog: vi.fn(), clearLogs: vi.fn() }));
vi.mock("@/api/shell", () => ({ getModels: vi.fn() }));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const PLANTED = "<img src=x onerror=window.__pwned=1><b>planted</b>";

const row = (over: Partial<LogRow> = {}): LogRow => ({
  id: "l1",
  at_ms: 1,
  key_id: "k",
  key_label: "shop",
  provider: "vibes",
  route: "POST /v1/video/generations",
  status_code: 200,
  job_id: "0123456789abcdef",
  job_title: "a title",
  duration_ms: 42,
  error_code: null,
  ...over,
});

const list = vi.mocked(logs.listLogs);
let wrapper: VueWrapper | null = null;
let pinia = createPinia();

const settle = async () => {
  await flushPromises();
  await flushPromises();
};

async function open(path = "/logs") {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/logs", name: "logs", component: LogsView }],
  });
  await router.push(path);
  wrapper = mount(LogsView, { global: { plugins: [pinia, router] }, attachTo: document.body });
  await settle();
  return { w: wrapper, router };
}
const button = (w: VueWrapper, text: string) => w.findAll("button").find((b) => b.text() === text)!;
const lastQuery = () => list.mock.calls.at(-1)![0];
const setDate = async (w: VueWrapper, label: string, value: string) => {
  const input = w.find<HTMLInputElement>(`input[aria-label="${label}"]`);
  await input.setValue(value);
  await input.trigger("change");
  await settle();
};

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
  for (const fn of [...Object.values(logs), confirm.confirmDialog, ...Object.values(notify)]) {
    (fn as ReturnType<typeof vi.fn>).mockReset();
  }
  list.mockResolvedValue({ items: [row()], total: 120 });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("the list", () => {
  it("draws the columns and server strings as text", async () => {
    list.mockResolvedValue({
      items: [row({ route: PLANTED, key_label: PLANTED, job_title: PLANTED, error_code: PLANTED, status_code: 503 })],
      total: 1,
    });
    const { w } = await open();
    const text = w.find("tbody tr").text();
    expect(text).toContain("01234567");
    expect(text).toContain("42 ms");
    expect(text).toContain(PLANTED);
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("a row with no job says so", async () => {
    list.mockResolvedValue({ items: [row({ job_id: null, job_title: null })], total: 1 });
    const { w } = await open();
    expect(w.find("tbody tr").text()).toContain("no job");
  });

  it("the first load asks for 50 rows, all statuses, no dates", async () => {
    await open();
    expect(lastQuery()).toEqual({ limit: 50, offset: 0, status: "all" });
  });
});

describe("filters", () => {
  it("From and To send the half-open window of the viewer's own midnights, and search at once", async () => {
    const { w } = await open();
    await setDate(w, "From", "2026-10-01");
    await setDate(w, "To", "2026-10-15");
    expect(lastQuery()).toEqual({
      limit: 50,
      offset: 0,
      status: "all",
      since_ms: dayStart("2026-10-01"),
      until_ms: dayAfter("2026-10-15"),
    });
    expect(lastQuery().until_ms).toBeGreaterThan(dayStart("2026-10-15") + 23 * 3600 * 1000);
  });

  it("the search text is trimmed and Enter searches", async () => {
    const { w } = await open();
    const input = w.find<HTMLInputElement>('input[aria-label="Search"]');
    await input.setValue("  abc ");
    await input.trigger("keyup.enter");
    await settle();
    expect(lastQuery().q).toBe("abc");
  });

  it("the provider filter is sent and a change of it searches again from the first page", async () => {
    const { w } = await open();
    useScopeStore(pinia).provider = "dola";
    await settle();
    expect(lastQuery()).toMatchObject({ provider: "dola", offset: 0 });
    expect(w.exists()).toBe(true);
  });

  it("a stats link opens it on the failed-before-a-job status", async () => {
    await open("/logs?status=pre_job");
    expect(lastQuery().status).toBe("pre_job");
  });

  it("an unknown status in the address is ignored", async () => {
    await open("/logs?status=bogus");
    expect(lastQuery().status).toBe("all");
  });

  it("the filters survive leaving and coming back; the page starts at the first", async () => {
    const { w } = await open();
    await w.find<HTMLInputElement>('input[aria-label="Search"]').setValue("keep me");
    await setDate(w, "From", "2026-10-01");
    await button(w, "Next").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    w.unmount();
    wrapper = null;
    const again = await open();
    expect(lastQuery()).toMatchObject({ offset: 0, q: "keep me", since_ms: dayStart("2026-10-01") });
    expect(again.w.find<HTMLInputElement>('input[aria-label="From"]').element.value).toBe("2026-10-01");
  });
});

describe("paging and refresh", () => {
  it("Refresh keeps the page, Search goes back to the first", async () => {
    const { w } = await open();
    await button(w, "Next").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    await button(w, "Refresh").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    await button(w, "Search").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(0);
  });
});

describe("clear logs", () => {
  it("asks first, then says how many rows went and goes to the first page", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(true);
    vi.mocked(logs.clearLogs).mockResolvedValue({ deleted: 17 });
    const { w } = await open();
    await button(w, "Next").trigger("click");
    await settle();
    await button(w, "Clear logs").trigger("click");
    await settle();
    expect(confirm.confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ message: "Clear every request log row? Jobs, the ledger and alerts are kept." }),
    );
    expect(notify.success).toHaveBeenCalledWith("Cleared 17 rows");
    expect(lastQuery().offset).toBe(0);
  });

  it("a cancel deletes nothing", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    const { w } = await open();
    await button(w, "Clear logs").trigger("click");
    await settle();
    expect(logs.clearLogs).not.toHaveBeenCalled();
  });
});

describe("the detail", () => {
  const detail = (over: Partial<logs.LogDetail> = {}): logs.LogDetail => ({
    ...row(),
    request: { prompt: PLANTED },
    response: { ok: true },
    ...over,
  });

  it("shows the request and the answer as text, titled with the id", async () => {
    vi.mocked(logs.getLog).mockResolvedValue(detail());
    const { w } = await open();
    await w
      .find("tbody")
      .findAll("button")
      .find((b) => b.text() === "Details")!
      .trigger("click");
    await settle();
    expect(logs.getLog).toHaveBeenCalledWith("l1");
    const dialog = document.body.querySelector(".el-dialog")!;
    expect(dialog.textContent).toContain("Request l1");
    expect(dialog.textContent).toContain(PLANTED);
    expect(dialog.querySelector("img")).toBeNull();
    expect(dialog.textContent).toContain('"ok": true');
  });

  it("an answer that was not kept says so", async () => {
    vi.mocked(logs.getLog).mockResolvedValue(detail({ response: null }));
    const { w } = await open();
    await w
      .find("tbody")
      .findAll("button")
      .find((b) => b.text() === "Details")!
      .trigger("click");
    await settle();
    expect(document.body.querySelector(".el-dialog")!.textContent).toContain("(none kept)");
  });
});
