import { DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as api from "@/api/jobs-detail";
import type { JobDetail, JobMediaItem } from "@/api/jobs-detail";
import { openBlob, saveBlob } from "@/components/jobs/blob-actions";
import { jobChanges } from "@/components/jobs/job-changes";
import JobDetailDrawer from "@/components/jobs/JobDetailDrawer.vue";
import { copyText } from "@/components/shared/clipboard";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

vi.mock("@/api/jobs-detail", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/jobs-detail")>();
  return {
    ...actual,
    getJob: vi.fn(),
    renameJob: vi.fn(),
    recheckJob: vi.fn(),
    recoverJob: vi.fn(),
    fetchTitle: vi.fn(),
    regenerateJob: vi.fn(),
    resendWebhook: vi.fn(),
    revokeLinks: vi.fn(),
    fetchMediaBlob: vi.fn(),
    fetchDownload: vi.fn(),
    fetchDebug: vi.fn(),
  };
});
const poll = vi.fn();
vi.mock("@/composables/use-command-poll", () => ({ useCommandPoll: () => ({ poll }) }));
vi.mock("@/components/jobs/blob-actions", () => ({ openBlob: vi.fn(), saveBlob: vi.fn() }));
vi.mock("@/components/shared/clipboard", () => ({ copyText: vi.fn() }));
vi.mock("@/components/shared/confirm-dialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// Built in two parts: the lint rule bans the literal.
const SCRIPT_SCHEME = "java" + "script:";
const getJob = vi.mocked(api.getJob);
const A = "aaaaaaaa-1111";
const B = "bbbbbbbb-2222";

const detail = (over: Partial<JobDetail> = {}): JobDetail => ({
  id: A,
  provider: "dola",
  key_id: "k1",
  key_label: "key-one",
  key_prefix: "rk-key1",
  operation: "video",
  media_type: "video",
  model: "veo",
  title: "My title",
  status: "done",
  error_code: null,
  price: 10,
  account_id: "acc1",
  account_label: "acct-1",
  external_ref: null,
  created_ms: 1_700_000_000_000,
  finished_ms: null,
  error_status: null,
  can_recheck: false,
  outputs: 0,
  inputs: 0,
  net_charged: 10,
  model_substituted: false,
  prompt: "a cat on a roof",
  marker: "MARK",
  params: { ratio: "16:9" },
  completion: null,
  wallet_kind: "prepaid",
  pool: "video",
  account_cost: 1,
  attempts: 2,
  claims: 3,
  sent_ms: 1_700_000_001_000,
  render_since_ms: null,
  updated_ms: 1_700_000_002_000,
  upstream_price: null,
  upstream_model: null,
  error: null,
  warnings: null,
  callback: { status: null, attempts: null, last: null },
  no_recheck: false,
  frame_id: null,
  sent_text: "FRAME a cat on a roof MARK",
  events: [],
  media: [],
  debug_files: [],
  ...over,
});
const media = (over: Partial<JobMediaItem> = {}): JobMediaItem => ({
  role: "output",
  idx: 0,
  mime: "image/png",
  size: 120,
  width: 1,
  height: 1,
  duration_s: null,
  url: "/m/tok1",
  ...over,
});

const body = () => new DOMWrapper(document.body);
// A button is found by its text, or by its aria-label when it is an icon button.
const nameOf = (b: DOMWrapper<Element>): string => b.attributes("aria-label") ?? b.text();
const button = (text: string) =>
  body()
    .findAll("button")
    .find((b) => nameOf(b) === text);
const section = (name: string) => body().find(`[data-section=${name}]`);
const sectionNames = () =>
  body()
    .findAll("[data-section]")
    .map((s) => s.attributes("data-section"));
const buttonsIn = (name: string) => section(name).findAll("button").map(nameOf);
const mounted: VueWrapper[] = [];

async function goTo(label: "Overview" | "Timeline" | "Technical") {
  await body()
    .findAll("[role=tab]")
    .find((t) => t.text() === label)!
    .trigger("click");
  await flushPromises();
}

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  sessionStorage.clear();
  getJob.mockReset().mockResolvedValue(detail());
  for (const fn of Object.values(api)) if (vi.isMockFunction(fn) && fn !== getJob) fn.mockReset();
  poll.mockReset().mockResolvedValue({});
  vi.mocked(openBlob).mockReset();
  vi.mocked(saveBlob).mockReset();
  vi.mocked(copyText).mockReset().mockResolvedValue("clipboard");
  vi.mocked(confirmDialog).mockReset().mockResolvedValue(true);
  for (const fn of Object.values(notify)) vi.mocked(fn).mockReset();
});

async function show(job: JobDetail = detail(), tab?: "Timeline" | "Technical") {
  while (mounted.length) mounted.pop()!.unmount();
  document.body.replaceChildren();
  setActivePinia(createPinia());
  getJob.mockResolvedValue(job);
  const w = mount(JobDetailDrawer, { attachTo: document.body });
  mounted.push(w);
  const ui = useUiStore();
  ui.openJob(job.id);
  await flushPromises();
  await flushPromises();
  if (tab) await goTo(tab);
  return { w, ui };
}

// The viewport is a phone: matchMedia answers true for the 767 px query only.
const stubPhone = () =>
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: /767/.test(query),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));

