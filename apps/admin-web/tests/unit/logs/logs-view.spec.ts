import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { ApiError } from "@/api/client";
import * as logs from "@/api/logs";
import type { LogRow } from "@/api/logs";
import { dayAfter, dayStart } from "@/components/logs/log-window";
import { useLogsFilters } from "@/components/logs/logs-filters";
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
  key_prefix: "rk-shop0001",
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
    routes: [
      { path: "/logs", name: "logs", component: LogsView },
      { path: "/test", name: "test", component: { render: () => null } },
    ],
  });
  await router.push(path);
  wrapper = mount(LogsView, { global: { plugins: [pinia, router] }, attachTo: document.body });
  await settle();
  return { w: wrapper, router };
}
const button = (w: VueWrapper, text: string) => w.findAll("button").find((b) => b.text() === text)!;
const named = (w: VueWrapper, label: string) => w.find<HTMLButtonElement>(`button[aria-label="${label}"]`);
const older = (w: VueWrapper) => named(w, "Older page");
const lastQuery = () => list.mock.calls.at(-1)![0];
// The Status choices: segments on a desktop, chips on a tablet and a phone.
const statusButtons = (w: VueWrapper) => w.findAll(".lf__seg-btn, .lf [role='group'][aria-label='Status'] .chip");
const statusShown = (w: VueWrapper) =>
  statusButtons(w)
    .filter((b) => b.attributes("aria-pressed") === "true")
    .map((b) => b.text());
