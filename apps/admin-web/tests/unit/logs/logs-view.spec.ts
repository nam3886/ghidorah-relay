import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import * as logs from "@/api/logs";
import type { LogRow } from "@/api/logs";
import { dayAfter, dayStart, viewerZone } from "@/components/logs/log-window";
import * as confirm from "@/components/shared/confirm-dialog";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";
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
const button = (w: VueWrapper, text: string) =>
  text === "Next" ? w.find("button.btn-next") : w.findAll("button").find((b) => b.text() === text)!;
const lastQuery = () => list.mock.calls.at(-1)![0];
// The two native day fields, From then To.
const picker = (w: VueWrapper, which: "From" | "To") => w.find<HTMLInputElement>(`input[aria-label="${which}"]`);
// What the operator does with a day field: types (or clears) a day, and the field reports the change.
const setDay = async (w: VueWrapper, which: "From" | "To", day: string | null) => {
  await picker(w, which).setValue(day ?? "");
  await picker(w, which).trigger("change");
  await settle();
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
    const text = w.find("tbody tr:not(.is-day)").text();
    expect(text).toContain("01234567");
    expect(w.find("tbody .l-dur").text()).toBe("42");
    expect(text).toContain(PLANTED);
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("only a 5xx row is filled red; a 4xx row and a fine row are not tinted", async () => {
    list.mockResolvedValue({
      items: [
        row({ id: "a", status_code: 503 }),
        row({ id: "b", status_code: 422 }),
        row({ id: "c", status_code: 200 }),
        row({ id: "d", status_code: 399 }),
      ],
      total: 4,
    });
    const { w } = await open();
    const classes = w.findAll("tbody tr:not(.is-day)").map((r) => r.classes().join(" "));
    expect(classes[0]).toContain("row-tone-bad");
    expect(classes[0]).toContain("row-tone-fill");
    expect(classes[1]).not.toContain("row-tone");
    expect(classes[2]).not.toContain("row-tone");
    expect(classes[3]).not.toContain("row-tone");
  });

  it("the error code is plain mono text, red on a 5xx row and muted otherwise, never a chip", async () => {
    list.mockResolvedValue({
      items: [
        row({ id: "a", status_code: 503, error_code: "upstream_down" }),
        row({ id: "b", status_code: 422, error_code: "input.bad" }),
        row({ id: "c", status_code: 200, error_code: null }),
      ],
      total: 3,
    });
    const { w } = await open();
    const codes = w.findAll("tbody .l-err");
    expect(codes.map((c) => [c.text(), c.classes().includes("is-bad"), c.classes().includes("is-muted")])).toEqual([
      ["upstream_down", true, false],
      ["input.bad", false, true],
    ]);
    expect(codes[0]!.find("[data-tone]").exists()).toBe(false);
    // on a desktop the code has its own column: nothing of it sits under the status pill
    expect(w.find(".l-code").exists()).toBe(false);
    // no error: an empty cell, not a dash
    expect(w.findAll("tbody tr:not(.is-day)")[2]!.find(".l-err").exists()).toBe(false);
  });

  it("a duration has a thousands comma and no unit on a desktop", async () => {
    list.mockResolvedValue({ items: [row({ duration_ms: 1980 })], total: 1 });
    const { w } = await open();
    expect(w.find("tbody .l-dur").text()).toBe("1,980");
  });

  it("requests are grouped under a day header that counts the rows of the page", async () => {
    const now = Date.now();
    list.mockResolvedValue({
      items: [
        row({ id: "a", at_ms: now }),
        row({ id: "b", at_ms: now - 1000 }),
        row({ id: "c", at_ms: now - 3 * 86_400_000 }),
      ],
      total: 3,
    });
    const { w } = await open();
    const days = w.findAll('[data-testid="day-row"]').map((d) => d.text().replace(/\s+/g, " "));
    expect(days[0]).toMatch(/^Today · \d+ \w+ · 2 requests shown$/);
    expect(days[1]).toMatch(/· 1 request shown$/);
  });

  it("a header says of how many when the server's day total is larger than the page's rows", async () => {
    const now = Date.now();
    const key = new Date(now);
    const todayKey = `${key.getFullYear()}-${String(key.getMonth() + 1).padStart(2, "0")}-${String(key.getDate()).padStart(2, "0")}`;
    list.mockResolvedValue({
      items: [row({ id: "a", at_ms: now }), row({ id: "b", at_ms: now - 1000 })],
      total: 4812,
      day_totals: { [todayKey]: 4761 },
    });
    const { w } = await open();
    expect(w.find('[data-testid="day-row"]').text().replace(/\s+/g, " ")).toMatch(/· 2 of 4,761 requests shown$/);
  });

  it("a row opens its details from a chevron button named by the request id", async () => {
    const { w } = await open();
    expect(w.find('tbody button[aria-label="Open details of request l1"]').exists()).toBe(true);
  });

  it("a row with no job says so", async () => {
    list.mockResolvedValue({ items: [row({ job_id: null, job_title: null })], total: 1 });
    const { w } = await open();
    expect(w.find("tbody tr:not(.is-day)").text()).toContain("no job");
  });

  it("the first load asks for 50 rows, all statuses, no dates", async () => {
    await open();
    expect(lastQuery()).toEqual({ limit: 50, offset: 0, status: "all", tz: viewerZone() });
  });
});