describe("opening", () => {
  it("draws nothing until a job is opened", async () => {
    setActivePinia(createPinia());
    mounted.push(mount(JobDetailDrawer, { attachTo: document.body }));
    await flushPromises();
    expect(getJob).not.toHaveBeenCalled();
    expect(body().find("[data-section]").exists()).toBe(false);
  });

  it("reads the job that was opened and names the drawer by its first 8 characters", async () => {
    await show();
    expect(getJob).toHaveBeenCalledWith(A);
    expect(body().find("[role=dialog]").attributes("aria-label")).toBe("Job aaaaaaaa");
  });

  // The three heading tests need DetailDrawer's `heading` prop (the visible h2), which F2 adds.
  it("the drawer is headed with the job's title", async () => {
    await show();
    expect(body().find(".detail-drawer__title").text()).toBe("My title");
  });

  it("a job with no title is headed (untitled)", async () => {
    await show(detail({ title: null }));
    expect(body().find(".detail-drawer__title").text()).toBe("(untitled)");
  });

  it("a title with markup is text in the heading", async () => {
    const planted = `<img src=x onerror="window.__pwned=1">`;
    await show(detail({ title: planted }));
    expect(body().find(".detail-drawer__title").text()).toBe(planted);
    expect(body().find(".detail-drawer__title img").exists()).toBe(false);
  });

  it("the header says status, what it is and a copy chip with the short id", async () => {
    await show(detail({ media_type: "image", operation: "image", media: [media(), media({ idx: 1 })] }));
    const chips = body().find(".detail-drawer__chips");
    expect(chips.text()).toContain("done");
    expect(chips.text()).toContain("image · veo · 2 outputs");
    expect(chips.find("button").attributes("aria-label")).toBe("Copy job id aaaaaaaa");
    expect(chips.find("button").text()).toBe("aaaaaaaa");
    await chips.find("button").trigger("click");
    await flushPromises();
    expect(vi.mocked(copyText).mock.calls[0]![0]).toBe("aaaaaaaa");
  });

  it("a phone header says what it is without the count", async () => {
    stubPhone();
    await show(detail({ media: [media()] }));
    expect(body().find(".detail-drawer__chips").text()).toContain("video · veo");
    expect(body().find(".detail-drawer__chips").text()).not.toContain("output");
  });

  it("the phone sheet's Back names the page it goes back to", async () => {
    stubPhone();
    const { w } = await show();
    expect(button("Back to Jobs")).toBeDefined();
    await w.setProps({ backLabel: "Back to Alerts" });
    expect(button("Back to Alerts")).toBeDefined();
  });

  it("an answer with no outputs count still says how many results there are", async () => {
    await show(detail({ outputs: undefined as unknown as number, media: [] }));
    expect(body().find(".detail-drawer__chips").text()).toContain("video · veo · 0 outputs");
  });

  it("has Overview, Timeline and Technical tabs, Overview first and selected", async () => {
    await show();
    const tabs = body().findAll("[role=tab]");
    expect(tabs.map((t) => [t.text(), t.attributes("aria-selected")])).toEqual([
      ["Overview", "true"],
      ["Timeline", "false"],
      ["Technical", "false"],
    ]);
    await goTo("Technical");
    expect(
      body()
        .findAll("[role=tab]")
        .map((t) => t.attributes("aria-selected")),
    ).toEqual(["false", "false", "true"]);
  });

  it("each tab has its own sections, in order", async () => {
    await show();
    expect(sectionNames()).toEqual(["conclusion", "actions", "summary", "prompt"]);
    await goTo("Timeline");
    expect(sectionNames()).toEqual(["timeline"]);
    await goTo("Technical");
    expect(sectionNames()).toEqual(["technical", "title", "more-actions", "debug", "sent"]);
  });

  it("Close shuts it", async () => {
    const { ui } = await show();
    await button("Close")!.trigger("click");
    expect(ui.openJobId).toBeNull();
  });

  it("a failed read is a message with Retry", async () => {
    setActivePinia(createPinia());
    getJob.mockRejectedValueOnce(new ApiError(404, { error: { code: "resource.not_found", message: "Not found" } }));
    mounted.push(mount(JobDetailDrawer, { attachTo: document.body }));
    useUiStore().openJob(A);
    await flushPromises();
    // worded as the list's failed load is drawn, naming the job
    expect(body().find("[role=alert]").text()).toContain("Could not load the job: the relay answered 404 (Not found).");
    expect(body().find(".el-skeleton").exists()).toBe(false);
    getJob.mockResolvedValueOnce(detail());
    await button("Retry")!.trigger("click");
    await flushPromises();
    expect(section("conclusion").exists()).toBe(true);
  });

  it("drops the answer for a job that is no longer the open one", async () => {
    setActivePinia(createPinia());
    let slow!: (j: JobDetail) => void;
    getJob.mockImplementationOnce(() => new Promise<JobDetail>((res) => (slow = res)));
    getJob.mockResolvedValueOnce(detail({ id: B, title: "second" }));
    mounted.push(mount(JobDetailDrawer, { attachTo: document.body }));
    const ui = useUiStore();
    ui.openJob(A);
    await flushPromises();
    ui.openJob(B);
    await flushPromises();
    slow(detail({ id: A, title: "first" }));
    await flushPromises();
    expect(body().find("[role=dialog]").attributes("aria-label")).toBe("Job bbbbbbbb");
    await goTo("Technical");
    expect((body().find("[data-section=title] input").element as HTMLInputElement).value).toBe("second");
  });

  it("a sign-out closes the drawer and drops what it showed", async () => {
    const { ui } = await show(detail(), "Technical");
    expect(section("technical").exists()).toBe(true);
    useAuthStore().signOut();
    await flushPromises();
    expect(ui.openJobId).toBeNull();
    expect(sectionNames()).toEqual([]);
  });

  it("another job opens on Overview, whatever tab the last one was on", async () => {
    const { ui } = await show(detail(), "Technical");
    getJob.mockResolvedValue(detail({ id: B }));
    ui.openJob(B);
    await flushPromises();
    expect(
      body()
        .findAll("[role=tab]")
        .map((t) => t.attributes("aria-selected")),
    ).toEqual(["true", "false", "false"]);
    expect(sectionNames()).toContain("summary");
  });
});

