import type { AlertGroup } from "@/api/alerts";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { ApiError } from "@/api/client";
import * as api from "@/api/jobs";
import type { JobPage, JobSummary } from "@/api/jobs";
import * as shell from "@/api/shell";
import { announceJobChange } from "@/components/jobs/job-changes";
import { rememberedStatus } from "@/components/jobs/job-filters";
import { dayKey, localZone } from "@/components/jobs/job-row";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import ListPager from "@/components/shared/ListPager.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { RETRY_SAFE_TEXT } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";
import JobsView from "@/views/JobsView.vue";

vi.mock("@/api/jobs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/jobs")>();
  return {
    ...actual,
    listJobs: vi.fn(),
    listKeyOptions: vi.fn(),
    bulkRecheck: vi.fn(),
    bulkRegenerate: vi.fn(),
  };
});
vi.mock("@/api/shell", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/shell")>();
  return {
    ...actual,
    getStats: vi.fn(),
    getOpenAlertGroups: vi.fn(),
    getNewestReply: vi.fn(),
    getProvidersSummary: vi.fn(),
  };
});
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

const listJobs = vi.mocked(api.listJobs);
const bulkRecheck = vi.mocked(api.bulkRecheck);
const bulkRegenerate = vi.mocked(api.bulkRegenerate);

const job = (id: string, over: Partial<JobSummary> = {}): JobSummary => ({
  id,
  provider: "dola",
  key_id: "k1",
  key_label: "key-one",
  operation: "video",
  media_type: "video",
  model: "veo",
  title: `title of ${id}`,
  status: "done",
  error_code: null,
  price: 10,
  account_id: null,
  account_label: "acct-1",
  external_ref: null,
  created_ms: 1_700_000_000_000,
  finished_ms: null,
  error_status: null,
  can_recheck: false,
  outputs: 1,
  inputs: 0,
  net_charged: 10,
  model_substituted: false,
  ...over,
});
// A page of the list: `total` is every job the filters match (the ids on the page when not given).
const page = (ids: string[], total: number = ids.length): JobPage => ({
  items: ids.map((id) => job(id)),
  total,
  next_cursor: null,
});
const query = (over: Partial<api.JobListQuery> = {}): api.JobListQuery => ({
  status: "",
  provider: "",
  keyId: "",
  offset: 0,
  limit: 10,
  tz: expect.any(String),
  ...over,
});
// The stats strip behind the band's ledger (the canvas numbers).
const STATS: shell.Stats = {
  jobs: { queued: 1, in_progress: 3, done: 1284, failed: 37, pre_job_failures_24h: 6 },
  made: { videos: 335, images: 120 },
  accounts: { usable: 7, available: 8, dead: 1, disabled: 0 },
  keys: 5,
  alerts: [],
};
// The provider summary behind the strip and the "Jobs today" table: none of these tests is about it.
const SUMMARY: shell.ProvidersSummary = { day_tz: "UTC", day_start_ms: 0, providers: [], hours: [] };
const A = "aaaaaaaa-1111";
const B = "bbbbbbbb-2222";
const C = "cccccccc-3333";

const mounted: VueWrapper[] = [];
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
  document.body.replaceChildren();
});

beforeEach(() => {
  sessionStorage.clear();
  rememberedStatus.value = "";
  listJobs.mockReset().mockResolvedValue(page([A, B, C]));
  vi.mocked(api.listKeyOptions)
    .mockReset()
    .mockResolvedValue({ items: [{ id: "k1", label: "key-one" }] });
  bulkRecheck.mockReset();
  bulkRegenerate.mockReset();
  vi.mocked(shell.getStats).mockReset().mockResolvedValue(STATS);
  vi.mocked(shell.getOpenAlertGroups).mockReset().mockResolvedValue({ items: [] });
  vi.mocked(shell.getProvidersSummary).mockReset().mockResolvedValue(SUMMARY);
  vi.mocked(confirmDialog).mockReset().mockResolvedValue(true);
  for (const fn of Object.values(notify)) vi.mocked(fn).mockReset();
});

async function open(url = "/jobs") {
  setActivePinia(createPinia());
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/jobs", name: "jobs", component: JobsView },
      { path: "/alerts", name: "alerts", component: { render: () => null } },
    ],
  });
  await router.push(url);
  const w = mount(JobsView, { global: { plugins: [router] }, attachTo: document.body });
  mounted.push(w);
  await flushPromises();
  await flushPromises();
  return { w, router };
}

// the layout the test mounts (stubWidth sets it): a table on a desktop, stacked items on a phone
let layout: "phone" | "tablet" | "desktop" = "desktop";
const rows = (w: VueWrapper) => w.findAll(layout === "phone" ? "li.data-table__item" : "tbody tr:not(.is-day)");
const rowTick = (w: VueWrapper, i: number) => rows(w)[i]!.find("input[type=checkbox]");
const selectAll = (w: VueWrapper) => w.find("thead input[type=checkbox]");
// The header box ticks every row through a deferred table call, so the count is waited for.
async function tickAll(w: VueWrapper, on: boolean, shown: string): Promise<void> {
  await selectAll(w).setValue(on);
  await vi.waitFor(() => expect(w.find(".jobs__count").text()).toBe(shown));
}
// a button by its text, or (the phone's icon-only Refresh) by the start of its aria-label
const button = (w: VueWrapper, text: string) =>
  w.findAll("button").find((b) => b.text() === text || b.attributes("aria-label")?.startsWith(`${text},`))!;
const count = (w: VueWrapper) => w.find(".jobs__count").text();
const selects = (w: VueWrapper) => w.findAllComponents({ name: "FilterSelect" });
const lastQuery = () => listJobs.mock.lastCall![0];