describe("filters", () => {
  it("From and To send the half-open window of the viewer's own midnights, and search at once", async () => {
    const { w } = await open();
    await setDay(w, "From", "2026-10-01");
    await setDay(w, "To", "2026-10-15");
    expect(lastQuery()).toEqual({
      limit: 50,
      tz: viewerZone(),
      offset: 0,
      status: "all",
      since_ms: dayStart("2026-10-01"),
      until_ms: dayAfter("2026-10-15"),
    });
    expect(lastQuery().until_ms).toBeGreaterThan(dayStart("2026-10-15") + 23 * 3600 * 1000);
  });

  it("From alone sets since only; To alone sets until only", async () => {
    const { w } = await open();
    await setDay(w, "From", "2026-10-01");
    expect(lastQuery().since_ms).toBe(dayStart("2026-10-01"));
    expect(lastQuery().until_ms).toBeUndefined();
    await setDay(w, "From", null);
    await setDay(w, "To", "2026-10-15");
    expect(lastQuery().since_ms).toBeUndefined();
    expect(lastQuery().until_ms).toBe(dayAfter("2026-10-15"));
  });

  it("the search text is trimmed and Enter searches", async () => {
    const { w } = await open();
    const input = w.find<HTMLInputElement>('input[aria-label="Search"]');
    await input.setValue("  abc ");
    await w.find("form").trigger("submit");
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
    await setDay(w, "From", "2026-10-01");
    await button(w, "Next").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    w.unmount();
    wrapper = null;
    const again = await open();
    expect(lastQuery()).toMatchObject({ offset: 0, q: "keep me", since_ms: dayStart("2026-10-01") });
    expect(picker(again.w, "From").element.value).toBe("2026-10-01");
    expect(picker(again.w, "To").element.value).toBe("");
  });
});

