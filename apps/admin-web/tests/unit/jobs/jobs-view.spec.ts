import type { AlertGroup } from "@/api/alerts";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
vi.mock("@/components/shared/clipboard", () => ({ copyText: vi.fn().mockResolvedValue("clipboard") }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

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
  range: "today",
  q: "",
  ...over,
});
const STATS: shell.Stats = {
  jobs: { queued: 1, in_progress: 3, done: 1284, failed: 37, pre_job_failures_24h: 6 },
  made: { videos: 335, images: 120 },
  accounts: { usable: 7, available: 8, dead: 1, disabled: 0 },
  keys: 5,
  alerts: [],
};
// The provider summary behind the Today section: none of these tests but the last block is about it.
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
      { path: "/test", name: "test", component: { render: () => null } },
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

// the layout the test mounts (stubWidth sets it): a table on a desktop and a tablet, a list of rows on a phone
let layout: "phone" | "tablet" | "desktop" = "desktop";
const rows = (w: VueWrapper) => w.findAll(layout === "phone" ? "button.p-row" : "tbody tr.is-row");
const rowTick = (w: VueWrapper, i: number) => rows(w)[i]!.find("input[type=checkbox]");
const selectAll = (w: VueWrapper) => w.find("thead input[type=checkbox]");
const tickAll = async (w: VueWrapper, on: boolean): Promise<void> => {
  await selectAll(w).setValue(on);
};
const button = (w: VueWrapper, text: string) => w.findAll("button").find((b) => b.text() === text);
const has = (w: VueWrapper, text: string): boolean => !!button(w, text);
const count = (w: VueWrapper) => (w.find(".jobs__count").exists() ? w.find(".jobs__count").text() : "0 selected");
const keySelect = (w: VueWrapper) => w.findComponent({ name: "FilterSelect" });
const chips = (w: VueWrapper) => w.findAll(".jobs__chips button.chip");
const chip = (w: VueWrapper, label: string) => chips(w).find((c) => c.text() === label)!;
const pickStatus = (w: VueWrapper, label: string) => chip(w, label).trigger("click");
const lastQuery = () => listJobs.mock.calls.at(-1)![0];
// a reload of the page on screen, as a change made in the detail asks for one
const reloadNow = async (): Promise<void> => {
  announceJobChange();
  await flushPromises();
};

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

  it("shows the Ledger columns: a tick box, Status, Job, Provider, Account, Age, Points and the row's buttons", async () => {
    const { w } = await open();
    expect(w.findAll("thead th").map((th) => th.text())).toEqual([
      "",
      "Status",
      "Job",
      "Provider",
      "Account",
      "Age",
      "Points",
      "Actions",
    ]);
  });

  it("a tablet drops Account and the buttons and folds the account into the job line", async () => {
    stubWidth("tablet");
    listJobs.mockResolvedValue({ items: [job(A, { account_label: null })], total: 1, next_cursor: null });
    const { w } = await open();
    expect(w.findAll("thead th").map((th) => th.text())).toEqual(["", "Status", "Job", "Provider", "Age", "Points"]);
    expect(rows(w)[0]!.find(".sub").text()).toBe("veo · —");
  });

  it("a phone draws a row: the pill and the age, the title, the provider with the model and the points, the code", async () => {
    stubWidth("phone");
    listJobs.mockResolvedValue({
      items: [job(A, { status: "failed", error_code: "render.deadline", price: 10, net_charged: 0 })],
      total: 1,
      next_cursor: null,
    });
    const { w } = await open();
    expect(w.find("thead").exists()).toBe(false);
    const row = rows(w)[0]!;
    expect(row.find(".pill").text()).toBe("Failed");
    expect(row.find(".p-row__title").text()).toBe(`title of ${A}`);
    expect(row.find(".pchip").text()).toBe("dola");
    expect(row.find(".p-row__model").text()).toBe("veo");
    expect(row.find(".p-row__pts").text()).toBe("0 / 10 pts");
    expect(row.find(".p-row__code").text()).toBe("render.deadline");
    expect(w.find("input[type=checkbox]").exists()).toBe(false);
  });

  it("draws one row per job: the title, the model and id, a differing model, the code in red, the account", async () => {
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
    expect(first!.find(".title-link").text()).toBe("(untitled)");
    expect(first!.find(".sub").text()).toBe("veo · aaaaaaaa · model differs");
    expect(second!.find(".sub__code").text()).toBe("· render.deadline");
    expect(second!.find(".c-account").text()).toBe("acct-1");
    expect(second!.find(".c-pts").text()).toBe("0 / 10");
  });

  it("the pill says what a job on its way is doing with its step; the others their word, in their tone", async () => {
    listJobs.mockResolvedValue({
      items: [job(A, { status: "fetching" }), job(B, { status: "queued" }), job(C, { status: "failed" })],
      total: 3,
      next_cursor: null,
    });
    const { w } = await open();
    const pill = (i: number) => rows(w)[i]!.find(".pill");
    expect([pill(0).text(), pill(1).text(), pill(2).text()]).toEqual(["Fetching 3/4", "Queued", "Failed"]);
    expect(pill(0).classes()).toContain("tone-brand");
    expect(pill(1).classes()).toContain("tone-neutral");
    expect(pill(2).classes()).toContain("tone-bad");
  });

  it("the age counts from the last answer, and a queued job that waited 10 min or more is drawn slow", async () => {
    const now = Date.now();
    listJobs.mockResolvedValue({
      items: [job(A, { status: "queued", created_ms: now - 28 * 60_000 }), job(B, { created_ms: now - 5_000 })],
      total: 2,
      next_cursor: null,
    });
    const { w } = await open();
    expect(rows(w)[0]!.find(".c-age").text()).toBe("28 m");
    expect(rows(w)[0]!.find(".c-age").classes()).toContain("slow");
    expect(rows(w)[1]!.find(".c-age").text()).toMatch(/^\d s$/);
    expect(rows(w)[1]!.find(".c-age").classes()).not.toContain("slow");
  });

  it("points: a job still on its way that has charged nothing shows a dash, any other its net", async () => {
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
    const pts = (i: number) => rows(w)[i]!.find(".c-pts").text();
    expect([pts(0), pts(1), pts(2)]).toEqual(["—", "2", "0"]);
  });

  it("an empty list draws the first-run box with New test job; under a filter it offers Clear filters", async () => {
    listJobs.mockResolvedValue(page([]));
    const { w, router } = await open();
    expect(w.text()).toContain("No jobs yet");
    expect(w.find(".jobs__count").exists()).toBe(false);
    await w.get(".jl__box .jl__primary").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("test");
    await pickStatus(w, "Done");
    await flushPromises();
    expect(w.text()).toContain("No jobs match these filters");
    await button(w, "Clear filters")!.trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ range: "30d" }));
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
    await button(w, "Retry")!.trigger("click");
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
    await pickStatus(w, "Done");
    await flushPromises();
    expect(rows(w)).toHaveLength(1);
    slow(page([A, B]));
    await flushPromises();
    expect(rows(w)).toHaveLength(1);
    expect(rows(w)[0]!.text()).toContain("cccccccc");
  });

  it("reloads when the detail announces a change", async () => {
    await open();
    await reloadNow();
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("reads the key options once, and a failure is a toast", async () => {
    vi.mocked(api.listKeyOptions).mockRejectedValueOnce(
      new ApiError(500, { error: { code: "x", message: "no keys" } }),
    );
    await open();
    expect(notify.error).toHaveBeenCalledWith("no keys");
  });

  it("a row opens its job; Copy job id copies the whole id; Open in new tab opens its link", async () => {
    const opened = vi.fn();
    vi.stubGlobal("open", opened);
    const { w } = await open();
    await rows(w)[1]!.findAll("td")[2]!.trigger("click");
    expect(useUiStore().openJobId).toBe(B);
    await rows(w)[2]!.get('[aria-label="Open in new tab"]').trigger("click");
    expect(opened).toHaveBeenCalledWith(`/jobs?job=${C}`, "_blank", "noopener");
    await rows(w)[0]!.get('[aria-label="Copy job id · C"]').trigger("click");
    await flushPromises();
    expect(notify.success).toHaveBeenCalledWith("Copied job id aaaaaaaa");
  });

  it("a link with a job id opens its drawer", async () => {
    await open(`/jobs?job=${B}`);
    expect(useUiStore().openJobId).toBe(B);
  });
});

