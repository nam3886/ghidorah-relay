import { DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as api from "@/api/jobs-detail";
import type { JobDetail, JobMediaItem } from "@/api/jobs-detail";
import { openBlob, saveBlob } from "@/components/jobs/blob-actions";
import { jobChanges } from "@/components/jobs/job-changes";
import JobDetailDrawer from "@/components/jobs/JobDetailDrawer.vue";
import MediaViewer from "@/components/jobs/MediaViewer.vue";
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
// A button is found by its aria-label when it has one (an icon button), else by its text.
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
const ledger = (where: DOMWrapper<Element>) =>
  where.findAll(".jl-ledger__row").map((r) => [r.find("dt").text(), r.find("dd").text()]);
const mounted: VueWrapper[] = [];

async function goTo(label: "Overview" | "Timeline" | "Technical") {
  await body()
    .findAll("[role=tab]")
    .find((t) => t.text().startsWith(label))!
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

  it("reads the job that was opened and names the drawer by its heading", async () => {
    await show();
    expect(getJob).toHaveBeenCalledWith(A);
    const named = body().find("[role=dialog]");
    expect(named.attributes("aria-label")).toBeUndefined();
    expect(document.getElementById(named.attributes("aria-labelledby")!)!.textContent).toBe(body().find("h2").text());
  });

  it("the drawer is headed with the job's title", async () => {
    await show();
    expect(body().find(".rl-drawer__title").text()).toBe("My title");
  });

  it("a job with no title is headed (untitled)", async () => {
    await show(detail({ title: null }));
    expect(body().find(".rl-drawer__title").text()).toBe("(untitled)");
  });

  it("a title with markup is text in the heading", async () => {
    const planted = `<img src=x onerror="window.__pwned=1">`;
    await show(detail({ title: planted }));
    expect(body().find(".rl-drawer__title").text()).toBe(planted);
    expect(body().find(".rl-drawer__title img").exists()).toBe(false);
  });

  it("the head says the status, the provider, what it is and a copy chip with the short id", async () => {
    await show(detail({ media_type: "image", operation: "image", media: [media(), media({ idx: 1 })] }));
    const sub = body().find(".rl-drawer__sub");
    expect(sub.find(".jd-pill").text()).toBe("Done");
    expect(sub.find(".jd-chip").text()).toBe("dola");
    expect(sub.find(".jd-chip").classes()).toContain("p-dola");
    expect(sub.text()).toContain("image · veo · 2 outputs");
    const chip = sub.find("button.job-copy");
    expect(chip.attributes("aria-label")).toBe("Copy job id aaaaaaaa");
    expect(chip.text()).toBe("aaaaaaaa");
    await chip.trigger("click");
    await flushPromises();
    expect(vi.mocked(copyText).mock.calls[0]![0]).toBe("aaaaaaaa");
  });

  it("a phone is the whole screen: Back alone, and one line under the title", async () => {
    stubPhone();
    await show(detail({ media: [media()] }));
    expect(body().find(".rl-drawer__sub").text()).toBe("Done · dola · video · veo · aaaaaaaa");
    expect(button("Close · Esc")).toBeUndefined();
    expect(button("Back to Jobs")).toBeDefined();
    expect(body().find(".rl-job-drawer").classes()).toContain("is-sheet");
  });

  it("Back names the page it goes back to and closes the drawer", async () => {
    const { w, ui } = await show();
    expect(button("Back to Jobs")).toBeDefined();
    await w.setProps({ backLabel: "Back to Alerts" });
    await button("Back to Alerts")!.trigger("click");
    expect(ui.openJobId).toBeNull();
  });

  it("an answer with no outputs count still says how many results there are", async () => {
    await show(detail({ outputs: undefined as unknown as number, media: [] }));
    expect(body().find(".rl-drawer__sub").text()).toContain("video · veo · 0 outputs");
  });

  it("has Overview, Timeline and Technical tabs, Overview first and selected; Timeline carries its count", async () => {
    await show(detail({ events: [{ id: 1, at_ms: 1, source: "engine", stage: "done", text: "" }] }));
    const tabs = body().findAll("[role=tab]");
    expect(tabs.map((t) => [t.text(), t.attributes("aria-selected")])).toEqual([
      ["Overview", "true"],
      ["Timeline1", "false"],
      ["Technical", "false"],
    ]);
    await goTo("Technical");
    expect(
      body()
        .findAll("[role=tab]")
        .map((t) => t.attributes("aria-selected")),
    ).toEqual(["false", "false", "true"]);
  });

  it("a phone shows the sections as a segmented control in the body", async () => {
    stubPhone();
    await show();
    const seg = body().find(".jd-seg");
    expect(seg.attributes("role")).toBe("tablist");
    expect(seg.findAll("[role=tab]").map((t) => t.text())).toEqual(["Overview", "Timeline", "Technical"]);
  });

  it("each tab has its own sections, in order", async () => {
    await show();
    expect(sectionNames()).toEqual(["conclusion", "actions", "money", "summary", "prompt", "raw"]);
    await goTo("Timeline");
    expect(sectionNames()).toEqual(["timeline"]);
    await goTo("Technical");
    expect(sectionNames()).toEqual(["technical", "diagnostics", "title", "more-actions", "debug", "sent"]);
  });

  it("a phone draws Money, Details, Prompt and Latest steps as cards", async () => {
    stubPhone();
    await show(detail({ events: [{ id: 1, at_ms: 1, source: "engine", stage: "done", text: "stored" }] }));
    expect(sectionNames()).toEqual(["conclusion", "money", "summary", "prompt", "raw", "steps"]);
    expect(section("money").classes()).toContain("jd-card");
    expect(section("steps").text()).toContain("stored");
  });

  it("Close shuts it", async () => {
    const { ui } = await show();
    await button("Close · Esc")!.trigger("click");
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
    const named = body().find("[role=dialog]");
    expect(document.getElementById(named.attributes("aria-labelledby")!)!.textContent).toBe("second");
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
    expect(section("conclusion").text()).toContain("· net charged 1 point · 1m 35s");
    expect(section("conclusion").classes()).toContain("is-ok");
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
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_000_000 + 4 * 60_000);
    const said = async (over: Partial<JobDetail>) => {
      await show(detail(over));
      return [section("conclusion").find("b").text(), section("conclusion").find(".num").text()];
    };
    expect(await said({ status: "queued", net_charged: 0 })).toEqual([
      "Waiting for an account",
      "· step 1 of 4 · nothing charged yet · 4m 0s so far",
    ]);
    expect(await said({ status: "submitting", net_charged: 2 })).toEqual([
      "Sending it to dola",
      "· step 2 of 4 · charged 2 points · 4m 0s so far",
    ]);
    expect(await said({ status: "waiting", net_charged: 2 })).toEqual([
      "Dola is making it",
      "· step 2 of 4 · charged 2 points · 4m 0s so far",
    ]);
    expect(await said({ status: "fetching", net_charged: 1 })).toEqual([
      "Fetching the result",
      "· step 3 of 4 · charged 1 point · 4m 0s so far",
    ]);
    // unknown is not on the list's bar: no step
    expect(await said({ status: "unknown", net_charged: 2 })).toEqual([
      "Dola has not confirmed it yet",
      "· charged 2 points · 4m 0s so far",
    ]);
    vi.restoreAllMocks();
  });

  it("a job on its way shows its step in the head's pill, as the list does, and no count of results", async () => {
    await show(detail({ status: "fetching" }));
    const sub = body().find(".rl-drawer__sub");
    expect(sub.find(".jd-pill").text()).toBe("Fetching 3/4");
    expect(sub.text()).toContain("video · veo");
    expect(sub.text()).not.toContain("output");
    await show(detail({ status: "unknown" }));
    expect(body().find(".rl-drawer__sub .jd-pill").text()).toBe("Unknown");
  });

  it("a value that is not set is the list's empty-cell dash", async () => {
    await show(detail({ status: "queued", account_label: null, finished_ms: null }));
    const value = (k: string) => ledger(section("summary")).find(([label]) => label === k)?.[1];
    expect(value("Account")).toBe("—");
    expect(value("Finished")).toBe("—");
  });

  it("the facts of a done video job: model, media, account, key, frame, webhook and the short times", async () => {
    await show(
      detail({
        created_ms: new Date(2026, 9, 15, 20, 41, 12).getTime(),
        finished_ms: new Date(2026, 9, 15, 20, 45, 24).getTime(),
        params: { ratio: "16:9", duration: 30 },
        frame_id: 4,
        callback: { status: "delivered", attempts: 1, last: "HTTP 200" },
      }),
    );
    expect(ledger(section("summary"))).toEqual([
      ["Operation", "video"],
      ["Model", "veo"],
      ["Media", "video · 30 s · 16:9"],
      ["Account", "acct-1"],
      ["Proxy", "—"],
      ["Key", "key-one · rk-key1"],
      ["Provider task id", "—"],
      ["Conversation", "—"],
      ["Prompt frame", "video v4"],
      ["Webhook", "delivered · 1 try"],
      ["Created", "15 Oct 20:41:12"],
      ["Finished", "15 Oct 20:45:24"],
    ]);
  });

  it("a key without a label is named by its prefix, as the list names it", async () => {
    await show(detail({ key_label: null }));
    expect(ledger(section("summary")).find(([k]) => k === "Key")).toEqual(["Key", "rk-key1"]);
    await show(detail({ key_label: " ", key_prefix: null }));
    expect(ledger(section("summary")).find(([k]) => k === "Key")).toEqual(["Key", "k1"]);
  });

  const money = () =>
    section("money")
      .findAll(".jd-money__cell")
      .map((c) => c.text());

  it("Money: what was charged, what went back, what the upstream took and the margin after it", async () => {
    await show(
      detail({ price: 3, net_charged: 3, account_cost: 1, upstream_cost: 1, cost_unit: "credits", margin: 2 }),
    );
    expect(money()).toEqual([
      "Charged3 ptsveo",
      "Refunded0 ptsnothing failed",
      "Upstream cost1 creditdola video pool",
      "Margin+2 ptsafter upstream",
    ]);
    expect(section("money").find(".jd-money__value.is-ok").text()).toBe("+2 pts");
  });

  it("the upstream cost says how it was learnt, and an expected-only cost says expected", async () => {
    await show(detail({ upstream_cost: 3, cost_unit: "credits", cost_source: "balance_delta" }));
    expect(money()[2]).toBe("Upstream cost3 creditsbalance before − after · dola video pool");
    await show(detail({ upstream_cost: 2, cost_unit: "credits", cost_source: "reply" }));
    expect(money()[2]).toBe("Upstream cost2 creditsstated by upstream · dola video pool");
    await show(detail({ upstream_cost: 1, cost_unit: "credits", cost_source: "expected" }));
    expect(money()[2]).toBe("Upstream cost1 creditexpected · dola video pool");
  });

  it("a cost in a unit Settings has no points rule for is named in its unit and leaves the margin a dash", async () => {
    await show(detail({ upstream_cost: 2, cost_unit: "jobs", margin: null }));
    expect(money()[2]).toBe("Upstream cost2 jobsdola video pool");
    expect(money()[3]).toBe("Margin—no points rule for this unit");
  });

  it("the drawer shows the proxy, the upstream ids, the raw exchanges and the diagnostics the server gives", async () => {
    await show(
      detail({
        proxy: "203.0.113.24:8080",
        provider_task_id: "7419",
        conversation: "c_9f31",
        raw_request: { route: "POST /v1/videos", status_code: 201, body: { model: "veo", n: 1 } },
        raw_response: null,
        diagnostics: { send_path: "http", batches: ["b1"] },
      }),
    );
    const facts = ledger(section("summary"));
    expect(facts.find(([k]) => k === "Proxy")).toEqual(["Proxy", "203.0.113.24:8080"]);
    expect(facts.find(([k]) => k === "Provider task id")).toEqual(["Provider task id", "7419"]);
    expect(facts.find(([k]) => k === "Conversation")).toEqual(["Conversation", "c_9f31"]);
    const raw = section("raw");
    expect(raw.text()).toContain("POST /v1/videos · 201");
    expect(raw.findAll("pre > div").map((d) => d.text())).toEqual(
      JSON.stringify({ model: "veo", n: 1 }, null, 2)
        .split("\n")
        .map((l) => l.trim()),
    );
    expect(raw.text()).toContain("No answer to show.");
  });

  it("a failed job charged and paid back says refunded; an image's media names its ratio and count", async () => {
    await show(
      detail({
        media_type: "image",
        operation: "image",
        status: "failed",
        price: 1,
        net_charged: 0,
        account_cost: 0,
        params: { ratio: "2:3", count: 1 },
      }),
    );
    expect(money()).toEqual([
      "Charged1 ptsveo",
      "Refunded1 ptsrefunded",
      "Upstream cost—not known yet",
      "Margin—needs the upstream cost",
    ]);
    expect(ledger(section("summary"))[2]).toEqual(["Media", "image · 2:3 · ×1"]);
  });

  it("a done job with a net below its price shows what was charged and no refund", async () => {
    await show(detail({ status: "done", price: 10, net_charged: 4 }));
    expect(money()[1]).toBe("Refunded6 ptspartly refunded");
  });

  it("a substituted model says so in the ledger", async () => {
    await show(detail({ model_substituted: true }));
    expect(ledger(section("summary"))[1]).toEqual(["Model", "veo (model differs)"]);
  });

  it("the prompt is the customer's words and Copy prompt copies exactly that", async () => {
    await show();
    expect(section("prompt").find("p").text()).toBe("a cat on a roof");
    await button("Copy prompt")!.trigger("click");
    await flushPromises();
    expect(vi.mocked(copyText).mock.calls[0]![0]).toBe("a cat on a roof");
    expect(notify.success).toHaveBeenCalledWith("Prompt copied");
  });

  it("no success toast when nothing could be copied", async () => {
    vi.mocked(copyText).mockResolvedValue("failed");
    await show();
    await button("Copy prompt")!.trigger("click");
    await flushPromises();
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).toHaveBeenCalled();
  });

  const facts = () => ledger(body().find("[data-testid=job-facts]"));

  it("lists the technical facts, leaving out what the server does not have", async () => {
    await show(detail({ callback: { status: "failed", attempts: 3, last: null }, frame_id: 7 }), "Technical");
    expect(facts()).toEqual([
      ["Job id", A],
      ["Provider / pool", "dola / video"],
      ["Wallet", "dola prepaid"],
      ["Key", "key-one · rk-key1"],
      ["Account", "acct-1"],
      ["Attempts / claims", "2 / 3"],
      ["Account cost", "1 credit"],
      ["Prompt frame", "video v7"],
      ["Callback", "failed · 3 attempts"],
      ["Sent", expect.stringMatching(/^\d{1,2} [A-Z][a-z]{2} \d\d:\d\d:\d\d\.\d{3}$/)],
      ["Updated", expect.stringMatching(/^\d{1,2} [A-Z][a-z]{2} \d\d:\d\d:\d\d\.\d{3}$/)],
      ["Params", '{"ratio":"16:9"}'],
    ]);
  });

  it("words a delivered webhook by its HTTP code, a retry by its state and code, and leaves out a cost of 0", async () => {
    const fact = (name: string) => facts().find(([k]) => k === name)?.[1];
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
    expect(facts()[3]).toEqual(["Key", "key-one · rk-key1"]);
    expect(facts()[4]).toEqual(["Account", "acct-1"]);
    expect(body().find("[data-testid=job-facts]").classes()).toContain("is-stacked");
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
      "Account id or label, e.g. dola-n11-01",
    );
    expect(section("more-actions").text()).toContain("Looks for a finished result of this job on that account.");
  });

  it("a failed job offers Regenerate… in the row too, and Recover only under More actions", async () => {
    await show(detail({ status: "failed" }));
    expect(buttonsIn("actions")).toEqual(["Regenerate…"]);
    expect(section("actions").find("input").exists()).toBe(false);
    await goTo("Technical");
    expect(buttonsIn("more-actions")).toEqual(["Recover"]);
  });

  it("More actions has its own heading, drawn only when it has a button", async () => {
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

  it("a job still running offers neither Recover nor Regenerate…", async () => {
    await show(detail({ status: "queued" }));
    // with nothing to offer there is no action row at all (an empty one would double the gap)
    expect(section("actions").exists()).toBe(false);
    // one that has a row (it can be re-checked) still offers no Regenerate…
    await show(detail({ status: "unknown", can_recheck: true }));
    expect(buttonsIn("actions")).toEqual(["Re-check job · R"]);
    await show(detail({ status: "queued" }));
    await goTo("Technical");
    expect(buttonsIn("more-actions")).toEqual([]);
    expect(section("more-actions").find("input").exists()).toBe(false);
  });

  it("Re-check only when the server says the job can be", async () => {
    await show(detail({ status: "unknown", can_recheck: true }));
    expect(buttonsIn("actions")).toEqual(["Re-check job · R"]);
    await show(detail({ status: "unknown", can_recheck: false, media: [media()] }));
    expect(buttonsIn("actions")).not.toContain("Re-check job · R");
  });

  it("Download sits first in the row of a job with results, and says all for more than one", async () => {
    await show(detail({ media: [media()] }));
    expect(buttonsIn("actions")).toEqual(["Download · D", "Regenerate…", "Copy share link"]);
    await show(detail({ media: [media(), media({ idx: 1 })] }));
    expect(buttonsIn("actions")).toEqual(["Download all · D", "Regenerate…", "Copy share link"]);
  });

  it("Copy share link copies the full link of every result that has one, one a line", async () => {
    await show(
      detail({ media: [media({ url: "/m/a" }), media({ idx: 1, url: null }), media({ idx: 2, url: "/m/b" })] }),
    );
    await button("Copy share link")!.trigger("click");
    await flushPromises();
    expect(vi.mocked(copyText).mock.calls[0]![0]).toBe(`${location.origin}/m/a\n${location.origin}/m/b`);
    expect(notify.success).toHaveBeenCalledWith("Share link copied");
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

  it("on a phone Regenerate… and Download sit in the foot; the row keeps the icons", async () => {
    stubPhone();
    await show(detail({ media: [media()] }));
    const footer = body().find(".el-drawer__footer");
    expect(footer.findAll("button").map(nameOf)).toEqual(["Regenerate…", "Download"]);
    expect(buttonsIn("actions")).toEqual(["Copy share link"]);
  });

  it("on a phone a job with nothing to offer has no foot, and one that can be re-checked has it in the row", async () => {
    stubPhone();
    await show(detail({ status: "queued" }));
    expect(body().find(".el-drawer__footer").exists()).toBe(false);
    await show(detail({ status: "waiting", can_recheck: true }));
    expect(body().find(".el-drawer__footer").exists()).toBe(false);
    expect(buttonsIn("actions")).toEqual(["Re-check job · R"]);
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
    await button("Re-check job · R")!.trigger("click");
    await flushPromises();
    expect(api.recheckJob).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenLastCalledWith("Re-checked");
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(jobChanges.value).toBe(before + 1);
    await button("Re-check job · R")!.trigger("click");
    await flushPromises();
    expect(notify.success).toHaveBeenLastCalledWith("Re-checked: charged again");
  });

  it("R re-checks and D downloads while the drawer is open, but not from a field", async () => {
    vi.mocked(api.recheckJob).mockResolvedValue({ id: A, recharged: false });
    vi.mocked(api.fetchDownload).mockResolvedValue(new Blob(["1"]));
    await show(detail({ status: "unknown", can_recheck: true, media: [media()] }), "Technical");
    body()
      .find("[data-section=title] input")
      .element.dispatchEvent(new KeyboardEvent("keydown", { key: "d", bubbles: true }));
    await flushPromises();
    expect(saveBlob).not.toHaveBeenCalled();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "d" }));
    await flushPromises();
    expect(saveBlob).toHaveBeenCalledTimes(1);
    // R is the Overview's Re-check job · R
    await goTo("Overview");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "r" }));
    await flushPromises();
    expect(api.recheckJob).toHaveBeenCalledWith(A);
  });

  it("Recover with an empty account sends nothing", async () => {
    await show(detail({ status: "failed" }), "Technical");
    await button("Recover")!.trigger("click");
    await flushPromises();
    expect(api.recoverJob).not.toHaveBeenCalled();
    expect(notify.error).toHaveBeenCalledWith("Enter the account id or label to recover from");
  });

  it("Recover sends the trimmed account, polls the command and reads the job again", async () => {
    vi.mocked(api.recoverJob).mockResolvedValue({ command_id: "cmd-1" });
    await show(detail({ status: "failed" }), "Technical");
    await section("more-actions").find("input").setValue("  acct-9 ");
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
    await button("Download all · D")!.trigger("click");
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
    await button("Download · D")!.trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("Not found");
    expect(saveBlob).not.toHaveBeenCalled();
  });
});

