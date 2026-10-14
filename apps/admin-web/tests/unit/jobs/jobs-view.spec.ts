import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";
import { ApiError } from "@/api/client";
import * as api from "@/api/jobs";
import type { JobPage, JobSummary } from "@/api/jobs";
import { announceJobChange } from "@/components/jobs/job-changes";
import { rememberedStatus } from "@/components/jobs/job-filters";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
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
const page = (ids: string[], next: string | null = null): JobPage => ({
  items: ids.map((id) => job(id)),
  next_cursor: next,
});
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
  vi.mocked(confirmDialog).mockReset().mockResolvedValue(true);
  for (const fn of Object.values(notify)) vi.mocked(fn).mockReset();
});

async function open(url = "/jobs") {
  setActivePinia(createPinia());
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/jobs", name: "jobs", component: JobsView }],
  });
  await router.push(url);
  const w = mount(JobsView, { global: { plugins: [router] }, attachTo: document.body });
  mounted.push(w);
  await flushPromises();
  await flushPromises();
  return { w, router };
}

const rows = (w: VueWrapper) => w.findAll("tbody tr");
const rowTick = (w: VueWrapper, i: number) => rows(w)[i]!.find("input[type=checkbox]");
const selectAll = (w: VueWrapper) => w.find(".jobs__bulk input[type=checkbox]");
const button = (w: VueWrapper, text: string) => w.findAll("button").find((b) => b.text() === text)!;
const count = (w: VueWrapper) => w.find(".jobs__count").text();
const selects = (w: VueWrapper) => w.findAllComponents({ name: "ElSelect" });
const lastQuery = () => listJobs.mock.lastCall![0];