// matchMedia answering by the width named in the query: the phone edge is 767.x, the tablet band ends at 1279.x
function stubWidth(kind: "phone" | "tablet" | "desktop") {
  layout = kind;
  vi.stubGlobal("matchMedia", (q: string) => ({
    matches: kind === "phone" ? /max-width:\s*767/.test(q) : kind === "tablet" ? /1279/.test(q) : false,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}
const dayKeyOf = (ms: number | null): string => dayKey(ms, localZone());
// every test starts on a desktop; the tablet and phone tests say so
beforeEach(() => stubWidth("desktop"));

describe("the list", () => {
  it("loads the first 10 jobs with no filter", async () => {
    await open();
    expect(listJobs).toHaveBeenCalledTimes(1);
    expect(listJobs).toHaveBeenCalledWith(query());
  });

  it("shows the ten columns of the Jobs table, the provider first", async () => {
    const { w } = await open();
    const headers = w.findAll("thead th").map((th) => th.text());
    expect(headers).toEqual([
      "",
      "Provider",
      "Time",
      "Job",
      "Status",
      "Model",
      "Account",
      "Key",
      "Out · Refs",
      "Net points",
    ]);
  });

  it("a tablet drops Account, Key and Out · Refs and folds them into the Job cell", async () => {
    stubWidth("tablet");
    listJobs.mockResolvedValue({ items: [job(A, { account_label: null })], total: 1, next_cursor: null });
    const { w } = await open();
    expect(w.findAll("thead th").map((th) => th.text())).toEqual([
      "",
      "Provider",
      "Time",
      "Job",
      "Status",
      "Model",
      "Net points",
    ]);
    expect(rows(w)[0]!.find(".jobs__id").text()).toBe("aaaaaaaa · — · key-one");
  });

  it("a phone draws a stacked row: title and status, then id, model and account, then time, key and net", async () => {
    stubWidth("phone");
    listJobs.mockResolvedValue({
      items: [job(A, { status: "failed", error_code: "render.deadline", price: 10, net_charged: 0 })],
      total: 1,
      next_cursor: null,
    });
    const { w } = await open();
    expect(w.find("thead").exists()).toBe(false);
    const row = w.find(".prow");
    expect(row.find(".prow__title").text()).toBe(`title of ${A}`);
    expect(row.find(".prow__ids").text()).toBe("aaaaaaaa · veo · acct-1");
    expect(row.find(".prow__meta").text()).toMatch(/^\d\d:\d\d:\d\d · key-one · render\.deadline$/);
    expect(row.find('[data-testid="money-cell"]').attributes("data-refunded")).toBe("true");
    expect(w.find("[aria-label^='Select job aaaaaaaa']").exists()).toBe(true);
  });

  it("draws one row per job with the merged cells", async () => {
    listJobs.mockResolvedValue({
      items: [
        job(A, { title: null, model_substituted: true }),
        job(B, { status: "failed", error_code: "render.deadline", price: 10, net_charged: 0 }),
      ],
      total: 2,
      next_cursor: null,
    });
    const { w } = await open();
    const [first, second] = rows(w);
    expect(first!.text()).toContain("(untitled)");
    expect(first!.text()).toContain("aaaaaaaa");
    expect(first!.text()).toContain("video veo");
    expect(first!.text()).toContain("model differs");
    expect(first!.find('[data-testid="money-cell"]').attributes("data-refunded")).toBe("false");
    expect(second!.text()).toContain("failed");
    expect(second!.find('[data-testid="money-cell"]').attributes("data-refunded")).toBe("true");
    expect(second!.text()).toContain("render.deadline");
    expect(second!.text()).toContain("acct-1");
    expect(second!.text()).toContain("key-one");
  });

  it("the status chip keeps its own tone, a refund is the struck charge in the money cell, and the error code is plain text", async () => {
    listJobs.mockResolvedValue({
      items: [job(B, { status: "failed", error_code: "render.deadline", price: 10, net_charged: 0 })],
      total: 1,
      next_cursor: null,
    });
    const { w } = await open();
    const row = rows(w)[0]!;
    expect(row.find('[data-tone="bad"]').text()).toBe("failed");
    expect(row.find('[data-tone="neutral"]').exists()).toBe(false);
    expect(row.find('[data-tone="warn"]').exists()).toBe(false);
    expect(row.find(".money__price").text()).toBe("10");
    expect(row.find(".money__net").text()).toBe("0");
    const code = row.find(".jobs__code");
    expect(code.text()).toBe("render.deadline");
    expect(code.find("[data-tone]").exists()).toBe(false);
  });

  it("on a desktop the Key column grows to its longest value up to 120 px, and Job gives up the room", async () => {
    // happy-dom lays nothing out: a key value whose text is 150 px wide, in a 1302 px table
    const text = vi.spyOn(Range.prototype, "getBoundingClientRect").mockImplementation(function (this: Range) {
      const inKey = (this.startContainer as Element).classList?.contains("jobs__key");
      return { width: inKey ? 150 : 0 } as DOMRect;
    });
    const saved = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 1302 });
    try {
      const { w } = await open();
      await flushPromises();
      const vars = (w.get(".jobs__table").element as HTMLElement).style;
      expect(vars.getPropertyValue("--jobs-col-key")).toBe("120px");
      expect(parseFloat(vars.getPropertyValue("--jobs-col-account"))).toBeCloseTo((1302 * 6.5438) / 100, 6);
      expect(parseFloat(vars.getPropertyValue("--jobs-col-job"))).toBeCloseTo(
        (1302 * 20.3257) / 100 - (120 - (1302 * 7.4347) / 100),
        6,
      );
    } finally {
      text.mockRestore();
      if (saved) Object.defineProperty(HTMLElement.prototype, "offsetWidth", saved);
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth;
    }
  });

  it("the widths follow the table element's own width: first laid out narrow, then at the card's width", async () => {
    // Element Plus lays the table out at its columns' minimum widths (1084 px) before the card's (1302 px). A key
    // value of 84.5 px ("Partner C" with its padding; no stylesheet here) overflows the drawn Key at 1084 and fits it
    // at 1302, where the drawn shares come back.
    let width = 1084;
    const watched: Array<[Element, () => void]> = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        callback: () => void;
        constructor(callback: () => void) {
          this.callback = callback;
        }
        observe(el: Element) {
          watched.push([el, this.callback]);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    const text = vi.spyOn(Range.prototype, "getBoundingClientRect").mockImplementation(function (this: Range) {
      const inKey = (this.startContainer as Element).classList?.contains("jobs__key");
      return { width: inKey ? 84.5 : 0 } as DOMRect;
    });
    const saved = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => width });
    try {
      const { w } = await open();
      await flushPromises();
      const vars = (w.get(".jobs__table").element as HTMLElement).style;
      const table = w.get(".el-table__body").element;
      const onTable = watched.filter(([el]) => el === table);
      expect(onTable).toHaveLength(1);
      expect(vars.getPropertyValue("--jobs-col-key")).toBe("84.5px");
      width = 1302;
      onTable[0]![1]();
      await flushPromises();
      expect(vars.getPropertyValue("--jobs-col-key")).toBe("");
      expect(vars.getPropertyValue("--jobs-col-job")).toBe("");
    } finally {
      text.mockRestore();
      if (saved) Object.defineProperty(HTMLElement.prototype, "offsetWidth", saved);
      else delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth;
      vi.stubGlobal(
        "ResizeObserver",
        class {
          observe() {}
          unobserve() {}
          disconnect() {}
        },
      );
    }
  });

  it("values that fit their drawn widths leave the drawn column shares alone", async () => {
    const { w } = await open();
    await flushPromises();
    expect((w.get(".jobs__table").element as HTMLElement).style.getPropertyValue("--jobs-col-key")).toBe("");
  });

  it("an empty list has no count in its toolbar, as the no-results card draws it", async () => {
    listJobs.mockResolvedValue(page([]));
    const { w } = await open();
    expect(w.get(".jobs__count").isVisible()).toBe(false);
    expect(button(w, "Re-check").isVisible()).toBe(false);
  });

  it("says there are no jobs for an empty list, and that no job matches a filter", async () => {
    listJobs.mockResolvedValue(page([]));
    const { w } = await open();
    expect(w.text()).toContain("No jobs yet.");
    expect(w.text()).not.toContain("Clear filters");
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await flushPromises();
    expect(w.text()).toContain("No jobs match these filters.");
    await button(w, "Clear filters").trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query());
  });

  it("a job still on its way shows its step before the word, a settled one a dot", async () => {
    listJobs.mockResolvedValue({
      items: [job(A, { status: "fetching" }), job(B, { status: "queued" }), job(C)],
      total: 3,
      next_cursor: null,
    });
    const { w } = await open();
    const chip = (i: number) => rows(w)[i]!.find(".status-chip");
    expect(chip(0).find(".step").text()).toBe("3/4");
    expect(chip(0).find(".dot").exists()).toBe(false);
    expect(chip(1).find(".step").text()).toBe("1/4");
    expect(chip(2).find(".step").exists()).toBe(false);
    expect(chip(2).find(".dot").exists()).toBe(true);
  });

  it("a failed job is tinted and an unknown one is not", async () => {
    listJobs.mockResolvedValue({
      items: [job(A, { status: "failed" }), job(B, { status: "unknown" }), job(C)],
      total: 3,
      next_cursor: null,
    });
    const { w } = await open();
    expect(rows(w)[0]!.classes()).toContain("row-tone-bad");
    expect(rows(w)[1]!.classes().join(" ")).not.toContain("row-tone");
    expect(rows(w)[2]!.classes().join(" ")).not.toContain("row-tone");
  });

  it("net points: a job still on its way that has charged nothing shows a dash, any other its net", async () => {
    listJobs.mockResolvedValue({
      items: [
        job(A, { status: "queued", price: 0, net_charged: 0 }),
        job(B, { status: "queued", price: 2, net_charged: 2 }),
        job(C, { status: "done", price: 0, net_charged: 0 }),
      ],
      total: 3,
      next_cursor: null,
    });
    const { w } = await open();
    const net = (i: number) => rows(w)[i]!.find(".money__net").text();
    expect([net(0), net(1), net(2)]).toEqual(["—", "2", "0"]);
  });

  it("groups the rows under one header per day, with the jobs shown for that day", async () => {
    const { w } = await open();
    const days = w.findAll('[data-testid="day-row"]');
    expect(days).toHaveLength(1);
    expect(days[0]!.text()).toContain("· 3 jobs shown");
  });

  it("shows a failed load with Retry, and Retry asks again", async () => {
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    const { w } = await open();
    expect(w.find("[role=alert]").text()).toContain("boom");
    await button(w, "Retry").trigger("click");
    await flushPromises();
    expect(listJobs).toHaveBeenCalledTimes(2);
    expect(rows(w)).toHaveLength(3);
  });

  it("drops an old answer that arrives after a newer one", async () => {
    let slow!: (p: JobPage) => void;
    listJobs.mockReset();
    listJobs.mockImplementationOnce(() => new Promise<JobPage>((res) => (slow = res)));
    listJobs.mockResolvedValueOnce(page([C]));
    const { w } = await open();
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await flushPromises();
    expect(rows(w)).toHaveLength(1);
    slow(page([A, B]));
    await flushPromises();
    expect(rows(w)).toHaveLength(1);
    expect(rows(w)[0]!.text()).toContain("cccccccc");
  });

  it("reloads when the detail announces a change", async () => {
    await open();
    announceJobChange();
    await flushPromises();
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("reads the key options once, and a failure is a toast", async () => {
    vi.mocked(api.listKeyOptions).mockRejectedValueOnce(
      new ApiError(500, { error: { code: "x", message: "no keys" } }),
    );
    await open();
    expect(notify.error).toHaveBeenCalledWith("no keys");
  });
});

describe("filters", () => {
  it("the status filter reloads from page 1 with the status, submitting included", async () => {
    const { w } = await open();
    selects(w)[0]!.vm.$emit("update:modelValue", "submitting");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "submitting" }));
  });

  it("the key filter sends the key id", async () => {
    const { w } = await open();
    selects(w)[1]!.vm.$emit("update:modelValue", "k1");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ keyId: "k1" }));
  });

  it("shows All, not the placeholder, while nothing is filtered, after the word Status or Key", async () => {
    const { w } = await open();
    const shown = (i: number) => selects(w)[i]!.find(".filter-select__value").text();
    expect(shown(0)).toBe("All");
    expect(shown(1)).toBe("All");
    expect(selects(w)[0]!.find(".filter-select__label").text()).toBe("Status");
    expect(selects(w)[1]!.find(".filter-select__label").text()).toBe("Key");
  });

  it("choosing All again sends no filter, and the label follows a real choice", async () => {
    const { w } = await open();
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    selects(w)[1]!.vm.$emit("update:modelValue", "k1");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "failed", keyId: "k1" }));
    expect(selects(w)[0]!.find(".filter-select__value").text()).toBe("Failed");
    expect(selects(w)[1]!.find(".filter-select__value").text()).toBe("key-one");
    selects(w)[0]!.vm.$emit("update:modelValue", "__all__");
    selects(w)[1]!.vm.$emit("update:modelValue", "__all__");
    await flushPromises();
    expect(lastQuery()).toEqual(query());
    expect(selects(w)[0]!.find(".filter-select__value").text()).toBe("All");
    expect(selects(w)[1]!.find(".filter-select__value").text()).toBe("All");
  });

  it("every key option is readable: its label, else its prefix, else the start of its id", async () => {
    vi.mocked(api.listKeyOptions).mockResolvedValue({
      items: [
        { id: "k1", label: "key-one", prefix: "rk_aaaa" },
        { id: "k2", label: "", prefix: "rk_bbbb" },
        { id: "k3", label: "  ", prefix: "" },
        { id: "cccccccc-9999", label: "" },
      ],
    });
    const { w } = await open();
    const options = selects(w)[1]!.props("options") as Array<{ label: string }>;
    expect(options.map((o) => o.label)).toEqual(["All", "key-one", "rk_bbbb", "k3", "cccccccc"]);
  });

  it("the status options read as drawn: All, then each status with a capital", async () => {
    const { w } = await open();
    const options = selects(w)[0]!.props("options") as Array<{ label: string }>;
    expect(options.map((o) => o.label)).toEqual([
      "All",
      "Queued",
      "Submitting",
      "Waiting",
      "Fetching",
      "Unknown",
      "Done",
      "Failed",
    ]);
  });

  it("a job whose key has no label shows the key as the Key filter names it: its prefix, else the start of its id", async () => {
    vi.mocked(api.listKeyOptions).mockResolvedValue({ items: [{ id: "k2", label: "", prefix: "rk_bbbb" }] });
    listJobs.mockResolvedValue({
      items: [job(A, { key_label: "", key_id: "k2" }), job(B, { key_label: "", key_id: "kkkkkkkk-1234" })],
      total: 2,
      next_cursor: null,
    });
    const { w } = await open();
    expect(rows(w)[0]!.find("td:nth-child(8)").text()).toBe("rk_bbbb");
    expect(rows(w)[1]!.find("td:nth-child(8)").text()).toBe("kkkkkkkk");
  });

  it("a provider scope change reloads with the provider", async () => {
    await open();
    useScopeStore().provider = "vibes";
    await flushPromises();
    expect(lastQuery()).toEqual(query({ provider: "vibes" }));
  });

  it("the status filter is kept in memory for the next visit", async () => {
    const first = await open();
    selects(first.w)[0]!.vm.$emit("update:modelValue", "done");
    await flushPromises();
    first.w.unmount();
    mounted.pop();
    listJobs.mockClear();
    await open();
    expect(listJobs).toHaveBeenCalledWith(query({ status: "done" }));
  });

  it("a stats link (?status=failed) sets the filter before the first load", async () => {
    await open("/jobs?status=failed");
    expect(listJobs).toHaveBeenCalledTimes(1);
    expect(lastQuery().status).toBe("failed");
  });

  it("a status in the link that is not one of ours is ignored", async () => {
    await open("/jobs?status=%3Cb%3E");
    expect(lastQuery().status).toBe("");
  });

  it("a key id in the link sets the key filter", async () => {
    await open("/jobs?key_id=k9");
    expect(lastQuery().keyId).toBe("k9");
  });
});