describe("filters", () => {
  it("a single status that came from a link gets a chip of its own, pressed; a group chip sends its statuses", async () => {
    rememberedStatus.value = "submitting";
    const { w } = await open();
    expect(lastQuery()).toEqual(query({ status: "submitting" }));
    expect(chip(w, "Submitting").attributes("aria-pressed")).toBe("true");
    await pickStatus(w, "In flight · 0");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "queued,submitting,waiting,fetching" }));
    expect(chips(w).map((c) => c.text())).toEqual(["All", "Needs a look · 0", "In flight · 0", "Done"]);
  });

  it("an empty list under filters shows them as chips that each take theirs off, and says them in words", async () => {
    listJobs.mockResolvedValue({ ...page([]), has_jobs: true });
    rememberedStatus.value = "failed,unknown";
    const { w } = await open();
    const on = () => w.findAll(".jobs__active .chip").map((c) => c.text());
    expect(on()).toEqual(["Needs a look ×", "Today ×"]);
    expect(w.text()).toContain("Nothing is “Needs a look” today.");
    await w.findAll(".jobs__active .chip")[1]!.trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "failed,unknown", range: "30d" }));
    expect(on()).toEqual(["Needs a look ×"]);
    expect(w.text()).toContain("Nothing is “Needs a look” in the last 30 days.");
  });

  it("the key filter sends the key id", async () => {
    const { w } = await open();
    keySelect(w).vm.$emit("update:modelValue", "k1");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ keyId: "k1" }));
  });

  it("shows All while nothing is filtered: the All chip pressed, the Key filter All after its word", async () => {
    const { w } = await open();
    expect(chip(w, "All").attributes("aria-pressed")).toBe("true");
    expect(chips(w).filter((c) => c.attributes("aria-pressed") === "true")).toHaveLength(1);
    expect(keySelect(w).find(".filter-select__value").text()).toBe("All");
    expect(keySelect(w).find(".filter-select__label").text()).toBe("Key");
  });

  it("choosing All again sends no filter, and the label follows a real choice", async () => {
    const { w } = await open();
    await pickStatus(w, "Done");
    keySelect(w).vm.$emit("update:modelValue", "k1");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "done", keyId: "k1" }));
    expect(chip(w, "Done").attributes("aria-pressed")).toBe("true");
    expect(keySelect(w).find(".filter-select__value").text()).toBe("key-one");
    await pickStatus(w, "All");
    keySelect(w).vm.$emit("update:modelValue", "__all__");
    await flushPromises();
    expect(lastQuery()).toEqual(query());
    expect(chip(w, "All").attributes("aria-pressed")).toBe("true");
    expect(keySelect(w).find(".filter-select__value").text()).toBe("All");
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
    const options = keySelect(w).props("options") as Array<{ label: string }>;
    expect(options.map((o) => o.label)).toEqual(["All", "key-one", "rk_bbbb", "k3", "cccccccc"]);
  });

  it("the status options read as drawn: All, the two groups with their counts and Done", async () => {
    listJobs.mockResolvedValue({ ...page([A]), status_counts: { failed: 2, unknown: 1, queued: 4, waiting: 5 } });
    const { w } = await open();
    expect(chips(w).map((c) => c.text())).toEqual(["All", "Needs a look · 3", "In flight · 9", "Done"]);
    await pickStatus(w, "Needs a look · 3");
    await flushPromises();
    expect(lastQuery().status).toBe("failed,unknown");
  });

  it("the range switch and the search box narrow the list and the range moves the Today figures", async () => {
    const { w } = await open();
    const summary = vi.mocked(shell.getProvidersSummary);
    await w
      .findAll(".jobs__range-btn")
      .find((b) => b.text() === "7 days")!
      .trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ range: "7d" }));
    expect(summary).toHaveBeenLastCalledWith("7d");
    vi.useFakeTimers();
    try {
      await w.find(".jobs__search input").setValue("  lantern ");
      vi.advanceTimersByTime(300);
    } finally {
      vi.useRealTimers();
    }
    await flushPromises();
    expect(lastQuery()).toEqual(query({ range: "7d", q: "lantern" }));
  });

  it("a provider scope change reloads with the provider", async () => {
    await open();
    useScopeStore().provider = "vibes";
    await flushPromises();
    expect(lastQuery()).toEqual(query({ provider: "vibes" }));
  });

  it("the status filter is kept in memory for the next visit", async () => {
    const first = await open();
    await pickStatus(first.w, "Done");
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

  it("a link from Alerts sets a comma status, the range and the search before the first load", async () => {
    await open("/jobs?status=failed,unknown&range=7d&q=%20dola-01%20");
    expect(listJobs).toHaveBeenCalledTimes(1);
    expect([lastQuery().status, lastQuery().range, lastQuery().q]).toEqual(["failed,unknown", "7d", "dola-01"]);
  });

  it("a range or a status list in the link that is not one of ours is ignored", async () => {
    await open("/jobs?status=failed,nope&range=1y");
    expect([lastQuery().status, lastQuery().range]).toEqual(["", "today"]);
  });
});