describe("results and references", () => {
  const view = (name: string) => section(name);
  const viewer = () => mounted[0]!.findAllComponents(MediaViewer);

  it("draws an image and a video from a same-origin share link, with their facts", async () => {
    await show(
      detail({
        media: [
          media({ idx: 0, mime: "image/png", size: 120, url: "/m/tok1" }),
          media({ idx: 1, mime: "video/mp4", size: 5000, width: 1280, height: 720, duration_s: 30.08, url: "/m/tok2" }),
        ],
      }),
    );
    expect(view("results").find("h3").text()).toBe("Results · 2");
    expect(view("results").find("img").attributes("src")).toBe("/m/tok1");
    const video = view("results").find("video");
    expect(video.attributes("src")).toBe("/m/tok2");
    expect(video.attributes("controls")).toBeUndefined();
    expect(view("results").find(".media__badge").text()).toBe("30.1 s");
    expect(
      view("results")
        .findAll(".media__facts")
        .map((f) => f.text()),
    ).toEqual(["1×1 · 120 B · png", "30.1 s · 1280×720 · 5.0 KB · mp4"]);
    expect(button("Open")).toBeUndefined();
  });

  it("every video's button is Play video N, and it opens the viewer on that result", async () => {
    const video = (idx: number) => media({ idx, mime: "video/mp4", url: `/m/v${idx}` });
    await show(detail({ media: [video(0), video(1)] }));
    expect(
      view("results")
        .findAll(".media__play")
        .map((b) => b.attributes("aria-label")),
    ).toEqual(["Play video 1", "Play video 2"]);
    await view("results").findAll(".media__play")[1]!.trigger("click");
    await flushPromises();
    expect(viewer()[0]!.props("modelValue")).toBe(1);
    expect(body().find(".rl-viewer video").attributes("src")).toBe("/m/v1");
  });

  it("an image is a tile whose viewer lists every previewable result, a video included, an item with no link left out", async () => {
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
    const tiles = view("results").findAll("button.media__tile");
    expect(tiles.map((t) => t.attributes("aria-label"))).toEqual([
      "Open result 1 in the viewer",
      "Open result 3 in the viewer",
    ]);
    expect(body().find(".rl-viewer").exists()).toBe(false);
    await tiles[1]!.trigger("click");
    await flushPromises();
    const items = viewer()[0]!.props("items") as Array<{ src: string }>;
    expect(items.map((i) => i.src)).toEqual(["/m/tok1", "/m/tok2", "/m/tok3"]);
    expect(viewer()[0]!.props("modelValue")).toBe(2);
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
    await show(
      detail({
        media_type: "image",
        operation: "image",
        media: [media({ width: null, height: null })],
        params: { aspect_ratio: "4:3" },
      }),
    );
    expect(ratios()).toEqual(["aspect-ratio: 4 / 3;"]);
    expect(ledger(section("summary")).find(([k]) => k === "Media")?.[1]).toContain("4:3");
    await show(detail({ media: [media({ width: null, height: null })], params: {} }));
    expect(ratios()).toEqual(["aspect-ratio: 2 / 3;"]);
  });

  it("the viewer's buttons are ours with tooltips: the file, Download, Close, the steps, the tools and the strip", async () => {
    await show(
      detail({
        media: [
          media({ idx: 0, url: "/m/a", width: 1024, height: 1536, size: 2_400_000 }),
          media({ idx: 1, url: "/m/b" }),
        ],
      }),
    );
    await view("results").findAll("button.media__tile")[0]!.trigger("click");
    await flushPromises();
    const wrap = body().find(".rl-viewer");
    expect(wrap.find(".mv__file").text()).toBe("job-aaaaaaaa-0.png");
    expect(wrap.find(".mv__facts").text()).toBe("Result 1 of 2 · 1024×1536 · 2.4 MB · veo");
    expect(wrap.findAll("button").map(nameOf)).toEqual([
      "Download · D",
      "Close · Esc",
      "Previous result",
      "Next result",
      "Zoom out · −",
      "Zoom in · +",
      "Fit or actual size",
      "Rotate left",
      "Rotate right",
      "Show result 1",
      "Show result 2",
      "Scroll all results left",
      "Scroll all results right",
    ]);
    expect(button("Previous result")!.attributes("disabled")).toBeDefined();
    await button("Zoom in · +")!.trigger("click");
    expect(wrap.find(".mv__zoom").text()).toBe("125%");
    await button("Next result")!.trigger("click");
    expect(body().find(".rl-viewer .mv__facts").text()).toContain("Result 2 of 2");
    expect(body().find(".rl-viewer .mv__zoom").text()).toBe("100%");
    expect(button("Next result")!.attributes("disabled")).toBeDefined();
    vi.mocked(api.fetchDownload).mockResolvedValue(new Blob(["x"]));
    await button("Download · D")!.trigger("click");
    await flushPromises();
    expect(api.fetchDownload).toHaveBeenCalledWith(A, 1);
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), "job-aaaaaaaa-1.png");
    await body().find(".rl-viewer [aria-label='Close · Esc']").trigger("click");
    await flushPromises();
    expect(viewer()[0]!.props("modelValue")).toBeNull();
    expect(body().find("[data-testid=job-drawer]").exists()).toBe(true);
  });

  it("a video plays in our own controls, no browser chrome", async () => {
    await show(detail({ media: [media({ idx: 0, mime: "video/mp4", url: "/m/v" })] }));
    await view("results").find(".media__play").trigger("click");
    await flushPromises();
    const wrap = body().find(".rl-viewer");
    expect(wrap.find("video").attributes("controls")).toBeUndefined();
    expect(wrap.findAll(".mv__row button").map(nameOf)).toEqual([
      "Play",
      "Back 5 seconds",
      "Mute",
      "Playback speed",
      "Loop",
      "Full screen",
    ]);
    expect(wrap.find("input[aria-label=Seek]").exists()).toBe(true);
    await button("Playback speed")!.trigger("click");
    expect(button("Playback speed")!.text()).toBe("1.5×");
    expect(button("Loop")!.attributes("aria-pressed")).toBe("true");
  });

  it("a tile's name is on the tile itself, not on its picture, so a picture that does not load keeps it", async () => {
    await show(detail({ media: [media({ idx: 0, url: "/m/tok1" })] }));
    const tile = view("results").find("button.media__tile");
    expect(tile.attributes("aria-label")).toBe("Open result 1 in the viewer");
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
    expect(button("Download · D")).toBeUndefined();
  });

  it("a tile reads Open result N, a reference tile Open reference N", async () => {
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
    const items = section("timeline").findAll(".tl__row");
    expect(items).toHaveLength(2);
    expect(items[0]!.find(".tl__clock").text()).toBe("19:41:44");
    expect([items[0]!.find(".tl__stage").text(), items[0]!.find(".tl__what").text()]).toEqual([
      "recovered",
      "from history",
    ]);
    expect(items[0]!.attributes("data-tone")).toBe("ok");
    expect(items[0]!.find(".tl__dot").classes()).toContain("tone-ok");
    expect(items[0]!.find(".tl__line").exists()).toBe(true);
    expect(items[1]!.find(".tl__clock").text()).toBe("19:40:02");
    expect(items[1]!.find(".tl__stage").text()).toBe("submitted");
    expect(items[1]!.attributes("data-tone")).toBe("neutral");
    expect(items[1]!.find(".tl__line").exists()).toBe(false);
  });
});