describe("paging", () => {
  const pager = (w: VueWrapper) => w.findComponent(ListPager);

  it("hands the pager the total, the offset and the page size in offset mode, with the sizes 10, 20, 50 and 100", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    expect(pager(w).props()).toMatchObject({
      total: 25,
      offset: 0,
      limit: 10,
      sizes: [10, 20, 50, 100],
    });
  });

  it("a picked page asks for its offset, and the pager follows the page that arrived", async () => {
    listJobs.mockResolvedValueOnce(page([A], 25)).mockResolvedValueOnce(page([B], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 10);
    await flushPromises();
    expect(lastQuery()).toEqual(query({ offset: 10 }));
    expect(pager(w).props("offset")).toBe(10);
    expect(rows(w)[0]!.text()).toContain("bbbbbbbb");
  });

  it("the next button of the pager goes to the following page", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    await w.find(".pager button.btn-next").trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ offset: 10 }));
  });

  it("a new page size starts again from the first page, with the new limit", async () => {
    listJobs.mockResolvedValue(page([A], 95));
    const { w } = await open();
    pager(w).vm.$emit("move", 20);
    await flushPromises();
    pager(w).vm.$emit("size", 50);
    await flushPromises();
    expect(lastQuery()).toEqual(query({ offset: 0, limit: 50 }));
    expect(pager(w).props("limit")).toBe(50);
  });

  it("a filter change starts again from the first page", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 20);
    await flushPromises();
    expect(pager(w).props("offset")).toBe(20);
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "failed" }));
    expect(pager(w).props("offset")).toBe(0);
  });

  it("a filter change whose load fails still starts from the first page when it is read again", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 20);
    await flushPromises();
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await flushPromises();
    await button(w, "Retry").trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "failed" }));
  });

  it("a reload (Refresh, a bulk action) stays on the page on screen", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 10);
    await flushPromises();
    await button(w, "Refresh").trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ offset: 10 }));
  });

  it("a list that shrank under the page on screen ends up on its last page (the pager moves itself)", async () => {
    listJobs.mockResolvedValueOnce(page([A], 25)).mockResolvedValueOnce(page([B], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 20);
    await flushPromises();
    listJobs.mockReset();
    listJobs.mockResolvedValueOnce(page([], 12)).mockResolvedValueOnce(page([C], 12));
    await button(w, "Refresh").trigger("click");
    await flushPromises();
    expect(listJobs.mock.calls.map((c) => c[0].offset)).toEqual([20, 10]);
    expect(pager(w).props("offset")).toBe(10);
    expect(rows(w)[0]!.text()).toContain("cccccccc");
  });

  it("a failed page move leaves the pager where it was", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    pager(w).vm.$emit("move", 10);
    await flushPromises();
    expect(pager(w).props("offset")).toBe(0);
  });
});