const statusWords = (w: VueWrapper): string[] => statusButtons(w).map((b) => b.text());
async function chooseStatus(w: VueWrapper, word: string): Promise<void> {
  await statusButtons(w)
    .find((b) => b.text() === word)!
    .trigger("click");
  await settle();
}
const rowsOf = (w: VueWrapper) => w.findAll("tbody tr");
// The range picker's two inputs, From then To.
const picker = (w: VueWrapper, which: "From" | "To") => w.find<HTMLInputElement>(`input[aria-label="${which}"]`);
// What the operator does with the range: clicks into it (the panel opens), types the two days and presses Enter. The picker reports the change when the panel closes, against the range it had when it opened.
const setRange = async (w: VueWrapper, from: string, to: string) => {
  await picker(w, "From").trigger("focus");
  await settle();
  await picker(w, "From").setValue(from);
  await picker(w, "To").setValue(to);
  await settle();
  await picker(w, "To").trigger("keydown", { code: "Enter", key: "Enter" });
  await settle();
};
// Opens the panel as a click does (press, then focus) and waits for it to be there.
const openPanel = async (w: VueWrapper) => {
  await picker(w, "From").trigger("mousedown");
  await picker(w, "From").trigger("focus");
  await settle();
  await settle();
};
const rangePanel = () => document.body.querySelector<HTMLElement>(".el-date-range-picker")!;
// A day of the month the left panel shows (not a day of a neighbouring month).
const leftDay = (day: string) =>
  [...rangePanel().querySelectorAll<HTMLElement>(".is-left .el-date-table td.available")].find(
    (td) =>
      !td.classList.contains("prev-month") && !td.classList.contains("next-month") && td.textContent?.trim() === day,
  )!;

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
    expect(w.find('[data-testid="logs-table"]').attributes("data-state")).toBe("ready");
    const text = rowsOf(w)[0]!.text();
    expect(w.findAll("thead th").map((h) => h.text())).toEqual([
      "Time",
      "Status",
      "Request",
      "Job",
      "Provider",
      "Key",
      "Error code",
      "Duration",
    ]);
    expect(rowsOf(w)[0]!.findAll("td")[7]!.text()).toBe("42 ms");
    expect(text).toContain(PLANTED);
    expect(w.find("img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("no row is tinted, whatever its status: the pill and the code carry it", async () => {
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
    expect(rowsOf(w).map((r) => r.classes())).toEqual([["is-row"], ["is-row"], ["is-row"], ["is-row"]]);
  });

  it("the status pill is ok below 400, a warning from 400, bad from 500", async () => {
    list.mockResolvedValue({
      items: [
        row({ id: "a", status_code: 503 }),
        row({ id: "b", status_code: 422 }),
        row({ id: "c", status_code: 399 }),
      ],
      total: 3,
    });
    const { w } = await open();
    expect(w.findAll("tbody .pill").map((p) => [p.text(), p.classes()[2]])).toEqual([
      ["503", "tone-bad"],
      ["422", "tone-warn"],
      ["399", "tone-ok"],
    ]);
  });

  it("the error code is plain mono text, red on a 5xx row, amber on a 4xx one, red behind a 200, never a chip", async () => {
    list.mockResolvedValue({
      items: [
        row({ id: "a", status_code: 503, error_code: "upstream_down" }),
        row({ id: "b", status_code: 422, error_code: "input.bad" }),
        row({ id: "c", status_code: 200, error_code: "dola.content_refused" }),
        row({ id: "d", status_code: 200, error_code: null }),
      ],
      total: 4,
    });
    const { w } = await open();
    const codes = w.findAll("tbody .t-err span:not(.faint)");
    expect(codes.map((c) => [c.text(), c.classes()[0]])).toEqual([
      ["upstream_down", "tone-bad"],
      ["input.bad", "tone-warn"],
      ["dola.content_refused", "tone-bad"],
    ]);
    expect(codes[0]!.find("[data-tone]").exists()).toBe(false);
    // no error: a dash
    expect(rowsOf(w)[3]!.find(".t-err").text()).toBe("—");
  });

  it("a duration is in ms, and from one second in seconds with two decimals, in amber", async () => {
    list.mockResolvedValue({
      items: [row({ id: "a", duration_ms: 1840 }), row({ id: "b", duration_ms: 412 })],
      total: 2,
    });
    const { w } = await open();
    const [slow, quick] = rowsOf(w).map((r) => r.findAll("td")[7]!);
    expect([slow!.text(), slow!.classes()]).toEqual(["1.84 s", expect.arrayContaining(["slow"])]);
    expect([quick!.text(), quick!.classes()]).toEqual(["412 ms", expect.not.arrayContaining(["slow"])]);
  });

  it("the time shows its milliseconds beside the clock; the method is told apart from the path", async () => {
    list.mockResolvedValue({
      items: [row({ at_ms: new Date(2026, 9, 5, 8, 4, 9, 7).getTime(), route: "POST /v1/videos" })],
      total: 1,
    });
    const { w } = await open();
    expect(rowsOf(w)[0]!.find(".t-clock").text()).toBe("08:04:09.007");
    expect(rowsOf(w)[0]!.find(".ms").text()).toBe(".007");
    const route = rowsOf(w)[0]!.find(".t-route");
    expect(route.find(".method").text()).toBe("POST");
    expect(route.find(".method").classes()).toContain("post");
    expect(route.text()).toBe("POST /v1/videos");
  });

  it("a row opens its details from a button named by the request id, and from a click on the row", async () => {
    vi.mocked(logs.getLog).mockResolvedValue({ ...row(), request: {}, response: null });
    const { w } = await open();
    expect(w.find('tbody button[aria-label="Open request l1"]').exists()).toBe(true);
    await rowsOf(w)[0]!.trigger("click");
    await settle();
    expect(logs.getLog).toHaveBeenCalledWith("l1");
  });

  it("a value that may end in an ellipsis carries its whole text as its title: route, job title, error code", async () => {
    list.mockResolvedValue({
      items: [row({ status_code: 502, error_code: "dola.content_refused_by_review" })],
      total: 1,
    });
    const { w } = await open();
    const cells = rowsOf(w)[0]!;
    expect(cells.find(".row-open").attributes("title")).toBe("POST /v1/video/generations");
    expect(cells.find(".job-link").attributes("title")).toBe("a title");
    expect(cells.find(".t-err span").attributes("title")).toBe("dola.content_refused_by_review");
  });

  it("the Key cell names a key by its label, else by its prefix as Jobs does; a dash only when there is no key", async () => {
    list.mockResolvedValue({
      items: [
        row({ id: "l1" }),
        row({ id: "l2", key_label: "", key_prefix: "rk-q7mfz2kd" }),
        row({ id: "l3", key_id: null, key_label: null, key_prefix: null }),
      ],
      total: 3,
    });
    const { w } = await open();
    expect(w.findAll("tbody .t-key").map((k) => k.text())).toEqual(["shop", "rk-q7mfz2kd", "—"]);
  });

  it("a row with no job says so with a dash and has no job link; a job's title opens that job", async () => {
    list.mockResolvedValue({
      items: [row({ id: "a", job_id: null, job_title: null }), row({ id: "b" })],
      total: 2,
    });
    const { w } = await open();
    expect(rowsOf(w)[0]!.find(".t-job").text()).toBe("—");
    expect(rowsOf(w)[0]!.find(".job-link").exists()).toBe(false);
    await rowsOf(w)[1]!.find(".job-link").trigger("click");
    expect(useUiStore(pinia).openJobId).toBe("0123456789abcdef");
  });

  it("a request no provider handled shows a dash in the provider cell", async () => {
    list.mockResolvedValue({ items: [row({ provider: null })], total: 1 });
    const { w } = await open();
    expect(rowsOf(w)[0]!.findAll("td")[4]!.text()).toBe("—");
  });

  it("the provider is a chip in its own colours", async () => {
    const { w } = await open();
    const chip = rowsOf(w)[0]!.find(".pchip");
    expect(chip.text()).toBe("vibes");
    expect(chip.classes()).toContain("p-vibes");
  });

  it("the first load asks for 50 rows, all statuses, no dates", async () => {
    await open();
    expect(lastQuery()).toEqual({ limit: 50, offset: 0, status: "all" });
  });

  it("a failed load says what failed and what the relay answered; Retry reads again", async () => {
    list.mockRejectedValue(new ApiError(502, { error: { code: "upstream.bad_gateway", message: "bad gateway" } }));
    const { w } = await open();
    expect(w.find('[role="alert"]').text()).toContain(
      "Could not load the request logs: the relay answered 502 (bad gateway).",
    );
    const calls = list.mock.calls.length;
    list.mockResolvedValue({ items: [row()], total: 1 });
    await button(w, "Retry").trigger("click");
    await settle();
    expect(list.mock.calls.length).toBe(calls + 1);
    expect(w.find('[role="alert"]').exists()).toBe(false);
    expect(rowsOf(w)).toHaveLength(1);
  });

  it("the loading skeleton draws four columns of flat bars: the time, the status pill, the request, the duration", async () => {
    list.mockReturnValue(new Promise(() => undefined));
    const { w } = await open();
    const skeleton = w.find('[data-testid="logs-skeleton"]');
    expect(skeleton.attributes("aria-busy")).toBe("true");
    expect(w.find('[data-testid="logs-table"]').attributes("data-state")).toBe("loading");
    expect(skeleton.findAll("th").map((th) => th.text())).toEqual(["Time", "Status", "Request", "Duration"]);
    expect(skeleton.findAll("tbody tr")).toHaveLength(5);
    expect(
      skeleton
        .find("tbody tr")
        .findAll("td")
        .map((td) => td.find(".bar").classes()[1]),
    ).toEqual(["bar--time", "bar--pill", "bar--route", "bar--dur"]);
  });

  it("a phone's loading skeleton is a list of bars", async () => {
    showAt(390);
    list.mockReturnValue(new Promise(() => undefined));
    const { w } = await open();
    expect(w.find("table").exists()).toBe(false);
    expect(w.findAll(".p-row--skel")).toHaveLength(5);
  });

  it("an empty list with no filter says there are no requests yet and offers a test job", async () => {
    list.mockResolvedValue({ items: [], total: 0 });
    const { w, router } = await open();
    expect(w.find(".lt__title").text()).toBe("No requests yet");
    expect(button(w, "Clear filters")).toBeUndefined();
    await button(w, "Send a test job").trigger("click");
    await settle();
    expect(router.currentRoute.value.name).toBe("test");
  });

  it("an empty list under a filter says so; Clear filters clears every filter and searches from the first page", async () => {
    const { w } = await open();
    list.mockResolvedValue({ items: [], total: 0 });
    // a status alone is a filter
    await chooseStatus(w, "Errors");
    expect(w.find(".lt__title").text()).toBe("No requests match these filters");
    expect(button(w, "Clear filters")).toBeDefined();
    await w.find<HTMLInputElement>('input[aria-label="Search"]').setValue("nothing");
    await setRange(w, "2026-10-01", "2026-10-02");
    list.mockResolvedValue({ items: [row()], total: 1 });
    await button(w, "Clear filters").trigger("click");
    await settle();
    expect(lastQuery()).toEqual({ limit: 50, offset: 0, status: "all" });
    expect(w.find<HTMLInputElement>('input[aria-label="Search"]').element.value).toBe("");
    expect(statusShown(w)).toEqual(["All"]);
    expect(picker(w, "From").element.value).toBe("");
    expect(picker(w, "To").element.value).toBe("");
    expect(rowsOf(w)).toHaveLength(1);
  });

  it("the page says what the API does not give yet instead of drawing the day's numbers", async () => {
    const { w } = await open();
    const stats = w.find('[data-testid="logs-stats"]');
    expect(stats.attributes("aria-label")).toBe("Today in numbers");
    expect(stats.text()).toContain("not served by the API yet");
  });
});