describe("conclusion, ledger, prompt and technical", () => {
  it("a delivered job says what it gave, what it cost and how long it took", async () => {
    await show(
      detail({
        media_type: "image",
        media: [media(), media({ idx: 1 })],
        net_charged: 1,
        finished_ms: 1_700_000_095_000,
      }),
    );
    expect(section("conclusion").text()).toContain("Delivered 2 images");
    expect(section("conclusion").text()).toContain("· net charged 1 point · 1 min 35 s");
    expect(section("conclusion").classes()).toContain("jd-banner--ok");
  });

  it("a failed job says code and reason in an alert, and warnings follow", async () => {
    await show(
      detail({
        status: "failed",
        net_charged: 0,
        error_code: "render.deadline",
        error: "too slow",
        warnings: [{ code: "w" }],
      }),
    );
    expect(section("conclusion").attributes("role")).toBe("alert");
    expect(section("conclusion").text()).toBe("Failed · render.deadline — too slow");
    expect(body().text()).toContain('warnings: [{"code":"w"}]');
  });

  it("a code with no message shows only the code, and no warnings shows no line", async () => {
    await show(detail({ status: "failed", error_code: "render.deadline", error: null }));
    expect(section("conclusion").text()).toBe("Failed · render.deadline");
    expect(body().text()).not.toContain("warnings");
  });

  it("a job on its way says what it is doing, its step, its charge and how long so far", async () => {
    // the done banner's pattern: a bold lead, then the facts muted
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_000_000 + 4 * 60_000);
    const said = async (over: Partial<JobDetail>) => {
      await show(detail(over));
      return [section("conclusion").find("strong").text(), section("conclusion").find(".jd-muted").text()];
    };
    expect(await said({ status: "queued", net_charged: 0 })).toEqual([
      "Waiting for an account",
      "· step 1 of 4 · nothing charged yet · 4 min 0 s so far",
    ]);
    expect(await said({ status: "submitting", net_charged: 2 })).toEqual([
      "Sending it to dola",
      "· step 2 of 4 · charged 2 points · 4 min 0 s so far",
    ]);
    expect(await said({ status: "waiting", net_charged: 2 })).toEqual([
      "Dola is making it",
      "· step 2 of 4 · charged 2 points · 4 min 0 s so far",
    ]);
    expect(await said({ status: "fetching", net_charged: 1 })).toEqual([
      "Fetching the result",
      "· step 3 of 4 · charged 1 point · 4 min 0 s so far",
    ]);
    // unknown is not on the list's bar: no step
    expect(await said({ status: "unknown", net_charged: 2 })).toEqual([
      "Dola has not confirmed it yet",
      "· charged 2 points · 4 min 0 s so far",
    ]);
    vi.restoreAllMocks();
  });

  it("a job on its way shows its step in the header chip, as the list does, and no count of results", async () => {
    await show(detail({ status: "fetching" }));
    const chips = body().find(".detail-drawer__chips");
    expect(chips.find(".step").text()).toBe("3/4");
    expect(chips.text()).toContain("video · veo");
    expect(chips.text()).not.toContain("output");
    await show(detail({ status: "unknown" }));
    expect(body().find(".detail-drawer__chips .step").exists()).toBe(false);
    await show(detail({ status: "done" }));
    expect(body().find(".detail-drawer__chips .step").exists()).toBe(false);
  });

  it("a value that is not set is the list's empty-cell dash", async () => {
    await show(detail({ status: "queued", account_label: null, finished_ms: null }));
    const value = (k: string) => rows().find(([label]) => label === k)?.[1];
    expect(value("Account")).toBe("—");
    expect(value("Finished")).toBe("—");
  });

  const rows = () =>
    section("summary")
      .findAll(".ledger__row")
      .map((r) => [r.find("dt").text(), r.find("dd").text()]);

  it("the ledger of a done video job: model, media, points and the short times", async () => {
    await show(
      detail({
        created_ms: new Date(2026, 9, 15, 20, 41, 12).getTime(),
        finished_ms: new Date(2026, 9, 15, 20, 45, 24).getTime(),
        net_charged: 10,
        price: 10,
        params: { ratio: "16:9", duration: 30 },
      }),
    );
    expect(rows()).toEqual([
      ["Operation", "video"],
      ["Model", "veo"],
      ["Media", "video · 30 s · 16:9"],
      ["Points", "10 charged · 0 refunded"],
      ["Account", "acct-1"],
      ["Key", "key-one"],
      ["Created", "15/10 20:41:12"],
      ["Finished", "15/10 20:45:24"],
    ]);
  });

  it("a key without a label is named by its prefix, as the list names it", async () => {
    await show(detail({ key_label: null }));
    expect(rows().find(([k]) => k === "Key")).toEqual(["Key", "rk-key1"]);
    await show(detail({ key_label: " ", key_prefix: null }));
    expect(rows().find(([k]) => k === "Key")).toEqual(["Key", "k1"]);
  });

  it("a refund strikes the price: the struck price, an arrow and what was kept", async () => {
    await show(
      detail({
        media_type: "image",
        operation: "image",
        status: "failed",
        price: 1,
        net_charged: 0,
        params: { ratio: "2:3", count: 1 },
      }),
    );
    const points = section("summary")
      .findAll(".ledger__row")
      .find((r) => r.find("dt").text() === "Points")!;
    expect(points.find("s").text()).toBe("1");
    expect(points.find("dd").text()).toBe("1 → 0 refunded");
    expect(rows()[2]).toEqual(["Ratio · count", "2:3 · 1"]);
    const unrefunded = await show(detail({ price: 1, net_charged: 1 }));
    expect(unrefunded.w.find("s").exists()).toBe(false);
  });

  it("a done job with a net below its price shows what was charged and no refund", async () => {
    await show(detail({ status: "done", price: 10, net_charged: 4 }));
    const points = section("summary")
      .findAll(".ledger__row")
      .find((r) => r.find("dt").text() === "Points")!;
    expect(points.find("dd").text()).toBe("4 charged");
    expect(points.find("s").exists()).toBe(false);
  });

  it("a substituted model says so in the ledger", async () => {
    await show(detail({ model_substituted: true }));
    expect(rows()[1]).toEqual(["Model", "veo (model differs)"]);
  });

  it("the prompt is the customer's words and Copy prompt copies exactly that", async () => {
    await show();
    expect(section("prompt").find("p").text()).toBe("a cat on a roof");
    await button("Copy prompt")!.trigger("click");
    await flushPromises();
    expect(vi.mocked(copyText).mock.calls[0]![0]).toBe("a cat on a roof");
    expect(notify.success).toHaveBeenCalledWith("Prompt copied");
  });

  it("no toast when nothing could be copied", async () => {
    vi.mocked(copyText).mockResolvedValue("failed");
    await show();
    await button("Copy prompt")!.trigger("click");
    await flushPromises();
    expect(notify.success).not.toHaveBeenCalled();
  });

  const facts = (id: string) =>
    body()
      .findAll(`[data-testid=${id}] .tech__fact`)
      .map((f) => [f.find("dt").text(), f.find("dd").text()]);

  it("lists the technical facts, leaving out what the server does not have", async () => {
    await show(detail({ callback: { status: "failed", attempts: 3, last: null }, frame_id: 7 }), "Technical");
    expect(facts("job-facts")).toEqual([
      ["Job id", A],
      ["Provider / pool", "dola / video"],
      ["Wallet", "dola prepaid"],
      ["Attempts / claims", "2 / 3"],
      ["Account cost", "1 credit"],
      ["Prompt frame", "video v7"],
      ["Callback", "failed · 3 attempts"],
    ]);
    expect(facts("job-diagnostics")).toEqual([
      ["Sent", expect.stringMatching(/2023/)],
      ["Updated", expect.stringMatching(/2023/)],
      ["Params", '{"ratio":"16:9"}'],
    ]);
  });

  it("words a delivered webhook by its HTTP code, a retry by its state and code, and leaves out a cost of 0", async () => {
    const fact = (name: string) => facts("job-facts").find(([k]) => k === name)?.[1];
    await show(detail({ callback: { status: "sent", attempts: 1, last: "HTTP 200" }, account_cost: 2 }), "Technical");
    expect(fact("Callback")).toBe("200 · 1 attempt");
    expect(fact("Account cost")).toBe("2 credits");
    await show(
      detail({ callback: { status: "pending", attempts: 2, last: "HTTP 503: busy" }, account_cost: 0 }),
      "Technical",
    );
    expect(fact("Callback")).toBe("pending · 503 · 2 attempts");
    expect(fact("Account cost")).toBeUndefined();
    await show(detail({ callback: { status: "failed", attempts: 5, last: "error: timeout" } }), "Technical");
    expect(fact("Callback")).toBe("failed · 5 attempts");
  });

  it("a phone lists Key and Account among the facts too", async () => {
    stubPhone();
    await show(detail(), "Technical");
    expect(facts("job-facts").map(([k]) => k)).toEqual([
      "Job id",
      "Provider / pool",
      "Wallet",
      "Key",
      "Account",
      "Attempts / claims",
      "Account cost",
    ]);
    expect(facts("job-facts")[3]).toEqual(["Key", "key-one · rk-key1"]);
    expect(body().find("[data-testid=job-facts]").classes()).toContain("is-phone");
  });

  it("shows the text sent upstream, or a dash when none was sent", async () => {
    await show(detail(), "Technical");
    expect(section("sent").find("[data-field=sent-text]").text()).toBe("FRAME a cat on a roof MARK");
    await show(detail({ sent_text: null }), "Technical");
    expect(section("sent").find("[data-field=sent-text]").text()).toBe("—");
  });
});