describe("selection", () => {
  it("with nothing ticked the toolbar says 0 selected and has no bulk buttons; a tick shows them, and the count follows", async () => {
    const { w } = await open();
    const shown = () => [button(w, "Re-check").isVisible(), button(w, "Regenerate…").isVisible()];
    expect(w.get(".jobs__count").isVisible()).toBe(true);
    expect(count(w)).toBe("0 selected");
    expect(shown()).toEqual([false, false]);
    expect(button(w, "Re-check").attributes("disabled")).toBeDefined();
    expect(button(w, "Regenerate…").attributes("disabled")).toBeDefined();
    await rowTick(w, 1)!.setValue(true);
    expect(count(w)).toBe("1 selected");
    expect(shown()).toEqual([true, true]);
    expect(button(w, "Re-check").attributes("disabled")).toBeUndefined();
    expect(button(w, "Regenerate…").attributes("disabled")).toBeUndefined();
    await rowTick(w, 1)!.setValue(false);
    expect(count(w)).toBe("0 selected");
    expect(shown()).toEqual([false, false]);
  });

  it("a ticked job shows its own box ticked in the same render as the count; the select-all box stays empty", async () => {
    const { w } = await open();
    await rowTick(w, 1)!.setValue(true);
    expect(count(w)).toBe("1 selected");
    expect((rowTick(w, 1)!.element as HTMLInputElement).checked).toBe(true);
    expect((rowTick(w, 0)!.element as HTMLInputElement).checked).toBe(false);
    expect((rowTick(w, 2)!.element as HTMLInputElement).checked).toBe(false);
    expect(rows(w)[1]!.find(".el-checkbox").classes()).toContain("is-checked");
    await flushPromises();
    const all = selectAll(w).element as HTMLInputElement;
    expect([all.checked, all.indeterminate]).toEqual([false, false]);
  });

  it("select all ticks the page and again clears it", async () => {
    const { w } = await open();
    await tickAll(w, true, "3 selected");
    expect((rowTick(w, 2)!.element as HTMLInputElement).checked).toBe(true);
    await tickAll(w, false, "0 selected");
  });

  it("select all is empty for a part of the page, ticked only when every row is, and a click on a part ticks all", async () => {
    const { w } = await open();
    const all = selectAll(w).element as HTMLInputElement;
    await rowTick(w, 0)!.setValue(true);
    await flushPromises();
    expect([all.checked, all.indeterminate]).toEqual([false, false]);
    expect(w.find("thead .is-indeterminate").exists()).toBe(false);
    expect(w.find("thead [aria-checked='mixed']").exists()).toBe(false);
    await rowTick(w, 1)!.setValue(true);
    await rowTick(w, 2)!.setValue(true);
    await flushPromises();
    expect([all.checked, all.indeterminate]).toEqual([true, false]);
    await rowTick(w, 2)!.setValue(false);
    await flushPromises();
    expect([all.checked, all.indeterminate]).toEqual([false, false]);
    await tickAll(w, true, "3 selected");
    expect(all.checked).toBe(true);
  });

  it("ticking a box opens nothing; a click on the row, or on its title, opens the job", async () => {
    const { w } = await open();
    const ui = useUiStore();
    await rowTick(w, 0)!.trigger("click");
    expect(ui.openJobId).toBeNull();
    await rows(w)[1]!.findAll("td")[2]!.trigger("click");
    expect(ui.openJobId).toBe(B);
    ui.closeJob();
    await rows(w)[2]!.find("a.title-link").trigger("click");
    expect(ui.openJobId).toBe(C);
  });

  it("a phone shows the bulk bar only while a job is ticked, with the count and both buttons", async () => {
    stubWidth("phone");
    const { w } = await open();
    const bar = () => w.get('[aria-label="Bulk actions"]');
    expect(bar().isVisible()).toBe(false);
    await w.get(".data-table__check input").setValue(true);
    expect(bar().isVisible()).toBe(true);
    expect(bar().text()).toContain("1 selected");
    expect(
      bar()
        .findAll("button")
        .map((b) => b.text()),
    ).toEqual(["Re-check", "Regenerate…"]);
  });

  it("a new page starts with nothing ticked", async () => {
    listJobs.mockResolvedValueOnce(page([A], 25)).mockResolvedValueOnce(page([B], 25));
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    w.findComponent(ListPager).vm.$emit("move", 10);
    await flushPromises();
    expect(count(w)).toBe("0 selected");
    listJobs.mockResolvedValueOnce(page([A], 25));
    w.findComponent(ListPager).vm.$emit("move", 0);
    await flushPromises();
    expect(count(w)).toBe("0 selected");
  });

  it("the ticks come back on the table after an error hid it and Retry drew it again", async () => {
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    await button(w, "Refresh").trigger("click");
    await flushPromises();
    expect(rows(w)).toHaveLength(0);
    await button(w, "Retry").trigger("click");
    await flushPromises();
    await flushPromises();
    expect(count(w)).toBe("1 selected");
    expect((rowTick(w, 0)!.element as HTMLInputElement).checked).toBe(true);
  });

  it("a reload of the same page keeps the ticks that are still on it", async () => {
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await rowTick(w, 2)!.setValue(true);
    listJobs.mockResolvedValueOnce(page([A, B]));
    await button(w, "Refresh").trigger("click");
    await flushPromises();
    expect(count(w)).toBe("1 selected");
  });
});