describe("filters", () => {
  it("a range sends the half-open window of the viewer's own midnights, and searches at once", async () => {
    const { w } = await open();
    await setRange(w, "2026-10-01", "2026-10-15");
    expect(lastQuery()).toEqual({
      limit: 50,
      offset: 0,
      status: "all",
      since_ms: dayStart("2026-10-01"),
      until_ms: dayAfter("2026-10-15"),
    });
    expect(lastQuery().until_ms).toBeGreaterThan(dayStart("2026-10-15") + 23 * 3600 * 1000);
  });

  it("a range typed and confirmed with Enter shows in both inputs and sends the same window as a chosen one", async () => {
    const { w } = await open();
    await setRange(w, "2026-10-01", "2026-10-15");
    expect(picker(w, "From").element.value).toBe("2026-10-01");
    expect(picker(w, "To").element.value).toBe("2026-10-15");
    expect(lastQuery().since_ms).toBe(dayStart("2026-10-01"));
    expect(lastQuery().until_ms).toBe(dayAfter("2026-10-15"));
  });

  it("a range typed and left with the focus (blur) is applied", async () => {
    const { w } = await open();
    await picker(w, "From").trigger("focus");
    await picker(w, "From").setValue("2026-10-01");
    await picker(w, "To").setValue("2026-10-15");
    await settle();
    await picker(w, "To").trigger("blur");
    await settle();
    expect(lastQuery().since_ms).toBe(dayStart("2026-10-01"));
    expect(lastQuery().until_ms).toBe(dayAfter("2026-10-15"));
  });

  it("emptying both inputs and leaving them clears the range: no window, and the store holds empty texts", async () => {
    const { w } = await open();
    await setRange(w, "2026-10-01", "2026-10-15");
    await picker(w, "From").trigger("focus");
    await picker(w, "From").setValue("");
    await picker(w, "To").setValue("");
    await settle();
    await picker(w, "To").trigger("blur");
    await settle();
    expect(lastQuery().since_ms).toBeUndefined();
    expect(lastQuery().until_ms).toBeUndefined();
    expect(useLogsFilters(pinia).from).toBe("");
    expect(useLogsFilters(pinia).to).toBe("");
  });

  it("a range picked from the open panel replaces the one held and searches once, for exactly those days", async () => {
    // the held range is in the store before the view mounts, so only the pick can make the search that follows
    useLogsFilters(pinia).from = "2026-10-05";
    useLogsFilters(pinia).to = "2026-10-10";
    const { w } = await open();
    expect(picker(w, "From").element.value).toBe("2026-10-05");
    expect(lastQuery().since_ms).toBe(dayStart("2026-10-05"));
    expect(lastQuery().until_ms).toBe(dayAfter("2026-10-10"));
    await openPanel(w);
    const calls = list.mock.calls.length;
    leftDay("12").click();
    await settle();
    expect(list.mock.calls.length).toBe(calls); // one end alone is not a range yet
    leftDay("20").click();
    await settle();
    expect(picker(w, "From").element.value).toBe("2026-10-12");
    expect(picker(w, "To").element.value).toBe("2026-10-20");
    expect(list.mock.calls.length).toBe(calls + 1);
    expect(lastQuery().since_ms).toBe(dayStart("2026-10-12"));
    expect(lastQuery().until_ms).toBe(dayAfter("2026-10-20"));
  });

  // The clock is the one thing faked, so a quick option is worked out from a known day: 1 March 2026 is where a month
  // end falls inside "Last 30 days" and "Last 7 days".
  it.each([
    ["Today", "2026-03-01", "2026-03-01"],
    ["Yesterday", "2026-02-28", "2026-02-28"],
    ["Last 7 days", "2026-02-23", "2026-03-01"],
    ["Last 30 days", "2026-01-31", "2026-03-01"],
  ])("the quick option %s searches from %s to %s", async (text, first, last) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 2, 1, 12, 0, 0));
    try {
      const { w } = await open();
      await openPanel(w);
      const calls = list.mock.calls.length;
      [...rangePanel().querySelectorAll<HTMLElement>(".el-picker-panel__shortcut")]
        .find((b) => b.textContent?.trim() === text)!
        .click();
      await settle();
      expect(list.mock.calls.length).toBe(calls + 1);
      expect(picker(w, "From").element.value).toBe(first);
      expect(picker(w, "To").element.value).toBe(last);
      expect(lastQuery().since_ms).toBe(dayStart(first));
      expect(lastQuery().until_ms).toBe(dayAfter(last));
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ["only From", "2026-10-01", ""],
    ["only To", "", "2026-10-15"],
  ])("a saved range with %s is cleared at both ends before the first search", async (_, from, to) => {
    useLogsFilters(pinia).from = from;
    useLogsFilters(pinia).to = to;
    const { w } = await open();
    expect(picker(w, "From").element.value).toBe("");
    expect(picker(w, "To").element.value).toBe("");
    expect(useLogsFilters(pinia).from).toBe("");
    expect(useLogsFilters(pinia).to).toBe("");
    // the very first search, not only the last one, carries no date bound
    expect(list.mock.calls[0]![0].since_ms).toBeUndefined();
    expect(list.mock.calls[0]![0].until_ms).toBeUndefined();
  });

  it("the quick options are listed in the order Today, Yesterday, Last 7 days, Last 30 days", async () => {
    const { w } = await open();
    await openPanel(w);
    expect([...rangePanel().querySelectorAll(".el-picker-panel__shortcut")].map((b) => b.textContent?.trim())).toEqual([
      "Today",
      "Yesterday",
      "Last 7 days",
      "Last 30 days",
    ]);
  });

  it("the day text never becomes a Date in the app: the window is the viewer's own midnights, even 14 hours ahead of UTC", async () => {
    const zone = process.env.TZ;
    process.env.TZ = "Pacific/Kiritimati";
    try {
      const { w } = await open();
      await setRange(w, "2026-10-01", "2026-10-01");
      expect(lastQuery().since_ms).toBe(Date.UTC(2026, 9, 1) - 14 * 3600 * 1000);
      expect(lastQuery().until_ms).toBe(Date.UTC(2026, 9, 2) - 14 * 3600 * 1000);
    } finally {
      if (zone === undefined) delete process.env.TZ;
      else process.env.TZ = zone;
    }
  });

  it("the search text is trimmed and Enter searches", async () => {
    const { w } = await open();
    const input = w.find<HTMLInputElement>('input[aria-label="Search"]');
    await input.setValue("  abc ");
    await input.trigger("keydown", { key: "Enter" });
    await settle();
    expect(lastQuery().q).toBe("abc");
  });

  it("the provider filter is sent and a change of it searches again from the first page", async () => {
    const { w } = await open();
    await older(w).trigger("click");
    await settle();
    useScopeStore(pinia).provider = "dola";
    await settle();
    expect(lastQuery()).toMatchObject({ provider: "dola", offset: 0 });
    expect(w.exists()).toBe(true);
  });

  it("the Provider chips are All and each provider of the catalog; a chip is the shared filter and searches", async () => {
    useScopeStore(pinia).models = [
      { id: "m1", owned_by: "vibes" },
      { id: "m2", owned_by: "dola" },
    ];
    const { w } = await open();
    const chips = w.findAll('[data-testid^="logs-provider-"]');
    expect(chips.map((c) => c.text())).toEqual(["All", "dola", "vibes"]);
    expect(chips[0]!.attributes("aria-pressed")).toBe("true");
    await w.find('[data-testid="logs-provider-vibes"]').trigger("click");
    await settle();
    expect(useScopeStore(pinia).provider).toBe("vibes");
    expect(lastQuery()).toMatchObject({ provider: "vibes", offset: 0 });
    expect(w.find('[data-testid="logs-provider-vibes"]').attributes("aria-pressed")).toBe("true");
    await w.find('[data-testid="logs-provider-all"]').trigger("click");
    await settle();
    expect(lastQuery().provider).toBeUndefined();
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
    await setRange(w, "2026-10-01", "2026-10-02");
    await older(w).trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    w.unmount();
    wrapper = null;
    const again = await open();
    expect(lastQuery()).toMatchObject({ offset: 0, q: "keep me", since_ms: dayStart("2026-10-01") });
    expect(picker(again.w, "From").element.value).toBe("2026-10-01");
    expect(picker(again.w, "To").element.value).toBe("2026-10-02");
  });
});