describe("paging", () => {
  const pager = (w: VueWrapper) => w.findComponent(ListPager);

  it("hands the pager the total, the offset and the page size in offset mode, with the sizes 10, 20, 50 and 100", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    expect(pager(w).props()).toMatchObject({ total: 25, offset: 0, limit: 10, sizes: [10, 20, 50, 100] });
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
    await pickStatus(w, "Done");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "done" }));
    expect(pager(w).props("offset")).toBe(0);
  });

  it("a filter change whose load fails still starts from the first page when it is read again", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 20);
    await flushPromises();
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    await pickStatus(w, "Done");
    await flushPromises();
    await button(w, "Retry")!.trigger("click");
    await flushPromises();
    expect(lastQuery()).toEqual(query({ status: "done" }));
  });

  it("a reload (a change from the detail, a bulk action) stays on the page on screen", async () => {
    listJobs.mockResolvedValue(page([A], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 10);
    await flushPromises();
    await reloadNow();
    expect(lastQuery()).toEqual(query({ offset: 10 }));
  });

  it("a list that shrank under the page on screen ends up on its last page (the pager moves itself)", async () => {
    listJobs.mockResolvedValueOnce(page([A], 25)).mockResolvedValueOnce(page([B], 25));
    const { w } = await open();
    pager(w).vm.$emit("move", 20);
    await flushPromises();
    listJobs.mockReset();
    listJobs.mockResolvedValueOnce(page([], 12)).mockResolvedValueOnce(page([C], 12));
    await reloadNow();
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
  it("with nothing ticked there is no bulk bar; a tick shows it with the count and both buttons, and the count follows", async () => {
    const { w } = await open();
    expect(w.find('[aria-label="Bulk actions"]').exists()).toBe(false);
    await rowTick(w, 1).setValue(true);
    expect(count(w)).toBe("1 selected");
    expect([has(w, "Re-check"), has(w, "Regenerate…")]).toEqual([true, true]);
    await rowTick(w, 0).setValue(true);
    expect(count(w)).toBe("2 selected");
    await rowTick(w, 1).setValue(false);
    await rowTick(w, 0).setValue(false);
    expect(w.find('[aria-label="Bulk actions"]').exists()).toBe(false);
  });

  it("a ticked row is tinted and its box ticked; the select-all box stays empty until every row is", async () => {
    const { w } = await open();
    await rowTick(w, 1).setValue(true);
    expect(rows(w)[1]!.classes()).toContain("is-picked");
    expect(rows(w)[0]!.classes()).not.toContain("is-picked");
    expect((rowTick(w, 1).element as HTMLInputElement).checked).toBe(true);
    expect((selectAll(w).element as HTMLInputElement).checked).toBe(false);
    await rowTick(w, 0).setValue(true);
    await rowTick(w, 2).setValue(true);
    expect((selectAll(w).element as HTMLInputElement).checked).toBe(true);
  });

  it("select all ticks the page and again clears it; × clears the selection", async () => {
    const { w } = await open();
    await tickAll(w, true);
    expect(count(w)).toBe("3 selected");
    await tickAll(w, false);
    expect(count(w)).toBe("0 selected");
    await rowTick(w, 0).setValue(true);
    await w.get('[aria-label="Clear the selection"]').trigger("click");
    expect(count(w)).toBe("0 selected");
  });

  it("ticking a box opens nothing; a click on the row, or on its title, opens the job", async () => {
    const { w } = await open();
    const ui = useUiStore();
    await rowTick(w, 0).trigger("click");
    expect(ui.openJobId).toBeNull();
    await rows(w)[1]!.findAll("td")[3]!.trigger("click");
    expect(ui.openJobId).toBe(B);
    ui.closeJob();
    await rows(w)[2]!.find(".title-link").trigger("click");
    expect(ui.openJobId).toBe(C);
  });

  it("a phone has no tick boxes and no bulk bar; a row opens its job", async () => {
    stubWidth("phone");
    const { w } = await open();
    expect(w.find("input[type=checkbox]").exists()).toBe(false);
    expect(w.find('[aria-label="Bulk actions"]').exists()).toBe(false);
    await rows(w)[0]!.trigger("click");
    expect(useUiStore().openJobId).toBe(A);
  });

  it("a new page starts with nothing ticked", async () => {
    listJobs.mockResolvedValueOnce(page([A], 25)).mockResolvedValueOnce(page([B], 25));
    const { w } = await open();
    await rowTick(w, 0).setValue(true);
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
    await rowTick(w, 0).setValue(true);
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    await reloadNow();
    expect(rows(w)).toHaveLength(0);
    await button(w, "Retry")!.trigger("click");
    await flushPromises();
    expect(count(w)).toBe("1 selected");
    expect((rowTick(w, 0).element as HTMLInputElement).checked).toBe(true);
  });

  it("a reload of the same page keeps the ticks that are still on it", async () => {
    const { w } = await open();
    await rowTick(w, 0).setValue(true);
    await rowTick(w, 2).setValue(true);
    listJobs.mockResolvedValueOnce(page([A, B]));
    await reloadNow();
    expect(count(w)).toBe("1 selected");
  });
});

// The same tests on a desktop and a tablet (both draw the tick boxes). A tick, a reload, a page or filter change, an
// error and Retry must send the same ids.
describe.each(["desktop", "tablet"] as const)("what is sent is what is ticked on screen (%s)", (width) => {
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
    for (const i of indexes) await rowTick(w, i).setValue(true);
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    return w;
  }
  async function reload(next: JobPage | ApiError = page([A, B, C])): Promise<void> {
    if (next instanceof ApiError) listJobs.mockRejectedValueOnce(next);
    else listJobs.mockResolvedValueOnce(next);
    announceJobChange();
    await settle();
  }
  async function press(w: VueWrapper, label: string): Promise<void> {
    await button(w, label)!.trigger("click");
    await settle();
  }

  it.each(labels)("a tick right after a reload counts at once: %s sends exactly the ticks on screen", async (label) => {
    const w = await openTicked([0]);
    await reload();
    await rowTick(w, 2).setValue(true);
    expect(ticks(w)).toEqual([true, false, true]);
    expect(count(w)).toBe("2 selected");
    expect(action(w)).toBe(`bulk:${A},${C}`);
    await press(w, label);
    expect(bulk[label].mock.calls).toEqual([[[A, C]]]);
  });

  it.each(labels)("an untick right after a reload counts at once: %s leaves the unticked job out", async (label) => {
    const w = await openTicked([0, 1]);
    await reload();
    await rowTick(w, 0).setValue(false);
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
    announceJobChange();
    await settle();
    announceJobChange();
    await settle();
    await rowTick(w, 1).setValue(true);
    newer.resolve(page([A, B, C]));
    await settle();
    await rowTick(w, 2).setValue(true);
    older.resolve(page([B, D]));
    await settle();
    expect(ticks(w)).toEqual([true, true, true]);
    expect(action(w)).toBe(`bulk:${A},${B},${C}`);
    await press(w, "Regenerate…");
    expect(bulkRegenerate.mock.calls).toEqual([[[A, B, C]]]);
  });

  it("an error hides the table: nothing can be sent until Retry draws the ticks again, and then they are what is sent", async () => {
    const w = await openTicked([0]);
    await reload(boom());
    expect(rows(w)).toHaveLength(0);
    expect(count(w)).toBe("0 selected");
    expect(has(w, "Regenerate…")).toBe(false);
    expect(has(w, "Re-check")).toBe(false);
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
    announceJobChange();
    await settle();
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
    await reload(boom());
    listJobs.mockResolvedValueOnce(page([A, B]));
    await press(w, "Retry");
    expect(count(w)).toBe("0 selected");
    await reload(page([A, B, C]));
    expect(ticks(w)).toEqual([false, false, false]);
    expect(count(w)).toBe("0 selected");
  });

  it("a reload of the same page keeps the ticks still on it, and they are what is sent", async () => {
    const w = await openTicked([0, 2]);
    await reload(page([A, B]));
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
    await rowTick(w, 1).setValue(true);
    expect(action(w)).toBe(`bulk:${D}`);
    await press(w, "Regenerate…");
    expect(bulkRegenerate.mock.calls).toEqual([[[D]]]);
  });

  it("a filter change unticks the old rows at once, before the new list arrives", async () => {
    const w = await openTicked([0]);
    listJobs.mockReturnValueOnce(deferred().promise);
    await pickStatus(w, "Done");
    await settle();
    expect(ticks(w)).toEqual([false, false, false]);
    expect(count(w)).toBe("0 selected");
    await rowTick(w, 1).setValue(true);
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
    expect(has(w, "Regenerate…")).toBe(false);
    expect(has(w, "Re-check")).toBe(false);
  });

  it("a filter change while an error hides the table: the new list starts with nothing ticked", async () => {
    const w = await openTicked([0]);
    await reload(boom());
    listJobs.mockResolvedValueOnce(page([A, B]));
    await pickStatus(w, "Done");
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
    await reload();
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
    await reload(page([A, B]));
    answer(true);
    await settle();
    expect(bulkRegenerate.mock.calls).toEqual([[[A]]]);
  });

  it("a confirm answered after a failed reload hid the table sends nothing", async () => {
    const w = await openTicked([0]);
    let answer!: (ok: boolean) => void;
    vi.mocked(confirmDialog).mockReturnValueOnce(new Promise<boolean>((r) => (answer = r)));
    await press(w, "Regenerate…");
    await reload(boom());
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
    await rowTick(w, 2).setValue(true);
    await rowTick(w, 0).setValue(true);
    await button(w, "Re-check")!.trigger("click");
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
    await rowTick(w, 0).setValue(true);
    await button(w, "Re-check")!.trigger("click");
    await button(w, "Re-check")!.trigger("click");
    expect(bulkRecheck).toHaveBeenCalledTimes(1);
    done({ rechecked: [A], skipped: {} });
    await flushPromises();
  });

  it("jobs left alone turn the toast to info", async () => {
    bulkRecheck.mockResolvedValue({ rechecked: [A], skipped: { [B]: "job.no_recheck" } });
    const { w } = await open();
    await tickAll(w, true);
    await button(w, "Re-check")!.trigger("click");
    await flushPromises();
    expect(notify.info).toHaveBeenCalledWith("Re-checked 1, left 1 alone");
  });

  it("a failed call is a toast and the ticks stay", async () => {
    bulkRecheck.mockRejectedValue(new ApiError(422, { error: { code: "x", message: "too many", param: "job_ids" } }));
    const { w } = await open();
    await rowTick(w, 0).setValue(true);
    await button(w, "Re-check")!.trigger("click");
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
    await tickAll(w, true);
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    await button(w, "Regenerate…")!.trigger("click");
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
    await rowTick(w, 0).setValue(true);
    await button(w, "Regenerate…")!.trigger("click");
    await button(w, "Regenerate…")!.trigger("click");
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    answer(false);
    await flushPromises();
    expect(bulkRegenerate).not.toHaveBeenCalled();
  });

  it("sends the ticked ids, says made, already made and failed, and clears the ticks", async () => {
    bulkRegenerate.mockResolvedValue(MADE);
    const { w } = await open();
    await rowTick(w, 2).setValue(true);
    await rowTick(w, 0).setValue(true);
    await button(w, "Regenerate…")!.trigger("click");
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
    await rowTick(w, 0).setValue(true);
    await rowTick(w, 1).setValue(true);
    await button(w, "Regenerate…")!.trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith(RETRY_SAFE_TEXT);
    expect(count(w)).toBe("2 selected");
    expect(listJobs).toHaveBeenCalledTimes(1);
    await button(w, "Regenerate…")!.trigger("click");
    await flushPromises();
    expect(bulkRegenerate.mock.calls).toEqual([[[A, B]], [[A, B]]]);
  });

  it("the control's action is the sorted selection, so an unchanged selection is one action and a changed one is another", async () => {
    const { w } = await open();
    const action = () => w.findComponent(RegenerateControl).props("action");
    await rowTick(w, 1).setValue(true);
    await rowTick(w, 0).setValue(true);
    expect(action()).toBe(`bulk:${A},${B}`);
    await rowTick(w, 1).setValue(false);
    expect(action()).toBe(`bulk:${A}`);
  });

  it("offers a new regenerate only once a key was kept for exactly this selection", async () => {
    const { w } = await open();
    await rowTick(w, 0).setValue(true);
    await rowTick(w, 1).setValue(true);
    sessionStorage.setItem("regenerateOperations", JSON.stringify({ [`bulk:${A},${B}`]: "z".repeat(20) }));
    // the control reads the record after each call and when its action changes
    await rowTick(w, 1).setValue(false);
    expect(w.text()).not.toContain("Start a new regenerate");
    await rowTick(w, 1).setValue(true);
    expect(w.text()).toContain("Start a new regenerate");
  });
});