// The same tests in both layouts: the table of a desktop (el-table with reserve-selection) and the stacked list of a
// phone (its own selection). A tick, a reload, a page or filter change, an error and Retry must send the same ids.
describe.each(["desktop", "phone"] as const)("what is sent is what is ticked on screen (%s)", (width) => {
  beforeEach(() => stubWidth(width));
  // Timers are faked and never advanced: nothing here may wait for a timer, only for the render (microtasks). A tick
  // must count the moment it is made, whatever reload came just before.
  const settle = async (): Promise<void> => {
    for (let i = 0; i < 10; i++) await nextTick();
  };
  const D = "dddddddd-4444";
  const boom = () => new ApiError(500, { error: { code: "x", message: "boom" } });
  // the boxes on screen, row by row
  const ticks = (w: VueWrapper): boolean[] =>
    rows(w).map((r) => (r.find("input[type=checkbox]").element as HTMLInputElement).checked);
  // the operation-key action of the bulk Regenerate (bulkRegenerate keys the call by the same bulkAction of its ids)
  const action = (w: VueWrapper): string => w.findComponent(RegenerateControl).props("action");
  const deferred = () => {
    let resolve!: (p: JobPage) => void;
    const promise = new Promise<JobPage>((r) => (resolve = r));
    return { promise, resolve };
  };
  const bulk = { "Regenerate…": bulkRegenerate, "Re-check": bulkRecheck } as const;
  const labels = Object.keys(bulk) as (keyof typeof bulk)[];

  beforeEach(() => {
    bulkRegenerate.mockResolvedValue({ created: [], failed: {} });
    bulkRecheck.mockResolvedValue({ rechecked: [], skipped: {} });
  });
  afterEach(() => vi.useRealTimers());

  // Opens the list (A, B, C unless the first page is given), ticks the given rows, then fakes the timers.
  async function openTicked(indexes: number[], first?: JobPage): Promise<VueWrapper> {
    if (first) listJobs.mockResolvedValueOnce(first);
    const { w } = await open();
    for (const i of indexes) await rowTick(w, i)!.setValue(true);
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    return w;
  }
  async function reload(w: VueWrapper, next: JobPage | ApiError = page([A, B, C])): Promise<void> {
    if (next instanceof ApiError) listJobs.mockRejectedValueOnce(next);
    else listJobs.mockResolvedValueOnce(next);
    await button(w, "Refresh").trigger("click");
    await settle();
  }
  async function press(w: VueWrapper, label: string): Promise<void> {
    await button(w, label).trigger("click");
    await settle();
  }

  it.each(labels)("a tick right after a reload counts at once: %s sends exactly the ticks on screen", async (label) => {
    const w = await openTicked([0]);
    await reload(w);
    await rowTick(w, 2)!.setValue(true);
    expect(ticks(w)).toEqual([true, false, true]);
    expect(count(w)).toBe("2 selected");
    expect(action(w)).toBe(`bulk:${A},${C}`);
    await press(w, label);
    expect(bulk[label].mock.calls).toEqual([[[A, C]]]);
  });

  it.each(labels)("an untick right after a reload counts at once: %s leaves the unticked job out", async (label) => {
    const w = await openTicked([0, 1]);
    await reload(w);
    await rowTick(w, 0)!.setValue(false);
    expect(ticks(w)).toEqual([false, true, false]);
    expect(action(w)).toBe(`bulk:${B}`);
    await press(w, label);
    expect(bulk[label].mock.calls).toEqual([[[B]]]);
  });

  it("two reloads in flight, ticks made while they run: the newer answer keeps them, the older one is dropped", async () => {
    const w = await openTicked([0]);
    const older = deferred();
    const newer = deferred();
    listJobs.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    await button(w, "Refresh").trigger("click");
    announceJobChange();
    await settle();
    await rowTick(w, 1)!.setValue(true);
    newer.resolve(page([A, B, C]));
    await settle();
    await rowTick(w, 2)!.setValue(true);
    older.resolve(page([B, D]));
    await settle();
    expect(ticks(w)).toEqual([true, true, true]);
    expect(action(w)).toBe(`bulk:${A},${B},${C}`);
    await press(w, "Regenerate…");
    expect(bulkRegenerate.mock.calls).toEqual([[[A, B, C]]]);
  });

  it("an error hides the table: nothing can be sent until Retry draws the ticks again, and then they are what is sent", async () => {
    const w = await openTicked([0]);
    await reload(w, boom());
    expect(rows(w)).toHaveLength(0);
    expect(count(w)).toBe("0 selected");
    expect(button(w, "Regenerate…").attributes("disabled")).toBeDefined();
    expect(button(w, "Re-check").attributes("disabled")).toBeDefined();
    listJobs.mockResolvedValueOnce(page([A, B, C]));
    await press(w, "Retry");
    expect(ticks(w)).toEqual([true, false, false]);
    expect(action(w)).toBe(`bulk:${A}`);
    await press(w, "Regenerate…");
    expect(bulkRegenerate.mock.calls).toEqual([[[A]]]);
  });

  it("a reload and a failed one right behind it, then Retry: the tick comes back and is what is sent", async () => {
    const w = await openTicked([0]);
    const first = deferred();
    listJobs.mockReturnValueOnce(first.promise).mockRejectedValueOnce(boom());
    await button(w, "Refresh").trigger("click");
    first.resolve(page([A, B, C]));
    announceJobChange();
    await settle();
    expect(rows(w)).toHaveLength(0);
    listJobs.mockResolvedValueOnce(page([A, B, C]));
    await press(w, "Retry");
    expect(ticks(w)).toEqual([true, false, false]);
    await press(w, "Re-check");
    expect(bulkRecheck.mock.calls).toEqual([[[A]]]);
  });

  it("a job that left the page while an error hid the table does not come back ticked", async () => {
    const w = await openTicked([2]);
    await reload(w, boom());
    listJobs.mockResolvedValueOnce(page([A, B]));
    await press(w, "Retry");
    expect(count(w)).toBe("0 selected");
    await reload(w, page([A, B, C]));
    expect(ticks(w)).toEqual([false, false, false]);
    expect(count(w)).toBe("0 selected");
  });

  it("a reload of the same page keeps the ticks still on it, and they are what is sent", async () => {
    const w = await openTicked([0, 2]);
    await reload(w, page([A, B]));
    expect(ticks(w)).toEqual([true, false]);
    expect(action(w)).toBe(`bulk:${A}`);
    await press(w, "Re-check");
    expect(bulkRecheck.mock.calls).toEqual([[[A]]]);
  });

  it("a page change starts with nothing ticked, even a job that moved onto it; a tick there is what is sent", async () => {
    const w = await openTicked([0, 1], page([A, B], 25));
    listJobs.mockResolvedValueOnce(page([B, D], 25));
    w.findComponent(ListPager).vm.$emit("move", 10);
    await settle();
    expect(ticks(w)).toEqual([false, false]);
    expect(count(w)).toBe("0 selected");
    await rowTick(w, 1)!.setValue(true);
    expect(action(w)).toBe(`bulk:${D}`);
    await press(w, "Regenerate…");
    expect(bulkRegenerate.mock.calls).toEqual([[[D]]]);
  });

  it("a filter change unticks the old rows at once, before the new list arrives", async () => {
    const w = await openTicked([0]);
    listJobs.mockReturnValueOnce(deferred().promise);
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await settle();
    expect(ticks(w)).toEqual([false, false, false]);
    expect(count(w)).toBe("0 selected");
    await rowTick(w, 1)!.setValue(true);
    await press(w, "Re-check");
    expect(bulkRecheck.mock.calls).toEqual([[[B]]]);
  });

  it("a page-size change unticks the rows at once, before the new page arrives", async () => {
    const w = await openTicked([0, 2]);
    listJobs.mockReturnValueOnce(deferred().promise);
    w.findComponent(ListPager).vm.$emit("size", 20);
    await settle();
    expect(rows(w)).toHaveLength(3);
    expect(ticks(w)).toEqual([false, false, false]);
    expect(count(w)).toBe("0 selected");
    expect(button(w, "Regenerate…").attributes("disabled")).toBeDefined();
    expect(button(w, "Re-check").attributes("disabled")).toBeDefined();
  });

  it("a filter change while an error hides the table: the new list starts with nothing ticked", async () => {
    const w = await openTicked([0]);
    await reload(w, boom());
    listJobs.mockResolvedValueOnce(page([A, B]));
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await settle();
    expect(ticks(w)).toEqual([false, false]);
    expect(count(w)).toBe("0 selected");
  });

  it("a bulk failure that is safe to retry, then a reload: the ticks stay and the retry sends the same ids", async () => {
    const lost = new ApiError(503, {});
    lost.retrySafe = true;
    bulkRegenerate.mockReset().mockRejectedValueOnce(lost).mockResolvedValueOnce({ created: [], failed: {} });
    const w = await openTicked([0, 1]);
    await press(w, "Regenerate…");
    await reload(w);
    expect(ticks(w)).toEqual([true, true, false]);
    expect(action(w)).toBe(`bulk:${A},${B}`);
    await press(w, "Regenerate…");
    expect(bulkRegenerate.mock.calls).toEqual([[[A, B]], [[A, B]]]);
  });

  it("while the confirm is open a reload can only drop ticks: what is sent is the ticks on screen at the answer", async () => {
    const w = await openTicked([0, 2]);
    let answer!: (ok: boolean) => void;
    vi.mocked(confirmDialog).mockReturnValueOnce(new Promise<boolean>((r) => (answer = r)));
    await press(w, "Regenerate…");
    await reload(w, page([A, B]));
    answer(true);
    await settle();
    expect(bulkRegenerate.mock.calls).toEqual([[[A]]]);
  });

  it("a confirm answered after a failed reload hid the table sends nothing", async () => {
    const w = await openTicked([0]);
    let answer!: (ok: boolean) => void;
    vi.mocked(confirmDialog).mockReturnValueOnce(new Promise<boolean>((r) => (answer = r)));
    await press(w, "Regenerate…");
    await reload(w, boom());
    answer(true);
    await settle();
    expect(bulkRegenerate).not.toHaveBeenCalled();
    expect(notify.error).toHaveBeenCalledWith("Tick at least one job");
  });
});