describe("paging", () => {
  it("says which rows are shown of how many, with a thousands comma", async () => {
    list.mockResolvedValue({ items: [row({ id: "a" }), row({ id: "b" })], total: 18442 });
    const { w } = await open();
    expect(w.find(".lv-foot__text").text()).toBe("Showing 1–2 of 18,442");
    await older(w).trigger("click");
    await settle();
    expect(w.find(".lv-foot__text").text()).toBe("Showing 51–52 of 18,442");
  });

  it("Older goes on by a page, Newer goes back, Newer is off on the first page and Older on the last", async () => {
    // a page is 50 rows, the last one what is left of 120
    list.mockImplementation(async (q) => ({
      items: Array.from({ length: Math.min(50, 120 - q.offset) }, (_, i) => row({ id: `r${q.offset + i}` })),
      total: 120,
    }));
    const { w } = await open();
    expect(named(w, "Newer page").attributes("disabled")).toBeDefined();
    expect(older(w).attributes("disabled")).toBeUndefined();
    await older(w).trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    expect(named(w, "Newer page").attributes("disabled")).toBeUndefined();
    await older(w).trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(100);
    expect(older(w).attributes("disabled")).toBeDefined();
    await named(w, "Newer page").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
  });

  it("an empty page has no pager", async () => {
    list.mockResolvedValue({ items: [], total: 0 });
    const { w } = await open();
    expect(w.find(".lv-foot").exists()).toBe(false);
  });

  it("a search goes back to the first page; there is no Refresh and no Updated line", async () => {
    const { w } = await open();
    await older(w).trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    expect(button(w, "Refresh")).toBeUndefined();
    expect(w.text()).not.toContain("Updated");
    await w.find('input[aria-label="Search"]').trigger("keydown", { key: "Enter" });
    await settle();
    expect(lastQuery().offset).toBe(0);
  });

  it("a status choice searches at once from the first page", async () => {
    const { w } = await open();
    await older(w).trigger("click");
    await settle();
    await chooseStatus(w, "Errors");
    expect(lastQuery()).toMatchObject({ status: "error", offset: 0 });
    expect(statusWords(w)).toEqual(["All", "OK below 400", "Errors", "Failed before a job"]);
    expect(statusShown(w)).toEqual(["Errors"]);
  });
});

