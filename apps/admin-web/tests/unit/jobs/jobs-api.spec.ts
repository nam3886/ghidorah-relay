import { beforeEach, describe, expect, it, vi } from "vitest";
import { bulkRecheck, bulkRegenerate, JOB_STATUSES, listJobs, listKeyOptions } from "@/api/jobs";
import {
  fetchDebug,
  fetchDownload,
  fetchMediaBlob,
  fetchTitle,
  getJob,
  recheckJob,
  recoverJob,
  regenerateJob,
  renameJob,
  resendWebhook,
  revokeLinks,
} from "@/api/jobs-detail";

// The call sites of the Jobs views, through the real client with fetch stubbed: the paths, the query, the bodies, and
// the regenerate action names that decide which retry key is sent.
const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
let draws = 0;
const K = (n: number): string => n.toString(16).padStart(2, "0").repeat(16);

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const calls = () => fetchMock.mock.calls.map(([url, init]) => ({ url, method: init.method, body: init.body }));
const bodyOf = (n: number): Record<string, unknown> => JSON.parse(String(fetchMock.mock.calls[n]![1].body));
const urlOf = (n: number) => new URL(fetchMock.mock.calls[n]![0], "http://x");

beforeEach(() => {
  sessionStorage.clear();
  fetchMock.mockReset();
  fetchMock.mockImplementation(() => Promise.resolve(json({})));
  vi.stubGlobal("fetch", fetchMock);
  draws = 0;
  vi.spyOn(crypto, "getRandomValues").mockImplementation(((bytes: Uint8Array) =>
    bytes.fill(++draws)) as typeof crypto.getRandomValues);
});

describe("list", () => {
  it("sends the page size and offset and drops every empty filter", async () => {
    await listJobs({ status: "", provider: "", keyId: "", offset: 0, limit: 10 });
    expect(urlOf(0).pathname).toBe("/admin/jobs");
    expect(urlOf(0).search).toBe("?limit=10&offset=0");
  });

  it("sends the status, provider and key that are set, with the offset and never a cursor", async () => {
    await listJobs({ status: "submitting", provider: "dola", keyId: "k-1", offset: 30, limit: 20 });
    const q = urlOf(0).searchParams;
    expect(Object.fromEntries(q)).toEqual({
      limit: "20",
      offset: "30",
      status: "submitting",
      provider: "dola",
      key_id: "k-1",
    });
    expect(q.has("cursor")).toBe(false);
  });

  it("sends the created_at window as epoch ms, and nothing when it is not set", async () => {
    await listJobs({
      status: "",
      provider: "",
      keyId: "",
      offset: 20,
      limit: 20,
      createdFromMs: 1_700_000_000_000,
      createdToMs: 1_700_003_600_000,
    });
    expect(Object.fromEntries(urlOf(0).searchParams)).toEqual({
      limit: "20",
      offset: "20",
      created_from_ms: "1700000000000",
      created_to_ms: "1700003600000",
    });
    await listJobs({ status: "", provider: "", keyId: "", offset: 0, limit: 20 });
    expect(urlOf(1).searchParams.has("created_from_ms")).toBe(false);
    expect(urlOf(1).searchParams.has("created_to_ms")).toBe(false);
  });

  it("sends the display zone as tz, and nothing when it is not set", async () => {
    await listJobs({ status: "", provider: "", keyId: "", offset: 0, limit: 20, tz: "Asia/Ho_Chi_Minh" });
    expect(Object.fromEntries(urlOf(0).searchParams)).toEqual({ limit: "20", offset: "0", tz: "Asia/Ho_Chi_Minh" });
    await listJobs({ status: "", provider: "", keyId: "", offset: 0, limit: 20 });
    expect(urlOf(1).searchParams.has("tz")).toBe(false);
  });

  it("hands the day totals of the answer through unchanged", async () => {
    const day_totals = { "2026-10-15": 212, "2026-10-16": 3 };
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(json({ items: [], total: 215, next_cursor: null, day_totals })),
    );
    const page = await listJobs({ status: "", provider: "", keyId: "", offset: 0, limit: 20, tz: "Asia/Ho_Chi_Minh" });
    expect(page.day_totals).toEqual(day_totals);
  });

  it("the status filter offers submitting beside the others", () => {
    expect([...JOB_STATUSES]).toEqual(["queued", "submitting", "waiting", "fetching", "unknown", "done", "failed"]);
  });

  it("reads the keys for the key filter", async () => {
    await listKeyOptions();
    expect(calls()[0]).toMatchObject({ url: "/admin/keys", method: "GET" });
  });
});