describe("the page head and the 10-second read", () => {
  const hidden = (on: boolean) => Object.defineProperty(document, "hidden", { configurable: true, get: () => on });
  afterEach(() => {
    vi.useRealTimers();
    Reflect.deleteProperty(document, "hidden");
  });

  it("heads the page with the title and New test job, which opens the Test page", async () => {
    const { w, router } = await open();
    expect(w.find("h1").text()).toBe("Jobs");
    await button(w, "New test job")!.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("test");
  });

  it("a phone keeps the title for a screen reader and has New test job as an icon button", async () => {
    stubWidth("phone");
    const { w } = await open();
    expect(w.find("h1").classes()).toContain("sr-only");
    expect(w.find('[aria-label="New test job"]').exists()).toBe(true);
    expect(button(w, "New test job")).toBeUndefined();
  });

  it("the banner comes after the Today section and before the list", async () => {
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
    const { useStatsStore } = await import("@/stores/stats");
    await useStatsStore().refresh();
    await flushPromises();
    const order = w.findAll(
      '[data-testid="jobs-today"], [role="status"]:not(.jl__box), section[aria-label="Recent jobs"]',
    );
    expect(order.map((n) => n.attributes("data-testid") ?? n.attributes("role") ?? n.attributes("aria-label"))).toEqual(
      ["jobs-today", "status", "Recent jobs"],
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
  ])("a job a second either side of local midnight in %s sits under two day headers", async (tz, at) => {
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
    expect(lines[1]).toContain("aaaaaaaa");
    expect(lines[2]).toContain("14 Oct");
    expect(lines[3]).toContain("bbbbbbbb");
  });

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
    ["the status", (w: VueWrapper) => pickStatus(w, "Done")],
    ["the key", (w: VueWrapper) => keySelect(w).vm.$emit("update:modelValue", "k1")],
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
    await reloadNow();
    expect(day(w)).toContain("· 212 jobs");
    listJobs.mockReturnValueOnce(new Promise<JobPage>(() => {}));
    await change(w);
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
  it("title, account and error code are text, never markup", async () => {
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
  const provider = (name: string, over: Partial<shell.ProviderSummary> = {}): shell.ProviderSummary => ({
    provider: name,
    execution: "http",
    claims_enabled: true,
    running: 0,
    queued: 0,
    done_today: 0,
    failed_today: 0,
    accounts: { usable: 0, total: 0 },
    pools: [],
    ...over,
  });
  const twoProviders: shell.ProvidersSummary = {
    day_tz: "UTC",
    day_start_ms: 0,
    hours: [{ h: 0, done: { dola: 4, vibes: 2 }, failed: {} }],
    providers: [
      provider("dola", {
        execution: "browser",
        running: 2,
        queued: 4,
        done_today: 446,
        failed_today: 19,
        accounts: { usable: 5, total: 8 },
      }),
      provider("vibes", { done_today: 312, failed_today: 3, accounts: { usable: 3, total: 3 } }),
    ],
  };

  it("every row carries its provider chip in the provider's colours; the model is not coloured", async () => {
    listJobs.mockResolvedValue({ items: [job(A, { provider: "vibes" })], total: 1, next_cursor: null });
    const { w } = await open();
    const row = rows(w)[0]!;
    expect(row.get(".pchip").classes()).toContain("p-vibes");
    expect(row.get(".pchip").text()).toBe("vibes");
    expect(row.get(".sub").classes().join(" ")).not.toMatch(/p-/);
  });

  it("the Today section adds the providers up: done today, in flight with the queued, failed with its share, usable accounts", async () => {
    vi.mocked(shell.getProvidersSummary).mockResolvedValue(twoProviders);
    const { w } = await open();
    const today = w.get('[data-testid="jobs-today"]');
    expect(today.get(".jt-big").text()).toBe("758");
    expect(today.text()).toContain("In flight now2");
    expect(today.text()).toContain("4 queued");
    expect(today.text()).toContain("Failed today22");
    expect(today.text()).toContain("2.8%");
    expect(today.text()).toContain("8 / 11");
    expect(today.text()).toContain("3 need a look");
    expect(today.findAll(".jt-hour")).toHaveLength(24);
  });

  it("a provider card shows done, failed (red above 3%), its send path and its usable accounts", async () => {
    vi.mocked(shell.getProvidersSummary).mockResolvedValue(twoProviders);
    const { w } = await open();
    const dola = w.get('[data-testid="provider-card-dola"]');
    expect(dola.text()).toContain("Browser");
    expect(dola.text()).toContain("446");
    expect(dola.get(".jt-card__fail").text()).toBe("19 failed");
    expect(dola.get(".jt-card__fail").classes()).toContain("is-bad");
    expect(dola.text()).toContain("5 / 8");
    expect(w.get('[data-testid="provider-card-vibes"] .jt-card__fail').classes()).not.toContain("is-bad");
  });

  it("reads the provider summary with the list and on every 10-second read; a card picks the provider, again clears it", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    vi.mocked(shell.getProvidersSummary).mockResolvedValue(twoProviders);
    const { w } = await open();
    const reads = vi.mocked(shell.getProvidersSummary).mock.calls.length;
    expect(reads).toBeGreaterThan(0);
    vi.advanceTimersByTime(10_000);
    await flushPromises();
    expect(vi.mocked(shell.getProvidersSummary).mock.calls.length).toBe(reads + 1);
    await w.get('[data-testid="provider-card-vibes"]').trigger("click");
    await flushPromises();
    expect(useScopeStore().provider).toBe("vibes");
    expect(w.get('[data-testid="provider-card-vibes"]').attributes("aria-pressed")).toBe("true");
    expect(listJobs).toHaveBeenLastCalledWith(query({ provider: "vibes" }));
    await w.get('[data-testid="provider-card-vibes"]').trigger("click");
    expect(useScopeStore().provider).toBe("");
  });

  it("a tablet draws three figure cards and no provider cards; a phone one card with three tiles", async () => {
    vi.mocked(shell.getProvidersSummary).mockResolvedValue(twoProviders);
    stubWidth("tablet");
    let { w } = await open();
    expect(w.findAll(".jt-stat")).toHaveLength(3);
    expect(w.find('[data-testid="provider-card-dola"]').exists()).toBe(false);
    w.unmount();
    mounted.pop();
    stubWidth("phone");
    ({ w } = await open());
    expect(w.findAll(".jt-tile").map((t) => t.text())).toEqual(["In flight2", "Failed22", "Usable8/11"]);
    expect(w.findAll(".jt-spark__bar")).toHaveLength(24);
  });
});
