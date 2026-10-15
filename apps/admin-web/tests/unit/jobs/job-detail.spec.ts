import { DOMWrapper, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import * as api from "@/api/jobs-detail";
import type { JobDetail, JobMediaItem } from "@/api/jobs-detail";
import { openBlob, saveBlob } from "@/components/jobs/blob-actions";
import { jobChanges } from "@/components/jobs/job-changes";
import JobDetailDialog from "@/components/jobs/JobDetailDialog.vue";
import { copyText } from "@/components/shared/clipboard";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { notify } from "@/lib/notify";
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
const mounted: VueWrapper[] = [];

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount();
  document.body.replaceChildren();
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

async function show(job: JobDetail = detail()) {
  while (mounted.length) mounted.pop()!.unmount();
  document.body.replaceChildren();
  setActivePinia(createPinia());
  getJob.mockResolvedValue(job);
  const w = mount(JobDetailDialog, { attachTo: document.body });
  mounted.push(w);
  const ui = useUiStore();
  ui.openJob(job.id);
  await flushPromises();
  await flushPromises();
  return { w, ui };
}

describe("opening", () => {
  it("draws nothing until a job is opened", async () => {
    setActivePinia(createPinia());
    mounted.push(mount(JobDetailDialog, { attachTo: document.body }));
    await flushPromises();
    expect(getJob).not.toHaveBeenCalled();
    expect(body().find("[data-section]").exists()).toBe(false);
  });

  it("reads the job that was opened and names the dialog by its first 8 characters", async () => {
    await show();
    expect(getJob).toHaveBeenCalledWith(A);
    expect(body().find(".el-dialog__title").text()).toBe("Job aaaaaaaa");
  });

  it("has every section of the old dialog, in order", async () => {
    await show();
    const names = body()
      .findAll("[data-section]")
      .map((s) => s.attributes("data-section"));
    expect(names).toEqual([
      "conclusion",
      "summary",
      "title",
      "prompt",
      "actions",
      "results",
      "references",
      "debug",
      "timeline",
      "technical",
    ]);
  });

  it("Close shuts it", async () => {
    const { ui } = await show();
    await button("Close")!.trigger("click");
    expect(ui.openJobId).toBeNull();
  });

  it("a failed read is a message with Retry", async () => {
    setActivePinia(createPinia());
    getJob.mockRejectedValueOnce(new ApiError(404, { error: { code: "resource.not_found", message: "Not found" } }));
    mounted.push(mount(JobDetailDialog, { attachTo: document.body }));
    useUiStore().openJob(A);
    await flushPromises();
    expect(body().find("[role=alert]").text()).toContain("Not found");
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
    mounted.push(mount(JobDetailDialog, { attachTo: document.body }));
    const ui = useUiStore();
    ui.openJob(A);
    await flushPromises();
    ui.openJob(B);
    await flushPromises();
    slow(detail({ id: A, title: "first" }));
    await flushPromises();
    expect(body().find(".el-dialog__title").text()).toBe("Job bbbbbbbb");
    expect((body().find("[data-section=title] input").element as HTMLInputElement).value).toBe("second");
  });
});

describe("conclusion, prompt and technical", () => {
  it("shows the verdict line", async () => {
    await show(
      detail({
        status: "failed",
        net_charged: 0,
        error_code: "render.deadline",
        error: "too slow",
        warnings: [{ code: "w" }],
      }),
    );
    const text = section("conclusion").text();
    expect(text).toContain("failed");
    expect(text).toContain("video · veo · net charged 0");
    expect(text).toContain("render.deadline: too slow");
    expect(text).toContain('warnings: [{"code":"w"}]');
    expect(section("conclusion").find(".el-alert").classes()).toContain("el-alert--error");
  });

  it("a code with no message shows only the code, and no warnings shows no line", async () => {
    await show(detail({ status: "failed", error_code: "render.deadline", error: null }));
    expect(section("conclusion").text()).toContain("render.deadline");
    expect(section("conclusion").text()).not.toContain("render.deadline:");
    expect(section("conclusion").text()).not.toContain("warnings");
  });

  it("the prompt is the customer's words and Copy prompt copies exactly that", async () => {
    await show();
    expect(section("prompt").find("pre").text()).toBe("a cat on a roof");
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

  it("lists the technical facts, a dash for what is empty", async () => {
    await show(detail({ callback: { status: "failed", attempts: 3, last: null }, frame_id: 7 }));
    const facts = section("technical")
      .findAll(".el-descriptions__label")
      .map((dt) => [dt.text(), dt.element.nextElementSibling!.textContent]);
    expect(facts).toEqual([
      ["Job id", A],
      ["Provider / pool", "dola / video"],
      ["Wallet", "prepaid"],
      ["Key", "key-one"],
      ["Account", "acct-1"],
      ["External ref", "-"],
      ["Attempts / claims", "2 / 3"],
      ["Account cost", "1"],
      ["Upstream price", "-"],
      ["Upstream model", "-"],
      ["Completion", "-"],
      ["Sent", expect.stringMatching(/2023/)],
      ["Updated", expect.stringMatching(/2023/)],
      ["Callback", "failed (3 attempts)"],
      ["Prompt frame version", "7"],
      ["Params", '{"ratio":"16:9"}'],
    ]);
  });

  it("shows the text sent upstream, or a dash when none was sent", async () => {
    await show();
    expect(section("technical").find("[data-field=sent-text]").text()).toBe("FRAME a cat on a roof MARK");
    await show(detail({ sent_text: null }));
    expect(section("technical").find("[data-field=sent-text]").text()).toBe("-");
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
    expect(body().find(".el-dialog img").exists()).toBe(false);
    const text = body().find(".el-dialog").text();
    expect(text).toContain(planted);
    expect(section("prompt").find("pre").text()).toBe(planted);
    expect(section("conclusion").text()).toContain(`render.deadline: ${planted}`);
    expect((body().find("[data-section=title] input").element as HTMLInputElement).value).toBe(planted);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe("rename", () => {
  it("sends the typed title, says Renamed, reads the job again and tells the list", async () => {
    vi.mocked(api.renameJob).mockResolvedValue({ id: A, title: "New name" });
    await show();
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
    await show();
    const before = jobChanges.value;
    await button("Rename")!.trigger("click");
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("Invalid (title)");
    expect(jobChanges.value).toBe(before);
  });
});

describe("which actions show", () => {
  const shown = () => section("actions").findAll("button").map(nameOf);

  it("a done job offers Recover and Regenerate, and nothing else without more facts", async () => {
    await show();
    expect(shown()).toEqual(["Recover", "Regenerate"]);
    expect(section("actions").find("input").attributes("placeholder")).toBe("account id or label");
  });

  it("a job still running offers neither Recover nor Regenerate", async () => {
    await show(detail({ status: "queued" }));
    expect(shown()).toEqual([]);
    expect(section("actions").find("input").exists()).toBe(false);
  });

  it("Re-check only when the server says the job can be", async () => {
    await show(detail({ status: "unknown", can_recheck: true }));
    expect(shown()).toEqual(["Re-check"]);
  });

  it("Resend webhook only for a failed callback", async () => {
    await show(detail({ status: "queued", callback: { status: "failed", attempts: 3, last: null } }));
    expect(shown()).toEqual(["Resend webhook"]);
    await show(detail({ status: "queued", callback: { status: "pending", attempts: 0, last: null } }));
    expect(shown()).toEqual([]);
  });

  it("Revoke share links only while some media still has a link", async () => {
    await show(detail({ status: "queued", media: [media({ url: null }), media({ idx: 1, url: "/m/t" })] }));
    expect(shown()).toEqual(["Revoke share links"]);
    await show(detail({ status: "queued", media: [media({ url: null })] }));
    expect(shown()).toEqual([]);
  });

  it("Fetch title needs both the upstream reference and the account", async () => {
    await show(detail({ status: "queued", external_ref: "ref-1", account_label: null }));
    expect(shown()).toEqual([]);
    await show(detail({ status: "queued", external_ref: null, account_label: "acct" }));
    expect(shown()).toEqual([]);
    await show(detail({ status: "queued", external_ref: "ref-1", account_label: "acct" }));
    expect(shown()).toEqual(["Fetch title"]);
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
    await show();
    await button("Recover")!.trigger("click");
    await flushPromises();
    expect(api.recoverJob).not.toHaveBeenCalled();
    expect(notify.error).toHaveBeenCalledWith("Enter the account id or label to recover from");
  });

  it("Recover sends the trimmed account, polls the command and reads the job again", async () => {
    vi.mocked(api.recoverJob).mockResolvedValue({ command_id: "cmd-1" });
    await show();
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
    await show(detail({ external_ref: "r", account_label: "a" }));
    await button("Fetch title")!.trigger("click");
    await flushPromises();
    expect(api.fetchTitle).toHaveBeenCalledWith(A);
    expect(poll).toHaveBeenCalledWith("cmd-2", "Fetch title");
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(jobChanges.value).toBe(before + 1);
  });

  it("Resend webhook says it was queued again", async () => {
    vi.mocked(api.resendWebhook).mockResolvedValue({ id: A, resent: true });
    await show(detail({ callback: { status: "failed", attempts: 3, last: null } }));
    await button("Resend webhook")!.trigger("click");
    await flushPromises();
    expect(api.resendWebhook).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenCalledWith("Webhook queued again");
    expect(getJob).toHaveBeenCalledTimes(2);
  });

  it("Revoke asks first and says how many links stopped", async () => {
    vi.mocked(api.revokeLinks).mockResolvedValue({ id: A, revoked: 2 });
    await show(detail({ media: [media()] }));
    await button("Revoke share links")!.trigger("click");
    await vi.waitFor(() => expect(body().text()).toContain("Every share link of this job stops working. Continue?"));
    expect(api.revokeLinks).not.toHaveBeenCalled();
    await body().find(".el-popconfirm__action .el-button--danger").trigger("click");
    await flushPromises();
    expect(api.revokeLinks).toHaveBeenCalledWith(A);
    expect(notify.success).toHaveBeenCalledWith("Revoked 2 link(s)");
  });

  it("Regenerate asks first, makes the job and names it", async () => {
    vi.mocked(api.regenerateJob).mockResolvedValue({
      id: "dddddddd-4444",
      status: "queued",
      queue_position: 1,
      replayed: false,
    });
    await show();
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    await button("Regenerate")!.trigger("click");
    await flushPromises();
    expect(api.regenerateJob).not.toHaveBeenCalled();
    const before = jobChanges.value;
    await button("Regenerate")!.trigger("click");
    await flushPromises();
    expect(confirmDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message: "Regenerate makes a NEW job for the same key at today’s price, and charges it. Continue?",
      }),
    );
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
    await button("Regenerate")!.trigger("click");
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
    await button("Regenerate")!.trigger("click");
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
});

describe("results and references", () => {
  const view = (name: string) => section(name);

  it("draws an image and a video from a same-origin share link, with their facts", async () => {
    await show(
      detail({
        media: [
          media({ idx: 0, mime: "image/png", size: 120, url: "/m/tok1" }),
          media({ idx: 1, mime: "video/mp4", size: 5000, duration_s: 4, url: "/m/tok2" }),
        ],
      }),
    );
    expect(view("results").find("img").attributes("src")).toBe("/m/tok1");
    const video = view("results").find("video");
    expect(video.attributes("src")).toBe("/m/tok2");
    expect(video.attributes("controls")).toBeDefined();
    expect(view("results").text()).toContain("image/png · 120 B");
    expect(view("results").text()).toContain("video/mp4 · 5000 B · 4s");
    expect(button("Open")).toBeUndefined();
  });

  it("an image is a thumbnail whose viewer lists every previewable image of the list, and a video is left out", async () => {
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
    const images = mounted[0]!.findAllComponents({ name: "ElImage" });
    expect(images).toHaveLength(2);
    expect(images[0]!.props("previewSrcList")).toEqual(["/m/tok1", "/m/tok3"]);
    expect(images[1]!.props("initialIndex")).toBe(1);
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

  it("a reference opens through the input route and has no Download", async () => {
    const blob = new Blob(["x"]);
    vi.mocked(api.fetchMediaBlob).mockResolvedValue(blob);
    await show(detail({ media: [media({ role: "input", idx: 1, url: null })] }));
    expect(view("references").findAll("button").map(nameOf)).toEqual(["Open"]);
    await view("references").find("button").trigger("click");
    await flushPromises();
    expect(api.fetchMediaBlob).toHaveBeenCalledWith(A, "input", 1);
    expect(view("results").text()).toContain("None");
  });

  it("an item that previews has no Open; every result has Download, which saves job-<id8>-<idx>", async () => {
    const blob = new Blob(["x"]);
    vi.mocked(api.fetchDownload).mockResolvedValue(blob);
    await show(detail({ media: [media({ idx: 3 })] }));
    expect(view("results").findAll("button").map(nameOf)).toEqual(["Download"]);
    await view("results").find("button").trigger("click");
    await flushPromises();
    expect(api.fetchDownload).toHaveBeenCalledWith(A, 3);
    expect(saveBlob).toHaveBeenCalledWith(blob, "job-aaaaaaaa-3");
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
    await show();
    expect(section("debug").find(".el-empty__description").text()).toBe("None");
  });

  it("a text file is shown as text, never as markup", async () => {
    vi.mocked(api.fetchDebug).mockResolvedValue({ kind: "text", text: "<script>window.__ran=1</script><b>x</b>" });
    await show(detail({ debug_files: [{ idx: 4, mime: "text/html", size: 40, url: "/admin/jobs/x/debug/4" }] }));
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
    await show(detail({ debug_files: [{ idx: 0, mime: "image/png", size: 3, url: "/admin/jobs/x/debug/0" }] }));
    await button("File 0 (image/png)")!.trigger("click");
    await flushPromises();
    expect(openBlob).toHaveBeenCalledWith(blob);
    expect(section("debug").find("pre").exists()).toBe(false);
  });
});

describe("layout", () => {
  const stubWidth = (narrow: boolean) =>
    vi.stubGlobal("matchMedia", () => ({
      matches: narrow,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));

  it("the facts are one column under 640 px and two above, the wide Params row spanning both", async () => {
    stubWidth(true);
    const narrow = await show();
    for (const d of narrow.w.findAllComponents({ name: "ElDescriptions" })) expect(d.props("column")).toBe(1);
    stubWidth(false);
    const wide = await show();
    const tables = wide.w.findAllComponents({ name: "ElDescriptions" });
    expect(tables).toHaveLength(2);
    for (const d of tables) expect(d.props("column")).toBe(2);
    for (const name of ["Job id", "Params", "Wallet"]) {
      const label = section("technical")
        .findAll(".el-descriptions__label")
        .find((l) => l.text() === name)!;
      expect(label.element.nextElementSibling!.getAttribute("colspan")).toBe(name === "Wallet" ? "1" : "3");
    }
  });
});

describe("timeline", () => {
  it("says No events when there are none", async () => {
    await show();
    expect(section("timeline").text()).toContain("No events");
  });

  it("lists the events newest first, and marks a recovery", async () => {
    await show(
      detail({
        events: [
          { id: 1, at_ms: 1_700_000_000_000, source: "engine", stage: "submitted", text: "sent" },
          { id: 2, at_ms: 1_700_000_100_000, source: "admin", stage: "recovered", text: "from history" },
        ],
      }),
    );
    const items = section("timeline").findAll(".el-timeline-item");
    expect(items).toHaveLength(2);
    expect(items[0]!.text()).toContain("recovered");
    expect(items[0]!.text()).toContain("from history");
    expect(items[0]!.find(".el-tag").classes()).toContain("el-tag--success");
    expect(items[1]!.text()).toContain("engine");
    expect(items[1]!.text()).toContain("submitted");
    expect(items[1]!.text()).toContain("sent");
    expect(items[1]!.find(".el-tag").classes()).toContain("el-tag--info");
  });
});