describe("paging and refresh", () => {
  it("a page size from the pager is asked for and starts again at the first page", async () => {
    const { w } = await open();
    await button(w, "Next").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    w.findComponent({ name: "ListPager" }).vm.$emit("size", 100);
    await settle();
    expect(lastQuery()).toMatchObject({ limit: 100, offset: 0 });
  });

  it("clearing both day pickers drops both ends of the window", async () => {
    const { w } = await open();
    await setDay(w, "From", "2026-10-01");
    await setDay(w, "To", "2026-10-15");
    await setDay(w, "From", null);
    await setDay(w, "To", null);
    expect(lastQuery().since_ms).toBeUndefined();
    expect(lastQuery().until_ms).toBeUndefined();
  });

  it("Search goes back to the first page; there is no Refresh and no Updated line", async () => {
    const { w } = await open();
    await button(w, "Next").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    expect(button(w, "Refresh")).toBeUndefined();
    expect(w.text()).not.toContain("Updated");
    await button(w, "Search").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(0);
  });

  it("a status choice searches at once from the first page", async () => {
    const { w } = await open();
    await button(w, "Next").trigger("click");
    await settle();
    await w.find<HTMLSelectElement>('select[aria-label="Status"]').setValue("error");
    await settle();
    expect(lastQuery()).toMatchObject({ status: "error", offset: 0 });
    expect(w.findAll('select[aria-label="Status"] option').map((o) => o.text())).toEqual([
      "All",
      "OK below 400",
      "Errors",
      "Failed before a job",
    ]);
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
      expect.objectContaining({
        title: "Clear every request log row?",
        message: "Jobs, the ledger and alerts are kept.",
        confirmText: "Clear logs",
      }),
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
    await w.find('tbody button[aria-label="Open details of request l1"]').trigger("click");
    await settle();
    expect(logs.getLog).toHaveBeenCalledWith("l1");
    const dialog = document.body.querySelector('[data-testid="request-drawer"]')!;
    expect(dialog.getAttribute("role")).toBe("dialog");
    expect(dialog.textContent).toContain("Request l1");
    expect(dialog.textContent).toContain(PLANTED);
    expect(dialog.querySelector("img")).toBeNull();
    expect(dialog.textContent).toContain('"ok": true');
  });

  it("an answer that was not kept says so", async () => {
    vi.mocked(logs.getLog).mockResolvedValue(detail({ response: null }));
    const { w } = await open();
    await w.find('tbody button[aria-label="Open details of request l1"]').trigger("click");
    await settle();
    const drawer = document.body.querySelector('[data-testid="request-drawer"]')!;
    expect(drawer.textContent).toContain("(none kept)");
    expect(drawer.querySelector('button[aria-label="Copy Answer body"]')).toBeNull();
  });

  it("lists the facts as a ledger and gives each body its own Copy", async () => {
    vi.mocked(logs.getLog).mockResolvedValue(
      detail({ status_code: 422, error_code: "dola.content_refused", duration_ms: 1980, job_id: "5a0f3c88aaaa" }),
    );
    const { w } = await open();
    await w.find('tbody button[aria-label="Open details of request l1"]').trigger("click");
    await settle();
    const drawer = document.body.querySelector('[data-testid="request-drawer"]')!;
    const facts = [...drawer.querySelectorAll('[data-testid="request-facts"] .rd__row')].map((r) => [
      r.querySelector("dt")!.textContent,
      r.querySelector("dd")!.textContent!.trim(),
    ]);
    expect(facts.map((f) => f[0])).toEqual(["Time", "Provider", "Key", "Job", "Error", "Duration"]);
    expect(facts.find((f) => f[0] === "Job")![1]).toBe("5a0f3c88");
    expect(facts.find((f) => f[0] === "Error")![1]).toBe("dola.content_refused");
    expect(facts.find((f) => f[0] === "Duration")![1]).toBe("1,980 ms");
    expect(drawer.querySelector('button[aria-label="Copy Request body"]')).not.toBeNull();
    expect(drawer.querySelector('button[aria-label="Copy Answer body"]')).not.toBeNull();
    expect(drawer.querySelectorAll("pre")).toHaveLength(2);
  });

  it("the job link closes the drawer and opens the job drawer", async () => {
    vi.mocked(logs.getLog).mockResolvedValue(detail());
    const { w } = await open();
    await w.find('tbody button[aria-label="Open details of request l1"]').trigger("click");
    await settle();
    document.body.querySelector<HTMLButtonElement>('[data-testid="request-facts"] .rd__link')!.click();
    await settle();
    expect(useUiStore(pinia).openJobId).toBe("0123456789abcdef");
    expect(w.findComponent({ name: "LogDetailDrawer" }).props("modelValue")).toBe(false);
  });

  it("a request with no job shows no job link", async () => {
    vi.mocked(logs.getLog).mockResolvedValue(detail({ job_id: null, job_title: null }));
    const { w } = await open();
    await w.find('tbody button[aria-label="Open details of request l1"]').trigger("click");
    await settle();
    expect(document.body.querySelector(".rd__link")).toBeNull();
    expect(document.body.querySelector('[data-testid="request-facts"]')!.textContent).toContain("no job");
  });
});