describe("clear logs", () => {
  it("is an icon button that asks first, then says how many rows went and goes to the first page", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(true);
    vi.mocked(logs.clearLogs).mockResolvedValue({ deleted: 17 });
    const { w } = await open();
    await older(w).trigger("click");
    await settle();
    await named(w, "Clear logs").trigger("click");
    await settle();
    expect(confirm.confirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Clear every request log row?",
        message: "Jobs, the ledger and alerts are kept.",
        confirmText: "Clear logs",
        danger: true,
      }),
    );
    expect(notify.success).toHaveBeenCalledWith("Cleared 17 rows");
    expect(lastQuery().offset).toBe(0);
  });

  it("a cancel deletes nothing", async () => {
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    const { w } = await open();
    await named(w, "Clear logs").trigger("click");
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
  const openOne = async (log: logs.LogDetail) => {
    vi.mocked(logs.getLog).mockResolvedValue(log);
    const { w } = await open();
    await w.find('tbody button[aria-label="Open request l1"]').trigger("click");
    await settle();
    return { w, drawer: document.body.querySelector('[data-testid="request-drawer"]')! };
  };

  it("shows the request and the answer as text, titled with the prompt", async () => {
    const { drawer } = await openOne(detail());
    expect(logs.getLog).toHaveBeenCalledWith("l1");
    expect(drawer.getAttribute("role")).toBe("dialog");
    expect(drawer.querySelector("h2")!.textContent).toBe(PLANTED);
    expect(drawer.textContent).toContain("planted");
    expect(drawer.querySelector("img")).toBeNull();
    expect(drawer.textContent).toContain('"ok": true');
  });

  it("is titled with the job's title when the body carries no prompt, else with the request id", async () => {
    const withJob = await openOne(detail({ request: {} }));
    expect(withJob.drawer.querySelector("h2")!.textContent).toBe("a title");
    withJob.w.unmount();
    wrapper = null;
    const bare = await openOne(detail({ request: {}, job_title: null }));
    expect(bare.drawer.querySelector("h2")!.textContent).toBe("Request l1");
  });

  it("names a key with no label by its prefix", async () => {
    const { drawer } = await openOne(detail({ key_label: "", key_prefix: "rk-q7mfz2kd" }));
    const key = [...drawer.querySelectorAll('[data-testid="request-facts"] .rd__row')].find(
      (r) => r.querySelector("dt")!.textContent === "Key",
    );
    expect(key!.querySelector("dd")!.textContent!.trim()).toBe("rk-q7mfz2kd");
  });

  it("an answer that was not kept says so", async () => {
    const { drawer } = await openOne(detail({ response: null }));
    expect(drawer.textContent).toContain("(none kept)");
    expect(drawer.querySelector('button[aria-label="Copy answer body"]')).toBeNull();
  });

  it("lists the facts as a ledger and gives each body its own Copy", async () => {
    const { drawer } = await openOne(
      detail({
        status_code: 422,
        error_code: "dola.content_refused",
        duration_ms: 1980,
        job_id: "5a0f3c88aaaa",
        at_ms: new Date(2026, 9, 24, 14, 31, 58, 903).getTime(),
      }),
    );
    const facts = [...drawer.querySelectorAll('[data-testid="request-facts"] .rd__row')].map((r) => [
      r.querySelector("dt")!.textContent,
      r.querySelector("dd")!.textContent!.trim(),
    ]);
    expect(facts.map((f) => f[0])).toEqual(["Time", "Duration", "Provider", "Key", "Job", "Error"]);
    expect(facts.find((f) => f[0] === "Time")![1]).toBe("24 Oct 14:31:58.903");
    expect(facts.find((f) => f[0] === "Job")![1]).toBe("5a0f3c88");
    expect(facts.find((f) => f[0] === "Error")![1]).toBe("dola.content_refused");
    expect(facts.find((f) => f[0] === "Duration")![1]).toBe("1.98 s");
    expect(drawer.querySelector('button[aria-label="Copy request body"]')).not.toBeNull();
    expect(drawer.querySelector('button[aria-label="Copy answer body"]')).not.toBeNull();
    expect(drawer.querySelectorAll("pre")).toHaveLength(2);
  });

  it("shows the status pill and the route under the title, the method told apart", async () => {
    const { drawer } = await openOne(detail({ status_code: 402, route: "POST /v1/videos" }));
    expect(drawer.querySelector(".rd__pill")!.textContent).toBe("402");
    expect(drawer.querySelector(".rd__pill")!.className).toContain("tone-warn");
    expect(drawer.querySelector(".rd__route")!.textContent!.replace(/\s+/g, " ").trim()).toBe("POST /v1/videos");
    expect(drawer.querySelector(".rd__method")!.className).toContain("post");
  });

  it("says what the API does not keep: the request headers, and the upstream exchange", async () => {
    const { drawer } = await openOne(detail());
    expect(drawer.textContent).toContain("The API does not keep the upstream exchange yet.");
    const headers = [...drawer.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(
      (t) => t.textContent?.trim() === "Headers",
    )!;
    headers.click();
    await settle();
    expect(drawer.textContent).toContain("The API does not keep request headers yet.");
    expect(drawer.querySelectorAll("pre")).toHaveLength(1);
  });

  it("a request that made no job says it ended at the relay", async () => {
    const { drawer } = await openOne(detail({ job_id: null, job_title: null, provider: "vibes" }));
    expect(drawer.textContent).toContain("The request ended at the relay: no job was made, so nothing reached vibes.");
  });

  it("colours the JSON of a body: keys, strings, numbers and literals each in their own class", async () => {
    const { drawer } = await openOne(detail({ request: { prompt: "a cat", n: 1, ok: true } }));
    const pre = drawer.querySelector("pre")!;
    expect([...pre.querySelectorAll(".key")].map((e) => e.textContent)).toEqual(['"prompt"', '"n"', '"ok"']);
    expect([...pre.querySelectorAll(".str")].map((e) => e.textContent)).toEqual(['"a cat"']);
    expect([...pre.querySelectorAll(".num")].map((e) => e.textContent)).toEqual(["1"]);
    expect([...pre.querySelectorAll(".lit")].map((e) => e.textContent)).toEqual(["true"]);
  });

  it("the job link closes the drawer and opens the job drawer", async () => {
    const { w } = await openOne(detail());
    document.body.querySelector<HTMLButtonElement>('[data-testid="request-facts"] .rd__link')!.click();
    await settle();
    expect(useUiStore(pinia).openJobId).toBe("0123456789abcdef");
    expect(w.findComponent({ name: "LogDetailDrawer" }).props("modelValue")).toBe(false);
  });

  it("a request with no job shows no job link", async () => {
    const { drawer } = await openOne(detail({ job_id: null, job_title: null }));
    expect(document.body.querySelector(".rd__link")).toBeNull();
    expect(drawer.querySelector('[data-testid="request-facts"]')!.textContent).toContain("no job");
  });

  it("Back and Close both leave the drawer", async () => {
    const { w, drawer } = await openOne(detail());
    expect(drawer.querySelector('button[aria-label="Close · Esc"]')).not.toBeNull();
    drawer.querySelector<HTMLButtonElement>('button[aria-label="Back to Request logs"]')!.click();
    await settle();
    expect(w.findComponent({ name: "LogDetailDrawer" }).props("modelValue")).toBe(false);
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

  it("keeps five columns, the status first, folds the error code or the job title, and the key, under the route", async () => {
    showAt(768);
    rich();
    const { w } = await open();
    expect(w.findAll("thead th").map((h) => h.text())).toEqual(["Status", "Time", "Request", "Provider", "Duration"]);
    const [first, second] = rowsOf(w);
    expect(first!.find(".row-open").text()).toBe("POST /v1/video/generations");
    expect(first!.find(".t-sub").text()).toBe("account.dead_session · shop");
    expect(first!.find(".t-sub").classes()).toContain("tone-bad");
    expect(first!.findAll("td")[4]!.text()).toBe("2.14 s");
    expect(second!.find(".t-sub").text()).toBe("shop");
  });

  it("puts the job's title in that line when there is no error, and says 'job failed' behind a 200", async () => {
    showAt(768);
    list.mockResolvedValue({
      items: [row({ id: "a" }), row({ id: "b", status_code: 200, error_code: "dola.content_refused" })],
      total: 2,
    });
    const { w } = await open();
    expect(rowsOf(w)[0]!.find(".t-sub").text()).toBe("a title · shop");
    expect(rowsOf(w)[1]!.find(".t-sub").text()).toBe("job failed: dola.content_refused · shop");
  });

  it("Clear logs is the same icon button, there is no More menu, and the title is left to the top bar", async () => {
    showAt(768);
    vi.mocked(confirm.confirmDialog).mockResolvedValue(false);
    const { w } = await open();
    expect(w.find('button[aria-label="More actions"]').exists()).toBe(false);
    expect(w.find("h1").classes()).toContain("sr-only");
    await named(w, "Clear logs").trigger("click");
    await settle();
    expect(confirm.confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ confirmText: "Clear logs" }));
    expect(logs.clearLogs).not.toHaveBeenCalled();
  });

  it("holds the search behind the Search button and the dates behind Filters", async () => {
    showAt(768);
    const { w } = await open();
    expect(w.find('input[aria-label="Search"]').exists()).toBe(false);
    expect(w.find('input[aria-label="From"]').exists()).toBe(false);
    await named(w, "Search requests").trigger("click");
    await settle();
    expect(w.find('input[aria-label="Search"]').exists()).toBe(true);
    await named(w, "Filters: dates").trigger("click");
    await settle();
    const sheet = document.body.querySelector('[data-testid="logs-filters-sheet"]')!;
    expect(sheet.querySelector('input[aria-label="From"]')).not.toBeNull();
    expect(sheet.querySelector('input[aria-label="To"]')).not.toBeNull();
  });

  it("a search typed stays visible after the Search button is pressed again", async () => {
    showAt(768);
    const { w } = await open();
    await named(w, "Search requests").trigger("click");
    await w.find<HTMLInputElement>('input[aria-label="Search"]').setValue("abc");
    await named(w, "Search requests").trigger("click");
    await settle();
    expect(w.find('input[aria-label="Search"]').exists()).toBe(true);
  });
});

describe("the provider of a request", () => {
  it("is a chip in its tone; a request no provider handled has none, a dash in a table", async () => {
    showAt(768);
    list.mockResolvedValue({ items: [row({ id: "a" }), row({ id: "b", provider: null })], total: 2 });
    const { w } = await open();
    const [vibes, none] = rowsOf(w);
    expect(vibes!.find(".pchip").classes()).toContain("p-vibes");
    expect(none!.find(".pchip").exists()).toBe(false);
    expect(none!.findAll("td")[3]!.text()).toBe("—");
  });

  it("a phone row has the chip too, and none when there is no provider", async () => {
    showAt(390);
    list.mockResolvedValue({ items: [row({ id: "a" }), row({ id: "b", provider: null })], total: 2 });
    const { w } = await open();
    const [vibes, none] = w.findAll(".p-row");
    expect([vibes!.find(".pchip").text(), vibes!.find(".pchip").classes().includes("p-vibes")]).toEqual([
      "vibes",
      true,
    ]);
    expect(none!.find(".pchip").exists()).toBe(false);
  });
});

describe("the phone", () => {
  it("lists a request as short lines, the whole row opening the details", async () => {
    showAt(390);
    list.mockResolvedValue({
      items: [row({ status_code: 502, error_code: "account.dead_session", duration_ms: 2140 })],
      total: 1,
    });
    vi.mocked(logs.getLog).mockResolvedValue({ ...row(), request: {}, response: null });
    const { w } = await open();
    expect(w.find("table").exists()).toBe(false);
    const item = w.find(".p-row");
    expect(item.find(".p-row__route").text()).toBe("POST /v1/video/generations");
    expect(item.find(".p-row__right").text()).toMatch(/^\d\d:\d\d:\d\d · 2\.14 s$/);
    expect(item.find(".p-row__sub").text()).toBe("account.dead_session · shop");
    expect(item.find(".p-row__sub").classes()).toContain("tone-bad");
    expect(w.find('[aria-label="Open request l1"]').exists()).toBe(true);
    await w.find('[aria-label="Open request l1"]').trigger("click");
    await settle();
    expect(logs.getLog).toHaveBeenCalledWith("l1");
  });

  it("wraps its four status chips on one set and has no Search button: a choice searches at once, typing after a pause", async () => {
    showAt(390);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const { w } = await open();
      expect(button(w, "Search")).toBeUndefined();
      expect(w.find('[data-testid="arrow-scroller"]').classes()).toContain("asc--wrap");
      await chooseStatus(w, "Errors");
      expect(lastQuery().status).toBe("error");
      await named(w, "Search requests").trigger("click");
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

  it("Load older adds the next page under the rows it has, and goes once every row is there", async () => {
    showAt(390);
    list.mockResolvedValueOnce({ items: [row({ id: "a" }), row({ id: "b" })], total: 3 });
    const { w } = await open();
    expect(w.find(".lv-foot").exists()).toBe(false);
    list.mockResolvedValueOnce({ items: [row({ id: "c" })], total: 3 });
    await button(w, "Load older").trigger("click");
    await settle();
    expect(lastQuery().offset).toBe(50);
    expect(w.findAll(".p-row").map((r) => r.attributes("aria-label"))).toEqual([
      "Open request a",
      "Open request b",
      "Open request c",
    ]);
    expect(button(w, "Load older")).toBeUndefined();
  });

  it("a new search starts the list again from the first page, not under the old rows", async () => {
    showAt(390);
    list.mockResolvedValueOnce({ items: [row({ id: "a" })], total: 99 });
    const { w } = await open();
    list.mockResolvedValueOnce({ items: [row({ id: "b" })], total: 99 });
    await button(w, "Load older").trigger("click");
    await settle();
    list.mockResolvedValueOnce({ items: [row({ id: "z" })], total: 1 });
    await chooseStatus(w, "Errors");
    expect(w.findAll(".p-row").map((r) => r.attributes("aria-label"))).toEqual(["Open request z"]);
  });
});

describe("typing on a desktop", () => {
  it("does not search by itself: Enter does", async () => {
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
    await w.find('tbody button[aria-label="Open request l1"]').trigger("click");
    await settle();
    return document.body.querySelector('[data-testid="request-drawer"]')!;
  };
  const writeText = vi.fn(async (_text: string) => undefined);

  it("an error code is amber on a client error, red on a server error and behind a 200", async () => {
    const errorLine = (drawer: Element) =>
      [...drawer.querySelectorAll(".rd__row")].find((r) => r.querySelector("dt")?.textContent === "Error")!;
    const tone = async (status: number) => {
      const drawer = await openDrawer(detail({ status_code: status, error_code: "x.y" }));
      const out = errorLine(drawer).querySelector("dd")!.className;
      wrapper!.unmount();
      wrapper = null;
      document.body.replaceChildren();
      return out;
    };
    expect(await tone(502)).toContain("tone-bad");
    expect(await tone(422)).toContain("tone-warn");
    expect(await tone(200)).toContain("tone-bad");
  });

  it("a request with no error has no Error line", async () => {
    const drawer = await openDrawer(detail());
    expect(drawer.textContent).not.toContain("Error");
  });

  it("a body that is already text is shown as it is, not quoted", async () => {
    const drawer = await openDrawer(detail({ request: "plain text body", response: "an answer in words" }));
    const [request, answer] = [...drawer.querySelectorAll("pre")].map((p) => p.textContent!.trim());
    expect(request).toBe("plain text body");
    expect(answer).toBe("an answer in words");
  });

  it("each Copy copies its own body and says so", async () => {
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    writeText.mockClear();
    const drawer = await openDrawer(detail());
    const copy = (label: string) => drawer.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
    copy("Copy request body").click();
    await settle();
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify({ prompt: "a cat" }, null, 2));
    expect(notify.success).toHaveBeenLastCalledWith("Copied the request body");
    // the copied one shows a tick for a moment, the other keeps its copy icon
    expect(copy("Copy request body").querySelector("path")!.getAttribute("d")).toBe("M20 6 9 17l-5-5");
    expect(copy("Copy answer body").querySelector("path")!.getAttribute("d")).toMatch(/^M9 9h10/);
    copy("Copy answer body").click();
    await settle();
    expect(writeText).toHaveBeenLastCalledWith(JSON.stringify({ ok: true }, null, 2));
    expect(notify.success).toHaveBeenLastCalledWith("Copied the answer body");
  });
});

describe("the request drawer on a phone", () => {
  it("is a drawer whose way back names the page it returns to", async () => {
    showAt(390);
    vi.mocked(logs.getLog).mockResolvedValue({ ...row(), request: {}, response: null });
    const { w } = await open();
    await w.find('[aria-label="Open request l1"]').trigger("click");
    await settle();
    const drawer = document.body.querySelector('[data-testid="request-drawer"]')!;
    expect(drawer.querySelector('button[aria-label="Back to Request logs"]')).not.toBeNull();
  });
});