describe("bulk re-check", () => {
  it("posts the ticked ids in table order, says how many, and clears the ticks", async () => {
    bulkRecheck.mockResolvedValue({ rechecked: [A, C], skipped: {} });
    const { w } = await open();
    await rowTick(w, 2)!.setValue(true);
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Re-check").trigger("click");
    await flushPromises();
    expect(bulkRecheck).toHaveBeenCalledWith([A, C]);
    expect(notify.success).toHaveBeenCalledWith("Re-checked 2");
    expect(count(w)).toBe("0 selected");
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("a second click while the re-check is under way sends it once", async () => {
    let done!: (r: { rechecked: string[]; skipped: Record<string, string> }) => void;
    bulkRecheck.mockReturnValueOnce(new Promise((r) => (done = r)));
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Re-check").trigger("click");
    await button(w, "Re-check").trigger("click");
    expect(bulkRecheck).toHaveBeenCalledTimes(1);
    done({ rechecked: [A], skipped: {} });
    await flushPromises();
  });

  it("jobs left alone turn the toast to info", async () => {
    bulkRecheck.mockResolvedValue({ rechecked: [A], skipped: { [B]: "job.no_recheck" } });
    const { w } = await open();
    await tickAll(w, true, "3 selected");
    await button(w, "Re-check").trigger("click");
    await flushPromises();
    expect(notify.info).toHaveBeenCalledWith("Re-checked 1, left 1 alone");
  });

  it("a failed call is a toast and the ticks stay", async () => {
    bulkRecheck.mockRejectedValue(new ApiError(422, { error: { code: "x", message: "too many", param: "job_ids" } }));
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Re-check").trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("too many (job_ids)");
    expect(count(w)).toBe("1 selected");
  });
});

describe("bulk regenerate", () => {
  const MADE = {
    created: [
      { from: A, id: "n1", status: "queued", replayed: false },
      { from: B, id: "n2", status: "queued", replayed: true },
    ],
    failed: { [C]: "wallet.insufficient" },
  };

  it("asks first, naming the count, today's price and the charge", async () => {
    const { w } = await open();
    await tickAll(w, true, "3 selected");
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    await button(w, "Regenerate…").trigger("click");
    await flushPromises();
    expect(confirmDialog).toHaveBeenCalledWith({
      title: "Regenerate",
      message: "Regenerate 3 job(s)? Each makes a NEW job at today’s price and charges its key.",
      confirmText: "Regenerate",
      danger: true,
    });
    expect(bulkRegenerate).not.toHaveBeenCalled();
    expect(count(w)).toBe("3 selected");
  });

  it("a second click while the confirm is open asks nothing more", async () => {
    let answer!: (ok: boolean) => void;
    vi.mocked(confirmDialog).mockReturnValueOnce(new Promise<boolean>((r) => (answer = r)));
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Regenerate…").trigger("click");
    await button(w, "Regenerate…").trigger("click");
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    answer(false);
    await flushPromises();
    expect(bulkRegenerate).not.toHaveBeenCalled();
  });

  it("sends the ticked ids, says made, already made and failed, and clears the ticks", async () => {
    bulkRegenerate.mockResolvedValue(MADE);
    const { w } = await open();
    await rowTick(w, 2)!.setValue(true);
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Regenerate…").trigger("click");
    await flushPromises();
    expect(bulkRegenerate).toHaveBeenCalledWith([A, C]);
    expect(notify.info).toHaveBeenCalledWith("Made 1, already made 1, failed 1");
    expect(count(w)).toBe("0 selected");
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("keeps the selection after a failure that is safe to retry, and the retry sends the same ids", async () => {
    const lost = new ApiError(503, {});
    lost.retrySafe = true;
    bulkRegenerate.mockRejectedValueOnce(lost).mockResolvedValueOnce(MADE);
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await rowTick(w, 1)!.setValue(true);
    await button(w, "Regenerate…").trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith(RETRY_SAFE_TEXT);
    expect(count(w)).toBe("2 selected");
    expect(listJobs).toHaveBeenCalledTimes(1);
    await button(w, "Regenerate…").trigger("click");
    await flushPromises();
    expect(bulkRegenerate.mock.calls).toEqual([[[A, B]], [[A, B]]]);
  });

  it("the control's action is the sorted selection, so an unchanged selection is one action and a changed one is another", async () => {
    const { w } = await open();
    const action = () => w.findComponent(RegenerateControl).props("action");
    await rowTick(w, 1)!.setValue(true);
    await rowTick(w, 0)!.setValue(true);
    expect(action()).toBe(`bulk:${A},${B}`);
    await rowTick(w, 1)!.setValue(false);
    expect(action()).toBe(`bulk:${A}`);
  });

  it("offers a new regenerate only once a key was kept for exactly this selection", async () => {
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await rowTick(w, 1)!.setValue(true);
    sessionStorage.setItem("regenerateOperations", JSON.stringify({ [`bulk:${A},${B}`]: "z".repeat(20) }));
    // the control reads the record after each call and when its action changes
    await rowTick(w, 1)!.setValue(false);
    expect(w.text()).not.toContain("Start a new regenerate");
    await rowTick(w, 1)!.setValue(true);
    expect(w.text()).toContain("Start a new regenerate");
  });
});

describe("the page head and the 10-second read", () => {
  const hidden = (on: boolean) => Object.defineProperty(document, "hidden", { configurable: true, get: () => on });
  afterEach(() => {
    vi.useRealTimers();
    Reflect.deleteProperty(document, "hidden");
  });

  it("heads the page with the title, one sentence, the update time and a Refresh button", async () => {
    const { w } = await open();
    expect(w.find("h1").text()).toBe("Jobs");
    expect(w.text()).toContain("Every generation job across keys, grouped by day.");
    expect(w.text()).toMatch(/Updated \d\d:\d\d:\d\d · auto every 10 s/);
    expect(button(w, "Refresh").exists()).toBe(true);
  });

  it("a phone says 'by day' and has an icon-only Refresh that names the update time", async () => {
    stubWidth("phone");
    const { w } = await open();
    expect(w.text()).toContain("Every generation job across keys, by day.");
    expect(w.text()).not.toContain("auto every 10 s");
    const refresh = w.find("button.jobs__refresh");
    expect(refresh.text()).toBe("");
    expect(refresh.attributes("aria-label")).toMatch(/^Refresh, updated \d\d:\d\d:\d\d$/);
  });

  it("the banner comes after the Jobs today table and before the list", async () => {
    vi.mocked(shell.getOpenAlertGroups).mockResolvedValue({ items: [{ kind: "drift" } as AlertGroup] });
    vi.mocked(shell.getProvidersSummary).mockResolvedValue({
      ...SUMMARY,
      providers: [
        {
          provider: "dola",
          execution: "browser",
          claims_enabled: true,
          running: 0,
          queued: 0,
          done_today: 0,
          failed_today: 0,
          accounts: { usable: 0, total: 0 },
          pools: [],
        },
      ],
    });
    const { w } = await open();
    await button(w, "Refresh").trigger("click");
    await flushPromises();
    const order = w.findAll('[data-testid="provider-table"], [role="status"], section[aria-label="Job list"]');
    expect(order.map((n) => n.attributes("data-testid") ?? n.attributes("role") ?? n.attributes("aria-label"))).toEqual(
      ["provider-table", "status", "Job list"],
    );
  });

  it("reads the list and the provider summary again every 10 s while the tab is shown, never while it is hidden", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await open();
    const summary = vi.mocked(shell.getProvidersSummary);
    const lists = listJobs.mock.calls.length;
    const sums = summary.mock.calls.length;
    const reads = () => [listJobs.mock.calls.length, summary.mock.calls.length];
    vi.advanceTimersByTime(9_999);
    await flushPromises();
    expect(reads()).toEqual([lists, sums]);
    vi.advanceTimersByTime(1);
    await flushPromises();
    expect(reads()).toEqual([lists + 1, sums + 1]);
    hidden(true);
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    expect(reads()).toEqual([lists + 1, sums + 1]);
    hidden(false);
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    expect(reads()).toEqual([lists + 2, sums + 2]);
  });

  it("coming back to the tab reads at once, and leaving it reads nothing", async () => {
    await open();
    const lists = listJobs.mock.calls.length;
    hidden(true);
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();
    expect(listJobs.mock.calls.length).toBe(lists);
    hidden(false);
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();
    expect(listJobs.mock.calls.length).toBe(lists + 1);
  });

  it("a read still under way is not overtaken by the next tick", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await open();
    let slow!: (p: JobPage) => void;
    listJobs.mockReturnValueOnce(new Promise<JobPage>((res) => (slow = res)));
    vi.advanceTimersByTime(10_000);
    const seen = listJobs.mock.calls.length;
    vi.advanceTimersByTime(10_000);
    expect(listJobs.mock.calls.length).toBe(seen);
    slow(page([A, B, C]));
    await flushPromises();
  });

  it("stops reading once the page is gone", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { w } = await open();
    w.unmount();
    mounted.pop();
    const lists = listJobs.mock.calls.length;
    vi.advanceTimersByTime(30_000);
    expect(listJobs.mock.calls.length).toBe(lists);
  });
});

describe("time zones", () => {
  const zone = process.env.TZ;
  afterEach(() => {
    if (zone === undefined) delete process.env.TZ;
    else process.env.TZ = zone;
  });

  // [zone, the instant of its local midnight between 14 and 15 October 2026]
  it.each([
    ["Asia/Saigon", Date.UTC(2026, 9, 14, 17)],
    ["America/Los_Angeles", Date.UTC(2026, 9, 15, 7)],
    ["Pacific/Auckland", Date.UTC(2026, 9, 14, 11)],
  ])(
    "a job a second either side of local midnight in %s sits under two day headers, with its own clock",
    async (tz, at) => {
      process.env.TZ = tz;
      listJobs.mockResolvedValue({
        items: [job(A, { created_ms: at + 1000 }), job(B, { created_ms: at - 1000 })],
        total: 2,
        next_cursor: null,
      });
      const { w } = await open();
      const lines = w.findAll("tbody tr").map((tr) => tr.text());
      expect(lines).toHaveLength(4);
      expect(lines[0]).toContain("15 Oct");
      expect(lines[1]).toContain("00:00:01");
      expect(lines[2]).toContain("14 Oct");
      expect(lines[3]).toContain("23:59:59");
    },
  );

  it.each([
    ["Asia/Saigon", Date.UTC(2026, 9, 14, 17)],
    ["America/Los_Angeles", Date.UTC(2026, 9, 15, 7)],
  ])(
    "asks for the day totals in the zone of the browser, exactly as the browser reports it (%s), and puts each on its own day's header",
    async (tz, at) => {
      process.env.TZ = tz;
      const sent = Intl.DateTimeFormat().resolvedOptions().timeZone;
      listJobs.mockResolvedValue({
        items: [job(A, { created_ms: at + 1000 }), job(B, { created_ms: at - 1000 })],
        total: 2,
        next_cursor: null,
        day_totals: { "2026-10-15": 212, "2026-10-14": 1 },
      });
      const { w } = await open();
      expect(lastQuery().tz).toBe(sent);
      const days = w.findAll('[data-testid="day-row"]').map((d) => d.text());
      expect(days[0]).toMatch(/15 Oct · 212 jobs$/);
      expect(days[1]).toMatch(/14 Oct · 1 job$/);
    },
  );

  it.each([
    ["the status", (w: VueWrapper) => selects(w)[0]!.vm.$emit("update:modelValue", "failed")],
    ["the key", (w: VueWrapper) => selects(w)[1]!.vm.$emit("update:modelValue", "k1")],
    ["the provider", () => (useScopeStore().provider = "vibes")],
  ])("a change of %s drops the old day totals at once, before the new list arrives", async (_what, change) => {
    const day = (w: VueWrapper) => w.get('[data-testid="day-row"]').text();
    listJobs.mockResolvedValue({ items: [job(A), job(B)], total: 2, next_cursor: null });
    const { w } = await open();
    await w.vm.$nextTick();
    listJobs.mockReset().mockResolvedValueOnce({
      items: [job(A), job(B)],
      total: 2,
      next_cursor: null,
      day_totals: { [dayKeyOf(job(A).created_ms)]: 212 },
    });
    await button(w, "Refresh").trigger("click");
    await flushPromises();
    expect(day(w)).toContain("· 212 jobs");
    listJobs.mockReturnValueOnce(new Promise<JobPage>(() => {}));
    change(w);
    await flushPromises();
    expect(day(w)).not.toContain("212");
    expect(day(w)).toContain("· 2 jobs shown");
  });

  it("today's header counts its jobs on this page and says shown, an earlier day says its total, and one job is 1 job", async () => {
    const now = Date.now();
    const earlier = now - 2 * 86_400_000;
    listJobs.mockResolvedValue({
      items: [job(A, { created_ms: now }), job(B, { created_ms: earlier })],
      total: 2,
      next_cursor: null,
      day_totals: { [dayKeyOf(now)]: 212, [dayKeyOf(earlier)]: 1 },
    });
    const { w } = await open();
    const days = w.findAll('[data-testid="day-row"]').map((d) => d.text());
    expect(days[0]).toMatch(/^Today · .+ · 1 job shown$/);
    expect(days[1]).toMatch(/ · 1 job$/);
  });

  it("a day the server gave no total for says how many of its jobs are shown", async () => {
    listJobs.mockResolvedValue({ items: [job(A), job(B)], total: 2, next_cursor: null, day_totals: {} });
    const { w } = await open();
    expect(w.get('[data-testid="day-row"]').text()).toContain("· 2 jobs shown");
  });
});

describe("server text", () => {
  it("title, key, account and error code are text, never markup", async () => {
    const planted = '<img src=x onerror="window.__pwned=1">';
    listJobs.mockResolvedValue({
      items: [job(A, { title: planted, key_label: planted, account_label: planted, error_code: planted })],
      total: 1,
      next_cursor: null,
    });
    const { w } = await open();
    expect(w.find("tbody img").exists()).toBe(false);
    expect(rows(w)[0]!.text()).toContain(planted);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe("providers", () => {
  afterEach(() => vi.useRealTimers());
  const twoProviders: shell.ProvidersSummary = {
    day_tz: "UTC",
    day_start_ms: 0,
    hours: [],
    providers: ["dola", "vibes"].map((provider) => ({
      provider,
      execution: "http" as const,
      claims_enabled: true,
      running: 0,
      queued: 0,
      done_today: 0,
      failed_today: 0,
      accounts: { usable: 0, total: 0 },
      pools: [],
    })),
  };

  it("every row carries its provider: the rail class, a tinted cell with its tile and name, the model in its ink", async () => {
    listJobs.mockResolvedValue({ items: [job(A, { provider: "vibes" })], total: 1, next_cursor: null });
    const { w } = await open();
    const row = w.get(".jobs__table tbody tr:not(.is-day)");
    expect(row.classes()).toEqual(expect.arrayContaining(["jobs-row", "p-vibes"]));
    expect(row.get("td.col-provider").text()).toBe("Vvibes");
    expect(row.get(".jobs__model").text()).toBe("veo");
  });

  it("a phone row has the provider's side column", async () => {
    stubWidth("phone");
    listJobs.mockResolvedValue({ items: [job(A, { provider: "meta" })], total: 1, next_cursor: null });
    const { w } = await open();
    const item = w.get("li.data-table__item");
    expect(item.classes()).toContain("p-meta");
    expect(item.get(".prow__side").text()).toBe("Mmeta");
  });

  it("reads the provider summary with the list and on every 10-second read; a row of Jobs today picks the provider", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    vi.mocked(shell.getProvidersSummary).mockResolvedValue(twoProviders);
    const { w } = await open();
    const reads = vi.mocked(shell.getProvidersSummary).mock.calls.length;
    expect(reads).toBeGreaterThan(0);
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    expect(vi.mocked(shell.getProvidersSummary).mock.calls.length).toBe(reads + 1);
    await w.get('[data-testid="provider-row-vibes"]').trigger("click");
    await flushPromises();
    expect(useScopeStore().provider).toBe("vibes");
    expect(listJobs).toHaveBeenLastCalledWith(query({ provider: "vibes" }));
    await w.get('[data-testid="provider-row-vibes"]').trigger("click");
    expect(useScopeStore().provider).toBe("");
  });
});