describe("the tablet", () => {
  const rich = () =>
    list.mockResolvedValue({
      items: [
        row({ id: "a", status_code: 502, error_code: "account.dead_session", duration_ms: 2140 }),
        row({ id: "b", job_id: null, job_title: null, status_code: 200 }),
      ],
      total: 2,
    });

  it("keeps five columns, folds the error code under the pill and the job under the route, and writes the unit", async () => {
    showAt(768);
    rich();
    const { w } = await open();
    expect(w.findAll("thead th").map((h) => h.text())).toEqual([
      "Time",
      "Status",
      "Route",
      "Key",
      "Duration",
      "Actions",
    ]);
    const [first, second] = w.findAll("tbody tr:not(.is-day)");
    expect(first!.find(".l-code").text()).toBe("account.dead_session");
    expect(first!.find(".l-code").classes()).toContain("is-bad");
    expect(first!.find(".l-job-link").text()).toBe("a title");
    expect(first!.find(".l-dur").text()).toBe("2,140 ms");
    expect(second!.find(".l-nojob").text()).toBe("no job");
    expect(second!.find(".l-code").exists()).toBe(false);
  });

  it("puts Clear logs in a More menu: closed at first, one item, closed again by Escape and by a click outside", async () => {
    showAt(768);
    const { w } = await open();
    expect(w.findAll("button").some((b) => b.text() === "Clear logs")).toBe(false);
    const more = w.find('button[aria-label="More actions"]');
    expect(more.attributes("aria-haspopup")).toBe("menu");
    expect(more.attributes("aria-expanded")).toBe("false");
    await more.trigger("click");
    expect(more.attributes("aria-expanded")).toBe("true");
    expect(w.findAll('[role="menuitem"]').map((i) => i.text())).toEqual(["Clear logs"]);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await settle();
    expect(w.find('[role="menu"]').exists()).toBe(false);
    await more.trigger("click");
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await settle();
    expect(w.find('[role="menu"]').exists()).toBe(false);
  });

  it("the menu item asks first, like the desktop button", async () => {
    showAt(768);
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    const { w } = await open();
    await w.find('button[aria-label="More actions"]').trigger("click");
    await w.find('[role="menuitem"]').trigger("click");
    await settle();
    expect(confirm.confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ confirmText: "Clear logs" }));
    expect(logs.clearLogs).not.toHaveBeenCalled();
    expect(w.find('[role="menu"]').exists()).toBe(false);
  });
});

describe("the phone", () => {
  it("lists a request as four short lines, the whole row opening the details", async () => {
    showAt(390);
    list.mockResolvedValue({
      items: [row({ status_code: 502, error_code: "account.dead_session", duration_ms: 2140 })],
      total: 1,
    });
    vi.mocked(logs.getLog).mockResolvedValue({ ...row(), request: {}, response: null });
    const { w } = await open();
    expect(w.find("table").exists()).toBe(false);
    const item = w.find(".p-req");
    expect(item.find(".p-req__route").text()).toBe("POST /v1/video/generations");
    expect(item.find(".p-req__job").text()).toBe("a title");
    expect(item.find(".p-req__dur").text()).toBe("2,140 ms");
    expect(item.find(".p-req__err").classes()).toContain("is-bad");
    expect(w.find('[aria-label="Open request l1"]').exists()).toBe(true);
    await w.find('[aria-label="Open request l1"]').trigger("click");
    await settle();
    expect(logs.getLog).toHaveBeenCalledWith("l1");
  });

  it("has no Search button: a choice in Status searches at once, and typing searches after a pause", async () => {
    showAt(390);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const { w } = await open();
      expect(w.findAll("button").some((b) => b.text() === "Search")).toBe(false);
      await w.find<HTMLSelectElement>('select[aria-label="Status"]').setValue("error");
      await settle();
      expect(lastQuery().status).toBe("error");
      const calls = list.mock.calls.length;
      await w.find<HTMLInputElement>('input[aria-label="Search"]').setValue("abc");
      expect(list.mock.calls.length).toBe(calls);
      await vi.advanceTimersByTimeAsync(400);
      await settle();
      expect(lastQuery().q).toBe("abc");
    } finally {
      vi.useRealTimers();
    }
  });

  it("puts the More button 44 high and keeps the same single menu item", async () => {
    showAt(390);
    const { w } = await open();
    await w.find('button[aria-label="More actions"]').trigger("click");
    expect(w.find('[role="menu"]').classes()).toContain("is-phone");
    expect(w.findAll('[role="menuitem"]').map((i) => i.text())).toEqual(["Clear logs"]);
  });
});