describe("the list", () => {
  it("loads the first page with no filter and no cursor", async () => {
    await open();
    expect(listJobs).toHaveBeenCalledTimes(1);
    expect(listJobs).toHaveBeenCalledWith({ status: "", provider: "", keyId: "", cursor: null });
  });

  it("shows the 13 columns of the old table", async () => {
    const { w } = await open();
    const headers = w.findAll("thead th").map((th) => th.text());
    expect(headers).toEqual([
      "",
      "Progress",
      "Title",
      "Job",
      "Operation",
      "Status",
      "Points charged",
      "Results",
      "References",
      "Account",
      "Key",
      "Created",
      "Actions",
    ]);
  });

  it("draws one row per job with the merged cells", async () => {
    listJobs.mockResolvedValue({
      items: [
        job(A, { title: null, model_substituted: true }),
        job(B, { status: "failed", error_code: "render.deadline", price: 10, net_charged: 0 }),
      ],
      next_cursor: null,
    });
    const { w } = await open();
    const [first, second] = rows(w);
    expect(first!.text()).toContain("(untitled)");
    expect(first!.text()).toContain("aaaaaaaa");
    expect(first!.text()).toContain("dola");
    expect(first!.text()).toContain("video · veo");
    expect(first!.text()).toContain("model differs");
    expect(first!.text()).not.toContain("refunded");
    expect(second!.text()).toContain("failed");
    expect(second!.text()).toContain("refunded");
    expect(second!.text()).toContain("render.deadline");
    expect(second!.text()).toContain("acct-1");
    expect(second!.text()).toContain("key-one");
  });

  it("says No jobs for an empty list", async () => {
    listJobs.mockResolvedValue(page([]));
    const { w } = await open();
    expect(w.text()).toContain("No jobs");
  });

  it("the bar has the step of the status", async () => {
    listJobs.mockResolvedValue({ items: [job(A, { status: "fetching" })], next_cursor: null });
    const { w } = await open();
    const bar = w.find("[role=progressbar]");
    expect(bar.attributes("aria-valuenow")).toBe("3");
    expect(w.find(".bar__fill").attributes("style")).toContain("width: 75%");
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
    expect(lastQuery()).toEqual({ status: "submitting", provider: "", keyId: "", cursor: null });
  });

  it("the key filter sends the key id", async () => {
    const { w } = await open();
    selects(w)[1]!.vm.$emit("update:modelValue", "k1");
    await flushPromises();
    expect(lastQuery()).toEqual({ status: "", provider: "", keyId: "k1", cursor: null });
  });

  it("a provider scope change reloads with the provider", async () => {
    await open();
    useScopeStore().provider = "vibes";
    await flushPromises();
    expect(lastQuery()).toEqual({ status: "", provider: "vibes", keyId: "", cursor: null });
  });

  it("the status filter is kept in memory for the next visit", async () => {
    const first = await open();
    selects(first.w)[0]!.vm.$emit("update:modelValue", "done");
    await flushPromises();
    first.w.unmount();
    mounted.pop();
    listJobs.mockClear();
    await open();
    expect(listJobs).toHaveBeenCalledWith({ status: "done", provider: "", keyId: "", cursor: null });
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
  it("Next asks for the cursor the server gave, and Prev goes back to the page it left", async () => {
    listJobs.mockResolvedValueOnce(page([A], "c-2")).mockResolvedValueOnce(page([B], "c-3"));
    const { w } = await open();
    const nav = () => w.findAll("nav button");
    expect(w.text()).toContain("Page 1");
    expect(nav()[0]!.attributes("disabled")).toBeDefined();
    await nav()[1]!.trigger("click");
    await flushPromises();
    expect(lastQuery().cursor).toBe("c-2");
    expect(w.text()).toContain("Page 2");
    expect(rows(w)[0]!.text()).toContain("bbbbbbbb");
    listJobs.mockResolvedValueOnce(page([A], "c-2"));
    await nav()[0]!.trigger("click");
    await flushPromises();
    expect(lastQuery().cursor).toBeNull();
    expect(w.text()).toContain("Page 1");
    expect(rows(w)[0]!.text()).toContain("aaaaaaaa");
  });

  it("Next is off on the last page", async () => {
    const { w } = await open();
    expect(w.findAll("nav button")[1]!.attributes("disabled")).toBeDefined();
  });

  it("a filter change starts again from page 1", async () => {
    listJobs.mockResolvedValueOnce(page([A], "c-2")).mockResolvedValueOnce(page([B]));
    const { w } = await open();
    await w.findAll("nav button")[1]!.trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Page 2");
    listJobs.mockResolvedValueOnce(page([A]));
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await flushPromises();
    expect(lastQuery()).toEqual({ status: "failed", provider: "", keyId: "", cursor: null });
    expect(w.text()).toContain("Page 1");
  });

  it("a filter change whose load fails still forgets the cursors of the old filter", async () => {
    listJobs.mockResolvedValueOnce(page([A], "c-2")).mockResolvedValueOnce(page([B], "c-3"));
    const { w } = await open();
    await w.findAll("nav button")[1]!.trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Page 2");
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    selects(w)[0]!.vm.$emit("update:modelValue", "failed");
    await flushPromises();
    expect(w.text()).toContain("Page 1");
    expect(w.findAll("nav button")[1]!.attributes("disabled")).toBeDefined();
  });

  it("a failed page move leaves the cursor where it was", async () => {
    listJobs.mockResolvedValueOnce(page([A], "c-2"));
    const { w } = await open();
    listJobs.mockRejectedValueOnce(new ApiError(500, { error: { code: "x", message: "boom" } }));
    await w.findAll("nav button")[1]!.trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Page 1");
  });
});

describe("selection", () => {
  it("the bulk buttons stay off until a job is ticked, and the count follows", async () => {
    const { w } = await open();
    expect(count(w)).toBe("0 selected");
    expect(button(w, "Re-check selected").attributes("disabled")).toBeDefined();
    expect(button(w, "Regenerate selected").attributes("disabled")).toBeDefined();
    await rowTick(w, 1)!.setValue(true);
    expect(count(w)).toBe("1 selected");
    expect(button(w, "Re-check selected").attributes("disabled")).toBeUndefined();
    expect(button(w, "Regenerate selected").attributes("disabled")).toBeUndefined();
    await rowTick(w, 1)!.setValue(false);
    expect(count(w)).toBe("0 selected");
  });

  it("select all ticks the page and again clears it", async () => {
    const { w } = await open();
    await selectAll(w).setValue(true);
    expect(count(w)).toBe("3 selected");
    expect((rowTick(w, 2)!.element as HTMLInputElement).checked).toBe(true);
    await selectAll(w).setValue(false);
    expect(count(w)).toBe("0 selected");
  });

  it("select all is half-ticked for a part of the page and full when every row is ticked", async () => {
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    expect(w.find(".jobs__bulk .is-indeterminate").exists()).toBe(true);
    await rowTick(w, 1)!.setValue(true);
    await rowTick(w, 2)!.setValue(true);
    expect(w.find(".jobs__bulk .is-indeterminate").exists()).toBe(false);
    expect((selectAll(w).element as HTMLInputElement).checked).toBe(true);
  });

  it("ticking a box opens nothing; a click on the row, or Details, opens the job", async () => {
    const { w } = await open();
    const ui = useUiStore();
    await rowTick(w, 0)!.trigger("click");
    expect(ui.openJobId).toBeNull();
    await rows(w)[1]!.findAll("td")[2]!.trigger("click");
    expect(ui.openJobId).toBe(B);
    ui.closeJob();
    await rows(w)[2]!
      .findAll("button")
      .find((b) => b.text() === "Details")!
      .trigger("click");
    expect(ui.openJobId).toBe(C);
  });

  it("a new page starts with nothing ticked", async () => {
    listJobs.mockResolvedValueOnce(page([A], "c-2")).mockResolvedValueOnce(page([B]));
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await w.findAll("nav button")[1]!.trigger("click");
    await flushPromises();
    expect(count(w)).toBe("0 selected");
    listJobs.mockResolvedValueOnce(page([A], "c-2"));
    await w.findAll("nav button")[0]!.trigger("click");
    await flushPromises();
    expect(count(w)).toBe("0 selected");
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

describe("bulk re-check", () => {
  it("posts the ticked ids in table order, says how many, and clears the ticks", async () => {
    bulkRecheck.mockResolvedValue({ rechecked: [A, C], skipped: {} });
    const { w } = await open();
    await rowTick(w, 2)!.setValue(true);
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Re-check selected").trigger("click");
    await flushPromises();
    expect(bulkRecheck).toHaveBeenCalledWith([A, C]);
    expect(notify.success).toHaveBeenCalledWith("Re-checked 2");
    expect(count(w)).toBe("0 selected");
    expect(listJobs).toHaveBeenCalledTimes(2);
  });

  it("jobs left alone turn the toast to info", async () => {
    bulkRecheck.mockResolvedValue({ rechecked: [A], skipped: { [B]: "job.no_recheck" } });
    const { w } = await open();
    await selectAll(w).setValue(true);
    await button(w, "Re-check selected").trigger("click");
    await flushPromises();
    expect(notify.info).toHaveBeenCalledWith("Re-checked 1, left 1 alone");
  });

  it("a failed call is a toast and the ticks stay", async () => {
    bulkRecheck.mockRejectedValue(new ApiError(422, { error: { code: "x", message: "too many", param: "job_ids" } }));
    const { w } = await open();
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Re-check selected").trigger("click");
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
    await selectAll(w).setValue(true);
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    await button(w, "Regenerate selected").trigger("click");
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

  it("sends the ticked ids, says made, already made and failed, and clears the ticks", async () => {
    bulkRegenerate.mockResolvedValue(MADE);
    const { w } = await open();
    await rowTick(w, 2)!.setValue(true);
    await rowTick(w, 0)!.setValue(true);
    await button(w, "Regenerate selected").trigger("click");
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
    await button(w, "Regenerate selected").trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith(RETRY_SAFE_TEXT);
    expect(count(w)).toBe("2 selected");
    expect(listJobs).toHaveBeenCalledTimes(1);
    await button(w, "Regenerate selected").trigger("click");
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

describe("server text", () => {
  it("title, key, account and error code are text, never markup", async () => {
    const planted = '<img src=x onerror="window.__pwned=1">';
    listJobs.mockResolvedValue({
      items: [job(A, { title: planted, key_label: planted, account_label: planted, error_code: planted })],
      next_cursor: null,
    });
    const { w } = await open();
    expect(w.find("tbody img").exists()).toBe(false);
    expect(rows(w)[0]!.text()).toContain(planted);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