describe("bulk actions", () => {
  it("re-check posts the job ids", async () => {
    await bulkRecheck(["a", "b"]);
    expect(calls()[0]).toMatchObject({ url: "/admin/jobs/bulk-recheck", method: "POST" });
    expect(bodyOf(0)).toEqual({ job_ids: ["a", "b"] });
  });

  it("regenerate posts the ids with one key, kept under the action bulk:<sorted ids>", async () => {
    await expect(bulkRegenerate(["b", "a"])).resolves.toEqual({});
    expect(calls()[0]).toMatchObject({ url: "/admin/jobs/bulk-regenerate", method: "POST" });
    expect(bodyOf(0)).toEqual({ job_ids: ["b", "a"], operation_key: K(1) });
  });

  it("the same selection after a 503 sends the same key, in any order", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: { code: "internal", message: "boom" } }, 503));
    await expect(bulkRegenerate(["a", "b"])).rejects.toMatchObject({ retrySafe: true });
    expect(JSON.parse(sessionStorage.getItem("regenerateOperations")!)).toEqual({ "bulk:a,b": K(1) });
    await bulkRegenerate(["b", "a"]);
    expect(bodyOf(1).operation_key).toBe(K(1));
    expect(JSON.parse(sessionStorage.getItem("regenerateOperations")!)).toEqual({});
  });

  it("a changed selection sends a new key", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: { code: "internal", message: "boom" } }, 503));
    await expect(bulkRegenerate(["a", "b"])).rejects.toBeTruthy();
    await bulkRegenerate(["a"]);
    expect(bodyOf(1)).toEqual({ job_ids: ["a"], operation_key: K(2) });
  });
});

describe("one job", () => {
  it("reads the detail by id (encoded)", async () => {
    await getJob("a/b");
    expect(calls()[0]).toMatchObject({ url: "/admin/jobs/a%2Fb", method: "GET" });
  });

  it("rename patches the title; an empty title clears it", async () => {
    await renameJob("j1", "New name");
    await renameJob("j1", "");
    expect(calls()[0]).toMatchObject({ url: "/admin/jobs/j1", method: "PATCH" });
    expect(bodyOf(0)).toEqual({ title: "New name" });
    expect(bodyOf(1)).toEqual({ title: "" });
  });

  it("re-check, fetch title, resend and revoke are bare POSTs", async () => {
    await recheckJob("j1");
    await fetchTitle("j1");
    await resendWebhook("j1");
    await revokeLinks("j1");
    expect(calls()).toEqual([
      { url: "/admin/jobs/j1/recheck", method: "POST", body: undefined },
      { url: "/admin/jobs/j1/fetch-title", method: "POST", body: undefined },
      { url: "/admin/jobs/j1/resend-webhook", method: "POST", body: undefined },
      { url: "/admin/jobs/j1/revoke-links", method: "POST", body: undefined },
    ]);
  });

  it("recover posts the account text as given", async () => {
    await recoverJob("j1", "acct-7");
    expect(calls()[0]).toMatchObject({ url: "/admin/jobs/j1/recover", method: "POST" });
    expect(bodyOf(0)).toEqual({ account: "acct-7" });
  });

  it("regenerate posts only the key, kept under the action job:<id>", async () => {
    fetchMock.mockResolvedValueOnce(json({}, 503));
    await expect(regenerateJob("j1")).rejects.toMatchObject({ retrySafe: true });
    expect(JSON.parse(sessionStorage.getItem("regenerateOperations")!)).toEqual({ "job:j1": K(1) });
    await regenerateJob("j1");
    expect(calls()[1]).toMatchObject({ url: "/admin/jobs/j1/regenerate", method: "POST" });
    expect(bodyOf(0)).toEqual({ operation_key: K(1) });
    expect(bodyOf(1)).toEqual({ operation_key: K(1) });
  });

  it("another job has its own key", async () => {
    fetchMock.mockResolvedValueOnce(json({}, 503));
    await expect(regenerateJob("j1")).rejects.toBeTruthy();
    await regenerateJob("j2");
    expect(bodyOf(1).operation_key).toBe(K(2));
  });
});

describe("files", () => {
  const file = (type: string, body: string) =>
    fetchMock.mockResolvedValueOnce(new Response(body, { headers: { "content-type": type } }));

  it("an output without a link opens through the outputs proxy, an input through the images proxy", async () => {
    file("image/png", "x");
    await fetchMediaBlob("j1", "output", 2);
    file("image/png", "x");
    await fetchMediaBlob("j1", "input", 0);
    expect(calls().map((c) => c.url)).toEqual(["/admin/jobs/j1/outputs/2/content", "/admin/jobs/j1/images/0/content"]);
  });

  it("download reads the content route", async () => {
    file("video/mp4", "x");
    const blob = await fetchDownload("j1", 3);
    expect(calls()[0]!.url).toBe("/admin/jobs/j1/content/3");
    expect(blob.type).toBe("video/mp4");
  });

  it("a debug image is a blob and anything else is text", async () => {
    file("image/png; charset=binary", "png");
    const image = await fetchDebug("j1", 0);
    expect(image.kind).toBe("image");
    file("text/plain; charset=utf-8", "<b>page</b>");
    await expect(fetchDebug("j1", 1)).resolves.toEqual({ kind: "text", text: "<b>page</b>" });
    expect(calls().map((c) => c.url)).toEqual(["/admin/jobs/j1/debug/0", "/admin/jobs/j1/debug/1"]);
  });

  it("a missing file is the server's error", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: { code: "resource.not_found", message: "Not found" } }, 404));
    await expect(fetchDebug("j1", 9)).rejects.toMatchObject({ status: 404, message: "Not found" });
  });
});