describe("typing on a desktop", () => {
  it("does not search by itself: the Search button or Enter does", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const { w } = await open();
      const calls = list.mock.calls.length;
      await w.find<HTMLInputElement>('input[aria-label="Search"]').setValue("abc");
      await vi.advanceTimersByTimeAsync(1000);
      await settle();
      expect(list.mock.calls.length).toBe(calls);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the request drawer, in detail", () => {
  const detail = (over: Partial<logs.LogDetail> = {}): logs.LogDetail => ({
    ...row(),
    request: { prompt: "a cat" },
    response: { ok: true },
    ...over,
  });
  const openDrawer = async (log: logs.LogDetail) => {
    vi.mocked(logs.getLog).mockResolvedValue(log);
    const { w } = await open();
    await w.find('tbody button[aria-label="Open details of request l1"]').trigger("click");
    await settle();
    return document.body.querySelector('[data-testid="request-drawer"]')!;
  };
  const writeText = vi.fn(async (_text: string) => undefined);

  it("an error code is red on a server error and muted on a client error", async () => {
    const bad = await openDrawer(detail({ status_code: 502, error_code: "account.dead_session" }));
    expect(bad.querySelector(".rd__bad")!.classList.contains("is-muted")).toBe(false);
    wrapper!.unmount();
    wrapper = null;
    const quiet = await openDrawer(detail({ status_code: 422, error_code: "dola.content_refused" }));
    expect(quiet.querySelector(".rd__bad")!.classList.contains("is-muted")).toBe(true);
  });

  it("a request with no error has no Error line", async () => {
    const drawer = await openDrawer(detail());
    expect(drawer.textContent).not.toContain("Error");
  });

  it("a body that is already text is shown as it is, not quoted", async () => {
    const drawer = await openDrawer(detail({ request: "plain text body", response: "an answer in words" }));
    const [request, answer] = [...drawer.querySelectorAll("pre")].map((p) => p.textContent);
    expect(request).toBe("plain text body");
    expect(answer).toBe("an answer in words");
  });

  it("each Copy copies its own body and says Copied", async () => {
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    writeText.mockClear();
    const drawer = await openDrawer(detail());
    const copy = (label: string) => drawer.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
    copy("Copy Request body").click();
    await settle();
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify({ prompt: "a cat" }, null, 2));
    expect(copy("Copy Request body").textContent!.trim()).toBe("Copied");
    expect(copy("Copy Answer body").textContent!.trim()).toBe("Copy");
    copy("Copy Answer body").click();
    await settle();
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify({ ok: true }, null, 2));
  });
});

describe("the request drawer on a phone", () => {
  it("is a sheet whose way back names the page it returns to", async () => {
    showAt(390);
    vi.mocked(logs.getLog).mockResolvedValue({ ...row(), request: {}, response: null });
    const { w } = await open();
    await w.find('[aria-label="Open request l1"]').trigger("click");
    await settle();
    const drawer = document.body.querySelector('[data-testid="request-drawer"]')!;
    expect(drawer.querySelector('button[aria-label="Back to Request logs"]')).not.toBeNull();
    expect(drawer.querySelector('button[aria-label="Close"]')).toBeNull();
  });
});