describe("server text is text", () => {
  const planted = '<img src=x onerror="window.__pwned=1">';

  it("title, prompt, error, events, params and names never become markup", async () => {
    await show(
      detail({
        title: planted,
        prompt: planted,
        error_code: "render.deadline",
        error: planted,
        status: "failed",
        key_label: planted,
        account_label: planted,
        sent_text: planted,
        params: { x: planted },
        warnings: [planted],
        events: [{ id: 1, at_ms: 1_700_000_000_000, source: planted, stage: planted, text: planted }],
      }),
    );
    expect(section("conclusion").text()).toContain(`render.deadline — ${planted}`);
    expect(section("prompt").find("p").text()).toBe(planted);
    expect(section("summary").text()).toContain(planted);
    await goTo("Timeline");
    expect(section("timeline").text()).toContain(planted);
    await goTo("Technical");
    expect(section("sent").find("pre").text()).toBe(planted);
    expect(section("technical").text()).toContain(JSON.stringify({ x: planted }));
    expect((body().find("[data-section=title] input").element as HTMLInputElement).value).toBe(planted);
    expect(body().find("[role=dialog] img").exists()).toBe(false);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe("rename", () => {
  it("sends the typed title, says Renamed, reads the job again and tells the list", async () => {
    vi.mocked(api.renameJob).mockResolvedValue({ id: A, title: "New name" });
    await show(detail(), "Technical");
    const input = body().find("[data-section=title] input");
    expect((input.element as HTMLInputElement).value).toBe("My title");
    await input.setValue("New name");
    const before = jobChanges.value;
    await button("Rename")!.trigger("click");
    await flushPromises();
    expect(api.renameJob).toHaveBeenCalledWith(A, "New name");
    expect(notify.success).toHaveBeenCalledWith("Renamed");
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(jobChanges.value).toBe(before + 1);
  });

  it("a refused title is the server's message and the list is not told", async () => {
    vi.mocked(api.renameJob).mockRejectedValue(
      new ApiError(422, { error: { code: "input.invalid", message: "Invalid", param: "title" } }),
    );
    await show(detail(), "Technical");
    const before = jobChanges.value;
    await button("Rename")!.trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("Invalid (title)");
    expect(jobChanges.value).toBe(before);
  });
});

describe("which actions show, and where", () => {
  it("a done job offers Regenerate… in the overview row and Recover under More actions", async () => {
    await show();
    expect(buttonsIn("actions")).toEqual(["Regenerate…"]);
    expect(section("actions").find("input").exists()).toBe(false);
    expect(button("Regenerate…")!.attributes("aria-haspopup")).toBe("dialog");
    await goTo("Technical");
    expect(buttonsIn("more-actions")).toEqual(["Recover"]);
    expect(section("more-actions").find("input").attributes("placeholder")).toBe(
      "Account id or label, e.g. acct-n11-01",
    );
  });

  it("a failed job has the Actions card: Recover with its input, then Regenerate… and a helper line", async () => {
    await show(detail({ status: "failed" }));
    expect(section("actions").find("h3").text()).toBe("Actions");
    expect(buttonsIn("actions")).toEqual(["Recover", "Regenerate…"]);
    expect(section("actions").find("input").attributes("placeholder")).toBe("Account id or label, e.g. acct-n11-01");
    expect(section("actions").text()).toContain("Looks for a finished result of this job on that account.");
    expect(section("actions").text()).toContain("Makes a new job at today’s price.");
  });

  it("More actions is a card with its own heading, drawn only when it has a button", async () => {
    await show(detail({ status: "queued" }), "Technical");
    expect(section("more-actions").find("h3").exists()).toBe(false);
    await show(detail({ status: "done" }), "Technical");
    expect(section("more-actions").find("h3").text()).toBe("More actions");
  });

  it("warnings are a banner of their own, and none when there are none", async () => {
    await show(detail({ warnings: [{ code: "w" }] }));
    expect(section("warnings").text()).toContain('warnings: [{"code":"w"}]');
    await show(detail({ warnings: [] }));
    expect(section("warnings").exists()).toBe(false);
  });

  it("Recover is in one place only: the card for a failed job, More actions for a done one", async () => {
    await show(detail({ status: "failed" }), "Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
    expect(section("more-actions").find("input").exists()).toBe(false);
    await goTo("Overview");
    expect(section("actions").findAll("input")).toHaveLength(1);
    await show(detail({ status: "done" }));
    expect(section("actions").find("input").exists()).toBe(false);
    await goTo("Technical");
    expect(buttonsIn("more-actions")).toEqual(["Recover"]);
    expect(section("more-actions").findAll("input")).toHaveLength(1);
  });

  it("a job still running offers neither Recover nor Regenerate…", async () => {
    await show(detail({ status: "queued" }));
    // with nothing to offer there is no action row at all (an empty one would double the gap)
    expect(section("actions").exists()).toBe(false);
    // one that has a row (it can be re-checked) still offers no Regenerate…
    await show(detail({ status: "unknown", can_recheck: true }));
    expect(buttonsIn("actions")).toEqual(["Re-check"]);
    await show(detail({ status: "queued" }));
    await goTo("Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
    expect(section("more-actions").find("input").exists()).toBe(false);
  });

  it("Re-check only when the server says the job can be", async () => {
    await show(detail({ status: "unknown", can_recheck: true }));
    expect(buttonsIn("actions")).toEqual(["Re-check"]);
  });

  it("Download sits first in the row of a job with results, and says all for more than one", async () => {
    await show(detail({ media: [media()] }));
    expect(buttonsIn("actions")).toEqual(["Download", "Regenerate…"]);
    await show(detail({ media: [media(), media({ idx: 1 })] }));
    expect(buttonsIn("actions")).toEqual(["Download all", "Regenerate…"]);
  });

  it("Resend webhook only for a failed callback", async () => {
    await show(detail({ status: "queued", callback: { status: "failed", attempts: 3, last: null } }), "Technical");
    expect(buttonsIn("more-actions")).toEqual(["Resend webhook"]);
    await show(detail({ status: "queued", callback: { status: "pending", attempts: 0, last: null } }), "Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
  });

  it("Revoke share links only while some media still has a link", async () => {
    await show(
      detail({ status: "queued", media: [media({ url: null }), media({ idx: 1, url: "/m/t" })] }),
      "Technical",
    );
    expect(buttonsIn("more-actions")).toEqual(["Revoke share links"]);
    await show(detail({ status: "queued", media: [media({ url: null })] }), "Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
    // another button keeps the card, and still no Revoke without a link
    await show(
      detail({
        status: "queued",
        media: [media({ url: null })],
        callback: { status: "failed", attempts: 3, last: null },
      }),
      "Technical",
    );
    expect(buttonsIn("more-actions")).toEqual(["Resend webhook"]);
  });

  it("Fetch title needs both the upstream reference and the account", async () => {
    await show(detail({ status: "queued", external_ref: "ref-1", account_label: null }), "Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
    await show(detail({ status: "queued", external_ref: null, account_label: "acct" }), "Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
    await show(detail({ status: "queued", external_ref: "ref-1", account_label: "acct" }), "Technical");
    expect(buttonsIn("more-actions")).toEqual(["Fetch title"]);
  });

  it("on a phone the row moves to a sticky footer, and a failed job keeps its card in the body", async () => {
    stubPhone();
    await show(detail({ media: [media()] }));
    const footer = body().find(".el-drawer__footer");
    expect(footer.findAll("button").map(nameOf)).toEqual(["Download", "Regenerate…"]);
    expect(section("actions").exists()).toBe(false);
    // a failed job that can be re-checked keeps everything in its card, Re-check included: no footer
    await show(detail({ status: "failed", can_recheck: true }));
    expect(body().find(".el-drawer__footer").exists()).toBe(false);
    expect(buttonsIn("actions")).toEqual(["Recover", "Re-check", "Regenerate…"]);
  });

  it("on a phone a job with nothing to offer has no footer, and one that can be re-checked has it", async () => {
    stubPhone();
    await show(detail({ status: "queued" }));
    expect(body().find(".el-drawer__footer").exists()).toBe(false);
    await show(detail({ status: "waiting", can_recheck: true }));
    expect(body().find(".el-drawer__footer").findAll("button").map(nameOf)).toEqual(["Re-check"]);
  });
});

describe("loading", () => {
  it("while the read is out the drawer draws the list's loading bars, not Element Plus's shimmer", async () => {
    setActivePinia(createPinia());
    getJob.mockImplementationOnce(() => new Promise<JobDetail>(() => undefined));
    mounted.push(mount(JobDetailDrawer, { attachTo: document.body }));
    useUiStore().openJob(A);
    await flushPromises();
    const bars = body().find("[data-state=loading]");
    expect(bars.attributes("aria-busy")).toBe("true");
    expect(bars.findAll(".jd-skeleton__bar")).toHaveLength(8);
    expect(body().find(".el-skeleton").exists()).toBe(false);
  });
});

describe("what each action sends and says", () => {
  it("Re-check says so, and says when it charged again", async () => {
    vi.mocked(api.recheckJob)
      .mockResolvedValueOnce({ id: A, recharged: false })
      .mockResolvedValueOnce({ id: A, recharged: true });
    const before = jobChanges.value;
    await show(detail({ status: "unknown", can_recheck: true }));
    await button("Re-check")!.trigger("click");
    await flushPromises();
    expect(api.recheckJob).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenLastCalledWith("Re-checked");
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(jobChanges.value).toBe(before + 1);
    await button("Re-check")!.trigger("click");
    await flushPromises();
    expect(notify.success).toHaveBeenLastCalledWith("Re-checked: charged again");
  });

  it("Recover with an empty account sends nothing", async () => {
    await show(detail({ status: "failed" }));
    await button("Recover")!.trigger("click");
    await flushPromises();
    expect(api.recoverJob).not.toHaveBeenCalled();
    expect(notify.error).toHaveBeenCalledWith("Enter the account id or label to recover from");
  });

  it("Recover sends the trimmed account, polls the command and reads the job again", async () => {
    vi.mocked(api.recoverJob).mockResolvedValue({ command_id: "cmd-1" });
    await show(detail({ status: "failed" }));
    await section("actions").find("input").setValue("  acct-9 ");
    await button("Recover")!.trigger("click");
    await flushPromises();
    expect(api.recoverJob).toHaveBeenCalledWith(A, "acct-9");
    expect(poll).toHaveBeenCalledWith("cmd-1", "Recover");
    expect(getJob).toHaveBeenCalledTimes(2);
  });

  it("Fetch title polls its command and reads the job and the list again", async () => {
    vi.mocked(api.fetchTitle).mockResolvedValue({ command_id: "cmd-2" });
    const before = jobChanges.value;
    await show(detail({ external_ref: "r", account_label: "a" }), "Technical");
    await button("Fetch title")!.trigger("click");
    await flushPromises();
    expect(api.fetchTitle).toHaveBeenCalledWith(A);
    expect(poll).toHaveBeenCalledWith("cmd-2", "Fetch title");
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(jobChanges.value).toBe(before + 1);
  });

  it("Resend webhook says it was queued again", async () => {
    vi.mocked(api.resendWebhook).mockResolvedValue({ id: A, resent: true });
    await show(detail({ callback: { status: "failed", attempts: 3, last: null } }), "Technical");
    await button("Resend webhook")!.trigger("click");
    await flushPromises();
    expect(api.resendWebhook).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenCalledWith("Webhook queued again");
    expect(getJob).toHaveBeenCalledTimes(2);
  });

  it("Revoke asks first and says how many links stopped", async () => {
    vi.mocked(api.revokeLinks).mockResolvedValue({ id: A, revoked: 2 });
    await show(detail({ media: [media()] }), "Technical");
    await button("Revoke share links")!.trigger("click");
    await vi.waitFor(() => expect(body().text()).toContain("Every share link of this job stops working. Continue?"));
    expect(api.revokeLinks).not.toHaveBeenCalled();
    // worded as every confirm: Cancel, then the destructive action
    expect(
      body()
        .findAll(".el-popconfirm__action .el-button")
        .map((b) => b.text()),
    ).toEqual(["Cancel", "Revoke"]);
    await body().find(".el-popconfirm__action .el-button--danger").trigger("click");
    await flushPromises();
    expect(api.revokeLinks).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenCalledWith("Revoked 2 link(s)");
  });

  it("Regenerate… asks first, makes the job and names it", async () => {
    vi.mocked(api.regenerateJob).mockResolvedValue({
      id: "dddddddd-4444",
      status: "queued",
      queue_position: 1,
      replayed: false,
    });
    await show();
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    await button("Regenerate…")!.trigger("click");
    await flushPromises();
    expect(api.regenerateJob).not.toHaveBeenCalled();
    const before = jobChanges.value;
    await button("Regenerate…")!.trigger("click");
    await flushPromises();
    expect(confirmDialog).toHaveBeenLastCalledWith({
      title: "Regenerate this job?",
      message: "Regenerate makes a NEW job for the same key at today’s price, and charges it.",
      confirmText: "Regenerate",
      danger: true,
    });
    expect(api.regenerateJob).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenCalledWith("New job dddddddd (queued)");
    expect(jobChanges.value).toBe(before + 1);
  });

  it("a regenerate the server already did says Already made", async () => {
    vi.mocked(api.regenerateJob).mockResolvedValue({
      id: "dddddddd-4444",
      status: "done",
      queue_position: null,
      replayed: true,
    });
    await show();
    await button("Regenerate…")!.trigger("click");
    await flushPromises();
    expect(notify.success).toHaveBeenCalledWith("Already made: job dddddddd (done)");
  });

  it("a regenerate that failed with a kept key offers a new one, behind a warning", async () => {
    const lost = new ApiError(503, {});
    lost.retrySafe = true;
    vi.mocked(api.regenerateJob).mockImplementation(() => {
      sessionStorage.setItem("regenerateOperations", JSON.stringify({ [`job:${A}`]: "q".repeat(32) }));
      return Promise.reject(lost);
    });
    await show();
    expect(button("Start a new regenerate")).toBeUndefined();
    await button("Regenerate…")!.trigger("click");
    await flushPromises();
    expect(button("Start a new regenerate")).toBeDefined();
    await button("Start a new regenerate")!.trigger("click");
    await flushPromises();
    expect(confirmDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message:
          "The earlier attempt may already have made jobs. Starting a new one can charge again. Check the Jobs list first.",
      }),
    );
    expect(sessionStorage.getItem("regenerateOperations")).not.toContain(`job:${A}`);
    expect(button("Start a new regenerate")).toBeUndefined();
  });

  it("Download saves every result as job-<id8>-<idx>, one file each", async () => {
    const first = new Blob(["1"]);
    const second = new Blob(["2"]);
    vi.mocked(api.fetchDownload).mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    await show(detail({ media: [media({ idx: 3 }), media({ idx: 5 })] }));
    await button("Download all")!.trigger("click");
    await flushPromises();
    expect(api.fetchDownload).toHaveBeenNthCalledWith(1, A, 3);
    expect(api.fetchDownload).toHaveBeenNthCalledWith(2, A, 5);
    expect(saveBlob).toHaveBeenNthCalledWith(1, first, "job-aaaaaaaa-3");
    expect(saveBlob).toHaveBeenNthCalledWith(2, second, "job-aaaaaaaa-5");
  });

  it("a failed download is the server's message", async () => {
    vi.mocked(api.fetchDownload).mockRejectedValue(
      new ApiError(404, { error: { code: "resource.not_found", message: "Not found" } }),
    );
    await show(detail({ media: [media()] }));
    await button("Download")!.trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("Not found");
    expect(saveBlob).not.toHaveBeenCalled();
  });
});

describe("results and references", () => {
  const view = (name: string) => section(name);

  it("draws an image and a video from a same-origin share link, with their facts", async () => {
    await show(
      detail({
        media: [
          media({ idx: 0, mime: "image/png", size: 120, url: "/m/tok1" }),
          media({ idx: 1, mime: "video/mp4", size: 5000, width: 1280, height: 720, duration_s: 30.08, url: "/m/tok2" }),
        ],
      }),
    );
    expect(view("results").find("img").attributes("src")).toBe("/m/tok1");
    const video = view("results").find("video");
    expect(video.attributes("src")).toBe("/m/tok2");
    expect(video.attributes("controls")).toBeUndefined();
    expect(view("results").find(".media__badge").text()).toBe("0:30");
    expect(
      view("results")
        .findAll(".media__facts span")
        .map((f) => f.text()),
    ).toEqual(["video/mp4", "5.0 KB", "1280×720", "30.08 s"]);
    expect(button("Open")).toBeUndefined();
    await view("results").find(".media__play").trigger("click");
    expect(view("results").find("video").attributes("controls")).toBeDefined();
    expect(view("results").find(".media__play").exists()).toBe(false);
  });

  it("a video card is named Result and its button Play video, numbered only when there are several", async () => {
    const video = (idx: number) => media({ idx, mime: "video/mp4", url: `/m/v${idx}` });
    await show(detail({ media: [video(0)] }));
    expect(view("results").find("figure").attributes("aria-label")).toBe("Result");
    expect(view("results").find(".media__play").attributes("aria-label")).toBe("Play video");
    await show(detail({ media: [video(0), video(1)] }));
    expect(
      view("results")
        .findAll("figure")
        .map((f) => f.attributes("aria-label")),
    ).toEqual(["Result 1", "Result 2"]);
    expect(
      view("results")
        .findAll(".media__play")
        .map((b) => b.attributes("aria-label")),
    ).toEqual(["Play video 1", "Play video 2"]);
  });

  it("an image is a tile whose viewer lists every previewable image of the list, and a video is left out", async () => {
    await show(
      detail({
        media: [
          media({ idx: 0, url: "/m/tok1" }),
          media({ idx: 1, mime: "video/mp4", url: "/m/tok2" }),
          media({ idx: 2, url: "/m/tok3" }),
          media({ idx: 3, url: null }),
        ],
      }),
    );
    expect(mounted[0]!.findAllComponents({ name: "ElImage" })).toHaveLength(2);
    // each image is a named button, as drawn; pressing the second opens the viewer on it
    const tiles = view("results").findAll("button.media__tile");
    expect(tiles.map((t) => t.attributes("aria-label"))).toEqual([
      "Open image 1 in the viewer",
      "Open image 2 in the viewer",
    ]);
    expect(mounted[0]!.findComponent({ name: "ElImageViewer" }).exists()).toBe(false);
    await tiles[1]!.trigger("click");
    const viewer = mounted[0]!.findComponent({ name: "ElImageViewer" });
    expect(viewer.props("urlList")).toEqual(["/m/tok1", "/m/tok3"]);
    expect(viewer.props("initialIndex")).toBe(1);
  });

  it("a tile takes its picture's shape: the stored size, else the job's ratio, else 2:3", async () => {
    const ratios = () =>
      view("results")
        .findAll("button.media__tile")
        .map((t) => t.attributes("style"));
    await show(detail({ media: [media({ width: 2304, height: 1728 })], params: { ratio: "9:16" } }));
    expect(ratios()).toEqual(["aspect-ratio: 2304 / 1728;"]);
    await show(detail({ media: [media({ width: null, height: null })], params: { ratio: "16:9" } }));
    expect(ratios()).toEqual(["aspect-ratio: 16 / 9;"]);
    // a job that stores its ratio as aspect_ratio, as the ledger also reads it
    await show(
      detail({
        media_type: "image",
        operation: "image",
        media: [media({ width: null, height: null })],
        params: { aspect_ratio: "4:3" },
      }),
    );
    expect(ratios()).toEqual(["aspect-ratio: 4 / 3;"]);
    const ledger = section("summary")
      .findAll(".ledger__row")
      .map((r) => [r.find("dt").text(), r.find("dd").text()]);
    expect(ledger.find(([k]) => k === "Ratio · count")?.[1]).toContain("4:3");
    await show(detail({ media: [media({ width: null, height: null })], params: {} }));
    expect(ratios()).toEqual(["aspect-ratio: 2 / 3;"]);
  });

  it("the viewer's buttons are ours, in the canvas's glyphs: no Element Plus icon is left", async () => {
    await show(detail({ media: [media({ idx: 0, url: "/m/a" }), media({ idx: 1, url: "/m/b" })] }));
    await view("results").findAll("button.media__tile")[0]!.trigger("click");
    await flushPromises();
    const names = body()
      .findAll(".el-image-viewer__wrapper button")
      .map((b) => b.attributes("aria-label"));
    expect(names).toEqual([
      "Zoom out",
      "Zoom in",
      "Fit or actual size",
      "Rotate left",
      "Rotate right",
      "Close",
      "Previous image",
      "Next image",
    ]);
    expect(body().find(".el-image-viewer__actions .el-icon").exists()).toBe(false);
    const viewer = mounted[0]!.findComponent({ name: "ElImageViewer" });
    // Next steps on from the picture shown, and wraps around
    const next = () => body().find(".el-image-viewer__wrapper [aria-label='Next image']").trigger("click");
    await next();
    expect(viewer.emitted("switch")?.at(-1)).toEqual([1]);
    await next();
    expect(viewer.emitted("switch")?.at(-1)).toEqual([0]);
    await body().find(".el-image-viewer__wrapper [aria-label='Close']").trigger("click");
    expect(mounted[0]!.findComponent({ name: "ElImageViewer" }).exists()).toBe(false);
  });

  it("a tile's name is on the tile itself, not on its picture, so a picture that does not load keeps it", async () => {
    await show(detail({ media: [media({ idx: 0, url: "/m/tok1" })] }));
    const tile = view("results").find("button.media__tile");
    expect(tile.attributes("aria-label")).toBe("Open image 1 in the viewer");
    expect(tile.find("[aria-label], [role]").exists()).toBe(false);
  });

  it.each([
    ["a script URL", `${SCRIPT_SCHEME}alert(1)`],
    ["a data URL", "data:text/html,<b>x</b>"],
    ["another host", "https://evil.example/m/abc"],
    ["a protocol-relative host", "//evil.example/m/abc"],
    ["a path that is not /m/", "/admin/jobs/x/content"],
  ])("%s is never drawn: no preview, and Open goes through the admin session", async (_name, url) => {
    await show(detail({ media: [media({ url })] }));
    expect(view("results").find("img, video, iframe, a").exists()).toBe(false);
    expect(view("results").text()).toContain("no preview");
    expect(body().html()).not.toContain("evil.example");
    expect(body().html()).not.toContain(SCRIPT_SCHEME);
    expect(button("Open")).toBeDefined();
  });

  it("an item whose link is gone gets Open, which fetches it as a blob and opens it", async () => {
    const blob = new Blob(["x"], { type: "image/png" });
    vi.mocked(api.fetchMediaBlob).mockResolvedValue(blob);
    await show(detail({ media: [media({ idx: 2, url: null })] }));
    expect(view("results").text()).toContain("no preview");
    await view("results").find("[aria-label=Open]").trigger("click");
    await flushPromises();
    expect(api.fetchMediaBlob).toHaveBeenCalledWith(A, "output", 2);
    expect(openBlob).toHaveBeenCalledWith(blob);
  });

  it("a reference opens through the input route, has no Download, and is counted in its heading", async () => {
    const blob = new Blob(["x"]);
    vi.mocked(api.fetchMediaBlob).mockResolvedValue(blob);
    await show(detail({ status: "failed", media: [media({ role: "input", idx: 1, url: null })] }));
    expect(buttonsIn("references")).toEqual(["Open"]);
    expect(view("references").find("h3").text()).toBe("References · 1");
    await view("references").find("button").trigger("click");
    await flushPromises();
    expect(api.fetchMediaBlob).toHaveBeenCalledWith(A, "input", 1);
    expect(view("results").exists()).toBe(false);
    expect(button("Download")).toBeUndefined();
  });

  it("a tile reads Open image N, a reference tile Open reference N", async () => {
    await show(
      detail({ status: "failed", media: [media({ role: "input", idx: 0 }), media({ role: "input", idx: 1 })] }),
    );
    expect(
      view("references")
        .findAll("button.media__tile")
        .map((t) => t.attributes("aria-label")),
    ).toEqual(["Open reference 1 in the viewer", "Open reference 2 in the viewer"]);
  });

  it("a failed Open is the server's message", async () => {
    vi.mocked(api.fetchMediaBlob).mockRejectedValue(
      new ApiError(404, { error: { code: "resource.not_found", message: "Not found" } }),
    );
    await show(detail({ media: [media({ url: null })] }));
    await view("results").find("[aria-label=Open]").trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("Not found");
    expect(openBlob).not.toHaveBeenCalled();
  });
});

describe("debug files", () => {
  it("says None when there are none", async () => {
    await show(detail(), "Technical");
    expect(section("debug").find(".debug-none").text()).toBe("None");
  });

  it("a text file is shown as text, never as markup", async () => {
    vi.mocked(api.fetchDebug).mockResolvedValue({ kind: "text", text: "<script>window.__ran=1</script><b>x</b>" });
    await show(
      detail({ debug_files: [{ idx: 4, mime: "text/html", size: 40, url: "/admin/jobs/x/debug/4" }] }),
      "Technical",
    );
    await button("File 4 (text/html)")!.trigger("click");
    await flushPromises();
    expect(api.fetchDebug).toHaveBeenCalledWith(A, 4);
    expect(section("debug").find("pre").text()).toBe("<script>window.__ran=1</script><b>x</b>");
    expect(section("debug").find("b").exists()).toBe(false);
    expect(openBlob).not.toHaveBeenCalled();
  });

  it("an image file opens in a new tab as a blob", async () => {
    const blob = new Blob(["png"], { type: "image/png" });
    vi.mocked(api.fetchDebug).mockResolvedValue({ kind: "image", blob });
    await show(
      detail({ debug_files: [{ idx: 0, mime: "image/png", size: 3, url: "/admin/jobs/x/debug/0" }] }),
      "Technical",
    );
    await button("File 0 (image/png)")!.trigger("click");
    await flushPromises();
    expect(openBlob).toHaveBeenCalledWith(blob);
    expect(section("debug").find("pre").exists()).toBe(false);
  });
});

describe("timeline", () => {
  it("says No events when there are none", async () => {
    await show(detail(), "Timeline");
    expect(section("timeline").text()).toContain("No events");
  });

  it("lists the events newest first with their clock time, and marks a recovery", async () => {
    await show(
      detail({
        events: [
          {
            id: 1,
            at_ms: new Date(2026, 9, 15, 19, 40, 2).getTime(),
            source: "engine",
            stage: "submitted",
            text: "sent",
          },
          {
            id: 2,
            at_ms: new Date(2026, 9, 15, 19, 41, 44).getTime(),
            source: "admin",
            stage: "recovered",
            text: "from history",
          },
        ],
      }),
      "Timeline",
    );
    const items = section("timeline").findAll(".timeline__item");
    expect(items).toHaveLength(2);
    expect(items[0]!.find(".timeline__time").text()).toBe("19:41:44");
    expect(items[0]!.find(".timeline__what").text()).toBe("recovered from history");
    expect(items[0]!.attributes("data-tone")).toBe("ok");
    expect(items[0]!.find(".timeline__dot").classes()).toContain("tone-ok");
    expect(items[1]!.find(".timeline__time").text()).toBe("19:40:02");
    expect(items[1]!.find(".timeline__what").text()).toBe("submitted sent");
    expect(items[1]!.attributes("data-tone")).toBe("neutral");
  });
});
